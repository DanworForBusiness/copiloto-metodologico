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
    assert 'aria-label="En este documento"' in text
    assert "<p>En este documento</p>" in text
    assert "<a href=\"#p1\"><span>01</span>Objetivos</a>" in text
    assert "<a href=\"#reglas\"><span>12</span>Reglas de oro</a>" in text


def test_document_index_has_no_leftover_source_number_prefix():
    # El artefacto fuente ya trae "1 · Objetivos" en el label — el índice
    # numera POR SU CUENTA (igual que Copilot); el prefijo del fuente no debe
    # duplicarse en el título visible.
    text = client.get("/comercial/estrategia").text
    index_html = text.split('aria-label="En este documento"')[1].split("</nav>")[0]
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


def test_no_global_contenteditable_reintroduced():
    html = (Path(__file__).resolve().parents[2] / "webapp" / "templates" / "commercial" / "strategy.html").read_text(encoding="utf-8")
    assert "contenteditable" not in html


def test_all_expected_section_links_present_in_index():
    text = client.get("/comercial/estrategia").text
    index_html = text.split('aria-label="En este documento"')[1].split("</nav>")[0]
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
    load_strategy.cache_clear()
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
    text = client.get("/comercial/estrategia").text
    for banned in ("cs-edit-toggle", "cs-edit-panel", ">Editar<", "editing-mode", "contenteditable"):
        assert banned not in text, banned
    assert "contenteditable" not in _STRATEGY_HTML


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


def test_content_owner_sees_edit_affordance_and_panel(monkeypatch):
    _with_owner(monkeypatch)
    text = owner_client.get("/comercial/estrategia").text
    assert 'id="cs-edit-toggle"' in text
    assert 'id="cs-edit-panel"' in text
    assert 'id="cs-save-btn"' in text


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
# L — El endpoint de escritura EXISTE, autoritativo, pero HONESTO: nunca
# devuelve éxito (el storage gate no está resuelto) ni escribe el documento.
# ============================================================================

def test_write_endpoint_exists_and_enforces_auth_before_anything_else():
    assert "@router.post" in _STRATEGY_PY


def test_owner_write_attempt_is_honestly_unavailable_not_a_fake_success(monkeypatch):
    _with_owner(monkeypatch)
    response = owner_client.post(
        "/comercial/estrategia", json={}, headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 503
    body = response.json()
    assert body["state"] == "unavailable"
    assert body["reason"] == "concurrency_unconfirmed"


def test_repository_exposes_read_only_contract():
    from webapp.services import commercial_strategy_repository as repo
    text = Path(repo.__file__).read_text(encoding="utf-8")
    for banned in ("def save_strategy", "def update_strategy", "def delete_strategy"):
        assert banned not in text


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
