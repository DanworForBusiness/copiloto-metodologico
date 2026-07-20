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
auth_service.ALLOWED_USERS = "ALL"  # baseline: perfil full (ver test_routes)

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
    ("/admin/runtime/purge", {"mode": "preview"}),
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


def test_runtime_purge_preview_then_delete_respects_range(monkeypatch, tmp_path):
    from webapp.services import admin_tools as at
    fb = tmp_path / "fb"; fb.mkdir()
    in_range = fb / "feedback_20260101_120000_aa.yaml"; in_range.write_text("x")
    out_range = fb / "feedback_20260715_120000_bb.yaml"; out_range.write_text("x")
    cases = tmp_path / "cl"; cases.mkdir()
    case_in = cases / "case_20260102_000000_cc.yaml"; case_in.write_text("x")
    monkeypatch.setattr(at, "_RUNTIME_EXPORT_DIRS", (
        ("feedback_log", fb), ("activity_log", tmp_path / "al"), ("case_library", cases),
    ))
    data = {"folders": ["feedback_log", "case_library"],
            "date_from": "2026-01-01", "date_to": "2026-01-31"}

    # Previsualizar: reporta pero NO borra.
    preview = diag_client.post("/admin/runtime/purge", data={**data, "mode": "preview"})
    assert preview.status_code == 200
    assert "PREVISUALIZACI" in preview.text and "Nada fue borrado" in preview.text
    assert in_range.exists() and case_in.exists()

    # Eliminar: borra solo lo del rango en las carpetas elegidas.
    delete = diag_client.post("/admin/runtime/purge", data={**data, "mode": "delete"})
    assert delete.status_code == 200
    assert "BORRADO EJECUTADO" in delete.text
    assert not in_range.exists() and not case_in.exists()
    assert out_range.exists()  # fuera de rango, intacto

    # Sin carpetas elegidas: mensaje claro, sin acción.
    empty = diag_client.post("/admin/runtime/purge", data={"mode": "delete"})
    assert "al menos una carpeta" in empty.text


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


def test_feedback_analysis_recognizes_current_schema_incl_academy():
    # La analítica debe reconocer el schema actual: asistente, módulos, secciones
    # y Danwor Academy.
    import analyze_feedback
    entries = [
        {"type": "academy_content", "academy_page": "ACAD01_Paradigmas", "academy_title": "Intro",
         "rating": "up", "ts": "t1"},
        {"type": "academy_content", "academy_page": "ACAD01_Paradigmas", "academy_title": "Intro",
         "rating": "down", "comment": "faltó un ejemplo", "ts": "t2"},
        {"type": "assistant_response", "module": "05", "action": "chat", "topic": "x",
         "rating": "up", "ts": "t3"},
        {"type": "module_content", "module": "04_Modelado", "module_title": "Modelado",
         "rating": "up", "ts": "t4"},
        {"type": "section_content", "module": "01_Arquitectura", "module_title": "Arquitectura",
         "section_slug": "espacios-transportes", "section_title": "Espacios & Transportes",
         "rating": "down", "comment": "confuso el transporte transversal", "ts": "t5"},
    ]
    acad = analyze_feedback.analyze_academy(entries)
    assert acad["total"] == 2 and acad["total_up"] == 1 and acad["total_down"] == 1
    assert acad["by_page"][0]["page"] == "ACAD01_Paradigmas"
    assert acad["comments_down"][0]["comment"] == "faltó un ejemplo"
    assert analyze_feedback.analyze_assistant(entries)["total"] == 1
    assert analyze_feedback.analyze_modules(entries)["total"] == 1
    secs = analyze_feedback.analyze_sections(entries)
    assert secs["total"] == 1 and secs["total_down"] == 1
    assert secs["by_section"][0]["section_title"] == "Espacios & Transportes"
    assert secs["comments_down"][0]["comment"] == "confuso el transporte transversal"
    summary = analyze_feedback.build_summary(
        analyze_feedback.analyze_assistant(entries),
        analyze_feedback.analyze_modules(entries),
        secs,
        acad,
    )
    assert "DANWOR ACADEMY" in summary
    assert "SECCIONES DE MÓDULOS" in summary
    assert "CONTENIDO DE MÓDULOS" in summary
    assert "ASISTENTE IA" in summary


def test_content_proposals_render_result():
    response = diag_client.post("/admin/content/propose", data={})
    assert response.status_code == 200
    assert "Propuestas de contenido" in response.text


# ── SharePoint (Fase 2) ───────────────────────────────────────────────────

def test_sharepoint_requires_diagnostic():
    assert client.post("/admin/sharepoint/seed", data={}).status_code == 403
    assert client.post("/admin/sharepoint/refresh-cache", data={}).status_code == 403
    assert client.get("/admin/sharepoint/seed.json").status_code == 403


def test_sharepoint_seed_without_token_explains():
    response = diag_client.post("/admin/sharepoint/seed", data={"graph_token": ""})
    assert response.status_code == 200
    assert "token de Microsoft Graph" in response.text


def test_sharepoint_seed_happy_path_and_download(monkeypatch):
    from core import sharepoint_cases
    fake = [{
        "_search_text": "Error TIMEOUT RFC_READ_TABLE SAP_BASIS cliente ACME",
        "history": "Historia de prueba.", "case_number": "00123", "client": "ACME",
        "title": "Timeout RFC", "tags": "rfc,timeout", "web_url": "", "created": "", "modified": "",
    }]
    monkeypatch.setattr(sharepoint_cases, "fetch_sap_cases", lambda token, **kw: fake)

    response = diag_client.post("/admin/sharepoint/seed", data={"graph_token": "FAKE"})
    assert response.status_code == 200
    assert "Seed generado" in response.text
    assert "/admin/sharepoint/seed.json" in response.text

    download = diag_client.get("/admin/sharepoint/seed.json")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/json")
    assert "00123" in download.text
