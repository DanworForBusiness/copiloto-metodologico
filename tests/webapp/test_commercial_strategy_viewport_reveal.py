"""
test_commercial_strategy_viewport_reveal.py — COMMERCIAL STRATEGY: FINAL
VIEWPORT / VISUAL EXPERIENCE STANDARDIZATION gate (owner gate, ver HANDOFF).

Auditoría previa (ver HANDOFF): Estrategia ya estaba conectada al ÚNICO
mecanismo de reveal-on-scroll de Hub (`copilotLayout().init()` ->
`initContentReveals()`, IntersectionObserver + `.content-reveal`/
`.is-visible`, ver app.js/content.css) -- vía el MISMO `x-data=
"copilotLayout('commercial')"` que usa CUALQUIER otra página de Hub. La
inconsistencia reportada por el owner NO era una segunda implementación
Strategy-only: era que el selector compartido (`.content-section > p/table/
ul/.callout/...`) no calzaba con la mayoría de los bloques propios de
Estrategia (grillas de KPIs, funnel, árboles, timelines, y CUALQUIER tabla/
lista envuelta en un `<div x-show="!editing">` por el toggle VIEW/EDIT) --
esos bloques nunca recibían `.content-reveal` y por eso aparecían
inmediatamente, mientras headers/párrafos/callouts (que SÍ calzan con el
selector tal cual) sí fadeaban.

Fix (smallest shared-primitive correction, ver HANDOFF):
1. `.content-section > .content-block` -- UNA entrada genérica nueva en el
   selector compartido de `initContentReveals()` (app.js) -- cualquier
   módulo (no solo Estrategia) puede optar un bloque propio marcándolo
   `.content-block`, en vez de que cada forma de contenedor nueva necesite
   su propia entrada ahí (mismo espíritu "genérico, no un hardcode por
   módulo" que las gates de Metrics/Telemetry previas de esta sesión).
2. `strategy.html` marca sus bloques VIEW-mode top-level (grillas, árboles,
   timelines, tablas/listas envueltas) con esa clase -- NUNCA sus
   contrapartes `x-show="editing"` (edición no necesita reveal decorativo).
3. Contenido interactivo (tabs/subtabs de Segmentos, todo lo edit-mode) se
   excluye A PROPÓSITO del reveal: su visibilidad la dispara un CLICK, no
   el scroll -- documentado inline en strategy.html.
4. Reconciliación de anchor-jump (`.section-index a` click, app.js,
   TRANSVERSAL) y de toggle `editing` (`$watch`, SOLO en el x-data de
   Estrategia -- ese toggle view/edit no existe en ningún otro módulo).

Cero cambios a Strategy business content/persistence/editing model/feedback
semantics/metrics/telemetry/IA de navegación -- solo hygiene de reveal.

Cubre (ver checklist del owner):
 A. Estrategia usa el primitivo compartido (misma entrada `.content-block`
    que CUALQUIER módulo podría usar, no una lista Strategy-específica)
 B. bloques equivalentes en TODAS las secciones siguen el MISMO contrato
 C. el target de un anchor jump nunca queda opacity:0 (reconciliación)
 D. el toggle `editing` reconcilia visibilidad (no retriggerea/oculta)
 E. el fragmento HTMX de feedback está fuera del mecanismo de reveal
 F. fallback sin JS/IntersectionObserver dice visible (fail-open, intacto)
 G. Copiloto no se tocó -- `.content-block` es opt-in, cero uso hoy fuera
    de Estrategia
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.services import auth_service

_ROOT = Path(__file__).resolve().parents[2]
_APP_JS = (_ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
_CONTENT_CSS = (_ROOT / "webapp" / "static" / "content.css").read_text(encoding="utf-8")
_STRATEGY_HTML_SRC = (_ROOT / "webapp" / "templates" / "commercial" / "strategy.html").read_text(encoding="utf-8")
_MODULE_HTML_SRC = (_ROOT / "webapp" / "templates" / "module.html").read_text(encoding="utf-8")
_SECTION_FEEDBACK_SRC = (_ROOT / "webapp" / "templates" / "partials" / "section_feedback.html").read_text(encoding="utf-8")

_SESSION = auth_service.create_session(
    {"email": "viewport-test@danwor.com", "name": "Viewport Test", "first_name": "Viewport", "family_name": "Test"}
)
client = TestClient(app)
client.cookies.set(auth_service.COOKIE_NAME, _SESSION)

_OWNER_SESSION = auth_service.create_session(
    {"email": "viewport-owner-test@danwor.com", "name": "Viewport Owner", "first_name": "Viewport", "family_name": "Owner"}
)
owner_client = TestClient(app)
owner_client.cookies.set(auth_service.COOKIE_NAME, _OWNER_SESSION)


def _strategy_html(monkeypatch, as_owner=False) -> str:
    # `monkeypatch` (nunca una asignación a nivel de módulo) para
    # `COMMERCIAL_ALLOWED_USERS`/`ALLOWED_USERS` -- una mutación a nivel de
    # módulo persistiría más allá de este archivo (pytest importa TODOS los
    # test_*.py antes de correr nada) y pisaría el allowlist que otros
    # archivos hermanos de Estrategia ya configuraron para sus propias
    # sesiones, rompiéndolas. `monkeypatch` revierte automático al terminar
    # cada test, sin pisar el estado de nadie más.
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "viewport-test@danwor.com,viewport-owner-test@danwor.com")
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "viewport-owner-test@danwor.com" if as_owner else "")
    c = owner_client if as_owner else client
    res = c.get("/comercial/estrategia")
    assert res.status_code == 200
    return res.text


# ============================================================================
# A — Estrategia usa el ÚNICO primitivo compartido de Hub (nunca uno propio).
# ============================================================================

def test_a_strategy_page_wires_the_shared_copilot_layout_component():
    assert 'x-data="copilotLayout(\'commercial\')"' in _STRATEGY_HTML_SRC


def test_a_shared_selector_gains_one_generic_content_block_entry():
    # La entrada nueva es GENÉRICA (`.content-block`, sin prefijo `cs-`/
    # Strategy) -- cualquier módulo puede optar sin editar este archivo.
    assert '".content-section > .content-block"' in _APP_JS


def test_a_no_second_reveal_mechanism_exists_in_strategy_css_or_html():
    # Ni un IntersectionObserver propio, ni una clase de reveal paralela
    # (`cs-reveal`/`cs-fade`/etc) -- Estrategia NUNCA reimplementa lo que
    # app.js/content.css ya proveen transversalmente.
    css_source = (_ROOT / "webapp" / "static" / "commercial_strategy.css").read_text(encoding="utf-8")
    assert "IntersectionObserver" not in css_source
    assert "new IntersectionObserver(" not in _STRATEGY_HTML_SRC
    assert not re.search(r"\bcs-reveal\b|\bcs-fade\b", css_source)


# ============================================================================
# B — Bloques equivalentes en TODAS las secciones siguen el MISMO contrato:
# cada tipo de contenedor VIEW-mode que no calza con el selector base recibe
# `.content-block`, consistente sección a sección (no algunas sí, otras no).
# ============================================================================

def test_b_every_pillar_kpi_grid_and_view_wrapped_table_carries_content_block():
    # Los 4 patrones que antes NO revelaban en NINGÚN lado (grillas de KPI,
    # tablas/listas envueltas en `<div x-show="!editing">`, árboles,
    # timelines) ahora SIEMPRE llevan `.content-block` junto a su clase
    # `cs-*` original -- nunca reemplazada, solo aumentada.
    for needle in [
        'class="cs-kpi-grid content-block" x-show="!editing"',
        'class="cs-tree content-block" x-show="!editing"',
        'class="cs-timeline content-block" x-show="!editing"',
        'class="cs-role-grid content-block" x-show="!editing"',
        'class="cs-meet-grid content-block" x-show="!editing"',
        'class="cs-cluster-grid content-block" x-show="!editing"',
        'class="cs-chan-grid content-block" x-show="!editing"',
        'class="cs-evgrid content-block" x-show="!editing"',
    ]:
        assert needle in _STRATEGY_HTML_SRC, f"falta content-block consistente en: {needle}"
    # Las 4 tablas/listas que estaban envueltas en un div anónimo (ticket,
    # sensitivity, events_catalog, capacity assignments) también.
    assert _STRATEGY_HTML_SRC.count('<div class="content-block" x-show="!editing">{{ data_table(') == 4
    assert '<div class="content-block" x-show="!editing">{{ bullets(strategy.focus.rules) }}</div>' in _STRATEGY_HTML_SRC


def test_b_edit_mode_counterparts_never_receive_content_block():
    # `.cs-kpi-grid`/`.cs-numlist`/etc también se usan del lado EDIT
    # (`x-show="editing"`) -- esos NUNCA deben llevar `.content-block`
    # (edición no necesita reveal decorativo, ver gate item 7).
    for m in re.finditer(r'<div class="[^"]*"\s+x-show="editing"[^>]*>', _STRATEGY_HTML_SRC):
        assert "content-block" not in m.group(0), f"un nodo edit-mode no debe revelar: {m.group(0)}"


def test_b_rendered_page_exposes_content_block_on_the_expected_block_types(monkeypatch):
    text = _strategy_html(monkeypatch)
    # Confirma en el HTML REALMENTE renderizado (no solo el source del
    # template) -- Jinja no comió la clase.
    assert 'cs-kpi-grid content-block' in text
    assert 'cs-tree content-block' in text
    assert 'cs-timeline content-block' in text


def test_b_interactive_tab_content_is_documented_as_excluded_and_never_marked():
    # Los paneles de tab/subtab de Segmentos (click-driven, no scroll-driven)
    # deliberadamente NUNCA llevan `.content-block` -- documentado inline.
    assert "Viewport Experience Standardization" in _STRATEGY_HTML_SRC
    assert "NO lleva `.content-block` a propósito" in _STRATEGY_HTML_SRC


# ============================================================================
# C — Anchor jump: el target nunca queda visualmente oculto esperando al
# observer -- reconciliación GENÉRICA en el click handler compartido.
# ============================================================================

def test_c_nav_click_handler_reconciles_target_visibility_generically():
    # La reconciliación vive en el MISMO handler delegado que ya dispara
    # nav_jump para `.section-index a, .module-roadmap__links a` --
    # transversal (Copiloto Y Estrategia comparten ese selector), nunca un
    # segundo listener Strategy-only.
    m = re.search(
        r'const nav = el\.closest\("\.section-index a, \.module-roadmap__links a"\);\s*'
        r'if \(nav\) \{(?P<body>.*?)\n  \}',
        _APP_JS, re.DOTALL,
    )
    assert m, "no se encontró el handler delegado de nav_jump en app.js"
    body = m.group("body")
    assert "getElementById" in body
    assert "content-reveal:not(.is-visible)" in body
    assert "classList.add(\"is-visible\")" in body


# ============================================================================
# D — Toggle `editing`: reconcilia visibilidad en ambos sentidos, sin
# retriggerear un fade cuando contenido YA visible vuelve a mostrarse.
# ============================================================================

def test_d_editing_watcher_reconciles_visibility_both_directions():
    # `$watch('editing', ...)` vive en el x-data de `.cs-edit-shell` (el
    # ÚNICO lugar de Estrategia con un toggle view/edit real -- Copiloto no
    # tiene nada equivalente, así que esto es intencionalmente local a
    # Estrategia, no una extensión del primitivo compartido).
    m = re.search(
        r"x-init=\"\$watch\('editing', \(\) => \{ \$nextTick\(\(\) => \{\s*"
        r"\$el\.querySelectorAll\('\.content-reveal:not\(\.is-visible\)'\)\.forEach\(n => n\.classList\.add\('is-visible'\)\);",
        _STRATEGY_HTML_SRC,
    )
    assert m, "no se encontró el watcher de reconciliación de editing en strategy.html"


def test_d_editing_watcher_is_attached_to_the_edit_shell_not_a_new_wrapper():
    # No se creó un wrapper/componente Alpine nuevo -- el watcher cuelga del
    # MISMO `.cs-edit-shell` que ya gobierna `editing`/`doc`/`save()`.
    shell_start = _STRATEGY_HTML_SRC.index('class="cs-edit-shell"')
    watcher_idx = _STRATEGY_HTML_SRC.index("$watch('editing'", shell_start)
    next_section_idx = _STRATEGY_HTML_SRC.index('<section id="hero"', shell_start)
    assert shell_start < watcher_idx < next_section_idx


# ============================================================================
# E — El fragmento HTMX de feedback por sección queda FUERA del mecanismo de
# reveal (mismo estado en Copiloto, ver module.html) -- un swap nunca puede
# heredar/disparar una animación de entrada de página.
# ============================================================================

def test_e_section_feedback_partial_carries_no_reveal_class():
    assert "content-reveal" not in _SECTION_FEEDBACK_SRC
    assert "content-block" not in _SECTION_FEEDBACK_SRC


def test_e_feedback_root_class_is_not_in_the_shared_reveal_selector():
    # `.section-feedback` (el div raíz que HTMX reemplaza) nunca aparece en
    # la lista de selectores de initContentReveals -- ni antes ni después
    # de este gate, en Copiloto o en Estrategia por igual.
    assert '"section-feedback"' not in _APP_JS
    assert ".section-feedback" not in re.search(
        r"const selector = \[(.*?)\]\.join", _APP_JS, re.DOTALL
    ).group(1)


# ============================================================================
# F — Fail-open intacto: sin IntersectionObserver o con reduced-motion, CERO
# elemento recibe `.content-reveal` -> CSS por default totalmente visible.
# Este gate no tocó esa guardia (solo agregó selectores/consumidores).
# ============================================================================

def test_f_no_intersection_observer_short_circuits_before_any_class_is_added():
    m = re.search(
        r"function initContentReveals\(shell\) \{\s*"
        r'const container = shell\?\.querySelector\("\.module-content"\);\s*'
        r'if \(!container \|\| !\("IntersectionObserver" in window\)\) return;\s*'
        r'if \(window\.matchMedia\("\(prefers-reduced-motion: reduce\)"\)\.matches\) return;',
        _APP_JS,
    )
    assert m, "las guardias fail-open de initContentReveals cambiaron o se movieron"


def test_f_reduced_motion_css_forces_full_opacity_independent_of_js():
    assert re.search(
        r"@media \(prefers-reduced-motion: reduce\) \{\s*"
        r"\.content-reveal \{ opacity: 1; transform: none; transition: none;",
        _CONTENT_CSS,
    ), "el override CSS de reduced-motion para .content-reveal cambió/desapareció"


# ============================================================================
# G — Copiloto no se tocó: `.content-block` es puramente opt-in (una entrada
# de selector), CERO uso hoy fuera de Estrategia.
# ============================================================================

def test_g_module_html_untouched_by_this_gate():
    assert "content-block" not in _MODULE_HTML_SRC


def test_g_copilot_module_page_still_renders_and_wires_reveal(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    res = client.get("/modules/01_Arquitectura_y_Conectividad")
    assert res.status_code == 200
    assert "content-block" not in res.text
    assert 'x-data="copilotLayout(' in res.text


def test_g_full_route_surface_unchanged_by_this_gate():
    import json
    snapshot = json.loads((_ROOT / "tests" / "route_surface.json").read_text(encoding="utf-8"))
    # 126 -> 127: sin cambios de rutas en ESTE gate (Viewport Reveal) -- el +1
    # es `POST /comercial/feedback` (Commercial Feedback Corrective,
    # homologación al Hub), combinado localmente al hacer fast-forward de
    # este checkout contra origin/main. No toca ninguna ruta de Estrategia.
    # 127 -> 138 (Capital Humano Phase 4.1/4.1.1 -- Profile Documents +
    # SharePoint write corrective, merge de origin/main hacia este branch,
    # ver test_staffing_production_corrective.py para el desglose completo):
    # +11 rutas de Capital Humano (Profile/Import/Edit/Documents), CERO
    # rutas de Estrategia tocadas -- este gate sigue sin cambios propios.
    assert len(snapshot) == 138
