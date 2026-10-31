"""
connectivity_inventory_service.py — extracción AD-HOC de conexiones (F2.1) desde el Catálogo del módulo 01.

SOLO LECTURA y SIN PERSISTENCIA: lista y lee conexiones de los Spaces pedidos con `CliConnectionSource` (perfil del CLI
aislado + `--secrets-file` efímero), bajo un presupuesto GLOBAL (`Budget`: llamadas físicas, reintentos, duración) y
devuelve ÚNICAMENTE el resumen saneado de B1 (`review_summary`): preflight, estado de ejecución, exhaustividad,
cobertura y PRESENCIA de metadatos. El inventario completo vive solo en memoria durante la ejecución: nada se escribe
en SharePoint, Spaces, Catálogos ni almacenamiento ad-hoc. No hay Assessment (F2.3), reconciliación (F2.2) ni
publicación (F2.4).

Reutiliza del módulo 04 (sin duplicar): la AUTORIZACIÓN (`datasphere_assessment_service.can_execute` /
`can_use_client_profiles`), la VALIDACIÓN de URLs/hosts de tenant (`_check_url`: https + sufijos SAP +
`DANWOR_DATASPHERE_ALLOWED_HOSTS`) y de nombres, y el contexto NO sensible de la sesión (solo lectura).

Controles propios:
  * ``DANWOR_CONNECTIVITY_EXTRACTION_UI`` (feature flag): apagado por defecto ⇒ la herramienta no existe (sin «+», POST 404).
  * ``DANWOR_CONNECTIVITY_PILOT_HOSTS`` (opcional, SERVIDOR): lista de hostnames de tenant habilitados para el piloto; si está
    definida, cualquier otro tenant se rechaza. Nunca viene del navegador.
  * launcher del CLI en modo ``direct`` (fail-closed): con `npx` los nietos podrían sobrevivir a un timeout en Linux.
  * workspace efímero fuera de todo directorio persistido (fail-closed) y borrado VERIFICADO.
  * concurrencia: semáforo global + single-flight por (usuario, tenant).

Identidad (decisión D1): ``tenant_id`` del inventario = clave técnica PROVISIONAL derivada del host
(``provisional-host:<hostname>``). NO es la identidad gobernada: F2.2 la reemplazará por el ``tenant_id`` del registro
gobernado `space→environment`/tenant. El environment de cada Space es DECLARADO por el consultor (D2), no homologado.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from webapp.services import datasphere_assessment_service as asd

log = logging.getLogger("danwor.connectivity_inventory")

FLAG_ENV = "DANWOR_CONNECTIVITY_EXTRACTION_UI"
PILOT_HOSTS_ENV = "DANWOR_CONNECTIVITY_PILOT_HOSTS"
MODULES = frozenset({"01_Arquitectura_y_Conectividad"})
# El selector de perfiles del dueño reutiliza el endpoint EXISTENTE del módulo 04 (misma autorización de dueño; sin ruta nueva).
PROFILES_STEM = "04_Modelado_y_Desarrollo"
ENVIRONMENTS = ("DEV", "QAS")
SPACE_ROWS = 4
MAX_SPACES = 10
WORK_PREFIX = "dsp-conn-web-"
# Presupuesto del SERVIDOR (no editable desde la UI). Solicitudes = llamadas FÍSICAS (preflight incluido).
LIMITS = {"max_requests": 150, "max_total_retries": 6, "max_duration_s": 600.0}
CALL_TIMEOUT_S = 90.0
MAX_ATTEMPTS = 3
MAX_CONCURRENT = 2

TITLE = "No se pudo extraer las conexiones"
PHASE_EXTRACTING = "extracting"
PHASE_COPY = {PHASE_EXTRACTING: "Extrayendo conexiones (solo lectura)..."}
MESSAGES = {
    "INVALID_PARAMETER": "Revisá los campos marcados.",
    "OPERATION_ALREADY_RUNNING": "Ya hay una extracción en curso para este tenant. Esperá a que termine.",
    "CAPACITY_EXCEEDED": "Hay demasiadas extracciones en curso. Intentá de nuevo en unos minutos.",
    "CLI_UNAVAILABLE": "El CLI de SAP Datasphere no está disponible en este entorno. No se ejecutó nada.",
    "CLI_LAUNCHER_NOT_DIRECT": "El CLI de SAP Datasphere no se puede ejecutar en modo directo en este entorno. No se ejecutó nada.",
    "WORKSPACE_UNSAFE": "El directorio temporal no es seguro en este entorno (cae bajo un directorio persistido). No se ejecutó nada.",
    "WORKSPACE_CLEANUP_FAILED": "No se pudo verificar la limpieza del espacio temporal de la extracción. Avisá al administrador.",
    "PREFLIGHT_INSUFFICIENT": "Las credenciales no permiten listar conexiones en el Space de prueba. No se extrajo nada.",
    "REQUESTS_EXCEEDED": "Se alcanzó el límite de solicitudes de la extracción. El resultado no es completo.",
    "DURATION_EXCEEDED": "Se alcanzó la duración máxima de la extracción. El resultado no es completo.",
    "RETRIES_EXCEEDED": "Se alcanzó el límite de reintentos de la extracción. El resultado no es completo.",
    "INVENTORY_FAILED": "No se pudo extraer ninguna conexión de los Spaces pedidos.",
    "REAL_PROFILE_MODIFIED": "El perfil del CLI del servidor cambió durante la extracción: el resultado no es válido. Avisá al administrador.",
    "UNEXPECTED_ERROR": "La extracción falló por un error inesperado. No se guardó nada.",
}

_LOCKS_GUARD = threading.Lock()
_RUNNING: dict[tuple[str, str], threading.Lock] = {}
_CAPACITY = threading.BoundedSemaphore(MAX_CONCURRENT)


# ───────────────────────────── habilitación y autorización (reutilizadas) ─────────────────────────────
def flag_enabled() -> bool:
    return os.getenv(FLAG_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def tool_enabled(stem: str) -> bool:
    return flag_enabled() and stem in MODULES


def can_execute(request) -> bool:
    return asd.can_execute(request)                      # mismo criterio que el Assessment del módulo 04


def can_use_client_profiles(request) -> bool:
    return asd.can_use_client_profiles(request)          # perfiles de SharePoint: solo el dueño


def tool_context(request, stem: str) -> dict:
    """Contexto de la plantilla. ``form`` = SOLO host/token/client_id de la sesión del módulo 04 (lectura; acá no se guarda nada)."""
    enabled = tool_enabled(stem)
    can = bool(enabled and can_execute(request))
    owner_only = bool(can and can_use_client_profiles(request))
    form: dict[str, str] = {}
    if can:
        stored = asd.load_form_context(asd.session_key(request))
        form = {k: stored[k] for k in ("host", "token_url", "client_id") if stored.get(k)}
    from webapp.services import datasphere_profiles
    return {"enabled": enabled, "can_execute": can, "stem": stem, "form": form,
            "owner_profiles_url": datasphere_profiles.selector_url(PROFILES_STEM, owner_only),
            "space_rows": list(range(1, SPACE_ROWS + 1)), "environments": list(ENVIRONMENTS)}


# ───────────────────────────── valores y validación ─────────────────────────────
@dataclass(frozen=True)
class ExtractionValues:
    host: str
    token_url: str
    client_id: str
    client: str
    spaces: tuple = ()                    # ((space_id, environment_declarado), ...) en el orden del formulario
    probe_connection: str = ""            # opcional: valida read_detail en el preflight

    @classmethod
    def from_form(cls, *, host: str, token_url: str, client_id: str, client: str,
                  space_ids: list[str], space_envs: list[str], probe_connection: str = "") -> "ExtractionValues":
        pairs = []
        for i, raw in enumerate(space_ids):
            space = (raw or "").strip()
            env = (space_envs[i] if i < len(space_envs) else "").strip()
            if space or env:
                pairs.append((space, env))
        return cls(host=(host or "").strip(), token_url=(token_url or "").strip(), client_id=(client_id or "").strip(),
                   client=" ".join((client or "").split()), spaces=tuple(pairs),
                   probe_connection=(probe_connection or "").strip())


def pilot_hosts() -> frozenset[str]:
    return frozenset(h.strip().lower().strip(".") for h in os.getenv(PILOT_HOSTS_ENV, "").split(",") if h.strip())


def validate(values: ExtractionValues, client_secret: str) -> dict[str, str]:
    errors: dict[str, str] = {}
    asd._check_url(values.host, errors, "host")                    # MISMA validación de tenant que el módulo 04
    asd._check_url(values.token_url, errors, "token_url")
    pilot = pilot_hosts()
    if pilot and "host" not in errors and (urlparse(values.host).hostname or "").lower().strip(".") not in pilot:
        errors["host"] = "Este tenant no está habilitado para la extracción de conexiones durante el piloto."
    if not values.client_id or len(values.client_id) > 300 or any(c.isspace() for c in values.client_id):
        errors["client_id"] = "Ingresá el Client ID (sin espacios)."
    if not client_secret or len(client_secret) > 1000:
        errors["client_secret"] = "Ingresá el Client Secret."
    if not asd._CLIENT_RE.match(values.client):
        errors["client"] = "Ingresá el cliente (letras, números, espacios, _ . -)."
    seen: set[str] = set()
    if not values.spaces:
        errors["spaces"] = "Ingresá al menos un Space ID con su environment declarado."
    elif len(values.spaces) > MAX_SPACES:
        errors["spaces"] = f"Podés pedir hasta {MAX_SPACES} Spaces por extracción."
    for space, env in values.spaces:
        if not asd._NAME_RE.match(space):
            errors["spaces"] = "Cada Space ID debe ser un nombre técnico (letras, números, _ . -)."
            break
        if env not in ENVIRONMENTS:
            errors["spaces"] = "Elegí el environment declarado (DEV o QAS) de cada Space."
            break
        if space.casefold() in seen:
            errors["spaces"] = "Hay Spaces repetidos."
            break
        seen.add(space.casefold())
    if values.probe_connection and not asd._NAME_RE.match(values.probe_connection):
        errors["probe_connection"] = "Ingresá un nombre técnico de conexión válido (letras, números, _ . -)."
    return errors


def provisional_tenant_key(host: str) -> str:
    """Clave técnica PROVISIONAL (D1). No es la identidad gobernada del tenant (F2.2)."""
    return "provisional-host:" + (urlparse(host).hostname or "").lower().strip(".")


# ───────────────────────────── resultado ─────────────────────────────
@dataclass
class ExtractionOutcome:
    state: str                                 # INVALID | FAILED | PARTIAL | SUCCESS
    error_code: str | None = None
    message: str = ""
    title: str = ""
    errors: dict = field(default_factory=dict)
    operation_id: str | None = None
    duration_s: float = 0.0
    summary: dict | None = None                # SOLO review_summary (B1)


def _failed(code: str, op_id: str, started: float, summary: dict | None = None) -> ExtractionOutcome:
    return ExtractionOutcome("FAILED", code, MESSAGES[code], TITLE, operation_id=op_id,
                             duration_s=round(time.monotonic() - started, 2), summary=summary)


def _lock_for(user_email: str, host: str) -> threading.Lock:
    key = ((user_email or "").lower(), (urlparse(host).hostname or "").lower())
    with _LOCKS_GUARD:
        return _RUNNING.setdefault(key, threading.Lock())


def _launcher_mode() -> str:
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    return dsp.resolve_datasphere_cli_launcher(Path(dsp.__file__).resolve().parents[2]).mode


def _workspace_safe(base: Path) -> bool:
    from tools.catalog.modeling_assessment import paths as ma_paths
    try:
        return ma_paths.persisted_dir_containing(base) is None
    except Exception:                                   # noqa: BLE001 — si no puede verificarse, no se ejecuta
        return False


# ───────────────────────────── ejecución ─────────────────────────────
def run(values: ExtractionValues, client_secret: str, *, user_email: str = "", on_phase=None,
        sleep=time.sleep) -> ExtractionOutcome:
    """Valida y extrae (solo lectura). Devuelve SOLO el resumen saneado; el secreto no se guarda ni se devuelve."""
    from tools.catalog.connectivity_governance import (
        Budget, CliConnectionSource, SafetyError, ephemeral_workdir, real_cli_profile_dir, review_summary, run_probe,
    )

    op_id = uuid.uuid4().hex[:10]
    started = time.monotonic()
    errors = validate(values, client_secret)
    if errors:
        return ExtractionOutcome("INVALID", "INVALID_PARAMETER", MESSAGES["INVALID_PARAMETER"], TITLE, errors=errors, operation_id=op_id)
    try:
        if _launcher_mode() != "direct":
            return _failed("CLI_LAUNCHER_NOT_DIRECT", op_id, started)
    except Exception:                                   # noqa: BLE001 — CLI no resoluble: nada se ejecuta
        return _failed("CLI_UNAVAILABLE", op_id, started)
    base = Path(tempfile.gettempdir())
    if not _workspace_safe(base):
        return _failed("WORKSPACE_UNSAFE", op_id, started)
    if not _CAPACITY.acquire(blocking=False):
        return _failed("CAPACITY_EXCEEDED", op_id, started)
    lock = _lock_for(user_email, values.host)
    if not lock.acquire(blocking=False):
        _CAPACITY.release()
        return _failed("OPERATION_ALREADY_RUNNING", op_id, started)
    try:
        if on_phase is not None:
            on_phase(PHASE_EXTRACTING)
        budget = Budget(**LIMITS)
        spaces = [s for s, _env in values.spaces]
        with ephemeral_workdir(WORK_PREFIX) as wd:
            src = CliConnectionSource(
                host=values.host, client_id=values.client_id, client_secret=client_secret, token_url=values.token_url,
                probe_space=spaces[0], probe_name=values.probe_connection or None, timeout=CALL_TIMEOUT_S,
                work_dir=str(wd), budget=budget,          # perfil aislado + --secrets-file por defecto
            )
            try:
                out = run_probe(src, budget, client=values.client, tenant_id=provisional_tenant_key(values.host),
                                tenant_host=values.host, space_environment_map=dict(values.spaces),
                                expected_spaces=spaces, max_attempts=MAX_ATTEMPTS, sleep=sleep,
                                real_profile=real_cli_profile_dir())    # el perfil REAL del CLI del servidor no debe cambiar
            finally:
                src.close()                               # suelta las referencias al secreto
        summary = review_summary(out)                     # ÚNICO dato que sale de acá (sin hosts/SID/nombres/hashes)
        if out.real_profile_modified:
            log.warning("connectivity_inventory op=%s status=FAILED code=REAL_PROFILE_MODIFIED", op_id)
            return _failed("REAL_PROFILE_MODIFIED", op_id, started, summary)
        if out.result == "ABORTED":
            code = out.abort_code if out.abort_code in MESSAGES else "UNEXPECTED_ERROR"
            return _failed(code, op_id, started, summary)
        if out.result == "PREFLIGHT_INSUFFICIENT":
            return _failed("PREFLIGHT_INSUFFICIENT", op_id, started, summary)
        status = (out.inventory or {}).get("status")
        duration = round(time.monotonic() - started, 2)
        if status == "COMPLETE":
            return ExtractionOutcome("SUCCESS", None, "", "", operation_id=op_id, duration_s=duration, summary=summary)
        if status == "PARTIAL":
            return ExtractionOutcome("PARTIAL", None, "La extracción terminó con errores parciales: revisá la cobertura.",
                                     "Extracción parcial", operation_id=op_id, duration_s=duration, summary=summary)
        return _failed("INVENTORY_FAILED", op_id, started, summary)
    except SafetyError:
        log.warning("connectivity_inventory op=%s status=FAILED code=WORKSPACE_CLEANUP_FAILED", op_id)
        return _failed("WORKSPACE_CLEANUP_FAILED", op_id, started)
    except Exception as exc:                             # noqa: BLE001 — solo el TIPO al log; nunca el mensaje
        log.warning("connectivity_inventory op=%s status=FAILED code=UNEXPECTED_ERROR exc=%s", op_id, type(exc).__name__)
        return _failed("UNEXPECTED_ERROR", op_id, started)
    finally:
        lock.release()
        _CAPACITY.release()
