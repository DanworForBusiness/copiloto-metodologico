"""
Raíz genérica del assessment: objeto raíz (modelo analítico | vista | tabla local | tabla remota) + su clausura semántica UPSTREAM.

MATRIZ DE APLICABILIDAD (derivada del registro real de superficies; ver ``test_applicability_matrix_*``):
  * Evidencia/regla ESPECÍFICA de Modelo Analítico -> NOT_APPLICABLE (razón ROOT_NOT_ANALYTIC_MODEL) cuando la raíz no es AM:
      am.contract_inventory, am.association_targets, am.contract_delta, am.numeric_attributes_not_declared_as_measures,
      harmonization.cross_am_reuse (el reúso entre AM es un concepto de AM), rule.am_semantic_underuse_review.
  * Todo lo demás (fuente, ingesta, propagación, armonización local, nomenclatura, procesamiento) se calcula sobre la clausura de la raíz:
      APPLICABLE para vista; CONDICIONAL para tablas (la clausura es la propia tabla: sin linaje upstream las superficies de arista quedan
      NOT_APPLICABLE por NO_UNITS_IN_SCOPE o evaluadas sobre la tabla).
Las superficies N/A NO reducen la cobertura (salen del denominador) y NO se inventa ninguna penalidad.
"""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

import pytest

from tools.catalog.modeling_assessment import profile as ma_profile
from tools.catalog.modeling_assessment import shadow as ma_shadow
from tools.catalog.modeling_assessment.assessability import SURFACE_REGISTRY
from tools.catalog.modeling_assessment.render_md import render_markdown
from tools.catalog.modeling_assessment.scope import UnknownRootObjectError, UnknownTerminalAmError

AM_ONLY = {"am.contract_inventory", "am.association_targets", "am.contract_delta", "am.numeric_attributes_not_declared_as_measures",
           "harmonization.cross_am_reuse", "rule.am_semantic_underuse_review"}
DEC = {"type": "cds.Decimal", "precision": 13, "scale": 3}


def _w(root: Path, rel: str, body) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body), encoding="utf-8")


def _pool() -> Path:
    """Acme: tabla local LT_DIM, tabla remota RAW_T, vistas 1VR_RAW -> 3VF_P, AM 4AM_X (con etiqueta de negocio)."""
    root = Path(tempfile.mkdtemp(prefix="genroot_"))
    col = {"A": {"type": "cds.String", "@EndUserText.label": "A"},
           "M": {**DEC, "@AnalyticsDetails.measureType": {"#": "AGGREGATED"}, "@EndUserText.label": "M"}}

    def view(name, src, **extra):
        body = {"kind": "entity", "elements": col,
                "query": {"SELECT": {"from": {"ref": [src]}, "columns": [{"ref": [src, "A"], "as": "A"}, {"ref": [src, "M"], "as": "M"}]}}}
        body.update(extra)
        return {"definitions": {name: body}}
    _w(root, "Acme/local-tables/LT_DIM.json", {"definitions": {"LT_DIM": {"kind": "entity", "elements": {"A": {"type": "cds.String"}, "M": DEC}}}})
    _w(root, "Acme/remote-tables/RAW_T.json", {"definitions": {"RAW_T": {"kind": "entity", "elements": {"A": {"type": "cds.String"}, "M": DEC},
        "@DataWarehouse.remote.connection": "S4PREM", "@DataWarehouse.remote.entity": "ABAPTABLES.TAB1"}}})
    _w(root, "Acme/views/1VR_RAW.json", view("1VR_RAW", "RAW_T"))
    _w(root, "Acme/views/3VF_P.json", view("3VF_P", "1VR_RAW", **{"@ObjectModel.modelingPattern": {"#": "ANALYTICAL_FACT"}}))
    am = view("4AM_X", "3VF_P", **{"@ObjectModel.modelingPattern": {"#": "ANALYTICAL_CUBE"}, "@Analytics.dataCategory": {"#": "CUBE"}})
    am["definitions"]["4AM_X"]["@EndUserText.label"] = "Modelo de prueba"
    _w(root, "Acme/analytic-models/4AM_X.json", am)
    return root


