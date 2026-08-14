"""
test_staffing_timeline_css.py — Invariante de coordenadas del timeline
(revisión con dato real, ver HANDOFF).

Bug real reportado: el marcador de "hoy" caía visualmente a la izquierda de
AGO 2026 (como si fuera JUL) porque .staffing-timeline__today-overlay
hardcodeaba `grid-template-columns: 190px 1fr` (sin gap) mientras
.staffing-timeline__header/__row ya usaban `var(--timeline-label-width)`
(280px, tras un ensanche manual para legibilidad) + `gap: 10px` — dos grids
con orígenes de columna distintos. `compute_today_marker` (Python, testeado
en test_staffing_service.py) nunca calculó mal el %; el bug vivía 100% en
CSS. Este archivo lee el CSS fuente (no hay motor de layout en la suite)
para fijar que las tres piezas SIEMPRE comparten la misma variable/gap —
sin importar qué valor tenga `--timeline-label-width` en un breakpoint
dado, cambiar ese ancho nunca debe poder desincronizar dónde arranca la
pista de calendario.
"""
from __future__ import annotations

import re
from pathlib import Path

_CSS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")


def _rule_body(selector_prefix: str) -> str:
    idx = _CSS.index(selector_prefix)
    return _CSS[idx: _CSS.index("}", idx) + 1]


def test_today_overlay_uses_the_shared_label_width_variable_not_a_literal():
    body = _rule_body(".staffing-timeline__today-overlay {")
    assert "var(--timeline-label-width)" in body
    # Ningún ancho de columna hardcodeado en px reemplazando la variable —
    # el bug real era exactamente esto (190px hardcodeado, desincronizado
    # del 280px real de header/row).
    assert not re.search(r"grid-template-columns:\s*\d+px", body)


def test_today_overlay_gap_matches_header_and_row_gap():
    # El gap TAMBIÉN es parte del origen de la segunda columna — un gap
    # distinto desincroniza el origen aunque el ancho de la primera columna
    # coincida.
    header_body = _rule_body(".staffing-timeline__header, .staffing-timeline__row {")
    overlay_body = _rule_body(".staffing-timeline__today-overlay {")
    header_gap = re.search(r"gap:\s*([\d.]+px)", header_body).group(1)
    overlay_gap = re.search(r"gap:\s*([\d.]+px)", overlay_body).group(1)
    assert header_gap == overlay_gap == "10px"


def test_today_overlay_and_header_row_use_the_same_label_width_token():
    header_body = _rule_body(".staffing-timeline__header, .staffing-timeline__row {")
    overlay_body = _rule_body(".staffing-timeline__today-overlay {")
    assert "var(--timeline-label-width)" in header_body
    assert "var(--timeline-label-width)" in overlay_body


def test_narrow_breakpoint_keeps_header_row_and_overlay_synced_in_one_rule():
    # A ≤900px las tres piezas se overridean JUNTAS en una sola regla — no
    # hay forma de que una quede desincronizada de las otras dos en ese
    # breakpoint. `tools.css` es un archivo grande y compartido con varias
    # secciones ajenas a Staffing — se busca el bloque @media específico
    # que contiene el override real (no el primer @media 900px del
    # archivo, que pertenece a otra feature).
    rule_idx = _CSS.index(
        ".staffing-timeline__header, .staffing-timeline__row, "
        ".staffing-timeline__today-overlay { grid-template-columns:"
    )
    media_idx = _CSS.rindex("@media", 0, rule_idx)
    assert media_idx < rule_idx < _CSS.index("}", rule_idx)
    assert "(max-width: 900px)" in _CSS[media_idx: rule_idx]


def test_label_width_variable_defined_once_on_the_timeline_root():
    # Fuente única de verdad — nunca un segundo `--timeline-label-width:`
    # redefiniéndolo con otro valor fuera de este selector base (los
    # breakpoints angostos overridean grid-template-columns directo, no la
    # variable, así que no hay una segunda definición legítima).
    assert _CSS.count("--timeline-label-width:") == 1


# ============================================================================
# Espacio de coordenadas día-exacto (ver HANDOFF — smoke #1 del owner
# reportó una barra de asignación a la IZQUIERDA del marcador de "hoy" pese
# a que su fecha real era POSTERIOR). Verificado con un repro real de
# navegador (getBoundingClientRect sobre el CSS/HTML shippeado): el
# contenedor de la barra (`.staffing-timeline__track`) y el del marcador
# (`.staffing-timeline__today-track`) YA resuelven al mismo ancho físico
# (ambos son la celda "1fr" de grids independientes que comparten
# `var(--timeline-label-width)` + `gap`, con stretch horizontal implícito
# por default de CSS Grid) — no había una fecha mal calculada ni un
# containing-block roto en el código shippeado; la lectura visual del owner
# es consistente con haber probado assets VIEJOS (pre día-exacta, con
# `grid-column` por mes) contra un smoke sin hard-refresh/restart. Estos
# tests fijan la parte del contrato que SÍ es responsabilidad del CSS
# fuente: `width: 100%` EXPLÍCITO en vez de depender del stretch implícito
# (más robusto a un futuro `justify-items`/`justify-self`), y que la barra
# NUNCA vuelva a ser un ítem de grid posicionado por mes (`grid-column`/
# `grid-row`) — la regresión original que motivó el primer corrective.
# ============================================================================

def test_track_is_not_a_grid_container():
    # `.staffing-timeline__track` (barras de asignación) y su vacation-lane
    # gemela reusan la MISMA regla — si volviera a ser `display: grid`, las
    # barras (`position: absolute; left: X%`) dejarían de posicionarse
    # día-exacto sobre su propio ancho y volverían a depender de columnas
    # discretas por mes (la regresión original).
    body = _rule_body(".staffing-timeline__track {")
    assert "display: grid" not in body
    assert "grid-template-columns" not in body


def test_track_and_today_track_declare_width_explicitly():
    # Ancho EXPLÍCITO (ver HANDOFF) en vez de confiar en el stretch
    # implícito de CSS Grid — mismo resultado físico, pero ya no depende de
    # un default que un `justify-items`/`justify-self` futuro podría
    # romper en silencio.
    track_body = _rule_body(".staffing-timeline__track {")
    today_track_body = _rule_body(".staffing-timeline__today-track {")
    assert "width: 100%" in track_body
    assert "width: 100%" in today_track_body


def test_bars_use_absolute_positioning_not_month_grid_columns():
    # `.staffing-timeline__bar`/`.staffing-timeline__vacation-bar` deben
    # posicionarse con `position: absolute` (día-exacto, `left`/`width` en
    # % inline vía `barStyle()`) — nunca `grid-row`/`grid-column`/
    # `align-self` (mecanismo viejo de índice de mes, la barra quedaba
    # pegada al día 1 de su mes sin importar la fecha real).
    for selector in (".staffing-timeline__bar {", ".staffing-timeline__vacation-bar {"):
        body = _rule_body(selector)
        assert "position: absolute" in body
        assert "grid-row" not in body
        assert "grid-column" not in body
        assert "align-self" not in body
