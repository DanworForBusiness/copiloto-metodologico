"""M4 — hechos de NAMING (artefacto + campo juntos): solo hechos, sin juicio, sin score/banda/peso/umbral/penalidad."""
from __future__ import annotations

import ast
import json
import random
import re
from pathlib import Path

import pytest

from tests._modeling_assessment_m3_fixtures import m3_entities
from tests._modeling_assessment_m4_fixtures import f4, m4_entities, m4_pair_with_identical_artifact_naming
from tests.test_modeling_assessment_graph import ent, pool
from tools.catalog.modeling_assessment import legacy_bridge as bridge
from tools.catalog.modeling_assessment import model as m
from tools.catalog.modeling_assessment import naming_rules as R
from tools.catalog.modeling_assessment.facts_naming import FieldNameAnchor, naming_facts
from tools.catalog.modeling_assessment.identity import classify_universe
from tools.catalog.modeling_assessment.scope import AssessmentUniverse

PKG = Path(__file__).resolve().parent.parent / "tools" / "catalog" / "modeling_assessment"


def _facts(ents=None, am="AM_X"):
    ents = ents or m4_entities()
    return ents, naming_facts(AssessmentUniverse.from_entities(ents), ents, am)


def _one(facts, fid, subject):
    out = [f for f in facts if f.fact_id == fid and f.subject == subject]
    assert len(out) == 1, (fid, subject, len(out))
    return out[0]


def _hist(records):
    return {r["key"]: r["count"] for r in records}


# ── forma del conjunto de hechos ───────────────────────────────────────────
def test_all_naming_facts_are_naming_area_modeling_plane_and_use_the_expected_evidence_classes():
    _, facts = _facts()
    assert {f.area for f in facts} == {m.Area.NAMING} and {f.plane for f in facts} == {m.Plane.MODELING}
    assert {f.fact_id for f in facts} == {
        "naming.artifact_name_contract", "naming.field_name_contract", "naming.field_name_forms",
        "naming.field_name_continuity", "naming.artifact_name_populations", "naming.field_vocabulary_summary",
        "naming.same_normalized_label_multiple_technical_names", "naming.convention_context"}
    cls = {f.fact_id: f.evidence_class for f in facts}
    assert cls["naming.artifact_name_contract"] is m.EvidenceClass.A and cls["naming.field_name_contract"] is m.EvidenceClass.A
    for fid in ("naming.field_name_forms", "naming.field_name_continuity", "naming.artifact_name_populations",
                "naming.field_vocabulary_summary", "naming.same_normalized_label_multiple_technical_names"):
        assert cls[fid] is m.EvidenceClass.B, fid                 # derivable / heuristico: nunca disfrazado de observacion
    assert cls["naming.convention_context"] is m.EvidenceClass.D


def test_facts_fit_the_phase1_area_card_and_serialize_to_pure_json():
    _, facts = _facts()
    card = m.AreaCard(m.Area.NAMING, m.ApplicabilityDecision(m.Applicability.APPLICABLE), m.Assessability.PARTIALLY_ASSESSED, facts, ())
    json.dumps(m.to_jsonable(card), sort_keys=True, allow_nan=False)


def test_no_quality_vocabulary_no_systematic_or_fragmented_label_and_no_signal_anywhere():
    _, facts = _facts()
    blob = json.dumps([m.to_jsonable(f) for f in facts], ensure_ascii=False).lower()
    for word in ("systematic", "fragmented", "chaotic", "peripheral", "irrelevant"):
        assert not re.search(rf"\b{word}\b", blob), word
    for f in facts:
        assert not m.find_forbidden_tokens(f.fact_id)
        assert f.evidence_class in (m.EvidenceClass.A, m.EvidenceClass.B, m.EvidenceClass.D)
        assert not [k for k in _all_keys(f.value) if m.find_forbidden_tokens(k)], f.key      # sin score/banda/peso/umbral/penalidad


