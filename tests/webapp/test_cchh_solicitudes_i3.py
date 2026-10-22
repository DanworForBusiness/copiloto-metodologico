"""I3 — CCHH recibe y gestiona las solicitudes de Operaciones: bandeja «Solicitudes recibidas», Tomar en gestión, Solicitar corrección, detalle estructurado y aislamiento de contadores/listados.

Tests focalizados (autorización, privacidad, integridad, persistencia). La persistencia es la de I1; aislamiento con `tmp_path` + `remote_store` falso (fixtures reutilizadas de I1)."""
from __future__ import annotations

import re

import pytest

from webapp.services import busqueda_repository as repo, human_capital_service as svc, profile_repository
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, OPS_A, SID, SID2, doc, env, files, remote, req, rev, seed_catalog, send, to_state   # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import ORIGIN, operations_users, ops, session_client                                        # noqa: F401

SID3 = "0b6c1d2e-7a44-4f0e-8c35-9d2f5e1a7b21"
HX = {"HX-Request": "true"}


def cchh():
    return session_client(CCHH_EMAIL)


def post(client, path, headers=ORIGIN, **data):
    return client.post(path, data=data, headers=headers)


def _rows(html: str) -> list[str]:
    return re.findall(r'data-solicitud-id="([0-9a-f-]+)"', html)


def _active_ids() -> set[str]:
    return {b["busqueda_id"] for b in svc.list_busquedas_gestionadas()}


# ═══════════════════ listado: solo procesos gestionados ═══════════════════
def test_the_process_list_counts_only_managed_busquedas_and_keeps_direct_cchh_ones():
    to_state("enviada", SID)
    send(SID2); svc.observe_solicitud(SID2, rev(SID2), "Falta algo", CCHH_EMAIL)
    send(SID3); svc.cancel_solicitud(SID3, rev(SID3), OPS_A)
    direct = svc.create_busqueda({"nombre_puesto": "Alta directa CCHH", "fecha_apertura": "2026-09-01", "area_consultora": "SAP"}, actor=CCHH_EMAIL)
    html = cchh().get("/cchh/busquedas").text
    assert direct["busqueda_id"] in html and "Alta directa CCHH" in html
    for pending in (SID, SID2, SID3):
        assert f'href="/cchh/busquedas/{pending}"' not in html.split("hc-solicitudes-panel")[-1].split('data-section-id="busquedas-activas"')[-1]   # ni en activas ni en cerradas
    assert "1 proceso" in html and "Procesos activos" in html                                            # contador = solo la alta directa


def test_a_taken_request_joins_the_active_list_and_a_cancelled_one_never_does():
    to_state("en_gestion", SID)
    send(SID2); svc.cancel_solicitud(SID2, rev(SID2), OPS_A)
    assert _active_ids() == {SID}
    html = cchh().get("/cchh/busquedas").text
    assert html.count('class="busqueda-card"') == 1 and f'href="/cchh/busquedas/{SID}"' in html


def test_publicaciones_only_offers_managed_busquedas():
    to_state("enviada", SID)
    send(SID2); svc.take_solicitud(SID2, rev(SID2), CCHH_EMAIL)
    html = cchh().get("/cchh/publicaciones/nueva").text
    assert SID2 in html and SID not in html


# ═══════════════════ bandeja Solicitudes recibidas ═══════════════════
def test_the_inbox_shows_sent_and_observed_by_default_and_cancelled_only_under_its_filter():
    send(SID); send(SID2); svc.observe_solicitud(SID2, rev(SID2), "Falta algo", CCHH_EMAIL)
    send(SID3); svc.cancel_solicitud(SID3, rev(SID3), OPS_A)
    c = cchh()
    assert sorted(_rows(c.get("/cchh/busquedas").text)) == sorted([SID, SID2])                           # por defecto: enviadas + observadas
    assert _rows(c.get("/cchh/solicitudes", params={"solicitud": "enviadas"}, headers=HX).text) == [SID]
    assert _rows(c.get("/cchh/solicitudes", params={"solicitud": "observadas"}, headers=HX).text) == [SID2]
    canc = c.get("/cchh/solicitudes", params={"solicitud": "canceladas"}, headers=HX).text
    assert _rows(canc) == [SID3] and "Cancelada" in canc and "Por atender · 2" in canc                    # no cuentan como pendientes
    assert _rows(c.get("/cchh/solicitudes", params={"solicitud": "inventado"}, headers=HX).text) == _rows(c.get("/cchh/busquedas").text)


