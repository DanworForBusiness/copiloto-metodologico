"""
test_reporting_kpi_smart_accent.py — SMART KPI TILE ACCENT OVERRIDE.

Capa OPCIONAL de override semántico del accent/borde de las KPI tiles: un KPI
"firmado" (bueno/malo) pinta su borde verde (>0) / rojo (<0); 0/null conserva el
accent patronado por defecto; KPIs neutros (hours/count) nunca se overridean.
Ejecuta el PRIMITIVO REAL `renderDashboardKpi` (vía node, sandbox vm — no solo
asserts de source) + verifica que la lógica vive en el primitivo COMPARTIDO y el
CSS de los modificadores gana al patrón por posición.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_kpi_smart_accent.node.js"
_REPORTING_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")
_TOOLS_CSS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_smart_accent_behaviour_invariants_hold():
    # Rules 1-4 (comportamiento real del primitivo): neutro conserva accent;
    # firmado + → verde; firmado − → rojo; firmado 0/null → neutral.
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"smart-accent invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL SMART ACCENT INVARIANTS PASSED" in result.stdout


def test_smart_accent_lives_in_the_shared_kpi_primitive():
    # Rule 5: la lógica está en el primitivo COMPARTIDO renderDashboardKpi, no
    # duplicada por experiencia (un solo lugar computa el override semántico).
    fn = _REPORTING_JS[_REPORTING_JS.index("function renderDashboardKpi"):_REPORTING_JS.index("function reportingLoadingHtml")]
    assert "smartAccent" in fn
    assert 'effectiveAccent = n > 0 ? "positive" : "negative"' in fn
    # Derivado de la metadata semántica existente (format === "signed"), nunca de
    # parsear el texto renderteado.
    assert 'smartAccent === undefined ? (format === "signed")' in fn
    # Ningún otro lugar reimplementa el override: el JS arma la clase por
    # variable (`activity-kpi--${effectiveAccent}`), nunca un literal
    # `activity-kpi--positive`/`--negative` suelto en otra experiencia.
    assert "activity-kpi--positive" not in _REPORTING_JS
    assert "activity-kpi--negative" not in _REPORTING_JS
    # Y toda referencia a `effectiveAccent` (la variable del override) vive
    # dentro del primitivo — no se filtra a ningún renderer de experiencia.
    assert _REPORTING_JS.count("effectiveAccent") == fn.count("effectiveAccent")


def test_smart_accent_css_modifiers_win_over_positional_pattern():
    # Los modificadores semánticos deben GANAR al accent rotativo por posición
    # (nth-child, specificity 0,2,0): doble clase (0,2,0) declarada DESPUÉS.
    assert ".activity-kpi.activity-kpi--positive { --kpi-accent:" in _TOOLS_CSS
    assert ".activity-kpi.activity-kpi--negative { --kpi-accent:" in _TOOLS_CSS
    pos_i = _TOOLS_CSS.index(".activity-kpi.activity-kpi--positive")
    nth_i = _TOOLS_CSS.index(".activity-kpi:nth-child(1)")
    assert pos_i > nth_i, "los modificadores semánticos deben declararse DESPUÉS de los nth-child para ganar el desempate"
