"""
test_commercial_prospect_ratio.py — Shared Stage-0 Prospect Ratio (ver
HANDOFF: owner GO tras el STOP semántico sobre "Forecast Ponderado" y el
reset de tipo). El ratio manual de Prospecto, hasta ahora exclusivo de
Forecast de pipeline (`forecastViewMode === "stage"`, C6/Pipeline Forecast),
pasa a ser compartido con Prospectos — su dueño conceptual primario — SIN
tocar el modelo Odoo-probability existente ("Pipeline ponderado"/"Hs Pond."
en la vista por mes, ni la columna homónima que Prospectos ya traía).

Las invariantes de comportamiento (estado compartido, aritmética, KPI
rediseñado, independencia del modelo Odoo-probability, reset de tipo) viven
en commercial_prospect_ratio.node.js, que ejecuta el motor REAL. Acá quedan
las de ARCHIVO/regresión que no requieren el harness completo.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "webapp" / "static"
_HARNESS = Path(__file__).resolve().parent / "commercial_prospect_ratio.node.js"
_COMMERCIAL_JS = (_STATIC / "commercial.js").read_text(encoding="utf-8")


def _js_code(raw: str) -> str:
    """JS sin comentarios — MISMO helper que test_commercial_forecast.py: los
    bloques que documentan por qué se retiró un literal (p. ej. "Ratio
    aplicado") lo nombran a propósito en la prosa, y un scan del texto crudo
    daría un falso positivo."""
    return "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_prospect_ratio_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Prospect ratio invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL COMMERCIAL PROSPECT RATIO INVARIANTS PASSED" in result.stdout


def test_no_second_ratio_state_was_introduced():
    # A/B (owner GO §5): `forecastProspectRatioRaw` sigue siendo la ÚNICA
    # fuente — renombrarlo hubiera sido aceptable pero no se justificaba
    # (owner GO: "prefer reusing/renaming minimally").
    assert _COMMERCIAL_JS.count("forecastProspectRatioRaw:") == 1
    for banned in ("prospectsRatio", "forecastRatio", "weightedRatio"):
        assert banned not in _COMMERCIAL_JS, banned


def test_prospects_and_forecast_ratio_fields_use_distinct_attributes():
    # Cada panel tiene su PROPIO atributo de `<input>` (mismo estado, dos
    # hosts de re-render distintos) — nunca comparten uno, o el listener de
    # un panel re-renderizaría/restauraría foco en el panel equivocado.
    assert "data-commercial-forecast-ratio" in _COMMERCIAL_JS
    assert "data-commercial-prospects-ratio" in _COMMERCIAL_JS
    assert _COMMERCIAL_JS.count('function commercialProspectRatioFieldHtml(') == 1
    assert _COMMERCIAL_JS.count("function commercialForecastRatioFieldHtml()") == 1


def test_two_separate_delegated_listeners_for_the_ratio_input():
    # `input`, no `change`, en LOS DOS — recalcula en cada tecla. Dos
    # listeners SEPARADOS (nunca uno compartido con una rama `if/else`
    # adentro): cada uno re-renderiza y restaura foco sobre SU propio host.
    assert _COMMERCIAL_JS.count('event.target.closest("[data-commercial-forecast-ratio]")') == 1
    assert _COMMERCIAL_JS.count('event.target.closest("[data-commercial-prospects-ratio]")') == 1
    forecast_listener = _COMMERCIAL_JS.split(
        'event.target.closest("[data-commercial-forecast-ratio]");', 1)[1].split("});", 1)[0]
    prospects_listener = _COMMERCIAL_JS.split(
        'event.target.closest("[data-commercial-prospects-ratio]");', 1)[1].split("});", 1)[0]
    assert "commercialRenderForecastPanel()" in forecast_listener
    assert "commercialRenderProspectsPanel()" in prospects_listener
    assert "commercialRenderProspectsPanel" not in forecast_listener
    assert "commercialRenderForecastPanel" not in prospects_listener


def test_reopening_either_panel_refreshes_the_shared_ratio():
    # E (owner GO): "cerrar y reabrir no vuelve a pedir nada" (lazy load)
    # significa que el DOM queda como el último render — sin este gancho,
    # reabrir un panel mostraría un ratio desactualizado si se cambió desde
    # el OTRO panel mientras tanto. `commercialMaybeRerenderForecastForMode`
    # ya existía (C6b, título conmutable); su par de Prospectos es NUEVO.
    assert _COMMERCIAL_JS.count("function commercialMaybeRerenderForecastForMode(") == 1
    assert _COMMERCIAL_JS.count("function commercialMaybeRerenderProspectsForRatio(") == 1
    # Enganchados en LOS DOS caminos de activación reales (click y keydown).
    assert _COMMERCIAL_JS.count("commercialMaybeRerenderProspectsForRatio(kpiTrigger)") == 2


def test_pipeline_ponderado_month_view_keeps_its_odoo_probability_identity():
    # N — hard gate (owner GO §11): el modelo Odoo-probability de la vista
    # por mes no gana ningún vocabulario del ratio manual.
    ponderado_metric = _COMMERCIAL_JS.split('ponderado: { key: "weighted_hours"', 1)[1].split("},", 1)[0]
    assert '"Forecast Ponderado — Pipeline por Mes de Cierre"' in ponderado_metric
    assert "Hs Pond." in ponderado_metric
    # Y la vista por etapa (dueña del ratio manual) usa vocabulario DISTINTO
    # — nunca "Ponderado" a secas para las dos cosas (owner GO §3).
    stage_view = _js_code(_COMMERCIAL_JS).split(
        "function commercialForecastStageViewHtml(", 1)[1].split("\n}", 1)[0]
    assert "Prospecto ponderado" in stage_view
    assert "Forecast total" in stage_view
    assert "Ratio aplicado" not in stage_view


def test_prospects_table_column_labels_disambiguate_the_two_weightings():
    table = _COMMERCIAL_JS.split("function commercialProspectsTableHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert "Hs prob. pond." in table
    assert "Hs prosp. pond." in table
    assert "Hs Pond." not in table, "el rótulo viejo y ambiguo no debe sobrevivir en ningún lado de esta tabla"
    # La columna Odoo (`weighted_hours`, sort key existente) sigue siendo
    # SORTABLE con su mismo key — nunca reapuntada a la columna nueva.
    assert 'th("weighted_hours", "Hs prob. pond.")' in table
