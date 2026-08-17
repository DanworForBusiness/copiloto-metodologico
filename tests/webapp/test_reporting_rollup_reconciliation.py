"""
test_reporting_rollup_reconciliation.py — QA4 (owner live QA, "Avanti"):
parent aggregate must equal the aggregate of canonical descendant leaves,
for EVERY hierarchical additive measure. The bug lived in the frontend
tree-render engine (`renderBusinessTreeRows`), not in the backend service,
so this executes the REAL JS engine (via node — no browser/jsdom framework)
against synthetic fixtures rather than asserting on source strings alone.
See reporting_rollup_reconciliation.node.js for the actual invariants
(single-child exact equality, multi-child correct distribution, filtered-
population reconciliation, categorical uniform/mixed propagation).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_rollup_reconciliation.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_hierarchy_rollup_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"reconciliation invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL RECONCILIATION INVARIANTS PASSED" in result.stdout


def test_leaf_node_never_truncates_to_first_row_when_rollup_exists():
    # Corrective — el patrón viejo (`node.rows[0]` para nodos sin hijos
    # declarados) no debe quedar; `c.rollup(node.rows)` es el ÚNICO camino
    # cuando la columna lo define, sin importar si el nodo tiene hijos.
    src = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")
    fn = src[src.index("function renderBusinessTreeRows"):src.index("function renderBusinessTreeRows") + 1500]
    assert "const leafRow = hasChildren ? null : node.rows[0];" not in fn
    assert "if (c.rollup) content = c.rollup(node.rows);" in fn
    assert "else if (node.rows.length === 1) content = c.render(node.rows[0]);" in fn
