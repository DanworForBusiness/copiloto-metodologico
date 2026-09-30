"""test_human_capital_busquedas.py — Búsquedas (procesos de selección): modelo +
service + rutas del lifecycle transaccional (alta/edición/detalle/pipeline/
cierre). La relación con candidatos es por `profile_id` (Banco = maestro), y la
semántica de dominio (estado activo/cerrado; 5 etapas; resultado sólo en
Finalizado; historial de transiciones) es la de la app de referencia. MISMO
patrón de aislamiento/durabilidad-en-test que el resto de CCHH."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.routers.human_capital import _fecha_dmy
from webapp.services import auth_service, busqueda_repository, human_capital_service, profile_repository
from webapp.services import human_capital_catalog_repository as catalog_repo

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


# ── owner visual/UX pass: fechas · terminología/contadores · vistas · catálogos ──

def test_fecha_dmy_presents_iso_as_ddmmyyyy():
    # SÓLO presentación: la persistencia sigue en ISO. Fecha pura, datetime ISO,
    # vacío y basura (passthrough sin romper el render).
    assert _fecha_dmy("2026-09-10") == "10-09-2026"
    assert _fecha_dmy("2026-09-10T14:30:00") == "10-09-2026"
    assert _fecha_dmy("") == ""
    assert _fecha_dmy(None) == ""
    assert _fecha_dmy("no es fecha") == "no es fecha"


def test_list_uses_proceso_terminology_full_counts_and_ddmmyyyy_dates():
    c = _client()
    human_capital_service.create_busqueda({"nombre_puesto": "P1", "fecha_apertura": "2026-09-10"})
    html = c.get("/cchh/busquedas").text
    # Terminología "proceso" en acciones y headers (owner): nunca "Nueva búsqueda"
    # ni títulos de sección "Activas"/"Cerradas".
    assert "+ Nuevo proceso" in html and "+ Nuevo candidato" in html
    assert "Nueva búsqueda" not in html
    assert ">Procesos activos<" in html and ">Procesos cerrados<" in html
    assert ">Activas<" not in html and ">Cerradas<" not in html
    # CTA primario de ESTA sección = "+ Nuevo proceso" (owner): botón gold/primario.
    assert 'class="btn-primary">+ Nuevo proceso' in html
    assert 'class="btn-secondary">+ Nuevo candidato' in html
    # Contador = "N procesos" (el título ya dice activos/cerrados; no se repite).
    assert "1 proceso</span>" in html
    assert "1 proceso activo" not in html
    # Fecha visible en DD-MM-YYYY, nunca ISO crudo.
    assert "10-09-2026" in html and "Desde 2026-09-10" not in html


def test_detail_view_switch_has_pipeline_default_and_pending_disabled():
    c = _client()
    b = human_capital_service.create_busqueda({"nombre_puesto": "P", "fecha_apertura": "2026-09-01"})
    html = c.get(f"/cchh/busquedas/{b['busqueda_id']}").text
    # Botonera de vistas con el primitivo compartido; Pipeline activa por defecto.
    assert "dashboard-view-switch" in html
    assert "Vista pipeline" in html and "is-active" in html
    # Las diferidas EXISTEN visualmente pero deshabilitadas (no ausentes).
    for pending in ("Vista evaluación", "Scoring (JD)", "Sugeridos del banco", "Reportes"):
        assert pending in html
    assert "disabled" in html


def test_pipeline_empty_columns_render_stage_empty_state():
    c = _client()
    b = human_capital_service.create_busqueda({"nombre_puesto": "P", "fecha_apertura": "2026-09-01"})
    html = c.get(f"/cchh/busquedas/{b['busqueda_id']}").text
    # Cada etapa vacía muestra su empty-state propio DENTRO de la columna.
    assert "busqueda-col__empty" in html
    assert "entrevistas de líder realizadas ni programadas" in html
    assert "candidatos finalizados" in html


def test_route_remove_candidate_end_to_end_keeps_profile_and_nm_contract():
    # Contrato de "Quitar" (owner): quita la CandidateApplication de ESTE proceso,
    # NO borra el Profile, NO afecta otras aplicaciones del mismo Profile, y el
    # candidato vuelve a estar disponible para volver a agregarlo acá.
    c = _client()
    pid = _profile()
    a = human_capital_service.create_busqueda({"nombre_puesto": "A", "fecha_apertura": "2026-09-01"})
    b = human_capital_service.create_busqueda({"nombre_puesto": "B", "fecha_apertura": "2026-09-01"})
    human_capital_service.add_candidate_to_busqueda(a["busqueda_id"], pid)
    human_capital_service.add_candidate_to_busqueda(b["busqueda_id"], pid)  # también en B (N:M)
    aid = human_capital_service.get_busqueda(a["busqueda_id"])["candidatos"][0]["application_id"]
    # Aparece en Preselección de A antes de quitar.
    assert human_capital_service.get_busqueda(a["busqueda_id"])["candidatos"][0]["etapa"] == "Preselección"
    # Quitar vía la ruta real (fragmento HX), MISMO endpoint que el botón trash.
    r = c.post(f"/cchh/busquedas/{a['busqueda_id']}/candidatos/{aid}/eliminar", headers={"HX-Request": "true"})
    assert r.status_code == 200
    a2 = human_capital_service.get_busqueda(a["busqueda_id"])
    assert a2["candidatos"] == []                                   # fuera de A (contador baja)
    assert profile_repository.get(pid) is not None                  # Profile intacto en el Banco
    assert len(human_capital_service.get_busqueda(b["busqueda_id"])["candidatos"]) == 1  # B intacto
    # Vuelve a estar disponible para agregarlo de nuevo a A.
    assert pid in {p["profile_id"] for p in human_capital_service.profiles_available_for_busqueda(a2)}


def test_detail_remove_button_declares_confirmed_trigger():
    # El bug era el botón trash SIN `hx-trigger="confirmed"`: con `data-dw-confirm`,
    # el delegado hace preventDefault y sólo re-dispara vía el evento `confirmed`.
    c = _client()
    pid = _profile()
    b = human_capital_service.create_busqueda({"nombre_puesto": "D", "fecha_apertura": "2026-09-01"})
    human_capital_service.add_candidate_to_busqueda(b["busqueda_id"], pid)
    html = c.get(f"/cchh/busquedas/{b['busqueda_id']}").text
    assert '/eliminar' in html and 'hx-trigger="confirmed"' in html and 'data-dw-confirm' in html


def test_empty_preseleccion_shows_add_action_only_when_active():
    # "+ Agregar candidato" contextual SÓLO en la etapa inicial (Preselección) de
    # un proceso activo; ni en otras etapas ni en un proceso cerrado.
    c = _client()
    b = human_capital_service.create_busqueda({"nombre_puesto": "D", "fecha_apertura": "2026-09-01"})
    html = c.get(f"/cchh/busquedas/{b['busqueda_id']}").text
    assert html.count("busqueda-col__empty-add") == 1  # sólo Preselección
    human_capital_service.close_busqueda(b["busqueda_id"])
    html2 = c.get(f"/cchh/busquedas/{b['busqueda_id']}").text
    assert "busqueda-col__empty-add" not in html2       # cerrado: sin acción de alta


def test_busqueda_attention_estancada_and_sin_requerimiento():
    old = (datetime.now() - timedelta(days=40)).date().isoformat()
    recent = datetime.now().date().isoformat()
    b_old = human_capital_service.create_busqueda({"nombre_puesto": "Old", "fecha_apertura": old})
    att = human_capital_service.busqueda_attention(human_capital_service.get_busqueda(b_old["busqueda_id"]))
    assert att["estancada"] is True and att["dias_sin_movimiento"] >= 40
    assert att["sin_requerimiento"] is True  # pedido_formal_recibido default False
    b_new = human_capital_service.create_busqueda(
        {"nombre_puesto": "New", "fecha_apertura": recent, "pedido_formal_recibido": "on"})
    att2 = human_capital_service.busqueda_attention(human_capital_service.get_busqueda(b_new["busqueda_id"]))
    assert att2["estancada"] is False and att2["sin_requerimiento"] is False


def test_route_atencion_filter_returns_active_panel_fragment():
    c = _client()
    old = (datetime.now() - timedelta(days=40)).date().isoformat()
    human_capital_service.create_busqueda({"nombre_puesto": "Estanc", "fecha_apertura": old})
    human_capital_service.create_busqueda(
        {"nombre_puesto": "Fresh", "fecha_apertura": datetime.now().date().isoformat(), "pedido_formal_recibido": "on"})
    r = c.get("/cchh/busquedas?atencion=estancadas", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "busquedas-activas-panel" in r.text
    assert "Estanc" in r.text and "Fresh" not in r.text
    assert "app-shell" not in r.text  # fragmento, no la página completa


def _seed_catalog(**cats):
    """Escribe un snapshot de catálogo (MISMO patrón que las suites de gobernanza
    de Seniority/Fuente) para probar el enforcement de área/área-consultora."""
    snap = catalog_repo.build_snapshot(
        {name: [{"valor": v, "orden": i + 1} for i, v in enumerate(vals)] for name, vals in cats.items()},
        refreshed_by="test", source={"drive_id": "d", "item_id": "i"},
    )
    p = catalog_repo.snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(catalog_repo._serialize(snap))


def test_area_and_area_consultora_are_catalog_governed():
    _seed_catalog(area=["Datos & BI", "IT"], area_consultora=["SAP", "Cloud"])
    # Valores del catálogo activo -> aceptados.
    b = human_capital_service.create_busqueda({
        "nombre_puesto": "D", "fecha_apertura": "2026-09-01",
        "area_departamento": "Datos & BI", "area_consultora": "SAP"})
    assert b["area_departamento"] == "Datos & BI" and b["area_consultora"] == "SAP"
    # Fuera del catálogo -> rechazado (sin fallback a texto libre).
    with pytest.raises(ValueError):
        human_capital_service.create_busqueda({
            "nombre_puesto": "D", "fecha_apertura": "2026-09-01", "area_departamento": "Marketing"})
    with pytest.raises(ValueError):
        human_capital_service.create_busqueda({
            "nombre_puesto": "D", "fecha_apertura": "2026-09-01", "area_consultora": "Fantasía"})


def test_area_edit_preserves_legacy_value_out_of_catalog():
    # Alta con catálogo amplio, luego el catálogo se "achica" y el valor queda
    # legacy: una edición NO relacionada debe poder guardarse conservándolo.
    _seed_catalog(area=["Datos & BI", "Legacy Área"], area_consultora=["SAP"])
    b = human_capital_service.create_busqueda({
        "nombre_puesto": "D", "fecha_apertura": "2026-09-01", "area_departamento": "Legacy Área"})
    _seed_catalog(area=["Datos & BI"], area_consultora=["SAP"])  # "Legacy Área" ya no está
    updated = human_capital_service.update_busqueda(
        b["busqueda_id"], b["revision"],
        {"nombre_puesto": "D2", "fecha_apertura": "2026-09-01", "area_departamento": "Legacy Área"})
    assert updated["nombre_puesto"] == "D2"
    assert updated["area_departamento"] == "Legacy Área"  # legacy preservado
