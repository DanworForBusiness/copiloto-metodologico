"""
test_reporting_frecuencia_order.py — Calendario Frecuencia sort corrective
(owner live comparison vs PBIP). Ver reporting_frecuencia_order.node.js
para las invariantes del motor real (FRECUENCIA_ORDER/_frecuenciaCompare).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from webapp.services.reporting_service import _FRECUENCIA_ORDER

_HARNESS = Path(__file__).resolve().parent / "reporting_frecuencia_order.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_frecuencia_order_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Frecuencia order invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL FRECUENCIA ORDER INVARIANTS PASSED" in result.stdout


def test_backend_frecuencia_order_matches_pbip_switch():
    # Cliente.tmdl, columna 'ID Frecuencia': SWITCH(..., "Mensual",1,
    # "Bimestral",2, "Trimestral",3, "Cuatrimestral",4, "Semestral",5,
    # "Anual",6, ...) — evidencia PRIMARIA, nunca "Semanal".
    assert _FRECUENCIA_ORDER == ["Mensual", "Bimestral", "Trimestral", "Cuatrimestral", "Semestral", "Anual"]
