"""I2 — Operaciones → Búsquedas (REG-CH-01): rutas, autorización, formulario, listado, detalle y gestión de solicitudes.

La persistencia es SIEMPRE la de I1 (`human_capital_service` / `busqueda_repository`): estos tests verifican que las rutas la usan y no agregan otra. Aislamiento: `tmp_path` + `remote_store` falso en memoria
(fixtures reutilizadas de I1). Sin Spaces, SharePoint, Entra ni DigitalOcean."""
from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.services import auth_service, busqueda_repository as repo, human_capital_service as svc, profile_repository
from tests.webapp.test_busqueda_solicitud_i1 import (  # noqa: F401  (fixtures y helpers reutilizados de I1)
    CCHH_EMAIL, OPS_A, OPS_B, SID, SID2, FakeRemote, doc, env, files, remote, req, rev, seed_catalog, send, to_state,
)

ORIGIN = {"Origin": "http://testserver"}
BASE = "/operaciones/busquedas"
OUTSIDER = "outsider@danwor.com"
SID3 = "0b6c1d2e-7a44-4f0e-8c35-9d2f5e1a7b21"


@pytest.fixture(autouse=True)
def operations_users(monkeypatch):
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", f"{OPS_A},{OPS_B}")


def session_client(email: str) -> TestClient:
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T U", "first_name": "T", "family_name": "U"}))
    return c


def ops(email: str = OPS_A) -> TestClient:
    return session_client(email)


def form_payload(sid: str | None = SID, **over) -> dict:
    """Cuerpo de formulario HTML equivalente a `req()` (seniority multi-valor)."""
    body = {k: ("" if v is None else v) for k, v in req(**over).items()}
    if sid is not None:
        body["submission_id"] = sid
    return body


def post_new(client: TestClient, sid: str | None = SID, headers=ORIGIN, **over):
    return client.post(f"{BASE}/nueva", data=form_payload(sid, **over), headers=headers)


def text(resp) -> str:
    return resp.text


# ═══════════════════════ navegación ═══════════════════════
def test_nav_has_busquedas_as_02_right_below_asignaciones_and_reporting_and_metrics_shift_down(monkeypatch):
    from webapp import hub_metrics_access
    monkeypatch.setattr(hub_metrics_access, "is_vertical_metrics_enabled", lambda vertical: True)
    monkeypatch.setattr(hub_metrics_access, "can_access_hub_metrics", lambda user: True)
    html = text(ops().get(BASE))
    order = [html.index(x) for x in ("<strong>Asignaciones</strong>", "<strong>Búsquedas</strong>", "<strong>Reporting</strong>", "<strong>Métricas de uso</strong>")]
    assert order == sorted(order)
    assert re.search(r"<span>02</span><strong>Búsquedas</strong>", html) and re.search(r"<span>03</span><strong>Reporting</strong>", html) and re.search(r"<span>04</span><strong>Métricas de uso</strong>", html)
    assert re.search(r'href="/operaciones/busquedas" class="module-link is-active"', html)


def test_nav_is_present_and_not_active_in_the_other_operaciones_pages():
    html = text(ops().get("/operaciones/reporting/integral"))
    assert re.search(r'href="/operaciones/busquedas" class="module-link\s*"', html) and "<strong>Búsquedas</strong>" in html
    assert re.search(r'href="/operaciones/reporting/integral" class="module-link is-active"', html) and html.count("module-link is-active") == 1


def test_metrics_link_is_absent_without_hub_metrics_access(monkeypatch):
    from webapp import hub_metrics_access
    monkeypatch.setattr(hub_metrics_access, "can_access_hub_metrics", lambda user: False)
    html = text(ops().get(BASE))
    assert "Métricas de uso" not in html and "<span>02</span><strong>Búsquedas</strong>" in html


def test_the_script_is_served_and_the_stylesheet_is_only_the_minimal_hub_adjustment():
    assert ops().get("/static/operations_busquedas.js").status_code == 200
    css = ops().get("/static/operations_busquedas.css")
    assert css.status_code == 200
    declared = " ".join(re.findall(r"\{([^}]*)\}", re.sub(r"/\*.*?\*/", "", css.text, flags=re.S)))        # solo las declaraciones
    assert "font-family" not in declared and "#" not in declared and "rgb" not in declared and "background" not in declared   # sin tipografía ni colores propios (solo tokens del Hub)     # solo cabecera estándar de Operaciones + respiro post-divisor


@pytest.mark.parametrize("path", [BASE, f"{BASE}/nueva", f"{BASE}/{SID}", f"{BASE}/{SID}/editar"])
def test_screens_use_the_hub_primitives_and_no_custom_styles_or_typography(path):
    send()
    html = ops().get(path).text
    assert "human_capital.css" in html                                                       # mismas hojas que CCHH
    assert not re.search(r'class="[^"]*\bopsb-(?!hero|checks)', html)                              # ninguna clase de estilo propia salvo la cabecera estándar de Operaciones
    assert "font-family" not in html and "font-size" not in html and "font:" not in html     # ni tipografía propia inline
    if path == f"{BASE}/{SID}":                                                               # detalle = MISMA composición que el detalle de un proceso de CCHH (hero + fila de estado + acciones a la derecha)
        assert 'class="module-hero hc-profiles-hero"' in html and "busqueda-status-row" in html and "hc-profiles-hero-actions" in html and "status-badge" in html
    else:
        assert re.search(r'class="module-hero [^"]*opsb-hero"', html) and ("hero-lead full-page-subtitle" in html or "catalog-verdict-line" in html)


# ═══════════════════════ autorización server-side en TODAS las rutas ═══════════════════════
def _routes(sid: str):
    return [
        ("get", BASE), ("get", f"{BASE}/nueva"), ("get", f"{BASE}/{sid}"), ("get", f"{BASE}/{sid}/editar"),
        ("post", f"{BASE}/nueva"), ("post", f"{BASE}/{sid}/editar"), ("post", f"{BASE}/{sid}/cancelar"),
    ]


def _call(client: TestClient, method: str, path: str, headers=ORIGIN):
    if method == "get":
        return client.get(path)
    return client.post(path, data={"expected_revision": "1", **form_payload(SID)}, headers=headers)


def test_without_session_reads_redirect_to_login_and_writes_are_401_and_nothing_is_created():
    send()
    anon = TestClient(app, follow_redirects=False)
    for method, path in _routes(SID):
        r = _call(anon, method, path)
        assert (r.status_code == 303 or r.status_code == 307 or r.status_code == 302) if method == "get" else r.status_code == 401, (method, path, r.status_code)
    assert repo.get(SID)["solicitud"]["estado"] == "enviada" and len(files()) == 1