def _all_keys(value):
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            out.append(k)
            out.extend(_all_keys(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_all_keys(v))
    return out


def test_label_equality_is_never_claimed_as_a_proven_same_concept_synonym_duplicate_or_defect():
    _, facts = _facts()
    fid = "naming.same_normalized_label_multiple_technical_names"
    assert "naming.same_label_different_name" not in {f.fact_id for f in facts}
    assert not any("concept" in f.fact_id or "synonym" in f.fact_id for f in facts)
    fact = _one(facts, fid, "closure")
    keys = set(_all_keys(fact.value))
    for needle in ("concept", "synonym", "duplicate", "inconsisten", "defect", "same_concept"):
        assert not any(needle in k.lower() for k in keys), needle
    blob = json.dumps(m.to_jsonable(fact), ensure_ascii=False).lower()
    for word in ("synonym", "duplicat", "inconsisten", "defect"):
        assert word not in blob, word
    assert "do not prove" in fact.value["basis"]                                   # lo que la evidencia NO prueba, dicho explicitamente


# ── ARTEFACTO: §28 vigente, motivos, familias, procedencia ───────────────────
def test_artifact_name_contract_reuses_parse_name_as_is_for_every_non_system_artifact():
    ents, facts = _facts()
    for f in facts:
        if f.fact_id != "naming.artifact_name_contract" or f.assessability is m.Assessability.NOT_APPLICABLE:
            continue
        pn = bridge.parse_name(f.subject)
        s = f.value["section_28"]
        assert s["compliant"] is bool(pn["canonical"]) and s["violation_reasons"] == list(pn["issues"])
        assert s["applicable"] is True


def test_section_28_compliance_and_exact_reasons_and_families_for_the_synthetic_product():
    _, facts = _facts()
    expected = {"AM_X": True, "3VF_X": True, "2VR_X": True, "1TR_ZCDS_B": True,
                "VD_DIM": False, "ZCDS_A": False, "0MAT_ATTR": False, "LOC_T": False}
    for name, ok in expected.items():
        v = _one(facts, "naming.artifact_name_contract", name).value
        assert v["section_28"]["compliant"] is ok, name
        assert (v["section_28"]["violation_reasons"] == []) is ok
    assert _one(facts, "naming.artifact_name_contract", "VD_DIM").value["section_28"]["violation_families"] == ["MISSING_LAYER_PREFIX"]
    assert _one(facts, "naming.artifact_name_contract", "ZCDS_A").value["section_28"]["violation_families"] == ["NO_RECOGNIZED_PATTERN"]
    assert _one(facts, "naming.artifact_name_contract", "VD_DIM").value["name_shape"] == "UNLAYERED_TABLE_OR_VIEW"
    assert _one(facts, "naming.artifact_name_contract", "2VR_X").value["name_shape"] == "LAYER_ARTIFACT_USE_PREFIX"
    assert _one(facts, "naming.artifact_name_contract", "AM_X").value["name_shape"] == "TYPED_ARTIFACT"
    assert _one(facts, "naming.artifact_name_contract", "2VR_X").value["naming_family"] == "#VR_"
    assert _one(facts, "naming.artifact_name_contract", "VD_DIM").value["naming_family"] == "VD_"


def test_identity_provenance_rule_and_certainty_travel_with_each_artifact_fact():
    _, facts = _facts()
    ident = {n: _one(facts, "naming.artifact_name_contract", n).value["identity"]
             for n in ("AM_X", "VD_DIM", "ZCDS_A", "1TR_ZCDS_B", "0MAT_ATTR", "LOC_T")}
    assert ident["AM_X"]["identity"] == "PROJECT_ASSIGNED" and ident["VD_DIM"]["identity"] == "PROJECT_ASSIGNED"
    assert ident["ZCDS_A"] == {"identity": "SOURCE_DERIVED_IMPORTED", "implementation": "REMOTE_IMPORTED",
                               "rule": "ID_SOURCE_EXACT", "certainty": "PROVEN", "remote_stem": "ZCDS_A"}
    assert ident["1TR_ZCDS_B"]["rule"] == "ID_SOURCE_NAME_EXTENDS_STEM" and ident["1TR_ZCDS_B"]["certainty"] == "HEURISTIC"
    assert ident["LOC_T"]["identity"] == "UNKNOWN" and ident["LOC_T"]["certainty"] is None


def test_source_name_relation_records_what_the_project_added_over_the_source_identity():
    _, facts = _facts()
    b = _one(facts, "naming.artifact_name_contract", "1TR_ZCDS_B").value["source_name_relation"]
    assert b["project_added_tokens"] == ["1TR"] and b["project_removed_tokens"] == [] and b["name_equals_stem"] is False
    a = _one(facts, "naming.artifact_name_contract", "ZCDS_A").value["source_name_relation"]
    assert a["name_equals_stem"] is True and a["project_added_tokens"] == []
    assert _one(facts, "naming.artifact_name_contract", "VD_DIM").value["source_name_relation"] is None


def test_a_compliant_source_derived_name_and_a_project_name_are_both_just_facts_with_their_own_provenance():
    _, facts = _facts()
    pops = _one(facts, "naming.artifact_name_populations", "closure").value
    by = {r["identity"]: r for r in pops["by_identity"]}
    assert (by["PROJECT_ASSIGNED"]["applicable"], by["PROJECT_ASSIGNED"]["compliant"], by["PROJECT_ASSIGNED"]["non_compliant"]) == (4, 3, 1)
    assert (by["SOURCE_DERIVED_IMPORTED"]["applicable"], by["SOURCE_DERIVED_IMPORTED"]["compliant"],
            by["SOURCE_DERIVED_IMPORTED"]["non_compliant"]) == (3, 1, 2)
    assert by["SOURCE_DERIVED_IMPORTED"]["compliant_artifacts"] == ["1TR_ZCDS_B"]
    assert by["SOURCE_DERIVED_IMPORTED"]["heuristic_identity_count"] == 1
    assert (by["UNKNOWN"]["applicable"], by["UNKNOWN"]["non_compliant"]) == (1, 1)
    assert by["UNKNOWN"]["non_compliant_artifacts"] == ["LOC_T"]
    assert by["SYSTEM"] == {"identity": "SYSTEM", "artifacts": 1, "applicable": 0}
    # razones por procedencia, asimetricas a proposito (el total 4/4 no distingue cumplimiento de incumplimiento)
    assert by["PROJECT_ASSIGNED"]["non_compliant_ratio"] == 0.25
    assert by["SOURCE_DERIVED_IMPORTED"]["non_compliant_ratio"] == 0.666667
    assert by["UNKNOWN"]["non_compliant_ratio"] == 1.0


def test_populations_expose_prevalence_reason_histogram_and_family_histogram_without_any_label():
    _, facts = _facts()
    v = _one(facts, "naming.artifact_name_populations", "closure").value
    s = v["section_28"]
    assert (v["closure_artifacts"], v["system_artifacts_excluded"]) == (9, 1)
    assert (s["applicable"], s["compliant"], s["non_compliant"], s["non_compliant_ratio"]) == (8, 4, 4, 0.5)
    assert _hist(s["violation_family_histogram"]) == {"NO_RECOGNIZED_PATTERN": 3, "MISSING_LAYER_PREFIX": 1}
    assert s["distinct_violation_families"] == 2
    assert sum(_hist(s["violation_reason_histogram"]).values()) == 4          # los motivos EXACTOS se cuentan aparte de las familias
    assert _hist(s["naming_family_histogram"])["#VR_"] == 1
    assert set(v) == {"closure_artifacts", "system_artifacts_excluded", "section_28", "by_identity", "basis"}


def test_system_artifacts_are_not_applicable_for_every_per_artifact_naming_fact_and_never_counted():
    _, facts = _facts()
    for fid in ("naming.artifact_name_contract", "naming.field_name_contract", "naming.field_name_forms",
                "naming.field_name_continuity"):
        f = _one(facts, fid, "SAP.TIME.DAY")
        assert f.assessability is m.Assessability.NOT_APPLICABLE and f.value is None
        assert f.applicability.reason is m.ApplicabilityReason.SYSTEM_ARTIFACT
    summ = _one(facts, "naming.field_vocabulary_summary", "closure").value
    assert "DAY" not in json.dumps(summ)


def test_unknown_identity_stays_assessed_and_does_not_change_the_current_section_28_fact():
    _, facts = _facts()
    f = _one(facts, "naming.artifact_name_contract", "LOC_T")
    assert f.assessability is m.Assessability.ASSESSED and f.cause is None
    assert f.value["section_28"]["compliant"] is bool(bridge.parse_name("LOC_T")["canonical"])


def test_unknown_identity_does_not_invalidate_any_identity_independent_naming_fact():
    """Identidad UNKNOWN no baja la assessability de: nombre tecnico, parse_name, §28, motivos, nombre de campo, etiquetas y forma."""
    _, facts = _facts()
    assert _one(facts, "naming.artifact_name_contract", "LOC_T").value["identity"]["identity"] == "UNKNOWN"
    for fid in ("naming.artifact_name_contract", "naming.field_name_contract", "naming.field_name_forms", "naming.field_name_continuity"):
        f = _one(facts, fid, "LOC_T")
        assert f.assessability is m.Assessability.ASSESSED and f.cause is None and f.value is not None, fid
        assert f.applicability.state is m.Applicability.APPLICABLE
    s28 = _one(facts, "naming.artifact_name_contract", "LOC_T").value["section_28"]
    assert s28["applicable"] is True and s28["violation_reasons"] == list(bridge.parse_name("LOC_T")["issues"]) and s28["violation_reasons"]
    assert _one(facts, "naming.field_name_contract", "LOC_T").value["fields"][0]["label"] == "Clave"
    assert _one(facts, "naming.field_name_forms", "LOC_T").value["fields"][0]["form"] == "ALNUM_1_3"


def test_an_unknown_identity_artifact_is_counted_in_the_unknown_bucket_not_dropped_or_charged_to_the_project():
    _, facts = _facts()
    by = {r["identity"]: r for r in _one(facts, "naming.artifact_name_populations", "closure").value["by_identity"]}
    assert by["UNKNOWN"]["applicable"] == 1 and by["UNKNOWN"]["non_compliant_artifacts"] == ["LOC_T"]
    assert "LOC_T" not in by["PROJECT_ASSIGNED"]["non_compliant_artifacts"]


def test_convention_context_explains_but_never_rewrites_current_compliance():
    _, facts = _facts()
    c = _one(facts, "naming.convention_context", "closure")
    assert c.evidence_class is m.EvidenceClass.D and c.assessability is m.Assessability.NOT_ASSESSED
    assert c.cause is m.Cause.CONTEXT_REQUIRED
    assert "local_convention_for_section_28_deviations" in c.value["open_context_questions"]


# ── CAMPO: etiquetas ────────────────────────────────────────────────────────
def test_field_name_contract_reports_label_presence_equality_and_difference_per_field():
    _, facts = _facts()
    v = _one(facts, "naming.field_name_contract", "ZCDS_A").value
    by = {r["field"]: r for r in v["fields"]}
    assert by["LAND1"]["label_equals_field"] is True and by["LAND1"]["label_matches_field_normalized"] is True
    assert by["BUKRS"]["label"] == "Sociedad" and by["BUKRS"]["label_equals_field"] is False
    assert (v["n_fields"], v["label_present"], v["label_absent"], v["label_equals_field"], v["label_differs_from_field"]) == (4, 4, 0, 1, 3)
    b = _one(facts, "naming.field_name_contract", "1TR_ZCDS_B").value
    assert b["label_absent"] == 1 and {r["field"]: r["label_present"] for r in b["fields"]}["ORDERID"] is False
    assert {r["field"]: r["label"] for r in b["fields"]}["ORDERID"] is None


def test_elements_exceeding_exposed_fields_is_reported_not_hidden():
    _, facts = _facts()
    v = _one(facts, "naming.field_name_contract", "AM_X").value
    assert v["n_elements"] == 7 and v["n_fields"] == 5 and v["elements_exceed_fields"] is True


def test_an_artifact_without_exposed_fields_is_not_assessed_for_field_naming_never_assumed_empty_or_bad():
    ents = m4_entities()
    ents["VD_DIM"].fields = []
    _, facts = _facts(ents)
    for fid in ("naming.field_name_contract", "naming.field_name_forms", "naming.field_name_continuity"):
        f = _one(facts, fid, "VD_DIM")
        assert f.assessability is m.Assessability.NOT_ASSESSED and f.cause is m.Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT
        assert f.value is None
    summ = _one(facts, "naming.field_vocabulary_summary", "closure").value
    assert "VD_DIM" in summ["artifacts_without_exposed_fields"]


# ── CAMPO: formas (heuristica B, descriptiva) ───────────────────────────────
@pytest.mark.parametrize("name,form,case,custom", [
    ("K", "ALNUM_1_3", "UPPER", False), ("MID", "ALNUM_1_3", "UPPER", False),
    ("BUKRS", "ALNUM_4_6", "UPPER", False), ("PERIOD", "ALNUM_4_6", "UPPER", False), ("Fecha", "ALNUM_4_6", "MIXED", False),
    ("vbeln", "ALNUM_4_6", "LOWER", False),
    ("COSTCENTER", "ALNUM_7_PLUS", "UPPER", False), ("COMPANYCODE", "ALNUM_7_PLUS", "UPPER", False),
    ("ZED_NIVEL_1", "UNDERSCORE_SEPARATED", "UPPER", True), ("REAL_ML", "UNDERSCORE_SEPARATED", "UPPER", False),
    ("ZTERM", "ALNUM_4_6", "UPPER", False),                  # empieza con Z pero sin `_`: la forma NO lo trata como espacio propio
    ("A B", "OTHER_CHARACTERS", "UPPER", False), ("$projection", "OTHER_CHARACTERS", "LOWER", False), ("", "OTHER_CHARACTERS", "NO_LETTERS", False),
    ("123", "ALNUM_1_3", "NO_LETTERS", False),
    ("ABC", "ALNUM_1_3", "UPPER", False), ("ABCD", "ALNUM_4_6", "UPPER", False),            # limites de la forma: 3|4 y 6|7
    ("ABCDEF", "ALNUM_4_6", "UPPER", False), ("ABCDEFG", "ALNUM_7_PLUS", "UPPER", False),
])
def test_field_name_form_is_a_pure_mechanical_description(name, form, case, custom):
    r = R.field_name_form(name)
    assert (r["form"], r["case_style"], r["custom_namespace_prefix"]) == (form, case, custom)


def test_form_does_not_decide_provenance_a_ddic_looking_name_introduced_here_is_still_introduced():
    ents = m4_entities()
    ents["VD_DIM"].fields = [*ents["VD_DIM"].fields, f4("WRTTP", "Tipo de valor")]       # parece DDIC pero no esta en ninguna fuente
    _, facts = _facts(ents)
    by = {r["field"]: r for r in _one(facts, "naming.field_name_continuity", "VD_DIM").value["fields"]}
    assert by["WRTTP"]["name_anchor"] == "FIRST_APPEARS_AT_THIS_ARTIFACT"
    forms = {r["field"]: r for r in _one(facts, "naming.field_name_forms", "VD_DIM").value["fields"]}
    assert forms["WRTTP"]["form"] == "ALNUM_4_6"


def test_forms_fact_aggregates_are_consistent_with_its_records_and_carry_the_ruleset_version():
    _, facts = _facts()
    v = _one(facts, "naming.field_name_forms", "ZCDS_A").value
    assert v["ruleset"] == R.FIELD_NAME_RULESET == "field-name-forms/v1"
    assert _hist(v["form_histogram"]) == {"ALNUM_4_6": 3, "UNDERSCORE_SEPARATED": 1}
    assert v["custom_namespace_prefixed_count"] == 1                    # ZED_NIVEL: forma de espacio propio, no prueba de gobierno


# ── CAMPO: continuidad del nombre por linaje ────────────────────────────────
def _anchors(facts, artifact):
    return {r["field"]: r["name_anchor"] for r in _one(facts, "naming.field_name_continuity", artifact).value["fields"]}


def test_continuity_classes_across_the_chain_with_their_evidence_chain():
    _, facts = _facts()
    assert set(_anchors(facts, "ZCDS_A").values()) == {"IS_REMOTE_ARTIFACT_FIELD"}
    assert set(_anchors(facts, "LOC_T").values()) == {"IS_LOCAL_BOUNDARY_FIELD"}
    assert _anchors(facts, "VD_DIM") == {"BUKRS": "ANCHORED_AT_REMOTE_ARTIFACT", "MATNR": "ANCHORED_AT_REMOTE_ARTIFACT", "DESC_NEW": "FIRST_APPEARS_AT_THIS_ARTIFACT"}
    assert _anchors(facts, "2VR_X") == {"BUKRS": "ANCHORED_AT_REMOTE_ARTIFACT", "COMPANYCODE": "ANCHORED_AT_REMOTE_ARTIFACT",
                                        "MATNR": "ANCHORED_AT_REMOTE_ARTIFACT", "MID": "FIRST_APPEARS_AT_THIS_ARTIFACT",
                                        "LK": "ANCHORED_AT_LOCAL_BOUNDARY"}
    assert _anchors(facts, "3VF_X") == {"BUKRS": "ANCHORED_AT_REMOTE_ARTIFACT", "COMPANYCODE": "ANCHORED_AT_REMOTE_ARTIFACT",
                                        "MID": "FIRST_APPEARS_IN_INTERMEDIATE", "AMT_ML": "FIRST_APPEARS_AT_THIS_ARTIFACT",
                                        "LK": "ANCHORED_AT_LOCAL_BOUNDARY"}
    assert _anchors(facts, "AM_X") == {"BUKRS": "ANCHORED_AT_REMOTE_ARTIFACT", "MID": "FIRST_APPEARS_IN_INTERMEDIATE",
                                       "AMT_ML": "FIRST_APPEARS_IN_INTERMEDIATE", "DUP": "FIRST_APPEARS_AT_THIS_ARTIFACT",
                                       "LK": "ANCHORED_AT_LOCAL_BOUNDARY"}


def test_continuity_keeps_the_evidence_chain_sources_direct_providers_and_the_derived_flag():
    _, facts = _facts()
    recs = {r["field"]: r for r in _one(facts, "naming.field_name_continuity", "2VR_X").value["fields"]}
    remote = {"REMOTE_ENTITY_ARTIFACT"}
    assert recs["MATNR"]["earliest_observable_anchors"] == [{"artifact": "0MAT_ATTR", "kind": "REMOTE_ENTITY_ARTIFACT"},
                                                            {"artifact": "ZCDS_A", "kind": "REMOTE_ENTITY_ARTIFACT"}]
    assert recs["MATNR"]["remote_source_anchor"] == {"status": "OBSERVED", "artifacts": ["0MAT_ATTR", "ZCDS_A"]}
    assert recs["MATNR"]["direct_lineage_providers_with_field"] == ["VD_DIM"]
    assert recs["COMPANYCODE"]["remote_source_anchor"] == {"status": "OBSERVED", "artifacts": ["1TR_ZCDS_B"]}
    assert {a["kind"] for a in recs["COMPANYCODE"]["earliest_observable_anchors"]} == remote
    # ancla LOCAL: es el ancla observable mas temprana, pero NO una ancla de fuente remota
    assert recs["LK"]["earliest_observable_anchors"] == [{"artifact": "LOC_T", "kind": "LOCAL_BOUNDARY_ARTIFACT"}]
    assert recs["LK"]["remote_source_anchor"] == {"status": "UNAVAILABLE_IN_OBSERVABLE_LINEAGE", "artifacts": []}
    assert recs["MID"]["earliest_observable_anchors"] == [{"artifact": "2VR_X", "kind": "THIS_ARTIFACT"}]
    assert recs["MID"]["remote_source_anchor"]["status"] == "UNAVAILABLE_IN_OBSERVABLE_LINEAGE"
    assert recs["MID"]["in_direct_lineage_provider"] is False
    am = {r["field"]: r for r in _one(facts, "naming.field_name_continuity", "AM_X").value["fields"]}
    assert am["MID"]["direct_lineage_providers_with_field"] == ["3VF_X"] and am["AMT_ML"]["derived"] is True
    vf = _one(facts, "naming.field_name_continuity", "3VF_X").value
    assert vf["retained_from_direct_lineage_provider"] == 4 and vf["lineage_provider_count"] == 1
    assert vf["lineage_ancestor_count"] == 6 and vf["remote_artifact_ancestor_count"] == 3 and vf["local_boundary_ancestor_count"] == 1


def test_an_intermediate_anchor_is_reported_as_such_and_never_as_a_source():
    _, facts = _facts()
    am = {r["field"]: r for r in _one(facts, "naming.field_name_continuity", "AM_X").value["fields"]}
    assert am["MID"]["earliest_observable_anchors"] == [{"artifact": "2VR_X", "kind": "INTERMEDIATE_ARTIFACT"}]
    assert am["AMT_ML"]["earliest_observable_anchors"] == [{"artifact": "3VF_X", "kind": "INTERMEDIATE_ARTIFACT"}]
    assert am["MID"]["remote_source_anchor"] == {"status": "UNAVAILABLE_IN_OBSERVABLE_LINEAGE", "artifacts": []}


def test_a_local_anchor_is_never_promoted_to_a_remote_source_anchor():
    _, facts = _facts()
    local_class = ("ANCHORED_AT_LOCAL_BOUNDARY", "IS_LOCAL_BOUNDARY_FIELD")
    seen = 0
    for f in facts:
        if f.fact_id != "naming.field_name_continuity" or f.value is None:
            continue
        for r in f.value["fields"]:
            kinds = {a["kind"] for a in r["earliest_observable_anchors"]}
            if r["name_anchor"] in local_class:
                seen += 1
                assert "REMOTE_ENTITY_ARTIFACT" not in kinds
                assert r["remote_source_anchor"] == {"status": "UNAVAILABLE_IN_OBSERVABLE_LINEAGE", "artifacts": []}
            if r["remote_source_anchor"]["status"] == "OBSERVED":
                assert r["remote_source_anchor"]["artifacts"] and "REMOTE_ENTITY_ARTIFACT" in kinds
    assert seen == 4                                   # `LK` en LOC_T, 2VR_X, 3VF_X y AM_X


def test_a_chain_that_ends_only_at_local_artifacts_has_no_remote_source_anchor_anywhere():
    """Patron CASE 1 / CASE 3: las hojas son tablas locales sin `remote_entity`. No se infiere una fuente mas alla del snapshot."""
    ents = pool(
        ent("AM_L", up=["VF_L"], cube=True, has_query=True, fields=[f4("MATNR", "Material"), f4("NEWF", "Nuevo")]),
        ent("VF_L", up=["LOC_L"], has_query=True, fields=[f4("MATNR", "Material"), f4("NEWF", "Nuevo")]),
        ent("LOC_L", repository_type="DWC_LOCAL_TABLE", fields=[f4("MATNR", "Material")]))
    _, facts = _facts(ents, am="AM_L")
    assert _anchors(facts, "AM_L") == {"MATNR": "ANCHORED_AT_LOCAL_BOUNDARY", "NEWF": "FIRST_APPEARS_IN_INTERMEDIATE"}
    for a in ("AM_L", "VF_L", "LOC_L"):
        v = _one(facts, "naming.field_name_continuity", a).value
        assert v["remote_source_anchor_counts"]["observed"] == 0
    summ = _one(facts, "naming.field_vocabulary_summary", "closure").value
    assert summ["remote_source_anchor_counts"] == {"observed": 0, "unavailable": summ["field_occurrences"]}
    assert summ["terminal_am"]["remote_source_anchor_counts"] == {"observed": 0, "unavailable": 2}


def test_remote_source_anchor_counts_are_reported_per_artifact_and_for_the_terminal_am():
    _, facts = _facts()
    assert _one(facts, "naming.field_name_continuity", "AM_X").value["remote_source_anchor_counts"] == {"observed": 1, "unavailable": 4}
    summ = _one(facts, "naming.field_vocabulary_summary", "closure").value
    assert summ["terminal_am"]["remote_source_anchor_counts"] == {"observed": 1, "unavailable": 4}
    assert summ["remote_source_anchor_counts"]["observed"] + summ["remote_source_anchor_counts"]["unavailable"] == summ["field_occurrences"]


def test_continuity_counts_cover_every_anchor_class_with_zeros_and_sum_to_the_fields():
    _, facts = _facts()
    v = _one(facts, "naming.field_name_continuity", "AM_X").value
    counts = {r["anchor"]: r["count"] for r in v["name_anchor_counts"]}
    assert set(counts) == {o.value for o in FieldNameAnchor} and sum(counts.values()) == v["n_fields"] == 5
    assert counts["ANCHORED_AT_REMOTE_ARTIFACT"] == 1 and counts["FIRST_APPEARS_IN_INTERMEDIATE"] == 2 and counts["IS_REMOTE_ARTIFACT_FIELD"] == 0


def test_an_artifact_with_only_association_providers_has_no_lineage_evidence_not_a_fabricated_anchor():
    ents = m4_entities()
    ents["VD_DIM"].upstreams = []
    ents["VD_DIM"].associations = [{"campo": "_t", "target": "LOC_T", "on": {}}]
    ents["VD_DIM"].assoc_upstreams = ["LOC_T"]
    _, facts = _facts(ents)
    assert set(_anchors(facts, "VD_DIM").values()) == {"NO_LINEAGE_PROVIDER"}


def test_field_name_anchor_never_depends_on_the_artifact_identity_or_name_compliance():
    _, base = _facts()
    ents = m4_entities()
    ents["VD_DIM"].repository_type = "DWC_LOCAL_TABLE"        # cambia su identidad (PROJECT_ASSIGNED -> UNKNOWN), no su linaje ni sus campos
    ids = classify_universe(ents)
    assert ids["VD_DIM"].identity.value == "UNKNOWN"
    _, again = _facts(ents)
    for a in ("VD_DIM", "2VR_X", "3VF_X", "AM_X"):
        assert _anchors(base, a) == _anchors(again, a)


# ── etiqueta normalizada compartida por >= 2 nombres tecnicos (evidencia, no prueba de concepto) ──
def test_same_normalized_label_with_multiple_technical_names_is_a_group_with_where_it_is_declared():
    _, facts = _facts()
    v = _one(facts, "naming.same_normalized_label_multiple_technical_names", "closure").value
    groups = {g["label"]: g for g in v["groups"]}
    assert set(groups) == {"sociedad", "material", "importe ars"} and v["group_count"] == 3
    soc = groups["sociedad"]
    assert [t["field"] for t in soc["technical_names"]] == ["BUKRS", "COMPANYCODE"] and soc["distinct_name_count"] == 2
    assert soc["occurrence_count"] == 8 and {t["field"]: len(t["artifacts"]) for t in soc["technical_names"]} == {"BUKRS": 5, "COMPANYCODE": 3}
    assert {a["artifact"]: a["fields"] for a in soc["artifacts_declaring_multiple_names"]} == {
        "2VR_X": ["BUKRS", "COMPANYCODE"], "3VF_X": ["BUKRS", "COMPANYCODE"]}
    assert soc["terminal_am_fields"] == ["BUKRS"]
    imp = groups["importe ars"]
    assert imp["terminal_am_fields"] == ["AMT_ML", "DUP"]
    assert [a["artifact"] for a in imp["artifacts_declaring_multiple_names"]] == ["AM_X"]


def test_numbered_column_series_are_marked_so_they_are_not_read_as_synonyms():
    ents = m4_entities()
    ents["VD_DIM"].fields = [*ents["VD_DIM"].fields, f4("HSL01", "Total periodo"), f4("HSL02", "Total periodo"), f4("HSLVT", "Total periodo")]
    ents["2VR_X"].fields = [*ents["2VR_X"].fields, f4("TXT1", "Texto"), f4("TXT2", "Texto")]
    _, facts = _facts(ents)
    groups = {g["label"]: g for g in _one(facts, "naming.same_normalized_label_multiple_technical_names", "closure").value["groups"]}
    assert groups["texto"]["is_numbered_series"] is True and groups["texto"]["distinct_name_stems"] == 1
    assert groups["texto"]["occurrence_count"] == 2 and groups["texto"]["distinct_name_count"] == 2   # la serie NO se filtra de la poblacion
    assert groups["total periodo"]["is_numbered_series"] is False and groups["total periodo"]["distinct_name_stems"] == 2
    assert groups["sociedad"]["is_numbered_series"] is False                       # BUKRS / COMPANYCODE: sinonimos, no serie
    assert R.numeric_suffix_stem("HSL01") == "HSL" and R.numeric_suffix_stem("DATA_CTRLR10") == "DATA_CTRLR"
    assert R.numeric_suffix_stem("123") == "123" and R.numeric_suffix_stem("BUKRS") == "BUKRS"


def test_two_unlabelled_fields_with_different_names_are_not_a_shared_concept():
    ents = m4_entities()
    ents["VD_DIM"].fields = [*ents["VD_DIM"].fields, f4("NOLAB1", ""), f4("NOLAB2", "   ")]     # 2 campos sin etiqueta (en 2 artefactos)
    ents["2VR_X"].fields = [*ents["2VR_X"].fields, f4("NOLAB3", None)]
    _, facts = _facts(ents)
    v = _one(facts, "naming.same_normalized_label_multiple_technical_names", "closure").value
    assert "" not in {g["label"] for g in v["groups"]} and v["group_count"] == 3                # la ausencia de etiqueta no es una etiqueta compartida


def test_single_name_labels_and_empty_labels_never_form_a_group_and_label_equals_field_is_flagged_not_hidden():
    _, facts = _facts()
    v = _one(facts, "naming.same_normalized_label_multiple_technical_names", "closure").value
    labels = {g["label"] for g in v["groups"]}
    assert "land1" not in labels and "clave" not in labels and "medio" not in labels and "" not in labels
    mat = {g["label"]: g for g in v["groups"]}["material"]
    flags = {t["field"]: t["label_matches_field_normalized_in_artifacts"] for t in mat["technical_names"]}
    assert flags == {"MATERIAL": ["1TR_ZCDS_B"], "MATNR": []}                    # MATERIAL repite su nombre; MATNR no


# ── agregados de campo ───────────────────────────────────────────────────────
def test_field_vocabulary_summary_aggregates_occurrences_by_provenance_and_for_the_terminal_am():
    _, facts = _facts()
    v = _one(facts, "naming.field_vocabulary_summary", "closure").value
    assert v["unit"].startswith("field occurrences")
    assert v["field_occurrences"] == 4 + 3 + 1 + 1 + 3 + 5 + 5 + 5       # ZCDS_A, 1TR, 0MAT, LOC_T, VD_DIM, 2VR_X, 3VF_X, AM_X
    assert v["distinct_field_names"] == len({"BUKRS", "MATNR", "ZED_NIVEL", "LAND1", "COMPANYCODE", "MATERIAL", "ORDERID", "LK",
                                             "DESC_NEW", "MID", "AMT_ML", "DUP"})
    assert v["label_absent"] == 1
    am = v["terminal_am"]
    assert am["artifact"] == "AM_X" and am["field_occurrences"] == 5 and am["label_present"] == 5
    assert {r["anchor"]: r["count"] for r in am["name_anchor_counts"]}["ANCHORED_AT_REMOTE_ARTIFACT"] == 1
    ident = {r["identity"]: r for r in v["by_identity"]}
    assert set(ident) == {"PROJECT_ASSIGNED", "SOURCE_DERIVED_IMPORTED", "UNKNOWN"}
    assert ident["SOURCE_DERIVED_IMPORTED"]["field_occurrences"] == 8
    cross = {(r["anchor"], r["form"]): r["count"] for r in v["anchor_by_form"]}
    assert sum(cross.values()) == v["field_occurrences"]


def test_artifact_level_facts_alone_do_not_separate_two_products_but_field_level_facts_do():
    """Aprendizaje de la calibracion: CASE 3 y CASE 4 tienen §28 casi igual y se separan en el nivel campo."""
    _, rich = _facts(m4_pair_with_identical_artifact_naming(rich_fields=True), am="AM_Q")
    _, poor = _facts(m4_pair_with_identical_artifact_naming(rich_fields=False), am="AM_Q")
    key = lambda fs: [m.to_jsonable(f) for f in fs if f.fact_id == "naming.artifact_name_populations"]
    assert key(rich) == key(poor)                                                       # identicos a nivel artefacto
    sr = _one(rich, "naming.field_vocabulary_summary", "closure").value
    sp = _one(poor, "naming.field_vocabulary_summary", "closure").value
    assert sr["label_equals_field"] == 0 and sp["label_equals_field"] == sp["field_occurrences"] == 6
    assert sr["label_differs_from_field"] == 6 and sp["label_differs_from_field"] == 0
    assert sr["name_anchor_counts"] == sp["name_anchor_counts"]                         # el ancla del nombre es la misma: lo que cambia es la etiqueta


# ── reglas auxiliares ────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["AM_X", "4AM_X", "DF_X", "1DF_X", "1XX_foo", "1TZ_foo", "VD_X", "T_X", "foo", "ZCDS_A", "0MAT",
                                  "2VR_X", "3VF_Y", "1TR_Z", "5VR_X", "RF", "DAC_Q", "TV_X", "2TZ_a"])
