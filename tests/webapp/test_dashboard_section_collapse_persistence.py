"""
test_dashboard_section_collapse_persistence.py — wrapper del harness node que
prueba la persistencia de COLAPSO opt-in del primitivo compartido
`initDashboardSections` (sobrevive HTMX swaps; opt-in via `data-persist-collapse`,
sin regresión para los consumidores que no lo piden). Ver el .node.js."""
from __future__ import annotations
import shutil, subprocess
from pathlib import Path
import pytest

_H = Path(__file__).resolve().parent / "dashboard_section_collapse_persistence.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible")
def test_dashboard_section_collapse_persistence_invariants_hold():
    r = subprocess.run(["node", str(_H)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"collapse persistence invariants failed:\n{r.stdout}\n{r.stderr}"
    assert "ALL DASHBOARD SECTION COLLAPSE PERSISTENCE INVARIANTS PASSED" in r.stdout