@pytest.mark.parametrize("email", [CCHH_EMAIL, OUTSIDER])
def test_authenticated_users_outside_operaciones_get_403_everywhere_without_side_effects(email):
    send()
    before = repo.get(SID)
    c = session_client(email)
    for method, path in _routes(SID):
        assert _call(c, method, path).status_code == 403, (method, path)
    assert repo.get(SID) == before and len(files()) == 1


def test_any_authorized_operaciones_user_can_read_create_and_manage():
    assert ops(OPS_A).get(BASE).status_code == 200 and ops(OPS_B).get(f"{BASE}/nueva").status_code == 200
    assert post_new(ops(OPS_A)).status_code == 303
    assert ops(OPS_B).get(f"{BASE}/{SID}").status_code == 200       # lo ve otro usuario de Operaciones
    assert ops(OPS_B).get(f"{BASE}/{SID}/editar").status_code == 200


def test_the_cchh_user_cannot_use_the_operaciones_surface_and_an_operaciones_user_cannot_enter_cchh():
    assert session_client(CCHH_EMAIL).get(BASE).status_code == 403
    assert ops(OPS_A).get("/cchh/busquedas").status_code == 403
    assert session_client(CCHH_EMAIL).get("/cchh/busquedas").status_code == 200   # CCHH sigue funcionando


# ═══════════════════════ protección same-origin en las escrituras ═══════════════════════
@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.example"}, {"Referer": "https://evil.example/x"}])
def test_writes_without_same_origin_are_rejected_and_do_not_change_anything(headers):
    send()
    before = repo.get(SID)
    c = ops()
    assert post_new(c, SID2, headers=headers).status_code == 403
    assert c.post(f"{BASE}/{SID}/editar", data={"expected_revision": "1", **form_payload(SID)}, headers=headers).status_code == 403
    assert c.post(f"{BASE}/{SID}/cancelar", data={"expected_revision": "1"}, headers=headers).status_code == 403
    assert repo.get(SID) == before and len(files()) == 1


def test_referer_of_the_same_site_is_accepted_when_origin_is_absent():
    r = post_new(ops(), headers={"Referer": "http://testserver/operaciones/busquedas/nueva"})
    assert r.status_code == 303


# ═══════════════════════ formulario (GET) ═══════════════════════
def test_new_form_has_a_server_generated_submission_id_and_the_reg_ch_01_structure():
    c = ops()
    html = text(c.get(f"{BASE}/nueva"))
    ids = re.findall(r'name="submission_id" value="([0-9a-f-]{36})"', html)
    assert len(ids) == 1 and str(uuid.UUID(ids[0])) == ids[0]
    assert ids[0] != re.findall(r'name="submission_id" value="([0-9a-f-]{36})"', text(c.get(f"{BASE}/nueva")))[0]   # uno nuevo por render
    for name in ("nombre_puesto", "tipo_busqueda", "proyecto", "cantidad_recursos", "dedicacion_valor", "dedicacion_unidad", "duracion_tipo", "duracion_meses", "area_consultora",
                 "ubicacion", "seniority", "modalidad", "dias_presenciales", "lugar_trabajo", "responsabilidades", "skills_excluyentes", "habilidades_blandas", "ingles",
                 "certificaciones", "tipo_contrato", "fecha_inicio_prevista", "comentarios"):
        assert f'name="{name}"' in html, name
    assert "Enviar a Capital Humano" in html and "fecha_apertura" not in html      # la fecha de apertura la asigna el sistema
    assert "horas_por_dia" not in html and "Indistinto" in html


@pytest.mark.parametrize("mode, busy, label", [("nueva", "Enviando solicitud…", "Enviar a Capital Humano"), ("editar", "Guardando cambios…", "Guardar cambios"),
                                               ("corregir", "Reenviando solicitud…", "Corregir y reenviar a Capital Humano")])
def test_submit_buttons_use_the_hub_integrated_loading_pattern(mode, busy, label):
    if mode != "nueva":
        to_state("observada" if mode == "corregir" else "enviada")
    html = text(ops().get(f"{BASE}/nueva" if mode == "nueva" else f"{BASE}/{SID}/editar"))
    assert 'class="btn-primary asr-generate"' in html and 'class="asr-generate__busy htmx-indicator"' in html and 'class="asr-generate__spinner"' in html
    assert busy in html and f'<span class="asr-generate__label">{label}</span>' in html and "data-status" not in html and "data-busy-label" not in html


def test_the_cancel_dialog_uses_the_same_loading_pattern_and_the_script_only_toggles_the_hub_class():
    send()
    html = text(ops().get(f"{BASE}/{SID}"))
    assert "asr-generate__spinner" in html and "Cancelando solicitud…" in html
    js = ops().get("/static/operations_busquedas.js").text
    assert "htmx-request" in js and 'opsbBusy === "true"' in js and "textContent" not in js            # no pinta textos ni estilos propios; el éxito lo confirma el servidor con la redirección


def test_new_form_offers_the_governed_catalogs_only():
    html = text(ops().get(f"{BASE}/nueva"))
    assert '<option value="SAP"' in html and '<option value="Cloud"' in html and 'value="SSR"' in html and 'value="JR"' in html
    seed_catalog(area_consultora=["Datos"], seniority=["SR"])
    html = text(ops().get(f"{BASE}/nueva"))
    assert '<option value="Datos"' in html and '<option value="SAP"' not in html and 'value="JR"' not in html


def test_missing_catalogs_are_explained_instead_of_failing():
    seed_catalog(area_consultora=[], seniority=[])
    r = ops().get(f"{BASE}/nueva")
    assert r.status_code == 200 and "todavía no están disponibles" in r.text


# ═══════════════════════ alta y envío ═══════════════════════
def test_valid_creation_persists_through_i1_and_redirects_with_a_visible_confirmation():
    c = ops(OPS_A)
    r = post_new(c)
    assert r.status_code == 303 and r.headers["location"] == f"{BASE}/{SID}?ok=enviada"
    d = repo.get(SID)
    assert d["estado"] == "activo" and d["solicitud"]["estado"] == "enviada" and d["solicitud"]["submission_id"] == SID == d["busqueda_id"]
    assert d["origen"]["tipo"] == "operaciones" and d["origen"]["creado_por"] == OPS_A and d["solicitud"]["solicitante"] == OPS_A and d["pedido_formal_recibido"] is True
    assert d["requerimiento"]["nombre_puesto"] == "Consultor SAP FI" and d["requerimiento"]["seniority"] == ["SSR", "SR"]
    assert d["fecha_apertura"] == repo._business_today() and len(files()) == 1
    page = c.get(r.headers["location"])
    assert page.status_code == 200 and "Solicitud enviada a Capital Humano" in page.text and "Consultor SAP FI" in page.text


