"""
test_human_capital_visual_corrective.py — Capital Humano, corrección visual
del "Banco de Perfiles" (ver HANDOFF). Root cause real (confirmado leyendo
CSS, no adivinado): `.feedback-field label` (tools.css) fija
`color: var(--ink)` sin excepción -- un token pensado EXCLUSIVAMENTE para el
panel lateral/wizard de diagnóstico, que son theme-invariant (siempre
oscuros, ver comentario de `.module-nav` en tools.css). Capital Humano es el
PRIMER consumidor que coloca `.feedback-field` dentro de `.module-content`,
que SÍ cambia de tema -- de ahí el texto casi invisible en tema claro
reportado en vivo.

Fix: (1) override del token en el primitivo compartido (tools.css, beneficia
a cualquier consumidor futuro); (2) hoja de estilos propia del workspace
(`human_capital.css`, MISMO mecanismo que commercial.css/reporting.css) para
lo que es genuinamente nuevo -- densidad/composición de un form CRUD denso,
sin precedente compartido en el resto del Hub.

Cero cambio de repositorios/servicios/rutas/Graph -- sólo CSS + <link> en
templates. Ver test_profile_document_repository.py/test_profile_document_
service.py/test_human_capital_profile_documents_routes.py para el contrato
funcional del delete (commit separado, sin tocar acá)."""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_TOOLS_CSS = (_ROOT / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
_HC_CSS_PATH = _ROOT / "webapp" / "static" / "human_capital.css"
_TEMPLATES_DIR = _ROOT / "webapp" / "templates" / "human_capital"

_HC_PAGES = ["profiles.html", "profile_edit.html", "profile_import.html",
             "profile_import_preview.html", "profile_import_result.html"]


def _rule_body(css: str, selector_prefix: str) -> str:
    idx = css.index(selector_prefix)
    return css[idx: css.index("}", idx) + 1]


# ============================================================================
# Root cause: `.feedback-field label` ahora tiene override de tema claro
# ============================================================================

def test_feedback_field_label_has_a_light_theme_color_override():
    body = _rule_body(_TOOLS_CSS, ".content-theme-light .feedback-field label {")
    assert "color:" in body
    assert "var(--ink)" not in body, "el override tiene que ser un color CONCRETO, no el mismo token oscuro"


def test_feedback_field_label_dark_theme_token_is_untouched():
    # El fix es ADITIVO -- el consumidor histórico (wizard/panel lateral,
    # siempre oscuro) sigue viendo EXACTAMENTE lo mismo que antes.
    body = _rule_body(_TOOLS_CSS, ".feedback-field label {")
    assert "color: var(--ink)" in body


# ============================================================================
# human_capital.css existe y sigue el MISMO mecanismo que commercial.css/
# reporting.css (archivo propio por workspace, linkeado por cada página)
# ============================================================================

def test_human_capital_css_file_exists_and_is_not_trivial():
    assert _HC_CSS_PATH.exists()
    css = _HC_CSS_PATH.read_text(encoding="utf-8")
    assert len(css) > 500


def test_every_human_capital_page_links_its_own_stylesheet():
    for page in _HC_PAGES:
        html = (_TEMPLATES_DIR / page).read_text(encoding="utf-8")
        assert "human_capital.css" in html, page
        assert '<link rel="stylesheet"' in html, page


def test_human_capital_css_never_touches_other_workspace_selectors():
    # Blast radius acotado -- cero regla que pise Comercial/Reporting/
    # Operaciones (los archivos de esos workspaces son la fuente de verdad
    # de sus propios selectores, human_capital.css no debe redeclararlos).
    css = _HC_CSS_PATH.read_text(encoding="utf-8")
    for banned in (".commercial-", ".staffing-", ".reporting-", "#hc-import"):
        assert banned not in css, banned


# ============================================================================
# Form CRUD denso: grilla + look de campo consistente con .whatsnew-filter
# (Comercial/What's New) -- reuso deliberado, nunca una paleta nueva.
# ============================================================================

def test_profile_form_uses_a_responsive_grid_not_a_stacked_column():
    css = _HC_CSS_PATH.read_text(encoding="utf-8")
    body = _rule_body(css, ".hc-profile-form {")
    assert "display: grid" in body
    assert "auto-fill" in body or "auto-fit" in body, "columnas responsive, nunca un ancho fijo"


def test_profile_form_inputs_have_explicit_borders_and_theme_aware_color():
    css = _HC_CSS_PATH.read_text(encoding="utf-8")
    assert "border: 1px solid rgba(128,128,128,.35)" in css, "MISMO borde que .whatsnew-filter, no uno nuevo"
    assert ".content-theme-dark .hc-profile-form input" in css
    assert ":focus" in css, "estado de foco explícito, ver requisito de accesibilidad"


def test_profile_form_checkboxes_are_excluded_from_the_text_field_look():
    # profile_import_preview.html reusa `.hc-profile-form` para envolver la
    # tabla de vista previa CON checkboxes por fila -- esos NUNCA deben
    # heredar el look de campo de texto (ancho 100%, borde, padding).
    css = _HC_CSS_PATH.read_text(encoding="utf-8")
    body = _rule_body(css, ".hc-profile-form input:not([type=\"hidden\"]):not([type=\"checkbox\"]):not([type=\"radio\"]):not([type=\"file\"]),")
    assert 'not([type="checkbox"])' in body


def test_import_preview_table_spans_the_full_form_grid_width():
    # `.hc-profile-form` también envuelve `.matrix-scroll` en
    # profile_import_preview.html -- sin esto, la tabla quedaría constreñida
    # a una sola celda de ~220px de la grilla.
    css = _HC_CSS_PATH.read_text(encoding="utf-8")
    assert ".hc-profile-form > .matrix-scroll" in css


# ============================================================================
# Cabecera compacta -- SOLO scoped a este archivo, la regla compartida de
# app.css sigue intacta para el resto del Hub.
# ============================================================================

def test_module_hero_reduction_lives_only_in_the_workspace_stylesheet():
    css = _HC_CSS_PATH.read_text(encoding="utf-8")
    assert ".module-hero h1" in css
    app_css = (_ROOT / "webapp" / "static" / "app.css").read_text(encoding="utf-8")
    assert "clamp(2.4rem,5vw,4.5rem)" in app_css, "la regla compartida de app.css NUNCA se tocó"


# ============================================================================
# Regression boundary: cero cambio funcional en esta corrección visual.
# ============================================================================

def test_no_backend_module_references_the_new_stylesheet_or_css_classes():
    for module in ("human_capital_service.py", "profile_repository.py",
                    "profile_document_service.py", "profile_document_repository.py"):
        src = (_ROOT / "webapp" / "services" / module).read_text(encoding="utf-8")
        assert "human_capital.css" not in src
        assert "hc-profile-form" not in src
