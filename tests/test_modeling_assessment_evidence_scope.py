"""
Alcance de evidencia (familias adquiridas) y su efecto epistémico en el Modeling Assessment.

AUDITORÍA DE USO (estática + empírica) de las familias opcionales del extractor:
  * replication-flows  -> CONSUMIDA: ``readers._read_replication_flow`` -> ``ingestion.processing_reference`` / ``ingestion.processing_context_inventory``
                          / ``source.leaf_contract_status`` (``processing_reference_present`` y causa PLANE_BOUNDARY vs PROVENANCE_UNKNOWN).
  * data-flows         -> CONSUMIDA: igual, más ``ingestion.dataflow_boundary`` (referencias de frontera derivadas de los archivos de Data Flow).
  * transformation-flows -> NO consumida (ningún lector ni hecho la interpreta; los archivos en el pool no alteran el documento).
  * task-chains        -> NO consumida (idem).
Los hechos de plano PROCESSING son cross-plane (rol XPL): no alteran denominadores de cobertura. La única superficie REQUERIDA que depende de
la evidencia de flows es ``source.leaf_contract_status`` (unidades siempre NOT_ASSESSED): cobertura y confianza NO cambian; cambia la CAUSA.
Regla: lo NO adquirido no se convierte en "ausente evaluado".
"""
from __future__ import annotations

import copy
import json
import re
import tempfile
from pathlib import Path

from tools.catalog.modeling_assessment import profile as ma_profile
from tools.catalog.modeling_assessment import shadow as ma_shadow
from tools.catalog.modeling_assessment.render_md import render_markdown

AM = "4AM_X"
ALL_FLOWS = ["data-flows", "replication-flows"]


def _w(root: Path, rel: str, body) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body), encoding="utf-8")


def _pool(*, data_flow=False, transformation_flow=False, task_chain=False) -> Path:
    root = Path(tempfile.mkdtemp(prefix="evscope_"))
    col = {"A": {"type": "cds.String", "@EndUserText.label": "A"}}
    _w(root, "Acme/local-tables/LOCAL_X.json", {"definitions": {"LOCAL_X": {"kind": "entity", "elements": {"A": {"type": "cds.String"}}}}})
    _w(root, "Acme/views/3VF_X.json", {"definitions": {"3VF_X": {"kind": "entity", "elements": col, "@ObjectModel.modelingPattern": {"#": "ANALYTICAL_FACT"},
        "query": {"SELECT": {"from": {"ref": ["LOCAL_X"]}, "columns": [{"ref": ["LOCAL_X", "A"], "as": "A"}]}}}}})
    _w(root, f"Acme/analytic-models/{AM}.json", {"definitions": {AM: {"kind": "entity", "elements": col, "@ObjectModel.modelingPattern": {"#": "ANALYTICAL_CUBE"},
        "@Analytics.dataCategory": {"#": "CUBE"}, "query": {"SELECT": {"from": {"ref": ["3VF_X"]}, "columns": [{"ref": ["3VF_X", "A"], "as": "A"}]}}}}})
    if data_flow:
        _w(root, "Acme/data-flows/DF_X.json", {"dataflows": {"DF_X": {"sources": {"SRC": {}}, "targets": {"LOCAL_X": {}}}}})
    if transformation_flow:
        _w(root, "Acme/transformation-flows/TF_X.json", {"transformationflows": {"TF_X": {"sources": {"LOCAL_X": {}}, "targets": {"LOCAL_Y": {}}}}})
    if task_chain:
        _w(root, "Acme/task-chains/TC_X.json", {"taskchains": {"TC_X": {"nodes": [{"name": "DF_X"}, {"name": "TF_X"}]}}})
    return root


def _doc(root: Path, scope: dict | None = None) -> dict:
    kw = {"evidence_scope": scope} if scope else {}
    doc, _meta = ma_shadow.assess(root, "Acme", AM, "General", **kw)
    return doc


def _fact(doc: dict, fact_id: str) -> dict:
    return next(f for f in doc["fact_index"] if f["fact"] == fact_id)


def _limitation_code(doc: dict) -> str:
    return next(l for l in doc["evidence_limitations"] if l["surface_id"] == "source.leaf_contract_status")["reason"]["reason_code"]


def _comparable(doc: dict) -> dict:
    d = copy.deepcopy(doc)
    d["scope"].pop("pool_signature")
    d["scope"].pop("evidence_scope")
    return d


# ── escenarios: adquirido y presente / adquirido y ausente / NO adquirido ──────────────────────────
def test_acquired_and_present_flow_reference_is_a_plane_boundary():
    doc = _doc(_pool(data_flow=True), {"effective_features": ALL_FLOWS, "requested_features": ALL_FLOWS})
    assert _fact(doc, "source.leaf_contract_status@LOCAL_X")["evidence_class"] == "D"
    assert _fact(doc, "ingestion.processing_reference@LOCAL_X")["assessability"] == "ASSESSED"
    assert _limitation_code(doc) == "PROCESSING_PLANE_BOUNDARY"


def test_acquired_and_absent_is_a_real_absence_provenance_not_observable():
    doc = _doc(_pool(), {"effective_features": ALL_FLOWS, "requested_features": ALL_FLOWS})
    assert _limitation_code(doc) == "SOURCE_PROVENANCE_NOT_OBSERVABLE"
    assert _fact(doc, "ingestion.dataflow_boundary@closure")["assessability"] == "ASSESSED"
    assert _fact(doc, "ingestion.processing_context_inventory@closure")["assessability"] == "ASSESSED"