def test_the_opening_date_is_assigned_by_the_system_and_a_client_supplied_value_is_ignored():
    c = ops()
    body = form_payload(SID)
    body["fecha_apertura"] = "1999-01-01"
    body["pedido_formal_recibido"] = ""
    c.post(f"{BASE}/nueva", data=body, headers=ORIGIN)
    assert repo.get(SID)["fecha_apertura"] == repo._business_today()


def test_no_draft_is_persisted_by_visiting_or_failing_validation():
    c = ops()
    c.get(f"{BASE}/nueva")
    assert post_new(c, nombre_puesto="").status_code == 422
    assert files() == [] and svc.list_solicitudes_view() == []


@pytest.mark.parametrize("field", ["nombre_puesto", "tipo_busqueda", "cantidad_recursos", "dedicacion_valor", "dedicacion_unidad", "duracion_tipo", "area_consultora", "ubicacion",
                                   "modalidad", "responsabilidades", "skills_excluyentes", "ingles", "tipo_contrato", "fecha_inicio_prevista"])
def test_each_required_field_gets_a_specific_message_and_keeps_the_rest_of_the_form(field):
    r = post_new(ops(), **{field: ""})
    assert r.status_code == 422 and 'data-error-kind="validation"' in r.text
    assert 'value="Consultor SAP FI"' in r.text or field == "nombre_puesto"           # no se pierde lo cargado
    assert f'name="submission_id" value="{SID}"' in r.text                          # el mismo envío se reintenta con el mismo id
    assert files() == []


def test_seniority_is_required_and_multiple_values_are_kept():
    body = form_payload(SID)
    body.pop("seniority")
    assert ops().post(f"{BASE}/nueva", data=body, headers=ORIGIN).status_code == 422 and files() == []
    body["seniority"] = ["JR", "SR"]
    assert ops().post(f"{BASE}/nueva", data=body, headers=ORIGIN).status_code == 303
    assert repo.get(SID)["requerimiento"]["seniority"] == ["JR", "SR"]


def test_validation_message_names_the_problem():
    r = post_new(ops(), cantidad_recursos="0")
    assert "cantidad de recursos" in r.text.lower() and r.status_code == 422
    r = post_new(ops(), area_consultora="Fantasía")
    assert "Área" in r.text and "SAP" in r.text


def test_conditional_rules_are_enforced_on_the_server_not_only_in_the_ui():
    c = ops()
    assert post_new(c, tipo_busqueda="proyecto", proyecto="").status_code == 422                    # proyecto obligatorio si es de proyecto
    assert post_new(c, modalidad="Híbrido", dias_presenciales="0").status_code == 422
    assert post_new(c, modalidad="Híbrido", dias_presenciales="6").status_code == 422
    assert post_new(c, modalidad="Híbrido", dias_presenciales="").status_code == 422
    assert post_new(c, modalidad="Híbrido", lugar_trabajo="").status_code == 422
    assert post_new(c, modalidad="On site", lugar_trabajo="").status_code == 422
    assert post_new(c, modalidad="Remoto", ubicacion="").status_code == 422                          # ubicación obligatoria también en remoto
    assert post_new(c, duracion_tipo="meses", duracion_meses="").status_code == 422
    assert post_new(c, duracion_tipo="meses", duracion_meses="121").status_code == 422
    assert files() == []


def test_conditional_acceptance_and_discarding_of_inapplicable_fields():
    c = ops()
    assert post_new(c, SID, modalidad="On site", dias_presenciales="", lugar_trabajo="Planta").status_code == 303        # On site: sin días
    assert repo.get(SID)["requerimiento"]["presencialidad"] == {"dias": None, "lugar": "Planta"}
    assert post_new(c, SID2, modalidad="Remoto", ubicacion="Indistinto", dias_presenciales="3", lugar_trabajo="x", tipo_busqueda="interna", proyecto="x",
                    duracion_tipo="indefinida", duracion_meses="9").status_code == 303
    r = repo.get(SID2)["requerimiento"]
    assert r["presencialidad"] is None and r["ubicacion"] == "Indistinto" and r["proyecto"] is None and r["duracion"] == {"tipo": "indefinida", "meses": None}
    assert post_new(c, SID3, modalidad="Híbrido", dias_presenciales="5", lugar_trabajo="CABA").status_code == 303


def test_a_session_that_does_not_identify_the_user_cannot_write_even_if_operaciones_is_authorized(monkeypatch):
    send()
    before = doc()
    monkeypatch.setattr(auth_service, "session_user", lambda request: {"sub": "", "name": "Sin identidad"})
    monkeypatch.setattr("webapp.operations_access.is_operations_authorized", lambda request: True)
    c = TestClient(app, follow_redirects=False)
    assert post_new(c, SID2).status_code == 403
    assert c.post(f"{BASE}/{SID}/editar", data={"expected_revision": "1", **form_payload(None)}, headers=ORIGIN).status_code == 403
    assert c.post(f"{BASE}/{SID}/cancelar", data={"expected_revision": "1"}, headers=ORIGIN).status_code == 403
    assert doc() == before and len(files()) == 1


def test_unknown_form_fields_are_ignored_and_cannot_inject_internal_fields():
    body = form_payload(SID)
    body.update({"estado": "cerrado", "solicitud": "x", "origen": "cchh", "revision": "99", "candidatos": "x", "jd_texto": "JD", "tomada_por": "x"})
    assert ops().post(f"{BASE}/nueva", data=body, headers=ORIGIN).status_code == 303
    d = repo.get(SID)
    assert d["estado"] == "activo" and d["origen"]["tipo"] == "operaciones" and d["revision"] == 1 and d["candidatos"] == [] and not d.get("jd_texto")
    assert d["solicitud"]["estado"] == "enviada" and "tomada_por" not in d["solicitud"]


@pytest.mark.parametrize("bad", [None, "", "no-es-uuid", "../../etc/passwd", "3f2504e0-4f89-41d3-9a0c-0305e82c3301/../x", "g" * 36])
def test_a_missing_or_malformed_submission_id_is_rejected_with_a_fresh_one_and_nothing_is_written(bad):
    r = ops().post(f"{BASE}/nueva", data=form_payload(bad), headers=ORIGIN)
    assert r.status_code == 422 and files() == []
    new_id = re.findall(r'name="submission_id" value="([^"]*)"', r.text)
    assert len(new_id) == 1 and str(uuid.UUID(new_id[0])) == new_id[0]


def test_double_submit_with_the_same_submission_id_creates_one_solicitud():
    c = ops()
    first, second = post_new(c), post_new(c)
    assert first.status_code == second.status_code == 303 and first.headers["location"] == second.headers["location"]
    d = repo.get(SID)
    assert len(files()) == 1 and [h["transicion"] for h in d["solicitud"]["historial"]] == ["enviar"] and d["revision"] == 1


