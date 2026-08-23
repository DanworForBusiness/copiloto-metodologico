"""
test_commercial_creadas_drill.py — Creadas Legacy Parity (ver HANDOFF —
paridad con el legacy `#created-drill-section`/`/crm/opp-by-month`). Ver
commercial_creadas_drill.node.js para las invariantes del motor real
(bucket de semana día-del-mes, año por default, ranking por vendedor, tabla
mensual con expansión, detalle por oportunidad, y los 4 estados del lazy
load).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "commercial_creadas_drill.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_creadas_drill_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Creadas drill invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL CREADAS DRILL INVARIANTS PASSED" in result.stdout