def test_the_inbox_hides_taken_requests_and_shows_the_resend_indicator():
    send(SID); svc.take_solicitud(SID, rev(SID), CCHH_EMAIL)
    send(SID2); svc.observe_solicitud(SID2, rev(SID2), "x", CCHH_EMAIL); svc.resubmit_solicitud(SID2, rev(SID2), req(comentarios="ok"), OPS_A)
    html = cchh().get("/cchh/busquedas").text
    assert _rows(html) == [SID2] and "Reenviada" in html and "1 reenvío" in html


def test_the_inbox_fragment_is_only_for_htmx_and_requires_cchh():
    send()
    assert cchh().get("/cchh/solicitudes").status_code == 303                                           # sin HX-Request: vuelve a la pantalla
    assert ops().get("/cchh/solicitudes", headers=HX).status_code == 403                                # Operaciones no entra a CCHH
    assert ops().get("/cchh/busquedas").status_code == 403


# ═══════════════════ detalle de la solicitud en CCHH ═══════════════════
def test_a_sent_request_shows_the_structured_requirement_and_only_take_or_request_correction():
    send()
    html = cchh().get(f"/cchh/busquedas/{SID}").text
    for fragment in ("Consultor SAP FI", "Migración S/4", "SSR, SR", "40 · Horas por semana", "Oficina del cliente, CABA", "Configurar FI.", "Intermedio", "Full-Time", "Historial"):
        assert fragment in html, fragment
    assert "Tomar en gestión" in html and "Solicitar corrección" in html and f'action="/cchh/busquedas/{SID}/tomar"' in html
    assert 'data-solicitud-badge>Enviada<' in html and "status-badge--activo" not in html.split("data-solicitud-badge")[0].split("busqueda-status-row")[-1]      # no se rotula «Activo» una solicitud pendiente
    for hidden in ("+ Agregar candidato", "Cerrar proceso", "busqueda-add-dialog", "busqueda-close-dialog", "busqueda-pipeline-bar", ">Editar<"):
        assert hidden not in html, hidden


@pytest.mark.parametrize("estado, marker", [("observada", "Corrección solicitada a Operaciones"), ("cancelada", "Solicitud cancelada por Operaciones")])
def test_observed_and_cancelled_requests_are_read_only_for_cchh(estado, marker):
    to_state(estado)
    html = cchh().get(f"/cchh/busquedas/{SID}").text
    assert marker in html and "Consultor SAP FI" in html
    for hidden in ("Tomar en gestión", "Solicitar corrección", "+ Agregar candidato", "Cerrar proceso", "busqueda-pipeline-bar", ">Editar<"):
        assert hidden not in html, hidden


def test_a_taken_request_shows_the_pipeline_actions_again_plus_the_original_requirement():
    to_state("en_gestion")
    html = cchh().get(f"/cchh/busquedas/{SID}").text
    assert "+ Agregar candidato" in html and "Cerrar proceso" in html and "busqueda-pipeline-bar" in html and "Migración S/4" in html and "Tomar en gestión" not in html


# ═══════════════════ acciones: autorización, integridad y persistencia ═══════════════════
def test_taking_a_request_moves_it_to_gestion_and_to_the_active_list():
    send()
    r = post(cchh(), f"/cchh/busquedas/{SID}/tomar", expected_revision=str(rev()))
    assert r.status_code == 303 and r.headers["location"] == f"/cchh/busquedas/{SID}?ok=tomada"
    d = doc()
    assert d["solicitud"]["estado"] == "en_gestion" and d["solicitud"]["tomada_por"] == CCHH_EMAIL and SID in _active_ids()
    page = cchh().get(r.headers["location"]).text
    assert "Solicitud tomada en gestión" in page and "+ Agregar candidato" in page


