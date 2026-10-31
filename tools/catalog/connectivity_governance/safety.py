"""Primitivas de SEGURIDAD de ejecución para la extracción de conectividad (F2.1c).

Reutilizables y testeables (por eso viven en el paquete, no en el script de sonda de `_handoffs/`, que no es
importable desde una exportación limpia). No cambian el motor F1 ni el contrato; son controles de ejecución:

- `assert_allowlisted`: valida host + token endpoint contra una allowlist EXACTA (sin coincidencias parciales
  ni inferencias por nombre) ANTES de cualquier autenticación/red.
- `Budget` / `BudgetedSource`: límites GLOBALES (cantidad de solicitudes, duración máxima, reintentos totales).
  Al agotarse un límite se lanza `BudgetError`, que NO es `SourceError` → el orquestador no la captura como error
  por-operación: propaga y ABORTA. Nunca se presenta COMPLETE/VERIFIED tras una interrupción por presupuesto.
- `ephemeral_workdir`: workspace temporal que se borra SIEMPRE (incluso ante error) y cuyo borrado se verifica.
- `profile_fingerprint`: huella de metadatos del perfil REAL del CLI para demostrar que no se tocó.
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional


class SafetyError(Exception):
    """Falla de un control de seguridad previo a la red (allowlist, confirmación)."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class BudgetError(Exception):
    """Se agotó un límite global de ejecución. NO es SourceError: aborta, no degrada a PARTIAL."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def assert_devqa_confirmed(value: str | None) -> None:
    """Confirmación explícita de tenant NO productivo. Debe ser exactamente 'DEVQA'."""
    if value != "DEVQA":
        raise SafetyError("DEVQA_NOT_CONFIRMED",
                          "falta la confirmación explícita de tenant NO productivo (DSP_ENV_CONFIRM=DEVQA)")


def assert_required(mapping: dict, names: Iterable[str]) -> None:
    """Exige que estén TODAS las claves de configuración obligatoria (no vacías)."""
    missing = [n for n in names if not (mapping.get(n) or "").strip()]
    if missing:
        raise SafetyError("CONFIG_INCOMPLETE", "falta configuración obligatoria: " + ", ".join(missing))


def assert_allowlisted(host: str, token_url: str, *, allowed_hosts: Iterable[str],
                       allowed_token_urls: Iterable[str]) -> None:
    """Rechaza host/token endpoint que no coincidan EXACTAMENTE con los autorizados. Sin substrings ni
    inferencias. Se llama ANTES de construir la fuente o tocar la red."""
    hosts = {h.strip() for h in allowed_hosts if h and h.strip()}
    tokens = {t.strip() for t in allowed_token_urls if t and t.strip()}
    if not hosts or not tokens:
        raise SafetyError("ALLOWLIST_EMPTY",
                          "allowlist vacía: configurá los hosts y token endpoints DEV/QA autorizados")
    if (host or "").strip() not in hosts:
        raise SafetyError("HOST_NOT_ALLOWLISTED", "el host no coincide EXACTAMENTE con un host DEV/QA autorizado")
    if (token_url or "").strip() not in tokens:
        raise SafetyError("TOKEN_URL_NOT_ALLOWLISTED",
                          "el token endpoint no coincide EXACTAMENTE con uno DEV/QA autorizado")


@dataclass
class Budget:
    """Límites GLOBALES de la sonda. `clock` inyectable para tests (sin esperas reales)."""
    max_requests: int
    max_duration_s: float
    max_total_retries: int
    clock: Callable[[], float] = time.monotonic
    _start: float = field(default=None, repr=False)
    requests_used: int = 0
    retries_used: int = 0

    def start(self) -> None:
        self._start = self.clock()

    def elapsed(self) -> float:
        return 0.0 if self._start is None else (self.clock() - self._start)

    def remaining(self) -> float:
        return self.max_duration_s - self.elapsed()

    def _check_duration(self) -> None:
        if self.remaining() <= 0:
            raise BudgetError("DURATION_EXCEEDED",
                              f"duración máxima superada ({self.max_duration_s}s)")

    def assert_within(self) -> None:
        """Respaldo POSTERIOR (no el único control): una ejecución que terminó fuera de la duración máxima no se
        presenta como resultado válido."""
        self._check_duration()

    def charge_request(self) -> None:
        self._check_duration()
        self.requests_used += 1
        if self.requests_used > self.max_requests:
            raise BudgetError("REQUESTS_EXCEEDED",
                              f"presupuesto de solicitudes agotado ({self.max_requests})")

    def call_timeout(self, op_timeout: Optional[float]) -> float:
        """Cobra UNA llamada FÍSICA (subprocess/HTTP) y devuelve el timeout a usar: el menor entre el de la
        operación y el tiempo RESTANTE del presupuesto. Así ninguna llamada bloqueante puede exceder la duración
        global (cota previa, no solo chequeo posterior)."""
        self.charge_request()
        remaining = self.remaining()
        return remaining if op_timeout is None else min(op_timeout, remaining)

    def charge_retry(self) -> None:
        self.retries_used += 1
        if self.retries_used > self.max_total_retries:
            raise BudgetError("RETRIES_EXCEEDED",
                              f"reintentos totales agotados ({self.max_total_retries})")
        self._check_duration()

    def retry_sleep(self, real_sleep: Callable[[float], None]) -> Callable[[float], None]:
        """Devuelve un `sleep` para RetryPolicy que COBRA un reintento global antes de dormir. Si la espera
        (backoff o Retry-After) no cabe en el tiempo restante, ABORTA en vez de dormir más allá del límite."""
        def _sleep(seconds: float) -> None:
            self.charge_retry()
            if seconds >= self.remaining():
                raise BudgetError("DURATION_EXCEEDED",
                                  f"la espera de reintento ({seconds}s) excede la duración máxima restante "
                                  f"({self.max_duration_s}s en total)")
            real_sleep(seconds)
        return _sleep


class BudgetedSource:
    """Envuelve un ConnectionSource y cobra 1 solicitud por operación de lectura (≈ una llamada CLI; los
    reintentos se cuentan aparte en el presupuesto de reintentos). Propaga BudgetError → aborta la extracción.

    Para fuentes SIN medición propia (p.ej. el simulado). Una fuente que ya mide sus llamadas FÍSICAS contra el
    mismo presupuesto (`CliConnectionSource(budget=…)`) NO se envuelve: sería doble conteo → se rechaza."""
    def __init__(self, inner, budget: Budget, *, skip_preflight: bool = False):
        if getattr(inner, "budget", None) is not None:
            raise ValueError("la fuente ya mide sus llamadas físicas contra un presupuesto: "
                             "envolverla en BudgetedSource duplicaría el conteo")
        self._inner = inner
        self._budget = budget
        self._skip_preflight = skip_preflight     # True cuando el preflight ya se validó aparte (preflight_report)
        self.guarantees_exhaustive_list = bool(getattr(inner, "guarantees_exhaustive_list", False))

    def preflight(self):
        if self._skip_preflight:
            return None                            # ya validado vía preflight_report(); no se re-ejecuta
        self._budget.charge_request()
        return self._inner.preflight()

    def list_spaces(self):
        self._budget.charge_request()
        return self._inner.list_spaces()

    def list_connections(self, space):
        self._budget.charge_request()
        return self._inner.list_connections(space)

    def get_connection(self, space, name):
        self._budget.charge_request()
        return self._inner.get_connection(space, name)


def remove_tree_verified(path: Path, *, attempts: int = 5, wait_s: float = 0.2,
                         sleep: Callable[[float], None] = time.sleep) -> None:
    """Borra `path` y VERIFICA que ya no exista (en Windows un proceso hijo rezagado puede retener archivos).
    Si no se pudo, lanza SafetyError: un perfil del CLI con secrets.json residual no se oculta."""
    def _onerror(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    for i in range(attempts):
        if not path.exists():
            return
        shutil.rmtree(path, **({"onexc": _onerror} if sys.version_info >= (3, 12) else {"onerror": _onerror}))
        if not path.exists():
            return
        if i < attempts - 1:
            sleep(wait_s)
    raise SafetyError("WORKDIR_CLEANUP_FAILED",
                      f"no se pudo borrar el workspace efímero (puede contener credenciales del CLI): {path}")


@contextmanager
def ephemeral_workdir(prefix: str = "dsp-conn-"):
    """Directorio temporal que se borra SIEMPRE (incluso ante error) y cuyo borrado se VERIFICA."""
    d = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield d
    finally:
        remove_tree_verified(d)


def real_cli_profile_dir(cli_name: str = "datasphere-cli") -> Path:
    """Directorio del perfil REAL del CLI del operador (lo que el aislamiento NO debe tocar)."""
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return Path(home) / ".@sap" / cli_name


def profile_fingerprint(path: Path) -> tuple:
    """Huella de SOLO METADATOS (ruta relativa, tamaño, mtime) — nunca lee contenidos. Sirve para demostrar en
    runtime que una ejecución aislada no modificó el perfil real del CLI."""
    if not path.exists():
        return ()
    out = []
    for f in sorted(path.rglob("*")):
        if f.is_file():
            st = f.stat()
            out.append((str(f.relative_to(path)), st.st_size, st.st_mtime_ns))
    return tuple(out)
