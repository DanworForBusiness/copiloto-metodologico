"""
test_commercial_active_opportunities_navigation.py — Active Opportunities
Phase 2 / C3.1 (ver HANDOFF — interaction + spacing corrective): al abrir
"Oportunidades Activas — Análisis" desde el KPI, la vista navega a la
sección (scrollIntoView, sin behavior explícito para heredar
scroll-behavior de <html>); al cerrar/filtrar, nunca. El switch Horas|
Ponderadas que este archivo cubría (C3.1) fue retirado en C5a — el Funnel
muestra las tres series siempre, sin toggle que pueda disparar scroll.
Ver commercial_active_opportunities_navigation.node.js para las
invariantes del motor real (commercialMaybeScrollToOpenedAnalysis).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "commercial_active_opportunities_navigation.node.js"
_COMMERCIAL_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial.js").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_active_opportunities_navigation_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Active Opportunities navigation invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL COMMERCIAL ACTIVE OPPORTUNITIES NAVIGATION INVARIANTS PASSED" in result.stdout


def test_scroll_call_is_reused_by_both_click_and_keyboard_activation():
    # NO handler paralelo: un solo `commercialMaybeScrollToOpenedAnalysis`,
    # invocado desde los dos caminos de activación reales del motor
    # compartido (click y keydown Enter/Espacio) — nunca una copia.
    assert _COMMERCIAL_JS.count("function commercialMaybeScrollToOpenedAnalysis(") == 1
    assert _COMMERCIAL_JS.count("commercialMaybeScrollToOpenedAnalysis(kpiTrigger, root)") == 2


def test_capability_and_local_filters_never_trigger_scroll():
    # Cambiar de capability o de los filtros locales de Actividades tampoco
    # debe navegar — acotado al RESTO del delegado de click (hasta que
    # cierra, antes del delegado de "change"), sin capturar el delegado de
    # `keydown` (más abajo en el archivo), que SÍ la invoca legítimamente
    # por su propio camino de activación.
    click_tail = _COMMERCIAL_JS.split('const capButton = event.target.closest("[data-commercial-capability]");', 1)[1] \
        .split('document.addEventListener("change"', 1)[0]
    assert "commercialMaybeScrollToOpenedAnalysis" not in click_tail


def test_scroll_margin_top_clears_the_shared_sticky_topbar():
    # No scrollTo con números mágicos — scroll-margin-top en el primitivo
    # que se scrollea (.dashboard-section), reusando el MISMO patrón que
    # `.content-section` ya usa en app.css/tools.css para el mismo problema
    # (despejar el .topbar sticky compartido).
    commercial_css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial.css").read_text(encoding="utf-8")
    assert ".dashboard-section { scroll-margin-top:" in commercial_css
    assert "scrollTo(" not in _COMMERCIAL_JS


def test_module_content_gets_its_own_scroll_behavior_respecting_reduced_motion():
    # El contenedor con overflow REAL en este layout de Hub es
    # `.module-content` (auditado en vivo), no `<html>` — `scroll-behavior`
    # no cruza a un scroll box distinto. Mismo par smooth/reduced-motion que
    # `html` ya usa en app.css, aplicado al contenedor correcto para esta
    # página.
    commercial_css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial.css").read_text(encoding="utf-8")
    assert ".module-content { scroll-behavior: smooth; }" in commercial_css
    reduced = re.search(r"@media \(prefers-reduced-motion: reduce\)\s*\{([^}]*\{[^}]*\}[^}]*)\}", commercial_css)
    assert reduced and ".module-content { scroll-behavior: auto; }" in reduced.group(1)


def test_header_never_shrinks_or_wraps_the_title_text():
    # F — estructura/clases (regresión real detectada en vivo durante el QA
    # de C3.1, cuando el header compartía fila con el switch Horas|
    # Ponderadas): el título nunca comprime su texto a dos renglones.
    # `flex-wrap` en el contenedor y `white-space:nowrap` en el título
    # siguen vigentes aunque C5a retiró el switch — mismo par de reglas que
    # `.hero-lead-row` ya usa, y el título sigue siendo texto de longitud
    # variable dentro de un `justify-content:space-between`.
    commercial_css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial.css").read_text(encoding="utf-8")
    assert "flex-wrap: wrap" in re.search(r"\.category-plot-header\s*\{([^}]*)\}", commercial_css).group(1)
    assert ".category-plot-header .perspective-label { margin-bottom: 0; white-space: nowrap; }" in commercial_css


def test_funnel_block_has_a_real_section_gap_before_whatever_follows():
    # Spacing rhythm corrective (C3.1, ver HANDOFF — "spacing NO cerrado"):
    # antes de este fix el eje X quedaba pegado (0px) al bloque siguiente.
    # El margin va en el wrapper de TODO el Funnel (`[data-commercial-dist]`)
    # para que sea estructural — no depende de qué contenido venga después
    # (hoy la tabla provisoria, en C4 Forecast/Origen/Detalle) — y usa el
    # MISMO valor que el Hub ya usa para "después de un bloque analítico"
    # (`.dashboard-section` margin, `.activity-kpi-grid` margin-bottom),
    # nunca un número inventado para esta captura puntual.
    # `margin-top` (owner feedback post-C5d): la ENTRADA del bloque (título
    # + leyenda) sólo tenía el padding-top de `.dashboard-section__body`
    # (4px) contra los 12px que rodean "Detalle de Oportunidades" — mismo
    # valor (12px), no uno nuevo, para igualar el ritmo de entrada y salida.
    commercial_css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial.css").read_text(encoding="utf-8")
    tools_css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    assert "[data-commercial-dist] { margin-top: 12px; margin-bottom: 12px; }" in commercial_css
    assert ".dashboard-section { margin: 12px 0;" in tools_css
    assert ".activity-kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 12px; }" in tools_css


def test_close_button_lives_in_summary_aligned_with_the_count():
    # Owner corrective, segunda vuelta (ver HANDOFF): la versión anterior
    # (posicionamiento absoluto sobre el `<details>`, `top` calibrado a
    # mano) tenía dos problemas reales que el owner reportó con evidencia
    # (screenshots): (1) el botón desaparecía al colapsar el `<details>`
    # nativamente — vivía en `.dashboard-section__body`, que es justo lo
    # que ese toggle esconde; (2) el `top` en px, calibrado contra un
    # mockup, quedaba ~4-5px desalineado del baseline real de "XX
    # oportunidad(es)" en la app real. Fix estructural: el botón se movió
    # DENTRO de `<summary>` (commercial.js), como flex-item más — hereda
    # `align-items:center`/`gap` del primitivo compartido
    # `.dashboard-section__head` (tools.css), así que la alineación es
    # gratis y el botón sobrevive el colapso nativo (el `<summary>` SIEMPRE
    # es visible). Cero `position:absolute`, cero `top`/`right` a mano.
    commercial_js = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial.js").read_text(encoding="utf-8")
    commercial_css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial.css").read_text(encoding="utf-8")
    summary_block = commercial_js.split('data-section-id="activas-analisis"', 1)[1].split("</summary>", 1)[0]
    assert 'class="dashboard-section__count"' in summary_block
    assert 'data-commercial-close-analysis' in summary_block
    assert summary_block.index("dashboard-section__count") < summary_block.index("data-commercial-close-analysis"), (
        "el botón va DESPUÉS del conteo, en el MISMO flex row de <summary>"
    )
    panel_close_rule = re.search(r"\.panel-close\s*\{([^}]*)\}", commercial_css).group(1)
    assert "position: absolute" not in panel_close_rule
    assert "top:" not in panel_close_rule and "right:" not in panel_close_rule
    assert '[data-section-id="activas-analisis"] { position: relative; }' not in commercial_css
    assert '[data-section-id="activas-analisis"] .dashboard-section__count { margin-right:' not in commercial_css


def test_internal_funnel_gaps_match_the_detail_table_rhythm():
    # Owner corrective (screenshot, ver HANDOFF): "respetar los márgenes
    # armónicos que ya resuelve la tabla de detalle... para que apliquen en
    # el título y leyendas previo al gráfico" — el ritmo interno compacto
    # (3px/8px, corrective anterior) quedó marcado como "comprimido" en
    # comparación directa, DENTRO del mismo panel, contra "Detalle de
    # Oportunidades" (12px/10px, ver `[data-commercial-dist]` y
    # `.urgency-legend`). Título→leyenda sube a 8px. `align-items:flex-end`
    # (corrective histórico) sigue vigente — sólo cambia el valor del
    # margin.
    # leyenda→plot (owner corrective post-C5e, ver HANDOFF: "saquemos el
    # margin-top en Etapa y movámoslo entre el gráfico y las leyendas"): el
    # aire que vivía como `margin-top` de la barra de filtros de análisis
    # (Etapa/Vendedor/Buscar) se trasladó ACÁ — 12px — en vez de separar esa
    # barra del `<summary>`.
    commercial_css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial.css").read_text(encoding="utf-8")
    assert ".category-plot-header { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 6px 12px; margin-bottom: 8px; }" in commercial_css
    assert ".category-plot-legend { display: flex; flex-wrap: wrap; gap: 6px 16px; margin: 0 0 12px;" in commercial_css
    assert '[data-commercial-analysis-filters] { margin: 0 0 12px;' in commercial_css


def test_no_focus_stealing_introduced_by_the_scroll_corrective():
    # El scroll es puramente visual — nunca mueve el foco (no hay contrato
    # aria-expanded/focus previo en el motor compartido, así que esta
    # corrective no debe inventar uno).
    scroll_fn = _COMMERCIAL_JS.split("function commercialMaybeScrollToOpenedAnalysis(", 1)[1].split("\nfunction ", 1)[0]
    assert ".focus(" not in scroll_fn
    assert "aria-expanded" not in _COMMERCIAL_JS


def test_funnel_header_no_longer_carries_a_dimension_switch():
    # C5a retiró el switch Horas|Ponderadas (C3.1): el header del Funnel
    # sólo emite el rótulo — ya no hay un segundo control compartiendo la
    # fila. El wrapper viejo (title/switch en containers separados, de
    # ANTES de C3.1) tampoco existe.
    dist_fn = _COMMERCIAL_JS.split("function commercialDistribucionHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert '<div class="category-plot-header">' in dist_fn
    header_block = dist_fn.split('<div class="category-plot-header">', 1)[1].split("</div>", 1)[0]
    assert "perspective-label" in header_block
    assert "dashboard-view-switch" not in header_block
    assert "data-commercial-dim" not in dist_fn
    assert "staffing-timeline-toolbar" not in _COMMERCIAL_JS


def test_funnel_header_css_reuses_the_existing_label_left_control_right_pattern():
    commercial_css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial.css").read_text(encoding="utf-8")
    assert ".category-plot-header {" in commercial_css
    header_rule = re.search(r"\.category-plot-header\s*\{([^}]*)\}", commercial_css).group(1)
    # Mismo criterio que `.hero-lead-row` (ya en este archivo): flex +
    # space-between, no un layout inventado para este chart puntual.
    assert "flex" in header_rule and "space-between" in header_rule


def test_opportunities_bar_color_is_neutral_graphite_not_blue():
    # Color semantics (C3.1 — Danwor): Oportunidades deja de ser azul en
    # las reglas REALES — el comentario de ownership sí menciona el color
    # viejo en prosa histórica, por eso se descartan comentarios acá.
    commercial_css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial.css").read_text(encoding="utf-8")
    rules_only = re.sub(r"/\*.*?\*/", "", commercial_css, flags=re.S)
    assert "#2f6fbd" not in rules_only
    assert ".category-plot__bar--count { background: var(--commercial-neutral-ink); }" in commercial_css


def test_hours_series_stays_gold_and_weighted_uses_a_distinguishable_gold_tone():
    # C5a (tres series simultáneas): Horas sigue en `--gold`, sin cambios.
    # Hs Ponderadas comparte el eje derecho con Horas pero NO puede compartir
    # el mismo color — necesita un tono DISTINGUIBLE de la misma familia
    # dorada (nunca un color ajeno a esa semántica).
    # Owner corrective (dark theme, ver HANDOFF): `#d9a520` (heredado del
    # eje derecho) resultó demasiado parecido a `--gold` (#e7b008) en
    # oscuro — mismo tono amarillo, apenas distinta luminancia — y las
    # barras dejaban de distinguirse. `#e8820a` vira el HUE hacia naranja
    # (no sólo luminancia) para una diferenciación real, aplicado a los
    # TRES consumidores que comparten este tono (bar/leyenda/eje derecho).
    commercial_css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial.css").read_text(encoding="utf-8")
    assert ".category-plot__bar--hours { background: var(--gold); }" in commercial_css
    assert ".category-plot-legend .swatch-bar--hours { background: var(--gold); }" in commercial_css
    assert ".category-plot__bar--weighted { background: #a67c00; }" in commercial_css
    assert ".category-plot-legend .swatch-bar--weighted { background: #a67c00; }" in commercial_css
    assert ".content-theme-dark .category-plot__bar--weighted { background: #e8820a; }" in commercial_css
    assert ".content-theme-dark .category-plot-legend .swatch-bar--weighted { background: #e8820a; }" in commercial_css
    assert ".content-theme-dark .category-plot__axis--right span { color: #e8820a; }" in commercial_css


def test_opportunities_color_is_centralized_in_one_local_theme_aware_variable():
    # Color token audit (C3.1, ver HANDOFF): no existe un token Hub
    # apropiado para reutilizar (--tab-ink está nombrado/documentado para
    # `.experience-tabs`, no como contrato genérico) — se centraliza como
    # variable LOCAL de esta hoja, nunca un primitivo Hub nuevo con un solo
    # consumidor, y nunca el literal duplicado en cada selector.
    commercial_css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial.css").read_text(encoding="utf-8")
    assert ":root { --commercial-neutral-ink: #1f1f24; }" in commercial_css
    assert ".content-theme-dark { --commercial-neutral-ink: #e4e4ec; }" in commercial_css
    # Un solo lugar (fuera de prosa) define los literales — todo lo demás
    # los consume por var. Comentarios se descartan: mencionan el par varias
    # veces documentando de dónde vienen y por qué no se adoptó --tab-ink.
    rules_only = re.sub(r"/\*.*?\*/", "", commercial_css, flags=re.S)
    assert rules_only.count("#1f1f24") == 1
    assert rules_only.count("#e4e4ec") == 1
    assert rules_only.count("var(--commercial-neutral-ink)") == 3  # bar--count, axis--left, swatch-bar


def test_axis_and_swatch_colors_are_theme_aware_not_hardcoded_hex_only():
    # Nunca un gris fijo que desaparezca contra el fondo oscuro — ambos
    # consumidores restantes (eje, swatch) resuelven por la MISMA variable
    # theme-aware, nunca un hex propio que pueda desalinearse del tile.
    commercial_css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial.css").read_text(encoding="utf-8")
    assert ".category-plot__axis--left span { right: 7px; color: var(--commercial-neutral-ink); }" in commercial_css
    assert ".category-plot-legend .swatch-bar { width: 11px; height: 11px; border-radius: 2px; background: var(--commercial-neutral-ink); }" in commercial_css
