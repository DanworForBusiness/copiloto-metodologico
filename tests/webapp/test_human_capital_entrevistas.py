"""test_human_capital_entrevistas.py — Entrevistas: modelo + service + rutas.

Dominio verbatim de la app de referencia (app-seleccion,
supabase/schema.sql tabla `entrevistas` + lib/entrevistaAutomatica.ts + lib/
actions/candidatos.ts::actualizarEtapaPipeline — ver
webapp/services/entrevista_repository.py module docstring para las citas
exactas): registro FLAT (único campo de "estado" real es `concretada`), se
autocompleta UNA VEZ por (profile_id, busqueda_id) al mover una postulación a
"Entrevista CCHH"/"Entrevista Líder"/"Decisión Final", o se carga manualmente
para un candidato del Banco sin ninguna Búsqueda todavía. `origen` se deriva
siempre del Profile (`source`, catálogo `fuente`) — no se duplica como campo
propio de la entrevista. MISMO patrón de aislamiento/durabilidad-en-test que
el resto de CCHH."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import (
    auth_service, busqueda_repository, entrevista_repository, human_capital_service, profile_repository,
)
from webapp.services import human_capital_catalog_repository as catalog_repo

_AUTHORIZED_EMAIL = "hc-entrevistas-test@danwor.com"


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(entrevista_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(entrevista_repository.remote_store, "delete_path", lambda path: True)
    monkeypatch.setattr(entrevista_repository.remote_store, "enabled", lambda: False)
    monkeypatch.setattr(busqueda_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(busqueda_repository.remote_store, "enabled", lambda: False)
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)


def _client() -> TestClient:
    session = auth_service.create_session(
        {"email": _AUTHORIZED_EMAIL, "name": "E T", "first_name": "E", "family_name": "T"})
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


def _profile(**over) -> dict:
    base = {"first_name": "Cand", "last_name": "Idato"}
    base.update(over)
    return human_capital_service.create_profile(base)


def _seed_area_consultora(*values):
    snap = catalog_repo.build_snapshot(
        {"area_consultora": [{"valor": v, "orden": i + 1} for i, v in enumerate(values)]},
        refreshed_by="test", source={"drive_id": "d", "item_id": "i"})
    p = catalog_repo.snapshot_path(); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(catalog_repo._serialize(snap))


def _seed_modalidad_contratacion(*values):
    snap = catalog_repo.build_snapshot(
        {"modalidad_contratacion": [{"valor": v, "orden": i + 1} for i, v in enumerate(values)]},
        refreshed_by="test", source={"drive_id": "d", "item_id": "i"})
    p = catalog_repo.snapshot_path(); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(catalog_repo._serialize(snap))


# ── modelo / service ─────────────────────────────────────────────────────────

def test_create_manual_defaults_to_concretada_true_no_busqueda():
    profile = _profile()
    e = human_capital_service.create_entrevista_manual(profile["profile_id"], {})
    assert e["concretada"] is True
    assert e["busqueda_id"] is None
    assert e["application_id"] is None
    assert e["profile_id"] == profile["profile_id"]
    assert e["revision"] == 1


def test_create_manual_rejects_missing_or_deleted_profile():
    with pytest.raises(ValueError):
        human_capital_service.create_entrevista_manual("no-existe", {})
    profile = _profile()
    human_capital_service.soft_delete_profile(profile["profile_id"])
    with pytest.raises(ValueError):
        human_capital_service.create_entrevista_manual(profile["profile_id"], {})


def test_update_preserves_legacy_area_and_rejects_new_out_of_catalog_value():
    _seed_area_consultora("Consultoria", "RRHH")
    profile = _profile()
    e = human_capital_service.create_entrevista_manual(profile["profile_id"], {"area": "Consultoria"})
    # Legacy: el catálogo cambia y ya no incluye "Consultoria" -- pero como no
    # se está tocando el campo, se preserva (MISMO contrato que Seniority/Fuente).
    _seed_area_consultora("RRHH", "Finanzas")
    updated = human_capital_service.update_entrevista(
        e["entrevista_id"], e["revision"], {"area": "Consultoria", "observaciones": "x"})
    assert updated["area"] == "Consultoria"
    assert updated["observaciones"] == "x"
    # Si en cambio se intenta CAMBIAR a otro valor fuera de catálogo, se rechaza.
    with pytest.raises(ValueError):
        human_capital_service.update_entrevista(
            updated["entrevista_id"], updated["revision"], {"area": "Otra Cosa Inventada"})


def test_modalidad_contratacion_governed_rejects_invalid_value():
    _seed_modalidad_contratacion("Full time", "Part time")
    profile = _profile()
    with pytest.raises(ValueError):
        human_capital_service.create_entrevista_manual(profile["profile_id"], {"modalidad_contratacion": "Freelance"})
    e = human_capital_service.create_entrevista_manual(profile["profile_id"], {"modalidad_contratacion": "Full time"})
    assert e["modalidad_contratacion"] == "Full time"


def test_marcar_no_concretada_flips_the_only_state_field():
    profile = _profile()
    e = human_capital_service.create_entrevista_manual(profile["profile_id"], {})
    assert e["concretada"] is True
    updated = human_capital_service.marcar_entrevista_no_concretada(e["entrevista_id"], e["revision"])
    assert updated["concretada"] is False


def test_delete_removes_the_entrevista():
    profile = _profile()
    e = human_capital_service.create_entrevista_manual(profile["profile_id"], {})
    human_capital_service.delete_entrevista(e["entrevista_id"])
    assert human_capital_service.get_entrevista(e["entrevista_id"]) is None


def test_delete_missing_raises_not_found():
    with pytest.raises(entrevista_repository.EntrevistaNotFoundError):
        human_capital_service.delete_entrevista("no-existe")


def test_entrevista_view_derives_origen_from_profile_source_never_duplicated():
    _seed_area_consultora("RRHH")
    profile = human_capital_service.create_profile({"first_name": "A", "last_name": "B", "source": None})
    # El Profile de este fixture no tiene catálogo de `fuente` poblado, así que
    # alcanza con verificar que `origen` sigue la fuente actual del Profile
    # (incluida cuando cambia DESPUÉS de creada la entrevista -- nunca se
    # congela un valor propio en la entrevista).
    e = human_capital_service.create_entrevista_manual(profile["profile_id"], {})
    view = human_capital_service.entrevista_view(e)
    assert view["origen"] == profile.get("source")
    assert "puesto_mostrado" in view and view["puesto_es_derivado"] is False


# ── auto-creación al mover etapa (integración con Búsquedas) ────────────────

def _busqueda_with_applicant(profile_id: str, **over) -> tuple[dict, str]:
    base = {"nombre_puesto": "Consultor SAP", "fecha_apertura": "2026-01-01"}
    base.update(over)
    b = human_capital_service.create_busqueda(base)
    b = human_capital_service.add_candidate_to_busqueda(b["busqueda_id"], profile_id)
    app_id = b["candidatos"][0]["application_id"]
    return b, app_id


@pytest.mark.parametrize("etapa", ["Entrevista CCHH", "Entrevista Líder", "Decisión Final"])
def test_moving_to_an_interview_stage_creates_the_entrevista_once(etapa):
    profile = _profile()
    b, app_id = _busqueda_with_applicant(profile["profile_id"])
    assert entrevista_repository.find_by_profile_and_busqueda(profile["profile_id"], b["busqueda_id"]) is None
    resultado = "Ingreso" if etapa == "Decisión Final" else None
    # Decisión Final no es Finalizado -- resultado_final no aplica ahí, pero el
    # repo lo fuerza a None salvo en Finalizado; pasamos None siempre salvo que
    # se quiera ejercitar esa rama explícitamente en otro test.
    human_capital_service.move_application_stage(b["busqueda_id"], app_id, etapa, None)
    created = entrevista_repository.find_by_profile_and_busqueda(profile["profile_id"], b["busqueda_id"])
    assert created is not None
    assert created["puesto_evaluado"] == "Consultor SAP"
    assert created["application_id"] == app_id


def test_moving_between_interview_stages_never_duplicates_the_row():
    profile = _profile()
    b, app_id = _busqueda_with_applicant(profile["profile_id"])
    human_capital_service.move_application_stage(b["busqueda_id"], app_id, "Entrevista CCHH", None)
    human_capital_service.move_application_stage(b["busqueda_id"], app_id, "Entrevista Líder", None)
    human_capital_service.move_application_stage(b["busqueda_id"], app_id, "Decisión Final", None)
    matching = [e for e in human_capital_service.list_entrevistas() if e.get("busqueda_id") == b["busqueda_id"]]
    assert len(matching) == 1


def test_moving_to_preseleccion_alone_does_not_create_an_entrevista():
    # Preselección es la etapa inicial (ya la tiene al agregar el candidato) --
    # no dispara autocreación (MISMO gate que la referencia: sólo CCHH/Líder/
    # Decisión Final "implican que hubo entrevista").
    profile = _profile()
    b, app_id = _busqueda_with_applicant(profile["profile_id"])
    human_capital_service.move_application_stage(b["busqueda_id"], app_id, "Preselección", None)
    assert entrevista_repository.find_by_profile_and_busqueda(profile["profile_id"], b["busqueda_id"]) is None


def test_area_of_auto_created_entrevista_derives_from_busqueda_area_consultora():
    _seed_area_consultora("RRHH", "Comercial")
    profile = _profile()
    b, app_id = _busqueda_with_applicant(profile["profile_id"])
    human_capital_service.update_busqueda(b["busqueda_id"], b["revision"], {
        "nombre_puesto": b["nombre_puesto"], "fecha_apertura": b["fecha_apertura"], "area_consultora": "RRHH",
    })
    human_capital_service.move_application_stage(b["busqueda_id"], app_id, "Entrevista CCHH", None)
    created = entrevista_repository.find_by_profile_and_busqueda(profile["profile_id"], b["busqueda_id"])
    assert created["area"] == "RRHH"


# ── rutas ────────────────────────────────────────────────────────────────────

def test_unauthenticated_redirects():
    c = TestClient(app)
    assert c.get("/cchh/entrevistas", follow_redirects=False).status_code in (302, 303, 307)


def test_list_page_renders_empty_state():
    r = _client().get("/cchh/entrevistas")
    assert r.status_code == 200
    assert "Sin entrevistas" in r.text


def test_new_page_lists_available_profiles():
    profile = _profile(first_name="Marta", last_name="Diaz")
    r = _client().get("/cchh/entrevistas/nueva")
    assert r.status_code == 200
    assert "Marta Diaz" in r.text


def test_create_submit_redirects_straight_to_edit_no_detail_page():
    # Owner UX pass 1: sin vista de detalle standalone -- después de crear,
    # se sigue directo a Editar (no a una página de sólo lectura).
    profile = _profile(first_name="Marta", last_name="Diaz")
    client = _client()
    r = client.post("/cchh/entrevistas/nueva", data={
        "profile_id": profile["profile_id"], "puesto_evaluado": "Data Engineer",
    }, follow_redirects=False)
    assert r.status_code == 303
    redirect_url = r.headers["location"]
    assert redirect_url.endswith("/editar")
    edit_page = client.get(redirect_url)
    assert edit_page.status_code == 200
    assert "Marta Diaz" in edit_page.text
    assert "Data Engineer" in edit_page.text
    listing = client.get("/cchh/entrevistas")
    assert "Marta Diaz" in listing.text
    assert "1 entrevista" in listing.text


def test_create_submit_without_profile_id_shows_validation_error():
    r = _client().post("/cchh/entrevistas/nueva", data={})
    assert r.status_code == 200
    assert "No se guardó" in r.text


def test_standalone_detail_route_does_not_exist():
    # Owner UX pass 1: "si no tiene consumidores reales, eliminar esa
    # superficie" -- GET /cchh/entrevistas/{id} (sin /editar) ya no es una
    # ruta registrada en absoluto, para NINGÚN id (no sólo uno inexistente).
    profile = _profile()
    e = human_capital_service.create_entrevista_manual(profile["profile_id"], {})
    r = _client().get(f"/cchh/entrevistas/{e['entrevista_id']}")
    assert r.status_code == 404
    r_missing = _client().get("/cchh/entrevistas/no-existe")
    assert r_missing.status_code == 404


def test_edit_submit_updates_fields_and_redirects_to_list():
    profile = _profile()
    e = human_capital_service.create_entrevista_manual(profile["profile_id"], {})
    client = _client()
    r = client.post(f"/cchh/entrevistas/{e['entrevista_id']}/editar", data={
        "observaciones": "Buena impresión", "expected_revision": str(e["revision"]),
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cchh/entrevistas"
    updated = human_capital_service.get_entrevista(e["entrevista_id"])
    assert updated["observaciones"] == "Buena impresión"


def test_edit_submit_stale_revision_shows_concurrent_modification():
    profile = _profile()
    e = human_capital_service.create_entrevista_manual(profile["profile_id"], {})
    r = _client().post(f"/cchh/entrevistas/{e['entrevista_id']}/editar", data={
        "expected_revision": str(e["revision"] + 1),
    })
    assert r.status_code == 200
    assert "cambió desde que abriste el formulario" in r.text


def test_no_concretada_route_saves_fields_and_flips_state():
    profile = _profile()
    e = human_capital_service.create_entrevista_manual(profile["profile_id"], {})
    client = _client()
    r = client.post(f"/cchh/entrevistas/{e['entrevista_id']}/no-concretada", data={
        "observaciones": "No se presentó", "expected_revision": str(e["revision"]),
    }, follow_redirects=False)
    assert r.status_code == 303
    updated = human_capital_service.get_entrevista(e["entrevista_id"])
    assert updated["concretada"] is False
    assert updated["observaciones"] == "No se presentó"


def test_delete_route_removes_it_and_redirects_to_list():
    profile = _profile()
    e = human_capital_service.create_entrevista_manual(profile["profile_id"], {})
    client = _client()
    r = client.post(f"/cchh/entrevistas/{e['entrevista_id']}/eliminar", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cchh/entrevistas"
    assert human_capital_service.get_entrevista(e["entrevista_id"]) is None


# ── UX pass 1 (owner): tabla = primitive compartido, sólo Editar/Eliminar,
# disclaimer homologado, form actions homologadas ─────────────────────────────

def test_list_table_opts_into_the_shared_draggable_resizable_columns_primitive():
    # MISMO mecanismo que Candidatos (human_capital_profiles_panel.html):
    # data-draggable-columns-key + data-resizable-columns-key en el wrapper,
    # data-column-key en cada <col>/<th> reordenable -- nunca una variante
    # `.hc-entrevistas-*` del mecanismo.
    profile = _profile()
    human_capital_service.create_entrevista_manual(profile["profile_id"], {})
    html = _client().get("/cchh/entrevistas").text
    assert 'data-draggable-columns-key="cchh-entrevistas"' in html
    assert 'data-resizable-columns-key="cchh-entrevistas"' in html
    for key in ("candidato", "fecha", "origen", "puesto", "area", "modalidad", "remuneracion"):
        assert f'data-column-key="{key}"' in html


def test_list_actions_are_only_editar_and_eliminar_no_ver():
    profile = _profile()
    e = human_capital_service.create_entrevista_manual(profile["profile_id"], {})
    html = _client().get("/cchh/entrevistas").text
    assert f'href="/cchh/entrevistas/{e["entrevista_id"]}/editar"' in html
    assert f'action="/cchh/entrevistas/{e["entrevista_id"]}/eliminar"' in html
    assert "Ver entrevista" not in html
    assert f'href="/cchh/entrevistas/{e["entrevista_id"]}"' not in html


def test_edit_disclaimer_uses_the_shared_info_tooltip_not_a_bespoke_banner():
    profile = _profile()
    b, app_id = _busqueda_with_applicant(profile["profile_id"])
    human_capital_service.move_application_stage(b["busqueda_id"], app_id, "Entrevista CCHH", None)
    e = entrevista_repository.find_by_profile_and_busqueda(profile["profile_id"], b["busqueda_id"])
    html = _client().get(f"/cchh/entrevistas/{e['entrevista_id']}/editar").text
    # MISMO primitive exacto que "Requerimiento formal recibido" en
    # busqueda_form.html -- nunca un .callout/banner propio de Entrevistas.
    assert 'class="activity-kpi__info"' in html
    assert 'class="chart-tooltip"' in html
    assert "callout" not in html
    assert "Pipeline de la Búsqueda" in html
    # Una entrevista manual (sin busqueda_id) no necesita el disclaimer.
    manual = human_capital_service.create_entrevista_manual(profile["profile_id"], {})
    manual_html = _client().get(f"/cchh/entrevistas/{manual['entrevista_id']}/editar").text
    assert 'class="activity-kpi__info"' not in manual_html


def test_edit_form_has_only_guardar_no_cancelar():
    profile = _profile()
    e = human_capital_service.create_entrevista_manual(profile["profile_id"], {})
    html = _client().get(f"/cchh/entrevistas/{e['entrevista_id']}/editar").text
    assert ">Guardar cambios<" in html
    # No confundir con el "Cancelar" del diálogo de confirmación compartido
    # (data-dw-confirm-cancel, ajeno al form de edición): lo que se prohíbe
    # es el link `.btn-secondary` de "cancelar edición" que existía antes.
    assert '<a href="/cchh/entrevistas" class="btn-secondary">Cancelar</a>' not in html


def test_create_form_has_crear_and_cancelar():
    html = _client().get("/cchh/entrevistas/nueva").text
    assert ">Crear entrevista<" in html
    assert ">Cancelar<" in html
    assert 'href="/cchh/entrevistas"' in html


def test_edit_and_create_use_the_same_shared_back_primitive():
    profile = _profile()
    e = human_capital_service.create_entrevista_manual(profile["profile_id"], {})
    for url in ("/cchh/entrevistas/nueva", f"/cchh/entrevistas/{e['entrevista_id']}/editar"):
        html = _client().get(url).text
        assert 'class="catalog-back" href="/cchh/entrevistas"' in html
        assert "Volver a Entrevistas" in html


def test_nav_shows_entrevistas_entry():
    r = _client().get("/cchh/entrevistas")
    assert 'href="/cchh/entrevistas"' in r.text
    assert "Entrevistas" in r.text
