"""test_human_capital_busquedas.py — Búsquedas (procesos de selección): modelo +
service + rutas del lifecycle transaccional (alta/edición/detalle/pipeline/
cierre). La relación con candidatos es por `profile_id` (Banco = maestro), y la
semántica de dominio (estado activo/cerrado; 5 etapas; resultado sólo en
Finalizado; historial de transiciones) es la de la app de referencia. MISMO
patrón de aislamiento/durabilidad-en-test que el resto de CCHH."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import auth_service, busqueda_repository, human_capital_service, profile_repository

_AUTHORIZED_EMAIL = "hc-busquedas-test@danwor.com"


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(busqueda_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(busqueda_repository.remote_store, "enabled", lambda: False)
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)


def _client() -> TestClient:
    session = auth_service.create_session(
        {"email": _AUTHORIZED_EMAIL, "name": "B T", "first_name": "B", "family_name": "T"})
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


def _profile(**over) -> str:
    base = {"first_name": "Cand", "last_name": "Idato", "email": "c@danwor.com"}
    base.update(over)
    return profile_repository.create(base)["profile_id"]


# ── modelo / service ─────────────────────────────────────────────────────────

def test_create_defaults_to_activo_and_empty_pipeline():
    b = human_capital_service.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"})
    assert b["estado"] == "activo"
    assert b["fecha_cierre"] is None
    assert b["candidatos"] == []
    assert b["revision"] == 1
    assert human_capital_service.get_busqueda(b["busqueda_id"]) is not None


def test_create_requires_nombre_and_fecha():
    with pytest.raises(ValueError):
        human_capital_service.create_busqueda({"nombre_puesto": "", "fecha_apertura": "2026-09-01"})
    with pytest.raises(ValueError):
        human_capital_service.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": ""})


def test_create_validates_enums():
    with pytest.raises(ValueError):
        human_capital_service.create_busqueda({"nombre_puesto": "D", "fecha_apertura": "2026-09-01", "modalidad": "Nope"})
    with pytest.raises(ValueError):
        human_capital_service.create_busqueda({"nombre_puesto": "D", "fecha_apertura": "2026-09-01", "area_consultora": "XX"})


def test_update_respects_revision():
    b = human_capital_service.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"})
    human_capital_service.update_busqueda(b["busqueda_id"], 1, {"nombre_puesto": "Dev Sr", "fecha_apertura": "2026-09-01"})
    assert human_capital_service.get_busqueda(b["busqueda_id"])["nombre_puesto"] == "Dev Sr"
    with pytest.raises(busqueda_repository.BusquedaConcurrentModificationError):
        human_capital_service.update_busqueda(b["busqueda_id"], 1, {"nombre_puesto": "X", "fecha_apertura": "2026-09-01"})


def test_add_candidate_starts_in_preseleccion_with_history():
    pid = _profile()
    b = human_capital_service.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"})
    b = human_capital_service.add_candidate_to_busqueda(b["busqueda_id"], pid)
    app_ = b["candidatos"][0]
    assert app_["profile_id"] == pid
    assert app_["etapa"] == "Preselección"
    assert app_["resultado_final"] is None
    assert app_["historial"] == [{"etapa": "Preselección", "fecha": app_["historial"][0]["fecha"]}]


def test_add_candidate_does_not_duplicate_active():
    pid = _profile()
    b = human_capital_service.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"})
    human_capital_service.add_candidate_to_busqueda(b["busqueda_id"], pid)
    b = human_capital_service.add_candidate_to_busqueda(b["busqueda_id"], pid)
    assert len(b["candidatos"]) == 1


def test_move_stage_records_history_and_forces_resultado_rules():
    pid = _profile()
    b = human_capital_service.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"})
    b = human_capital_service.add_candidate_to_busqueda(b["busqueda_id"], pid)
    aid = b["candidatos"][0]["application_id"]
    # etapa intermedia con resultado -> el resultado se ignora (None)
    b = human_capital_service.move_application_stage(b["busqueda_id"], aid, "Entrevista CCHH", "Ingreso")
    app_ = b["candidatos"][0]
    assert app_["etapa"] == "Entrevista CCHH"
    assert app_["resultado_final"] is None
    assert app_["historial"][-1]["etapa"] == "Entrevista CCHH"
    # Finalizado con resultado -> se conserva
    b = human_capital_service.move_application_stage(b["busqueda_id"], aid, "Finalizado", "Ingreso")
    assert b["candidatos"][0]["resultado_final"] == "Ingreso"


def test_move_stage_rejects_invalid_etapa():
    pid = _profile()
    b = human_capital_service.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"})
    b = human_capital_service.add_candidate_to_busqueda(b["busqueda_id"], pid)
    aid = b["candidatos"][0]["application_id"]
    with pytest.raises(ValueError):
        human_capital_service.move_application_stage(b["busqueda_id"], aid, "Etapa Fantasma", None)


def test_close_finalizes_pending_and_sets_cerrado():
    pid = _profile()
    b = human_capital_service.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"})
    b = human_capital_service.add_candidate_to_busqueda(b["busqueda_id"], pid)
    aid = b["candidatos"][0]["application_id"]
    b = human_capital_service.close_busqueda(b["busqueda_id"], [aid])
    assert b["estado"] == "cerrado"
    assert b["fecha_cierre"]
    assert b["candidatos"][0]["etapa"] == "Finalizado"
    assert b["candidatos"][0]["resultado_final"] == "Rechazado"


def _avail_ids(busqueda_id):
    b = human_capital_service.get_busqueda(busqueda_id)
    return {p["profile_id"] for p in human_capital_service.profiles_available_for_busqueda(b)}


def test_availability_is_per_search_not_global_single_active():
    # Contrato N:M aprobado: un candidato en la búsqueda activa A SIGUE
    # disponible para la búsqueda activa B; sólo deja de estar disponible para
    # A (mientras su postulación en A no esté finalizada). Participar en otra
    # búsqueda activa NUNCA lo excluye.
    pid = _profile()
    a = human_capital_service.create_busqueda({"nombre_puesto": "A", "fecha_apertura": "2026-09-01"})
    b = human_capital_service.create_busqueda({"nombre_puesto": "B", "fecha_apertura": "2026-09-01"})
    assert pid in _avail_ids(a["busqueda_id"])
    assert pid in _avail_ids(b["busqueda_id"])
    human_capital_service.add_candidate_to_busqueda(a["busqueda_id"], pid)  # queda en A (activa)
    assert pid not in _avail_ids(a["busqueda_id"])   # ya no disponible para A
    assert pid in _avail_ids(b["busqueda_id"])       # SIGUE disponible para B (activa)
    # tras finalizar su postulación en A, vuelve a estar disponible para A
    app_id = human_capital_service.get_busqueda(a["busqueda_id"])["candidatos"][0]["application_id"]
    human_capital_service.move_application_stage(a["busqueda_id"], app_id, "Finalizado", "Rechazado")
    assert pid in _avail_ids(a["busqueda_id"])


def test_remove_candidate_does_not_delete_profile():
    pid = _profile()
    b = human_capital_service.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"})
    b = human_capital_service.add_candidate_to_busqueda(b["busqueda_id"], pid)
    aid = b["candidatos"][0]["application_id"]
    b = human_capital_service.remove_application(b["busqueda_id"], aid)
    assert b["candidatos"] == []
    assert profile_repository.get(pid) is not None  # el Banco no se toca


# ── rutas ────────────────────────────────────────────────────────────────────

def test_routes_list_new_detail_render():
    c = _client()
    assert c.get("/cchh/busquedas").status_code == 200
    assert c.get("/cchh/busquedas/nueva").status_code == 200
    b = human_capital_service.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"})
    r = c.get(f"/cchh/busquedas/{b['busqueda_id']}")
    assert r.status_code == 200 and "Dev" in r.text


def test_route_create_redirects_to_detail():
    c = _client()
    r = c.post("/cchh/busquedas/nueva",
               data={"nombre_puesto": "Nuevo Puesto", "fecha_apertura": "2026-09-01"},
               follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/cchh/busquedas/")


def test_route_move_stage_hx_returns_fragment_not_full_page():
    c = _client()
    pid = _profile()
    b = human_capital_service.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"})
    b = human_capital_service.add_candidate_to_busqueda(b["busqueda_id"], pid)
    aid = b["candidatos"][0]["application_id"]
    r = c.post(f"/cchh/busquedas/{b['busqueda_id']}/candidatos/{aid}/etapa",
               data={"etapa": "Entrevista CCHH"}, headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "app-shell" not in r.text and "<!doctype" not in r.text.lower()  # fragmento
    assert human_capital_service.get_busqueda(b["busqueda_id"])["candidatos"][0]["etapa"] == "Entrevista CCHH"


def test_route_add_candidate_and_close():
    c = _client()
    pid = _profile()
    b = human_capital_service.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"})
    bid = b["busqueda_id"]
    r = c.post(f"/cchh/busquedas/{bid}/candidatos", data={"profile_id": pid}, headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert len(human_capital_service.get_busqueda(bid)["candidatos"]) == 1
    r = c.post(f"/cchh/busquedas/{bid}/cerrar", follow_redirects=False)
    assert r.status_code == 303
    assert human_capital_service.get_busqueda(bid)["estado"] == "cerrado"


def test_detail_404_for_unknown():
    assert _client().get("/cchh/busquedas/does-not-exist").status_code == 404


def test_routes_require_authorization():
    session = auth_service.create_session({"email": "x@danwor.com", "name": "X", "first_name": "X", "family_name": "Y"})
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    assert c.get("/cchh/busquedas").status_code == 403
