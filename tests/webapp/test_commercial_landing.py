"""
test_commercial_landing.py — Comercial / Landing Homologation: el Dashboard
recupera las DOS familias de tiles del CRM legacy (9 principales + 6
Analíticas del Pipeline) con su riqueza informativa, muestra la composición
New Name / Base Instalada de forma honesta (segmentos que se superponen, sin
resto inventado), no finge accionabilidad donde todavía no hay drilldown, y no
reserva espacio para el análisis contextual hasta que se lo acciona.

Ver commercial_landing.node.js para las invariantes del motor real.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "commercial_landing.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_landing_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Commercial landing invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL COMMERCIAL LANDING INVARIANTS PASSED" in result.stdout
