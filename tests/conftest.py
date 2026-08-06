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
