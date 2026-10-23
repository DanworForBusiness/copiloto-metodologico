"""Checklist de Onboarding en contenedores colapsables: la agrupación es SOLO presentación. Los 17 ítems, sus claves, el estado `{clave: bool}`, el guardado inmediato por ítem, los endpoints y el cálculo de progreso no cambian."""
from __future__ import annotations

import re

import pytest

from webapp.services import human_capital_service as svc, onboarding_checklist as ck, onboarding_repository as orepo, profile_repository
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, env, remote  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import session_client, operations_users  # noqa: F401
from tests.webapp.test_pipeline_interview_shortcut import _manual  # noqa: F401

CANONICAL = ("ddjj_domicilio", "cv_formato_danwor", "nota_convenio_colectivo", "oferta_empleo", "nda", "dni", "foto", "titulos_certificados", "preocupacional", "alta_arca",
             "nota_herramientas", "sensibilizacion_iso", "requerimiento_perfil", "comunidad_wp", "alta_legajo_diamo", "alta_timenotes", "verificacion_referencias")
APPROVED = {
    "documentacion_personal": ("Documentación personal", ["dni", "foto", "ddjj_domicilio", "titulos_certificados"]),
    "contratacion_alta_legal": ("Contratación y alta legal", ["oferta_empleo", "nda", "nota_convenio_colectivo", "preocupacional", "alta_arca"]),
    "perfil_verificacion": ("Perfil y verificación", ["cv_formato_danwor", "requerimiento_perfil", "verificacion_referencias"]),
    "induccion_comunicacion": ("Inducción y comunicación", ["nota_herramientas", "sensibilizacion_iso", "comunidad_wp"]),
    "altas_sistemas_internos": ("Altas en sistemas internos", ["alta_legajo_diamo", "alta_timenotes"]),
}


@pytest.fixture()
def c():
    return session_client(CCHH_EMAIL)


def _onboarding(ticked=()) -> str:
    bid = _manual()
    pid = profile_repository.create({"first_name": "Ana", "last_name": "Ingreso", "email": "ana@danwor.com"})["profile_id"]
    app_id = svc.add_candidate_to_busqueda(bid, pid, actor=CCHH_EMAIL)["candidatos"][-1]["application_id"]
    svc.move_application_stage(bid, app_id, "Finalizado", "Ingreso", actor=CCHH_EMAIL)
    for k in ticked:
        orepo.set_item(app_id, k, True)
    return app_id


def _page(c, app_id) -> str:
    return c.get(f"/cchh/onboarding/{app_id}/editar").text


def _groups(html: str) -> dict:
    out = {}
    for m in re.finditer(r'<details class="dashboard-section hc-onb-group hc-onb-group--(\w+)" data-section-id="(\w+)" data-onboarding-group="\w+" data-group-estado="\w+"( open)?>(.*?)</details>', html, re.S):
        estado, key, opened, body = m.group(1), m.group(2), bool(m.group(3)), m.group(4)
        out[key] = {"estado": estado, "open": opened, "progress": re.search(r"data-group-progress>([^<]+)<", body).group(1), "badge": re.search(r'status-badge status-badge--(\w+)">([^<]+)<', body).groups(),
                    "items": re.findall(r'data-onboarding-item="(\w+)"', body), "label": re.search(r'dashboard-section__title">([^<]+)<', body).group(1)}
    return out


# ═══════════════════ la definición: una partición exacta de los 17 ítems, sin tocar la canónica ═══════════════════
def test_the_canonical_items_their_order_and_the_progress_formula_are_untouched():
    assert tuple(ck.ITEM_KEYS) == CANONICAL and len(ck.CHECKLIST_ITEMS) == 17
    assert ck.progress({}) == {"hechos": 0, "total": 17, "porcentaje": 0, "completo": False}
    assert ck.progress({"dni": True, "nda": True}) == {"hechos": 2, "total": 17, "porcentaje": 12, "completo": False}
    assert ck.progress({k: True for k in CANONICAL})["completo"] is True and ck.progress({k: True for k in CANONICAL})["porcentaje"] == 100
    assert [it["key"] for it in ck.items_view(None)] == list(CANONICAL)


def test_the_groups_partition_exactly_the_17_items_with_the_approved_names_and_order():
    keys = [k for _, _, items in ck.CHECKLIST_GROUPS for k in items]
    assert sorted(keys) == sorted(CANONICAL) and len(keys) == len(set(keys)) == 17                              # ningún ítem repetido ni faltante
    assert {key: (label, list(items)) for key, label, items in ck.CHECKLIST_GROUPS} == APPROVED
    assert [key for key, _, _ in ck.CHECKLIST_GROUPS] == list(APPROVED)