def test_requesting_a_correction_needs_a_reason_and_moves_it_to_observada():
    send()
    c = cchh()
    before = doc()
    for empty in ("", "   "):
        r = post(c, f"/cchh/busquedas/{SID}/solicitar-correccion", expected_revision=str(rev()), motivo=empty)
        assert r.status_code == 422 and "motivo" in r.text.lower()
    assert doc() == before
    r = post(c, f"/cchh/busquedas/{SID}/solicitar-correccion", expected_revision=str(rev()), motivo="Falta el lugar de trabajo")
    assert r.status_code == 303 and r.headers["location"].endswith("?ok=correccion")
    d = doc()
    assert d["solicitud"]["estado"] == "observada" and d["solicitud"]["observacion"]["motivo"] == "Falta el lugar de trabajo" and SID not in _active_ids()
    ops_page = ops().get(f"/operaciones/busquedas/{SID}").text                                          # Operaciones ve la observación y puede corregir
    assert "Falta el lugar de trabajo" in ops_page and "Corregir y reenviar" in ops_page


@pytest.mark.parametrize("action, data", [("tomar", {}), ("solicitar-correccion", {"motivo": "x"})])
def test_operaciones_users_cannot_take_or_request_corrections_and_nothing_changes(action, data):
    send()
    before = doc()
    assert post(ops(), f"/cchh/busquedas/{SID}/{action}", expected_revision="1", **data).status_code == 403
    assert doc() == before


@pytest.mark.parametrize("action", ["tomar", "solicitar-correccion"])
def test_cchh_writes_require_the_same_origin_a_valid_revision_and_a_current_state(action):
    send()
    before = doc()
    data = {"expected_revision": "1", "motivo": "x"}
    for headers in ({}, {"Origin": "https://evil.example"}):
        assert cchh().post(f"/cchh/busquedas/{SID}/{action}", data=data, headers=headers).status_code == 403
    assert cchh().post(f"/cchh/busquedas/{SID}/{action}", data={**data, "expected_revision": "x"}, headers=ORIGIN).status_code == 400
    svc.edit_solicitud(SID, 1, req(comentarios="otro"), OPS_A)                                           # Operaciones editó: la revisión 1 quedó vieja
    stale = cchh().post(f"/cchh/busquedas/{SID}/{action}", data=data, headers=ORIGIN)
    assert stale.status_code == 409 and "cambió" in stale.text and doc()["solicitud"]["estado"] == "enviada"
    assert before["solicitud"]["estado"] == "enviada"


def test_transitions_that_do_not_apply_are_controlled_conflicts_not_errors():
    to_state("cancelada")
    c = cchh()
    assert post(c, f"/cchh/busquedas/{SID}/tomar", expected_revision=str(rev())).status_code == 409
    assert post(c, f"/cchh/busquedas/{SID}/solicitar-correccion", expected_revision=str(rev()), motivo="x").status_code == 409
    assert doc()["solicitud"]["estado"] == "cancelada"
    direct = svc.create_busqueda({"nombre_puesto": "Directa", "fecha_apertura": "2026-09-01", "area_consultora": "SAP"}, actor=CCHH_EMAIL)
    assert post(c, f"/cchh/busquedas/{direct['busqueda_id']}/tomar", expected_revision="1").status_code == 409     # una alta directa de CCHH no es una solicitud
    assert post(c, "/cchh/busquedas/no-existe/tomar", expected_revision="1").status_code == 404


