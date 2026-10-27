"""Operaciones ↔ CCHH sobre la MISMA búsqueda: visibilidad cruzada de las ediciones, auditoría de los cambios compartidos (puesto, área, modalidad), sin sobrescrituras silenciosas, y permisos / visibilidad de
candidatos y de los actores de CCHH desde Operaciones. Mismos fixtures que I1/I2 (`tmp_path` + remoto falso: sin Spaces, SharePoint ni DigitalOcean)."""
from __future__ import annotations

import re

import pytest

from webapp import access
from webapp.services import auth_service, busqueda_repository as repo, human_capital_service as svc, profile_repository
from tests.webapp.test_busqueda_solicitud_i1 import (  # noqa: F401
    CCHH_EMAIL, OPS_A, OPS_B, SID, FakeRemote, doc, env, files, remote, req, rev, seed_catalog, send, to_state,
)
from tests.webapp.test_operations_busquedas_i2 import BASE, ORIGIN, form_payload, ops, session_client  # noqa: F401

OWNER = "owner@danwor.com"


@pytest.fixture(autouse=True)
def users(monkeypatch):
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", f"{OPS_A},{OPS_B},{OWNER}")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", f"{CCHH_EMAIL},{OWNER}")


def _cchh_edit(**fields):
    d = doc()
    base = {"nombre_puesto": d["nombre_puesto"], "fecha_apertura": d["fecha_apertura"], "area_consultora": d["area_consultora"], "modalidad": d["modalidad"]}
    return svc.update_busqueda(SID, d["revision"], {**base, **fields}, actor=CCHH_EMAIL)


def test_an_operations_edit_after_the_take_shows_up_in_cchh_and_a_cchh_edit_shows_up_in_operations():
    to_state("en_gestion")
    r = ops(OPS_B).post(f"{BASE}/{SID}/editar", data={"expected_revision": str(rev()), **form_payload(None, nombre_puesto="Puesto de Operaciones", comentarios="nota de Operaciones")}, headers=ORIGIN)
    assert r.status_code == 303
    hc = session_client(CCHH_EMAIL).get(f"/cchh/busquedas/{SID}").text
    assert "Puesto de Operaciones" in hc and "Editada por Operaciones" in hc and OPS_B in hc and "nota de Operaciones" in hc          # proceso, requerimiento e historial de CCHH
    _cchh_edit(nombre_puesto="Puesto de CCHH", area_consultora="Cloud", modalidad="Remoto")
    page = ops().get(f"{BASE}/{SID}").text
    assert "Puesto de CCHH" in page and "Editada por Capital Humano" in page and CCHH_EMAIL in page                                   # requerimiento e historial de Operaciones
    assert "Nombre del puesto" in page and "Área de la consultora" in page and "Modalidad" in page                                   # auditoría: qué campos cambió CCHH
    d = doc()
    assert d["nombre_puesto"] == d["requerimiento"]["nombre_puesto"] == "Puesto de CCHH" and d["requerimiento"]["area_consultora"] == "Cloud" and d["requerimiento"]["modalidad"] == "Remoto"
    assert [h["transicion"] for h in d["solicitud"]["historial"] if h["transicion"] in ("editar", "editar_cchh")] == ["editar", "editar_cchh"]


def test_cross_edits_never_overwrite_each_other_silently():
    to_state("en_gestion")
    stale = rev()
    _cchh_edit(nombre_puesto="Puesto de CCHH")
    # 1) un formulario de Operaciones abierto ANTES del cambio de CCHH no lo pisa: conflicto 409 y nada cambia
    before = doc()
    r = ops().post(f"{BASE}/{SID}/editar", data={"expected_revision": str(stale), **form_payload(None, nombre_puesto="Consultor SAP FI", comentarios="viejo")}, headers=ORIGIN)
    assert r.status_code == 409 and doc() == before
    # 2) un formulario nuevo ya viene con el valor de CCHH; editar otro campo lo conserva (el ledger registra solo lo que cambió)
    html = ops().get(f"{BASE}/{SID}/editar").text
    assert 'value="Puesto de CCHH"' in html
    r = ops().post(f"{BASE}/{SID}/editar", data={"expected_revision": str(rev()), **form_payload(None, nombre_puesto="Puesto de CCHH", comentarios="otro campo")}, headers=ORIGIN)
    assert r.status_code == 303
    d = doc()
    assert d["nombre_puesto"] == d["requerimiento"]["nombre_puesto"] == "Puesto de CCHH"
    last = d["solicitud"]["historial"][-1]
    assert last["transicion"] == "editar" and last["actor"] == OPS_A and "Nombre del puesto" not in last["motivo"] and "Perfil solicitado" not in last["motivo"] and "Observaciones" in last["motivo"]
    # 3) CCHH no propaga vacíos al requerimiento (Operaciones exige modalidad y puesto)
    _cchh_edit(modalidad="")
    assert doc()["requerimiento"]["modalidad"] == "Híbrido" and doc()["modalidad"] is None
    ev = [h for h in doc()["solicitud"]["historial"] if h["transicion"] == "editar_cchh"]
    assert len(ev) == 2 and ev[0]["actor"] == ev[1]["actor"] == CCHH_EMAIL and "Modalidad" in ev[1]["motivo"]


