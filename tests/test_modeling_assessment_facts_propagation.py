"""M3 — hechos de PROPAGATION: evolucion del contrato a lo largo del linaje. Solo hechos; el pass-through no se juzga."""
from __future__ import annotations

from tests._modeling_assessment_m3_fixtures import fld, m3_entities, with_assocs
from tests.test_modeling_assessment_graph import ent, pool
from tools.catalog.modeling_assessment import model as m
from tools.catalog.modeling_assessment.facts_propagation import propagation_facts
from tools.catalog.modeling_assessment.scope import AssessmentUniverse

A_ = m.Assessability


def _facts(ents=None, am="AM_M"):
    ents = ents or m3_entities()
    return ents, propagation_facts(AssessmentUniverse.from_entities(ents), ents, am)


def _by(facts, fid, subject=None):
    out = [f for f in facts if f.fact_id == fid and (subject is None or f.subject == subject)]
    return out[0] if subject is not None else out


def test_every_propagation_fact_is_propagation_area_modeling_plane():
    _, facts = _facts()
    assert {f.area for f in facts} == {m.Area.PROPAGATION} and {f.plane for f in facts} == {m.Plane.MODELING}
    assert {f.fact_id for f in facts} == {"propagation.derived_fields", "propagation.contract_delta", "propagation.contract_occurrences",
                                          "propagation.rederivation", "propagation.derivation_semantics"}


# ── derivados por artefacto ────────────────────────────────────────────────
def test_derived_fields_split_measures_from_non_measures_and_keep_role_context():
    ents = m3_entities()
    ents["VF_M"].fields.append(fld("SUPPLIER", derived=True))                     # derivado que NO es medida
    _, facts = _facts(ents)
    f = _by(facts, "propagation.derived_fields", "VF_M")
    v = f.value
    assert f.evidence_class is m.EvidenceClass.A and f.assessability is A_.ASSESSED
    assert (v["n_derived"], v["n_derived_measures"], v["n_derived_non_measures"]) == (3, 2, 1)
    assert [r["field"] for r in v["derived_fields"]] == ["AMT", "QTY", "SUPPLIER"]
    assert v["declared_role"]["declares_fact"] is True and v["derivation_visible"] is True
    # una dimension con 0 derivados es un hecho, no una carencia
    assert _by(facts, "propagation.derived_fields", "DIM_V").value["n_derived"] == 0


def test_the_terminal_am_derived_fields_live_in_the_am_inventory_not_here():
    _, facts = _facts()
    assert [f for f in facts if f.fact_id == "propagation.derived_fields" and f.subject == "AM_M"] == []


def test_view_without_a_parsed_query_is_partially_assessed_with_an_explicit_cause():
    _, facts = _facts()
    f = _by(facts, "propagation.derived_fields", "NOQ_V")
    assert f.assessability is A_.PARTIALLY_ASSESSED and f.cause is m.Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT
    assert f.value["derivation_visible"] is False and f.value["n_derived"] == 0


def test_system_artifacts_are_not_applicable_for_derivation_facts():
    _, facts = _facts()
    f = _by(facts, "propagation.derived_fields", "SAP.TIME.DAY")
    assert f.assessability is A_.NOT_APPLICABLE and f.value is None
    assert f.applicability.reason is m.ApplicabilityReason.SYSTEM_ARTIFACT


# ── delta por eslabon ──────────────────────────────────────────────────────
def test_contract_delta_exists_for_non_leaf_providers_only_and_never_for_the_terminal_am_consumer():
    _, facts = _facts()
    subjects = {f.subject for f in _by(facts, "propagation.contract_delta")}
    assert subjects == {"VR_M->VF_M", "DIM_V->VR_M", "NOQ_V->VR_M"}
    # las hojas (SRC1..) viven en ingestion.field_retention; VF_M->AM_M vive en am.contract_delta
    assert not any(s.startswith("SRC") or s.endswith("->AM_M") for s in subjects)


def test_contract_delta_introduced_vs_inherited_and_first_derivation():
    _, facts = _facts()
    f = _by(facts, "propagation.contract_delta", "VR_M->VF_M")
    v = f.value
    assert f.evidence_class is m.EvidenceClass.B
    assert v["fields"]["added"] == ["QTY", "NEWF"] and v["fields"]["removed"] == [] and v["fields"]["retained_count"] == 6
    assert v["measures"]["added"] == ["AMT", "QTY"]
    assert [a["target"] for a in v["associations"]["introduced"]] == ["DIM_A", "DIM_B"] and v["associations"]["inherited"] == []
    assert v["keys"]["equal"] is True and v["keys"]["both_declare_keys"] is True
    assert v["derived"]["in_both"] == ["AMT"] and v["derived"]["only_in_consumer"] == ["QTY"]
    assert v["consumer_layer_inference"] == {"inferred_layer": "3", "inferred_layer_basis": "DECLARED_ROLE"}
    assert v["consumer_role"]["declares_fact"] is True
    assert v["consumer_lineage_provider_count"] == 1


