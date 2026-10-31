"""Fuentes de conexiones para la extracción F2.1.

- `SimulatedConnectionSource`: cliente Datasphere SIMULADO (puro, sin red) para tests — soporta paginación,
  429/Retry-After, timeouts, espacios inaccesibles y fallo de preflight. Registra toda llamada (solo lectura).
- `CliConnectionSource`: fuente REAL que reutiliza `DatasphereCli` (OAuth client-credentials + CLI). Cableada
  pero **no ejecutada** en F2.1 (requiere un tenant NO productivo para validar el scope de conexiones — gate).
  Import de `DatasphereCli` PEREZOSO para no romper la pureza del paquete ni exigir el CLI al importar.

Ninguna fuente escribe en el tenant/SharePoint/Spaces: solo emite verbos de LECTURA (list/get).
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from .extraction import ConnectionSource, RetryPolicy, SourceError, sanitize, with_retry
from .safety import Budget, BudgetError, remove_tree_verified


# ── Helpers de faults para el simulado ────────────────────────────────────────
def rate_limited(retry_after: float = 1.0) -> SourceError:
    return SourceError("RATE_LIMITED", "429 Too Many Requests", retriable=True, retry_after=retry_after)


def timed_out() -> SourceError:
    return SourceError("TIMEOUT", "la llamada excedió el tiempo límite", retriable=True)


class SimulatedConnectionSource:
    """Cliente Datasphere simulado. `spaces` = {space: [doc, ...]} (docs con forma conn_space_*.json).

    `faults` = {(op, space[, extra]): [SourceError, ...]} — cada clave consume un error por llamada (FIFO),
    permitiendo "falla una vez y luego responde". `op` ∈ {"list","get"}; para "get" extra = connection name;
    para "list" extra opcional = índice de página.
    """
    def __init__(self, spaces: dict, *, page_size: Optional[int] = None,
                 inaccessible: Optional[set] = None, preflight_error: Optional[SourceError] = None,
                 faults: Optional[dict] = None, retry: Optional[RetryPolicy] = None,
                 tenant: str = "sim-tenant", guarantees_exhaustive_list: bool = True):
        self._spaces = {s: list(v) for s, v in spaces.items()}
        self.page_size = page_size
        self.inaccessible = set(inaccessible or ())
        self.preflight_error = preflight_error
        self._faults = {k: list(v) for k, v in (faults or {}).items()}
        self.retry = retry or RetryPolicy()
        self.tenant = tenant
        # El simulado CONOCE el conjunto completo de datos → por defecto puede garantizar exhaustividad.
        # Se puede poner False para imitar el caso del CLI (array sin cursor/total → UNVERIFIABLE).
        self.guarantees_exhaustive_list = guarantees_exhaustive_list
        self.calls: list = []          # registro de operaciones de LECTURA (para el test de "no escrituras")

    def _pop_fault(self, *key):
        q = self._faults.get(key)
        if q:
            return q.pop(0)
        return None

    def preflight(self) -> None:
        self.calls.append(("preflight", self.tenant))
        if self.preflight_error is not None:
            raise self.preflight_error

    def list_spaces(self) -> list:
        self.calls.append(("list_spaces", self.tenant))
        return list(self._spaces.keys())

    def list_connections(self, space: str) -> list:
        if space in self.inaccessible:
            raise SourceError("SPACE_INACCESSIBLE", f"espacio '{space}' inaccesible")
        conns = self._spaces.get(space, [])
        size = self.page_size or (len(conns) or 1)
        pages = [conns[i:i + size] for i in range(0, len(conns), size)] or [[]]
        out: list = []
        for idx, page in enumerate(pages):
            def fetch(idx=idx, page=page):
                err = self._pop_fault("list", space, idx)
                if err is not None:
                    raise err
                self.calls.append(("list", space, idx))
                return page
            # La fuente resuelve su paginación y los 429 por página (Retry-After honrado por with_retry).
            out.extend(with_retry(fetch, self.retry))
        return [{"name": c["name"]} for c in out]

    def get_connection(self, space: str, name: str) -> dict:
        err = self._pop_fault("get", space, name)
        if err is not None:
            raise err
        self.calls.append(("get", space, name))
        for c in self._spaces.get(space, []):
            if c.get("name") == name:
                return c
        raise SourceError("GET_FAILED", f"conexión '{name}' no encontrada en '{space}'")


# ── Clasificación verificable de errores del CLI ──────────────────────────────
_RETRY_AFTER_RE = re.compile(r"retry[-\s]?after[:\s]+(\d+(?:\.\d+)?)", re.I)


def _parse_retry_after(msg: str) -> Optional[float]:
    m = _RETRY_AFTER_RE.search(msg)
    return float(m.group(1)) if m else None


def classify_cli_error(exc: Exception, *, secrets: tuple = ()) -> SourceError:
    """Mapea una excepción del CLI a un SourceError ESTRUCTURADO y verificable.

    Regla de reintento: solo 429/Retry-After y TIMEOUT son retriables. Los errores de AUTORIZACIÓN
    (401/403) y de ejecución/JSON NO se reintentan (no se asume que todo error del CLI es transitorio).
    La clasificación 429/401/403 depende de que el CLI exponga esos datos en stderr; si no, cae a
    CLI_EXECUTION_ERROR (no-retriable) — fail-safe, nunca retriable por defecto.
    `secrets`: valores conocidos que se borran literalmente del mensaje (además de los patrones de `sanitize`).
    """
    if isinstance(exc, subprocess.TimeoutExpired):
        return SourceError("TIMEOUT", "la operación del CLI excedió el tiempo límite", retriable=True)
    msg = sanitize(exc, secrets)
    low = msg.lower()
    if "429" in msg or "too many requests" in low or "rate limit" in low:
        return SourceError("RATE_LIMITED", msg, retriable=True, retry_after=_parse_retry_after(msg))
    if "403" in msg or "forbidden" in low or "permission" in low or "insufficient" in low or "not authorized" in low:
        return SourceError("SCOPE_DENIED", msg, retriable=False)        # autorización: NO reintentar
    if "401" in msg or "unauthorized" in low:
        return SourceError("AUTH_401", msg, retriable=False)            # autorización: NO reintentar
    if "could not parse" in low or "empty output" in low or "expected json" in low:
        return SourceError("INVALID_OR_INCOMPLETE_JSON", msg, retriable=False)
    if "cli_unavailable" in low or "no se pudo iniciar" in low:
        return SourceError("CLI_UNAVAILABLE", msg, retriable=False)
    return SourceError("CLI_EXECUTION_ERROR", msg, retriable=False)     # no asumir transitorio


# ── Preflight de permisos separado (cada eslabón: OK / FAILED / NOT_VERIFIED) ──
class CheckStatus(Enum):
    OK = "OK"
    FAILED = "FAILED"
    NOT_VERIFIED = "NOT_VERIFIED"       # NO ejecutada ⇒ nunca "autorizada"


@dataclass(frozen=True)
class PreflightCheck:
    name: str                          # oauth_token | list_spaces | list_connections | read_detail
    status: CheckStatus
    detail: str = ""                   # sanitizado


@dataclass(frozen=True)
class PreflightReport:
    checks: tuple

    def status_of(self, name: str) -> CheckStatus:
        for c in self.checks:
            if c.name == name:
                return c.status
        return CheckStatus.NOT_VERIFIED

    def authorized_for_extraction(self) -> bool:
        # Mínimo para extraer: token OK y poder listar conexiones. El resto se maneja por-operación.
        return self.status_of("oauth_token") == CheckStatus.OK and self.status_of("list_connections") == CheckStatus.OK

    def to_dict(self) -> dict:
        return {c.name: {"status": c.status.value, "detail": c.detail} for c in self.checks}


# ── Fuente real (reutiliza DatasphereCli). Cableada; NO ejecutada contra tenants reales. ─────
@dataclass
class CliConnectionSource:
    """Fuente real: OAuth client-credentials + `datasphere spaces connections list/get` vía `DatasphereCli`.

    Reutiliza el extractor OAuth existente. AISLAMIENTO por (tenant, usuario, operación): UNA instancia por
    ejecución, con su propio `work_dir`; por defecto el CLI corre con un PERFIL AISLADO (USERPROFILE/HOME/APPDATA…
    redirigidos al workspace y entorno mínimo: ver `isolated_cli_env`) y el login usa `--secrets-file` efímero
    (el secreto no va por argv). NO se reutilizan sesiones OAuth entre clientes. Toda operación lleva `timeout`.

    PRESUPUESTO (opcional, `budget`): cada llamada FÍSICA (HTTP OAuth, cada subprocess del CLI, incluidos
    preflight y reintentos) cobra 1 solicitud y recibe como timeout el menor entre `timeout` y el tiempo RESTANTE;
    si el corte fue por el presupuesto ⇒ BudgetError (aborta), nunca un error parcial. No combinar con
    `BudgetedSource` (doble conteo; se rechaza).

    EXHAUSTIVIDAD (brecha declarada, NO resuelta): `datasphere spaces connections list --space <SPACE>`
    devuelve un array en UNA llamada y NO expone cursor/total/hasMore (verificado por la guía y por los
    exports reales). Por lo tanto la exhaustividad NO es verificable desde el resultado. Este adaptador
    NO inventa paginación y declara `guarantees_exhaustive_list = False`: la cobertura reportada refleja lo
    que el CLI devolvió, no una exhaustividad comprobada. (Si en el futuro el CLI expone cursor/total, se
    contrasta e itera entonces.) No ejecutar contra tenants reales hasta el gate F2.1-real.
    """
    host: str
    client_id: str
    client_secret: str              # por argumento; NUNCA se persiste/loguea (todo error se sanitiza)
    token_url: str
    probe_space: Optional[str] = None     # espacio para validar scope list_connections en el preflight
    probe_name: Optional[str] = None      # conexión para validar scope read_detail en el preflight
    timeout: float = 120.0                # tiempo máximo por operación (seg); al vencer → SourceError TIMEOUT
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    work_dir: Optional[str] = None
    isolated_profile: bool = True         # perfil del CLI aislado del perfil real del operador
    use_secrets_file: bool = True         # login sin secreto en argv
    budget: Optional[Budget] = None       # medición de llamadas FÍSICAS + cota de duración
    _cli: object = field(default=None, repr=False, compare=False)
    _owned_dir: Optional[Path] = field(default=None, repr=False, compare=False)
    _last_preflight: Optional["PreflightReport"] = field(default=None, repr=False, compare=False)

    guarantees_exhaustive_list: bool = False   # el CLI no expone cursor/total → exhaustividad no verificable

    def _cli_obj(self):
        if self._cli is None:
            from tools.catalog.DSP_CLI_Export_Lineage import DatasphereCli  # import PEREZOSO
            if self.work_dir:
                wd = Path(self.work_dir)
            else:
                wd = self._owned_dir = Path(tempfile.mkdtemp(prefix="dsp-conn-"))   # se borra en close()
            self._cli = DatasphereCli(host=self.host, client_id=self.client_id,
                                      client_secret=self.client_secret, token_url=self.token_url, work_dir=wd,
                                      isolated_profile=self.isolated_profile)
        return self._cli

    def _secrets(self) -> tuple:
        return (self.client_secret, getattr(self._cli, "client_secret", None))

    def _physical(self, fn: Callable[[Optional[float]], object], op_timeout: Optional[float]):
        """Ejecuta UNA llamada física bajo el presupuesto (si hay): cobra, acota el timeout al tiempo restante y
        convierte un corte por presupuesto en BudgetError (aborto explícito)."""
        if self.budget is None:
            return fn(op_timeout)
        t = self.budget.call_timeout(op_timeout)
        capped = op_timeout is None or t < op_timeout
        try:
            return fn(t)
        except subprocess.TimeoutExpired:
            if not capped:
                raise
        raise BudgetError("DURATION_EXCEEDED",
                          f"la llamada se cortó al agotarse la duración máxima ({self.budget.max_duration_s}s)")

    def _call(self, args: list):
        """Llamada CLI de lectura → JSON. Errores clasificados y SANITIZADOS; el SourceError se lanza FUERA del
        bloque except (sin __context__ que arrastre la excepción original). BudgetError propaga intacto."""
        try:
            out = self._physical(lambda t: self._cli_obj()._run(args, expect_json=True, timeout=t), self.timeout)
        except BudgetError:
            raise
        except Exception as e:
            err = classify_cli_error(e, secrets=self._secrets())
        else:
            return out
        raise err

    def _run_json(self, args: list):
        return self._call(args)

    # — Preflight separado: cada permiso se verifica por separado; lo no ejecutado queda NOT_VERIFIED —
    def preflight_report(self) -> PreflightReport:
        checks = []
        cli = self._cli_obj()
        # 1) OAuth válido (token + login desatendido)
        oauth_timeout = min(30.0, self.timeout) if self.timeout else 30.0
        detail = None
        try:
            self._physical(lambda t: cli.oauth_preflight(timeout=t), oauth_timeout)
            self._physical(lambda t: cli.login(timeout=t, use_secrets_file=self.use_secrets_file), self.timeout)
        except BudgetError:
            raise
        except Exception as e:
            # TimeoutExpired → mensaje fijo; el resto se sanitiza con patrones + secreto conocido. Nunca str(e) crudo.
            detail = classify_cli_error(e, secrets=self._secrets()).message
        if detail is None:
            checks.append(PreflightCheck("oauth_token", CheckStatus.OK))
            oauth_ok = True
        else:
            checks.append(PreflightCheck("oauth_token", CheckStatus.FAILED, detail))
            oauth_ok = False
        # Sin OAuth no se puede verificar nada más ⇒ NOT_VERIFIED (nunca "autorizado").
        if not oauth_ok:
            for n in ("list_spaces", "list_connections", "read_detail"):
                checks.append(PreflightCheck(n, CheckStatus.NOT_VERIFIED, "no ejecutada: OAuth no válido"))
            return self._remember(PreflightReport(tuple(checks)))
        # 2) Listar espacios
        try:
            self._call(["spaces", "list"])
            checks.append(PreflightCheck("list_spaces", CheckStatus.OK))
        except SourceError as e:
            checks.append(PreflightCheck("list_spaces", CheckStatus.FAILED, e.message))
        # 3) Listar conexiones (requiere probe_space)
        if self.probe_space:
            try:
                self._call(["spaces", "connections", "list", "--space", self.probe_space])
                checks.append(PreflightCheck("list_connections", CheckStatus.OK))
                conn_ok = True
            except SourceError as e:
                checks.append(PreflightCheck("list_connections", CheckStatus.FAILED, e.message))
                conn_ok = False
        else:
            checks.append(PreflightCheck("list_connections", CheckStatus.NOT_VERIFIED, "no ejecutada: falta probe_space"))
            conn_ok = False
        # 4) Leer detalle (requiere probe_space + probe_name)
        if self.probe_space and self.probe_name and conn_ok:
            try:
                self._call(["spaces", "connections", "get", "--space", self.probe_space, "--name", self.probe_name])
                checks.append(PreflightCheck("read_detail", CheckStatus.OK))
            except SourceError as e:
                checks.append(PreflightCheck("read_detail", CheckStatus.FAILED, e.message))
        else:
            checks.append(PreflightCheck("read_detail", CheckStatus.NOT_VERIFIED, "no ejecutada: falta probe_name o list_connections no OK"))
        return self._remember(PreflightReport(tuple(checks)))

    def _remember(self, report: PreflightReport) -> PreflightReport:
        self._last_preflight = report
        return report

    def preflight(self) -> None:
        # Contrato del orquestador: lanza SourceError si el mínimo (OAuth) no está. El detalle por eslabón
        # está en preflight_report(); una operación NO ejecutada nunca cuenta como autorizada. Si esta instancia
        # ya corrió preflight_report() (la sonda), se reutiliza: el preflight NO se re-ejecuta ni se re-cobra.
        report = self._last_preflight or self.preflight_report()
        if report.status_of("oauth_token") != CheckStatus.OK:
            detail = next((c.detail for c in report.checks if c.name == "oauth_token"), "")
            raise SourceError("OAUTH_FAILED", detail or "OAuth no válido", retriable=False)

    def list_spaces(self) -> list:
        out = self._call(["spaces", "list"])            # el orquestador reintenta si es retriable
        return [s.get("name", s) if isinstance(s, dict) else s for s in (out or [])]

    def list_connections(self, space: str) -> list:
        # El orquestador NO reintenta list_connections (es responsabilidad de la fuente): acá sí reintentamos
        # los transitorios (429/TIMEOUT) con la política propia. NO se pagina (ver docstring: exhaustividad
        # no verificable); una sola llamada.
        def call():
            return list(self._call(["spaces", "connections", "list", "--space", space]) or [])
        return with_retry(call, self.retry)

    def get_connection(self, space: str, name: str) -> dict:
        return self._call(["spaces", "connections", "get", "--space", space, "--name", name])  # el orquestador reintenta

    def close(self) -> None:
        """Suelta las referencias al secreto y borra (verificado) el workspace PROPIO si lo creó esta instancia.
        Un `work_dir` provisto por el llamador lo limpia el llamador (p.ej. `ephemeral_workdir`)."""
        self.client_secret = None
        if self._cli is not None:
            self._cli.client_secret = None
        if self._owned_dir is not None:
            owned, self._owned_dir = self._owned_dir, None
            remove_tree_verified(owned)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
