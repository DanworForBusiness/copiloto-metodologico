"""
test_reporting_rentabilidad_source_parity.py — RENTABILIDAD — PBIP <-> HUB
SOURCE-PARITY RECONCILIATION (owner live comparison vs PBIP, golden caso
2026/Junio/Mantenimiento/En curso). Ver reporting_rentabilidad_source_parity.node.js
para las invariantes de Vendidas per-proyecto/faceteada.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_rentabilidad_source_parity.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_rentabilidad_source_parity_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Rentabilidad source-parity invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL RENTABILIDAD SOURCE-PARITY INVARIANTS PASSED" in result.stdout
