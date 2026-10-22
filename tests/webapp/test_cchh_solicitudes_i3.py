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
    for fragment in ("Consultor SAP FI", "Migración S/4", "SSR, SR", "40 · Horas por semana", "Oficina del cliente, CABA", "Configurar FI.", "Intermedio", "FTE", "Historial"):
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


def test_the_inbox_is_ordered_by_planned_start_date_descending_and_paginated_from_row_50():
    for i in range(52):
        send(str(__import__("uuid").uuid4()), nombre_puesto=f"Perfil {i:02d}", fecha_inicio_prevista=f"2026-{(i % 12) + 1:02d}-{(i % 27) + 1:02d}")
    c = cchh()
    html = c.get("/cchh/busquedas").text
    rows = html.split('<tr data-solicitud-id=')[1:]
    dates = [re.findall(r"\d\d-\d\d-\d{4}", r)[-1] for r in rows]                 # la última fecha de cada fila = inicio previsto
    assert len(dates) == 50 and "52 solicitudes" in html and 'class="activity-pager"' in html
    as_iso = [d[6:] + d[3:5] + d[:2] for d in dates]
    assert as_iso == sorted(as_iso, reverse=True)
    page2 = c.get("/cchh/solicitudes", params={"solicitud": "recibidas", "page": "2", "page_size": "50"}, headers=HX).text
    assert len(_rows(page2)) == 2 and "52 solicitudes" in page2