def test_retry_by_a_second_tab_or_user_of_the_same_content_is_idempotent_for_the_same_requester_only():
    assert post_new(ops(OPS_A)).status_code == 303
    assert post_new(ops(OPS_A)).status_code == 303 and len(files()) == 1
    r = post_new(ops(OPS_B))                                          # mismo id, otro solicitante: conflicto (nunca se acepta en silencio)
    assert r.status_code == 409 and len(files()) == 1 and repo.get(SID)["solicitud"]["solicitante"] == OPS_A


def test_same_submission_id_with_different_content_is_a_conflict_with_a_fresh_id_and_no_overwrite():
    c = ops()
    post_new(c)
    r = post_new(c, nombre_puesto="Otro perfil")
    assert r.status_code == 409 and "otro contenido" in r.text and f'name="submission_id" value="{SID}"' not in r.text
    assert repo.get(SID)["requerimiento"]["nombre_puesto"] == "Consultor SAP FI" and len(files()) == 1 and 'value="Otro perfil"' in r.text


def test_retry_after_an_edit_returns_the_current_state_for_the_original_payload_and_conflicts_for_the_edited_one():
    c = ops()
    post_new(c)
    c.post(f"{BASE}/{SID}/editar", data={"expected_revision": "1", **form_payload(None, comentarios="editado")}, headers=ORIGIN)
    assert post_new(c).status_code == 303                                                         # el payload original sigue siendo el mismo envío
    assert post_new(c, comentarios="editado").status_code == 409
    assert len(files()) == 1


def test_html_in_user_text_is_escaped_in_every_screen():
    c = ops()
    evil = "<script>alert(1)</script>"
    post_new(c, nombre_puesto=evil, responsabilidades=evil, comentarios=evil)
    for path in (BASE, f"{BASE}/{SID}", f"{BASE}/{SID}/editar"):
        html = c.get(path).text
        assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html, path


# ═══════════════════════ listado, filtros e indicadores ═══════════════════════
def _make_all_states():
    send(SID, nombre_puesto="Perfil Nueva")                                              # nueva
    send(SID2, nombre_puesto="Perfil Reenviada", tipo_busqueda="interna", proyecto=None)
    svc.observe_solicitud(SID2, rev(SID2), "Falta detalle", CCHH_EMAIL)
    svc.resubmit_solicitud(SID2, rev(SID2), req(nombre_puesto="Perfil Reenviada", tipo_busqueda="interna", proyecto=None, comentarios="ok"), OPS_A)
    send(SID3, nombre_puesto="Perfil Observada")
    svc.observe_solicitud(SID3, rev(SID3), "Falta algo", CCHH_EMAIL)
    ids = {"nueva": SID, "reenviada": SID2, "observada": SID3}
    for key, sid in (("en_gestion", "a1111111-1111-4111-8111-111111111111"), ("cancelada", "b2222222-2222-4222-8222-222222222222"), ("cerrada", "c3333333-3333-4333-8333-333333333333")):
        send(sid, nombre_puesto=f"Perfil {key}")
        if key == "cancelada":
            svc.cancel_solicitud(sid, rev(sid), OPS_A)
        else:
            svc.take_solicitud(sid, rev(sid), CCHH_EMAIL)
        if key == "cerrada":
            svc.close_busqueda(sid)
        ids[key] = sid
    return ids


def test_list_empty_state():
    html = ops().get(BASE).text
    assert 'data-empty-kind="dataset_empty"' in html and "Sin solicitudes" in html and "Todavía no enviaste ninguna búsqueda a Capital Humano." in html and "<table" not in html
    assert "Creá la primera" not in html and 'no_matches' not in html and "No se encontraron" not in html           # no se mezcla con el estado «sin resultados del filtro»
    # DOS accesos de alta con el MISMO destino: el superior (sin cambios) y el del estado vacío, en el FORMATO de los estados vacíos de CCHH (ícono «+» `.table-action-btn` + tooltip)
    top = re.findall(r'<a href="([^"]+)" class="btn-primary">\+ Nueva búsqueda</a>', html)
    empty = re.search(r'<a href="([^"]+)" class="table-action-btn chart-tooltip-anchor busqueda-col__empty-add" data-empty-new aria-label="Nueva búsqueda">\s*<svg[^>]*><path d="M12 5v14"/><path d="M5 12h14"/></svg>\s*<span class="chart-tooltip" aria-hidden="true"><p>Nueva búsqueda</p></span>', html)
    assert top == [f"{BASE}/nueva"] and empty and empty.group(1) == f"{BASE}/nueva"
    assert "btn-primary\" data-empty-new" not in html                                                              # no es un botón de texto
    assert ops().get(top[0]).status_code == ops().get(empty.group(1)).status_code == 200                           # ambos navegan al mismo formulario


def test_filtered_empty_state_is_not_the_initial_state_and_offers_no_create_button():
    send(SID)
    html = ops().get(BASE, params={"q": "zzz-sin-coincidencias"}).text
    assert 'data-empty-kind="no_matches"' in html and "No se encontraron búsquedas con los filtros seleccionados." in html
    assert "Todavía no enviaste" not in html and "data-empty-new" not in html and "dataset_empty" not in html
    assert len(re.findall(r'>\+ Nueva búsqueda</a>', html)) == 1                                                       # solo el botón superior (HTMX: el fragmento no repite el alta)
    frag = ops().get(BASE, params={"q": "zzz"}, headers={"HX-Request": "true"}).text
    assert "No se encontraron búsquedas con los filtros seleccionados." in frag and "data-empty-new" not in frag


def test_list_shows_the_requested_columns_and_the_six_derived_indicators_without_new_states():
    ids = _make_all_states()
    html = ops().get(BASE).text
    for col in ("Perfil solicitado", "Proyecto / necesidad", "Área", "Recursos", "Apertura", "Estado", "Inicio previsto"):
        assert col in html, col
    for key, sid in ids.items():
        m = re.search(rf'<tr data-solicitud-id="{sid}" data-indicador="([a-z_]+)"', html)
        assert m and m.group(1) == key, (key, m and m.group(1))
    assert {"Nueva", "Reenviada", "Observada", "En gestión", "Cancelada", "Cerrada"} <= set(re.findall(r'class="status-badge status-badge--\w+"[^>]*>([^<]+)<', html))
    assert 'class="matrix-scroll"' in html and 'class="dashboard-table"' in html and 'class="dashboard-section"' in html and "hc-profile-filters filter-toolbar-card" in html
    assert "Migración S/4" in html and "Necesidad interna" in html and "SAP" in html and "02/11/2026" in html
    # los estados persistidos siguen siendo los de I1
    assert {repo.get(s)["solicitud"]["estado"] for s in ids.values()} == {"enviada", "observada", "en_gestion", "cancelada"}