def test_group_progress_state_and_default_open_follow_the_ticked_items():
    empty = ck.groups_view(None)
    assert [g["estado"] for g in empty] == ["sin_empezar"] * 5 and all(g["abierto_por_defecto"] and g["hechos"] == 0 for g in empty) and sum(g["total"] for g in empty) == 17
    partial = {g["key"]: g for g in ck.groups_view({"dni": True, "foto": True, "ddjj_domicilio": True, "titulos_certificados": True, "nda": True})}
    assert (partial["documentacion_personal"]["estado"], partial["documentacion_personal"]["hechos"], partial["documentacion_personal"]["abierto_por_defecto"]) == ("completo", 4, False)
    assert (partial["contratacion_alta_legal"]["estado"], partial["contratacion_alta_legal"]["hechos"], partial["contratacion_alta_legal"]["abierto_por_defecto"]) == ("en_curso", 1, True)
    assert partial["altas_sistemas_internos"]["estado"] == "sin_empezar" and partial["altas_sistemas_internos"]["abierto_por_defecto"]
    done = ck.groups_view({k: True for k in CANONICAL})
    assert all(g["estado"] == "completo" and not g["abierto_por_defecto"] and g["hechos"] == g["total"] for g in done)             # todo completo: los cinco inician colapsados
    assert sum(g["hechos"] for g in done) == ck.progress({k: True for k in CANONICAL})["hechos"] == 17           # los grupos suman el mismo progreso general
    assert [it["checked"] for g in ck.groups_view({"dni": True}) for it in g["tareas"] if it["key"] == "dni"] == [True]


# ═══════════════════ la pantalla ═══════════════════
def test_the_edit_screen_renders_five_collapsible_groups_with_progress_state_and_default_open(c):
    app_id = _onboarding(ticked=("dni", "foto", "ddjj_domicilio", "titulos_certificados", "nda"))
    html = _page(c, app_id)
    g = _groups(html)
    assert list(g) == list(APPROVED) and {k: v["label"] for k, v in g.items()} == {k: v[0] for k, v in APPROVED.items()}
    assert [g[k]["items"] for k in g] == [APPROVED[k][1] for k in g]                                              # cada ítem en su contenedor, en el orden aprobado
    assert g["documentacion_personal"] == {**g["documentacion_personal"], "estado": "completo", "open": False, "progress": "4/4", "badge": ("activo", "Completo")}
    assert (g["contratacion_alta_legal"]["open"], g["contratacion_alta_legal"]["progress"], g["contratacion_alta_legal"]["badge"]) == (True, "1/5", ("tentativo", "En curso"))
    assert (g["perfil_verificacion"]["open"], g["perfil_verificacion"]["progress"], g["perfil_verificacion"]["badge"]) == (True, "0/3", ("inactivo", "Sin empezar"))   # pendientes: abiertos
    assert "5/17 · 29%" in html and 'class="status-badge status-badge--tentativo" data-onboarding-progress' in html                                 # el progreso general se conserva en su lugar de siempre
    assert 'draggable' not in html.split('id="hc-onboarding-checklist"')[1]                                      # los grupos no se reordenan
    assert html.count('class="dashboard-section hc-onb-group') == 5 and "dashboard-section__caret" in html


def test_every_item_keeps_its_key_state_and_immediate_save_endpoint(c):
    app_id = _onboarding(ticked=("nda",))
    html = _page(c, app_id)
    keys = re.findall(r'data-onboarding-item="(\w+)"', html)
    assert sorted(keys) == sorted(CANONICAL) and len(keys) == 17
    for k in CANONICAL:
        m = re.search(r'<input type="checkbox" name="checked" value="1" data-onboarding-item="%s" (checked)?\s*hx-post="/cchh/onboarding/%s/checklist/%s" hx-trigger="change" hx-include="this"\s*hx-target="#hc-onboarding-checklist" hx-swap="outerHTML">' % (k, app_id, k), html)
        assert m, k                                                                                              # mismo endpoint, mismo disparador y mismo objetivo de reemplazo
        assert bool(m.group(1)) == (k == "nda")


