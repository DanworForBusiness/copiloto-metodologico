"""Regression for successful Rentabilidad years without unassigned hours."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible")
def test_rentabilidad_loaded_year_lifecycle():
    harness = Path(__file__).with_name("reporting_rentabilidad_loaded_years.node.js")
    result = subprocess.run(["node", str(harness)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "ALL RENTABILIDAD LOADED YEAR INVARIANTS PASSED" in result.stdout
