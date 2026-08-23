"""
test_reporting_capacidad_hotcold_lifecycle.py — HOT WINDOW / COLD HISTORY
corrective (Horas Productivas, owner decision 2026-08-18 — "PRODUCTIVAS —
COMPLETAR TESTS FALTANTES", sección 9).

Los tests de backend (test_reporting_batch_b.py) ya prueban, contra
`core.microsoft365` mockeado, que:
  A. el catálogo de años (`/anios-disponibles`) no descarga ningún CSV;
  B. la carga inicial (`anios=None`) descarga SOLO el año actual+anterior;
  C. un año frío explícito descarga SOLO ese archivo.

Pero ninguno de esos tests ejercita el LIFECYCLE del lado JS — el owner
señaló explícitamente "no basta con testear helpers aislados, cubrir el
lifecycle JS que faltaba". Este harness carga `reporting.js` real en un
sandbox Node (mismo patrón que reporting_initial_load_contract.node.js) con
un `fetch` FAKE que responde según el endpoint/parámetro `anios`, y prueba
en runtime real:
  D. available years (2021..2026) permanecen visibles como opciones aunque
     LOADED inicialmente sea solo 2025/2026 (`capacidadProductivasAnioOptions`
     nunca se angosta a los años ya hidratados);
  E. seleccionar un año frío (2024) dispara EXACTAMENTE un fetch;
  F. reseleccionar/reusar ese mismo año ya cargado NO dispara un fetch
     adicional;
  G. el selector de años sigue completo (2021..2026) después del merge;
  H. single-flight: dos intentos concurrentes del mismo año frío (2023)
     generan UNA sola request, nunca dos.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_capacidad_hotcold_lifecycle.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_capacidad_hotcold_lifecycle_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Capacidad hot/cold lifecycle invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL CAPACIDAD HOT/COLD LIFECYCLE INVARIANTS PASSED" in result.stdout