def test_filter_by_each_indicator_and_ignoring_unknown_values():
    ids = _make_all_states()
    c = ops()
    for key, sid in ids.items():
        html = c.get(f"{BASE}?estado={key}").text
        rows = re.findall(r'data-solicitud-id="([0-9a-f-]+)"', html)
        assert rows == [sid], (key, rows)
        assert f'<option value="{key}" selected>' in html
    assert len(re.findall(r'data-solicitud-id=', c.get(f"{BASE}?estado=inventado").text)) == 6      # valor desconocido = sin filtro


def test_search_by_profile_or_project_is_case_and_accent_insensitive():
    send(SID, nombre_puesto="Consultor Básico", proyecto="Migración Alfa")
    send(SID2, nombre_puesto="Analista", proyecto="Proyecto Beta")
    c = ops()
    ids = lambda q: re.findall(r'data-solicitud-id="([0-9a-f-]+)"', c.get(BASE, params={"q": q}).text)   # noqa: E731
    assert ids("basico") == [SID] and ids("MIGRACION") == [SID] and ids("beta") == [SID2] and sorted(ids("")) == sorted([SID, SID2])
    html = c.get(BASE, params={"q": "zzz"}).text
    assert 'data-empty-kind="no_matches"' in html and "Sin resultados" in html and "Limpiar" in html


def test_search_and_filter_combine():
    ids = _make_all_states()
    html = ops().get(BASE, params={"q": "perfil", "estado": "cancelada"}).text
    assert re.findall(r'data-solicitud-id="([0-9a-f-]+)"', html) == [ids["cancelada"]]


def test_list_only_contains_operaciones_requests_and_never_candidate_data():
    send(SID, nombre_puesto="Solicitud real")
    direct = svc.create_busqueda({"nombre_puesto": "Alta directa CCHH", "fecha_apertura": "2026-09-01", "area_consultora": "SAP"}, actor=CCHH_EMAIL)
    pid = profile_repository.create({"first_name": "Cand", "last_name": "Secreto", "email": "secreto@candidato.com"})["profile_id"]
    svc.add_candidate_to_busqueda(direct["busqueda_id"], pid)
    html = ops().get(BASE).text
    assert "Solicitud real" in html and "Alta directa CCHH" not in html and direct["busqueda_id"] not in html
    assert "Secreto" not in html and "secreto@candidato.com" not in html and pid not in html


def test_list_is_ordered_by_planned_start_date_descending_and_reports_totals():
    send(SID, nombre_puesto="Temprana", fecha_inicio_prevista="2026-11-02")
    send(SID2, nombre_puesto="Tardía", fecha_inicio_prevista="2027-03-15")
    send(SID3, nombre_puesto="Intermedia", fecha_inicio_prevista="2026-12-20")
    html = ops().get(BASE).text
    assert html.index("Tardía") < html.index("Intermedia") < html.index("Temprana") and "3 solicitudes" in html


def test_list_survives_a_storage_failure(monkeypatch):
    monkeypatch.setattr(svc, "list_solicitudes_view", lambda: (_ for _ in ()).throw(repo.BusquedaStorageError("boom /secret/path")))
    r = ops().get(BASE)
    assert r.status_code == 200 and 'data-error-kind="source"' in r.text and "/secret/path" not in r.text


def test_filters_and_pagination_are_reactive_htmx_fragments_like_the_cchh_lists():
    _make_all_states()
    c = ops()
    frag = c.get(BASE, params={"estado": "observada"}, headers={"HX-Request": "true"}).text
    assert "<html" not in frag and 'id="opsb-panel"' in frag and len(re.findall(r'data-solicitud-id=', frag)) == 1
    full = c.get(BASE, params={"estado": "observada"}).text
    assert "<html" in full and 'id="opsb-panel"' in full and 'hx-get="/operaciones/busquedas"' in full and "✕ Limpiar" in full


def test_the_list_is_paginated_with_the_shared_pager_and_keeps_the_filters():
    for i in range(52):
        send(str(uuid.uuid4()), nombre_puesto=f"Perfil {i:02d}")
    c = ops()
    page1 = c.get(BASE).text
    assert len(re.findall(r'data-solicitud-id=', page1)) == 50 and "52 solicitudes" in page1 and 'class="activity-pager"' in page1
    page2 = c.get(BASE, params={"page": "2", "page_size": "50"}, headers={"HX-Request": "true"}).text
    assert len(re.findall(r'data-solicitud-id=', page2)) == 2 and "52 solicitudes" in page2
    assert "10 solicitudes" in c.get(BASE, params={"q": "Perfil 0", "estado": "nueva"}).text          # filtro + búsqueda antes de paginar: el contador es el total FILTRADO
    kept = c.get(BASE, params={"q": "Perfil", "estado": "nueva"}).text
    assert re.search(r'hx-get="[^"]*page=2[^"]*"', kept) and all("q=Perfil" in u and "estado=nueva" in u for u in re.findall(r'hx-get="([^"]*page=2[^"]*)"', kept))


# ═══════════════════════ detalle ═══════════════════════
def test_detail_shows_the_full_request_and_its_history():
    c = ops()
    post_new(c)
    html = c.get(f"{BASE}/{SID}").text
    for fragment in ("Consultor SAP FI", "Migración S/4", "SSR, SR", "40 · Horas por semana", "6 meses", "Buenos Aires", "Híbrido", "2 por semana", "Oficina del cliente, CABA", "Configurar FI.",
                     "SAP FI, S/4HANA", "Comunicación", "Intermedio", "Full-Time", "02/11/2026", OPS_A, "Enviada a Capital Humano", "data-historial"):
        assert fragment in html, fragment
    assert f'href="{BASE}/{SID}/editar"' in html and 'data-dialog-open="opsb-cancel-dialog"' in html and 'data-indicador="nueva"' in html


def test_detail_of_an_observed_request_shows_the_observation_the_correct_action_and_the_shared_history():
    to_state("observada")
    html = ops().get(f"{BASE}/{SID}").text
    assert "data-observacion" in html and "Falta el proyecto" in html and "Corregir y reenviar" in html and ">Editar<" not in html
    # El historial es EL MISMO que ve Capital Humano: Operaciones ve también quién de CCHH hizo cada movimiento (decisión del owner: entender qué pasó con la búsqueda).
    assert CCHH_EMAIL in html and "Corrección solicitada por Capital Humano" in html and 'data-indicador="observada"' in html


