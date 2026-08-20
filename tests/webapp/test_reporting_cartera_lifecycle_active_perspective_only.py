"""
test_reporting_cartera_lifecycle_active_perspective_only.py — ACTIVE
EXPERIENCE + ACTIVE PERSPECTIVE ONLY corrective (owner decision
2026-08-19 — lifecycle audit: "Cartera es la experience default, hidrata
automáticamente en DOMContentLoaded, y hydrateCarteraTab cargaba las 3
fuentes -Mantenimiento, Proyecto, años- sin importar si el usuario
alguna vez visitaba Proyecto").

Reemplaza el corrective anterior "MAIN-TAB HYDRATION CONTRACT" (ambas
perspectivas eager, ver test_reporting_cartera.py) — el owner priorizó
"nunca iniciar una perspectiva que el usuario no pidió" por sobre la
ganancia de latencia que ese corrective buscaba. Este harness carga
`reporting.js` real en un sandbox Node (mismo patrón que
reporting_capacidad_hotcold_lifecycle.node.js/reporting_initial_load_
contract.node.js) con un `fetch` FAKE que cuenta llamadas por endpoint/
perspectiva, y prueba en runtime real:
  A. bootstrap Cartera/Mantenimiento — Mantenimiento + su metadata de años
     se solicitan, Proyecto NUNCA;
  B. cambiar a Proyecto dispara ESE fetch, exactamente una vez;
  C. volver a Mantenimiento (ya ready) — cero refetch;
  D. volver a Proyecto (ya ready) — cero refetch;
  E. renderizar repetidamente mientras el status ya no es "idle" nunca
     duplica requests;
  F. las otras 5 experiencias siguen hidratando con sus triggers actuales
     (sin regresión).

NO prueba cancelación cross-tab (fuera de alcance de este corrective,
ver owner decision — un fetch de Mantenimiento en vuelo sigue
completando en background si el usuario cambia de tab antes de que
resuelva)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_cartera_lifecycle_active_perspective_only.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_cartera_active_perspective_only_lifecycle_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Cartera active-perspective-only lifecycle invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL CARTERA ACTIVE-PERSPECTIVE-ONLY LIFECYCLE INVARIANTS PASSED" in result.stdout