def test_fan_in_is_retained_per_edge_so_a_later_signal_can_restrict_pass_through_to_single_provider_edges():
    _, facts = _facts()
    assert _by(facts, "propagation.contract_delta", "DIM_V->VR_M").value["consumer_lineage_provider_count"] == 5      # + DF_T (hoja producida por un flujo)


def test_pure_pass_through_edge_is_described_with_plain_facts_and_no_judgment():
    chain = pool(
        ent("AM_C", up=["VF_C"], cube=True, has_query=True, fields=[fld("A"), fld("B")]),
        ent("VF_C", up=["VR_C"], pattern="ANALYTICAL_FACT", has_query=True, fields=[fld("A"), fld("B")]),
        ent("VR_C", up=["SRC_C"], has_query=True, fields=[fld("A"), fld("B")]),
        ent("SRC_C", remote_entity="ABAP_CDS.C", remote_connection="C1", fields=[fld("A"), fld("B")]))
    _, facts = _facts(chain, "AM_C")
    v = _by(facts, "propagation.contract_delta", "VR_C->VF_C").value
    assert v["descriptors"] == {"schema_identical": True, "introduces_no_derived_field": True,
                                "introduces_no_association": True, "introduces_no_measure": True}
    assert v["derived"]["in_consumer"] == [] and v["fields"]["added"] == [] and v["fields"]["removed"] == []


def test_edges_touching_system_artifacts_are_not_applicable():
    ents = m3_entities()
    # un artefacto de sistema con proveedor de linaje no hoja
    ents["SAP.TIME.M"].upstreams = ["SYS_BASE"]
    ents["SYS_BASE"] = ent("SYS_BASE", up=["SRC1"], has_query=True)
    ents["SAP.TIME.M"].has_query = True
    _, facts = _facts(ents)
    f = _by(facts, "propagation.contract_delta", "SYS_BASE->SAP.TIME.M")
    assert f.assessability is A_.NOT_APPLICABLE and f.applicability.reason is m.ApplicabilityReason.SYSTEM_ARTIFACT


# ── ocurrencias del contrato del AM ────────────────────────────────────────
def _occ(facts):
    return {(e["kind"], e["target"] or e["fields"][0]): e
            for e in _by(facts, "propagation.contract_occurrences", "closure").value["elements"]}


def test_occurrences_separate_direct_spine_introduction_points_from_all_closure_occurrences():
    _, facts = _facts()
    el = _occ(facts)
    dim_a = el[("association", "DIM_A")]
    assert dim_a["fields"] == ["_dimA", "_dimA2"]                                  # mismo destino, dos campos de asociacion
    assert [o["artifact"] for o in dim_a["closure_occurrences"]] == ["AM_M", "VF_M"]
    assert dim_a["direct_spine_introduction_points"] == ["VF_M"] and dim_a["introduction_point_count"] == 1
    amt = el[("measure", "AMT")]
    assert [(o["artifact"], o["lineage_distance"], o["on_strict_lineage_spine"], o["as_measure"], o["derived"])
            for o in amt["closure_occurrences"]] == [("AM_M", 0, True, True, False), ("VF_M", 1, True, True, True),
                                                      ("VR_M", 2, True, False, True)]
    assert amt["direct_spine_introduction_points"] == ["VR_M"]                      # aqui aparece el campo por primera vez
    assert el[("measure", "QTY")]["direct_spine_introduction_points"] == ["VF_M"]
    calc = el[("derived_field", "CALC")]
    assert calc["direct_spine_introduction_points"] == ["AM_M"]


def test_no_generic_origin_key_is_ever_emitted():
    _, facts = _facts()
    blob = repr([f.value for f in facts]) + " ".join(f.fact_id for f in facts)
    assert "origin_artifacts" not in blob and "contract_origination" not in blob and "'origin'" not in blob


def test_occurrences_cover_the_semantic_closure_and_flag_what_is_off_the_strict_spine():
    chain = pool(
        with_assocs(ent("AM_X", up=["V1"], cube=True, has_query=True, fields=[fld("F")], measures=[{"campo": "F", "agg": ""}]),
                    [("_d", "DIMX")]),
        ent("V1", up=["S"], has_query=True, fields=[fld("F")]),
        ent("S", remote_entity="X.S", remote_connection="C", fields=[fld("F")]),
        ent("DIMX", pattern="ANALYTICAL_DIMENSION", has_query=True, fields=[fld("F")]))     # solo por asociacion
    _, facts = _facts(chain, "AM_X")
    occ = {o["artifact"]: o for o in _occ(facts)[("measure", "F")]["closure_occurrences"]}
    assert set(occ) == {"AM_X", "V1", "S", "DIMX"}
    assert occ["DIMX"]["on_strict_lineage_spine"] is False and occ["DIMX"]["lineage_distance"] is None
    assert occ["DIMX"]["semantic_distance"] == 1 and occ["S"]["on_strict_lineage_spine"] is True
    # DIMX NO es punto de introduccion de espina; que aparezca fuera de ella no se interpreta
    assert _occ(facts)[("measure", "F")]["direct_spine_introduction_points"] == ["S"]


