"""Primitivas de SEGURIDAD de ejecución para la extracción de conectividad (F2.1c).

Reutilizables y testeables (por eso viven en el paquete, no en el script de sonda de `_handoffs/`, que no es
importable desde una exportación limpia). No cambian el motor F1 ni el contrato; son controles de ejecución:

- `assert_allowlisted`: valida host + token endpoint contra una allowlist EXACTA (sin coincidencias parciales
  ni inferencias por nombre) ANTES de cualquier autenticación/red.
- `Budget` / `BudgetedSource`: límites GLOBALES (cantidad de solicitudes, duración máxima, reintentos totales).
  Al agotarse un límite se lanza `BudgetError`, que NO es `SourceError` → el orquestador no la captura como error
  por-operación: propaga y ABORTA. Nunca se presenta COMPLETE/VERIFIED tras una interrupción por presupuesto.
- `ephemeral_workdir`: workspace temporal que se borra SIEMPRE (incluso ante error).
"""
from __future__ import annotations

import shutil
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable


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

    def _check_duration(self) -> None:
        if self.elapsed() > self.max_duration_s:
            raise BudgetError("DURATION_EXCEEDED",
                              f"duración máxima superada ({self.max_duration_s}s)")

    def charge_request(self) -> None:
        self._check_duration()
        self.requests_used += 1
        if self.requests_used > self.max_requests:
            raise BudgetError("REQUESTS_EXCEEDED",
                              f"presupuesto de solicitudes agotado ({self.max_requests})")

    def charge_retry(self) -> None:
        self.retries_used += 1
        if self.retries_used > self.max_total_retries:
            raise BudgetError("RETRIES_EXCEEDED",
                              f"reintentos totales agotados ({self.max_total_retries})")
        self._check_duration()

    def retry_sleep(self, real_sleep: Callable[[float], None]) -> Callable[[float], None]:
        """Devuelve un `sleep` para RetryPolicy que COBRA un reintento global antes de dormir."""
        def _sleep(seconds: float) -> None:
            self.charge_retry()
            real_sleep(seconds)
        return _sleep


class BudgetedSource:
    """Envuelve un ConnectionSource y cobra 1 solicitud por operación de lectura (≈ una llamada CLI; los
    reintentos se cuentan aparte en el presupuesto de reintentos). Propaga BudgetError → aborta la extracción."""
    def __init__(self, inner, budget: Budget, *, skip_preflight: bool = False):
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


@contextmanager
def ephemeral_workdir(prefix: str = "dsp-conn-"):
    """Directorio temporal que se borra SIEMPRE (incluso ante error)."""
    d = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)
