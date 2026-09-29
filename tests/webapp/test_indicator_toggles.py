"""
test_indicator_toggles.py — wrapper del harness node que prueba el primitivo
Hub-wide `initIndicatorToggles` (app.js): checkboxes de presentación
("Indicadores") que muestran/ocultan metadata contextual, 100% cliente,
persistido en localStorage, NUNCA acoplado a filtrado/query. Ver el .node.js."""
from __future__ import annotations
import shutil, subprocess
from pathlib import Path
import pytest

_H = Path(__file__).resolve().parent / "indicator_toggles.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible")
def test_indicator_toggle_invariants_hold():
    r = subprocess.run(["node", str(_H)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"indicator toggle invariants failed:\n{r.stdout}\n{r.stderr}"
    assert "ALL INDICATOR TOGGLE INVARIANTS PASSED" in r.stdout
