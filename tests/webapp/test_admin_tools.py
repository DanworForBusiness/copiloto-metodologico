import io
import zipfile

import pytest
from fastapi.testclient import TestClient

import webapp.main as web_main
from webapp.main import app
from webapp.services import auth_service

# Las herramientas de gestión solo las habilita el dueño (APP_OWNER) en modo
# diagnóstico. En tests, el usuario de prueba ES el owner.
web_main.APP_OWNER = "test@danwor.com"

_SESSION = auth_service.create_session(
    {"email": "test@danwor.com", "name": "Test User", "first_name": "Test", "family_name": "User"}
)

# Cliente normal (logueado, sin diagnóstico) y cliente en modo diagnóstico.
client = TestClient(app)
client.cookies.set(auth_service.COOKIE_NAME, _SESSION)

diag_client = TestClient(app)
diag_client.cookies.set(auth_service.COOKIE_NAME, _SESSION)
diag_client.cookies.set("dw-diagnostic", "on")


@pytest.fixture(autouse=True)
def _isolate_report_dirs(monkeypatch, tmp_path):
    """Los scripts escriben en reports/ y trees/proposed/ (archivos trackeados).

    Redirigimos sus directorios de salida a tmp para no ensuciar el repo al
    correr la suite. Las fuentes de lectura (case_library/, feedback_log/) quedan
    reales para que el análisis tenga datos.
    """
    import analyze_cases
    import analyze_feedback
    import propose_tree_updates
    import propose_content_updates

    reports = tmp_path / "reports"
    monkeypatch.setattr(analyze_cases, "REPORTS_DIR", reports, raising=False)
    monkeypatch.setattr(analyze_feedback, "REPORTS_DIR", reports, raising=False)
    monkeypatch.setattr(propose_tree_updates, "REPORTS_DIR", reports, raising=False)
    monkeypatch.setattr(propose_tree_updates, "TREES_DIR", tmp_path / "trees", raising=False)
    monkeypatch.setattr(propose_content_updates, "REPORTS_DIR", tmp_path / "reports_cp", raising=False)


_ADMIN_GET = ["/admin/runtime/inventory", "/admin/runtime/export.zip"]
_ADMIN_POST = [
    ("/admin/cases/analyze", {}),
    ("/admin/feedback/analyze", {"kind": "all"}),
    ("/admin/trees/propose", {}),
    ("/admin/content/propose", {}),
]


# ── Gating ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", _ADMIN_GET)
def test_admin_get_requires_diagnostic(path):
    assert client.get(path).status_code == 403
    assert diag_client.get(path).status_code == 200


@pytest.mark.parametrize("path,data", _ADMIN_POST)
def test_admin_post_requires_diagnostic(path, data):
    assert client.post(path, data=data).status_code == 403
    assert diag_client.post(path, data=data).status_code == 200


# ── Panel en la página de módulo ──────────────────────────────────────────

def test_gestion_group_shows_tools_only_in_diagnostic_mode():
    standard = client.get("/modules/01_Arquitectura_y_Conectividad")
    assert "Export runtime" not in standard.text

    diag = diag_client.get("/modules/01_Arquitectura_y_Conectividad")
    assert diag.status_code == 200
    assert "Export runtime" in diag.text
    assert "Case Library" in diag.text
    assert "Propuestas de árbol" in diag.text
    # El placeholder viejo ya no está.
    assert "se irán trayendo desde la versión original" not in diag.text


# ── Export runtime ────────────────────────────────────────────────────────

def test_runtime_export_returns_zip_with_manifest():
    response = diag_client.get("/admin/runtime/export.zip")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert "manifest.json" in archive.namelist()


def test_runtime_inventory_lists_the_three_folders():
    response = diag_client.get("/admin/runtime/inventory")
    assert response.status_code == 200
    assert "feedback_log" in response.text
    assert "activity_log" in response.text
    assert "case_library" in response.text


# ── Análisis / propuestas ─────────────────────────────────────────────────

def test_cases_analysis_renders_summary():
    response = diag_client.post("/admin/cases/analyze", data={})
    assert response.status_code == 200
    assert "admin-result-output" in response.text
    assert "CASE LIBRARY" in response.text


def test_feedback_analysis_renders_result():
    response = diag_client.post("/admin/feedback/analyze", data={"kind": "all"})
    assert response.status_code == 200
    assert "Feedback" in response.text


def test_tree_proposals_run_after_cases_analysis():
    # Las propuestas leen los reportes que escribe el análisis de casos.
    diag_client.post("/admin/cases/analyze", data={})
    response = diag_client.post("/admin/trees/propose", data={})
    assert response.status_code == 200
    assert "Propuestas de árbol" in response.text


def test_content_proposals_render_result():
    response = diag_client.post("/admin/content/propose", data={})
    assert response.status_code == 200
    assert "Propuestas de contenido" in response.text
