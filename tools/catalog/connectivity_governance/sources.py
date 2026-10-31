"""Fuentes de conexiones para la extracción F2.1.

- `SimulatedConnectionSource`: cliente Datasphere SIMULADO (puro, sin red) para tests — soporta paginación,
  429/Retry-After, timeouts, espacios inaccesibles y fallo de preflight. Registra toda llamada (solo lectura).
- `CliConnectionSource`: fuente REAL que reutiliza `DatasphereCli` (OAuth client-credentials + CLI). Cableada
  pero **no ejecutada** en F2.1 (requiere un tenant NO productivo para validar el scope de conexiones — gate).
  Import de `DatasphereCli` PEREZOSO para no romper la pureza del paquete ni exigir el CLI al importar.

Ninguna fuente escribe en el tenant/SharePoint/Spaces: solo emite verbos de LECTURA (list/get).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .extraction import ConnectionSource, RetryPolicy, SourceError, sanitize, with_retry


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
                 tenant: str = "sim-tenant"):
        self._spaces = {s: list(v) for s, v in spaces.items()}
        self.page_size = page_size
        self.inaccessible = set(inaccessible or ())
        self.preflight_error = preflight_error
        self._faults = {k: list(v) for k, v in (faults or {}).items()}
        self.retry = retry or RetryPolicy()
        self.tenant = tenant
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


# ── Fuente real (reutiliza DatasphereCli). Cableada; NO ejecutada en F2.1. ─────
@dataclass
class CliConnectionSource:
    """Fuente real: OAuth client-credentials + `datasphere spaces connections list/get` vía `DatasphereCli`.

    Reutiliza el extractor OAuth existente (secreto por argumento, nunca persistido). AISLAMIENTO por
    (tenant, usuario, operación): se construye UNA instancia por ejecución, con su propio `DatasphereCli`
    (HOME temporal aislado); NO se reutilizan sesiones OAuth entre clientes.

    No se ejecuta contra tenants reales en F2.1. El `preflight` valida OAuth y el scope de conexiones
    (listar conexiones de un espacio de prueba); esa validación de compatibilidad real requiere un tenant
    NO productivo (gate F2.1-real).
    """
    host: str
    client_id: str
    client_secret: str              # por argumento; NUNCA se persiste/loguea (se sanitiza todo error)
    token_url: str
    probe_space: Optional[str] = None     # espacio mínimo para validar scope de conexiones en preflight
    _cli: object = field(default=None, repr=False, compare=False)

    def _cli_obj(self):
        if self._cli is None:
            # Import PEREZOSO: no acoplar el paquete puro al CLI pesado.
            from tools.catalog.DSP_CLI_Export_Lineage import DatasphereCli  # type: ignore
            self._cli = DatasphereCli(host=self.host, client_id=self.client_id,
                                      client_secret=self.client_secret, token_url=self.token_url)
        return self._cli

    def preflight(self) -> None:
        cli = self._cli_obj()
        try:
            cli.oauth_preflight()                       # valida client-credentials
            cli.login()                                 # login desatendido
            if self.probe_space:                        # valida SCOPE de conexiones con una lectura mínima
                cli._run(["spaces", "connections", "list", "--space", self.probe_space], expect_json=True)
        except Exception as e:                          # nunca propagar el secreto
            raise SourceError("OAUTH_OR_SCOPE_FAILED", sanitize(e)) from None

    def list_spaces(self) -> list:
        try:
            out = self._cli_obj()._run(["spaces", "list"], expect_json=True)
        except Exception as e:
            raise SourceError("SPACES_LIST_FAILED", sanitize(e)) from None
        return [s.get("name", s) if isinstance(s, dict) else s for s in (out or [])]

    def list_connections(self, space: str) -> list:
        try:
            out = self._cli_obj()._run(["spaces", "connections", "list", "--space", space], expect_json=True)
        except Exception as e:
            raise SourceError("SPACE_INACCESSIBLE", sanitize(e)) from None
        return list(out or [])

    def get_connection(self, space: str, name: str) -> dict:
        try:
            return self._cli_obj()._run(["spaces", "connections", "get", "--space", space, "--name", name], expect_json=True)
        except Exception as e:
            raise SourceError("GET_FAILED", sanitize(e)) from None
