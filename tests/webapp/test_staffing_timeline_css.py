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