def test_candidates_cannot_be_associated_before_the_request_is_taken():
    send()
    pid = profile_repository.create({"first_name": "Cand", "last_name": "X", "email": "x@candidato.com"})["profile_id"]
    cchh().post(f"/cchh/busquedas/{SID}/candidatos", data={"profile_id": pid})
    assert doc()["candidatos"] == []                                                                     # el servidor (I1) lo rechaza aunque se fuerce la petición
    post(cchh(), f"/cchh/busquedas/{SID}/tomar", expected_revision=str(rev()))
    cchh().post(f"/cchh/busquedas/{SID}/candidatos", data={"profile_id": pid})
    assert [a["profile_id"] for a in doc()["candidatos"]] == [pid]


def test_a_storage_error_on_take_is_a_controlled_503(monkeypatch):
    send()
    monkeypatch.setattr(svc, "take_solicitud", lambda *a, **k: (_ for _ in ()).throw(repo.BusquedaStorageError("x /secreto")))
    r = post(cchh(), f"/cchh/busquedas/{SID}/tomar", expected_revision="1")
    assert r.status_code == 503 and "secreto" not in r.text and doc()["solicitud"]["estado"] == "enviada"


def _names(html: str) -> list[str]:
    return [r.split("</a>")[0].rsplit(">", 1)[-1] for r in html.split('<span class="dw-idcell__name"><a href=')[1:]]


def test_inbox_order_is_last_reception_descending_a_new_request_comes_first():
    send(SID, nombre_puesto="Primera"); send(SID2, nombre_puesto="Segunda"); send(SID3, nombre_puesto="Tercera")
    assert _names(cchh().get("/cchh/busquedas").text) == ["Tercera", "Segunda", "Primera"]


def test_a_resubmitted_request_returns_to_the_first_position_and_an_observation_does_not_move_it():
    send(SID, nombre_puesto="Primera"); send(SID2, nombre_puesto="Segunda"); send(SID3, nombre_puesto="Tercera")
    svc.observe_solicitud(SID, rev(SID), "Falta algo", CCHH_EMAIL)                                        # observar NO cambia la fecha de recepción
    assert _names(cchh().get("/cchh/busquedas").text) == ["Tercera", "Segunda", "Primera"]
    svc.resubmit_solicitud(SID, rev(SID), req(nombre_puesto="Primera", comentarios="corregido"), OPS_A)   # reenviar = nueva recepción
    assert _names(cchh().get("/cchh/busquedas").text) == ["Primera", "Tercera", "Segunda"]
    assert doc(SID)["solicitud"]["ultimo_envio_en"] > doc(SID3)["solicitud"]["ultimo_envio_en"]


def test_cancelled_requests_are_ordered_by_cancellation_date_inside_their_filter():
    send(SID, nombre_puesto="Primera"); send(SID2, nombre_puesto="Segunda"); send(SID3, nombre_puesto="Tercera")
    svc.cancel_solicitud(SID2, rev(SID2), OPS_A)
    svc.cancel_solicitud(SID, rev(SID), OPS_A)                                                             # la última en cancelarse va primero aunque se haya enviado antes
    canc = cchh().get("/cchh/solicitudes", params={"solicitud": "canceladas"}, headers=HX).text
    assert _names(canc) == ["Primera", "Segunda"]
    assert _names(cchh().get("/cchh/busquedas").text) == ["Tercera"]                                       # «Por atender» no incluye canceladas


def test_ties_are_broken_deterministically():
    from webapp.routers.human_capital import _solicitud_recepcion_key
    same = {"enviada_en": "2026-10-22T10:00:00+00:00", "ultimo_envio_en": "2026-10-22T10:00:00+00:00", "estado_solicitud": "enviada"}
    rows = [{**same, "busqueda_id": "b"}, {**same, "busqueda_id": "c"}, {**same, "busqueda_id": "a"}, {**same, "enviada_en": "2026-10-22T09:00:00+00:00", "busqueda_id": "z"}]
    assert [r["busqueda_id"] for r in sorted(rows, key=_solicitud_recepcion_key, reverse=True)] == ["c", "b", "a", "z"]
    assert sorted(rows, key=_solicitud_recepcion_key, reverse=True) == sorted(reversed(rows), key=_solicitud_recepcion_key, reverse=True)


