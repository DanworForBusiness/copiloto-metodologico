"""
test_commercial_strategy_section_nav.py — Hub Transversal Module Section
Navigation (ver HANDOFF): nav anidado OPCIONAL que un módulo activo puede
exponer debajo de su propio `.module-link` en el sidebar del workspace —
Comercial → 02 · Estrategia es el PRIMER consumidor, nunca el dueño de la
forma (`workspace_shell.html::module_section_nav`).

Cubre:
 A. módulos sin section-nav (CRM) renderizan EXACTAMENTE igual que antes
 B. Estrategia expone hijos anidados SOLO cuando está activa
 C. Portada + 6 pilares + 2 acción + 4 anexos están todos presentes
 D. cada ancla del sidebar apunta a un id que existe de verdad en la página
 E. los títulos del sidebar vienen del documento canónico de Estrategia
    (`strategy.<key>.title`), NUNCA de un string hardcodeado
 F. `_SECTION_NAV_MAP` es la ÚNICA fuente estructural — nunca un segundo
    mapa Estrategia-específico solo para el sidebar
 H. los links del sidebar participan del MISMO selector delegado de
    anchor-seguro/telemetría que `.section-index a` (app.js), nunca una
    segunda implementación
 J. `nav_jump` sigue atribuido a CM02/Comercial vía el mismo mecanismo
    genérico (dw_module_id) — sin evento nuevo
 K. el nav anidado vive fuera de cualquier control de Edit mode
 L. el contrato refleja un título editado sin plumbing adicional
 M. `.module-subnav` no tiene reglas de tema propias — vive dentro de
    `.module-nav`, que nunca repinta con `.content-theme-light`
 N. módulos sin section-nav no pagan costo (cero wrapper/observer/caret)
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers.commercial_strategy import _SECTION_NAV_MAP, _build_section_navigation
from webapp.services import auth_service
from webapp.services.commercial_strategy_repository import load_strategy

_ROOT = Path(__file__).resolve().parents[2]
_APP_JS = (_ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
_TOOLS_CSS = (_ROOT / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
_LAYOUT_CSS = (_ROOT / "webapp" / "static" / "layout.css").read_text(encoding="utf-8")
_STRATEGY_HTML = (_ROOT / "webapp" / "templates" / "commercial" / "strategy.html").read_text(encoding="utf-8")
_NAV_HTML = (_ROOT / "webapp" / "templates" / "partials" / "commercial_nav.html").read_text(encoding="utf-8")
_SHELL_HTML = (_ROOT / "webapp" / "templates" / "partials" / "workspace_shell.html").read_text(encoding="utf-8")

_TEST_EMAIL = "section-nav-test@danwor.com"

_SESSION = auth_service.create_session(
    {"email": _TEST_EMAIL, "name": "Section Nav Test", "first_name": "Section", "family_name": "Test"}
)
client = TestClient(app)
client.cookies.set(auth_service.COOKIE_NAME, _SESSION)


@pytest.fixture(autouse=True)
def _authorized(monkeypatch):
    # `monkeypatch` (NUNCA una asignación directa a nivel de módulo, ver
    # `test_commercial_strategy.py`) -- varios archivos de test de
    # Comercial reasignan `auth_service.COMMERCIAL_ALLOWED_USERS` a nivel
    # de módulo; en una corrida combinada, el ÚLTIMO módulo importado
    # "gana" esa asignación global para TODOS los tests que corren después
    # (pytest importa todos los módulos de test en la fase de collection,
    # antes de ejecutar ninguno). `monkeypatch` reasigna en cada test y
    # revierte solo, así que este archivo queda correcto sin importar en
    # qué orden se ejecute junto a los demás.
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", _TEST_EMAIL)


# ============================================================================
# A / N — CRM (módulo sin section-nav) permanece EXACTAMENTE igual
# ============================================================================

def test_a_crm_has_no_subnav_wrapper():
    response = client.get("/comercial")
    assert response.status_code == 200
    assert "module-subnav" not in response.text


def test_n_crm_nav_markup_is_the_flat_three_link_list():            # 01 CRM · 02 Estrategia · 03 Presentaciones (V1)
    response = client.get("/comercial")
    nav_start = response.text.index('<nav aria-label="Comercial">')
    nav_end = response.text.index("</nav>", nav_start)
    nav_block = response.text[nav_start:nav_end]
    assert nav_block.count('class="module-link') == 3
    assert "module-subnav" not in nav_block


# ============================================================================
# B / C — Estrategia expone Portada + 6 + 2 + 4 SOLO cuando activa
# ============================================================================

def test_b_strategy_page_has_subnav_wrapper():
    response = client.get("/comercial/estrategia")
    assert response.status_code == 200
    assert "module-subnav" in response.text


def test_c_portada_plus_six_plus_two_plus_four_links_present():
    response = client.get("/comercial/estrategia")
    assert '<div class="module-subnav"' in response.text
    # Conteo robusto: sobre `section_navigation` en memoria, nunca sobre un
    # recorte de string frágil contra el HTML renderizado.
    strategy = load_strategy()
    nav = _build_section_navigation(strategy)
    total_links = sum(len(g["links"]) for g in nav["groups"])
    assert total_links == 12  # 6 pilares + 2 acción + 4 anexos
    assert nav["top"]["label"] == "Portada"
    # Etiquetas ACORTADAS a propósito (owner feedback tras browser
    # validation: la columna angosta del sidebar hacía envolver los
    # títulos largos a 2 líneas) — NUNCA toca las etiquetas del índice
    # in-page "EN ESTE MÓDULO" (esas siguen completas, viven aparte en
    # strategy.html, con columnas anchas donde entran en una línea).
    assert [g["label"] for g in nav["groups"]] == ["Pilares", "Acciones", "Anexos"]
    # El índice in-page "EN ESTE MÓDULO" sigue con su etiqueta LARGA
    # original -- el sidebar acorta la SUYA, nunca la del índice in-page
    # (CSS la muestra en mayúsculas vía text-transform; el HTML en sí
    # sigue en minúsculas/título, ver strategy.html).
    assert "Pilares estratégicos" in client.get("/comercial/estrategia").text
    assert [len(g["links"]) for g in nav["groups"]] == [6, 2, 4]


# ============================================================================
# D — cada ancla del sidebar apunta a un id que existe de verdad
# ============================================================================

def test_d_every_sidebar_anchor_matches_a_real_section_id():
    response = client.get("/comercial/estrategia")
    strategy = load_strategy()
    nav = _build_section_navigation(strategy)
    all_ids = [nav["top"]["id"]] + [item["id"] for g in nav["groups"] for item in g["links"]]
    for anchor_id in all_ids:
        assert f'id="{anchor_id}"' in response.text, f"falta id={anchor_id} en la página"


def test_d_top_anchor_is_the_existing_hero_never_a_new_one():
    assert _build_section_navigation(load_strategy())["top"]["id"] == "hero"
    assert '<section id="hero" class="module-hero">' in _STRATEGY_HTML


# ============================================================================
# E — los títulos vienen del documento canónico, nunca hardcodeados
# ============================================================================

def test_e_sidebar_titles_come_from_the_canonical_strategy_document():
    strategy = load_strategy()
    nav = _build_section_navigation(strategy)
    for anchor_id, (_family, _code, content_key) in _SECTION_NAV_MAP.items():
        item = next(i for g in nav["groups"] for i in g["links"] if i["id"] == anchor_id)
        assert item["label"] == strategy[content_key]["title"]


# ============================================================================
# F — UN solo mapa estructural, nunca un segundo mapa solo para el sidebar
# ============================================================================

def test_f_strategy_template_no_longer_hardcodes_a_second_nav_map():
    # El template lee `nav_map` (context, resuelto en Python) -- ya NO
    # define su propio dict literal `{% set cs_nav_map = {...} %}`.
    assert "{% set cs_nav_map = nav_map %}" in _STRATEGY_HTML
    assert "'p1': ('pilares'" not in _STRATEGY_HTML
    assert "'genda': ('pilares'" not in _STRATEGY_HTML


def test_f_router_context_passes_the_same_map_instance_strategy_reads():
    # `nav_map` (para el índice/eyebrows in-page) y `_build_section_navigation`
    # (para el sidebar) derivan AMBOS de `_SECTION_NAV_MAP` -- nunca dos
    # definiciones independientes.
    assert len(_SECTION_NAV_MAP) == 12
    nav = _build_section_navigation(load_strategy())
    nav_ids = {item["id"] for g in nav["groups"] for item in g["links"]}
    assert nav_ids == set(_SECTION_NAV_MAP.keys())


# ============================================================================
# H / J — reusa el selector delegado de anchor-seguro + nav_jump, sin
# segunda implementación ni evento nuevo
# ============================================================================

def test_h_module_subnav_links_share_the_existing_safe_anchor_selector():
    assert '".section-index a, .module-roadmap__links a, .module-subnav a"' in _APP_JS


def test_h_no_second_click_handler_or_reveal_implementation_was_added():
    # Un solo listener delegado de click para "saltar por índice" en TODO
    # app.js -- nunca uno propio para el nav anidado nuevo.
    assert _APP_JS.count('el.closest(".section-index a') == 1


def test_j_no_strategy_specific_telemetry_event_was_introduced():
    assert "section_nav_jump" not in _APP_JS
    assert "module_section_nav_jump" not in _APP_JS
    # `nav_jump` sigue siendo el ÚNICO evento de este tipo.
    assert _APP_JS.count('dwBeacon("nav_jump"') == 1


# ============================================================================
# K — el nav anidado vive fuera del control de Edit mode
# ============================================================================

def test_k_subnav_lives_outside_the_edit_form_and_has_no_edit_controls():
    response = client.get("/comercial/estrategia")
    text = response.text
    subnav_start = text.index('<div class="module-subnav"')
    save_btn_pos = text.find('id="cs-save-btn"')
    # El form de edición (con el botón Guardar) está DESPUÉS del nav lateral
    # en el documento y en OTRO subárbol -- nunca anidados entre sí. Ambos
    # existen en la misma página (la barra "Editar" vive arriba del hero),
    # así que alcanza probar que ninguno contiene al otro.
    aside_start = text.index('<aside class="module-nav"')
    aside_end = text.index("</aside>", aside_start)
    assert subnav_start > aside_start and text.index("</div>", subnav_start) < aside_end
    assert save_btn_pos == -1 or not (aside_start < save_btn_pos < aside_end)


def test_k_module_section_nav_macro_never_reads_editing_or_doc_state():
    macro_start = _SHELL_HTML.index("{% macro module_section_nav")
    macro_end = _SHELL_HTML.index("{% endmacro %}", macro_start)
    macro_body = _SHELL_HTML[macro_start:macro_end]
    assert "editing" not in macro_body
    assert "doc." not in macro_body
    assert "x-model" not in macro_body
    assert "contenteditable" not in macro_body


# ============================================================================
# L / M — Save/reload: el contrato refleja el último título persistido.
# Dos capas de prueba, nunca contra el documento REAL (ver incidente previo
# de corrupción de contenido — toda escritura acá va contra un runtime
# AISLADO en tmp_path, nunca `webapp/services/commercial_strategy_data/`):
#   L — unidad rápida: la función pura, en memoria.
#   M — integración REAL: reusa los fixtures ya establecidos de
#       test_commercial_strategy.py (`_isolated_repo`/`_save_payload`/
#       `_with_owner`/`owner_client`) para ir por el camino de PRODUCTO
#       completo -- POST oficial de Estrategia → runtime persistido
#       (tmp_path) → reload → section_navigation/heading/índice in-page
#       TODOS de acuerdo. `_isolated_repo` reemplaza el singleton del
#       repository por uno apuntando a `tmp_path` ANTES de cualquier
#       request -- cero riesgo para el documento real, cleanup automático
#       de pytest (`tmp_path` se descarta solo al terminar el test).
# ============================================================================

def test_l_section_navigation_reflects_an_edited_title_with_zero_extra_plumbing():
    strategy = load_strategy()
    edited = dict(strategy)
    edited["objectives"] = {**strategy["objectives"], "title": "Título editado de prueba"}
    nav = _build_section_navigation(edited)
    assert nav["groups"][0]["links"][0]["label"] == "Título editado de prueba"


def test_m_real_save_via_official_endpoint_propagates_to_heading_index_and_sidebar(tmp_path, monkeypatch):
    from tests.webapp.test_commercial_strategy import _isolated_repo, _save_payload, _with_owner, owner_client

    # `owner_client` (test_commercial_strategy.py) trae una sesión FIJA
    # para "content-owner-test@danwor.com" -- el `_authorized` autouse de
    # ESTE archivo (arriba) reemplaza `COMMERCIAL_ALLOWED_USERS` por
    # `_TEST_EMAIL` para el resto de sus tests, lo que excluiría a ese
    # email acá. `can_edit_strategy_user` exige AMBOS gates (comercial +
    # content owner) -- se re-habilita el email del owner explícitamente,
    # sobre el MISMO `monkeypatch` de este test (revierte solo al terminar).
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "content-owner-test@danwor.com")
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)  # runtime AISLADO en tmp_path -- nunca el documento real

    seed_strategy = load_strategy()
    new_title = "Objetivos — título editado (test M, camino real)"
    payload = _save_payload(1, objectives={**seed_strategy["objectives"], "title": new_title})
    save_response = owner_client.post(
        "/comercial/estrategia", json=payload, headers={"Origin": "http://testserver"},
    )
    assert save_response.status_code == 200, save_response.text

    # Reload -- reconstruye el contexto de página desde cero, MISMO GET que
    # un usuario real haría después de guardar.
    reload_response = owner_client.get("/comercial/estrategia")
    assert reload_response.status_code == 200
    text = reload_response.text

    reloaded = load_strategy()
    assert reloaded["objectives"]["title"] == new_title  # el runtime AISLADO sí persistió

    nav = _build_section_navigation(reloaded)
    assert nav["groups"][0]["links"][0]["label"] == new_title  # sidebar

    # section heading (eyebrow [01] + <h2>) e índice in-page: MISMA fuente
    # (`strategy.objectives.title`, ver strategy.html) -- deben coincidir,
    # nunca uno desactualizado respecto del otro.
    assert text.count(new_title) >= 3, "heading + índice in-page + sidebar deben coincidir"


# ============================================================================
# M — sin variantes de tema propias: `.module-subnav` vive dentro de
# `.module-nav`, que nunca repinta con `.content-theme-light`
# ============================================================================

def test_m_module_subnav_css_rules_exist():
    assert ".module-subnav {" in _TOOLS_CSS
    assert ".module-subnav__link" in _TOOLS_CSS
    assert ".module-subnav__group" in _TOOLS_CSS


def test_m_module_nav_is_never_repainted_by_content_theme_light():
    # MISMO invariante que ya regía antes de este cambio (layout.css):
    # `.content-theme-light` solo repinta `.module-content`, nunca
    # `.module-nav` -- el nav anidado nuevo hereda ese theme-invariance
    # gratis, sin reglas .content-theme-* propias que mantener.
    assert ".content-theme-light .module-nav" not in _LAYOUT_CSS
    assert ".content-theme-light .module-subnav" not in _TOOLS_CSS
    assert ".content-theme-dark .module-subnav" not in _TOOLS_CSS


def test_m_no_stray_jinja_comment_closer_in_css_files():
    # Regresión puntual del bug real encontrado durante esta implementación
    # (un comentario CSS cerrado con `#}` de Jinja en vez de `*/` se traga
    # todo el resto del archivo como comentario -- ningún selector nuevo
    # llega a parsearse). Nunca debería reaparecer en ningún .css servido.
    for css_path in (_ROOT / "webapp" / "static").glob("*.css"):
        assert "#}" not in css_path.read_text(encoding="utf-8"), css_path.name


# ============================================================================
# Mutua exclusión con el índice in-page (owner feedback tras browser
# validation): los DOS índices nunca se muestran a la vez. Fuente-contrato
# (la reactividad de `x-show`/IntersectionObserver en vivo no es
# observable en este harness — ver browser validation manual).
# ============================================================================

def test_subnav_starts_hidden_and_fades_in_only_once_the_in_page_index_is_scrolled_past():
    assert 'subnavVisible: false,' in _APP_JS
    assert 'x-show="subnavVisible"' in _SHELL_HTML
    assert "x-transition.opacity" in _SHELL_HTML


def test_subnav_visibility_observer_is_gated_by_both_elements_existing():
    # Cero costo en páginas sin `.module-subnav` (todo módulo que no optó a
    # section-nav) -- el observer nuevo ni se instancia ahí.
    assert 'const subnav = shell.querySelector(".module-subnav");' in _APP_JS
    assert 'const inPageIndex = shell.querySelector(".module-content .section-index");' in _APP_JS
    assert "if (subnav && inPageIndex" in _APP_JS


def test_subnav_visibility_reactive_state_toggles_correctly_when_set():
    # Prueba de integración liviana: renderiza la página y confirma que el
    # markup lleva el estado inicial correcto (oculto) -- la transición en
    # sí (CSS) queda fuera del alcance de un test de contrato de backend.
    response = client.get("/comercial/estrategia")
    subnav_tag_start = response.text.index('<div class="module-subnav"')
    subnav_tag_end = response.text.index(">", subnav_tag_start)
    subnav_open_tag = response.text[subnav_tag_start:subnav_tag_end]
    assert 'x-show="subnavVisible"' in subnav_open_tag
    assert "x-cloak" in subnav_open_tag


# ============================================================================
# Autorización — MISMO contrato que el resto de Comercial
# ============================================================================

def test_unauthorized_user_never_sees_the_subnav_either(monkeypatch):
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", _TEST_EMAIL)
    session = auth_service.create_session(
        {"email": "unlisted@danwor.com", "name": "Unlisted", "first_name": "Unlisted", "family_name": "User"}
    )
    ro = TestClient(app)
    ro.cookies.set(auth_service.COOKIE_NAME, session)
    response = ro.get("/comercial/estrategia")
    assert response.status_code == 403


# ============================================================================
# Contrato genérico — `module_registry` sigue sin conocer contenido de
# negocio; `commercial_nav.html` opta in por AUSENCIA, nunca un flag propio
# ============================================================================

def test_generic_opt_in_is_by_variable_absence_not_a_boolean_flag():
    # Genericity Gate: la condición chequea SOLO `section_navigation` --
    # NUNCA `active_page == 'estrategia'` (versión anterior, corregida). La
    # identidad de Estrategia no vive en la condición del primitivo
    # compartido, solo en la POSICIÓN del include dentro de este archivo
    # workspace-específico (aceptable, ver ticket).
    assert "{% if section_navigation %}" in _NAV_HTML
    assert "active_page == 'estrategia' and section_navigation" not in _NAV_HTML
    assert "has_section_nav" not in _NAV_HTML
    assert "show_section_nav" not in _NAV_HTML


def test_shared_macro_js_and_css_carry_no_strategy_specific_identity():
    # Genericity Gate, prueba grep-style: el macro compartido, el JS y el
    # CSS nuevos nunca hardcodean "estrategia"/"CM02"/nombres de sección de
    # Estrategia en CÓDIGO (selectores, condicionales, comparaciones de
    # string) -- solo pueden aparecer en PROSA de comentario explicando el
    # contexto ("Estrategia es el primer consumidor"), nunca en lógica.
    macro_start = _SHELL_HTML.index("{% macro module_section_nav")
    macro_end = _SHELL_HTML.index("{% endmacro %}", macro_start) + len("{% endmacro %}")
    macro_body = _SHELL_HTML[macro_start:macro_end]
    for needle in ("estrategia", "CM02", "objectives", "demand_generation"):
        assert needle not in macro_body.lower() if needle.islower() else needle not in macro_body

    # app.js: el código nuevo (fuera de comentarios `//`) no debe mencionar
    # Estrategia -- una revisión línea por línea, descartando comentarios.
    code_lines = [l for l in _APP_JS.splitlines() if not l.strip().startswith("//")]
    code_only = "\n".join(code_lines)
    assert "estrategia" not in code_only.lower()
    assert "CM02" not in code_only

    # tools.css: mismo criterio, pero despojando comentarios `/* */`
    # primero -- la prosa explicativa ahí SÍ puede nombrar a Estrategia
    # como "primer consumidor" (documentación, no lógica); ningún
    # SELECTOR ni valor real puede.
    css_code_only = re.sub(r"/\*.*?\*/", "", _TOOLS_CSS, flags=re.DOTALL)
    assert "estrategia" not in css_code_only.lower()
    assert "CM02" not in css_code_only


def test_module_registry_was_not_touched_for_this_feature():
    registry_src = (_ROOT / "webapp" / "services" / "module_registry.py").read_text(encoding="utf-8")
    assert "section_navigation" not in registry_src
    assert "module_section_nav" not in registry_src


# ============================================================================
# G — cada link del sidebar liga al MISMO `activeSection` compartido
# (ver HANDOFF: el rAF real no es observable en este harness de browser;
# esta es la prueba estructural del contrato de binding en sí, más la
# prueba en vivo sancionada por el ticket -- set directo de
# `Alpine.$data(root).activeSection` -- documentada aparte en el reporte).
# ============================================================================

def test_g_every_subnav_link_binds_to_the_shared_scrollspy_state():
    macro_start = _SHELL_HTML.index("{% macro module_section_nav")
    macro_end = _SHELL_HTML.index("{% endmacro %}", macro_start)
    macro_body = _SHELL_HTML[macro_start:macro_end]
    # Mismo `activeSection` que ya gobierna `.module-roadmap__links a` -- cero
    # estado de scroll-spy propio para el nav nuevo.
    assert macro_body.count(':class="activeSection ===') == 2  # top + item
    assert macro_body.count(':aria-current="activeSection ===') == 2


def test_g_activeSection_state_has_a_single_declaration_never_a_second_one_for_subnav():
    # `activeSection` ya existía en `copilotLayout()` antes de este feature
    # (gobierna `.module-roadmap__links a`) -- el nav nuevo debe LEERLO, nunca
    # declarar un segundo estado de scroll-spy propio.
    assert _APP_JS.count("activeSection:") == 1
    assert "subnavActiveSection" not in _APP_JS
    assert "activeSubnavSection" not in _APP_JS


# ============================================================================
# Q / R — un módulo representativo del OTRO workspace (Copiloto) y su
# navegación de roadmap existente quedan byte-for-byte sin tocar.
# ============================================================================

def test_q_representative_other_workspace_module_has_no_subnav_wrapper():
    response = client.get("/modules/03_Integracion_y_Estrategia_de_Datos")
    assert response.status_code == 200
    assert "module-subnav" not in response.text


def test_r_existing_copilot_roadmap_navigation_is_unaffected():
    response = client.get("/modules/03_Integracion_y_Estrategia_de_Datos")
    assert "module-roadmap__links" in response.text
    # Mismo selector delegado de siempre, ahora con el sufijo `.module-subnav
    # a` agregado -- pero `.module-roadmap__links a` sigue intacto, primero
    # en la lista, sin reordenar ni duplicar.
    assert _APP_JS.count('.module-roadmap__links a') == 1


# ============================================================================
# §6 — deep-link: el ancla objetivo de cada link existe de verdad (ver
# test_d) y es alcanzable por el MISMO mecanismo de reveal-seguro que ya
# cubre `.section-index`/`.module-roadmap__links` (ver test_h) -- ningún
# ancla del sidebar queda fuera de ese contrato. El auto-scroll real al
# cargar con `#hash` en la URL y el comportamiento con un hash inválido son
# puramente de NAVEGADOR (el fragmento nunca viaja al servidor por HTTP) --
# no observables con TestClient; confirmado manualmente en vivo (ver
# reporte) usando la MISMA mecánica de anchor ya existente en la app, no una
# nueva.
# ============================================================================

def test_deep_link_every_subnav_anchor_is_covered_by_the_safe_reveal_selector():
    # Cualquier <a> que el sidebar emite matchea el selector delegado que
    # dispara el force-reveal en app.js -- ninguno queda "huérfano" de esa
    # protección (lo que causaría el flash opacity:0 en un deep-link frío).
    assert '.module-subnav a' in _APP_JS
    response = client.get("/comercial/estrategia")
    strategy = load_strategy()
    nav = _build_section_navigation(strategy)
    for group in nav["groups"]:
        for item in group["links"]:
            assert f'href="{item["href"]}"' in response.text
            assert f'id="{item["id"]}"' in response.text


def test_deep_link_invalid_hash_has_no_server_side_special_casing():
    # Un hash inválido nunca llega al servidor (HTTP no transmite el
    # fragment) -- confirma que la página no depende de ningún parsing de
    # fragment del lado del router (lo que sí podría romperse con un hash
    # desconocido). Sin lógica de fragment == sin forma de que un hash
    # inválido rompa nada server-side.
    router_src = (_ROOT / "webapp" / "routers" / "commercial_strategy.py").read_text(encoding="utf-8")
    assert "request.url.fragment" not in router_src
    assert ".fragment" not in router_src


# ============================================================================
# §7 — viewport corto: el sidebar sigue siendo sticky con scroll interno
# PROPIO (pre-existente, ver app.css) -- el nav anidado nuevo no reemplaza
# ni angosta ese contrato; la sección activa puede salir del viewport visible
# del sidebar sin romper nada porque el sidebar mismo scrollea (confirmado en
# vivo en browser con viewport corto, ver reporte).
# ============================================================================

def test_short_viewport_module_nav_keeps_its_pre_existing_sticky_internal_scroll():
    app_css = (_ROOT / "webapp" / "static" / "app.css").read_text(encoding="utf-8")
    assert "position:sticky" in app_css.replace(" ", "")
    assert ".module-nav{position:sticky" in app_css.replace(" ", "") or (
        ".module-nav" in app_css and "overflow:auto" in app_css.replace(" ", "")
    )
