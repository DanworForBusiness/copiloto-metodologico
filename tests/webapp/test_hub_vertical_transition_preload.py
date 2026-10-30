"""Preload transversal al cambiar de vertical (reusa el overlay del login). Verifica: (1) el macro del topbar carga el único
mecanismo compartido (script + hoja del overlay) y NO el brand-hero completo; (2) el JS REUTILIZA .auth-overlay/.auth-spinner y
sólo dispara en el switcher a OTRA vertical, con pageshow/bfcache y sin intervalos; (3) el visual vive en UNA sola fuente
compartida (hub_overlay.css) con colores fijos (tema-independiente), con el login trayéndola por @import y sin duplicarla; (4)
el login (landing) NO queda afectado."""
from __future__ import annotations

from pathlib import Path

from webapp import workspaces
from webapp.templating import templates

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "webapp/static/hub_transition.js").read_text(encoding="utf-8")
OVERLAY_CSS = (ROOT / "webapp/static/hub_overlay.css").read_text(encoding="utf-8")
BRAND = (ROOT / "webapp/static/brand-hero.css").read_text(encoding="utf-8")
TOPBAR = (ROOT / "webapp/templates/partials/platform_topbar.html").read_text(encoding="utf-8")


def _render_topbar():
    src = ('{% from "partials/platform_topbar.html" import platform_topbar %}'
           '{% call platform_topbar(cw, ws, "/", "", "Módulos") %}{% endcall %}')
    return templates.env.from_string(src).render(cw=workspaces.COPILOT,
                                                 ws=[workspaces.COPILOT, workspaces.COMMERCIAL])


# 1. El macro compartido carga el mecanismo único (script + hoja del overlay), SIN arrastrar el brand-hero completo
def test_topbar_loads_the_single_transition_mechanism():
    html = _render_topbar()
    assert html.count("/static/hub_transition.js") == 1
    assert 'src="/static/hub_transition.js"' in html and "defer" in html
    assert html.count("/static/hub_overlay.css") == 1                          # la hoja compartida del overlay
    # NO carga brand-hero.css completo: arrastraría su :root/html,body (colores oscuros del login) y rompería el tema claro
    assert "/static/brand-hero.css" not in html


# 2. El JS reutiliza el visual del login y dispara sólo en cambios de vertical
def test_js_reuses_login_overlay_and_targets_only_vertical_switch():
    assert "auth-overlay" in JS and "auth-spinner" in JS                       # REUSA el visual del login
    assert ".workspace-switch-menu a[href]" in JS and "workspace-switch-platform" in JS
    assert "is-active" in JS                                                    # omite la vertical actual
    assert "metaKey" in JS and "ctrlKey" in JS and "shiftKey" in JS and "button !== 0" in JS   # clicks modificados/nueva pestaña
    assert "hub-transitioning" in JS                                           # disparador propio (no toca auth-completing del login)
    assert "auth-completing" not in JS                                         # NO interfiere con el overlay de login


def test_js_ends_on_real_nav_not_arbitrary_timer():
    assert "pageshow" in JS and "persisted" in JS                              # bfcache/volver → limpia
    assert "setInterval" not in JS                                             # sin polling
    # único temporizador = watchdog de seguridad documentado (navegación cancelada), no el cierre normal
    assert "SAFETY_MS" in JS and "watchdog" in JS


# 3. El visual vive en UNA sola fuente compartida; el login la trae por @import; no se duplica; es tema-independiente
def test_overlay_visual_lives_in_one_shared_source():
    assert "body.hub-transitioning .auth-overlay[data-hub-transition]{display:flex}" in OVERLAY_CSS
    assert "prefers-reduced-motion:reduce" in OVERLAY_CSS and "[data-hub-transition] .auth-spinner{animation:none}" in OVERLAY_CSS
    assert OVERLAY_CSS.count(".auth-overlay{position:fixed") == 1
    assert OVERLAY_CSS.count("@keyframes auth-spin") == 1
    # colores LITERALES (no var()) → el overlay se ve igual que el login en cualquier tema claro/oscuro autenticado
    assert "color:#e4e4ec" in OVERLAY_CSS and "border-top-color:#e7b008" in OVERLAY_CSS
    # el login sigue teniendo el overlay PERO desde la fuente compartida (brand-hero la @importa) y NO la duplica
    assert '@import "hub_overlay.css";' in BRAND
    assert ".auth-overlay{position:fixed" not in BRAND


# 4. El login (landing) no queda afectado: sólo IMPORTA el lockup, no LLAMA al macro → no carga el script de transición
def test_login_landing_is_not_affected():
    landing = (ROOT / "webapp/templates/landing.html").read_text(encoding="utf-8")
    assert "import hub_brand_lockup" in landing
    assert "platform_topbar(" not in landing                                   # no llama al macro → no inyecta hub_transition.js
    # el disparador propio del login (auth-completing) sigue vivo en la fuente compartida, intacto
    assert "body.auth-completing .auth-overlay{display:flex}" in OVERLAY_CSS
    # el macro NO reintroduce auth-completing (su disparador es hub-transitioning)
    assert "auth-completing" not in TOPBAR.split("{% macro platform_topbar")[1]
