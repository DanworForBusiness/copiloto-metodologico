"""
test_commercial_opportunities_table.py — Active Opportunities Phase 2 / C4
(ver HANDOFF): detalle real por oportunidad, reemplazando el placeholder
"Detalle por Etapa" (agrupado, Batch 2A). Ver
commercial_opportunities_table.node.js para las invariantes del motor real
(columnas exactas, Etiquetas ausente, totales, orden por columna texto/
numérico/fecha con ausentes deterministas, y el Funnel intocado).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "commercial_opportunities_table.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_opportunities_table_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Opportunities table invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL COMMERCIAL OPPORTUNITIES TABLE INVARIANTS PASSED" in result.stdout
