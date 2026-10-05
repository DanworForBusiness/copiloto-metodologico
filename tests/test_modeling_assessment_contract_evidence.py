"""
Evidence release (Gate 8A.3) — S2-S6 + propiedades de Processing: hechos del CONTRATO SEMANTICO PUBLICADO, de la armonizacion, de la ubicacion de predicados,
de la reconvergencia de ramas y de la complejidad de vistas. Solo HECHOS descriptivos: sin juicio, sin puntaje, sin disposicion, sin etiqueta PLENA/BUENA/PARCIAL.

Pool sintetico (``tests/_evidence_release_pool.py``) con la forma del caso Illolay: ``2TR_VBAK`` producida por ``DF_VBAK`` desde la vista ``VBAK_HIST``.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests._evidence_release_pool import AM, write_pool
from tools.catalog.modeling_assessment import shadow as ma_shadow
from tools.catalog.modeling_assessment.evidence_contract import CanonicalEvidenceError, validate_canonical_evidence
from tools.catalog.modeling_assessment.model import FORBIDDEN_TOKENS, tokenize_identifier


@pytest.fixture(scope="module")
def doc() -> dict:
    d, _ = ma_shadow.assess(write_pool(bundled_df=True), "Acme", AM, "General")
    return d


def _ce(doc: dict) -> dict:
    return doc["canonical_evidence"]


def _walk_keys(o, acc):
    if isinstance(o, dict):
        for k, v in o.items():
            acc.append(k)
            _walk_keys(v, acc)
    elif isinstance(o, list):
        for v in o:
            _walk_keys(v, acc)
    return acc


def _art(doc: dict, name: str) -> dict:
    return next(a for a in _ce(doc)["harmonization_contract"]["artifacts"] if a["artifact"] == name)


# ═══════════════ S2 — contrato analitico publicado (17-28) ═══════════════
def test_associations_are_categorized_from_explicit_evidence_only(doc):
    by = {a["field"]: a for a in _ce(doc)["published_contract"]["associations"]["associations"]}
    assert by["_Dim"]["category"] == "BUSINESS_DIMENSION" and by["_Dim"]["categorization_basis"] == "DECLARED_DIMENSION_ROLE" and by["_Dim"]["target_declared_role"]["declares_dimension"]
    assert by["_Fecha"]["target"].startswith("SAP.TIME.") and by["_Fecha"]["target_in_graph"] is False       # destino de plataforma: no esta en el grafo, no es un artefacto del proyecto


def test_the_association_aggregates_report_totals_and_distinct_targets_never_a_quality_verdict(doc):
    agg = _ce(doc)["published_contract"]["associations"]["aggregates"]
    assert agg["total"] == 2 and agg["distinct_targets"] == 2 and sum(agg["by_category"].values()) == 2
    assert "not a quality measure" in _ce(doc)["published_contract"]["associations"]["definition"]


def test_an_association_to_an_undeclared_pool_target_is_unknown_not_guessed_as_business():
    root = write_pool(bundled_df=True)
    (root / "Acme/views/2VS_DIM.json").unlink()
    d, _ = ma_shadow.assess(root, "Acme", AM, "General")
    cats = {a["field"]: a for a in _ce(d)["published_contract"]["associations"]["associations"]}
    assert cats["_Dim"]["category"] == "UNKNOWN" and cats["_Dim"]["categorization_basis"] == "TARGET_NOT_IN_GRAPH"


def test_a_system_time_target_is_a_platform_dimension_when_the_loader_classifies_it_as_system():
    from tools.catalog.modeling_assessment.facts_contract_semantics import CAT_OTHER_PLATFORM, CAT_TIME_PLATFORM, _association_category

    class _Sys:
        class identity:                                                       # noqa: N801 - doble minimo de IdentityClassification
            value = "SYSTEM"
    assert _association_category("SAP.TIME.VIEW_DIMENSION_DAY", False, _Sys, None)[0] == CAT_TIME_PLATFORM
    assert _association_category("SAP.OTHER.THING", False, _Sys, None)[0] == CAT_OTHER_PLATFORM


def test_measure_type_composition_is_a_descriptive_population_with_no_defect_reading(doc):
    comp = _ce(doc)["published_contract"]["fields"]["measure_type_composition"]
    assert comp["measure_count"] == 3 and comp["family_count"] == 2 and comp["signature_count"] == 3 and comp["unresolved_type_count"] == 0
    sigs = {(s["family"], s["precision"], s["scale"]) for s in comp["signatures"]}
    assert sigs == {("DECIMAL", 20, 3), ("DECIMAL", 35, 3), ("FLOAT", None, None)}
    assert "not a defect" in comp["definition"]


def test_field_records_carry_the_declared_datatype_and_the_naming_facts_with_no_judgement(doc):
    f = next(x for x in _ce(doc)["published_contract"]["fields"]["fields"] if x["technical_name"] == "Cantidad")
    assert f["datatype"] == {"declared_type": "Decimal", "family": "DECIMAL", "precision": 20, "scale": 3, "length_text": "20,3"}
    assert f["role"] == "MEASURE" and f["derived"] is False and "quality" not in json.dumps(f)


def test_a_field_whose_name_is_a_source_column_records_the_source_vocabulary_fact(doc):
    f = next(x for x in _ce(doc)["published_contract"]["fields"]["fields"] if x["technical_name"] == "AUART")
    assert f["name_equals_remote_source_column"] is True and f["remote_source_artifacts"] == ["1TR_TVAKT", "1TR_VBAK"] and f["remote_source_artifact_count"] == 2


def test_naming_hygiene_and_semantic_contract_are_separate_categories_and_none_is_directional(doc):
    tax = _ce(doc)["naming_evidence"]["taxonomy"]
    cats = {t["item"]: t["category"] for t in tax}
    assert cats["section_28_artifact_name_conformity"] == "HYGIENE" and cats["field_name_form_and_case"] == "HYGIENE" and cats["ROLE_LAYER_DIVERGENCE"] == "HYGIENE"
    assert cats["LABEL_NOT_ENRICHING"] == "SEMANTIC_CONTRACT" and cats["published_field_and_measure_semantics"] == "SEMANTIC_CONTRACT" and cats["FIELD_VOCABULARY_MIX"] == "MIXED"
    assert not any(t["directional"] for t in tax)
    assert set(_ce(doc)["naming_evidence"]) == {"taxonomy", "hygiene", "semantic_contract"}


def test_the_declared_dimensions_not_associated_list_is_empty_when_every_declared_dimension_is_used(doc):
    assert _ce(doc)["published_contract"]["associations"]["declared_dimensions_in_closure_not_associated"] == []


def test_a_declared_dimension_in_the_closure_that_the_model_does_not_associate_is_reported_as_a_fact():
    root = write_pool(bundled_df=True)
    p = root / "Acme/analytic-models/4AM_X.json"
    body = json.loads(p.read_text(encoding="utf-8"))
    el = body["definitions"][AM]["elements"]
    el.pop("_Dim")
    body["definitions"][AM]["query"]["SELECT"]["columns"] = [c for c in body["definitions"][AM]["query"]["SELECT"]["columns"] if c.get("ref") != ["_Dim"]]
    p.write_text(json.dumps(body), encoding="utf-8")
    d, _ = ma_shadow.assess(root, "Acme", AM, "General")
    assert _ce(d)["published_contract"]["associations"]["aggregates"]["total"] == 1                       # solo la asociacion de tiempo sigue publicada


def test_published_contract_facts_are_fully_deterministic(doc):
    again, _ = ma_shadow.assess(write_pool(bundled_df=True), "Acme", AM, "General")
    assert json.dumps(_ce(again)["published_contract"], sort_keys=True) == json.dumps(_ce(doc)["published_contract"], sort_keys=True)


def test_no_canonical_evidence_key_uses_a_forbidden_judgement_token(doc):
    bad = sorted({k for k in _walk_keys(_ce(doc), []) if any(t in FORBIDDEN_TOKENS for t in tokenize_identifier(str(k)))})
    assert bad == []


# ═══════════════ S3 — contrato de armonizacion (29-33) ═══════════════
def test_the_flow_produced_table_exposes_its_mapping_characteristics_from_the_flow_attribute_mappings(doc):
    a = _art(doc, "2TR_VBAK")
    assert a["implementation"] == "TABLE_PRODUCED_BY_FLOW" and a["mapping_basis"] == "FLOW_ATTRIBUTE_MAPPINGS" and a["mapping_determinable"] is True
    assert a["mapping_counts"] == {"IDENTITY": 13, "RENAME": 1, "DERIVED": 0, "CAST": 0, "UNKNOWN": 0} and a["target_field_count"] == 14
    assert a["renamed_fields"] == [{"target": "BEZEI", "source_field": "BEZEI_REASON"}] and a["identity_mapping_ratio"] == 0.9286
    assert a["retained_source_fields"] == [{"source": "VBAK_HIST", "source_field_count": 14, "retained_source_fields": 14, "ratio": 1.0}]


def test_label_origin_counts_separate_own_name_from_provider_label_and_source_vocabulary_is_recorded(doc):
    a = _art(doc, "2TR_VBAK")
    assert a["label_origin_counts"]["EQUALS_OWN_NAME"] == 5 and a["label_origin_counts"]["EQUALS_PROVIDER_LABEL"] == 8 and a["label_origin_counts"]["DIFFERENT_FROM_PROVIDER"] == 0
    assert a["business_name_enrichment_ratio"] == 0.0 and a["source_vocabulary"]["fields_with_source_column_name"] == 13 and a["source_vocabulary"]["basis"] == "NAME_EQUALS_REMOTE_SOURCE_COLUMN_IN_CHAIN"


def test_a_projection_only_artifact_and_a_deriving_artifact_have_different_characters(doc):
    mat, mixed = _art(doc, "2TR_VBAK"), _art(doc, "2VS_DIM")
    assert mat["character"]["value"] == "TECHNICAL_MATERIALIZATION" and mat["character"]["dimensions"]["pure_projection_only"] is True
    assert mixed["character"]["value"] == "MIXED" and mixed["character"]["dimensions"]["has_transformation"] is True
    assert mixed["mapping_counts"]["DERIVED"] == 1 and mixed["mapping_counts"]["IDENTITY"] == 11 and mixed["transformation_classes"] == ["TEXT_FORMAT"] and mixed["mapping_basis"] == "QUERY_PROJECTION"


def test_layer_responsibility_is_only_a_hypothesis_with_its_basis_never_a_conclusion(doc):
    a = _art(doc, "2VS_DIM")
    assert a["inferred_layer"] == "2" and a["inferred_layer_basis"] == "SECTION_28_NAME" and "LAYER_2_BY_SECTION_28_NAME" in a["candidate_basis"]
    assert "DECLARED_DIMENSION_ROLE" in a["candidate_basis"] and "conclusion" not in json.dumps(a).lower()


def test_an_undeterminable_mapping_is_reported_as_unknown_with_a_reason_never_as_a_default_character():
    root = write_pool(bundled_df=True)
    p = root / "Acme/DF_VBAK.json"
    body = json.loads(p.read_text(encoding="utf-8"))
    for proc in body["dataflows"]["DF_VBAK"]["contents"]["processes"].values():
        proc["metadata"]["config"].pop("attributeMappings", None)
    p.write_text(json.dumps(body), encoding="utf-8")
    d, _ = ma_shadow.assess(root, "Acme", AM, "General")
    a = _art(d, "2TR_VBAK")
    assert a["mapping_determinable"] is False and a["character"]["value"] == "UNKNOWN" and a["character"]["reason_unknown"]


# ═══════════════ S4 — ubicacion de predicados / derivaciones (34-38) ═══════════════
def test_the_same_derivation_is_recorded_in_select_group_by_and_where_with_the_same_fingerprint(doc):
    occ = _ce(doc)["expression_occurrences"]
    mes = [o for o in occ["occurrences"] if o["exact_fp"] == "f5163c87d4e8775b"]
    assert {o["clause"] for o in mes} == {"SELECT", "GROUP_BY"}                                                 # WHERE la contiene como SUBEXPRESION, no como expresion completa
    group = next(g for g in occ["reuse_groups"] if g["exact_fp"] == "f5163c87d4e8775b")
    assert {(o["artifact"], o["clause"], o["containment"]) for o in group["occurrences"]} == {("3VF_X", "GROUP_BY", "WHOLE"), ("V_A", "SELECT", "WHOLE"), ("V_B", "WHERE", "CONTAINED")}
    assert group["transformation_classes"] == ["TEMPORAL", "TEXT_FORMAT"] and group["functions"] == ["CONCAT", "EXTRACT", "LPAD"]


def test_predicates_are_extracted_as_ast_occurrences_with_resolved_inputs(doc):
    where = [o for o in _ce(doc)["expression_occurrences"]["occurrences"] if o["clause"] == "WHERE"]
    assert len(where) == 1 and where[0]["artifact"] == "V_B" and where[0]["kind"] == "PREDICATE"
    assert where[0]["inputs"] == [{"provider": "2TR_VBAK", "field": "AUDAT", "state": "RESOLVED"}] and where[0]["resolvable"] is True


def test_trivial_join_predicates_are_counted_apart_and_never_treated_as_derivations(doc):
    occ = _ce(doc)["expression_occurrences"]
    assert occ["trivial_predicates"] == [{"clause": "JOIN_ON", "count": 1}]
    assert not any(o["clause"] == "JOIN_ON" for o in occ["occurrences"])


def test_transformation_class_comes_from_the_function_family_never_from_field_names(doc):
    occ = {o["output_name"] or o["clause"]: o for o in _ce(doc)["expression_occurrences"]["occurrences"]}
    assert occ["Cliente_Cod"]["transformation_classes"] == ["TEXT_FORMAT"] and occ["Cliente_Cod"]["functions"] == ["LEFT"]
    assert occ["Mes"]["transformation_classes"] == ["TEMPORAL", "TEXT_FORMAT"]


def test_expression_extraction_has_no_text_parsing_dependency():
    import tools.catalog.modeling_assessment.facts_expressions as fx
    src = Path(fx.__file__).read_text(encoding="utf-8")
    assert "import re" not in src and "re.compile" not in src and "sqlparse" not in src


# ═══════════════ S5 — reconvergencia de ramas (39-42) ═══════════════
def test_two_branches_reading_the_same_provider_that_reconverge_downstream_are_reported(doc):
    rec = _ce(doc)["branch_reconvergence"]["reconvergences"]
    assert len(rec) == 1
    r = rec[0]
    assert (r["shared_provider"], r["branch_a"], r["branch_b"], r["reconvergence_artifact"]) == ("2TR_VBAK", "V_A", "V_B", "3VF_X")
    assert r["shared_fields"] == ["AUDAT", "VBELN"] and r["overlap"] == 2 and r["fields_consumed_a"] == 12 and r["fields_consumed_b"] == 2
    assert r["consumer_asymmetry"] == "B_SUBSET_OF_A" and r["subset_ratio"] == 1.0 and r["jaccard"] == 0.1667


def test_reconvergence_records_the_path_structure_not_a_verdict(doc):
    r = _ce(doc)["branch_reconvergence"]["reconvergences"][0]
    assert r["paths_root_to_shared_provider"] == 2 and r["common_upstream_identities"] == ["2TR_VBAK"] and r["branch_depths"] == {"a": 2, "b": 2}
    assert _ce(doc)["branch_reconvergence"]["counts"] == {"pairs": 1, "shared_providers": 1} and "not a deviation" in _ce(doc)["branch_reconvergence"]["definition"]


def test_a_linear_chain_has_no_reconvergence(tmp_path):
    root = write_pool(bundled_df=True, root=tmp_path)
    p = root / "Acme/views/3VF_X.json"
    body = json.loads(p.read_text(encoding="utf-8"))
    sel = body["definitions"]["3VF_X"]["query"]["SELECT"]
    sel["from"] = {"ref": ["V_A"]}
    sel["columns"] = [c for c in sel["columns"] if c["ref"][0] == "V_A"]
    sel.pop("groupBy", None)
    body["definitions"]["3VF_X"]["elements"] = {k: v for k, v in body["definitions"]["3VF_X"]["elements"].items() if k != "FechaPedido"}
    p.write_text(json.dumps(body), encoding="utf-8")
    (root / "Acme/views/V_B.json").unlink()
    d, _ = ma_shadow.assess(root, "Acme", AM, "General")
    assert _ce(d)["branch_reconvergence"]["reconvergences"] == [] and _ce(d)["branch_reconvergence"]["counts"] == {"pairs": 0, "shared_providers": 0}


def test_ancestors_are_not_branches(doc):
    for r in _ce(doc)["branch_reconvergence"]["reconvergences"]:
        assert r["branch_a"] != r["branch_b"] and r["shared_provider"] not in (r["branch_a"], r["branch_b"])


# ═══════════════ S6 — complejidad descriptiva de vistas (43-44 de estructura) ═══════════════
def test_view_complexity_is_structural_and_descriptive(doc):
    views = {v["artifact"]: v for v in _ce(doc)["view_complexity"]["views"]}
    assert set(views) == {"2VS_DIM", "3VF_X", "V_A", "V_B"}
    j = views["3VF_X"]["metrics"]
    assert j["joins"] == 1 and j["join_types"] == [{"type": "left", "count": 1}] and j["unions"] == 0 and j["output_columns"] == 14
    assert views["V_B"]["metrics"]["filters"] == {"where_nodes": 1, "having_nodes": 0} and views["V_B"]["metrics"]["predicate_atoms"] == {"non_trivial": 1, "trivial": 0}


def test_the_view_type_is_descriptive_and_does_not_enter_the_complexity_metrics(doc):
    for v in _ce(doc)["view_complexity"]["views"]:
        assert v["view_type"] in ("SQL", "GRAPHICAL", "UNKNOWN") and "view_type" not in v["metrics"]
        assert v["view_type_basis"] and v["metrics"]["extractor_version"] == "view-complexity/v1"


# ═══════════════ Processing: propiedades de carga leidas del export (45-50) ═══════════════
def test_processing_properties_are_read_verbatim_from_the_export(doc):
    f = _ce(doc)["processing_flow_properties"]["flows"][0]
    assert (f["flow_kind"], f["flow_name"], f["closure_targets"]) == ("DATA_FLOW", "DF_VBAK", ["2TR_VBAK"])
    assert f["observed"]["target_write_modes"] == ["append"] and f["observed"]["target_upsert"] == [True]
    tgt = next(n for n in f["properties"]["nodes"] if n["role"] == "target")
    assert tgt["mode"] == "append" and tgt["upsert"] is True and tgt["batch_size"] == 1000 and tgt["remote_object_type"] == "TABLE"


def test_the_source_view_filter_is_carried_verbatim_without_interpretation(doc):
    f = _ce(doc)["processing_flow_properties"]["flows"][0]
    assert f["source_filters"] == [{"source_entity": "VBAK_HIST", "having": None,
                                    "where": [{"ref": ["1TR_VBAK", "ERDAT"]}, ">=", {"val": "20250101"}]}]


def test_append_and_upsert_never_become_inferred_load_semantics(doc):
    pr = _ce(doc)["processing_flow_properties"]
    assert pr["inferences"] == []
    blob = json.dumps({k: v for k, v in pr.items() if k not in ("definition", "unobserved_properties")}).lower()      # la definicion nombra lo que NO se infiere
    for token in ("incremental", "full_load", "\"delta\"", "federated", "replicated"):
        assert token not in blob


def test_unavailable_processing_metadata_stays_declared_as_unobserved(doc):
    un = _ce(doc)["processing_flow_properties"]["unobserved_properties"]
    assert [u["property"] for u in un] == ["schedule", "federation_state", "replication_state", "execution_mode"] and {u["reason"] for u in un} == {"NOT_IN_EXPORT"}


def test_a_truncate_mode_is_read_as_exported_and_differs_from_append():
    d, _ = ma_shadow.assess(write_pool(bundled_df=True, mode="truncate", upsert=False), "Acme", AM, "General")
    f = _ce(d)["processing_flow_properties"]["flows"][0]
    assert f["observed"]["target_write_modes"] == ["truncate"] and f["observed"]["target_upsert"] == [False]


def test_processing_evidence_is_complementary_and_never_changes_the_modeling_coverage_denominators(doc):
    proc = [s for s in doc["surfaces"] if s["surface_id"].startswith("ingestion.flow_load")]
    assert proc and all(s["role"] != "REQUIRED" for s in proc)


# ═══════════════ validador del contrato v2 ═══════════════
def test_the_persisted_v2_payload_validates(doc):
    validate_canonical_evidence(_ce(doc))


@pytest.mark.parametrize("path,mutate", [
    (("closure", "reference_resolution", "counts"), lambda d: d.__setitem__("UNRESOLVED", 5)),
    (("view_complexity",), lambda d: d.pop("views")),
    (("processing_flow_properties",), lambda d: d.__setitem__("inferences", ["FULL"])),
    (("published_contract", "associations", "aggregates"), lambda d: d.__setitem__("total", 99)),
])
def test_a_tampered_v2_payload_is_rejected(doc, path, mutate):
    ce = copy.deepcopy(_ce(doc))
    node = ce
    for k in path:
        node = node[k]
    mutate(node)
    with pytest.raises(CanonicalEvidenceError):
        validate_canonical_evidence(ce)


def test_a_v2_payload_missing_the_closure_section_is_rejected(doc):
    ce = copy.deepcopy(_ce(doc))
    ce.pop("closure")
    with pytest.raises(CanonicalEvidenceError):
        validate_canonical_evidence(ce)


def test_the_assessability_surfaces_for_the_new_facts_are_all_declared_and_none_is_required_except_closure_resolution(doc):
    ids = {s["surface_id"]: s for s in doc["surfaces"]}
    required_new = {i for i, s in ids.items() if s["role"] == "REQUIRED" and i.startswith(("am.closure", "am.published", "harmonization.contract", "propagation.expression_occ", "propagation.branch", "ingestion.view_complex"))}
    assert required_new == {"am.closure_reference_resolution"}
