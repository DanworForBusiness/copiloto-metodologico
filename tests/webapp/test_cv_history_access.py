"""«Históricos sin validación» — ACCESO. La pantalla (consulta, CV original, borradores, aceptación, completar, vincular, descartar, reabrir, reintentar) la opera cualquier usuario AUTORIZADO DE CCHH (`human_capital_access`:
APP_OWNER o `HUMAN_CAPITAL_ALLOWED_USERS`): el esquema de siempre, sin otro mecanismo. Premisa verificada en el código: ese permiso YA habilita crear, editar y borrar perfiles y subir documentos con el token de escritura, así que estas
acciones no amplían privilegios de escritura. Lo sensible de la IMPORTACIÓN (iniciar, planificar, ejecutar, eliminar el área temporal, diagnósticos de SharePoint) sigue siendo solo del owner. Mundo sintético con SharePoint y Graph simulados."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.webapp.test_cv_history_acceptance import PABLO, digest, draft, ready  # noqa: F401
from tests.webapp.test_cv_history_acceptance_ui import acks_of, confirm_create, form_of, review  # noqa: F401
from tests.webapp.test_cv_history_import_exec import OWNER, TOKEN, banco_profiles, env, ex, mf, planning, profile_document_repository, profile_repository  # noqa: F401
from tests.webapp.test_cv_history_pilot import BASE, HC_USER, client_for, home, sp  # noqa: F401
from webapp.main import app
from webapp.services import cv_history_drafts as drafts, cv_history_pilot as pilot

OUTSIDER = "fuera-de-cchh@danwor.com"                                                                                                  # autenticado y admitido en la app, pero SIN autorización de Capital Humano
ROUTER = (Path(__file__).resolve().parents[2] / "webapp" / "routers" / "human_capital.py").read_text(encoding="utf-8")


def screen_calls(rid, key, scanned):
    """Todas las rutas de la pantalla: (método, URL, datos)."""
    token = {"graph_token": TOKEN}
    action = lambda name, **data: ("post", f"{BASE}/{rid}/excepciones/{key}/{name}", {**token, **data})        # noqa: E731
    return [("get", f"{BASE}/{rid}/excepciones", {}), ("post", f"{BASE}/{rid}/cv-fuente/{key}", token),
            action("borrador", first_name="Pablo", last_name="Ruiz", revision="0"), action("aceptar-revisar"),
            action("aceptar", op="create", revision="1", fingerprint="x", ack=["irreversible"]),
            action("completar", first_name="Pablo", last_name="Ruiz", duplicate_confirmed="true"), action("vincular", profile_id="x"),
            ("post", f"{BASE}/{rid}/excepciones/{scanned}/descartar", {"reason": "NOT_A_CV"}), ("post", f"{BASE}/{rid}/excepciones/{scanned}/reabrir", {}), ("post", f"{BASE}/{rid}/excepciones/{scanned}/reintentar", {})]


def call(client, method, url, data):
    return client.get(url) if method == "get" else client.post(url, data=data)


def test_every_route_of_the_screen_redirects_anonymous_users_and_blocks_users_without_cchh_without_any_side_effect(env, monkeypatch):
    store, rid, pablo = ready(env)
    scanned = next(g["content_key"] for g in mf.list_groups(rid) if g["representative"]["name"] == "escaneado.pdf")
    before, puts, profiles = digest(), list(store.puts), set(banco_profiles())
    for method, url, data in screen_calls(rid, pablo, scanned):
        assert call(TestClient(app, follow_redirects=False), method, url, data).status_code in (302, 303, 307), url                    # sin sesión: al login
        assert call(client_for(OUTSIDER, monkeypatch), method, url, data).status_code == 403, url                                      # sin autorización de CCHH: bloqueado
    assert digest() == before and store.puts == puts and set(banco_profiles()) == profiles and mf.get_draft(rid, pablo) is None      # ni un byte escrito


def test_the_authorized_cchh_user_and_the_owner_reach_every_route_of_the_screen(env, monkeypatch):
    store, rid, pablo = ready(env)
    scanned = next(g["content_key"] for g in mf.list_groups(rid) if g["representative"]["name"] == "escaneado.pdf")
    for email in (HC_USER, OWNER):
        client = client_for(email, monkeypatch)
        for method, url, data in screen_calls(rid, pablo, scanned):
            assert call(client, method, url, data).status_code == 200, (email, url)


def test_a_cchh_user_runs_the_whole_flow_and_every_step_is_traced_to_them(env, monkeypatch):
    store, rid, pablo = ready(env)
    hc = client_for(HC_USER, monkeypatch)
    assert 'data-exception-row' in hc.get(f"{BASE}/{rid}/excepciones").text
    saved = hc.post(f"{BASE}/{rid}/excepciones/{pablo}/borrador", data={"graph_token": TOKEN, "revision": "0", "first_name": "Pablo", "last_name": "Ruiz", "email": "pablo.ruiz@a.com", "phone": "", "notes": "nota"})
    assert "Borrador guardado (revisión 1)" in saved.text
    doc = mf.get_draft(rid, pablo)
    assert (doc["created_by"], doc["updated_by"], doc["history"][0]["by"]) == (HC_USER, HC_USER, HC_USER)
    panel = review(hc, rid, pablo).text
    done = confirm_create(hc, rid, pablo, panel)
    assert 'data-accepted-state="done"' in done.text and "data-accept-done" in done.text
    ledger = mf.get_ledger(rid, pablo)
    assert ledger["accepted"]["by"] == HC_USER and ledger["resolved_by"] == HC_USER and ledger["state"] == ex.DONE
    profile = profile_repository.get(ledger["profile_id"])
    assert profile["import_origin"]["completed_by"] == HC_USER and profile["tags"] == "Histórico SharePoint"
    assert any(e["event"] == "IMPORT_DONE" and e.get("draft_revision") == 1 for e in mf.get_audit(rid, pablo)["events"])                  # la auditoría del lote queda
    scanned = next(g["content_key"] for g in mf.list_groups(rid) if g["representative"]["name"] == "escaneado.pdf")
    assert 'data-status="dismissed"' in hc.post(f"{BASE}/{rid}/excepciones/{scanned}/descartar", data={"graph_token": TOKEN, "reason": "NOT_A_CV"}).text
    assert mf.get_ledger(rid, scanned)["resolved_by"] == HC_USER
    assert 'data-status="open"' in hc.post(f"{BASE}/{rid}/excepciones/{scanned}/reabrir", data={"graph_token": TOKEN}).text


def test_the_write_token_is_still_required_for_a_cchh_user(env, monkeypatch):
    store, rid, pablo = ready(env)
    hc = client_for(HC_USER, monkeypatch)
    draft(rid, pablo)
    panel = review(hc, rid, pablo).text
    before, puts = digest(), list(store.puts)
    for accion, data in (("borrador", {"first_name": "Pablo", "last_name": "Ruiz", "revision": "1"}), ("completar", {"first_name": "Pablo", "last_name": "Ruiz", "duplicate_confirmed": "true"}), ("vincular", {"profile_id": "x"})):
        res = hc.post(f"{BASE}/{rid}/excepciones/{pablo}/{accion}", data={"graph_token": "", **data})
        assert "Sin token con permiso de escritura" in res.text, accion
    assert "Sin token con permiso de escritura" in confirm_create(hc, rid, pablo, panel, graph_token="").text
    assert digest() == before and store.puts == puts and mf.get_ledger(rid, pablo) is None                                           # sin token no se escribe nada, tampoco para CCHH


def test_the_import_and_staging_controls_and_the_sharepoint_diagnostics_stay_owner_only(env, monkeypatch):
    store, rid, pablo = ready(env)
    before, puts = digest(), list(store.puts)
    owner_only = [("get", f"{BASE}", {}), ("get", f"{BASE}/{rid}", {}), ("post", f"{BASE}/{rid}/plan", {}), ("post", f"{BASE}/{rid}/importar", {"graph_token": TOKEN, "confirm": ex.confirm_phrase(rid)}),
                  ("post", f"{BASE}/{rid}/procesar", {"graph_token": TOKEN}), ("post", f"{BASE}/{rid}/descartar", {"confirm": pilot.discard_phrase(rid)}), ("post", f"{BASE}/{rid}/reevaluar", {}),
                  ("post", f"{BASE}/{rid}/veredicto", {"content_key": pablo, "status": "OK"}), ("post", f"{BASE}/importacion", {"graph_token": TOKEN, "share_url": "https://x.sharepoint.com/y"}),
                  ("post", f"{BASE}/piloto", {"graph_token": TOKEN}), ("get", "/cchh/diagnostico/sharepoint", {}), ("post", "/cchh/diagnostico/sharepoint/sonda", {"graph_token": TOKEN}),
                  ("post", "/cchh/diagnostico/sharepoint/inventario", {"graph_token": TOKEN}), ("post", "/cchh/diagnostico/sharepoint/escritura", {"graph_token": TOKEN})]
    for email in (HC_USER, OUTSIDER):
        client = client_for(email, monkeypatch)
        for method, url, data in owner_only:
            assert call(client, method, url, data).status_code == 403, (email, url)                                                  # ni siquiera un usuario autorizado de CCHH
    assert digest() == before and store.puts == puts and mf.get_run(rid)["staging"]["state"] == "PRESENT"


def test_the_premise_that_cchh_users_already_create_profiles_and_upload_documents_is_in_the_code():
    """Si alguna vez esas capacidades pasaran a ser solo del owner, este test avisa que la pantalla de históricos ampliaría privilegios."""
    for route in ('@router.post("/cchh/candidatos/nuevo"', '@router.post("/cchh/candidatos/{profile_id}/documentos"', '@router.post("/cchh/candidatos/{profile_id}/eliminar"'):
        start = ROUTER.index(route)
        body = ROUTER[start: ROUTER.find("\n@router.", start + 10)]
        # No son solo del owner: ni el guard de diagnóstico ni una condición `not …is_owner…` que restrinja la operación. Un uso de `is_owner` solo para ARMAR el contexto de un panel (p. ej. `owner=app_access.is_owner(request)`
        # que habilita el control de consolidación) no restringe nada; la autorización efectiva es la sesión de Capital Humano de la propia ruta.
        assert "_require_owner_for_diagnostics" not in body and not re.search(r"not\s+(?:\w+\.)?is_owner\w*\(", body), route
    assert "def _require_human_capital_session" in ROUTER and "human_capital_access.is_human_capital_authorized(request)" in ROUTER


def test_the_historical_screen_uses_the_existing_cchh_scheme_and_no_parallel_mechanism():
    for fn in ("human_capital_cv_history_exceptions", "human_capital_cv_history_exception_action", "human_capital_cv_history_original_cv"):
        start = ROUTER.index(f"def {fn}(")
        body = ROUTER[start: ROUTER.find("\n@router.", start)]
        assert "_require_human_capital_session(request)" in body and "_require_owner_for_diagnostics" not in body, fn
    for fn in ("human_capital_cv_history_import_start", "human_capital_cv_history_import_run", "human_capital_cv_history_discard", "human_capital_cv_history_import_plan", "human_capital_cv_history_rearbitrate"):
        start = ROUTER.index(f"def {fn}(")
        assert "_require_owner_for_diagnostics(request)" in ROUTER[start: ROUTER.find("\n@router.", start)], fn


# ═══════════════════ entrada desde el menú de Capital Humano ═══════════════════
def test_the_cchh_menu_shows_historicos_and_the_entry_leads_authorized_users_to_the_current_import(env, monkeypatch):
    store, rid, pablo = ready(env)
    hc = client_for(HC_USER, monkeypatch)
    menu = hc.get("/cchh/candidatos").text
    assert '<a href="/cchh/historicos" class="module-link ">' in menu.replace("\r", "").replace("\n", "").replace("   ", " ") or "/cchh/historicos" in menu
    assert "<span>07</span><strong>Históricos</strong>" in menu.replace("\r", "").replace("\n", "").replace("  ", "")
    go = TestClient(app, follow_redirects=False)
    assert go.get("/cchh/historicos").status_code in (302, 303, 307)                                                                    # sin sesión: al login
    assert client_for(OUTSIDER, monkeypatch).get("/cchh/historicos").status_code == 403                                                  # sin CCHH: bloqueado
    for email in (HC_USER, OWNER):
        res = client_for(email, monkeypatch).get("/cchh/historicos", follow_redirects=False)
        assert res.status_code == 307 and res.headers["location"] == f"{BASE}/{rid}/excepciones"
    page = hc.get(f"{BASE}/{rid}/excepciones").text
    assert re.search(r'class="module-link is-active">\s*<span>07</span><strong>Históricos</strong>', page)                                # el menú marca la sección


def test_the_entry_shows_a_notice_when_there_is_no_import_with_pending_documents(home, monkeypatch):
    hc = client_for(HC_USER, monkeypatch)
    res = hc.get("/cchh/historicos", follow_redirects=False)
    assert res.status_code == 200 and "data-historicos-empty" in res.text and res.headers["cache-control"] == "no-store" and "<h1>Históricos sin validación</h1>" in res.text
    mf.create_run("ch-20261026T000000Z-aaaa", {"kind": pilot.KIND_IMPORT, "state": "CREATED", "staging": {"state": "DISCARDED"}, "source": {}, "counters": {}})
    assert "data-historicos-empty" in hc.get("/cchh/historicos", follow_redirects=False).text                                          # un lote con los datos temporales ya eliminados no cuenta


def test_back_to_the_run_link_is_only_for_the_owner(env, monkeypatch):
    store, rid, pablo = ready(env)
    assert f'href="{BASE}/{rid}"' in client_for(OWNER, monkeypatch).get(f"{BASE}/{rid}/excepciones").text
    assert f'href="{BASE}/{rid}"' not in client_for(HC_USER, monkeypatch).get(f"{BASE}/{rid}/excepciones").text                          # esa página (iniciar y ejecutar la importación) es solo del owner


# ═══════════════════ integración visual con el Hub ═══════════════════
def test_the_screen_reuses_the_hub_components_and_keeps_the_important_warnings(env, monkeypatch):
    store, rid, pablo = ready(env)
    page = client_for(HC_USER, monkeypatch).get(f"{BASE}/{rid}/excepciones").text
    for hub_component in ('class="module-hero hc-profiles-hero"', "hc-profile-filters filter-toolbar-card", 'class="dashboard-sections"', 'class="dashboard-section"',
                          'class="dashboard-table"', "status-badge", "data-hc-authorize-write"):
        assert hub_component in page, hub_component                                                                                       # lenguaje visual del Hub: nada de estilos propios paralelos
    assert "data-historicos-intro" in page and "no son perfiles del Banco" in page and "datos personales" in page                          # la advertencia importante sigue visible
    assert "data-historicos-help" in page and "<details" in page.split("data-historicos-help", 1)[0][-40:]                                 # la explicación larga está plegada
    assert "<h3>Resumen" not in page and page.count('class="callout') <= page.count("<tr") + 2                                             # un solo aviso general; callouts solo donde corresponde (aceptación irreversible)
    assert "data-exc-more" in page and "Otras acciones" in page                                                                           # las acciones secundarias, plegadas
