"""Ajuste 15 — Precarga de la Modalidad de contratación de la entrevista desde el requerimiento de Operaciones.

Precedencia: valor guardado → (candidato: no aplica, el Banco no tiene ese atributo) → valor compatible del requerimiento → sin especificar. Solo LECTURA/precarga: nada se escribe ni se modifica retroactivamente.
`tipo_contrato` del requerimiento == Modalidad de contratación (mismo catálogo gobernado); `dedicacion` (carga horaria) y `modalidad` (Híbrido/Remoto) son atributos distintos y NO se mapean."""
from __future__ import annotations

import re

import pytest

from webapp.services import busqueda_repository as brepo, entrevista_repository as erepo, human_capital_service as svc, profile_repository
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, OPS_A, SID, env, remote, req, seed_catalog  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import session_client, operations_users  # noqa: F401


@pytest.fixture()
def c():
    return session_client(CCHH_EMAIL)


def _taken(sid=SID, **over) -> str:
    svc.create_solicitud(sid, req(**over), OPS_A)
    svc.take_solicitud(sid, 1, CCHH_EMAIL)
    return sid


def _candidate(bid, first="Mateo") -> tuple[str, str]:
    pid = profile_repository.create({"first_name": first, "last_name": "Test", "email": f"{first.lower()}@danwor.com"})["profile_id"]
    return pid, svc.add_candidate_to_busqueda(bid, pid, actor=CCHH_EMAIL)["candidatos"][-1]["application_id"]


def _selected(html: str, select_id: str) -> str | None:
    block = html.split(f'id="{select_id}"')[1].split("</select>")[0]
    m = re.search(r'<option value="([^"]*)"\s+selected', block)
    return (m.group(1) or None) if m else None


def _legacy_interview(pid, bid, app_id, **fields) -> dict:
    """Entrevista como las creadas ANTES de este ajuste: puesto/área sí, modalidad vacía."""
    return erepo.create({"puesto_evaluado": "Consultor SAP FI", "area": "SAP", "modalidad_contratacion": None, **fields}, profile_id=pid, busqueda_id=bid, application_id=app_id)


# ═══════════════════ resolución (precedencia) ═══════════════════
def test_precedence_stored_then_requirement_then_unspecified():
    busqueda = {"requerimiento": {"tipo_contrato": "Part-Time"}}
    assert svc.resolve_entrevista_modalidad("Full-Time", busqueda) == ("Full-Time", "entrevista")        # el guardado gana siempre
    assert svc.resolve_entrevista_modalidad(None, busqueda) == ("Part-Time", "busqueda")                 # vacío -> condición del requerimiento
    assert svc.resolve_entrevista_modalidad("  ", busqueda) == ("Part-Time", "busqueda")
    assert svc.resolve_entrevista_modalidad(None, None) == (None, None)                                  # sin proceso -> sin especificar
    assert svc.resolve_entrevista_modalidad(None, {"requerimiento": {}}) == (None, None)
    assert svc.resolve_entrevista_modalidad(None, busqueda, edited=True) == (None, None)                 # ya editada: el vacío es decisión del usuario


def test_only_a_compatible_catalog_value_is_mapped_and_dedication_is_never_used():
    assert svc.busqueda_modalidad_contratacion({"requerimiento": {"tipo_contrato": "full time"}}) == "Full-Time"        # misma grafía del catálogo
    assert svc.busqueda_modalidad_contratacion({"requerimiento": {"tipo_contrato": "Contractor"}}) is None               # fuera del catálogo: no se mapea
    assert svc.busqueda_modalidad_contratacion({"requerimiento": {"dedicacion": {"valor": 20, "unidad": "horas_por_semana"}, "modalidad": "Híbrido"}}) is None   # dedicación/modalidad != contratación


def test_without_a_loaded_catalog_nothing_is_mapped(env):
    seed_catalog(area_consultora=["SAP"], seniority=["SR"])                                              # sin catálogo modalidad_contratacion
    assert svc.busqueda_modalidad_contratacion({"requerimiento": {"tipo_contrato": "Full-Time"}}) is None