def test_every_issue_that_parse_name_can_emit_maps_to_a_known_violation_family(name):
    pn = bridge.parse_name(name)
    for issue in pn["issues"]:
        assert R.violation_family(issue) is not R.ViolationFamily.UNMAPPED_ISSUE, (name, issue)
    shape = R.name_shape(pn, pn["issues"])
    assert shape in {s.value for s in R.NameShape}


def test_typed_artifact_with_a_layer_digit_has_its_own_family_and_shape():
    pn = bridge.parse_name("4AM_X")
    assert [R.violation_family(i).value for i in pn["issues"]] == ["TYPED_ARTIFACT_WITH_LAYER_DIGIT"]
    assert R.name_shape(pn, pn["issues"]) == "TYPED_ARTIFACT_WITH_LAYER_DIGIT"


def test_artifact_and_use_letter_violations_are_separate_families():
    pn = bridge.parse_name("1XZ_foo")                         # artefacto 'X' y uso 'Z' no homologados
    fams = [R.violation_family(i).value for i in pn["issues"]]
    assert fams == ["ARTIFACT_LETTER_NOT_HOMOLOGATED", "USE_LETTER_NOT_HOMOLOGATED"]


def test_an_unknown_issue_text_is_surfaced_not_silently_assigned_a_family():
    assert R.violation_family("texto que parse_name no emite") is R.ViolationFamily.UNMAPPED_ISSUE