def test_the_order_is_applied_before_paginating_and_the_pager_starts_at_row_50():
    for i in range(52):
        send(str(__import__("uuid").uuid4()), nombre_puesto=f"Perfil {i:02d}")
    c = cchh()
    page1 = c.get("/cchh/busquedas").text
    names1 = _names(page1)
    assert len(names1) == 50 and names1[0] == "Perfil 51" and names1[-1] == "Perfil 02" and "52 solicitudes" in page1 and 'class="activity-pager"' in page1
    assert _names(c.get("/cchh/solicitudes", params={"solicitud": "recibidas", "page": "2", "page_size": "50"}, headers=HX).text) == ["Perfil 01", "Perfil 00"]


def test_active_processes_keep_their_own_order_by_opening_date():
    for name, apertura in (("Proceso Vieja", "2026-01-10"), ("Proceso Reciente", "2026-09-10")):
        svc.create_busqueda({"nombre_puesto": name, "fecha_apertura": apertura, "area_consultora": "SAP"}, actor=CCHH_EMAIL)
    html = cchh().get("/cchh/busquedas").text
    assert html.index("Proceso Reciente") < html.index("Proceso Vieja")                                    # Procesos activos: fecha de apertura desc, sin cambios


# ═══════════════════ «+ Nuevo proceso»: tomar una solicitud en vez de crear un proceso manual ═══════════════════
def _available_ids(html: str) -> list[str]:
    return re.findall(r'<tr data-solicitud-id="([0-9a-f-]+)"', html.split("hc-solicitudes-disponibles-table")[-1]) if "hc-solicitudes-disponibles-table" in html else []


def test_new_process_lists_only_sent_or_resent_untaken_requests_with_the_minimum_data():
    send(SID, nombre_puesto="Enviada")
    send(SID2, nombre_puesto="Reenviada"); svc.observe_solicitud(SID2, rev(SID2), "x", CCHH_EMAIL); svc.resubmit_solicitud(SID2, rev(SID2), req(nombre_puesto="Reenviada", comentarios="ok"), OPS_A)
    send(SID3, nombre_puesto="Observada"); svc.observe_solicitud(SID3, rev(SID3), "x", CCHH_EMAIL)
    cancelled = "a1111111-1111-4111-8111-111111111111"; send(cancelled, nombre_puesto="Cancelada"); svc.cancel_solicitud(cancelled, rev(cancelled), OPS_A)
    taken = "b2222222-2222-4222-8222-222222222222"; send(taken, nombre_puesto="Tomada"); svc.take_solicitud(taken, rev(taken), CCHH_EMAIL)
    svc.create_busqueda({"nombre_puesto": "Alta directa", "fecha_apertura": "2026-09-01", "area_consultora": "SAP"}, actor=CCHH_EMAIL)
    html = cchh().get("/cchh/busquedas/nueva").text
    assert "Solicitudes disponibles para tomar" in html
    assert sorted(_available_ids(html)) == sorted([SID, SID2])                                              # solo enviadas y reenviadas
    for minimum in ("Migración S/4", "SAP", "Enviada", "Reenviada", "1 reenvío", "Tomar", "Recepción"):
        assert minimum in html, minimum
    for hidden in ("Observada", "Cancelada", "Tomada", "Alta directa"):
        assert f"{hidden}</a>" not in html, hidden                                                          # no reutilizables
    assert 'name="expected_revision"' in html and 'name="desde" value="nuevo"' in html


