"""
test_commercial_analytics_scoped_retry.py — Owner corrective "COMMERCIAL —
SCOPED RETRY FOR PIPELINE ANALYTICS" (ver HANDOFF). Ver
commercial_analytics_scoped_retry.node.js para las invariantes del motor
real: Summary READY + Analytics ERROR renderiza los dos estados a la vez;
el reintento pide EXACTAMENTE `/comercial/data/analytics` (cero requests a
los otros endpoints, incluidos los dos detalles lazy); loading escopeado
sólo dentro de la sección; éxito reemplaza SÓLO el body; Resumen queda
byte-idéntico; un reintento que vuelve a fallar deja salida (Reintentar de
nuevo); single-flight contra clicks repetidos; y Tipo-safety (una respuesta
obsoleta, de un Tipo que el usuario ya abandonó, nunca se adopta).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "commercial_analytics_scoped_retry.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_analytics_scoped_retry_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Commercial analytics scoped retry invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL COMMERCIAL ANALYTICS SCOPED RETRY INVARIANTS PASSED" in result.stdout
