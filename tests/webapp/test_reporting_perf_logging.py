"""
test_reporting_perf_logging.py — REPORTING PERF LOGGER: ENABLE EXISTING
PRODUCTION TIMING LOGGER (owner ticket, ver AUDIT "Cartera Proyecto Cold
Penalty Source Attribution" §4/§9 MEASURE).

Toda la instrumentación de timing de Reporting/Cartera (reporting_repository.py/
reporting_service.py/reporting_time_logs_durable.py, logger
`danwor.reporting.perf`) ya existía y ya era segura (solo elapsed_ms/
conteos) — el único problema era que sus `.info(...)` se descartaban antes
de llegar a cualquier handler, porque ningún archivo de este repo configura
logging (cero `basicConfig`/`dictConfig`) y el root logger queda en su
default `WARNING`. `webapp/main.py::_configure_reporting_perf_logging`
habilita ÚNICAMENTE ese logger nombrado, en el bootstrap del módulo — este
archivo prueba esa configuración, nunca vuelve a implementarla.

Import de `webapp.main` (módulo real, no un mock) a propósito: la
configuración corre UNA vez al importar el módulo — exactamente lo que pasa
en producción bajo `python -m uvicorn webapp.main:app` (ver Procfile) — así
que estos tests verifican el estado REAL post-bootstrap, no una simulación."""
from __future__ import annotations

import io
import logging
import sys

from webapp import main as web_main

_LOGGER_NAME = web_main._REPORTING_PERF_LOGGER_NAME
_HANDLER_NAME = web_main._REPORTING_PERF_HANDLER_NAME


def _our_handlers(logger):
    # Filtra por NOMBRE propio (`Handler.set_name`, stdlib) — nunca asume
    # "el único handler presente", porque el propio plugin de logging de
    # pytest puede adjuntar los suyos directo a un logger con
    # `propagate=False` (observado en esta suite: LogCaptureHandler/
    # _FileHandler/etc. conviviendo acá) — nada de esto lo agregó
    # `_configure_reporting_perf_logging`, así que no debe contar.
    return [h for h in logger.handlers if h.get_name() == _HANDLER_NAME]


def test_reporting_perf_logger_info_is_enabled():
    logger = logging.getLogger(_LOGGER_NAME)
    assert logger.level == logging.INFO
    assert logger.getEffectiveLevel() == logging.INFO


def test_reporting_perf_logger_has_exactly_one_own_stdout_stream_handler():
    logger = logging.getLogger(_LOGGER_NAME)
    ours = _our_handlers(logger)
    assert len(ours) == 1
    handler = ours[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stdout


def test_reporting_perf_logger_does_not_propagate_to_root():
    logger = logging.getLogger(_LOGGER_NAME)
    assert logger.propagate is False


def test_emitted_perf_record_reaches_the_configured_handler(monkeypatch):
    # Redirige el `stream` del handler YA configurado a un buffer propio —
    # nunca se re-configura el logger, solo se observa lo que emite.
    logger = logging.getLogger(_LOGGER_NAME)
    handler = _our_handlers(logger)[0]
    buf = io.StringIO()
    monkeypatch.setattr(handler, "stream", buf)
    logger.info("reporting.perf cartera.horas_delta mode=cold pages=3 rows=1786")
    output = buf.getvalue()
    assert "reporting.perf cartera.horas_delta mode=cold pages=3 rows=1786" in output
    # Formato compacto reconocible: nombre del logger + nivel + timestamp,
    # sin reescribir el contenido del mensaje existente.
    assert _LOGGER_NAME in output
    assert "INFO" in output


def test_repeated_bootstrap_does_not_duplicate_the_handler():
    logger = logging.getLogger(_LOGGER_NAME)
    before = len(_our_handlers(logger))
    web_main._configure_reporting_perf_logging()
    web_main._configure_reporting_perf_logging()
    after = len(_our_handlers(logger))
    assert before == after == 1


def test_root_logger_is_never_touched_by_this_configuration():
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    level_before = root.level
    web_main._configure_reporting_perf_logging()
    assert root.handlers == handlers_before
    assert root.level == level_before


def test_general_reporting_logger_without_perf_suffix_is_untouched():
    # `danwor.reporting` (SIN `.perf`) es un logger DISTINTO — este ticket es
    # explícito: "do not enable danwor.reporting general diagnostics".
    general = logging.getLogger("danwor.reporting")
    assert general.level == logging.NOTSET
    assert general.handlers == []


def test_uvicorn_loggers_are_never_touched_by_this_configuration():
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        handlers_before = list(logger.handlers)
        propagate_before = logger.propagate
        web_main._configure_reporting_perf_logging()
        assert logger.handlers == handlers_before
        assert logger.propagate == propagate_before