def test_new_process_explains_taking_a_request_as_a_tooltip_on_the_collapsible_not_as_section_text():
    help_text = "Si el requerimiento ya fue enviado desde Operaciones, tomalo desde acá en vez de crear un proceso nuevo: conserva su requerimiento y su historial."
    send(SID)
    html = cchh().get("/cchh/busquedas/nueva").text
    summary = re.search(r'data-section-id="solicitudes-disponibles".*?<summary class="dashboard-section__head"[^>]*>(.*?)</summary>', html, re.S).group(1)
    assert "Solicitudes disponibles para tomar" in summary and 'class="chart-tooltip-anchor"' in summary and 'class="activity-kpi__info"' in summary   # (i) a nivel del colapsable
    assert f'aria-label="{help_text}"' in summary and f'<span class="chart-tooltip"><p>{help_text}</p></span>' in summary
    assert "chart-tooltip-anchor" not in re.search(r"<h1>(.*?)</h1>", html, re.S).group(1)                                          # no en el encabezado de la página
    assert html.count(help_text) == 2 and 'class="field-hint">Si el requerimiento' not in html                                    # solo aria-label + tooltip: ya no es texto dentro de la sección


def test_the_take_shortcut_is_the_same_icon_action_in_the_landing_and_in_new_process_and_only_for_takeable_requests():
    send(SID, nombre_puesto="Enviada")
    send(SID2, nombre_puesto="Observada"); svc.observe_solicitud(SID2, rev(SID2), "x", CCHH_EMAIL)
    take = lambda html, sid: re.search(rf'<form method="post" action="/cchh/busquedas/{sid}/tomar" class="dw-inline-form" data-solicitud-take>(.*?)</form>', html, re.S)   # noqa: E731
    landing, fragment, nuevo = (cchh().get("/cchh/busquedas").text, cchh().get("/cchh/solicitudes", headers=HX).text, cchh().get("/cchh/busquedas/nueva").text)
    for html in (landing, fragment):
        assert take(html, SID) and not take(html, SID2) and f'/cchh/busquedas/{SID2}"' in html                  # observada: solo «Ver»; enviada: atajo «Tomar» + «Ver»
    inner = lambda m: re.sub(r"\s+", " ", re.sub(r'<input type="hidden" name="desde" value="nuevo">', "", m.group(1))).strip()   # noqa: E731
    assert inner(take(landing, SID)) == inner(take(nuevo, SID))                                               # MISMO ícono + tooltip en las dos superficies (macro único)
    row = re.search(rf'<tr data-solicitud-id="{SID}".*?</tr>', landing, re.S).group(0)
    assert row.index('aria-label="Ver solicitud"') < row.index("data-solicitud-take")                         # orden estándar de acciones: ver primero, tomar al final
    body = take(landing, SID).group(1)
    assert 'class="table-action-btn chart-tooltip-anchor" aria-label="Tomar solicitud"' in body and "<svg" in body and "<p>Tomar</p>" in body
    assert "table-action-btn--text" not in nuevo and ">Tomar</button>" not in nuevo                         # la palabra «Tomar» ya no es el botón
    assert 'name="desde" value="nuevo"' in nuevo and 'name="desde"' not in body                                # solo «+ Nuevo proceso» conserva su aviso de retorno
    revision = re.search(r'name="expected_revision" value="(\d+)"', body).group(1)
    r = cchh().post(f"/cchh/busquedas/{SID}/tomar", data={"expected_revision": revision}, headers=ORIGIN)       # el atajo del landing = la misma acción
    assert r.status_code == 303 and r.headers["location"] == f"/cchh/busquedas/{SID}?ok=tomada" and doc()["solicitud"]["estado"] == "en_gestion"


