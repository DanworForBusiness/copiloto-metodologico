"""test_human_capital_candidates_table_ergonomics.py — CCHH UX homologation:
adoption of the shared reorder/resize primitive (initDraggableColumns/
initResizableColumns, app.js) by the Candidates list.

The table-agnostic contract of the primitive itself is proven in
test_app_table_ergonomics.py; Commercial's own wiring/regression lives in
test_commercial_licensing_table_ergonomics.py. This file covers CCHH's own
adapter (partials/human_capital_profiles_panel.html's inline <script>) —
Candidates is SSR/static, so unlike Commercial it never rebuilds HTML from a
column array; it moves already-rendered DOM nodes into the stored order."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "human_capital_candidates_table_ergonomics.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_human_capital_candidates_table_ergonomics_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Human Capital candidates table ergonomics failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL HUMAN CAPITAL CANDIDATES TABLE ERGONOMICS INVARIANTS PASSED" in result.stdout
