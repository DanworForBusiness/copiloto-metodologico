"""
Regresión de aislamiento: el assessment de modelado NO recalcula ni toca el veredicto/score legacy del Catálogo (PLENA/BUENA/PARCIAL,
apto) y nunca presenta cobertura/confianza como puntaje de madurez. Solo comprobaciones de aislamiento (no hay motor de scoring nuevo).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ASSESSMENT_WEB = ("webapp/services/assessment_service.py", "webapp/services/assessment_presenter.py", "webapp/routers/catalog_assessment.py")


def test_assessment_code_never_imports_or_calls_the_legacy_evaluator():
    for rel in ASSESSMENT_WEB:
        src = (REPO / rel).read_text(encoding="utf-8")
        assert not re.search(r"evaluate_compliance|generate_catalog\b|_catalog_verdict|catalog_verdict_|\.veredicto|\.apto|entry\.verdict", src), rel
    pkg = REPO / "tools/catalog/modeling_assessment"
    for path in pkg.glob("*.py"):
        if path.name == "legacy_bridge.py":                                  # puente solo-lectura del informe de validación shadow
            continue
        assert not re.search(r"^\s*(from|import)\s+.*generate_catalog", path.read_text(encoding="utf-8"), re.M), path.name


def test_legacy_list_tile_template_and_scoring_modules_are_untouched_by_the_assessment_front():
    import subprocess
    out = subprocess.run(["git", "diff", "--stat", "main", "--", "webapp/templates/partials/catalog_flows.html",
                          "tools/catalog/generate_catalog.py", "tools/catalog/catalog_service.py"],
                         cwd=REPO, capture_output=True, text=True)
    if out.returncode != 0:                                                  # sin rama `main` local (p. ej. CI en detached HEAD): no aplica
        return
    assert out.stdout.strip() == "", out.stdout


def test_assessment_surfaces_never_use_the_legacy_verdict_vocabulary_for_coverage_or_confidence():
    for rel in ("webapp/templates/partials/catalog_assessment_report.html", "webapp/services/assessment_presenter.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        visible = re.sub(r"\{#.*?#\}", "", src, flags=re.S)
        assert not re.search(r"\b(PLENA|BUENA|PARCIAL|Plena|Buena|Parcial)\b", visible), rel
