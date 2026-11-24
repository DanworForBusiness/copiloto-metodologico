import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp import access
from webapp.services import auth_service

# Las herramientas de gestión solo las habilita el dueño (APP_OWNER) en modo
# diagnóstico. En tests, el usuario de prueba ES el owner. El seam vive ahora en
# webapp.access (access policy), no en webapp.main.
access.APP_OWNER = "test@danwor.com"
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


# Caso mínimo REAL para analyze_cases: lo único que exige load_cases() es un dict
# con `case_id`; el resto de los campos son los que consumen los analizadores
# (source.tree_id, route[node/answer], leaf_id, outcome, lessons). Con esto la
# rama de análisis normal corre de forma determinista, sin depender del
# case_library runtime del desarrollador.
_MINIMAL_CASE_YAML = """\
case_id: test-0001
uncharted: false
source:
  tree_id: 01_espacios_topologia
route:
  - node: n1
    answer: "Sí"
leaf_id: leaf_demo
outcome: anduvo
context_description: Caso de prueba para análisis determinista.
lessons:
  - Lección de prueba.
"""


@pytest.fixture(autouse=True)
def _isolate_report_dirs(monkeypatch, tmp_path):
    """Aísla las fuentes y salidas de los scripts de análisis del runtime real.

    - SALIDAS: reports/ y trees/proposed/ se redirigen a tmp para no ensuciar el
      repo al correr la suite.
    - FUENTE case_library/: se redirige a un tmp con un caso mínimo real. Antes
      se dejaba la fuente runtime real, lo que volvía NO determinista a
      test_cases_analysis_renders_summary: en local (con datos) tomaba la rama de
      análisis y en CI (checkout limpio, case_library gitignored) tomaba la rama
      "No hay casos para analizar". Ahora el input es controlado e idéntico en
      ambos entornos. (feedback_log/ queda real: su test solo verifica el título
      del panel, que no depende de que haya datos.)
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

    # Fuente de casos controlada (ver docstring). analyze_cases resuelve CASE_DIR
    # como default del argparse en runtime, así que el setattr al módulo alcanza.
    case_dir = tmp_path / "case_library"
    case_dir.mkdir()
    (case_dir / "case_test_0001.yaml").write_text(_MINIMAL_CASE_YAML, encoding="utf-8")
    monkeypatch.setattr(analyze_cases, "CASE_DIR", case_dir, raising=False)


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
    # Con case_library controlado (fixture) analyze_cases corre la rama normal y
    # emite el encabezado del reporte: "REPORTE DE CASE LIBRARY — ...".
    response = diag_client.post("/admin/cases/analyze", data={})
    assert response.status_code == 200
    assert "admin-result-output" in response.text
    assert "CASE LIBRARY" in response.text


def test_cases_analysis_empty_state_when_no_cases(monkeypatch, tmp_path):
    """Rama explícita SIN casos: se controla la fuente (CASE_DIR vacío) para no
    depender del entorno. analyze_cases sale temprano con un mensaje claro y el
    endpoint responde 200 (SystemExit lo captura admin_tools._capture_main)."""
    import analyze_cases

    empty_dir = tmp_path / "empty_case_library"
    empty_dir.mkdir()
    monkeypatch.setattr(analyze_cases, "CASE_DIR", empty_dir, raising=False)

    response = diag_client.post("/admin/cases/analyze", data={})
    assert response.status_code == 200
    assert "No hay casos para analizar" in response.text


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


# ── Borrado selectivo: filtro opcional por usuario ──────────────────────────
def _seed_user_runtime(monkeypatch, tmp_path):
    """feedback_log (1 YAML/usuario por archivo), activity_log (JSONL por día, user por línea), case_library (sin user)."""
    from webapp.services import admin_tools as at
    fb = tmp_path / "fb"; fb.mkdir()
    (fb / "feedback_20260110_120000_a1.yaml").write_text("user: ana@danwor.com\nrating: 5\n", encoding="utf-8")
    (fb / "feedback_20260110_130000_b1.yaml").write_text("user: beto@danwor.com\nrating: 4\n", encoding="utf-8")
    al = tmp_path / "al"; al.mkdir()
    (al / "activity_20260110.jsonl").write_text(
        '{"ts_utc":"2026-01-10T10:00:00","event":"page_view","user":"ana@danwor.com"}\n'
        '{"ts_utc":"2026-01-10T10:05:00","event":"page_view","user":"beto@danwor.com"}\n'
        '{"ts_utc":"2026-01-10T10:06:00","event":"login","user":"ana@danwor.com"}\n', encoding="utf-8")
    cl = tmp_path / "cl"; cl.mkdir()
    (cl / "case_20260110_000000_cc.yaml").write_text("case_id: case_20260110_000000_cc\n", encoding="utf-8")
    monkeypatch.setattr(at, "_RUNTIME_EXPORT_DIRS", (("feedback_log", fb), ("activity_log", al), ("case_library", cl)))
    monkeypatch.setattr(at, "FEEDBACK_LOG_DIR", fb)
    monkeypatch.setattr(at, "ACTIVITY_LOG_DIR", al)
    monkeypatch.setattr(at, "CASE_LIBRARY_DIR", cl)
    return fb, al, cl


def test_runtime_users_lists_distinct_emails(monkeypatch, tmp_path):
    from webapp.services import admin_tools as at
    _seed_user_runtime(monkeypatch, tmp_path)
    assert at.runtime_users() == ["ana@danwor.com", "beto@danwor.com"]


def test_runtime_purge_user_filter_feedback_file_activity_line_case_zero(monkeypatch, tmp_path):
    fb, al, cl = _seed_user_runtime(monkeypatch, tmp_path)
    data = {"folders": ["feedback_log", "activity_log", "case_library"], "date_from": "2026-01-01",
            "date_to": "2026-01-31", "user": "ana@danwor.com"}
    # Preview: refleja el filtro; feedback 1 archivo de ana, activity 2 eventos de ana, case 0; nada borrado.
    preview = diag_client.post("/admin/runtime/purge", data={**data, "mode": "preview"}).text
    assert "usuario: ana@danwor.com" in preview and "Nada fue borrado" in preview
    assert "feedback_log: 1 archivo(s) de ana@danwor.com" in preview
    assert "activity_log: 2 evento(s) de ana@danwor.com" in preview
    assert "case_library: 0 (sin usuario asociado" in preview
    assert (fb / "feedback_20260110_120000_a1.yaml").exists()                      # preview no borra
    # Delete: borra solo lo de ana; beto intacto en feedback y en activity.
    out = diag_client.post("/admin/runtime/purge", data={**data, "mode": "delete"}).text
    assert "BORRADO EJECUTADO" in out
    assert not (fb / "feedback_20260110_120000_a1.yaml").exists()                  # feedback de ana borrado
    assert (fb / "feedback_20260110_130000_b1.yaml").exists()                      # feedback de beto intacto
    remaining = (al / "activity_20260110.jsonl").read_text(encoding="utf-8")
    assert "ana@danwor.com" not in remaining and "beto@danwor.com" in remaining    # solo las líneas de ana se fueron
    assert (cl / "case_20260110_000000_cc.yaml").exists()                          # case_library intacto (sin usuario)


def test_runtime_purge_user_filter_no_results_is_zero(monkeypatch, tmp_path):
    fb, al, cl = _seed_user_runtime(monkeypatch, tmp_path)
    data = {"folders": ["feedback_log", "activity_log"], "date_from": "2026-01-01", "date_to": "2026-01-31",
            "user": "nadie@danwor.com", "mode": "delete"}
    out = diag_client.post("/admin/runtime/purge", data=data).text
    assert "feedback_log: 0 archivo(s) de nadie@danwor.com" in out
    assert "activity_log: 0 evento(s) de nadie@danwor.com" in out
    assert (al / "activity_20260110.jsonl").read_text(encoding="utf-8").count("\n") == 3   # nada se tocó


def test_runtime_purge_all_users_unchanged_behaviour(monkeypatch, tmp_path):
    fb, al, cl = _seed_user_runtime(monkeypatch, tmp_path)
    # Sin usuario («Todos»): conducta histórica por archivo (incluye el día completo de activity_log).
    out = diag_client.post("/admin/runtime/purge", data={"folders": ["activity_log"], "date_from": "2026-01-01",
                                                         "date_to": "2026-01-31", "mode": "preview"}).text
    assert "activity_log: 1 archivo(s) ·" in out and "usuario:" not in out


def test_runtime_purge_activity_keeps_other_users_and_unidentified_lines(monkeypatch, tmp_path):
    from webapp.services import admin_tools as at
    al = tmp_path / "al"; al.mkdir()
    day = al / "activity_20260110.jsonl"
    day.write_text(
        '{"event":"page_view","user":"ana@danwor.com"}\n'
        '{"event":"page_view","user":"beto@danwor.com"}\n'
        '{"event":"login","user":"ana@danwor.com"}\n'
        '{"event":"system_tick"}\n'                       # sin identidad reconocible: se conserva
        'esto-no-es-json\n', encoding="utf-8")            # línea corrupta: se conserva (nunca se asume del usuario)
    monkeypatch.setattr(at, "_RUNTIME_EXPORT_DIRS", (("feedback_log", tmp_path / "fb"), ("activity_log", al), ("case_library", tmp_path / "cl")))
    out = diag_client.post("/admin/runtime/purge", data={"folders": ["activity_log"], "date_from": "2026-01-01",
          "date_to": "2026-01-31", "user": "ana@danwor.com", "mode": "delete"}).text
    assert "activity_log: 2 evento(s) de ana@danwor.com" in out
    remaining = day.read_text(encoding="utf-8")
    assert "ana@danwor.com" not in remaining                 # los 2 eventos de ana se fueron
    assert '"user":"beto@danwor.com"' in remaining           # otro usuario intacto
    assert '"event":"system_tick"' in remaining               # línea sin usuario conservada
    assert "esto-no-es-json" in remaining                     # línea corrupta conservada
    assert not day.read_text(encoding="utf-8").strip() == ""  # el archivo no quedó vacío (no se borró)


def test_runtime_purge_case_library_user_abstains_without_global_fallback(monkeypatch, tmp_path):
    from webapp.services import admin_tools as at
    cl = tmp_path / "cl"; cl.mkdir()
    case = cl / "case_20260110_000000_x.yaml"; case.write_text("case_id: x\n", encoding="utf-8")
    monkeypatch.setattr(at, "_RUNTIME_EXPORT_DIRS", (("feedback_log", tmp_path / "fb"), ("activity_log", tmp_path / "al"), ("case_library", cl)))
    # Con usuario + SOLO case_library + Eliminar: se abstiene (0), NO vuelve al borrado global.
    out = diag_client.post("/admin/runtime/purge", data={"folders": ["case_library"], "date_from": "2026-01-01",
          "date_to": "2026-01-31", "user": "ana@danwor.com", "mode": "delete"}).text
    assert "case_library: 0 (sin usuario asociado" in out
    assert case.exists()                                     # el caso NO se borró (abstención, sin fallback global)