def test_not_acquired_data_flows_is_never_represented_as_acquired_and_absent():
    doc = _doc(_pool(), {"effective_features": ["replication-flows"], "requested_features": ["replication-flows"]})
    assert _limitation_code(doc) == "SUPPLEMENTARY_EVIDENCE_NOT_PROVIDED"                    # "no formaba parte del conjunto evaluado"
    assert _fact(doc, "ingestion.dataflow_boundary@closure")["assessability"] == "NOT_ASSESSED"
    assert _fact(doc, "ingestion.processing_context_inventory@closure")["assessability"] == "PARTIALLY_ASSESSED"
    assert _fact(doc, "source.leaf_contract_status@LOCAL_X")["evidence_class"] == "C1"


def test_no_flow_family_acquired_marks_the_whole_processing_context_not_assessed():
    doc = _doc(_pool(), {"effective_features": [], "requested_features": []})
    assert _fact(doc, "ingestion.processing_context_inventory@closure")["assessability"] == "NOT_ASSESSED"
    assert _fact(doc, "ingestion.dataflow_boundary@closure")["assessability"] == "NOT_ASSESSED"
    assert _limitation_code(doc) == "SUPPLEMENTARY_EVIDENCE_NOT_PROVIDED"


def test_unknown_scope_keeps_the_previous_behavior_exactly():
    legacy = _doc(_pool(data_flow=True))
    assert legacy["scope"]["evidence_scope"] is None
    full = _doc(_pool(data_flow=True), {"effective_features": ALL_FLOWS, "requested_features": ALL_FLOWS})
    assert _comparable(legacy) == _comparable(full)                                          # alcance completo == alcance desconocido (hoy)


def test_coverage_confidence_and_findings_do_not_change_only_the_reason_of_the_unassessed_leaf():
    full = _doc(_pool(), {"effective_features": ALL_FLOWS, "requested_features": ALL_FLOWS})
    part = _doc(_pool(), {"effective_features": ["replication-flows"], "requested_features": ["replication-flows"]})
    for key in ("summary", "observations", "review_required", "not_applicable", "descriptive_signals"):
        assert json.dumps(full[key], sort_keys=True) == json.dumps(part[key], sort_keys=True), key
    assert json.dumps(full["evidence_limitations"], sort_keys=True) != json.dumps(part["evidence_limitations"], sort_keys=True)


# ── el alcance viaja en el documento canónico ───────────────────────────────────────────────────
def test_document_preserves_requested_and_effective_features_and_validates():
    scope = {"requested_features": ["replication-flows", "tables", "views", "analytic-models"], "effective_features": ["replication-flows", "tables", "views", "analytic-models"]}
    doc = _doc(_pool(), scope)
    assert doc["scope"]["evidence_scope"] == {"requested_features": sorted(scope["requested_features"]), "effective_features": sorted(scope["effective_features"])}
    ma_profile.validate_assessment(doc)
    assert '"evidence_scope"' in ma_profile.canonical_assessment_json(doc)


def test_requested_and_effective_are_kept_distinct():
    doc = _doc(_pool(), {"requested_features": ["task-chains"], "effective_features": ["replication-flows", "task-chains"]})
    es = doc["scope"]["evidence_scope"]
    assert es["requested_features"] == ["task-chains"] and es["effective_features"] == ["replication-flows", "task-chains"]


def test_markdown_lists_non_acquired_families_only_when_scope_is_known_and_limited():
    full = {"effective_features": ["analytic-models", "views", "tables", "replication-flows", "data-flows", "transformation-flows", "task-chains"], "requested_features": []}
    limited = {"effective_features": ["analytic-models", "views", "tables", "replication-flows"], "requested_features": []}
    assert "Evidencia no adquirida" not in render_markdown(_doc(_pool(), full))
    assert "Evidencia no adquirida" not in render_markdown(_doc(_pool()))                    # alcance desconocido: sin línea
    md = render_markdown(_doc(_pool(), limited))
    assert "- **Evidencia no adquirida:** Data Flows, Transformation Flows, Task Chains" in md and "no implica que no existan" in md


def test_old_cached_documents_without_evidence_scope_remain_valid():
    doc = _doc(_pool())
    doc["scope"].pop("evidence_scope")                                                       # perfil cacheado antes del campo
    ma_profile.validate_assessment(doc)
    assert "Evidencia no adquirida" not in render_markdown(doc)


# ── auditoría: TF / TC no alteran el assessment ─────────────────────────────────────────────────
def test_transformation_flows_and_task_chains_in_the_pool_do_not_change_the_assessment():
    base = _doc(_pool(data_flow=True))
    extra = _doc(_pool(data_flow=True, transformation_flow=True, task_chain=True))
    assert _comparable(base) == _comparable(extra)


def test_static_usage_audit_only_replication_and_data_flows_are_interpreted():
    pkg = Path(__file__).resolve().parents[1] / "tools" / "catalog" / "modeling_assessment"
    pattern = re.compile(r"transformation[-_ ]?flows?|task[-_ ]?chains?|transformationflows|taskchains", re.I)
    code_hits = []
    for p in pkg.glob("*.py"):
        if p.name == "render_md.py":                                                          # solo rótulos de presentación del alcance
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line) and not line.lstrip().startswith("#") and "Task Chains y Transformation Flows" not in line:
                code_hits.append(f"{p.name}:{n}")
    assert code_hits == [], code_hits                                                         # ningún lector/hecho/regla interpreta TF/TC
    readers = (pkg / "readers.py").read_text(encoding="utf-8")
    assert '"replicationflows"' in readers and '"dataflows"' in readers
