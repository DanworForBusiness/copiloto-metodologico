"""
test_commercial_capabilities.py — Comercial / Batch 3A (C3): navegación de
capacidades (Dashboard | Actividades) y la capability "Actividades
Pendientes". Ver commercial_capabilities.node.js para las invariantes del
motor real: registry sin tabs muertas, `aria-current` único, semántica de
FECHA CALENDARIO para days_until, semana lunes→domingo, buckets
Retrasadas/Pendientes con orden fijo y NO draggables, filtros locales
client-side y escaping de todo dato de Odoo.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "commercial_capabilities.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_capabilities_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Commercial capabilities invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL COMMERCIAL CAPABILITIES INVARIANTS PASSED" in result.stdout
