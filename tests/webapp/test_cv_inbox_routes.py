"""Rutas de la bandeja de Carga masiva: acceso solo de Capital Humano, mismo origen en las escrituras, token en el cuerpo, la modalidad Excel intacta y el panel con los estados. SharePoint simulado (el de
`test_cv_inbox`); datos sintéticos."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.webapp.test_cv_history_pilot import TOKEN, home  # noqa: F401  (fixture)
from tests.webapp.test_cv_inbox import ANA, LUCIA, NONAME, PABLO, world  # noqa: F401  (fixture)
from webapp.main import app
from webapp.services import auth_service, cv_inbox as inbox, profile_repository

HC_USER, OTHER_USER = "hc-inbox-routes@danwor.com", "ops-inbox-routes@danwor.com"
ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture(autouse=True)
def _access(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", HC_USER)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", OTHER_USER)
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", OTHER_USER)


def client_as(email):
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "Test", "first_name": "T", "family_name": "T"}))
    return c


def test_the_import_page_offers_both_modes_and_keeps_the_excel_form(world):
    page = client_as(HC_USER).get("/cchh/candidatos/importar")
    assert page.status_code == 200
    html = page.text
    assert 'data-import-tab="sharepoint"' in html and 'data-import-tab="excel"' in html
    assert 'action="/cchh/candidatos/importar/preview"' in html and 'name="file"' in html                  # el contrato de Excel no cambió
    assert 'hx-post="/cchh/candidatos/importar/bandeja"' in html and "data-dw-graph-token" in html
    assert 'class="dashboard-view-switch"' in html and "¿Cómo funciona?" in html and "Guía de uso" in html                   # primitivos del Hub (switch del CRM, guía del Catálogo)
    excel = client_as(HC_USER).get("/cchh/candidatos/importar?modo=excel").text
    assert "x-data=\"{ modo: 'excel' }\"" in excel
    assert 'class="is-active"' not in html.split('data-import-modes', 1)[1].split('</section>', 1)[0]          # el activo lo decide SOLO Alpine: nunca dos botones encendidos


def test_only_human_capital_reaches_the_inbox_and_writes_need_same_origin(world):
    other = client_as(OTHER_USER)
    assert other.get("/cchh/candidatos/importar").status_code == 403
    assert other.post("/cchh/candidatos/importar/bandeja", data={"graph_token": TOKEN}).status_code == 403
    assert other.post("/cchh/candidatos/importar/bandeja/analizar", data={"graph_token": TOKEN}, headers=ORIGIN).status_code == 403
    anonymous = TestClient(app).post("/cchh/candidatos/importar/bandeja", data={"graph_token": TOKEN}, follow_redirects=False)
    assert anonymous.status_code in (302, 303, 307)
    hc = client_as(HC_USER)
    assert hc.post("/cchh/candidatos/importar/bandeja/analizar", data={"graph_token": TOKEN}).status_code == 403            # sin Origin: escritura rechazada
    assert hc.post(f"/cchh/candidatos/importar/bandeja/{'a' * 16}/borrar", data={"graph_token": TOKEN}, headers=ORIGIN).status_code == 404
    assert hc.post("/cchh/candidatos/importar/bandeja/x/aceptar", data={"graph_token": TOKEN}, headers=ORIGIN).status_code == 404


def test_the_panel_lists_analyzes_and_accepts_through_the_routes(world):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    intake.add("n1", "documento.pdf", NONAME)
    intake.add("p1", "pablo.pdf", PABLO)
    hc = client_as(HC_USER)
    assert "Conectá tu cuenta" in hc.post("/cchh/candidatos/importar/bandeja", data={}).text
    assert 'class="dashboard-view-switch"' in hc.post("/cchh/candidatos/importar/bandeja", data={"graph_token": TOKEN}).text
    panel = hc.post("/cchh/candidatos/importar/bandeja", data={"graph_token": TOKEN})
    assert panel.status_code == 200 and panel.headers["cache-control"] == "no-store" and panel.text.count("Pendiente de análisis") == 3
    analyzed = hc.post("/cchh/candidatos/importar/bandeja/analizar", data={"graph_token": TOKEN}, headers=ORIGIN).text
    assert "Se analizaron 3 CVs." in analyzed and "Lista para incorporar" in analyzed and "Requiere revisión" in analyzed and "Identidad incompleta" in analyzed
    assert 'activity-kpi activity-kpi--clickable' in analyzed and "data-cvinbox-discard" in analyzed and 'hx-trigger="confirmed"' in analyzed
    assert len(profile_repository.list()) == 3                                                                # analizar no crea candidatos
    row = next(r for r in inbox.overview(TOKEN)["rows"] if r["name"] == "cv1.pdf")
    assert f'value="{row["key"]}|{row["fingerprint"]}"' in analyzed                                              # solo las filas seguras se pueden seleccionar
    pablo = next(r for r in inbox.overview(TOKEN)["rows"] if r["name"] == "pablo.pdf")
    assert f'value="{pablo["key"]}|{pablo["fingerprint"]}" form="cvinbox-batch" aria-label="Seleccionar pablo.pdf" data-cvinbox-select>' in analyzed   # seleccionable para descartar, pero NO listo (sin data-ready)
    done = hc.post("/cchh/candidatos/importar/bandeja/aceptar", data={"graph_token": TOKEN, "sel": [f"{row['key']}|{row['fingerprint']}", f"{pablo['key']}|x"]}, headers=ORIGIN).text
    assert "Se incorporaron 1 CV al Banco." in done and "1 necesita revisión." in done and len(profile_repository.list()) == 4 and intake.in_inbox() == ["documento.pdf", "pablo.pdf"]
    noname = next(r for r in inbox.overview(TOKEN)["rows"] if r["name"] == "documento.pdf")
    refused = hc.post(f"/cchh/candidatos/importar/bandeja/{noname['key']}/aceptar", headers=ORIGIN,
                      data={"graph_token": TOKEN, "first_name": "Rosa", "last_name": "Paz", "decision": "create", "fingerprint": noname["fingerprint"], "revision": "0"}).text
    assert "Sin email" in refused and len(profile_repository.list()) == 4
    refused_delete = hc.post(f"/cchh/candidatos/importar/bandeja/{pablo['key']}/descartar", data={"graph_token": TOKEN, "reason": "NOT_A_CANDIDATE", "delete_original": "true"}, headers=ORIGIN).text
    assert "segunda confirmación" in refused_delete and "pablo.pdf" in intake.in_inbox() and intake.deletes == []
    dismissed = hc.post(f"/cchh/candidatos/importar/bandeja/{pablo['key']}/descartar", data={"graph_token": TOKEN, "reason": "NOT_A_CANDIDATE"}, headers=ORIGIN).text
    assert "CV descartado. El archivo se movió a «carga-masiva/descartados»" in dismissed and intake.in_folder("sub-descartados") == ["pablo.pdf"]


def test_the_excel_import_routes_are_untouched(world):
    hc = client_as(HC_USER)
    assert hc.post("/cchh/candidatos/importar/preview", files={}).status_code == 200
    assert "Elegí un archivo XLSX" in hc.post("/cchh/candidatos/importar/preview", files={}).text


def test_the_table_sorts_by_header_and_keeps_the_view_and_order_in_every_action(world):
    store, intake = world
    for fid, name, data in (("p1", "b-pablo.pdf", PABLO), ("a1", "c-cv.pdf", ANA), ("l1", "a-lucia.pdf", LUCIA)):
        intake.add(fid, name, data)
    hc = client_as(HC_USER)
    hc.post("/cchh/candidatos/importar/bandeja/analizar", data={"graph_token": TOKEN}, headers=ORIGIN)
    asc = hc.post("/cchh/candidatos/importar/bandeja", data={"graph_token": TOKEN, "orden": "cv", "dir": "asc"}).text
    desc = hc.post("/cchh/candidatos/importar/bandeja", data={"graph_token": TOKEN, "orden": "cv", "dir": "desc"}).text
    order = lambda html: [n for n in ("a-lucia.pdf", "b-pablo.pdf", "c-cv.pdf") if n in html and html.index(n) >= 0]
    assert sorted(order(asc), key=asc.index) == ["a-lucia.pdf", "b-pablo.pdf", "c-cv.pdf"] and sorted(order(desc), key=desc.index) == ["c-cv.pdf", "b-pablo.pdf", "a-lucia.pdf"]
    assert 'aria-sort="ascending"' in asc and "CV ▲" in asc and 'data-resizable-columns-key="cchh-carga-masiva"' in asc and '<col data-column-key="cv"' in asc
    assert '&#34;orden&#34;: &#34;cv&#34;' in asc or '"orden": "cv"' in asc                                                # el orden viaja en las acciones del panel
    keys = [r["key"] for r in inbox.overview(TOKEN)["rows"] if r["name"] in ("b-pablo.pdf", "a-lucia.pdf")]
    out = hc.post("/cchh/candidatos/importar/bandeja/descartar", data={"graph_token": TOKEN, "sel": [k + "|-" for k in keys], "reason": "NOT_A_CANDIDATE"}, headers=ORIGIN).text
    assert "Se descartaron 2 CVs." in out and intake.in_folder("sub-descartados") == ["a-lucia.pdf", "b-pablo.pdf"]


def test_tiles_filter_the_table_and_the_close_button_restores_it(world):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    intake.add("x1", "notas.txt", b"hola")
    hc = client_as(HC_USER)
    hc.post("/cchh/candidatos/importar/bandeja/analizar", data={"graph_token": TOKEN}, headers=ORIGIN)
    filtered = hc.post("/cchh/candidatos/importar/bandeja", data={"graph_token": TOKEN, "filtro": "problemas"}).text
    assert "notas.txt" in filtered and "cv1.pdf" not in filtered and 'data-cvinbox-clear-filter' in filtered and "activity-kpi--clickable is-active" in filtered
    full = hc.post("/cchh/candidatos/importar/bandeja", data={"graph_token": TOKEN}).text
    assert "notas.txt" in full and "cv1.pdf" in full and "data-cvinbox-clear-filter" not in full