# ═══════════════════ entrevistas nuevas ═══════════════════
def test_an_auto_created_interview_gets_area_and_modalidad_from_the_requirement(c):
    bid = _taken(tipo_contrato="Part-Time")
    pid, app_id = _candidate(bid)
    svc.move_application_stage(bid, app_id, "Entrevista CCHH", None, actor=CCHH_EMAIL)
    e = erepo.find_by_profile_and_busqueda(pid, bid)
    assert e["area"] == "SAP" and e["modalidad_contratacion"] == "Part-Time"
    page = c.get(f"/cchh/entrevistas/{e['entrevista_id']}/editar").text
    assert _selected(page, "ent-modalidad") == "Part-Time" and _selected(page, "ent-area") == "SAP"
    assert "Precargada desde las condiciones" not in page                                                # ya está guardada: no es una precarga transitoria


def test_the_new_form_from_the_pipeline_preloads_modalidad_and_creates_nothing(c):
    bid = _taken(tipo_contrato="Full-Time")
    pid, _ = _candidate(bid)
    before = len(erepo.list())
    page = c.get(f"/cchh/entrevistas/nueva?profile_id={pid}&busqueda_id={bid}").text
    assert _selected(page, "ent-modalidad") == "Full-Time" and _selected(page, "ent-area") == "SAP" and len(erepo.list()) == before
    other = c.get(f"/cchh/entrevistas/nueva?profile_id={pid}").text                                      # sin contexto de proceso: sin precarga
    assert _selected(other, "ent-modalidad") is None


def test_a_manual_process_without_requirement_leaves_modalidad_unspecified(c):
    bid = svc.create_busqueda({"nombre_puesto": "Solution Architect", "fecha_apertura": "2026-10-01"}, actor=CCHH_EMAIL)["busqueda_id"]
    pid, app_id = _candidate(bid)
    svc.move_application_stage(bid, app_id, "Entrevista CCHH", None, actor=CCHH_EMAIL)
    e = erepo.find_by_profile_and_busqueda(pid, bid)
    assert e["modalidad_contratacion"] is None and _selected(c.get(f"/cchh/entrevistas/{e['entrevista_id']}/editar").text, "ent-modalidad") is None


# ═══════════════════ entrevistas existentes: precarga de lectura, sin escribir ═══════════════════
def test_editing_a_never_edited_legacy_interview_preloads_modalidad_without_writing(c):
    bid = _taken(tipo_contrato="Part-Time")
    pid, app_id = _candidate(bid)
    e = _legacy_interview(pid, bid, app_id)
    page = c.get(f"/cchh/entrevistas/{e['entrevista_id']}/editar").text
    assert _selected(page, "ent-modalidad") == "Part-Time" and "Precargada desde las condiciones" in page
    after = erepo.get(e["entrevista_id"])
    assert after["modalidad_contratacion"] is None and after["revision"] == 1 and after["updated_at"] == e["updated_at"]   # nada se persistió por abrir la edición


def test_a_stored_value_is_never_overridden_and_an_edited_empty_stays_empty(c):
    bid = _taken(tipo_contrato="Part-Time")
    pid, app_id = _candidate(bid)
    e = _legacy_interview(pid, bid, app_id, modalidad_contratacion="Full-Time")
    page = c.get(f"/cchh/entrevistas/{e['entrevista_id']}/editar").text
    assert _selected(page, "ent-modalidad") == "Full-Time" and "Precargada desde las condiciones" not in page
    pid2, app2 = _candidate(bid, "Lucia")
    e2 = _legacy_interview(pid2, bid, app2)
    erepo.update(e2["entrevista_id"], 1, {"observaciones": "editada por CCHH"})                          # alguna edición previa => revision 2
    page2 = c.get(f"/cchh/entrevistas/{e2['entrevista_id']}/editar").text
    assert _selected(page2, "ent-modalidad") is None and "Precargada desde las condiciones" not in page2


def test_saving_the_preloaded_form_persists_the_value_and_changing_it_is_respected(c):
    bid = _taken(tipo_contrato="Part-Time")
    pid, app_id = _candidate(bid)
    e = _legacy_interview(pid, bid, app_id)
    r = c.post(f"/cchh/entrevistas/{e['entrevista_id']}/editar", data={"expected_revision": "1", "puesto_evaluado": "Consultor SAP FI", "area": "SAP", "modalidad_contratacion": "Full-Time"})
    assert r.status_code == 303 and erepo.get(e["entrevista_id"])["modalidad_contratacion"] == "Full-Time"   # el usuario eligió otra distinta de la precarga
