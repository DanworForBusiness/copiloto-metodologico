"""
conftest.py — Setup de tests.

Agrega el repo root a sys.path antes de colectar/importar los test files para que
`import webapp...` / `import core...` resuelvan. (El stub de Streamlit se retiró
junto con el runtime legacy: la webapp no depende de streamlit.)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(autouse=True)
def _isolate_runtime_logs(tmp_path_factory, monkeypatch):
    """Los tests corren con el TestClient autenticado como 'Test User'/'Junior'/
    'Senior' y golpean rutas que loguean actividad. Si el entorno tiene
    LOG_ACTIVITY=VERBOSE (dev/prod), cada corrida contaminaría el activity_log
    REAL con esos personas. Por defecto se apaga el logging y se redirige el dir
    a un tmp: ningún test toca los datos runtime reales. Los tests que prueban el
    logging (ver test_routes.py) hacen su propio monkeypatch y ganan sobre este."""
    from webapp.services import activity_service
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "OFF", raising=False)
    monkeypatch.setattr(
        activity_service, "ACTIVITY_LOG_DIR",
        tmp_path_factory.mktemp("runtime-activity"), raising=False,
    )


@pytest.fixture(autouse=True)
def _isolate_remote_store_persistence(tmp_path_factory, monkeypatch):
    """MISMO criterio que `_isolate_runtime_logs` arriba — Integral Cold
    Start Fase 1 (ver HANDOFF, reporting_time_logs_durable.py): un test que
    activa `PERSIST_ENABLED` real (`config.SPACES_*`) para probar
    `core/remote_store.py`/la persistencia durable de Time Logs puede, si
    corre en la MISMA sesión que otros archivos de test sin coordinarse,
    dejar estado real sobre `config.BASE_DIR` — se detectó una corrida real
    escribiendo bajo `reporting_cache/` en el propio repo (nunca en un
    `tmp_path`), con contenido de un test de OTRO archivo que ni siquiera
    conoce `config`/`PERSIST_ENABLED`. Por defecto, TODO test corre con
    persistencia apagada Y `BASE_DIR` redirigido a un tmp propio — ningún
    test debe poder tocar el repo real vía este mecanismo. Los tests que
    prueban la persistencia misma (test_remote_store_restore_coordination.py,
    test_reporting_time_logs_durable.py,
    test_reporting_time_logs_closed_year_integration.py) hacen su propio
    monkeypatch explícito y ganan sobre este, MISMO criterio que
    `_isolate_runtime_logs`."""
    import config
    monkeypatch.setattr(config, "PERSIST_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path_factory.mktemp("runtime-base-dir"), raising=False)
    monkeypatch.setattr(config, "PERSIST_READ_ONLY", False, raising=False)
    # Estado de sincronización del remote_store (módulo con estado de proceso): un test que lo deja "degradado" o con archivos sin reconciliar NO puede bloquear
    # el `mirror_up` de otro.
    from core import remote_store
    monkeypatch.setattr(remote_store, "_restore_degraded", False, raising=False)
    monkeypatch.setattr(remote_store, "_unreconciled", set(), raising=False)


def _case_listing(path) -> set[str]:
    import os
    try:
        return set(os.listdir(path))
    except OSError:
        return set()


@pytest.fixture(autouse=True)
def _isolate_case_library(tmp_path_factory, monkeypatch):
    """Ningún test puede escribir en la `case_library` REAL. Los tests que recorren un árbol del Wizard hasta una hoja crean un caso (`create_for_run`) en
    `config.CASE_LIBRARY_DIR`; ese valor se capturaba al importar (el singleton de `copilot_runtime` y `BASE_DIR` redirigido no lo cubrían), así que cada
    corrida completa dejaba ~10 casos `case_*.yaml` en el directorio real (que luego un `mirror_up` con credenciales intentaba subir al bucket). Se redirige a un tmp
    y, al terminar el test, se verifica que el directorio real no recibió archivos nuevos."""
    import config
    real = config.CASE_LIBRARY_DIR
    before = _case_listing(real)
    monkeypatch.setattr(config, "CASE_LIBRARY_DIR", tmp_path_factory.mktemp("runtime-case-library"))
    yield
    leaked = sorted(_case_listing(real) - before)
    assert not leaked, f"el test escribió en la case_library REAL ({real}): {leaked[:5]} (¿otro proceso escribiendo a la vez?)"
