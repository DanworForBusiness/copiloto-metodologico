"""
test_reporting_capacidad_zero_total_exclusion.py — Owner corrective "Horas
Productivas / No Asignadas — fila total=0" (ver HANDOFF, owner live QA:
"Damián Berni", año 2026, ENE..DIC=0/Total=0 — esa fila no debe existir).
Ver reporting_capacidad_zero_total_exclusion.node.js para las invariantes del
motor real: exclusión por SUM(mensual del Año/Consultor activos) == 0
(nunca por meses individuales en 0), empty state canónico cuando el
consultor seleccionado o "Todos" quedan en cero filas, Total recalculado
sobre las filas visibles, recálculo correcto al cambiar de Año, Productivas
intacta, y cero requests adicionales (100% client-side).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_capacidad_zero_total_exclusion.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_capacidad_zero_total_exclusion_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Capacidad zero-total exclusion invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL CAPACIDAD ZERO-TOTAL EXCLUSION INVARIANTS PASSED" in result.stdout