ROOTS = [("4AM_X", "analytic-model"), ("3VF_P", "view"), ("1VR_RAW", "view"), ("RAW_T", "remote-table"), ("LT_DIM", "local-table")]


def _doc(root: Path, name: str, hint: str | None, **kw) -> dict:
    doc, _meta = ma_shadow.assess(root, "Acme", name, "General", generic_root=True, root_type=hint, **kw)
    return doc


@pytest.fixture(scope="module")
def pool() -> Path:
    return _pool()


@pytest.mark.parametrize("name,root_type", ROOTS)
def test_every_supported_root_type_yields_a_structurally_valid_assessment(pool, name, root_type):
    doc = _doc(pool, name, root_type)
    ma_profile.validate_assessment(doc)
    assert doc["assessment_state"] == "ASSESSED" and doc["scope"]["applicability"] == "APPLICABLE"
    assert doc["scope"]["root_object"] == name and doc["scope"]["root_type"] == root_type and name in doc["scope"]["closure_artifacts"]
    assert doc["summary"]["coverage"] is not None and doc["summary"]["confidence"]["level"] in ("HIGH", "MEDIUM", "LOW")
    json.loads(ma_profile.canonical_assessment_json(doc))
    assert render_markdown(doc)


@pytest.mark.parametrize("name,root_type", ROOTS[1:])
def test_non_am_roots_never_fabricate_a_terminal_analytic_model(pool, name, root_type):
    doc = _doc(pool, name, root_type)
    assert doc["scope"]["terminal_analytic_model"] is None and doc["scope"]["terminal_analytic_model_label"] is None
    md = render_markdown(doc)
    assert f"- **Objeto raíz:** {name}" in md and "Modelo analítico terminal:" not in md and "del modelo analítico terminal" not in md


def test_closure_is_the_root_plus_its_upstream_only_never_downstream(pool):
    assert set(_doc(pool, "3VF_P", "view")["scope"]["closure_artifacts"]) == {"3VF_P", "1VR_RAW", "RAW_T"}          # sin 4AM_X (consumidor)
    assert _doc(pool, "RAW_T", "remote-table")["scope"]["closure_artifacts"] == ["RAW_T"]
    assert _doc(pool, "LT_DIM", "local-table")["scope"]["closure_artifacts"] == ["LT_DIM"]


@pytest.mark.parametrize("name,root_type", ROOTS[1:])
def test_am_only_surfaces_are_not_applicable_for_lower_roots_with_the_dedicated_reason(pool, name, root_type):
    doc = _doc(pool, name, root_type)
    na = {n["surface_id"]: n["reason"]["reason_code"] for n in doc["not_applicable"]}
    for surface in AM_ONLY:
        assert na.get(surface) == "ROOT_NOT_ANALYTIC_MODEL", (surface, na.get(surface))
    assert not any(l["surface_id"] in AM_ONLY for l in doc["evidence_limitations"])          # no aparecen como "no evaluado"


def test_am_root_keeps_its_am_surfaces_applicable_and_unchanged(pool):
    doc = _doc(pool, "4AM_X", "analytic-model")
    na = {n["surface_id"] for n in doc["not_applicable"]}
    assert not ({"am.contract_inventory", "am.association_targets", "am.contract_delta", "rule.am_semantic_underuse_review"} & na)
    legacy, _ = ma_shadow.assess(pool, "Acme", "4AM_X", "General")                           # camino de siempre (Catálogo)
    a, b = copy.deepcopy(doc), copy.deepcopy(legacy)
    for d in (a, b):
        for k in ("root_object", "root_type", "root_label"):
            d["scope"].pop(k, None)
    assert a == b                                                                              # generic_root solo AGREGA identidad para un AM
    assert doc["scope"]["terminal_analytic_model"] == "4AM_X" and doc["scope"]["root_object"] == "4AM_X" and doc["scope"]["root_label"] == "Modelo de prueba"
    assert doc["scope"]["terminal_analytic_model_label"] == "Modelo de prueba"


