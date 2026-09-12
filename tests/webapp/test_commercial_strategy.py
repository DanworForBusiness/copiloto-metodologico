"""
test_commercial_strategy.py — Commercial Strategy (Phase 1, READ-ONLY).

Cubre el checklist del owner (storage gate + phase 1 spec):
 A. nav expone "02 · Estrategia"
 B. ruta protegida por la MISMA auth de Comercial
 C. el documento seedeado carga
 D. las 13 secciones fuente están representadas
 E. tabs de segmento cambian correctamente (markup/estado Alpine presente)
 F. sub-tabs anidados cambian correctamente
 G. contenido de negocio real está presente (texto del artefacto fuente)
 H. las filas de "Próximos pasos" preservan el orden fuente
 I. status/prioridad mapean a las clases Hub esperadas
 J. ningún bloque de HTML/script/style crudo del artefacto fuente se renderiza
 K. no existe UI de Editar/Guardar en Fase 1
 L. no existe ningún endpoint POST/write de Estrategia
 M. la capability CRM (01) sigue intacta
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.services import auth_service

_TEMPLATES = Path(__file__).resolve().parents[2] / "webapp" / "templates"
_ROUTERS = Path(__file__).resolve().parents[2] / "webapp" / "routers"
_STRATEGY_HTML = (_TEMPLATES / "commercial" / "strategy.html").read_text(encoding="utf-8")
_STRATEGY_PY = (_ROUTERS / "commercial_strategy.py").read_text(encoding="utf-8")
_NAV_HTML = (_TEMPLATES / "partials" / "commercial_nav.html").read_text(encoding="utf-8")

auth_service.ALLOWED_USERS = "ALL"
auth_service.COMMERCIAL_ALLOWED_USERS = (
    "commercial-test@danwor.com,commercial-odoo-test@danwor.com,content-owner-test@danwor.com"
)

_SESSION = auth_service.create_session(
    {"email": "commercial-test@danwor.com", "name": "Commercial Test", "first_name": "Commercial", "family_name": "Test"}
)

client = TestClient(app)
client.cookies.set(auth_service.COOKIE_NAME, _SESSION)


def _unlisted_client(monkeypatch):
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "commercial-test@danwor.com")
    session = auth_service.create_session(
        {"email": "unlisted@danwor.com", "name": "Unlisted", "first_name": "Unlisted", "family_name": "User"}
    )
    ro = TestClient(app)
    ro.cookies.set(auth_service.COOKIE_NAME, session)
    return ro


# ============================================================================
# A — Nav expone "02 · Estrategia"
# ============================================================================

def test_commercial_nav_exposes_estrategia_as_second_capability():
    response = client.get("/comercial")
    text = response.text
    assert 'href="/comercial" class="module-link is-active"' in text
    assert 'href="/comercial/estrategia" class="module-link ' in text
    assert "<strong>Estrategia</strong>" in text


def test_estrategia_page_highlights_its_own_nav_item():
    response = client.get("/comercial/estrategia")
    assert response.status_code == 200
    assert 'href="/comercial/estrategia" class="module-link is-active"' in response.text


# ============================================================================
# B — Ruta protegida por la MISMA auth de Comercial
# ============================================================================

def test_anonymous_redirects_to_login():
    anon = TestClient(app)
    response = anon.get("/comercial/estrategia", follow_redirects=False)
    assert response.status_code == 303


def test_unlisted_authorized_app_user_is_denied_with_403(monkeypatch):
    ro = _unlisted_client(monkeypatch)
    response = ro.get("/comercial/estrategia")
    assert response.status_code == 403


def test_commercial_allowed_users_blank_denies_the_previously_authorized_user(monkeypatch):
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    response = client.get("/comercial/estrategia")
    assert response.status_code == 403


def test_route_reuses_commercial_workspace_gate_never_a_new_one():
    assert "commercial_access.is_commercial_authorized" in _STRATEGY_PY
    assert "workspaces.platform_login_redirect(" in _STRATEGY_PY


# ============================================================================
# C — El documento seedeado carga
# ============================================================================

def test_authorized_user_gets_200_and_real_content():
    response = client.get("/comercial/estrategia")
    assert response.status_code == 200
    assert "no está disponible" not in response.text
    assert "Dónde jugamos" in response.text


# ============================================================================
# D — Las 13 secciones fuente están representadas
# ============================================================================

def test_document_index_reuses_the_copilot_section_index_contract():
    # MISMO primitivo que module.html's "En este módulo" (.section-index,
    # variante plana sin roadmap): `<nav class="section-index"><p>...</p>
    # <a><span>NN</span>Título</a></nav>` — nunca una implementación propia.
    text = client.get("/comercial/estrategia").text
    assert 'class="section-index"' in text
    assert 'aria-label="En este módulo"' in text
    assert "<p>En este módulo</p>" in text
    assert "<a href=\"#p1\"><span>01</span>Objetivos</a>" in text
    assert "<a href=\"#reglas\"><span>12</span>Reglas de oro</a>" in text


def test_document_index_has_no_leftover_source_number_prefix():
    # El artefacto fuente ya trae "1 · Objetivos" en el label — el índice
    # numera POR SU CUENTA (igual que Copilot); el prefijo del fuente no debe
    # duplicarse en el título visible.
    text = client.get("/comercial/estrategia").text
    index_html = text.split('aria-label="En este módulo"')[1].split("</nav>")[0]
    assert "1 · Objetivos" not in index_html
    assert ">Objetivos</a>" in index_html


def test_no_duplicate_strategy_specific_index_implementation():
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial_strategy.css").read_text(encoding="utf-8")
    for banned in (".cs-index", ".cs-nav", ".cs-toc", ".cs-section-index"):
        assert banned not in css, banned


# ============================================================================
# 26 — Contrato visual (regresión estructural — la validación de color/
# contraste real es SIEMPRE en browser, ver reporte; esto solo evita que la
# regresión concreta encontrada por el owner vuelva a colarse en silencio).
# ============================================================================

def test_strategy_tiles_theme_via_scoped_tokens_not_fixed_dark_only():
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial_strategy.css").read_text(encoding="utf-8")
    # La técnica REAL (no inventada): reusa la MISMA redefinición de
    # `--card`/`--ink`/`--soft`/`--line` que `.copilot-panel` ya usa en
    # tools.css, aplicada a `.cs-doc` — nunca overrides de color sueltos
    # por componente como único mecanismo.
    assert ".content-theme-light .cs-doc" in css
    assert ".content-theme-dark .cs-doc" in css
    assert "--card:" in css.replace(" ", "") or "--card :" in css


def test_strategy_page_root_carries_the_theme_scoping_class():
    html = (Path(__file__).resolve().parents[2] / "webapp" / "templates" / "commercial" / "strategy.html").read_text(encoding="utf-8")
    assert 'class="module-content cs-doc"' in html


def test_no_standalone_font_import_or_root_palette_reintroduced():
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial_strategy.css").read_text(encoding="utf-8")
    for banned in ("fonts.googleapis.com/css2?family=Barlow", "@import url", "--gold-dim", "--panel2"):
        assert banned not in css, banned


def test_contenteditable_only_used_as_a_leaf_control_never_a_structural_wrapper(monkeypatch):
    # Pivot Edit UX gate: contenteditable pasó de PROHIBIDO (mega-form, sin
    # concurrencia confirmada) a AUTORIZADO explícitamente pero SOLO como
    # leaf de negocio individual (item 4 del gate) — nunca en <main>/
    # <section>/<table>/<tbody>/<tr>/<article>, que seguirían siendo el
    # documento persistido si alguna vez volvieran contenteditable. Este
    # test reemplaza la prohibición total: ahora prueba la forma correcta
    # de usarlo, no su ausencia.
    import re
    _with_owner(monkeypatch)
    rendered = owner_client.get("/comercial/estrategia").text
    banned_wrappers = ("main", "section", "table", "tbody", "tr", "article", "body", "html")
    allowed_leaf_tags = ("span", "p", "div")  # ver inline_text(): siempre un leaf de texto, nunca un wrapper multi-hijo
    for m in re.finditer(r"<(\w+)[^>]*\bcontenteditable\b", rendered):
        tag = m.group(1).lower()
        assert tag not in banned_wrappers, f"contenteditable found on structural wrapper <{tag}>"
        assert tag in allowed_leaf_tags, f"unexpected contenteditable host tag <{tag}>"


def test_contenteditable_text_transform_neutralized_generically(monkeypatch):
    # Bug REAL encontrado DOS VECES en browser QA con una regla estrecha
    # (primero `.eyebrow`, después `.cs-frow__label` del embudo) — un
    # barrido sistemático de los 76 leaves contenteditable de la página
    # confirmó que cualquier clase VIEW con `text-transform` (`innerText`
    # es CSS-aware) mayusculiza/etc el valor guardado permanentemente sin
    # importar lo que haya tipeado el usuario. El reset ahora es GENÉRICO
    # sobre `.cs-inline` (con `!important`, ver commercial_strategy.css) en
    # vez de una regla por clase que se olvidaría de la próxima — esto
    # prueba que la regla genérica existe, no una instancia puntual.
    _with_owner(monkeypatch)
    rendered = owner_client.get("/comercial/estrategia").text
    assert 'class="cs-inline eyebrow"' in rendered
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial_strategy.css").read_text(encoding="utf-8")
    assert ".cs-inline { text-transform: none !important; }" in css


def test_contenteditable_elements_never_persist_innerhtml_only_structured_paths(monkeypatch):
    # El modelo persistido sigue siendo el documento ESTRUCTURADO — cada
    # leaf contenteditable escribe a `doc` vía `setPath`/`getPath` (paths
    # de negocio conocidos), nunca serializa su propio DOM/innerHTML. Un
    # `contenteditable="true"` por cada `setPath(` (1:1, ver `inline_text`)
    # y cero uso de `innerHTML` en todo el documento prueban esto sin
    # depender de un regex frágil sobre el tag completo (sus propios
    # atributos Alpine, p. ej. `(v) =>`, contienen `>` legítimos).
    _with_owner(monkeypatch)
    rendered = owner_client.get("/comercial/estrategia").text
    assert rendered.count('contenteditable="true"') > 0
    # +1 = la propia definición `setPath(path, value) {` en el x-data (ver
    # más abajo), que también matchea la substring — cada @input="setPath(
    # ...)" restante corresponde 1:1 a un leaf contenteditable.
    assert rendered.count('contenteditable="true"') == rendered.count("setPath(") - 1
    assert "innerHTML" not in rendered


def test_all_expected_section_links_present_in_index():
    text = client.get("/comercial/estrategia").text
    index_html = text.split('aria-label="En este módulo"')[1].split("</nav>")[0]
    assert index_html.count("<a href=") == 12  # 13 secciones fuente - la portada (hero)


def test_all_thirteen_source_sections_are_represented():
    response = client.get("/comercial/estrategia")
    text = response.text
    for section_id in (
        "hero", "p1", "genda", "p2", "p3", "p4", "p5", "p6", "proximos",
        "mapa", "roles", "proceso", "reglas",
    ):
        assert f'id="{section_id}"' in text, section_id


def test_section_titles_present():
    text = client.get("/comercial/estrategia").text
    for title in (
        "Objetivos", "Generación de demanda", "Dónde jugamos", "Criterio de foco",
        "Asignación de capacidad", "Cadencia de revisión", "Qué no se hace",
        "Próximos pasos", "Mapa de contactos", "Mapa de roles y responsabilidades",
        "Etapas del pipeline", "Reglas de oro",
    ):
        assert title in text, title


# ============================================================================
# E/F — Tabs de segmento y sub-tabs (markup + estado Alpine)
# ============================================================================

def test_segment_tabs_markup_present_for_all_six_segments():
    text = client.get("/comercial/estrategia").text
    assert 'class="cs-segtabs"' in text
    for label in (
        "1 · Semillero de eventos", "2 · C-Field Base Instalada", "3 · C-Field NNN",
        "4 · C-Digital NNN", "5 · Base propia Danwor", "6 · Servicios / Partners",
    ):
        assert label in text, label
    # Estado Alpine: un `seg` inicial + `x-show` por panel (6 paneles).
    assert "x-data=\"{ seg: 'sgen'" in text
    assert text.count("x-show=\"seg ===") == 6


def test_nested_subtabs_markup_present_for_segments_with_subtabs():
    text = client.get("/comercial/estrategia").text
    assert 'class="cs-subtabs"' in text
    for label in ("RISE/GROW aún no en vivo", "Business One", "New Names puros"):
        assert text.count(label) >= 2  # aparece en C-Field NNN y C-Digital NNN
    # `subsel` cubre exactamente los 2 segmentos con sub-tabs (s2, s3).
    assert "'s2': 's2a'" in text
    assert "'s3': 's3a'" in text


# ============================================================================
# G — Contenido de negocio real presente
# ============================================================================

def test_real_business_wording_from_source_artifact_is_present():
    text = client.get("/comercial/estrategia").text
    for fragment in (
        "USD 500.000", "25.000 horas", "Modernización SAP", "Fernando Oriolo",
        "María José", "Julieta Tiddens", "Reality Data Breakfast",
        "Una oportunidad = un dueño", "Prospecto",
    ):
        assert fragment in text, fragment


# ============================================================================
# H — "Próximos pasos" preserva el orden fuente
# ============================================================================

def test_next_steps_rows_preserve_source_order():
    text = client.get("/comercial/estrategia").text
    first = text.index("Mapear cada cuenta por madurez de datos")
    second = text.index("Ejecutar el mapeo de madurez de cuentas")
    last = text.index("Organizar evento de generación de demanda")
    assert first < second < last


def test_next_steps_has_sixteen_rows():
    from webapp.services.commercial_strategy_repository import load_strategy
    doc = load_strategy()
    assert len(doc["next_steps"]["tasks"]) == 16


# ============================================================================
# I — Status/prioridad mapean a clases Hub
# ============================================================================

def test_status_and_priority_map_to_hub_badge_classes():
    text = client.get("/comercial/estrategia").text
    assert 'class="cs-priority">PRIORIDAD 1</span>' in text
    assert 'cs-priority--new' in text  # Segmento 5 · "PRIORIDAD 5 · SOLO LICENCIAS"
    assert "status-badge--tentativo" in text  # tareas Q4
    assert "status-badge--inactivo" in text  # tareas "A definir"


# ============================================================================
# J — Ningún bloque de HTML/script/style crudo del artefacto se renderiza
# ============================================================================

def test_no_raw_source_script_or_style_block_is_rendered():
    text = client.get("/comercial/estrategia").text
    # Nada del JS/CSS vanilla propio del artefacto standalone sobrevivió.
    for banned in (
        "function applyEditMode", "globalEditBtn", "dragstart", "recolorQ",
        "@import url('https://fonts.googleapis.com/css2?family=Barlow",
        "--gold-dim", "font-family:'Barlow'",
    ):
        assert banned not in text, banned


def test_persisted_markup_never_leaks_arbitrary_html_tags():
    # El mini-markup solo puede producir <strong>/<em>/<a href="http(s)...">/<br>
    # dentro del contenido persistido — nunca <script>/<img>/on*= atributos.
    text = client.get("/comercial/estrategia").text
    assert "<script>alert" not in text
    assert "onerror=" not in text
    assert "javascript:" not in text


# ============================================================================
# K/F — Un usuario de Comercial NO listado en CONTENT_OWNER: sin Editar,
# 403 si fuerza el POST. `client` (commercial-test@danwor.com) nunca está en
# CONTENT_OWNER en este archivo — MISMO usuario que el resto de los tests de
# lectura, deliberado (prueba el caso común: autorizado a Comercial, no a
# editar).
# ============================================================================

def test_non_owner_never_sees_edit_affordance():
    # Pivot Edit UX gate: la sección VIEW/EDIT vive en el MISMO árbol de
    # templates (nunca una rama "solo lectura" duplicada — ver docstring de
    # commercial_strategy.py), pero cada macro/bloque de edición (
    # `inline_text`, `kpi_grid_edit`, `string_list_edit`, `annex_tree_edit`,
    # tabla de próximos pasos, notas del mapa, toolbar) está gateado por
    # `{% if can_edit %}` puertas adentro — un no-owner no recibe NINGUNA
    # de esas strings en el HTML, aunque el template fuente sí las
    # contenga (ver test_contenteditable_only_used_as_a_leaf_control...).
    text = client.get("/comercial/estrategia").text
    for banned in (
        "cs-edit-toggle", "cs-edit-toolbar", ">Editar<", "editing-mode", "contenteditable",
        "cs-annex-add", "cs-table-edit", "cs-annex-notes-edit",
    ):
        assert banned not in text, banned


def test_non_owner_write_attempt_returns_403(monkeypatch):
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "")
    response = client.post("/comercial/estrategia")
    assert response.status_code == 403


# ============================================================================
# G — Usuario CONTENT_OWNER: recibe el affordance de Editar, puede "entrar"
# a EDIT (el panel existe en el DOM, aunque hoy responda 503 honesto al
# guardar — ver H/router).
# ============================================================================

_OWNER_SESSION = auth_service.create_session(
    {"email": "content-owner-test@danwor.com", "name": "Owner Test", "first_name": "Owner", "family_name": "Test"}
)
owner_client = TestClient(app)
owner_client.cookies.set(auth_service.COOKIE_NAME, _OWNER_SESSION)


def _with_owner(monkeypatch):
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "content-owner-test@danwor.com")


def test_content_owner_sees_edit_affordance_and_inline_controls(monkeypatch):
    # Pivot Edit UX gate: ya no existe un panel/formulario separado
    # (`#cs-edit-panel` fue retirado) — el owner recibe el toggle, el
    # save/cancel, y las afordancias de edición INLINE repartidas por
    # todo el documento (contenteditable en leaves de hero/objectives,
    # tabla editable de próximos pasos, árbol editable del mapa de
    # contactos), todas ocultas por `x-show="editing"` hasta que activa
    # "Editar" — presentes en el HTML, no en un `#cs-edit-panel` aparte.
    _with_owner(monkeypatch)
    text = owner_client.get("/comercial/estrategia").text
    assert 'id="cs-edit-toggle"' in text
    assert 'id="cs-save-btn"' in text
    assert "contenteditable" in text  # hero/objectives leaves
    assert "cs-table-edit" in text  # próximos pasos, tabla editable in-place
    assert "cs-tree" in text and "addAnnexNode(" in text  # mapa de contactos


# ============================================================================
# H — CONTENT_OWNER ausente/vacío: fail-closed, nadie edita implícitamente.
# ============================================================================

def test_empty_content_owner_fails_closed_for_everyone(monkeypatch):
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "")
    text = owner_client.get("/comercial/estrategia").text
    assert 'id="cs-edit-toggle"' not in text
    response = owner_client.post("/comercial/estrategia")
    assert response.status_code == 403


# ============================================================================
# I — El parser de CONTENT_OWNER es el MISMO CSV/alias que el resto del Hub
# (username compacto -> @AUTH_DEFAULT_DOMAIN, case-insensitive, coma-separado).
# ============================================================================

def test_content_owner_reuses_the_existing_csv_allowlist_convention(monkeypatch):
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "Content-Owner-Test, otro-usuario")
    assert auth_service.is_content_owner_user("content-owner-test@danwor.com") is True
    assert auth_service.is_content_owner_user("otro-usuario@danwor.com") is True
    assert auth_service.is_content_owner_user("nadie@danwor.com") is False


def test_content_owner_never_accepts_the_all_wildcard(monkeypatch):
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "ALL")
    assert auth_service.is_content_owner_user("anyone@danwor.com") is False


# ============================================================================
# J — APP_OWNER solo NUNCA implica can_edit_strategy.
# ============================================================================

def test_app_owner_alone_does_not_bypass_content_owner(monkeypatch):
    import config as _config
    monkeypatch.setattr(_config, "APP_OWNER", "content-owner-test@danwor.com")
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "")
    text = owner_client.get("/comercial/estrategia").text
    assert 'id="cs-edit-toggle"' not in text
    response = owner_client.post("/comercial/estrategia")
    assert response.status_code == 403


def test_commercial_access_never_references_app_owner():
    # `APP_OWNER` aparece en la PROSA de los docstrings (explicando por qué NO
    # se usa, herencia de Phase 1) — lo que este test protege es que nunca se
    # IMPORTE ni se LLAME como código real.
    access_py = (Path(__file__).resolve().parents[2] / "webapp" / "commercial_access.py").read_text(encoding="utf-8")
    assert "import access" not in access_py
    assert "from webapp import access" not in access_py
    assert "access.is_owner(" not in access_py
    assert "from config import APP_OWNER" not in access_py


# ============================================================================
# L/K-Z — El endpoint de escritura EXISTE, autoritativo, y con topología
# single-instance confirmada por el owner, persiste de verdad. Cada test que
# guarda usa un repositorio AISLADO (tmp_path, nunca el seed/runtime real)
# vía `_isolated_repo` — sin eso, un save real de un test contaminaría el
# resto (incluidos los de lectura de arriba, que leen el singleton global).
# ============================================================================

def test_write_endpoint_exists_and_enforces_auth_before_anything_else():
    assert "@router.post" in _STRATEGY_PY
    repo_text = (Path(__file__).resolve().parents[2] / "webapp" / "services" /
                 "commercial_strategy_repository.py").read_text(encoding="utf-8")
    assert "def save_strategy" in repo_text


def _isolated_repo(tmp_path, monkeypatch):
    """Reemplaza el singleton `_repository` del módulo por uno nuevo
    apuntando a `tmp_path` (seed real, runtime path descartable) — el
    router lee `_repository` por nombre en cada call, así que esto alcanza
    sin tocar nada más. `monkeypatch` revierte solo al terminar el test."""
    from webapp.services import commercial_strategy_repository as repo_module
    seed_path = Path(__file__).resolve().parents[2] / "webapp" / "services" / "commercial_strategy_data" / "strategy.yaml"
    fresh = repo_module.CommercialStrategyRepository(seed_path=seed_path, runtime_path=tmp_path / "strategy.yaml")
    monkeypatch.setattr(repo_module, "_repository", fresh)
    return fresh


def _save_payload(revision: int, **overrides) -> dict:
    # "COMPLETE VISIBLE BUSINESS VALUE EDITABILITY" gate: TODO
    # `EDITABLE_TOP_KEYS` viaja completo (mismo criterio que
    # `_editable_doc` del router — manda la sección canónica entera, el
    # validador server-side es la única frontera real, ver
    # commercial_strategy_editing.py). Los tests que solo quieren tocar UN
    # campo usan `overrides` para reemplazar una sección puntual (p. ej.
    # `_save_payload(1, hero={**base_hero, "kicker": "..."})`).
    import yaml
    from webapp.services import commercial_strategy_editing as editing
    seed_path = Path(__file__).resolve().parents[2] / "webapp" / "services" / "commercial_strategy_data" / "strategy.yaml"
    doc = yaml.safe_load(seed_path.read_text(encoding="utf-8"))
    payload = {"revision": revision}
    for key in editing.EDITABLE_TOP_KEYS:
        payload[key] = doc[key]
    payload.update(overrides)
    return payload


def _owner_post(payload: dict):
    return owner_client.post("/comercial/estrategia", json=payload, headers={"Origin": "http://testserver"})


def test_prose_edit_save_reload_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["hero"]["lead"] = "Nueva bajada editada en el test."
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    assert res.json()["document"]["hero"]["lead"] == "Nueva bajada editada en el test."
    from webapp.services.commercial_strategy_repository import load_strategy
    assert load_strategy()["hero"]["lead"] == "Nueva bajada editada en el test."
    assert load_strategy()["revision"] == 2


def test_get_never_mutates_the_canonical_document(tmp_path, monkeypatch):
    # "Cancelar" es 100% client-side (Alpine descarta `doc` y vuelve a copiar
    # `original`) — lo único verificable server-side es que un GET repetido
    # nunca cambia nada por sí mismo.
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    from webapp.services.commercial_strategy_repository import load_strategy
    before = load_strategy()
    owner_client.get("/comercial/estrategia")
    assert load_strategy() == before


def test_task_edit_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["next_steps"]["tasks"][0]["tarea"] = "Tarea editada"
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    assert load_strategy()["next_steps"]["tasks"][0]["tarea"] == "Tarea editada"


def test_task_add_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    before_count = len(payload["next_steps"]["tasks"])
    payload["next_steps"]["tasks"].append(
        {"id": "new-1", "tarea": "Nueva tarea", "origen": "Test", "responsable": "QA", "q": "Q1"}
    )
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    tasks = load_strategy()["next_steps"]["tasks"]
    assert len(tasks) == before_count + 1
    assert tasks[-1]["tarea"] == "Nueva tarea"


def test_task_delete_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    removed_id = payload["next_steps"]["tasks"].pop(0)["id"]
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    ids = [t["id"] for t in load_strategy()["next_steps"]["tasks"]]
    assert removed_id not in ids


def test_task_reorder_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    tasks = payload["next_steps"]["tasks"]
    first_id, second_id = tasks[0]["id"], tasks[1]["id"]
    tasks[0], tasks[1] = tasks[1], tasks[0]
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    reloaded = load_strategy()["next_steps"]["tasks"]
    assert reloaded[0]["id"] == second_id
    assert reloaded[1]["id"] == first_id


# ============================================================================
# Mapa de contactos (annex_map) — release blocker del gate "PIVOT EDIT UX
# TO INLINE STRUCTURED EDITING": nombre/rol editable en el árbol, agregar/
# eliminar/reordenar nodo, notas CRUD.
# ============================================================================

def test_contact_map_root_and_branch_name_role_edit_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["annex_map"]["sap_tree"]["root"]["name"] = "SAP Editado"
    payload["annex_map"]["sap_tree"]["branches"][0]["role"] = "Resp: Editado en el test"
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    saved = load_strategy()["annex_map"]["sap_tree"]
    assert saved["root"]["name"] == "SAP Editado"
    assert saved["branches"][0]["role"] == "Resp: Editado en el test"


def test_contact_map_nested_child_desc_and_sellers_edit_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    child = payload["annex_map"]["sap_tree"]["branches"][1]["children"][0]  # Corporate -> C-Field
    child["desc"] = "Descripción editada"
    child["sellers"] = "Vendedores editados"
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    saved_child = load_strategy()["annex_map"]["sap_tree"]["branches"][1]["children"][0]
    assert saved_child["desc"] == "Descripción editada"
    assert saved_child["sellers"] == "Vendedores editados"


def test_contact_map_add_node_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    before_count = len(payload["annex_map"]["lob_tree"]["branches"])
    payload["annex_map"]["lob_tree"]["branches"].append(
        {"name": "Nueva rama", "role": "Resp: Test", "desc": "", "sellers": "", "highlight": False, "children": []}
    )
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    branches = load_strategy()["annex_map"]["lob_tree"]["branches"]
    assert len(branches) == before_count + 1
    assert branches[-1]["name"] == "Nueva rama"


def test_contact_map_delete_node_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    removed_name = payload["annex_map"]["lob_tree"]["branches"].pop(0)["name"]
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    names = [b["name"] for b in load_strategy()["annex_map"]["lob_tree"]["branches"]]
    assert removed_name not in names


def test_contact_map_reorder_node_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    branches = payload["annex_map"]["sap_tree"]["branches"]
    first_name, second_name = branches[0]["name"], branches[1]["name"]
    branches[0], branches[1] = branches[1], branches[0]
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    reloaded = load_strategy()["annex_map"]["sap_tree"]["branches"]
    assert reloaded[0]["name"] == second_name
    assert reloaded[1]["name"] == first_name


def test_contact_map_note_add_edit_delete_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["annex_map"]["notes"][0]["title"] = "Título editado"
    payload["annex_map"]["notes"].append({"title": "Nota nueva", "text": "Texto nuevo"})
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    notes = load_strategy()["annex_map"]["notes"]
    assert notes[0]["title"] == "Título editado"
    assert notes[-1] == {"title": "Nota nueva", "text": "Texto nuevo"}


def _leaf_node(name: str) -> dict:
    return {"name": name, "role": "x", "desc": "", "sellers": "", "highlight": False}


def test_contact_map_tree_depth_is_bounded_against_adversarial_nesting(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    root_node = _leaf_node("d0")
    cursor = root_node
    for i in range(1, 10):
        child = _leaf_node(f"d{i}")
        cursor["children"] = [child]
        cursor = child
    payload["annex_map"]["sap_tree"]["branches"][0]["children"] = [root_node]
    res = _owner_post(payload)
    assert res.status_code == 422


def test_contact_map_only_editable_fields_change_non_editable_annex_map_metadata_survives(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    fresh = _isolated_repo(tmp_path, monkeypatch)
    before = fresh.load()
    payload = _save_payload(1)
    payload["annex_map"]["sap_tree"]["root"]["name"] = "SAP Editado"
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    after = load_strategy()
    assert after["annex_map"]["eyebrow"] == before["annex_map"]["eyebrow"]
    assert after["annex_map"]["title"] == before["annex_map"]["title"]
    assert after["annex_map"]["intro"] == before["annex_map"]["intro"]
    assert after["annex_map"]["lob_intro"] == before["annex_map"]["lob_intro"]


# ============================================================================
# "COMPLETE VISIBLE BUSINESS VALUE EDITABILITY" gate — tests A-J requeridos
# por los hallazgos de validación manual del owner (ver HANDOFF): tabla de
# economía del ticket, tabla de escenarios/sensibilidad, funnel, y una
# muestra de las demás tablas/secciones que antes quedaban de solo lectura.
# ============================================================================

def test_a_ticket_table_minimum_row_edit_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["objectives"]["ticket_table"]["rows"][0] = ["Mínimo", "USD 21K", "USD 63K"]
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    saved_rows = load_strategy()["objectives"]["ticket_table"]["rows"]
    assert saved_rows[0] == ["Mínimo", "USD 21K", "USD 63K"]


def test_b_ticket_table_add_row_persists_and_headers_survive(tmp_path, monkeypatch):
    # También cubre "otra celda de tabla editable" (item B) y confirma que
    # `headers` (estructural, ver commercial_strategy_editing._table) NUNCA
    # se pierde ni se acepta del cliente — sigue siendo el mismo que el
    # canónico después de agregar una fila.
    _with_owner(monkeypatch)
    fresh = _isolated_repo(tmp_path, monkeypatch)
    before_headers = fresh.load()["objectives"]["ticket_table"]["headers"]
    payload = _save_payload(1)
    payload["objectives"]["ticket_table"]["rows"].append(["Enterprise (ej.)", "USD 80K", "USD 240K"])
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    saved = load_strategy()["objectives"]["ticket_table"]
    assert saved["rows"][-1] == ["Enterprise (ej.)", "USD 80K", "USD 240K"]
    assert saved["headers"] == before_headers


def test_c_ticket_table_computed_column_is_canonical_input_not_silently_recalculated(tmp_path, monkeypatch):
    # Clasificación explícita (ver docstring de commercial_strategy_editing.py):
    # la columna "Computado (×3)" NO es derivada — no existe una fórmula
    # implementada en código en ningún lado del Hub, es contenido humano
    # mantenido a mano. Este test prueba esa clasificación de forma
    # explícita: editarla directamente persiste el valor EXACTO que mandó
    # el cliente — el servidor NUNCA la recalcula ni la sobrescribe.
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    # Valor deliberadamente NO consistente con ninguna fórmula ×3 — si el
    # servidor "corrigiera" esto de forma silenciosa, este test fallaría.
    payload["objectives"]["ticket_table"]["rows"][0][2] = "USD 999K (valor arbitrario del test)"
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    assert load_strategy()["objectives"]["ticket_table"]["rows"][0][2] == "USD 999K (valor arbitrario del test)"


def test_d_sensitivity_scenario_table_business_cell_edit_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["objectives"]["sensitivity_table"]["rows"][0][0] = "Todo al mínimo (editado por el test)"
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    assert load_strategy()["objectives"]["sensitivity_table"]["rows"][0][0] == "Todo al mínimo (editado por el test)"


def test_e_funnel_stage_label_edit_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["objectives"]["funnel"][0]["label"] = "Pipeline a generar — editado por el test"
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    assert load_strategy()["objectives"]["funnel"][0]["label"] == "Pipeline a generar — editado por el test"


def test_f_funnel_stage_business_value_edit_persists_and_level_stays_positional(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["objectives"]["funnel"][2]["value"] = "USD 999K (test)"
    payload["objectives"]["funnel"][2]["level"] = 999  # el cliente NUNCA controla level — estructural/posicional
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    saved_funnel = load_strategy()["objectives"]["funnel"]
    assert saved_funnel[2]["value"] == "USD 999K (test)"
    assert saved_funnel[2]["level"] == 3  # posición real (1-indexed), nunca el 999 que mandó el payload


def test_g_capacity_assignments_table_cell_edit_persists(tmp_path, monkeypatch):
    # "Otra tabla que antes quedaba de solo lectura" (item G) — capacity
    # era código+seed antes de este gate.
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["capacity"]["assignments_table"]["rows"][0][1] = "Responsable editado por el test"
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    assert load_strategy()["capacity"]["assignments_table"]["rows"][0][1] == "Responsable editado por el test"


def test_g2_events_catalog_table_cell_edit_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["demand_generation"]["events_catalog"]["rows"][0][1] = "Objetivo editado por el test"
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    assert load_strategy()["demand_generation"]["events_catalog"]["rows"][0][1] == "Objetivo editado por el test"


def test_i_failed_save_due_to_stale_revision_preserves_the_full_complete_document(tmp_path, monkeypatch):
    # "Failed Save preserves the complete inline draft" (item I) — a nivel
    # server, lo que se puede probar es que un Save rechazado (revision
    # vieja) no persiste NADA, ni siquiera parcialmente, y el documento
    # completo (todas las 13 secciones editables) queda exactamente como
    # estaba. El draft del cliente en sí vive en Alpine (`doc` nunca se
    # descarta en el camino de error — ver comentario del x-data en
    # strategy.html); acá se prueba el lado server del contrato.
    _with_owner(monkeypatch)
    fresh = _isolated_repo(tmp_path, monkeypatch)
    before = fresh.load()
    payload = _save_payload(1)
    payload["objectives"]["ticket_table"]["rows"][0][1] = "Este cambio NO debería persistir"
    payload["capacity"]["priority_rules"][0] = "Este otro tampoco"
    # Alguien más ya guardó primero -> revision actual ya no es 1.
    _owner_post(_save_payload(1))
    stale_res = _owner_post(payload)
    assert stale_res.status_code == 409
    after = fresh.load()
    assert after["objectives"]["ticket_table"] == before["objectives"]["ticket_table"]
    assert after["capacity"]["priority_rules"] == before["capacity"]["priority_rules"]


# ============================================================================
# SAVE / BEFOREUNLOAD BUG gate (ver HANDOFF) — el `save()`/`beforeunload`
# reales viven en JS dentro del template, no en Python; el test riguroso
# EJECUTA ese código extraído en vivo del archivo fuente (nunca una copia
# pegada) contra mocks de fetch/window — ver ese archivo. Este wrapper solo
# lo corre como subproceso para que `pytest tests/` lo cubra también.
# ============================================================================

def test_save_lifecycle_node_suite_passes():
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node no está disponible en este entorno")
    script = Path(__file__).resolve().parent / "test_commercial_strategy_save_lifecycle.node.js"
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_j_non_owner_sees_none_of_the_new_section_editing_controls():
    # Los NOMBRES de función viven en el `x-data` incondicional (inerte sin
    # botón que los invoque — ver docstring del bloque de edición) — lo que
    # importa para "no editing chrome" es que ninguna afordancia VISIBLE
    # (clase CSS gateada por `{% if can_edit %}`) llegue al HTML.
    text = client.get("/comercial/estrategia").text
    for banned in ("cs-table-edit", "cs-object-list-edit", "cs-annex-add", "cs-segment-edit", "cs-list-edit"):
        assert banned not in text, banned


def test_invalid_enum_rejected_atomically(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    from webapp.services.commercial_strategy_repository import load_strategy
    before = load_strategy()
    payload = _save_payload(1)
    payload["next_steps"]["tasks"][0]["q"] = "Q9"
    payload["hero"]["lead"] = "Esto NO debería persistir"
    res = _owner_post(payload)
    assert res.status_code == 422
    assert load_strategy() == before  # nada parcial se escribió


def test_stale_revision_returns_409_without_overwriting(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["hero"]["lead"] = "Primer guardado"
    res1 = _owner_post(payload)
    assert res1.status_code == 200, res1.text

    stale_payload = _save_payload(1)  # misma revision vieja, como si nunca hubiera visto el primer guardado
    stale_payload["hero"]["lead"] = "Segundo guardado, revision vieja"
    res2 = _owner_post(stale_payload)
    assert res2.status_code == 409
    body = res2.json()
    assert body["state"] == "stale"
    assert body["current_revision"] == 2

    from webapp.services.commercial_strategy_repository import load_strategy
    assert load_strategy()["hero"]["lead"] == "Primer guardado"


def test_durable_push_failure_reports_error_and_preserves_previous_canonical(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    from webapp.services import commercial_strategy_repository as repo_module
    monkeypatch.setattr(repo_module.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo_module.remote_store, "push_path", lambda path: False)

    from webapp.services.commercial_strategy_repository import load_strategy
    before = load_strategy()
    payload = _save_payload(1)
    payload["hero"]["lead"] = "Esto no debería sobrevivir al push fallido"
    res = _owner_post(payload)
    assert res.status_code == 503
    body = res.json()
    assert body["state"] == "unavailable"
    assert body["reason"] == "durable_push_failed"

    after = load_strategy()
    assert after["hero"]["lead"] == before["hero"]["lead"]
    assert after["revision"] == before["revision"] == 1


def test_retry_after_push_failure_succeeds(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    from webapp.services import commercial_strategy_repository as repo_module
    monkeypatch.setattr(repo_module.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo_module.remote_store, "push_path", lambda path: False)

    payload = _save_payload(1)
    payload["hero"]["lead"] = "Intento fallido"
    res = _owner_post(payload)
    assert res.status_code == 503

    # El intento fallido NUNCA incrementó la revision — el reintento sigue
    # siendo válido con revision=1 una vez que el push "empieza a andar".
    monkeypatch.setattr(repo_module.remote_store, "push_path", lambda path: True)
    payload2 = _save_payload(1)
    payload2["hero"]["lead"] = "Reintento exitoso"
    res2 = _owner_post(payload2)
    assert res2.status_code == 200, res2.text
    from webapp.services.commercial_strategy_repository import load_strategy
    assert load_strategy()["hero"]["lead"] == "Reintento exitoso"
    assert load_strategy()["revision"] == 2


def test_two_consecutive_successful_saves_are_both_visible_same_process_no_restart(tmp_path, monkeypatch):
    # Repository Cache Semantics Gate (ver HANDOFF): `load()` devuelve
    # `copy.deepcopy(self._cache)` — nunca relee disco — y `save()` reescribe
    # `self._cache` in-memory bajo el mismo RLock ANTES de retornar (ver
    # commercial_strategy_repository.py). Los tests de arriba ya prueban
    # save→load una vez (`test_prose_edit_save_reload_persists`) y
    # fail→retry→load (`test_retry_after_push_failure_succeeds`), pero
    # ninguno encadena DOS saves exitosos consecutivos (N -> N+1 -> N+2) en
    # el mismo proceso sin reiniciar — este test cierra exactamente ese
    # hueco, la prueba explícita que pidió el gate.
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    from webapp.services.commercial_strategy_repository import load_strategy

    assert load_strategy()["revision"] == 1

    payload_1 = _save_payload(1)
    payload_1["hero"]["lead"] = "Primer guardado exitoso"
    res_1 = _owner_post(payload_1)
    assert res_1.status_code == 200, res_1.text
    assert load_strategy()["hero"]["lead"] == "Primer guardado exitoso"
    assert load_strategy()["revision"] == 2

    payload_2 = _save_payload(2)
    payload_2["hero"]["lead"] = "Segundo guardado exitoso"
    res_2 = _owner_post(payload_2)
    assert res_2.status_code == 200, res_2.text
    assert load_strategy()["hero"]["lead"] == "Segundo guardado exitoso"
    assert load_strategy()["revision"] == 3


def test_malicious_markup_inert_on_reload_and_render(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["hero"]["lead"] = '<script>alert(1)</script> normal text <img src=x onerror="alert(2)">'
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    rendered = owner_client.get("/comercial/estrategia").text
    # Nunca una forma VIVA/ejecutable — ni como tag real ni como atributo real.
    # (La subcadena "onerror=" SÍ aparece en la página de forma inerte, dos
    # veces: dentro del JSON de `editable_doc` que precarga el formulario de
    # edición — Alpine lo asigna via `.value`, nunca `innerHTML` — y dentro
    # del texto escapado en la vista `onerror=&quot;...&quot;`. Ninguna de las
    # dos es un atributo real de un tag real, así que buscar la subcadena
    # sola daría falsos positivos — lo que importa es que NO exista como
    # atributo HTML vivo.)
    assert "<script>alert(1)</script>" not in rendered
    assert "<img src=x onerror=" not in rendered
    assert "&lt;script&gt;" in rendered  # el texto sobrevive, escapado — nunca ejecutable
    assert "&lt;img src=x onerror=" in rendered  # ídem, en la vista de lectura


def test_double_quote_in_edited_content_cannot_break_out_of_the_edit_state_attribute(tmp_path, monkeypatch):
    # Bug REAL encontrado por este módulo de tests: `{{ editable_doc|tojson }}`
    # escapa comillas SIMPLES pero no DOBLES — embebido antes directo dentro
    # de `x-data="..."` (atributo de comillas dobles, necesario porque el
    # resto de la expresión Alpine usa `'...'` sin escapar), un campo
    # editable con una comilla doble cerraba el atributo antes de tiempo.
    # Fix: el JSON vive en un <script type="application/json"> inerte, nunca
    # inline en el atributo — este test prueba el síntoma concreto que
    # habría delatado una regresión: la comilla doble del payload nunca debe
    # aparecer SIN escapar dentro de `x-data="`.
    import re
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["hero"]["lead"] = 'texto con "comillas dobles" adentro'
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    rendered = owner_client.get("/comercial/estrategia").text
    assert 'id="cs-editable-doc"' in rendered
    # El payload SÍ debe aparecer (sin escapar HTML) dentro del <script> inerte...
    script_body = re.search(r'id="cs-editable-doc">(.*?)</script>', rendered, re.S).group(1)
    assert "comillas dobles" in script_body
    # ...pero NUNCA sin escapar dentro de ningún atributo `x-data="..."` — si
    # el bug volviera, la comilla del payload truncaría ese atributo antes de
    # tiempo y esta búsqueda (no-greedy hasta la PRÓXIMA comilla real)
    # capturaría un fragmento roto en vez del JS completo.
    for attr_body in re.findall(r'x-data="(.*?)"', rendered, re.S):
        assert "comillas dobles" not in attr_body


def test_same_origin_content_owner_write_succeeds(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    res = _owner_post(_save_payload(1))
    assert res.status_code == 200


def test_non_content_owner_write_returns_403_even_with_valid_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "")
    _isolated_repo(tmp_path, monkeypatch)
    res = client.post("/comercial/estrategia", json=_save_payload(1), headers={"Origin": "http://testserver"})
    assert res.status_code == 403


def test_empty_content_owner_write_fails_closed_with_valid_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "")
    _isolated_repo(tmp_path, monkeypatch)
    res = owner_client.post("/comercial/estrategia", json=_save_payload(1), headers={"Origin": "http://testserver"})
    assert res.status_code == 403


def test_save_preserves_non_editable_sibling_fields_within_a_saved_section(tmp_path, monkeypatch):
    # Bug REAL encontrado durante el gate de edición inline (antes de este
    # test): `save()` hacía `candidate.update(updated_fields)` a nivel
    # tope — como cada validador de commercial_strategy_editing devuelve
    # SOLO las claves editables de una sección (p. ej. `objectives` sin
    # `eyebrow`/`title`/`intro`/`ticket_table`/`funnel`), ese `.update()`
    # reemplazaba el dict ENTERO de la sección, borrando esas claves
    # código+seed en cada Save real — nunca detectado porque ningún test
    # anterior recargaba esos campos específicos tras un save. Fix: merge
    # de un nivel en `save()` (ver commercial_strategy_repository.py).
    _with_owner(monkeypatch)
    fresh = _isolated_repo(tmp_path, monkeypatch)
    before = fresh.load()
    assert before["objectives"]["eyebrow"]  # confirma que el seed realmente lo trae
    assert "ticket_table" in before["objectives"]
    assert "funnel" in before["objectives"]

    payload = _save_payload(1)
    payload["objectives"]["coverage_note"] = "Coverage note editada por el test"
    res = _owner_post(payload)
    assert res.status_code == 200, res.text

    from webapp.services.commercial_strategy_repository import load_strategy
    after = load_strategy()
    assert after["objectives"]["coverage_note"] == "Coverage note editada por el test"
    assert after["objectives"]["eyebrow"] == before["objectives"]["eyebrow"]
    assert after["objectives"]["title"] == before["objectives"]["title"]
    assert after["objectives"]["intro"] == before["objectives"]["intro"]
    assert after["objectives"]["ticket_table"] == before["objectives"]["ticket_table"]
    assert after["objectives"]["funnel"] == before["objectives"]["funnel"]
    # hero y next_steps también son secciones parcialmente editables —
    # mismo invariante.
    assert "eyebrow" not in before["hero"] or after["hero"].get("eyebrow") == before["hero"].get("eyebrow")
    assert after["next_steps"]["eyebrow"] == before["next_steps"]["eyebrow"]
    assert after["next_steps"]["intro"] == before["next_steps"]["intro"]


def test_repository_reload_sees_durable_saved_document(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    fresh = _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["hero"]["kicker"] = "Kicker persistido"
    res = _owner_post(payload)
    assert res.status_code == 200, res.text

    from webapp.services import commercial_strategy_repository as repo_module
    reloaded_repo = repo_module.CommercialStrategyRepository(seed_path=fresh._seed_path, runtime_path=fresh._runtime_path)
    reloaded = reloaded_repo.load()
    assert reloaded["hero"]["kicker"] == "Kicker persistido"
    assert reloaded["revision"] == 2


def test_successful_save_increments_revision_exactly_once(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    res = _owner_post(_save_payload(1))
    assert res.json()["document"]["revision"] == 2


def test_failed_save_does_not_increment_revision(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["next_steps"]["tasks"][0]["q"] = "not-a-real-value"
    res = _owner_post(payload)
    assert res.status_code == 422
    from webapp.services.commercial_strategy_repository import load_strategy
    assert load_strategy()["revision"] == 1


# ============================================================================
# V — Un write forjado cross-site-style (sin Origin/Referer del mismo
# origen) falla el guard, incluso siendo CONTENT_OWNER.
# ============================================================================

def test_forged_cross_site_style_write_fails_the_same_origin_guard(monkeypatch):
    _with_owner(monkeypatch)
    response = owner_client.post(
        "/comercial/estrategia", json={}, headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_write_with_no_origin_or_referer_fails_closed(monkeypatch):
    _with_owner(monkeypatch)
    response = owner_client.post("/comercial/estrategia", json={}, headers={"Origin": "", "Referer": ""})
    assert response.status_code == 403


# ============================================================================
# M — La capability CRM (01) sigue intacta
# ============================================================================

def test_crm_capability_unaffected():
    response = client.get("/comercial")
    assert response.status_code == 200
    assert "<h1>Dashboard CRM</h1>" in response.text
    assert 'href="/comercial" class="module-link is-active"' in response.text


def test_nav_partial_lists_both_capabilities_in_order():
    idx_crm = _NAV_HTML.index('href="/comercial"')
    idx_strategy = _NAV_HTML.index('href="/comercial/estrategia"')
    assert idx_crm < idx_strategy


# ============================================================================
# Hub standardization pass — feedback de sección REUSA el primitivo
# transversal de Copilot (partials/section_feedback.html, tools.css
# .section-feedback*, feedback_store/type="section_content", evento
# "section.feedback") — nunca un componente/endpoint/storage nuevo.
# ============================================================================

def _strategy_feedback_sections() -> tuple[str, ...]:
    from webapp.routers.commercial_strategy import _FEEDBACK_SECTIONS
    return _FEEDBACK_SECTIONS


# C/D — cada sección prevista renderiza el primitivo compartido, con la
# identidad TÉCNICA estable (las claves del propio modelo de datos), nunca
# el id de ancla HTML (p1/genda/...) ni el título mutable.
def test_every_intended_section_renders_the_shared_feedback_primitive():
    text = client.get("/comercial/estrategia").text
    sections = _strategy_feedback_sections()
    assert len(sections) == 12
    for section_id in sections:
        assert f'id="section-feedback-{section_id}"' in text, section_id
        assert f'hx-post="/comercial/estrategia/sections/{section_id}/feedback"' in text, section_id


def test_feedback_identities_are_stable_model_keys_not_anchor_ids_or_titles():
    sections = _strategy_feedback_sections()
    # Las claves TÉCNICAS del propio modelo — nunca los anchors de sección
    # (p1/genda/p2/...) ni una traducción de los títulos visibles.
    for banned_anchor in ("p1", "genda", "p2", "p3", "p4", "p5", "p6", "mapa", "roles", "proceso", "reglas"):
        assert banned_anchor not in sections
    assert set(sections) == {
        "objectives", "demand_generation", "segments", "focus", "capacity",
        "cadence", "exclusions", "next_steps", "annex_map", "annex_roles",
        "annex_process", "annex_rules",
    }


# E — reusa feedback_store/FEEDBACK_LOG_DIR y el MISMO type="section_content"
# — nunca un directorio/formato/tipo de evento nuevo.
def test_no_duplicate_feedback_storage_or_event_taxonomy():
    assert "FEEDBACK_LOG_DIR" in _STRATEGY_PY
    assert '"type": "section_content"' in _STRATEGY_PY
    assert '"section.feedback"' in _STRATEGY_PY
    for banned in ("STRATEGY_FEEDBACK_LOG_DIR", "type\": \"strategy_section", "commercial_strategy_feedback_store"):
        assert banned not in _STRATEGY_PY, banned


# F — el request sigue el MISMO contrato que Copilot: Form-encoded, HTMX,
# re-renderiza el MISMO parcial.
def test_section_feedback_follows_the_existing_hub_htmx_form_contract(monkeypatch):
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "commercial-test@danwor.com")
    response = client.post(
        "/comercial/estrategia/sections/objectives/feedback",
        data={"rating": "up", "pack": "datasphere_core"},
    )
    assert response.status_code == 200
    assert 'id="section-feedback-objectives"' in response.text
    assert "&#128077;" in response.text or "👍" in response.text  # ¡Gracias! con el emoji correspondiente


def test_section_feedback_down_shows_optional_comment_form():
    response = client.post("/comercial/estrategia/sections/objectives/feedback", data={"rating": "down"})
    assert response.status_code == 200
    assert 'hx-post="/comercial/estrategia/sections/objectives/feedback/submit"' in response.text


def test_section_feedback_cancel_returns_to_the_bar():
    response = client.post("/comercial/estrategia/sections/objectives/feedback/cancel")
    assert response.status_code == 200
    assert 'class="section-feedback__bar"' in response.text


def test_section_feedback_unknown_section_is_404():
    response = client.post("/comercial/estrategia/sections/not-a-real-section/feedback", data={"rating": "up"})
    assert response.status_code == 404


# G/H — headers de sección reusan el primitivo real de Copilot; CERO regla
# de heading propia de Estrategia sobrevive.
def test_section_headers_reuse_the_exact_copilot_heading_markup():
    text = client.get("/comercial/estrategia").text
    assert text.count('<header class="content-heading"><div class="content-heading-copy"><span class="section-eyebrow">') == 12


def test_no_strategy_specific_heading_css_remains():
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial_strategy.css").read_text(encoding="utf-8")
    for banned in (".content-heading", ".section-eyebrow", " h2 ", "h2{", "h2 {"):
        assert banned not in css, banned


# I — CONTENT_OWNER es IRRELEVANTE para el feedback de sección — cualquier
# usuario autorizado a Comercial puede reaccionar, sin ser editor.
def test_content_owner_irrelevant_to_ordinary_section_feedback(monkeypatch):
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "")  # nadie es editor
    response = client.post("/comercial/estrategia/sections/objectives/feedback", data={"rating": "up"})
    assert response.status_code == 200  # feedback funciona igual sin ningún CONTENT_OWNER configurado


def test_section_feedback_route_never_checks_can_edit_strategy_user():
    # `can_edit_strategy_user` SÍ aparece en la PROSA del bloque (explicando
    # por qué NO se usa acá) — lo que este test protege es que nunca se
    # LLAME como código real dentro de las rutas de feedback.
    feedback_block = _STRATEGY_PY.split("# ── Feedback por sección")[1]
    assert "can_edit_strategy_user(" not in feedback_block