def test_ticking_still_saves_immediately_and_the_replacement_fragment_carries_the_groups(c):
    app_id = _onboarding()
    url = lambda k: f"/cchh/onboarding/{app_id}/checklist/{k}"
    r = c.post(url("dni"), data={"checked": "1"}, headers={"HX-Request": "true"})
    g = _groups(r.text)
    assert r.status_code == 200 and "<html" not in r.text and 'id="hc-onboarding-checklist"' in r.text and "1/17 · 6%" in r.text
    assert g["documentacion_personal"]["progress"] == "1/4" and g["documentacion_personal"]["badge"] == ("tentativo", "En curso") and g["documentacion_personal"]["open"]
    assert orepo.get(app_id)["checklist"]["dni"] is True and orepo.get(app_id)["completado"] is False
    r2 = c.post(url("dni"), data={}, headers={"HX-Request": "true"})                                              # destildar: no envía el campo
    assert _groups(r2.text)["documentacion_personal"]["progress"] == "0/4" and orepo.get(app_id)["checklist"]["dni"] is False
    assert c.post(url("inexistente"), data={"checked": "1"}).status_code == 404                                  # validación de ítem intacta
    assert "dni" in orepo.get(app_id)["checklist"] and "inexistente" not in orepo.get(app_id)["checklist"]


def test_completing_everything_collapses_the_five_groups_and_keeps_the_overall_100_percent(c):
    app_id = _onboarding(ticked=[k for k in CANONICAL if k != "alta_timenotes"])
    before = _groups(_page(c, app_id))
    assert before["altas_sistemas_internos"]["open"] and before["altas_sistemas_internos"]["progress"] == "1/2" and not before["documentacion_personal"]["open"]
    r = c.post(f"/cchh/onboarding/{app_id}/checklist/alta_timenotes", data={"checked": "1"}, headers={"HX-Request": "true"})
    g = _groups(r.text)
    assert all(not v["open"] and v["estado"] == "completo" and v["badge"] == ("activo", "Completo") for v in g.values())
    assert "Completado · 17/17" in r.text and orepo.get(app_id)["completado"] is True                            # progreso general y `completado` intactos
    assert "Completado · 17/17" in _page(c, app_id)


def test_the_manual_open_close_preference_script_is_wired_per_onboarding_and_survives_htmx_swaps(c):
    app_id = _onboarding()
    html = _page(c, app_id)
    assert f'data-onboarding-application="{app_id}"' in html
    script = html.split("<script>")[-1].split("</script>")[0]
    assert "sessionStorage" in script and '"dw-onb-collapse:"' in script and 'htmx:afterSwap' in script and "__hcOnbGroupsWired" in script
    assert 'closest("#hc-onboarding-checklist summary")' in script                                                # solo el click de la persona es preferencia manual (un reemplazo no)
    frag = c.post(f"/cchh/onboarding/{app_id}/checklist/dni", data={"checked": "1"}, headers={"HX-Request": "true"}).text
    assert "__hcOnbGroupsWired" in frag and f'data-onboarding-application="{app_id}"' in frag                    # el fragmento reemplazado también lo trae


def test_authorization_and_the_data_form_are_unchanged(c):
    app_id = _onboarding()
    anon = session_client("sin.acceso@danwor.com")
    assert anon.get(f"/cchh/onboarding/{app_id}/editar").status_code == 403 and anon.post(f"/cchh/onboarding/{app_id}/checklist/dni", data={"checked": "1"}).status_code == 403
    assert orepo.get(app_id)["checklist"] == {}
    html = _page(c, app_id)
    assert 'name="fecha_ingreso"' in html and 'id="onb-oferta"' in html and 'name="area"' in html and "Checklist de ingreso" in html and "Guardar cambios" in html


def test_layout_rules_align_the_second_column_match_the_hub_title_and_add_breathing_room():
    from pathlib import Path
    css = (Path(__file__).resolve().parents[2] / "webapp/static/human_capital.css").read_text(encoding="utf-8")
    assert ".hc-onb-group .dashboard-checkbox-group--grid-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }" in css               # columnas iguales: la segunda columna alinea en los cinco grupos
    assert "@media (max-width: 560px) { .hc-onb-group .dashboard-checkbox-group--grid-2 { grid-template-columns: minmax(0, 1fr); } }" in css   # una columna en móvil
    assert ".hc-onb-checklist { margin-top: 28px; }" in css and re.search(r"\.hc-onb-checklist > h2 \{[^}]*font-family: var\(--sans\); font-size: \.96rem; font-weight: 700;", css)   # aire + tipografía del título de grupo
    tpl = (Path(__file__).resolve().parents[2] / "webapp/templates/partials/human_capital_onboarding_checklist.html").read_text(encoding="utf-8")
    assert 'class="hc-profile-section hc-onb-checklist" id="hc-onboarding-checklist"' in tpl
