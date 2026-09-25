"""test_commercial_table_action_icons.py — owner "Iconographic Table Actions"
(ver HANDOFF): las tres acciones de fila de Licencias pasan de texto a ícono
compacto + tooltip + aria-label, preservando el comportamiento exacto.

El contrato de CLICK/propagación (ya en verde con este markup) vive en
test_commercial_licensing_action_wiring.py. Este archivo cubre lo nuevo: el
ícono, el nombre accesible, el tooltip visible, la columna compacta, y que el
primitivo (`commercialTableActionButtonHtml` + `.table-action-btn`/
`.chart-tooltip-anchor`) sea genuinamente reusable, no atado a Comercial.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "commercial_table_action_icons.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_table_action_icons_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Commercial table action icons failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL COMMERCIAL TABLE ACTION ICON INVARIANTS PASSED" in result.stdout