def test_taking_from_new_process_reuses_the_same_busqueda_without_creating_another():
    send(SID)
    before_files, before_hist = len(files()), [h["transicion"] for h in doc()["solicitud"]["historial"]]
    c = cchh()
    page = c.get("/cchh/busquedas/nueva").text
    revision = re.search(rf'/cchh/busquedas/{SID}/tomar".*?name="expected_revision" value="(\d+)"', page, re.S).group(1)
    r = c.post(f"/cchh/busquedas/{SID}/tomar", data={"expected_revision": revision, "desde": "nuevo"}, headers=ORIGIN)
    assert r.status_code == 303 and r.headers["location"] == f"/cchh/busquedas/{SID}?ok=tomada"          # al detalle de CCHH con el pipeline habilitado
    d = doc()
    assert len(files()) == before_files == 1 and d["busqueda_id"] == SID and d["solicitud"]["estado"] == "en_gestion"      # mismo busqueda_id, ninguna búsqueda nueva
    assert [h["transicion"] for h in d["solicitud"]["historial"]] == before_hist + ["tomar"] and d["requerimiento"]["nombre_puesto"] == "Consultor SAP FI" and d["origen"]["tipo"] == "operaciones"
    assert "+ Agregar candidato" in c.get(r.headers["location"]).text
    assert _available_ids(c.get("/cchh/busquedas/nueva").text) == [] and SID in _active_ids()               # desaparece de disponibles y figura en Procesos activos


BROWSER = {**ORIGIN, "Accept": "text/html,application/xhtml+xml"}


def test_conflict_taking_from_new_process_follows_post_redirect_get_and_shows_the_refreshed_list():
    send(SID, nombre_puesto="Primera"); send(SID2, nombre_puesto="Segunda")
    stale = rev(SID)
    svc.take_solicitud(SID, stale, CCHH_EMAIL)                                                              # otra persona la tomó mientras tanto
    c = cchh()
    r = c.post(f"/cchh/busquedas/{SID}/tomar", data={"expected_revision": str(stale), "desde": "nuevo"}, headers=BROWSER)
    assert r.status_code == 303 and r.headers["location"] == "/cchh/busquedas/nueva?aviso=tomada"          # el navegador NO queda en la URL del POST
    page = c.get(r.headers["location"])                                                                     # el aviso y la lista actualizada salen de un GET
    assert page.status_code == 200 and "data-solicitud-conflict" in page.text and "ya fue tomada" in page.text and _available_ids(page.text) == [SID2]
    assert len(files()) == 2 and doc()["solicitud"]["estado"] == "en_gestion"                               # la protección por revisión se mantiene: no hubo segunda toma ni duplicados


def test_non_html_consumers_get_the_conflict_status_and_message_instead_of_a_redirect():
    send()
    stale = rev()
    svc.take_solicitud(SID, stale, CCHH_EMAIL)
    r = cchh().post(f"/cchh/busquedas/{SID}/tomar", data={"expected_revision": str(stale), "desde": "nuevo"}, headers={**ORIGIN, "Accept": "application/json"})
    assert r.status_code == 409 and "location" not in r.headers and "ya fue tomada" in r.json()["detail"]
    other = cchh().post(f"/cchh/busquedas/{SID}/solicitar-correccion", data={"expected_revision": str(stale), "motivo": "x"}, headers={**ORIGIN, "Accept": "application/json"})
    assert other.status_code == 409 and "cambió" in other.json()["detail"] and doc()["solicitud"]["estado"] == "en_gestion"


def test_conflict_from_the_detail_screen_also_redirects_and_storage_errors_follow_the_same_pattern(monkeypatch):
    send()
    stale = rev()
    svc.edit_solicitud(SID, stale, req(comentarios="otro"), OPS_A)                                          # Operaciones editó: revisión vieja
    c = cchh()
    r = c.post(f"/cchh/busquedas/{SID}/tomar", data={"expected_revision": str(stale)}, headers=BROWSER)
    assert r.status_code == 303 and r.headers["location"] == f"/cchh/busquedas/{SID}?aviso=cambio"
    page = c.get(r.headers["location"])
    assert page.status_code == 200 and "cambió desde que abriste la pantalla" in page.text and doc()["solicitud"]["estado"] == "enviada"
    monkeypatch.setattr(svc, "take_solicitud", lambda *a, **k: (_ for _ in ()).throw(repo.BusquedaStorageError("x /secreto")))
    err = c.post(f"/cchh/busquedas/{SID}/tomar", data={"expected_revision": str(rev()), "desde": "nuevo"}, headers=BROWSER)
    assert err.status_code == 303 and err.headers["location"] == "/cchh/busquedas/nueva?aviso=error"
    assert "No pudimos guardar el cambio" in c.get(err.headers["location"]).text and "secreto" not in err.text