def test_a_taken_and_vigente_request_stays_editable_with_a_read_only_pipeline_and_no_cancel_or_delete():
    to_state("en_gestion")
    html = ops().get(f"{BASE}/{SID}").text
    assert "data-vigente-en-gestion" in html and f'href="{BASE}/{SID}/editar"' in html and 'data-indicador="en_gestion"' in html
    assert "opsb-cancel-dialog" not in html and "opsb-delete-dialog" not in html and "Cancelar solicitud" not in html and "Eliminar solicitud" not in html
    assert 'data-pipeline-mode="solo-lectura"' in html and "busqueda-board" in html and "busqueda-views" in html                    # botonera + tablero (ciclo), solo lectura
    for action in ("busqueda-add-dialog", "busqueda-close-dialog", "/cchh/busquedas", 'name="etapa"', "Contactar", "Quitar del proceso", "Agregar candidato"):
        assert action not in html, action
    assert "data-evaluation-locked" in html and "Sugeridos" in html and "Reportes" in html                                         # Evaluación bloqueada igual que en CCHH para quien no es el dueño
    assert "El pipeline se muestra en solo lectura" not in html                                                                    # sin la nota aclaratoria (pedido del owner)


@pytest.mark.parametrize("estado", ["cancelada"])
def test_non_editable_states_are_read_only_in_the_detail(estado):
    to_state(estado)
    html = ops().get(f"{BASE}/{SID}").text
    assert "data-readonly" in html and "solo lectura" in html
    for forbidden in ("/editar", ">Editar<", "Corregir y reenviar", "opsb-cancel-dialog", "Cancelar solicitud", "/cancelar"):
        assert forbidden not in html, forbidden
    assert CCHH_EMAIL not in html


def test_a_closed_process_is_labelled_closed_and_read_only():
    to_state("en_gestion")
    svc.close_busqueda(SID)
    html = ops().get(f"{BASE}/{SID}").text
    assert 'data-indicador="cerrada"' in html and "Cerrada el" in html and "data-readonly" in html and "/editar" not in html


def test_detail_shows_the_read_only_board_but_never_contact_data_internal_ids_or_internal_process_fields():
    to_state("en_gestion")
    pid = profile_repository.create({"first_name": "Cand", "last_name": "Secreto", "email": "secreto@candidato.com"})["profile_id"]
    d = doc()
    svc.update_busqueda(SID, d["revision"], {"nombre_puesto": "N", "fecha_apertura": d["fecha_apertura"], "area_consultora": "SAP", "jd_texto": "JD INTERNO",
                                             "palabras_clave": "CLAVES INTERNAS", "descripcion": "DESCRIPCION INTERNA"})
    svc.add_candidate_to_busqueda(SID, pid)
    html = ops().get(f"{BASE}/{SID}").text
    # Decisión del owner: Operaciones ve el tablero (quién está en cada etapa) y el MISMO historial que CCHH. Sigue sin ver contacto, ids internos ni datos internos del proceso.
    assert "Cand Secreto" in html and "Preselección" in html and "Candidato asociado" in html
    for secret in ("secreto@candidato.com", pid, "JD INTERNO", "CLAVES INTERNAS", "DESCRIPCION INTERNA", "tomada_por", "postulaciones", "/cchh/candidatos", "mailto:", "wa.me"):
        assert secret not in html, secret


@pytest.mark.parametrize("bad_id", ["no-existe", "3f2504e0-4f89-41d3-9a0c-0305e82c33ff", "..%2F..%2Fetc%2Fpasswd", "a" * 80])
def test_unknown_or_invalid_ids_are_404_on_every_route(bad_id):
    c = ops()
    assert c.get(f"{BASE}/{bad_id}").status_code == 404 and c.get(f"{BASE}/{bad_id}/editar").status_code == 404
    assert c.post(f"{BASE}/{bad_id}/editar", data={"expected_revision": "1", **form_payload(None)}, headers=ORIGIN).status_code == 404
    assert c.post(f"{BASE}/{bad_id}/cancelar", data={"expected_revision": "1"}, headers=ORIGIN).status_code == 404


def test_a_cchh_originated_busqueda_is_not_reachable_from_operaciones():
    b = svc.create_busqueda({"nombre_puesto": "Alta directa", "fecha_apertura": "2026-09-01", "area_consultora": "SAP"}, actor=CCHH_EMAIL)
    bid = b["busqueda_id"]
    c = ops()
    before = repo.get(bid)
    assert c.get(f"{BASE}/{bid}").status_code == 404 and c.get(f"{BASE}/{bid}/editar").status_code == 404
    assert c.post(f"{BASE}/{bid}/editar", data={"expected_revision": "1", **form_payload(None)}, headers=ORIGIN).status_code == 404
    assert c.post(f"{BASE}/{bid}/cancelar", data={"expected_revision": "1"}, headers=ORIGIN).status_code == 404
    assert repo.get(bid) == before


# ═══════════════════════ edición, corrección y reenvío ═══════════════════════
def test_edit_form_is_prefilled_for_an_enviada_request_and_keeps_the_revision():
    send()
    html = ops().get(f"{BASE}/{SID}/editar").text
    assert 'data-mode="editar"' in html and 'name="expected_revision" value="1"' in html and 'value="Consultor SAP FI"' in html and "Guardar cambios" in html
    assert re.search(r'<option value="SAP" selected>', html) and re.search(r'value="SSR" checked', html) and re.search(r'value="SR" checked', html) and 'value="JR" checked' not in html
    assert 'name="submission_id"' not in html


def test_editing_an_enviada_request_updates_it_keeps_the_opening_date_and_stays_enviada():
    send()
    opened = doc()["fecha_apertura"]
    c = ops(OPS_B)                                                         # otro usuario de Operaciones también puede editar
    r = c.post(f"{BASE}/{SID}/editar", data={"expected_revision": "1", **form_payload(None, nombre_puesto="Consultor SAP MM", comentarios="nota")}, headers=ORIGIN)
    assert r.status_code == 303 and r.headers["location"] == f"{BASE}/{SID}?ok=editada"
    d = doc()
    assert d["revision"] == 2 and d["solicitud"]["estado"] == "enviada" and d["requerimiento"]["nombre_puesto"] == "Consultor SAP MM" and d["nombre_puesto"] == "Consultor SAP MM"
    assert d["fecha_apertura"] == opened and d["solicitud"]["historial"][-1]["transicion"] == "editar" and d["solicitud"]["historial"][-1]["actor"] == OPS_B
    assert "Cambios guardados" in c.get(r.headers["location"]).text and len(files()) == 1


