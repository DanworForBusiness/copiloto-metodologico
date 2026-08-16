"""
test_reporting_capacidad_composition.py — REPORTING LIVE-QA CORRECTIVE
(owner: "VISUAL COMPOSITION CORRECTIVE" — Horas Productivas panel-local
Total Horas/Año/Mes/Expandir-Contraer must live INSIDE each panel card, not
detached above both). Renders the REAL `renderCapacidadProductivas()` HTML
against a synthetic fixture and inspects the resulting DOM structure — not
just source-seam string presence — same pattern as
reporting_capacidad_panel_independence.node.js.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_capacidad_composition.node.js"
_REPORTING_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_capacidad_composition_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Capacidad composition invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL CAPACIDAD COMPOSITION INVARIANTS PASSED" in result.stdout


# --- Source-seam: sin grid/CSS propio de Horas Productivas -----------------

def test_no_bespoke_grid_or_card_css_introduced_for_capacidad():
    # El primitivo de 2 columnas SIGUE siendo `.activity-grid-2` (compartido
    # con Rentabilidad Mejor/Peor) — nunca una clase nueva tipo
    # `.capacidad-grid`/`.productivas-panel`.
    import re
    assert not re.search(r"\.capacidad-grid|\.productivas-panel|\.horas-productivas-card", _REPORTING_JS)


def test_activity_grid_2_has_min_width_zero_for_wide_children():
    # Fix del primitivo COMPARTIDO (tools.css) — nunca un caso especial de
    # Horas Productivas: los hijos de `.activity-grid-2` deben poder
    # encogerse para que el overflow ancho quede DENTRO de `.matrix-scroll`,
    # nunca empujando la página entera.
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    assert ".activity-grid-2 > * { min-width: 0; }" in css


# --- UX CORRECTIVE (owner live QA, 2da pasada): Mes multi-select, columnas
# ordenables, ambos paneles entran en pantalla sin scroll, Expandir/Contraer
# ya no más altos de lo que deberían ---------------------------------------

def test_row_label_col_default_is_untouched_by_capacidad_colgroup():
    # FINAL VISUAL COMPOSITION corrective: la columna de label de Horas
    # Productivas ya NO usa un ancho fijo en el `th` (ni el 340px default
    # ni el `--panel` de 190px retirado) — el `<colgroup>` explícito del
    # markup (ver test_capacidad_composition_invariants_hold) gana sobre
    # CUALQUIER ancho de `th` en table-layout:fixed, así que el label es
    # genuinamente FLEXIBLE. El default de 340px sigue intacto para
    # Cartera/Rentabilidad/HV/Participación/Salud/Calendario (tablas de
    # ancho completo, sin colgroup propio).
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    assert "table.dashboard-table th.row-label-col { width: 340px; }" in css
    assert "row-label-col--panel" not in css
    assert "row-label-col--panel" not in _REPORTING_JS


def test_capacidad_table_uses_explicit_colgroup_flex_and_compact_columns():
    # Escape hatch YA documentado en tools.css ("un dominio con su propio
    # <colgroup>... el <col> explícito gana en table-layout:fixed") — la
    # columna de label es FLEXIBLE (`--flex`, consume el espacio
    # disponible), Horas/% Incidencia son COMPACTAS (`--compact`,
    # content-sized/bounded). Reusable Hub-wide, no clases inventadas por
    # Horas Productivas.
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    assert ".dashboard-table-col--flex { width: auto; }" in css
    assert ".dashboard-table-col--compact { width: 72px; }" in css
    assert '<col class="dashboard-table-col--flex">' in _REPORTING_JS
    assert '<col class="dashboard-table-col--compact">' in _REPORTING_JS


def test_capacidad_table_neutralizes_full_width_min_width_and_row_label_defaults():
    # `min-width:900px`/`width:300px` en reporting.css son correctos para
    # las matrices mensuales de ancho completo (12 columnas de mes) — pero
    # son la causa raíz del scroll horizontal innecesario en las 2 tablas
    # compactas de Horas Productivas. `.dashboard-table--compact` los anula
    # SOLO para esas 2 tablas (nunca para el resto del Hub).
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.css").read_text(encoding="utf-8")
    assert ".reporting-integral-preview table.dashboard-table { min-width: 900px; }" in css
    assert ".reporting-integral-preview table.dashboard-table.dashboard-table--compact { min-width: 0; }" in css
    assert ".reporting-integral-preview table.dashboard-table.dashboard-table--compact th.row-label-col { width: auto; }" in css
    assert '<table class="dashboard-table dashboard-table--compact">' in _REPORTING_JS


def test_sortable_header_css_reuses_shared_table_primitive():
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    assert "table.dashboard-table th.dashboard-table-th--sortable { cursor: pointer" in css


def test_timeline_toolbar_centers_items_never_stretches_buttons():
    # Owner live QA: "los botones de expandir y contraer siguen mas altos de
    # lo que deberian" — causa raíz `align-items:stretch` (default de flex)
    # estirando los botones a la altura del label Año/Mes más alto. Fix del
    # primitivo COMPARTIDO `.staffing-timeline-toolbar` (Staffing + las 5
    # experiencias de Reporting con toolbar, nunca un caso especial de
    # Horas Productivas).
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    assert "align-items: center" in css[css.index(".staffing-timeline-toolbar {"):css.index(".staffing-timeline-toolbar {") + 300]


def test_month_multi_select_reuses_dashboard_view_switch_primitive():
    # Owner: "boton cuadrado minimalista... gris/claro... mas obscuro/gold
    # al hacer click" — MISMO primitivo `.dashboard-view-switch` que el
    # perspective-switch (nunca una clase de botón nueva), solo un modifier
    # `--multi` más compacto.
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    assert ".dashboard-view-switch--multi button {" in css
    import re
    assert not re.search(r"\.mes-boton|\.month-toggle|\.capacidad-mes-btn", _REPORTING_JS)
    assert not re.search(r"\.mes-boton|\.month-toggle|\.capacidad-mes-btn", css)