@pytest.mark.parametrize("label,field,present,eq,norm", [
    ("LAND1", "LAND1", True, True, True), ("land1", "LAND1", True, False, True), ("Land 1", "LAND1", True, False, True),
    ("Sociedad", "BUKRS", True, False, False), ("", "BUKRS", False, False, False), (None, "BUKRS", False, False, False),
    ("  ", "BUKRS", False, False, False)])
def test_label_relations(label, field, present, eq, norm):
    r = R.label_relations(field, label)
    assert (r["label_present"], r["label_equals_field"], r["label_matches_field_normalized"]) == (present, eq, norm)


def test_helpers_histogram_token_difference_family_and_label_normalization():
    assert R.histogram(["a", "b", "a", None]) == [{"key": "a", "count": 2}, {"key": "<none>", "count": 1}, {"key": "b", "count": 1}]
    assert R.token_difference(["ZCDS", "B"], ["1TR", "ZCDS", "B"]) == ["1TR"]
    assert R.token_difference(["A", "A"], ["A"]) == []
    assert R.token_difference(["A"], ["A", "A"]) == ["A"]                    # respeta multiplicidad
    assert R.naming_family("2VR_X") == "#VR_" and R.naming_family("0MATERIAL_ATTR") == "#MATERIAL_" and R.naming_family("A004") is None
    assert R.normalize_label("  Plan   de CUENTAS ") == "plan de cuentas" and R.normalize_label("Año") == "año"


