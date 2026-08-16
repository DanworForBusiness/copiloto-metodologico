"""
test_reporting_capacidad_panel_independence.py — HORAS PRODUCTIVAS CORRECTIVE
(owner: "PRESERVE PBIP PANEL-INDEPENDENT TEMPORAL STATE"). Primary PBIP
evidence established that the two original visuals ("Cliente" izquierdo /
"Cliente + Part" derecho, ver real-data-lineage.md §8ter item 1) have
independent Año/Mes slicers — the earlier pass's shared-state simplification
was NOT authorized. Executes the REAL runtime functions
(`capacidadProductivasSelectedRows`/`getFilter`/`capacidadPanelAnioKey`/
`capacidadPanelMesKey`) via node — no browser framework, same pattern as
reporting_temporal_contract.node.js/reporting_rollup_reconciliation.node.js.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_capacidad_panel_independence.node.js"
_REPORTING_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_capacidad_panel_independence_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Capacidad panel-independence invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL CAPACIDAD PANEL-INDEPENDENCE INVARIANTS PASSED" in result.stdout


# --- Source-seam: nunca un único Año/Mes compartido reintroducido ----------

def test_capacidad_filter_bar_never_mounts_a_shared_anio_mes_select():
    # El filter bar compartido (Cliente + Client Partner-style) NUNCA vuelve
    # a montar un Año/Mes ÚNICO para Productivas — eso es exactamente la
    # simplificación no autorizada que este corrective retira.
    fn_start = _REPORTING_JS.index("function renderCapacidadFilterBar(")
    fn_end = _REPORTING_JS.index("\n}", fn_start)
    body = _REPORTING_JS[fn_start:fn_end]
    assert "capacidadProductivasAnioField()" not in body  # sin argumento = la forma vieja, compartida
    assert "capacidadProductivasMesField()" not in body


def test_capacidad_panel_fields_always_called_with_an_explicit_panel_argument():
    # Cualquier invocación de los field-renderers SIEMPRE pasa un panel
    # explícito ("cliente"/"clientePart"/panelKey) — nunca sin argumento.
    import re
    for m in re.finditer(r"capacidadProductivasAnioField\(([^)]*)\)", _REPORTING_JS):
        assert m.group(1).strip() != "", "capacidadProductivasAnioField invocado sin panel explícito"
    for m in re.finditer(r"capacidadProductivasMesField\(([^)]*)\)", _REPORTING_JS):
        assert m.group(1).strip() != "", "capacidadProductivasMesField invocado sin panel explícito"