def test_the_notice_comes_from_a_whitelist_never_from_free_text_in_the_url():
    send()
    page = cchh().get("/cchh/busquedas/nueva", params={"aviso": "<script>alert(1)</script>"}).text
    assert "data-solicitud-conflict" not in page and "<script>alert(1)</script>" not in page
    assert "data-solicitud-conflict" not in cchh().get("/cchh/busquedas/nueva").text                         # sin aviso: sin banner


def test_two_people_taking_the_same_request_never_duplicate_anything():
    from concurrent.futures import ThreadPoolExecutor
    send()
    revision = str(rev())

    def take(_):
        return cchh().post(f"/cchh/busquedas/{SID}/tomar", data={"expected_revision": revision, "desde": "nuevo"}, headers=ORIGIN).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(take, range(2)))
    assert statuses == [303, 409]                                                                           # una gana, la otra recibe el conflicto
    assert len(files()) == 1 and [h["transicion"] for h in doc()["solicitud"]["historial"]].count("tomar") == 1


def test_only_cchh_can_take_from_the_new_process_screen():
    send()
    before = doc()
    assert ops().post(f"/cchh/busquedas/{SID}/tomar", data={"expected_revision": "1", "desde": "nuevo"}, headers=ORIGIN).status_code == 403
    assert ops().get("/cchh/busquedas/nueva").status_code == 403
    assert doc() == before


def test_manual_creation_still_works_and_the_checkbox_is_clarified():
    send()
    c = cchh()
    html = c.get("/cchh/busquedas/nueva").text
    assert "por otros medios" in html and "No lo vincula con una solicitud digital" in html and 'name="pedido_formal_recibido"' in html
    assert "tomalo desde «Solicitudes disponibles para tomar»" in html                                       # en el alta la ayuda remite a la sección que existe ahí
    direct = svc.create_busqueda({"nombre_puesto": "Directa", "fecha_apertura": "2026-09-01", "area_consultora": "SAP"}, actor=CCHH_EMAIL)
    edit = c.get(f"/cchh/busquedas/{direct['busqueda_id']}/editar").text
    assert "por otros medios" in edit and "No lo vincula con una solicitud digital" in edit and "Solicitudes disponibles para tomar" not in edit      # en edición no menciona una sección que no está
    r = c.post("/cchh/busquedas/nueva", data={"nombre_puesto": "Manual urgente", "fecha_apertura": "2026-10-01", "area_consultora": "SAP", "pedido_formal_recibido": "on"})
    assert r.status_code == 303
    created = [b for b in svc.list_busquedas() if b["nombre_puesto"] == "Manual urgente"]
    assert len(created) == 1 and "solicitud" not in created[0] and created[0]["estado"] == "activo"          # búsqueda común, sin vínculo con ninguna solicitud
    assert doc(SID)["solicitud"]["estado"] == "enviada" and len(files()) == 3        # solicitud + proceso manual + alta directa del chequeo de edición


def test_new_process_screen_survives_a_read_error_and_edit_has_no_available_list(monkeypatch):
    send()
    direct = svc.create_busqueda({"nombre_puesto": "Directa", "fecha_apertura": "2026-09-01", "area_consultora": "SAP"}, actor=CCHH_EMAIL)
    with monkeypatch.context() as m:
        m.setattr(svc, "list_solicitudes_view", lambda: (_ for _ in ()).throw(repo.BusquedaStorageError("x")))
        page = cchh().get("/cchh/busquedas/nueva")
    assert page.status_code == 200 and "No hay solicitudes de Operaciones pendientes de tomar" in page.text and 'name="nombre_puesto"' in page.text   # la creación manual no depende de la lista
    assert "cchh-solicitudes-disponibles" not in cchh().get(f"/cchh/busquedas/{direct['busqueda_id']}/editar").text      # la sección existe solo en el alta
