"""Orquestación de extracción read-only de conexiones → inventario normalizado (F2.1).

Source-agnóstica: recibe un `ConnectionSource` (CLI real o simulado) y produce un `ConnectivityInventory`.
Maneja reintentos con límites de API (429 + Retry-After), timeouts y errores PARCIALES sin abortar todo.
NUNCA escribe en el tenant/SharePoint/Spaces: solo lee. NUNCA deja secretos/tokens en errores (ver `sanitize`).

El `environment` es un atributo GOBERNADO (lo asigna el registro de F2.2), NO un requisito técnico: la extracción solo necesita
tenant, Spaces y conexiones. Si el llamador provee `space_environment_map` se usa tal cual; un Space SIN clasificación se extrae
igual y queda con `environment: null` en el inventario (marcador interno UNASSIGNED_ENVIRONMENT), sin error y sin degradar el
estado. Nunca se infiere de nombres de Spaces, tenants o conexiones.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional, Protocol, runtime_checkable

from .inventory import (
    UNASSIGNED_ENVIRONMENT,
    ConnectivityInventory,
    Exhaustiveness,
    ExtractionError,
    InventoryCoverage,
    InventoryStatus,
    compute_exhaustiveness,
    compute_status,
)
from .snapshot import from_snapshot


# ── Errores de fuente (clasificados; mensaje ya sanitizado por quien lo lanza) ──
class SourceError(Exception):
    def __init__(self, code: str, message: str, *, retriable: bool = False, retry_after: Optional[float] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retriable = retriable
        self.retry_after = retry_after


# ── Sanitización de secretos/tokens (condición 8) ─────────────────────────────
_SECRET_PATTERNS = [
    # argv en forma de lista (repr de TimeoutExpired/CalledProcessError): '--client-secret', '<valor>'
    re.compile(r"""(--(?:client-secret|access-token|refresh-token|passcode|code)['"]?\s*,\s*['"])[^'"]*""", re.I),
    re.compile(r"(--(?:client-secret|access-token|refresh-token|passcode)[=\s]+)[^\s'\",\]]+", re.I),
    re.compile(r"(client[_-]?secret[\"'=:\s]+)\S+", re.I),
    re.compile(r"(authorization:\s*basic\s+)\S+", re.I),
    re.compile(r"(authorization:\s*bearer\s+)\S+", re.I),
    re.compile(r"(access_token[\"'=:\s]+)\S+", re.I),
    re.compile(r"(refresh_token[\"'=:\s]+)\S+", re.I),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),  # JWT
]


def _literal_variants(secret: str):
    # El valor puede aparecer tal cual, escapado por repr() (p.ej. barras dobles) o por JSON.
    yield secret
    yield repr(secret)[1:-1]
    yield json.dumps(secret)[1:-1]


def sanitize(text: object, secrets: Iterable[Optional[str]] = ()) -> str:
    """Redacta secretos/tokens de un mensaje antes de exponerlo en error/log. Nunca vuelca la respuesta cruda.

    `secrets`: valores CONOCIDOS (p.ej. el Client Secret de la ejecución) que se borran literalmente además de
    los patrones; defensa en profundidad para formatos que los patrones no anticipen."""
    s = str(text)
    for secret in secrets:
        if secret and len(secret) >= 4:
            for variant in _literal_variants(secret):
                s = s.replace(variant, "[REDACTED]")
    for pat in _SECRET_PATTERNS:
        if pat.groups:
            s = pat.sub(r"\1[REDACTED]", s)
        else:
            s = pat.sub("[REDACTED]", s)
    # Corta respuestas largas: nunca registrar payloads completos sin sanitizar.
    if len(s) > 500:
        s = s[:500] + "…[truncado]"
    return s


# ── Política de reintentos (sleep/clock inyectables → tests sin esperas reales) ──
@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 8.0
    sleep: Callable[[float], None] = field(default=lambda s: None)  # no-op por defecto; el real inyecta time.sleep


def with_retry(call: Callable, policy: RetryPolicy):
    """Ejecuta `call`; reintenta SOLO ante SourceError.retriable, respetando Retry-After. Relanza el último error."""
    last: Optional[SourceError] = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return call()
        except SourceError as e:
            last = e
            if not e.retriable or attempt == policy.max_attempts:
                raise
            delay = e.retry_after if e.retry_after is not None else min(policy.base_delay * (2 ** (attempt - 1)), policy.max_delay)
            policy.sleep(delay)
    assert last is not None
    raise last


# ── Contrato de la fuente de conexiones ───────────────────────────────────────
@runtime_checkable
class ConnectionSource(Protocol):
    """Fuente read-only de conexiones. Implementaciones: CLI real (DatasphereCli) o simulada (tests).

    `list_connections` DEBE resolver internamente su paginación (y reintentos 429 por página). Cualquier
    fallo se comunica como `SourceError` con `message` YA sanitizado.
    """
    def preflight(self) -> None: ...                       # valida OAuth + scope de conexiones; lanza SourceError si no
    def list_spaces(self) -> list: ...                     # nombres de espacios accesibles
    def list_connections(self, space: str) -> list: ...    # items (dicts con al menos "name") del espacio
    def get_connection(self, space: str, name: str) -> dict: ...  # doc completo (forma conn_space_*.json)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_inventory(
    source: ConnectionSource,
    *,
    client: str,
    tenant_id: str,
    tenant_host: str,
    space_environment_map: Optional[dict] = None,
    expected_spaces: Optional[list] = None,
    retry: Optional[RetryPolicy] = None,
    snapshot_id: Optional[str] = None,
    now: Optional[Callable[[], str]] = None,
) -> ConnectivityInventory:
    """Extrae conexiones de los espacios esperados y arma el inventario normalizado con cobertura y estado.

    Read-only. Errores parciales no abortan: se registran y el estado cae a PARTIAL/FAILED. Nunca persiste.
    """
    retry = retry or RetryPolicy()
    now = now or _now_iso
    space_environment_map = space_environment_map or {}
    snapshot_id = snapshot_id or uuid.uuid4().hex
    errors: list = []

    # ¿La fuente puede PROBAR completitud del listado? (CLI sin cursor/total ⇒ False.)
    source_guarantees_exhaustive = bool(getattr(source, "guarantees_exhaustive_list", False))

    def _inv(status, coverage, conns=()):
        return ConnectivityInventory(
            client=client, snapshot_id=snapshot_id, captured_at=now(),
            tenant_id=tenant_id, tenant_host=tenant_host, status=status,
            coverage=coverage, connections=tuple(conns),
            exhaustiveness=compute_exhaustiveness(status, source_guarantees_exhaustive=source_guarantees_exhaustive),
        )

    # 1) Preflight (OAuth + scope). Un fallo acá ⇒ FAILED, sin exponer secretos.
    try:
        source.preflight()
    except SourceError as e:
        errors.append(ExtractionError("preflight", e.code, sanitize(e.message), e.retriable))
        return _inv(InventoryStatus.FAILED, InventoryCoverage(errors=tuple(errors)))

    # 2) Determinar espacios objetivo (esperados si se declararon; si no, los que liste la fuente).
    if expected_spaces is not None:
        targets = tuple(expected_spaces)
    else:
        try:
            targets = tuple(with_retry(source.list_spaces, retry))
        except SourceError as e:
            errors.append(ExtractionError("spaces", e.code, sanitize(e.message), e.retriable))
            return _inv(InventoryStatus.FAILED, InventoryCoverage(errors=tuple(errors)))

    accessible: list = []
    inaccessible: list = []
    discovered = 0
    conns: list = []

    # 3) Por cada espacio objetivo: list (paginado por la fuente) → get por conexión.
    for space in targets:
        # environment es GOBERNADO (F2.2), no requisito técnico: sin clasificación el Space se extrae igual (clasificación pendiente).
        env = space_environment_map.get(space) or UNASSIGNED_ENVIRONMENT
        try:
            # `list_connections` resuelve su propia PAGINACIÓN y sus 429 por página (responsabilidad de la fuente).
            items = source.list_connections(space)
        except SourceError as e:
            inaccessible.append(space)
            errors.append(ExtractionError(f"space:{space}", e.code, sanitize(e.message), e.retriable))
            continue
        accessible.append(space)
        discovered += len(items)
        for item in items:
            name = item.get("name") if isinstance(item, dict) else str(item)
            if not name:
                errors.append(ExtractionError(f"space:{space}", "BAD_LIST_ITEM", "item de lista sin 'name'"))
                continue
            try:
                doc = with_retry(lambda: source.get_connection(space, name), retry)
            except SourceError as e:
                errors.append(ExtractionError(f"connection:{space}/{name}", e.code, sanitize(e.message), e.retriable))
                continue
            try:
                oc = from_snapshot(doc, tenant=tenant_id, environment=env)   # agente OPCIONAL (cond. 4/5)
            except Exception as e:  # parseo defensivo; nunca volcar el doc crudo
                errors.append(ExtractionError(f"connection:{space}/{name}", "PARSE_FAILED", sanitize(type(e).__name__)))
                continue
            conns.append(oc)

    coverage = InventoryCoverage(
        expected_spaces=targets,
        accessible_spaces=tuple(accessible),
        inaccessible_spaces=tuple(inaccessible),
        discovered_connections=discovered,
        extracted_connections=len(conns),
        errors=tuple(errors),
    )
    status = compute_status(coverage, preflight_ok=True, had_targets=bool(targets))
    return _inv(status, coverage, conns)
