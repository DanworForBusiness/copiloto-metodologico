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
    # La tile del listado (`catalog_flows.html`) ya NO está congelada: desde el gate «grado de la tile = Adopción metodológica» su grado
    # primario sale del Juicio persistido (ver test_catalog_assessment_routes). Los módulos de scoring legacy siguen intactos.
    out = subprocess.run(["git", "diff", "-U0", "main", "--", "tools/catalog/generate_catalog.py", "tools/catalog/catalog_service.py"],
                         cwd=REPO, capture_output=True, text=True)
    if out.returncode != 0:                                                  # sin rama `main` local (p. ej. CI en detached HEAD): no aplica
        return
    # La ÚNICA excepción admitida es la firma del cache incremental del generador (`_catalog_generator_signature`), que desde portability-scoring/v2 también cubre el
    # evaluador y la matriz de portabilidad para que un cambio de scoring invalide el cache; la evaluación de cumplimiento/veredicto legacy sigue intacta.
    # Segunda excepción admitida: el LOADER lee los Data Flows del export CLI (`dataflows`: sources/targets) y los une al linaje de la tabla que producen
    # (`dataflow_lineage`); es entrada del catálogo, no scoring (el assessment la desactiva) y `evaluate_compliance` no se toca.
    changed = [l for l in out.stdout.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    allowed = re.compile(r"signature|sha256|parts|portability_bdc|Firma del generador|reutilizamos salidas viejas|hashlib|invalid|scoring|persiste|here|extra|unknown|try:|except|return|\"\"\"|^[+-]\s*$|cache incremental|no reutilizamos|def load_folder|dataflow_lineage|export_df_membership|_dfname|_dfbody|_srcs|_tgts|_slot|slot\[|srcs|tname|Data Flow del export CLI|mixed-mode|MISMA regla")
    assert all(allowed.search(l) for l in changed), changed
    assert "catalog_service.py" not in out.stdout, out.stdout


def test_assessment_surfaces_never_use_the_legacy_verdict_vocabulary_for_coverage_or_confidence():
    for rel in ("webapp/templates/partials/catalog_assessment_report.html", "webapp/services/assessment_presenter.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        visible = re.sub(r"\{#.*?#\}", "", src, flags=re.S)
        # La presentación del Juicio metodológico (Gate 8D.2) usa su taxonomía AUTORIZADA (BUENA/ADECUADA/PARCIAL/No
        # determinable); se excluye de esta regresión (validada aparte en test_judgment_persistence). La cobertura/confianza
        # del assessment siguen sin poder usar el vocabulario del veredicto legacy.
        visible = re.sub(r"# BEGIN judgment-presentation.*?# END judgment-presentation", "", visible, flags=re.S)
        assert not re.search(r"\b(PLENA|BUENA|PARCIAL|Plena|Buena|Parcial)\b", visible), rel
