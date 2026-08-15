"""
test_reporting_table_formatting.py — TRANSVERSAL TABLE FORMATTING
CORRECTIVE (owner live QA): Horas Productivas still showed a raw
floating-point artifact ("3.5999999999999996") in its table cells despite
the prior KPI-primitive corrective. Root cause: `renderBusinessTreeRows`
(the shared table engine behind Cartera/Capacidad-Productivas/
Comercial-Participación — non-monthly business tables) interpolated the
RAW number returned by `sumField`/`render` directly into the `<td>`
whenever the column had no explicit `formatFn` — `sumField` intentionally
returns a raw number so `carteraKpiTiles`/`renderDashboardKpi` can format
it exactly once (formatting it twice reproduces the S.3 "Diff Hs KPI blank"
bug), but nothing formatted it on the TABLE-CELL path.

Fix: `renderBusinessTreeRows` now formats any `content` that is still a
raw `number` (never a pre-formatted string like `diffCell`'s HTML or a
percent string) via `fmtNum(content, "hours")` as the LAST step before
touching the DOM — closes the bug class transversally for any current or
future numeric column, without touching `sumField`'s raw-number contract
(so the KPI-tile path stays correct)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_table_formatting.node.js"
_REPORTING_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_table_formatting_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Table formatting invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL TABLE FORMATTING INVARIANTS PASSED" in result.stdout


def test_render_business_tree_rows_formats_any_raw_number_before_dom():
    fn = _REPORTING_JS[_REPORTING_JS.index("function renderBusinessTreeRows("):_REPORTING_JS.index("function renderHorasVendidas(")]
    assert 'if (typeof content === "number") content = fmtNum(content, "hours");' in fn
    # El guard corre DESPUÉS de resolver rollup/render (nunca antes, o
    # perdería el contenido ya calculado).
    assert fn.index('if (typeof content === "number")') > fn.index("c.render(node.rows[0])")
