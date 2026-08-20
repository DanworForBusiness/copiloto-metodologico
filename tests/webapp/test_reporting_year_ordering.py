"""
test_reporting_year_ordering.py — YEAR ORDERING corrective (owner decision
2026-08-19): homologa los selectores de Año del Hub a DESCENDENTE (más
reciente primero) y desacopla la semántica de "año default = más reciente
VÁLIDO" de la posición del array (audit previo encontró 4 `xxxDefaultAnio()`
acoplados a `options[options.length - 1]`, correcto SOLO mientras las
opciones fueran ascendentes).

Este harness carga `reporting.js` real en un sandbox Node (mismo patrón que
reporting_capacidad_ux_corrective.node.js) y prueba en runtime real:
  A. Cartera/Mantenimiento sigue descendente (ya lo era, sin regresión);
  B. Cartera/Proyecto — Año de Inicio pasa a descendente;
  C. `distinctValues()` genérico (facets de string no-año, p. ej. cliente)
     NO cambia de default — sigue ascendente;
  D-H. Rentabilidad/Horas Vendidas/Participación/Capacidad Productivas/
     Capacidad No Asignadas — descendente;
  I. `mostRecentValidYear` da el MISMO resultado sea el input ascendente,
     descendente, o sin ordenar;
  J. valores inválidos/no-año nunca ganan por accidente;
  K-N. los 4 defaults acoplados siguen eligiendo el año más reciente VÁLIDO,
     probado explícitamente contra un caso donde "el primero/último del
     array descendente" NO coincidiría con "el mayor valor numérico" si el
     helper mirara posición en vez de valor.

No prueba wall-clock ni requiere sleeps. El comportamiento hot/cold de
Capacidad/Productivas (ver test_reporting_capacidad_hotcold_lifecycle.py) y
el catálogo de años disponibles se prueban aparte — este archivo es
puramente sobre ORDEN de presentación y semántica de default, nunca sobre
qué años están hidratados/son hot/cold.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_year_ordering.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_year_ordering_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Year ordering invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL YEAR ORDERING INVARIANTS PASSED" in result.stdout
