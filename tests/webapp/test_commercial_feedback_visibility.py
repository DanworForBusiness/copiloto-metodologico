"""
test_commercial_feedback_visibility.py — Owner corrective "Homologación al
Danwor Hub — Feedback transversal" (ver HANDOFF). Ver
commercial_feedback_visibility.node.js para las invariantes del motor real:
visibilidad gateada por el status PRESENTE de cada capability (nunca
"alguna vez estuvo ready"), exactamente UN feedback por capability (nunca
por sub-tab), sin duplicarse al reabrir/filtrar/ordenar/cambiar de
sub-tab, sin fetches de datasets adicionales al reaccionar, estados
independientes entre capabilities, y reset completo al cambiar Todos/NN/BI.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "commercial_feedback_visibility.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_feedback_visibility_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Commercial feedback visibility invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL COMMERCIAL FEEDBACK VISIBILITY INVARIANTS PASSED" in result.stdout
