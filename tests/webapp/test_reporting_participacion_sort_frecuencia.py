"""
test_reporting_participacion_sort_frecuencia.py — Participación/Comercial:
sortable % Participación columns + Frecuencia filter (owner request). Ver
reporting_participacion_sort_frecuencia.node.js para las invariantes del
motor real.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_participacion_sort_frecuencia.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_participacion_sort_frecuencia_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Participacion sort/frecuencia invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL PARTICIPACION SORT/FRECUENCIA INVARIANTS PASSED" in result.stdout
