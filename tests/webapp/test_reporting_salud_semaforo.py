"""
test_reporting_salud_semaforo.py — SALUD DE CLIENTE: UX semáforo + rollup
semántico en filas padre colapsadas. Ejecuta las funciones REALES del semáforo
(saludScore/statusPillSalud/saludRollup) vía node + verifica el cableado en las
columnas y el primitive visual compartido.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_salud_semaforo.node.js"
_REPORTING_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")
_TOOLS_CSS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_salud_semaforo_and_rollup_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"salud semáforo invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL SALUD SEMAFORO INVARIANTS PASSED" in result.stdout


def test_salud_columns_declare_semantic_rollup():
    # Objetivo 2: las 6 columnas de salud declaran rollup semántico → la fila
    # padre colapsada muestra un valor agregado útil (antes: blanco).
    cols = _REPORTING_JS[_REPORTING_JS.index("const COMERCIAL_SALUD_COLUMNS = ["):_REPORTING_JS.index("const COMERCIAL_SALUD_COLUMNS = [") + 900]
    for field in ("saludFinanciera", "saludRelacion", "saludOperativo",
                  "saludContinuidad", "saludEstrategico", "saludFinal"):
        assert f'saludRollup("{field}")' in cols, field


def test_salud_reuses_shared_status_badge_primitive_not_isolated_styling():
    # Objetivo 1: reusa el primitive compartido `dashboard-status-badge--{color}`
    # (no styling aislado) + un punto semafórico discreto aria-hidden.
    fn = _REPORTING_JS[_REPORTING_JS.index("function saludBadge"):_REPORTING_JS.index("function saludBadge") + 500]
    assert "dashboard-status-badge dashboard-status-badge--" in fn
    assert 'class="salud-dot" aria-hidden="true"' in fn  # semáforo, no solo color
    assert ".dashboard-status-badge .salud-dot" in _TOOLS_CSS
    # Los 4 tonos del semáforo existen en el primitive compartido.
    for color in ("green", "amber", "red", "grey"):
        assert f".dashboard-status-badge--{color}" in _TOOLS_CSS