def test_a_cchh_edit_without_changes_writes_no_event():
    to_state("en_gestion")
    n = len(doc()["solicitud"]["historial"])
    _cchh_edit()
    assert len(doc()["solicitud"]["historial"]) == n


def test_permissions_operations_only_user_cannot_reach_cchh_and_outsiders_see_nothing():
    to_state("en_gestion")
    c = ops(OPS_A)                                                                              # usuario de Operaciones SIN acceso a CCHH
    assert c.get(f"/cchh/busquedas/{SID}").status_code == 403
    assert c.post(f"/cchh/busquedas/{SID}/cerrar", data={}, headers=ORIGIN).status_code == 403
    assert c.post(f"/cchh/busquedas/{SID}/candidatos", data={"profile_id": "x"}, headers=ORIGIN).status_code == 403
    assert session_client("outsider@danwor.com").get(f"{BASE}/{SID}").status_code == 403
    assert session_client(CCHH_EMAIL).get(f"{BASE}/{SID}").status_code == 403                  # CCHH sin acceso a Operaciones tampoco entra
    anon = session_client(CCHH_EMAIL)
    anon.cookies.clear()
    assert anon.post(f"{BASE}/{SID}/editar", data={"expected_revision": "1"}, headers=ORIGIN).status_code == 401


def test_operations_sees_candidate_names_and_cchh_actors_but_no_contact_data_or_actions(monkeypatch):
    to_state("en_gestion")
    pid = profile_repository.create({"first_name": "Cand", "last_name": "Visible", "email": "contacto@candidato.com", "phone": "+54 11 5555 1234", "profile_skills": "SAP FI", "seniority": "SR"})["profile_id"]
    svc.add_candidate_to_busqueda(SID, pid, actor=CCHH_EMAIL)
    html = ops().get(f"{BASE}/{SID}").text
    assert "Cand Visible" in html and "SAP FI" in html and "Candidato asociado" in html and CCHH_EMAIL in html                # nombre, perfil y actores de CCHH
    for hidden in ("contacto@candidato.com", "5555 1234", pid, "mailto:", "wa.me", "/cchh/candidatos", "/cchh/busquedas", 'name="etapa"', "Quitar del proceso", "Contactar", "busqueda-add-dialog", "busqueda-close-dialog"):
        assert hidden not in html, hidden
    assert 'data-pipeline-mode="solo-lectura"' in html
    # APP_OWNER: modo total (las mismas acciones del detalle de CCHH), sin cambiar la autorización de nadie más
    monkeypatch.setattr(access, "APP_OWNER", OWNER)
    full = session_client(OWNER).get(f"{BASE}/{SID}").text
    assert 'data-pipeline-mode="total"' in full and "busqueda-add-dialog" in full and "busqueda-close-dialog" in full and "Editar proceso" in full
    assert 'data-evaluation-locked' not in full and "/cchh/busquedas/" in full
    assert 'data-pipeline-mode="solo-lectura"' in ops().get(f"{BASE}/{SID}").text


def test_vigentes_and_cerradas_lists_and_their_empty_states_follow_the_hub_format():
    c = ops()
    html = c.get(BASE).text
    assert "Solicitudes Vigentes" in html and "Solicitudes Cerradas" in html
    assert re.search(r'class="dashboard-empty" data-empty-kind="no_cerradas"><span class="dashboard-empty__label">Sin solicitudes cerradas</span>', html)       # estado vacío con el formato del Hub (no un párrafo suelto)
    to_state("en_gestion")
    svc.close_busqueda(SID, actor=CCHH_EMAIL)
    html = c.get(BASE).text
    assert re.search(r'data-empty-kind="no_vigentes"', html) and 'id="opsb-table-closed"' in html and 'id="opsb-table"' not in html
    assert html.index("Solicitudes Vigentes") < html.index("Solicitudes Cerradas")
