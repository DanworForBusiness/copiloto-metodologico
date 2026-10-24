"""Importación histórica de CVs — I3.2: interfaz (rutas solo-owner) de la ejecución y de las excepciones. Reutiliza el SharePoint simulado con estado del archivo de la ejecución."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from tests.webapp.test_cv_history_import_exec import PII, TOKEN, banco_profiles, by_name, env, ex, mf, pilot, planned_run, planning, profile_document_repository, profile_repository, OWNER  # noqa: F401  (env es un fixture)
from tests.webapp.test_cv_history_pilot import BASE, HC_USER, client_for, home, sp  # noqa: F401  (home y sp son fixtures)
from webapp.main import app

squash = lambda html: html.replace("\r", "").replace("\n", "")      # noqa: E731


def scope_fields(rid):
    s = ex.plan_scope(mf.get_plan(rid))
    return {"plan_revision": str(s["revision"]), "expect_profiles": str(s["profiles"]), "expect_documents": str(s["documents"])}


def post_run(c, rid, **over):
    return c.post(f"{BASE}/{rid}/importar", data={"graph_token": TOKEN, "confirm": ex.confirm_phrase(rid), "batch": "20", **scope_fields(rid), **over})


def act_row(c, rid, key, accion, **data):
    return c.post(f"{BASE}/{rid}/excepciones/{key}/{accion}", data={"graph_token": TOKEN, **data})


def test_import_routes_are_owner_only(env, monkeypatch):
    rid = planned_run(env)
    key = by_name(rid)["escaneado.pdf"]
    calls = (("post", f"{BASE}/{rid}/importar", {"data": {"graph_token": TOKEN, "confirm": ex.confirm_phrase(rid)}}), ("get", f"{BASE}/{rid}/excepciones", {}), ("post", f"{BASE}/{rid}/excepciones/{key}/descartar", {"data": {"reason": "NOT_A_CV"}}))
    for method, url, kw in calls:
        assert getattr(TestClient(app, follow_redirects=False), method)(url, **kw).status_code in (302, 303, 307), url
        assert getattr(client_for(HC_USER, monkeypatch), method)(url, **kw).status_code == 403, url
    assert "execution" not in mf.get_run(rid) and mf.get_ledger(rid, key) is None


def test_the_panel_asks_for_the_explicit_confirmation_before_any_write_and_uses_the_write_token_hook(env, monkeypatch):
    store, world = env
    rid = planned_run(env)
    page = client_for(OWNER, monkeypatch).get(f"{BASE}/{rid}").text
    assert "data-import-start-form" in page and "data-import-warning" in page and ex.confirm_phrase(rid) in page and "Iniciar importación (plan revisión 1: 2 perfiles nuevos · 4 documentos)" in page and "data-hc-authorize-write" in page
    flat = squash(page)
    assert "data-scope-revision>1<" in flat and "data-scope-profiles>2<" in flat and "data-scope-documents>4<" in flat and "Pendientes que NO se importan ahora" in page          # los conteos efectivos del plan aprobado
    assert 'name="plan_revision" value="1"' in page and 'name="expect_profiles" value="2"' in page and 'name="expect_documents" value="4"' in page
    forms = re.findall(r"<form[^>]*data-import-start-form[^>]*>", page)
    assert forms and all("data-hc-write-token" in f and "data-dw-graph-token" not in f for f in forms)                    # un solo hook por formulario: el de ESCRITURA
    assert "human_capital_import.js" in page and "graph_token:" not in page and TOKEN not in page
    assert 'name="confirm"' in page and store.puts == [] and "execution" not in mf.get_run(rid)


def test_start_and_run_through_the_route_with_batches_and_auto_continue(env, monkeypatch):
    store, world = env
    rid = planned_run(env)
    owner = client_for(OWNER, monkeypatch)
    assert 'data-error-kind="missing_token"' in post_run(owner, rid, graph_token="").text and store.puts == []
    wrong = post_run(owner, rid, confirm="IMPORTAR")
    assert 'data-error-kind="invalid_input"' in wrong.text and "confirmación explícita" in wrong.text and store.puts == [] and "execution" not in mf.get_run(rid)
    step = post_run(owner, rid, batch="1")
    assert "data-import-result" in step.text and "Procesados 1 ·" in step.text and "data-import-autocontinue" in step.text and 'hx-trigger="load delay:700ms, submit"' in step.text and "data-import-start-form" not in step.text
    assert "data-import-state>RUNNING<" in squash(step.text) and "data-import-progress" in step.text and "Perfiles creados" in step.text
    cont = re.findall(r"<form[^>]*data-import-continue-form[^>]*>", step.text)
    assert cont and "data-hc-write-token" in cont[0] and "data-dw-graph-token" not in cont[0]
    guard = 0
    while "data-import-autocontinue" in step.text and guard < 10:                                                       # el navegador encadena las tandas con el disparador `load`
        step = owner.post(f"{BASE}/{rid}/importar", data={"graph_token": TOKEN, "batch": "1"})
        guard += 1
    html = squash(step.text)
    assert "data-import-state>DONE<" in html and "data-import-continue-form" not in step.text and "data-import-autocontinue" not in step.text
    assert "data-import-documents>4<" in html and "data-import-profiles>2<" in html and "data-import-exceptions-link" in step.text
    assert "Hist%C3%B3rico%20SharePoint" in step.text and "Revisar%20identidad" in step.text
    assert not [p for p in PII if p in step.text] and TOKEN not in step.text and len(store.real_files()) == 4


def test_a_denied_write_permission_never_starts_and_a_midrun_denial_explains_how_to_resume(env, monkeypatch):
    store, world = env
    rid = planned_run(env)
    owner = client_for(OWNER, monkeypatch)
    store.deny_probe = True
    denied = post_run(owner, rid)
    assert 'data-error-kind="invalid_input"' in denied.text and "Sin permiso de escritura" in denied.text and "execution" not in mf.get_run(rid) and store.real_files() == {}
    store.deny_probe = False
    store.deny_put = True
    mid = post_run(owner, rid)
    assert "PERMISSION_DENIED" in mid.text and "autorizá la escritura" in mid.text and "data-import-autocontinue" not in mid.text and "Reanudar" in mid.text
    store.deny_put = False
    done = post_run(owner, rid, confirm="")
    assert "data-import-state>DONE<" in squash(done.text) and len(store.real_files()) == 4


def test_the_discard_waits_for_the_execution_and_the_plan_cannot_be_regenerated(env, monkeypatch):
    rid = planned_run(env)
    owner = client_for(OWNER, monkeypatch)
    post_run(owner, rid, batch="1")
    blocked = owner.post(f"{BASE}/{rid}/descartar", data={"confirm": pilot.discard_phrase(rid)})
    assert 'data-error-kind="invalid_confirmation"' in blocked.text and "sin terminar" in blocked.text and mf.staging_files_present(rid) > 0
    refused = owner.post(f"{BASE}/{rid}/plan")
    assert 'data-error-kind="invalid_input"' in refused.text and "congelado" in refused.text


def test_the_exceptions_page_lists_filters_and_paginates_with_personal_data_only_for_the_owner(env, monkeypatch):
    rid = planned_run(env)
    owner = client_for(OWNER, monkeypatch)
    post_run(owner, rid, batch="100")
    page = owner.get(f"{BASE}/{rid}/excepciones")
    html = page.text
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store" and "datos personales" in html and html.count("data-exception-row") == 2
    assert "pablo.ruiz@a.com" in html and "MULTIPLE_EMAILS" in html and "READ_FAILURE" in html and "data-exceptions-summary" in html and "human_capital_import.js" in html
    assert "Pablo Ruiz" in html and "data-exc-complete" in html and "data-exc-link" in html and "data-exc-dismiss" in html
    assert all("data-dw-graph-token" not in f for f in re.findall(r"<form[^>]*>", html))
    only = owner.get(f"{BASE}/{rid}/excepciones?motivo=MULTIPLE_EMAILS").text
    assert only.count("data-exception-row") == 1 and "READ_FAILURE (1)</option>" in only
    resolved = owner.get(f"{BASE}/{rid}/excepciones?estado=resolved").text
    assert resolved.count("data-exception-row") == 0 and "data-exceptions-empty" in resolved
    ex.EXCEPTIONS_PAGE_SIZE = 1
    try:
        assert "página 1 de 2" in owner.get(f"{BASE}/{rid}/excepciones").text and "Siguiente" in owner.get(f"{BASE}/{rid}/excepciones").text
        assert owner.get(f"{BASE}/{rid}/excepciones?pagina=2").text.count("data-exception-row") == 1
    finally:
        ex.EXCEPTIONS_PAGE_SIZE = 25
    assert owner.get(f"{BASE}/ch-20200101T000000Z/excepciones").status_code == 404


def test_exception_actions_through_the_routes(env, monkeypatch):
    rid = planned_run(env)
    owner = client_for(OWNER, monkeypatch)
    post_run(owner, rid, batch="100")
    keys = by_name(rid)
    pablo = next(k for k, d in mf.get_plan(rid)["decisions"].items() if d["reasons"][:1] == [planning.MULTIPLE_EMAILS])
    before = set(banco_profiles())
    assert 'data-error-kind="exception_action"' in act_row(owner, rid, pablo, "completar", graph_token="", first_name="Pablo", last_name="Ruiz").text
    invalid = act_row(owner, rid, pablo, "completar", first_name="Pablo", last_name="")
    assert 'data-error-kind="exception_action"' in invalid.text and set(banco_profiles()) == before                      # nunca un perfil sin apellido
    advisory = act_row(owner, rid, pablo, "completar", first_name="Pablo", last_name="Ruiz")
    assert "data-duplicate-advisory" in advisory.text and "Vincular a ese perfil" in advisory.text and "Crear perfil nuevo igual" in advisory.text and set(banco_profiles()) == before
    created = act_row(owner, rid, pablo, "completar", first_name="Pablo", last_name="Ruiz", email="pablo.ruiz@a.com", duplicate_confirmed="true")
    assert 'data-status="resolved"' in created.text and "data-exception-done" in created.text and "Ver perfil" in created.text and len(banco_profiles()) == len(before) + 1
    scanned = keys["escaneado.pdf"]
    assert 'data-status="dismissed"' in act_row(owner, rid, scanned, "descartar", reason="NOT_A_CV").text
    reopened = act_row(owner, rid, scanned, "reabrir")
    assert 'data-status="open"' in reopened.text and "data-exc-complete" in reopened.text
    zoe = next(p for p in profile_repository.list() if p["first_name"] == "Zoe")
    assert 'data-error-kind="exception_action"' in act_row(owner, rid, scanned, "vincular", profile_id="x").text
    linked = act_row(owner, rid, scanned, "vincular", profile_id=zoe["profile_id"])
    assert 'data-status="resolved"' in linked.text and [d["filename"] for d in profile_document_repository.list_for_profile(zoe["profile_id"])] == ["escaneado.pdf"]
    for bad in (f"{BASE}/{rid}/excepciones/{scanned}/borrar", f"{BASE}/{rid}/excepciones/no-es-clave/descartar", f"{BASE}/ch-20200101T000000Z/excepciones/{scanned}/descartar"):
        assert owner.post(bad, data={"graph_token": TOKEN}).status_code == 404
    assert not [p for p in PII if p in post_run(owner, rid, confirm="").text]                                              # el panel (conteos) no muestra datos personales


def test_the_static_script_only_defers_the_request_for_the_authority_and_never_owns_a_credential():
    text = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "human_capital_import.js").read_text(encoding="utf-8")
    assert "dwHumanCapitalDocumentWriteToken" in text and "data-hc-write-token" in text and "interactive: !!interactive" in text
    for banned in ("acquireToken", "localStorage", "sessionStorage", "refresh_token", "Authorization"):
        assert banned not in text, banned

def test_a_stale_confirmation_form_cannot_start_the_import_after_the_plan_changed(env, monkeypatch):
    store, world = env
    rid = planned_run(env)
    owner = client_for(OWNER, monkeypatch)
    stale = scope_fields(rid)
    planning.generate_plan(rid, actor=OWNER)                                                                               # el plan cambia de revisión con el formulario ya abierto
    refused = owner.post(f"{BASE}/{rid}/importar", data={"graph_token": TOKEN, "confirm": ex.confirm_phrase(rid), **stale})
    assert 'data-error-kind="invalid_input"' in refused.text and "El plan cambió desde que lo revisaste" in refused.text and "execution" not in mf.get_run(rid) and store.puts == []
    assert "data-scope-revision>2<" in squash(refused.text)                                                                # la pantalla ya muestra la revisión vigente
    missing = owner.post(f"{BASE}/{rid}/importar", data={"graph_token": TOKEN, "confirm": ex.confirm_phrase(rid)})
    assert "Falta el alcance" in missing.text and store.puts == []
    started = post_run(owner, rid)                                                                                          # con el alcance vigente sí inicia
    assert "data-import-state>DONE<" in squash(started.text) and mf.get_run(rid)["execution"]["plan_revision"] == 2


def test_a_residual_probe_file_is_shown_in_the_panel_and_blocks_the_start(env, monkeypatch):
    store, world = env
    rid = planned_run(env)
    owner = client_for(OWNER, monkeypatch)
    store.fail_delete = True
    blocked = post_run(owner, rid)
    residue = mf.get_run(rid)["probe_residue"][0]
    assert 'data-error-kind="invalid_input"' in blocked.text and residue in blocked.text and "no se inició la importación" in blocked.text and "execution" not in mf.get_run(rid)
    panel = owner.get(f"{BASE}/{rid}").text
    assert "data-probe-residue" in panel and residue in panel and "data-import-start-form" in panel


def test_the_panel_reports_what_blocks_the_discard_and_offers_no_discard_form_until_it_is_resolved(env, monkeypatch):
    store, world = env
    rid = planned_run(env)
    owner = client_for(OWNER, monkeypatch)
    post_run(owner, rid, batch="100")
    panel = owner.get(f"{BASE}/{rid}").text
    assert "data-discard-blocked" in panel and "<strong>0</strong> contenidos sin terminar" in panel and "<strong>2</strong> excepciones abiertas" in panel and "data-pilot-discard-form" not in panel
    assert f"/cchh/diagnostico/cvs-historicos/{rid}/excepciones" in panel
    refused = owner.post(f"{BASE}/{rid}/descartar", data={"confirm": pilot.discard_phrase(rid)})
    assert 'data-error-kind="invalid_confirmation"' in refused.text and "2 excepciones abiertas" in refused.text and mf.staging_files_present(rid) > 0
    for key, d in mf.get_plan(rid)["decisions"].items():                                                                   # se resuelven desde la interfaz
        if d["action"] == planning.PENDING:
            assert 'data-status="dismissed"' in act_row(owner, rid, key, "descartar", reason="NOT_A_CV").text
    free = owner.get(f"{BASE}/{rid}").text
    assert "data-discard-blocked" not in free and "data-pilot-discard-form" in free                                          # el descarte sigue siendo explícito: formulario con frase
    wrong = owner.post(f"{BASE}/{rid}/descartar", data={"confirm": "ELIMINAR"})
    assert 'data-error-kind="invalid_confirmation"' in wrong.text and mf.staging_files_present(rid) > 0
    done = owner.post(f"{BASE}/{rid}/descartar", data={"confirm": pilot.discard_phrase(rid)})
    assert "data-pilot-discarded" in done.text and mf.staging_files_present(rid) == 0 and ex.execution_report(rid)["documents_uploaded"] == 4
