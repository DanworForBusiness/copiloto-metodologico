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


def test_incorporated_rows_show_the_persisted_candidate_and_only_the_actions_that_apply(world):
    """Vista «Incorporados»: el candidato persistido (sin datos propuestos vacíos), «Ver candidato» como ícono del Hub (no un enlace de texto) y sin lápiz ni papelera que parezcan editar o
    borrar al candidato; en una vista mixta, las acciones de bandeja de una fila incorporada dejan su lugar vacío. Columna de acciones nunca más angosta que su rótulo."""
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    intake.add("p1", "pablo.pdf", PABLO)
    hc = client_as(HC_USER)
    hc.post("/cchh/candidatos/importar/bandeja/analizar", data={"graph_token": TOKEN}, headers=ORIGIN)
    row = next(r for r in inbox.overview(TOKEN)["rows"] if r["name"] == "cv1.pdf")
    hc.post("/cchh/candidatos/importar/bandeja/aceptar", data={"graph_token": TOKEN, "sel": [f"{row['key']}|{row['fingerprint']}"]}, headers=ORIGIN)
    done = next(r for r in inbox.overview(TOKEN)["rows"] if r["name"] == "cv1.pdf")
    profile = profile_repository.get(done["profile_id"])
    view = hc.post("/cchh/candidatos/importar/bandeja", data={"graph_token": TOKEN, "vista": "incorporados"}).text
    assert "cv1.pdf" in view and "pablo.pdf" not in view
    assert ">Candidato</th>" in view.replace("\n", "") or "Candidato</th>" in view
    assert profile["email"] in view and "Candidato en el Banco" in view                                       # datos persistidos, no «—»
    assert f'href="/cchh/candidatos/{done["profile_id"]}" class="table-action-btn chart-tooltip-anchor" aria-label="Ver ficha del candidato"' in view
    assert ">Ver candidato</a>" not in view                                                                      # ya no es un enlace de texto
    assert "data-cvinbox-review-toggle" not in view and "data-cvinbox-discard" not in view                      # ni lápiz ni papelera en una vista solo de incorporados
    assert '<col style="width:76px">' in view                                                                    # dos acciones (ver candidato, eliminar original): el rótulo «Acciones» no desborda
    assert f'hx-post="/cchh/candidatos/importar/bandeja/{done["key"]}/eliminar"' in view and "data-cvinbox-delete" in view and "data-dw-confirm" in view
    assert "data-hc-write-token data-cvinbox-delete" in view and "ELIMINAR" in view and "como desde Candidatos" in view      # token de ESCRITURA, confirmación explícita y qué se elimina
    mixed = hc.post("/cchh/candidatos/importar/bandeja", data={"graph_token": TOKEN, "vista": "todos"}).text
    inc = mixed.split(f'data-cvinbox-row="{done["key"]}"', 1)[1].split("</tr>", 1)[0]
    assert "aria-label=\"Ver ficha del candidato\"" in inc and "hc-inbox-slot" in inc
    assert "data-cvinbox-review-toggle" not in inc and "data-cvinbox-discard" not in inc                        # su lugar queda vacío: nada que confunda con el candidato del Banco
    pending = mixed.split('data-cvinbox-row="', 1)[1].split("</tr>", 1)[0]
    assert "data-cvinbox-delete" not in pending or "cv1.pdf" in pending                                         # «Eliminar» solo en filas incorporadas
    assert "data-cvinbox-discard" in mixed                                                                       # las filas por resolver conservan sus acciones


def test_an_incorporated_cv_is_deleted_through_the_route_like_from_candidatos(world):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    hc = client_as(HC_USER)
    hc.post("/cchh/candidatos/importar/bandeja/analizar", data={"graph_token": TOKEN}, headers=ORIGIN)
    row = next(r for r in inbox.overview(TOKEN)["rows"] if r["name"] == "cv1.pdf")
    hc.post("/cchh/candidatos/importar/bandeja/aceptar", data={"graph_token": TOKEN, "sel": [f"{row['key']}|{row['fingerprint']}"]}, headers=ORIGIN)
    pid = next(r for r in inbox.overview(TOKEN)["rows"] if r["name"] == "cv1.pdf")["profile_id"]
    intake.allow_delete = True
    url = f"/cchh/candidatos/importar/bandeja/{row['key']}/eliminar"
    assert hc.post(url, data={"graph_token": TOKEN, "confirm_delete": "ELIMINAR"}).status_code == 403                 # escritura sin mismo origen: rechazada
    assert "requiere una confirmación explícita" in hc.post(url, data={"graph_token": TOKEN}, headers=ORIGIN).text and intake.deletes == []
    out = hc.post(url, data={"graph_token": TOKEN, "confirm_delete": "ELIMINAR", "vista": "incorporados"}, headers=ORIGIN).text
    assert "Se eliminó el candidato, sus 1 documento(s)" in out and intake.deletes == ["a1"] and (profile_repository.get(pid) or {}).get("deleted_at")
    assert "cv1.pdf" not in out.split("data-cvinbox-rows", 1)[-1]                                                    # la vista queda sin ese CV


def test_a_successful_incorporation_is_reported_as_success_and_the_pending_retire_is_retried_silently(world):
    """Un alta correcta se informa como ÉXITO aunque el retiro del original haya quedado pendiente (nunca un «Atención» sobre un alta correcta); el panel agenda UN reintento automático del retiro
    con el token de escritura, y ese reintento no muestra aviso si vuelve a fallar."""
    import urllib.error
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    hc = client_as(HC_USER)
    hc.post("/cchh/candidatos/importar/bandeja/analizar", data={"graph_token": TOKEN}, headers=ORIGIN)
    row = next(r for r in inbox.overview(TOKEN)["rows"] if r["name"] == "cv1.pdf")
    intake.fail_patch = [urllib.error.URLError("sin respuesta")]
    out = hc.post(f"/cchh/candidatos/importar/bandeja/{row['key']}/reintentar", data={"graph_token": TOKEN}, headers=ORIGIN).text   # sin decisión previa: no incorpora
    out = hc.post("/cchh/candidatos/importar/bandeja/aceptar", data={"graph_token": TOKEN, "sel": [f"{row['key']}|{row['fingerprint']}"]}, headers=ORIGIN).text
    assert 'data-kind="ok"' in out and "callout--warn" not in out.split("data-cvinbox-result", 1)[0][-200:]
    assert f'data-cvinbox-auto-retire="{row["key"]}"' in out and 'hx-trigger="load delay:3s"' in out and "&#34;auto&#34;: &#34;1&#34;" in out or '"auto": "1"' in out
    intake.fail_patch = [urllib.error.URLError("sin respuesta")]
    silent = hc.post(f"/cchh/candidatos/importar/bandeja/{row['key']}/retirar", data={"graph_token": TOKEN, "auto": "1"}, headers=ORIGIN).text
    assert "data-cvinbox-result" not in silent                                                                  # el reintento automático fallido no grita: el motivo queda en la fila
    done = hc.post(f"/cchh/candidatos/importar/bandeja/{row['key']}/retirar", data={"graph_token": TOKEN, "auto": "1"}, headers=ORIGIN).text
    assert "El archivo original se retiró" in done and intake.in_folder("sub-incorporados") == ["cv1.pdf"]
    assert "data-cvinbox-auto-retire" not in done                                                               # retirado: no se agenda otro reintento