def test_a_branched_spine_keeps_every_introduction_point_without_collapsing_to_one_origin():
    ents = pool(
        ent("AM_B", up=["V1"], cube=True, has_query=True, fields=[fld("F")]),
        ent("V1", up=["L", "R"], has_query=True, fields=[fld("F")]),
        ent("L", up=["S1"], has_query=True, fields=[fld("F")]), ent("R", up=["S2"], has_query=True, fields=[fld("F")]),
        ent("S1", remote_entity="X.1", remote_connection="C"), ent("S2", remote_entity="X.2", remote_connection="C"))
    _, facts = _facts(ents, "AM_B")
    assert ("measure", "F") not in _occ(facts)                                      # F no es medida del AM: no se inventa
    ents["AM_B"].measures = [{"campo": "F", "agg": ""}]
    _, facts = _facts(ents, "AM_B")
    e = _occ(facts)[("measure", "F")]
    assert e["direct_spine_introduction_points"] == ["L", "R"] and e["introduction_point_count"] == 2
    assert [o["artifact"] for o in e["closure_occurrences"]] == ["AM_B", "V1", "L", "R"]


def test_multiple_occurrences_are_not_interpreted_as_duplication_or_a_wrong_layer():
    _, facts = _facts()
    # el contrato de relaciones de G3 usa vocabulario propio (DUPLICATED_TRANSFORMATION...) en estos dos hechos; el resto no interpreta repeticion
    blob = repr([f.value for f in facts if f.fact_id not in ("propagation.rederivation", "propagation.derivation_semantics")]).lower()
    assert not any(t in blob for t in ("duplicat", "redundan", "wrong_layer", "misplaced"))


# ── re-derivacion ──────────────────────────────────────────────────────────
def test_rederivation_flags_the_same_field_derived_in_consecutive_lineage_artifacts():
    _, facts = _facts()
    f = _by(facts, "propagation.rederivation", "closure")
    occ = f.value["occurrences"]
    assert f.evidence_class is m.EvidenceClass.B and f.value["adjacent_count"] == 1
    assert occ == [{"field": "AMT", "consumer": "VF_M", "provider": "VR_M", "lineage_distance": 1, "adjacent": True,
                    "consumer_layer_inference": {"inferred_layer": "3", "inferred_layer_basis": "DECLARED_ROLE"},
                    "provider_layer_inference": {"inferred_layer": "3", "inferred_layer_basis": "NAME_PREFIX"},
                    # sin evidencia suplementaria (query compilado) la relacion NO se adivina: UNKNOWN explicito
                    "relation": "UNKNOWN", "relation_reason": "NO_QUERY_EVIDENCE", "exposed_by_root": True, "exposure_basis": "SAME_NAME_IN_ROOT_CONTRACT"}]


def test_rederivation_also_detects_non_adjacent_ancestors_with_their_distance():
    chain = pool(
        ent("AM_R", up=["V3"], cube=True, has_query=True, fields=[fld("F")]),
        ent("V3", up=["V2"], has_query=True, fields=[fld("F", derived=True)]),
        ent("V2", up=["V1"], has_query=True, fields=[fld("F")]),
        ent("V1", up=["S"], has_query=True, fields=[fld("F", derived=True)]),
        ent("S", remote_entity="X.S", remote_connection="C", fields=[fld("F")]))
    _, facts = _facts(chain, "AM_R")
    occ = _by(facts, "propagation.rederivation", "closure").value["occurrences"]
    assert [(o["field"], o["consumer"], o["provider"], o["lineage_distance"], o["adjacent"]) for o in occ] == [("F", "V3", "V1", 2, False)]


def test_no_rederivation_when_nothing_is_derived_twice():
    chain = pool(ent("AM_N0", up=["V"], cube=True, has_query=True, fields=[fld("F")]),
                 ent("V", up=["S"], has_query=True, fields=[fld("F", derived=True)]),
                 ent("S", remote_entity="X.S", remote_connection="C", fields=[fld("F")]))
    _, facts = _facts(chain, "AM_N0")
    assert _by(facts, "propagation.rederivation", "closure").value["occurrences"] == []


# ── limite de observabilidad ───────────────────────────────────────────────
def test_derivation_semantics_without_query_evidence_is_not_assessed_never_a_default_value():
    _, facts = _facts()
    f = _by(facts, "propagation.derivation_semantics", "closure")
    assert f.value is None and f.assessability is A_.NOT_ASSESSED
    assert f.cause is m.Cause.NOT_IN_SNAPSHOT and f.evidence_class is m.EvidenceClass.C1


def test_processing_context_never_enters_propagation_facts():
    _, facts = _facts()
    blob = repr([(f.fact_id, f.subject, f.value) for f in facts])
    assert "DF_T" not in blob and "ONLY_VIA_DF" not in blob
