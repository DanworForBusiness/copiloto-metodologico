"""
test_reporting_calendario_total_grain.py — Q3 (owner live QA): "el gran
total de Calendario suma aritméticamente bien pero los totales mensuales
individuales no reconcilian con los chequeos booleanos visibles".

Investigado contra evidencia PRIMARIA del PBIP (`Danwor - Tablero
Integral.SemanticModel/definition/tables/Calendario_Cliente.tmdl`): tabla
calculada, UNA fila por Cliente, `Total = Ene+Feb+...+Dic` POR FILA (grano
Cliente), columnas mensuales booleanas 0/1 (`CONTAINSSTRING` sobre
`Calendario_Act`). Conclusión: el comportamiento actual es CORRECTO por
diseño — un subtotal de columna (footer "Total") en una matriz SIEMPRE se
calcula contra el grano de hoja subyacente, independiente del estado
expandido/colapsado de las filas (mismo comportamiento que Power BI real).
La discrepancia reportada es la consecuencia ESPERADA de mostrar presencia
booleana (✓) en filas padre colapsadas mientras el footer es una cuenta
real de hojas — no doble-cuenta, no requiere cambio de código. Este
archivo documenta y prueba esa conclusión con evidencia de EJECUCIÓN real
(no solo aserción de patrón de fuente) — ver
reporting_calendario_total_grain.node.js para los invariantes.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_calendario_total_grain.node.js"
_REPORTING_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_calendario_total_grain_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"calendario total-grain invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL CALENDARIO TOTAL-GRAIN INVARIANTS PASSED" in result.stdout


def test_calendario_footer_total_is_leaf_grain_never_a_tree_node_total():
    # El footer "Total" (por mes, columna) se calcula sobre la lista PLANA
    # de Cliente (`rows`, ANTES de `buildTree`) — nunca sobre nodos de
    # árbol/`sumMonthly(node.rows)` de un nivel intermedio, que cambiaría
    # con el estado expandido/colapsado.
    fn = _REPORTING_JS[_REPORTING_JS.index("function renderCalendario("):_REPORTING_JS.index("function capacidadProductivasTotal")]
    assert "const totalMonthly = sumMonthly(rows);" in fn
    assert "const grandTotal = totalMonthly.reduce((a, b) => a + b, 0);" in fn
    # CRITICAL HUB STANDARDIZATION CORRECTIVE (owner live QA — raw float
    # leak): el footer sigue sumando sobre la lista plana, pero ahora pasa
    # por `fmtNum(..., "count")` — nunca un número crudo interpolado.
    assert '<tr class="is-total"><td class="row-label">Total</td>${totalMonthly.map(v => `<td>${fmtNum(v, "count")}</td>`).join("")}<td>${fmtNum(grandTotal, "count")}</td></tr>' in fn


def test_calendario_cell_is_boolean_presence_row_total_is_real_count():
    # Cada celda MENSUAL (leaf u hoja padre agregada) es presencia booleana
    # (✓/blank) — nunca la cuenta cruda. La columna Total POR FILA (una por
    # nodo, `showTotal=true`) SÍ es la cuenta real (suma de sus 12 meses),
    # igual que `Calendario_Cliente[Total]` del PBIP — las dos semánticas
    # coexisten a propósito, no es una inconsistencia.
    assert "function renderCalendarioCell(v) {\n  return v ? \"\\u2713\" : \"\";\n}" in _REPORTING_JS or 'return v ? "✓" : "";' in _REPORTING_JS
    fn = _REPORTING_JS[_REPORTING_JS.index("function renderCalendario("):_REPORTING_JS.index("function capacidadProductivasTotal")]
    assert "renderTreeRows(stateKey, tree, 0, true, renderCalendarioCell)" in fn