# ── propiedades comunes: determinismo, pureza, limites ───────────────────────
def test_output_is_independent_of_entity_insertion_order():
    ref = [m.to_jsonable(f) for f in _facts()[1]]
    items = list(m4_entities().items())
    rng = random.Random(11)
    for _ in range(4):
        rng.shuffle(items)
        assert [m.to_jsonable(f) for f in _facts(dict(items))[1]] == ref


def test_precomputed_identity_is_equivalent_to_internal_identity():
    ents = m4_entities()
    u = AssessmentUniverse.from_entities(ents)
    assert naming_facts(u, ents, "AM_X") == naming_facts(u, ents, "AM_X", identity=classify_universe(ents))


def test_extractor_is_pure_it_does_not_mutate_the_entities():
    ents = m4_entities()
    snap = repr({n: (list(e.fields), list(e.upstreams), list(e.associations)) for n, e in ents.items()})
    naming_facts(AssessmentUniverse.from_entities(ents), ents, "AM_X")
    assert repr({n: (list(e.fields), list(e.upstreams), list(e.associations)) for n, e in ents.items()}) == snap


def test_loader_injected_flow_lineage_and_orphans_never_appear_in_naming_facts():
    ents = m3_entities()
    u = AssessmentUniverse.from_entities(ents)
    blob = repr([(f.subject, f.value) for f in naming_facts(u, ents, "AM_M")])
    assert "ONLY_VIA_DF" not in blob                                    # DF_T (target de flujo que Modeling lee) es una hoja miembro; su linaje inyectado no


def test_naming_modules_read_only_loaded_entities_never_raw_files_or_legacy_directly():
    for name in ("naming_rules.py", "facts_naming.py"):
        text = (PKG / name).read_text(encoding="utf-8")
        tree = ast.parse(text)
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mods.add(("." * node.level) + (node.module or ""))
            elif isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
        assert not any("readers" in x for x in mods), (name, mods)
        assert not any(x.startswith("tools.") or x.startswith("webapp") or x.startswith("generate_catalog") for x in mods), (name, mods)
        assert "open(" not in text and "read_text" not in text and "json.load" not in text
    pure_mods = {n.module for n in ast.walk(ast.parse((PKG / "naming_rules.py").read_text(encoding="utf-8")))
                 if isinstance(n, ast.ImportFrom) and n.level}
    assert pure_mods == set(), "naming_rules.py debe ser puro (sin imports del paquete)"


def test_legacy_naming_helpers_are_not_copied_into_the_package():
    text = (PKG / "naming_rules.py").read_text(encoding="utf-8") + (PKG / "facts_naming.py").read_text(encoding="utf-8")
    for needle in ("CAPA_RE", "NOCAPA_RE", "ARTEFACTO =", "USO =", "def parse_name"):
        assert needle not in text, needle
