"""
test_reporting_frecuencia_order.py — Calendario Frecuencia sort corrective
(owner live comparison vs PBIP). Ver reporting_frecuencia_order.node.js
para las invariantes del motor real (FRECUENCIA_ORDER/_frecuenciaCompare).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from webapp.services.reporting_service import _FRECUENCIA_ORDER

_HARNESS = Path(__file__).resolve().parent / "reporting_frecuencia_order.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_frecuencia_order_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Frecuencia order invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL FRECUENCIA ORDER INVARIANTS PASSED" in result.stdout


def test_backend_frecuencia_order_matches_pbip_switch():
    # Cliente.tmdl, columna 'ID Frecuencia': SWITCH(..., "Mensual",1,
    # "Bimestral",2, "Trimestral",3, "Cuatrimestral",4, "Semestral",5,
    # "Anual",6, ...) — evidencia PRIMARIA, nunca "Semanal".
    assert _FRECUENCIA_ORDER == ["Mensual", "Bimestral", "Trimestral", "Cuatrimestral", "Semestral", "Anual"]


_REPORTING_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")


def test_participacion_default_sort_is_chronological_frequency():
    # Owner: al cargar por primera vez, Participación ordena por cronología de
    # frecuencia (Mensual→Anual), no alfabético; el click en columnas gana luego.
    fn = _REPORTING_JS[_REPORTING_JS.index("function comercialParticipacionSorted"):_REPORTING_JS.index("function comercialParticipacionSorted") + 800]
    assert "if (!key)" in fn  # rama de default (sin click)
    assert "_frecuenciaCompare(a.idFrecuencia, b.idFrecuencia)" in fn  # reusa el comparador compartido (Calendario)


def test_participacion_title_drops_id_prefix():
    # Owner: sacar "ID Frecuencia" del título → solo "Frecuencia / ...".
    fn = _REPORTING_JS[_REPORTING_JS.index("function renderComercialParticipacion"):_REPORTING_JS.index("function renderComercialSalud")]
    assert "Frecuencia / Permite Adelanto / Condición de Pago / Cliente" in fn
    assert "ID Frecuencia /" not in fn


def test_participacion_has_total_row_like_calendario():
    # Owner: fila de totales (como Calendario) — `tr.is-total` con el rollup de
    # cada columna sobre TODAS las filas (denominador fijo PBIP, nunca "100%"
    # asumido: el total es el rollup real de la medida).
    fn = _REPORTING_JS[_REPORTING_JS.index("function renderComercialParticipacion"):_REPORTING_JS.index("function renderComercialSalud")]
    assert '<tr class="is-total"><td class="row-label">Total</td>' in fn
    assert "COMERCIAL_PARTICIPACION_COLUMNS.map(c => `<td>${c.rollup(rows)}</td>`)" in fn
