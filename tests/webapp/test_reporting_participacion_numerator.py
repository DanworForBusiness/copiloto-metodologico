"""
test_reporting_participacion_numerator.py — REPORTING LIVE-QA CORRECTIVE
(owner: "127 cliente(s) visible, TODOS con % Participación = 0.00%").

Root cause: `_shape_participacion` produced `vendidasByYear`/
`certificadasByYear` as `{anio: number}` (escalar plano), pero el frontend
(`comercialParticipacionSelectedRows`) SIEMPRE esperó `{anio: {estado:
number}}` — itera `Object.keys(r.vendidasByYear[anio])` para sumar los
estados tildados. `Object.keys()` sobre un NÚMERO devuelve `[]` (un
primitivo no tiene claves propias enumerables), así que el `.forEach` nunca
ejecutaba su cuerpo — `vendidas`/`certificadas` quedaban en 0 SIEMPRE, sin
importar cuántos hechos reales existieran. Bucketizar el backend por
`(año, estado)` (MISMO grano que el frontend siempre esperó) resuelve esto.

Ejecuta la CADENA COMPLETA end-to-end (selected temporal fact total →
numerador → denominador fijo → % renderizado) vía node, con el escenario
EXACTO del owner (Años={2025,2026}, Estado={En curso}) — no solo presencia
de un patrón de código en el source.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_participacion_numerator.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_participacion_numerator_denominator_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Participacion numerator/denominator invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL PARTICIPACION NUMERATOR/DENOMINATOR INVARIANTS PASSED" in result.stdout
