"""
test_reporting_capacidad_ux_corrective.py — OWNER LIVE UX CORRECTIVE (2da
pasada sobre Horas Productivas): Mes multi-select (botones, ya no un
`<select>` de valor único) + headers Horas/% Incidencia clickeables para
ordenar asc/desc. Renders the REAL functions against synthetic fixtures —
same pattern as reporting_capacidad_panel_independence.node.js.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_capacidad_ux_corrective.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_capacidad_ux_corrective_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Capacidad UX corrective invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL CAPACIDAD UX CORRECTIVE INVARIANTS PASSED" in result.stdout