def test_correcting_an_observed_request_resubmits_it_as_enviada_with_a_visible_resend_indicator():
    to_state("observada")
    c = ops()
    html = c.get(f"{BASE}/{SID}/editar").text
    assert 'data-mode="corregir"' in html and "Falta el proyecto" in html and "Corregir y reenviar a Capital Humano" in html
    r = c.post(f"{BASE}/{SID}/editar", data={"expected_revision": str(rev()), **form_payload(None, comentarios="ya está")}, headers=ORIGIN)
    assert r.status_code == 303 and r.headers["location"] == f"{BASE}/{SID}?ok=reenviada"
    d = doc()
    assert d["solicitud"]["estado"] == "enviada" and d["solicitud"]["observacion"] is None and svc.get_solicitud_view(SID)["reenviada"] is True
    assert [h["transicion"] for h in d["solicitud"]["historial"]] == ["enviar", "observar", "reenviar"]
    page = c.get(r.headers["location"]).text
    assert "Solicitud corregida y reenviada" in page and 'data-indicador="reenviada"' in page and "Falta el proyecto" in page      # la observación queda en el historial


def test_editing_a_taken_vigente_request_is_allowed_and_leaves_actor_and_fields_in_the_history():
    to_state("en_gestion")
    c = ops(OPS_B)
    assert 'data-mode="editar"' in c.get(f"{BASE}/{SID}/editar").text
    r = c.post(f"{BASE}/{SID}/editar", data={"expected_revision": str(rev()), **form_payload(None, nombre_puesto="Consultor SAP MM", comentarios="tras la toma")}, headers=ORIGIN)
    assert r.status_code == 303 and r.headers["location"] == f"{BASE}/{SID}?ok=editada"
    d = doc()
    assert d["solicitud"]["estado"] == "en_gestion" and d["nombre_puesto"] == "Consultor SAP MM" and d["requerimiento"]["nombre_puesto"] == "Consultor SAP MM"   # CCHH ve el cambio en su proceso
    ev = d["solicitud"]["historial"][-1]
    assert ev["transicion"] == "editar" and ev["actor"] == OPS_B and "Perfil solicitado" in ev["motivo"] and "Observaciones" in ev["motivo"]
    page = c.get(r.headers["location"]).text
    assert "Editada por Operaciones" in page and OPS_B in page and "Cambios guardados" in page
    hc = session_client(CCHH_EMAIL).get(f"/cchh/busquedas/{SID}").text                       # visibilidad cruzada: la misma edición en el historial y el requerimiento de CCHH
    assert "Editada por Operaciones" in hc and "Consultor SAP MM" in hc


def test_a_cancelled_or_closed_request_cannot_be_edited_from_operations():
    to_state("en_gestion")
    svc.close_busqueda(SID, actor=CCHH_EMAIL)
    c = ops()
    before = doc()
    assert c.get(f"{BASE}/{SID}/editar").status_code == 303
    r = c.post(f"{BASE}/{SID}/editar", data={"expected_revision": str(rev()), **form_payload(None, nombre_puesto="Intento")}, headers=ORIGIN)
    assert r.status_code == 409 and doc() == before


@pytest.mark.parametrize("estado", ["cancelada"])
def test_non_editable_requests_redirect_on_get_and_reject_posts_without_changes(estado):
    to_state(estado)
    c = ops()
    before = doc()
    r = c.get(f"{BASE}/{SID}/editar")
    assert r.status_code == 303 and r.headers["location"] == f"{BASE}/{SID}"
    r = c.post(f"{BASE}/{SID}/editar", data={"expected_revision": str(rev()), **form_payload(None, nombre_puesto="Intento")}, headers=ORIGIN)
    assert r.status_code == 409 and "cambió" in r.text
    assert 'id="opsb-form"' not in r.text and "data-readonly" in r.text        # se muestra el detalle de solo lectura, nunca un formulario editable
    assert doc() == before


def test_stale_revision_is_a_conflict_and_changes_nothing():
    send()
    svc.edit_solicitud(SID, 1, req(comentarios="otro usuario"), OPS_B)               # alguien más editó (revisión 2)
    before = doc()
    r = ops().post(f"{BASE}/{SID}/editar", data={"expected_revision": "1", **form_payload(None, comentarios="mío")}, headers=ORIGIN)
    assert r.status_code == 409 and "cambió" in r.text and "mío" in r.text      # no pierde lo que el usuario había escrito
    assert doc() == before


def test_cchh_observing_while_the_form_is_open_does_not_let_operaciones_overwrite():
    send()
    stale = rev()
    svc.observe_solicitud(SID, stale, "Observación nueva", CCHH_EMAIL)
    r = ops().post(f"{BASE}/{SID}/editar", data={"expected_revision": str(stale), **form_payload(None, comentarios="tarde")}, headers=ORIGIN)
    assert r.status_code == 409 and doc()["solicitud"]["estado"] == "observada" and doc()["requerimiento"]["comentarios"] in (None, "")


@pytest.mark.parametrize("bad", ["", "x", "1.5", "-"])
def test_invalid_expected_revision_is_a_400(bad):
    send()
    assert ops().post(f"{BASE}/{SID}/editar", data={"expected_revision": bad, **form_payload(None)}, headers=ORIGIN).status_code == 400
    assert ops().post(f"{BASE}/{SID}/cancelar", data={"expected_revision": bad}, headers=ORIGIN).status_code == 400
    assert doc()["revision"] == 1


def test_missing_expected_revision_is_a_400():
    send()
    assert ops().post(f"{BASE}/{SID}/editar", data=form_payload(None), headers=ORIGIN).status_code == 400


def test_invalid_edit_rerenders_the_form_with_the_message_and_changes_nothing():
    send()
    before = doc()
    r = ops().post(f"{BASE}/{SID}/editar", data={"expected_revision": "1", **form_payload(None, cantidad_recursos="0")}, headers=ORIGIN)
    assert r.status_code == 422 and "cantidad de recursos" in r.text.lower() and 'name="expected_revision" value="1"' in r.text
    assert doc() == before


# ═══════════════════════ cancelación ═══════════════════════
@pytest.mark.parametrize("estado", ["enviada", "observada"])
def test_cancelling_enviada_or_observada_closes_the_request_with_an_optional_reason(estado):
    to_state(estado)
    c = ops(OPS_B)
    r = c.post(f"{BASE}/{SID}/cancelar", data={"expected_revision": str(rev()), "motivo": "Ya no se necesita"}, headers=ORIGIN)
    assert r.status_code == 303 and r.headers["location"] == f"{BASE}/{SID}?ok=cancelada"
    d = doc()
    assert d["solicitud"]["estado"] == "cancelada" and d["estado"] == "cerrado" and d["fecha_cierre"] == repo._business_today()
    assert d["solicitud"]["motivo_cancelacion"] == "Ya no se necesita" and d["solicitud"]["historial"][-1]["actor"] == OPS_B
    page = c.get(r.headers["location"]).text
    assert "Solicitud cancelada" in page and "Ya no se necesita" in page and 'data-indicador="cancelada"' in page and "/editar" not in page