@pytest.mark.parametrize("name,root_type", ROOTS[1:])
def test_not_applicable_surfaces_are_excluded_from_the_coverage_denominator(pool, name, root_type):
    doc = _doc(pool, name, root_type)
    cov = doc["summary"]["coverage"]
    req = cov["required_surfaces"]
    assert req["not_applicable"] >= 4                                                          # las superficies solo-AM salen del denominador
    assert req["applicable"] == req["assessed"] + req["partially_assessed"] + req["not_assessed"]
    assert cov["strict"] is None or 0 <= cov["strict"] <= 1


def test_confidence_is_not_lowered_merely_because_the_root_is_not_an_analytic_model(pool):
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    am = _doc(pool, "4AM_X", "analytic-model")["summary"]["confidence"]["level"]
    view = _doc(pool, "3VF_P", "view")["summary"]["confidence"]["level"]
    assert order[view] >= order[am]                                                          # el N/A de AM no es un factor de confianza


def test_applicability_matrix_is_derived_from_the_registry_and_am_only_rows_are_not_applicable(pool):
    docs = {f"{rt}:{n}": _doc(pool, n, rt) for n, rt in ROOTS}
    ids = [s.surface_id for s in SURFACE_REGISTRY]
    assert len(ids) == len(set(ids)) and AM_ONLY <= set(ids)
    for sid in AM_ONLY:
        for key, d in docs.items():
            state = "NOT_APPLICABLE" if any(s["surface_id"] == sid for s in d["not_applicable"]) else "APPLICABLE"
            assert (state == "NOT_APPLICABLE") == (not key.startswith("analytic-model") or sid == "harmonization.cross_am_reuse"), (sid, key)


# ── Catálogo: el modo estricto no cambia ───────────────────────────────────────
def test_catalog_path_still_requires_a_terminal_analytic_model(pool):
    with pytest.raises(UnknownTerminalAmError):
        ma_shadow.assess(pool, "Acme", "3VF_P", "General")                                  # sin generic_root: solo un AM terminal es unidad
    with pytest.raises(UnknownRootObjectError):
        ma_shadow.assess(pool, "Acme", "NO_EXISTE", "General", generic_root=True)           # objeto ausente en la evidencia
    assert issubclass(UnknownRootObjectError, UnknownTerminalAmError)


def test_root_type_is_inferred_from_the_evidence_when_the_extractor_gives_no_hint(pool):
    for name, expected in (("3VF_P", "view"), ("RAW_T", "remote-table"), ("LT_DIM", "local-table")):
        assert _doc(pool, name, None)["scope"]["root_type"] == expected


# ── alcance de evidencia sobre raíces genéricas ────────────────────────────────
def test_evidence_scope_epistemics_hold_for_a_view_root(pool):
    partial = {"requested_features": ["views", "tables"], "effective_features": ["views", "tables"]}
    doc = _doc(pool, "3VF_P", "view", evidence_scope=partial)
    assert doc["scope"]["evidence_scope"]["effective_features"] == ["tables", "views"]
    assert {f["fact"]: f["assessability"] for f in doc["fact_index"]}["ingestion.dataflow_boundary@closure"] == "NOT_ASSESSED"   # no adquirido != ausente
    full = {"requested_features": ["data-flows", "replication-flows"], "effective_features": ["data-flows", "replication-flows"]}
    doc2 = _doc(pool, "3VF_P", "view", evidence_scope=full)
    assert {f["fact"]: f["assessability"] for f in doc2["fact_index"]}["ingestion.dataflow_boundary@closure"] == "ASSESSED"


def test_old_documents_without_root_identity_still_render_as_analytic_model_reports(pool):
    doc = _doc(pool, "4AM_X", "analytic-model")
    for k in ("root_object", "root_type", "root_label"):
        doc["scope"].pop(k)
    ma_profile.validate_assessment(doc)
    md = render_markdown(doc)
    assert "- **Modelo analítico terminal:** 4AM_X" in md and "Objeto raíz" not in md