def test_the_cancellation_reason_is_optional_but_the_trace_is_not():
    send()
    assert ops().post(f"{BASE}/{SID}/cancelar", data={"expected_revision": "1"}, headers=ORIGIN).status_code == 303
    h = doc()["solicitud"]["historial"][-1]
    assert h["transicion"] == "cancelar" and h["actor"] == OPS_A and h["fecha"] and doc()["solicitud"]["motivo_cancelacion"] in (None, "")


@pytest.mark.parametrize("estado", ["en_gestion", "cancelada"])
def test_cancelling_a_non_cancellable_request_is_a_conflict_without_changes(estado):
    to_state(estado)
    before = doc()
    r = ops().post(f"{BASE}/{SID}/cancelar", data={"expected_revision": str(rev())}, headers=ORIGIN)
    assert r.status_code == 409 and doc() == before


def test_cancel_with_a_stale_revision_is_a_conflict_and_a_too_long_reason_is_a_validation_error():
    send()
    svc.edit_solicitud(SID, 1, req(comentarios="x"), OPS_B)
    assert ops().post(f"{BASE}/{SID}/cancelar", data={"expected_revision": "1"}, headers=ORIGIN).status_code == 409
    assert ops().post(f"{BASE}/{SID}/cancelar", data={"expected_revision": "2", "motivo": "x" * 1001}, headers=ORIGIN).status_code == 422
    assert doc()["solicitud"]["estado"] == "enviada"


def test_a_cancelled_request_cannot_be_edited_or_resubmitted_afterwards():
    send()
    ops().post(f"{BASE}/{SID}/cancelar", data={"expected_revision": "1"}, headers=ORIGIN)
    r = ops().post(f"{BASE}/{SID}/editar", data={"expected_revision": "2", **form_payload(None, nombre_puesto="Revivir")}, headers=ORIGIN)
    assert r.status_code == 409 and doc()["solicitud"]["estado"] == "cancelada" and doc()["requerimiento"]["nombre_puesto"] == "Consultor SAP FI"


# ═══════════════════════ errores de persistencia y resultado remoto ambiguo ═══════════════════════
def test_a_storage_error_on_create_is_a_controlled_503_without_leaking_internals(monkeypatch):
    def boom(*a, **k):
        raise repo.BusquedaStorageError("fallo en /srv/secreto/human-capital")
    monkeypatch.setattr(svc, "create_solicitud", boom)
    r = post_new(ops())
    assert r.status_code == 503 and "secreto" not in r.text and "no se va a duplicar" in r.text and f'name="submission_id" value="{SID}"' in r.text and 'value="Consultor SAP FI"' in r.text


def test_an_ambiguous_remote_outcome_keeps_the_same_submission_id_so_the_retry_cannot_duplicate(monkeypatch):
    calls = {"n": 0}
    real = svc.create_solicitud

    def flaky(sid, fields, actor):
        calls["n"] += 1
        if calls["n"] == 1:
            raise repo.BusquedaRemoteOutcomeUnknownError("indeterminado")
        return real(sid, fields, actor)
    monkeypatch.setattr(svc, "create_solicitud", flaky)
    c = ops()
    r = post_new(c)
    assert r.status_code == 503 and "No pudimos confirmar" in r.text and f'name="submission_id" value="{SID}"' in r.text
    assert post_new(c).status_code == 303 and len(files()) == 1


def test_a_failed_remote_write_leaves_no_request_and_the_retry_succeeds_once(remote):
    remote.enabled = True
    remote.push_script = [False, False]                                 # el servicio reintenta una vez: ambos PUT fallan
    c = ops()
    r = post_new(c)
    assert r.status_code in (503, 422) and files() == [] and svc.list_solicitudes_view() == []
    assert post_new(c).status_code == 303 and len(files()) == 1 and doc()["solicitud"]["estado"] == "enviada"


def test_storage_errors_on_edit_and_cancel_are_controlled_responses_and_keep_the_user_data(monkeypatch):
    send()
    monkeypatch.setattr(svc, "edit_solicitud", lambda *a, **k: (_ for _ in ()).throw(repo.BusquedaStorageError("x")))
    monkeypatch.setattr(svc, "cancel_solicitud", lambda *a, **k: (_ for _ in ()).throw(repo.BusquedaRemoteOutcomeUnknownError("x")))
    r = ops().post(f"{BASE}/{SID}/editar", data={"expected_revision": "1", **form_payload(None, comentarios="no perder esto")}, headers=ORIGIN)
    assert r.status_code == 503 and "no perder esto" in r.text
    r = ops().post(f"{BASE}/{SID}/cancelar", data={"expected_revision": "1"}, headers=ORIGIN)
    assert r.status_code == 503 and "No pudimos confirmar" in r.text


def test_reading_a_request_with_a_failing_store_is_a_503_not_a_500(monkeypatch):
    monkeypatch.setattr(svc, "get_solicitud_view", lambda bid, **kw: (_ for _ in ()).throw(repo.BusquedaConfigIncompleteError("x")))
    assert ops().get(f"{BASE}/{SID}").status_code == 503


# ═══════════════════════ una sola persistencia / compatibilidad con CCHH ═══════════════════════
def test_the_new_routes_use_only_the_i1_service_and_create_no_other_storage():
    post_new(ops())
    root = files()[0].parents[1]                                   # raíz de `human-capital/`
    stored = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and "catalogs" not in p.parts)   # `catalogs/` = snapshot sembrado por el fixture
    assert stored == [f"busquedas/{SID}.json"]


def test_cchh_routes_keep_working_with_a_request_created_from_operaciones():
    post_new(ops())
    cc = session_client(CCHH_EMAIL)
    assert cc.get("/cchh/busquedas").status_code == 200
    r = cc.get(f"/cchh/busquedas/{SID}")
    assert r.status_code == 200
    assert cc.get(f"/cchh/busquedas/{SID}/editar").status_code == 303        # no tomada: CCHH no la edita (I1)
    assert repo.get(SID)["solicitud"]["estado"] == "enviada"


def test_operaciones_cannot_touch_the_candidate_pipeline_through_the_new_routes():
    to_state("en_gestion")
    pid = profile_repository.create({"first_name": "Cand", "last_name": "X", "email": "x@candidato.com"})["profile_id"]
    svc.add_candidate_to_busqueda(SID, pid)
    before = doc()
    c = ops()
    for path in (f"{BASE}/{SID}/candidatos", f"{BASE}/{SID}/cerrar"):
        assert c.post(path, data={"profile_id": pid}, headers=ORIGIN).status_code in (404, 405)
    # `/eliminar` SÍ existe (bloque «eliminar solicitud no gestionada»), pero una solicitud ya tomada con candidatos jamás se elimina
    assert c.post(f"{BASE}/{SID}/eliminar", data={"expected_revision": str(rev())}, headers=ORIGIN).status_code == 409
    assert doc() == before and doc()["candidatos"]
