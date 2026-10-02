"""M5 — senales / observaciones / review items: FACT -> SIGNAL, sin juicio, sin score/banda/peso/umbral/penalidad."""
from __future__ import annotations

import ast
import dataclasses
import json
import random
import re
from pathlib import Path

import pytest

from tests._modeling_assessment_m2_fixtures import m2_entities, write_pool_files
from tests._modeling_assessment_m3_fixtures import m3_entities, with_assocs
from tests._modeling_assessment_m4_fixtures import f4, m4_entities
from tests.test_modeling_assessment_graph import ent, pool
from tools.catalog.modeling_assessment import model as m
from tools.catalog.modeling_assessment import signals as S
from tools.catalog.modeling_assessment.facts_am import am_facts
from tools.catalog.modeling_assessment.facts_harmonization import harmonization_facts
from tools.catalog.modeling_assessment.facts_ingestion import ingestion_facts
from tools.catalog.modeling_assessment.facts_naming import naming_facts
from tools.catalog.modeling_assessment.facts_propagation import propagation_facts
from tools.catalog.modeling_assessment.facts_source import source_facts
from tools.catalog.modeling_assessment.identity import classify_universe
from tools.catalog.modeling_assessment.readers import SupplementaryEvidence
from tools.catalog.modeling_assessment.scope import AssessmentUniverse

PKG = Path(__file__).resolve().parent.parent / "tools" / "catalog" / "modeling_assessment"


def all_facts(ents, am, evidence=None):
    u = AssessmentUniverse.from_entities(ents)
    ident = classify_universe(ents)
    return [*source_facts(u, ents, am, evidence=evidence, identity=ident), *ingestion_facts(u, ents, am, evidence=evidence, identity=ident),
            *am_facts(u, ents, am, identity=ident), *propagation_facts(u, ents, am, identity=ident),
            *harmonization_facts(u, ents, am, identity=ident), *naming_facts(u, ents, am, identity=ident)]


def run(ents, am, evidence=None):
    facts = all_facts(ents, am, evidence)
    return facts, S.evaluate_signals(facts)


def found(ev, signal_id):
    return [f for f in ev.findings if f.signal_id == signal_id]


def ids(ev):
    return {s.signal_id for s in ev.signals}


# ── registro / catalogo ──────────────────────────────────────────────────────
def test_registry_covers_every_canonical_observation_and_review_id_exactly_once():
    reg = [s.signal_id for s in S.SIGNAL_REGISTRY]
    assert len(reg) == len(set(reg))
    assert {x.value for x in m.ObservationId} | {x.value for x in m.ReviewId} <= set(reg)
    by_kind = {s.signal_id: s.kind for s in S.SIGNAL_REGISTRY}
    assert all(by_kind[o.value] is m.SignalKind.OBSERVATION for o in m.ObservationId)
    assert all(by_kind[r.value] is m.SignalKind.REVIEW for r in m.ReviewId)
    assert by_kind["SOURCE_FILTER_PRESENT"] is m.SignalKind.DESCRIPTIVE


def test_each_signal_belongs_to_its_canonical_area():
    A = m.Area
    expected = {"DIRECT_RAW_TABLE_ACCESS": A.SOURCE_EXTRACTION, "SOURCE_FILTER_PRESENT": A.SOURCE_EXTRACTION,
                "PASS_THROUGH_REVIEW": A.PROPAGATION, "CROSS_LAYER_REDERIVATION": A.PROPAGATION,
                "ROLE_LAYER_DIVERGENCE": A.INGESTION, "DUPLICATED_REMOTE_IMPORT": A.INGESTION, "LAYER_BOUNDARY_REVIEW": A.INGESTION,
                "FLAT_CENTRAL_ASSEMBLY_REVIEW": A.HARMONIZATION, "FIELD_VOCABULARY_MIX": A.NAMING, "OPAQUE_FIELD_NAME": A.NAMING,
                "LABEL_NOT_ENRICHING": A.NAMING, "AM_SEMANTIC_UNDERUSE_REVIEW": A.ANALYTIC_MODEL}
    assert {s.signal_id: s.area for s in S.SIGNAL_REGISTRY} == expected


def test_every_registry_entry_declares_its_limits_mapping_and_context():
    for s in S.SIGNAL_REGISTRY:
        assert s.mapping and s.interpretation_limits and s.required_fact_ids and s.role_scope, s.signal_id
        assert not m.find_forbidden_tokens(s.signal_id)
        if s.kind is m.SignalKind.REVIEW:
            assert s.legitimate_reasons and s.missing_context, s.signal_id
        if s.kind is m.SignalKind.OBSERVATION:
            assert s.context_question_key and s.missing_context, s.signal_id


def test_required_facts_of_the_registry_are_real_fact_ids_emitted_by_the_extractors():
    emitted = set()
    for ents, am in ((m2_entities(), "AM_P"), (m3_entities(), "AM_M"), (m4_entities(), "AM_X")):
        emitted |= {f.fact_id for f in all_facts(ents, am)}
    required = {r for s in S.SIGNAL_REGISTRY for r in s.required_fact_ids}
    assert required <= emitted, sorted(required - emitted)


def test_descriptive_reuse_and_source_family_facts_do_not_become_signals():
    reg = {s.signal_id for s in S.SIGNAL_REGISTRY}
    for fid in ("LOCAL_CHAIN_REUSE", "CROSS_AM_REUSE", "SOURCE_FAMILY", "SAPI_STATE"):
        assert fid not in reg
    facts, ev = run(m3_entities(), "AM_M")
    assert any(f.fact_id == "harmonization.cross_am_reuse" for f in facts)          # el hecho existe...
    assert not any("reuse" in s.signal_id.lower() for s in ev.signals)               # ...pero no hay senal con polaridad


# ── DIRECT_RAW_TABLE_ACCESS ──────────────────────────────────────────────────
def _m2_run(tmp_path, with_files=True):
    ents = m2_entities()
    ev = SupplementaryEvidence.from_files(write_pool_files(tmp_path), root=tmp_path) if with_files else None
    return ents, run(ents, "AM_P", ev)


def test_one_observation_per_raw_physical_table_and_none_for_cds_sapi_or_local(tmp_path):
    _, (facts, ev) = _m2_run(tmp_path)
    subjects = sorted(o.subjects[0] for o in ev.observations)
    assert subjects == ["RAW_T", "RAW_T2"]                                            # ABAPTABLES.TAB1 x2; CDS/SAPI/local no
    for o in ev.observations:
        assert o.observation_id is m.ObservationId.DIRECT_RAW_TABLE_ACCESS and o.area is m.Area.SOURCE_EXTRACTION
        assert o.resolution is m.Resolution.OPEN_CONTEXT_REQUIRED and o.context_question_key == "direct_raw_table_access_justified_exception"
        assert len(o.subjects) == 1 and any(r.startswith("source.contract@") for r in o.fact_refs)


def test_the_raw_table_observation_is_always_produced_independent_of_reach_filter_or_spine(tmp_path):
    ents = m2_entities()
    # RAW_T deja de alimentar al AM por linaje: pasa a alcanzarse solo por asociacion (sin camino de linaje puro)
    ents["VR_P"].upstreams = [u for u in ents["VR_P"].upstreams if u != "RAW_T"]
    ents["VR_P"]._raw_refs = [r for r in ents["VR_P"]._raw_refs if r != "RAW_T"]          # el grafo tambien recupera FROM/JOIN desde las refs crudas
    ents["DIM_P"].assoc_upstreams = sorted(set(ents["DIM_P"].assoc_upstreams) | {"RAW_T"})
    ents["DIM_P"].associations = [*ents["DIM_P"].associations, {"campo": "_raw", "target": "RAW_T", "on": {}}]
    ev_files = SupplementaryEvidence.from_files(write_pool_files(tmp_path), root=tmp_path)
    _, ev = run(ents, "AM_P", ev_files)
    t = next(f for f in found(ev, "DIRECT_RAW_TABLE_ACCESS") if f.subjects == ("RAW_T",))
    assert t.detail["reaches_terminal_by_strict_lineage"] is False and t.detail["source_filter"] == "PRESENT"
    assert "RAW_T" in {o.subjects[0] for o in ev.observations}


def test_raw_observation_attaches_reach_filter_label_and_identity_context(tmp_path):
    _, (facts, ev) = _m2_run(tmp_path)
    t = next(f for f in found(ev, "DIRECT_RAW_TABLE_ACCESS") if f.subjects == ("RAW_T",))
    d = t.detail
    assert d["remote_entity"] == "ABAPTABLES.TAB1" and d["artifact_label"] == "CDS View TAB1: texto" and d["reaches_terminal_by_strict_lineage"] is True
    assert d["lineage_distance"] == 3 and d["transitive_lineage_dependents_in_scope"] == 3 and d["source_filter"] == "PRESENT"
    assert d["identity"] == "UNKNOWN"                                  # `RAW_T` no se deriva del stem `TAB1`: M1 no conjetura
    assert d["source_filter"] in ("PRESENT", "ABSENT", "NOT_ASSESSED")
    t2 = next(f for f in found(ev, "DIRECT_RAW_TABLE_ACCESS") if f.subjects == ("RAW_T2",))
    assert t2.detail["source_filter"] == "ABSENT"


def test_without_supplementary_evidence_the_filter_is_not_assessed_but_the_observation_still_exists(tmp_path):
    _, (facts, ev) = _m2_run(tmp_path, with_files=False)
    assert {o.subjects[0] for o in ev.observations} == {"RAW_T", "RAW_T2"}
    assert {f.detail["source_filter"] for f in found(ev, "DIRECT_RAW_TABLE_ACCESS")} == {"NOT_ASSESSED"}


def test_observation_and_review_objects_carry_no_severity_or_numeric_field():
    obs_fields = {f.name for f in dataclasses.fields(m.Observation)}
    rev_fields = {f.name for f in dataclasses.fields(m.ReviewItem)}
    assert obs_fields == {"observation_id", "area", "subjects", "fact_refs", "context_question_key", "resolution"}
    assert rev_fields == {"review_id", "area", "subjects", "fact_refs", "missing_context", "legitimate_reasons", "no_automatic_quality_judgment"}


# ── SOURCE_FILTER_PRESENT (descriptivo) ──────────────────────────────────────
def test_source_filter_is_a_descriptive_signal_without_observation_or_review_item(tmp_path):
    _, (facts, ev) = _m2_run(tmp_path)
    sig = next(s for s in ev.signals if s.signal_id == "SOURCE_FILTER_PRESENT")
    assert sig.kind is m.SignalKind.DESCRIPTIVE and sig.fact_refs == ("source.query_filter@RAW_T",)
    assert [f.subjects for f in found(ev, "SOURCE_FILTER_PRESENT")] == [("RAW_T",)]
    assert all(o.observation_id is m.ObservationId.DIRECT_RAW_TABLE_ACCESS for o in ev.observations)     # el filtro no crea observacion propia
    assert all(r.review_id.value != "SOURCE_FILTER_PRESENT" for r in ev.review_items)


# ── PASS_THROUGH_REVIEW ──────────────────────────────────────────────────────
def _pt(**tweaks):
    flds = lambda *n: [f4(x, x.title()) for x in n]                  # noqa: E731
    am = ent("AM_PT", up=["VF_PT"], cube=True, has_query=True, fields=flds("A", "B"))
    vf = ent("VF_PT", up=["SRC_PT"], pattern="ANALYTICAL_FACT", has_query=True, fields=flds("A", "B"))
    src = ent("SRC_PT", remote_entity="ABAP_CDS.SRC", remote_connection="C1", fields=flds("A", "B"))
    ents = pool(am, vf, src)
    for fn in tweaks.values():
        fn(ents)
    return ents


def test_a_schema_identical_edge_with_nothing_introduced_is_a_pass_through_review_without_judgment():
    facts, ev = run(_pt(), "AM_PT")
    r = [i for i in ev.review_items if i.review_id is m.ReviewId.PASS_THROUGH_REVIEW]
    assert [i.subjects for i in r] == [("VF_PT", "AM_PT")] and r[0].no_automatic_quality_judgment is True
    assert r[0].fact_refs == ("am.contract_delta@VF_PT->AM_PT",) and "role" in r[0].missing_context.lower()
    assert "pass-through is not low maturity" in next(s for s in ev.signals if s.signal_id == "PASS_THROUGH_REVIEW").interpretation_limits


@pytest.mark.parametrize("name,tweak", [
    ("extra_field", lambda e: e["AM_PT"].fields.append(f4("C", "C"))),
    ("introduces_derived", lambda e: e["AM_PT"].fields.__setitem__(0, f4("A", "A", derived=True))),
    ("introduces_association", lambda e: with_assocs(e["AM_PT"], [("_x", "SRC_PT")])),
    ("introduces_measure", lambda e: e["AM_PT"].measures.append({"campo": "A", "agg": "SUM"})),
    ("consumer_is_declared_dimension", lambda e: setattr(e["AM_PT"], "modeling_pattern", "ANALYTICAL_CUBE DIMENSION")),
    ("combines_several_providers", lambda e: (e.update({"SRC2": ent("SRC2", remote_entity="ABAP_CDS.S2", remote_connection="C1",
                                                                     fields=[f4("A", "A"), f4("B", "B")])}),
                                                e["AM_PT"].upstreams.append("SRC2"))),
    ("derivation_flag_not_visible", lambda e: setattr(e["AM_PT"], "has_query", False)),
])
def test_pass_through_is_not_flagged_when_anything_is_introduced_or_the_evidence_is_ambiguous(name, tweak):
    _, ev = run(_pt(t=tweak), "AM_PT")
    assert not [i for i in ev.review_items if i.review_id is m.ReviewId.PASS_THROUGH_REVIEW and i.subjects == ("VF_PT", "AM_PT")], name


# ── CROSS_LAYER_REDERIVATION ─────────────────────────────────────────────────
def _redo(adjacent=True):
    der = lambda n: f4(n, n, derived=True)                            # noqa: E731
    am = ent("AM_RD", up=["VF_RD"], cube=True, has_query=True, fields=[f4("K", "K")])
    vf = ent("VF_RD", up=["VM_RD" if not adjacent else "VR_RD"], pattern="ANALYTICAL_FACT", has_query=True,
             fields=[f4("K", "K"), der("DOSIS"), der("VALOR")])
    chain = [am, vf, ent("SRC_RD", remote_entity="ABAP_CDS.S", remote_connection="C1", fields=[f4("K", "K")])]
    if adjacent:
        chain.append(ent("VR_RD", up=["SRC_RD"], has_query=True, fields=[f4("K", "K"), der("DOSIS"), der("VALOR")]))
    else:
        chain += [ent("VM_RD", up=["VR_RD"], has_query=True, fields=[f4("K", "K")]),
                  ent("VR_RD", up=["SRC_RD"], has_query=True, fields=[f4("K", "K"), der("DOSIS"), f4("OTHER", "O")])]
    return pool(*chain)


def test_the_same_field_derived_in_consecutive_layers_is_one_review_item_per_pair_listing_the_fields():
    _, ev = run(_redo(), "AM_RD")
    f = found(ev, "CROSS_LAYER_REDERIVATION")
    assert len(f) == 1 and f[0].subjects == ("VR_RD", "VF_RD")
    assert f[0].detail == {"provider": "VR_RD", "consumer": "VF_RD", "fields": ["DOSIS", "VALOR"], "lineage_distance": 1, "adjacent": True}
    r = [i for i in ev.review_items if i.review_id is m.ReviewId.CROSS_LAYER_REDERIVATION]
    assert len(r) == 1 and r[0].fact_refs == ("propagation.rederivation@closure",) and r[0].no_automatic_quality_judgment


def test_rederivation_in_later_non_adjacent_layers_is_flagged_as_not_adjacent():
    _, ev = run(_redo(adjacent=False), "AM_RD")
    f = found(ev, "CROSS_LAYER_REDERIVATION")
    assert [(x.subjects, x.detail["adjacent"], x.detail["fields"]) for x in f] == [(("VR_RD", "VF_RD"), False, ["DOSIS"])]


def test_a_field_derived_in_only_one_layer_is_not_a_rederivation():
    ents = _redo()
    ents["VR_RD"].fields = [f4("K", "K"), f4("DOSIS", "D"), f4("VALOR", "V")]          # sin derived en el proveedor
    _, ev = run(ents, "AM_RD")
    assert not found(ev, "CROSS_LAYER_REDERIVATION")


# ── ROLE_LAYER_DIVERGENCE ────────────────────────────────────────────────────
def _rl():
    am = with_assocs(ent("4AM_RL", up=["3VF_RL"], cube=True, has_query=True, fields=[f4("K", "K")]), [("_day", "SAP.TIME.X")])
    vf = ent("3VF_RL", up=["2TR_LEAF", "1TR_MID", "2VR_Z", "VD_NODIGIT", "2VD_OK"], pattern="ANALYTICAL_FACT", has_query=True, fields=[f4("K", "K")])
    return pool(am, vf,
                ent("2TR_LEAF", fields=[f4("K", "K")]),                                          # hoja local con capa 2 en el NOMBRE
                ent("1TR_MID", up=["SRC_RL"], has_query=True, fields=[f4("K", "K")]),            # capa 1 en el nombre pero tiene proveedores
                ent("2VR_Z", up=["SRC_RL"], pattern="ANALYTICAL_FACT", has_query=True, fields=[f4("K", "K")]),   # nombre capa 2, rol declarado hecho (3)
                ent("VD_NODIGIT", up=["SRC_RL"], pattern="ANALYTICAL_DIMENSION", has_query=True, fields=[f4("K", "K")]),  # sin digito
                ent("2VD_OK", up=["SRC_RL"], pattern="ANALYTICAL_DIMENSION", has_query=True, fields=[f4("K", "K")]),      # capa 2 y rol dimension: consistente
                ent("SRC_RL", remote_entity="ABAP_CDS.SRC", remote_connection="C1", fields=[f4("K", "K")]),
                ent("SAP.TIME.X", fields=[f4("K", "K")]))


def test_role_layer_divergence_only_when_the_name_encodes_a_layer_that_conflicts_with_position_or_declared_role():
    _, ev = run(_rl(), "4AM_RL")
    kinds = {f.subjects[0]: f.detail["divergence_kinds"] for f in found(ev, "ROLE_LAYER_DIVERGENCE")}
    assert kinds == {"2TR_LEAF": ["NAME_LAYER_ABOVE_BOUNDARY_POSITION"],
                     "1TR_MID": ["NAME_LAYER_INGESTION_BUT_NOT_A_BOUNDARY"],
                     "2VR_Z": ["NAME_LAYER_DIFFERS_FROM_DECLARED_ROLE_LAYER"]}
    # consistentes / sin capa en el nombre / sistema: nunca
    for quiet in ("3VF_RL", "4AM_RL", "VD_NODIGIT", "2VD_OK", "SRC_RL", "SAP.TIME.X"):
        assert quiet not in kinds
    r = [i for i in ev.review_items if i.review_id is m.ReviewId.ROLE_LAYER_DIVERGENCE]
    assert {i.subjects[0] for i in r} == set(kinds) and all(i.no_automatic_quality_judgment for i in r)


def test_role_layer_divergence_never_uses_the_inferred_layer_of_role_or_prefix_as_if_the_name_encoded_it():
    ents = _rl()
    ents["2TR_LEAF"].name = "ZLEAF_LOCAL"                           # sin capa en el nombre: la capa inferida sale de otra base
    ents2 = {("ZLEAF_LOCAL" if k == "2TR_LEAF" else k): v for k, v in ents.items()}
    ents2["3VF_RL"].upstreams = ["ZLEAF_LOCAL" if u == "2TR_LEAF" else u for u in ents2["3VF_RL"].upstreams]
    _, ev = run(ents2, "4AM_RL")
    assert "ZLEAF_LOCAL" not in {f.subjects[0] for f in found(ev, "ROLE_LAYER_DIVERGENCE")}


# ── DUPLICATED_REMOTE_IMPORT ─────────────────────────────────────────────────
def test_a_remote_entity_under_several_local_identities_is_a_review_item_with_its_scope(tmp_path):
    _, (facts, ev) = _m2_run(tmp_path)
    by = {f.detail["remote_entity"]: f for f in found(ev, "DUPLICATED_REMOTE_IMPORT")}
    assert set(by) == {"ABAPTABLES.TAB1", "ABAP_CDS.CDSA$E"}
    tab = by["ABAPTABLES.TAB1"].detail
    assert tab["duplicated_inside_closure"] is True and tab["spans_outside_closure"] is False and tab["in_closure"] == ["RAW_T", "RAW_T2"]
    cds = by["ABAP_CDS.CDSA$E"].detail
    assert cds["spans_outside_closure"] is True and cds["local_artifacts"] == ["CDS_A", "CDS_A2", "CDS_A3"] and cds["in_closure"] == ["CDS_A", "CDS_A2"]
    r = {i.subjects for i in ev.review_items if i.review_id is m.ReviewId.DUPLICATED_REMOTE_IMPORT}
    assert r == {("RAW_T", "RAW_T2"), ("CDS_A", "CDS_A2")}


# ── LAYER_BOUNDARY_REVIEW ────────────────────────────────────────────────────
def _lb():
    am = with_assocs(ent("AM_LB", up=["3VF_A"], cube=True, has_query=True, fields=[f4("K", "K")]), [("_d", "2VD_B")])
    r = lambda n: ent(n, remote_entity=f"ABAP_CDS.{n}", remote_connection="C1", fields=[f4("K", "K")])      # noqa: E731
    return pool(am,
                ent("3VF_A", up=["R1", "R2", "L3", "R5"], pattern="ANALYTICAL_FACT", has_query=True, fields=[f4("K", "K")]),
                ent("2VD_B", up=["R2"], pattern="ANALYTICAL_DIMENSION", has_query=True, fields=[f4("K", "K")]),
                r("R1"), r("R2"), r("R4"), r("R5"),
                ent("L3", fields=[f4("K", "K")]),
                ent("ZZZ_UNCLASSIFIED", up=["R5"], has_query=True, fields=[f4("K", "K")]))


def test_layer_boundary_requires_a_remote_leaf_whose_every_lineage_consumer_is_layer_3_or_above():
    ents = _lb()
    ents["2VD_B"].upstreams = ["R2"]
    ents["AM_LB"].upstreams = ["3VF_A", "ZZZ_UNCLASSIFIED"]
    _, ev = run(ents, "AM_LB")
    f = {x.detail["source"]: x for x in found(ev, "LAYER_BOUNDARY_REVIEW")}
    assert set(f) == {"R1"}                                            # R2: un consumidor en capa 2 -> no; L3 no es remota; R4 sin consumidores; R5 tiene un consumidor capa 9
    assert f["R1"].subjects == ("R1", "3VF_A") and f["R1"].detail["lineage_consumers"][0]["inferred_layer"] == "3"
    r = [i for i in ev.review_items if i.review_id is m.ReviewId.LAYER_BOUNDARY_REVIEW]
    assert [i.subjects for i in r] == [("R1", "3VF_A")] and r[0].legitimate_reasons


def test_layer_boundary_counts_an_unclassifiable_layer_as_not_at_or_above_3():
    # R5 consumida SOLO por 3VF_A (capa 3) -> cumple
    only_vf = _lb()
    only_vf["ZZZ_UNCLASSIFIED"].upstreams = []                       # ZZZ_UNCLASSIFIED deja de consumir R5
    only_vf["AM_LB"].upstreams = ["3VF_A"]
    _, ev = run(only_vf, "AM_LB")
    assert "R5" in {x.detail["source"] for x in found(ev, "LAYER_BOUNDARY_REVIEW")}
    # un segundo consumidor de linaje cuya capa no se puede clasificar ("9") hace que deje de cumplir "TODOS en capa >= 3"
    both = _lb()
    both["AM_LB"].upstreams = ["3VF_A", "ZZZ_UNCLASSIFIED"]
    _, ev2 = run(both, "AM_LB")
    assert "R5" not in {x.detail["source"] for x in found(ev2, "LAYER_BOUNDARY_REVIEW")}


# ── FLAT_CENTRAL_ASSEMBLY_REVIEW ─────────────────────────────────────────────
def _flat(fan_in, *, dimension=False, declares_assoc=False):
    srcs = [ent(f"S{i}", remote_entity=f"ABAP_CDS.S{i}", remote_connection="C1", fields=[f4("K", "K")]) for i in range(fan_in)]
    hub = ent("2VR_HUB", up=[s.name for s in srcs], has_query=True, pattern="ANALYTICAL_DIMENSION" if dimension else "", fields=[f4("K", "K")])
    if declares_assoc:
        with_assocs(hub, [("_a", "S0")])
    am = ent("AM_FL", up=["2VR_HUB"], cube=True, has_query=True, fields=[f4("K", "K")])
    return pool(am, hub, *srcs)


def test_flat_central_assembly_needs_fan_in_at_or_above_the_provisional_parameter_and_a_flat_contract():
    spec = next(s for s in S.SIGNAL_REGISTRY if s.signal_id == "FLAT_CENTRAL_ASSEMBLY_REVIEW")
    assert dict(spec.parameters) == {"minimum_fan_in": S.FLAT_CENTRAL_MINIMUM_FAN_IN}
    n = S.FLAT_CENTRAL_MINIMUM_FAN_IN
    _, ev = run(_flat(n), "AM_FL")
    f = found(ev, "FLAT_CENTRAL_ASSEMBLY_REVIEW")
    assert [x.subjects for x in f] == [("2VR_HUB",)] and f[0].detail["fan_in"] == n and f[0].detail["minimum_fan_in"] == n
    assert f[0].detail["boundary_providers"] == n


@pytest.mark.parametrize("kw", [dict(fan_in=S.FLAT_CENTRAL_MINIMUM_FAN_IN - 1), dict(fan_in=S.FLAT_CENTRAL_MINIMUM_FAN_IN, dimension=True),
                                dict(fan_in=S.FLAT_CENTRAL_MINIMUM_FAN_IN, declares_assoc=True)])
def test_flat_central_assembly_is_not_flagged_for_low_fan_in_declared_dimensions_or_declared_associations(kw):
    fan_in = kw.pop("fan_in")
    _, ev = run(_flat(fan_in, **kw), "AM_FL")
    assert not found(ev, "FLAT_CENTRAL_ASSEMBLY_REVIEW")


# ── FIELD_VOCABULARY_MIX ─────────────────────────────────────────────────────
def test_field_vocabulary_mix_fires_only_with_strong_evidence_and_never_for_weaker_cross_artifact_groups():
    _, ev = run(m4_entities(), "AM_X")
    f = {x.detail["label"]: x for x in found(ev, "FIELD_VOCABULARY_MIX")}
    assert set(f) == {"sociedad", "importe ars"}                       # `material` (MATNR/MATERIAL): sin co-declaracion ni exposicion en el AM -> solo hecho M4
    soc = f["sociedad"].detail
    assert soc["technical_names"] == ["BUKRS", "COMPANYCODE"] and soc["distinct_forms"] == ["ALNUM_4_6", "ALNUM_7_PLUS"]
    assert soc["evidence_tiers"] == ["CO_DECLARED_IN_ARTIFACT", "EXPOSED_BY_TERMINAL_AM"] and soc["terminal_am_fields"] == ["BUKRS"]
    assert f["importe ars"].detail["evidence_tiers"] == ["CO_DECLARED_IN_ARTIFACT", "EXPOSED_BY_TERMINAL_AM"]
    r = [i for i in ev.review_items if i.review_id is m.ReviewId.FIELD_VOCABULARY_MIX]
    assert len(r) == 2 and all("same business concept" in i.missing_context for i in r)
    lim = " ".join(next(s for s in ev.signals if s.signal_id == "FIELD_VOCABULARY_MIX").interpretation_limits)
    assert "not proof" in lim and "not a synonym" in lim


def test_numbered_series_never_produce_a_vocabulary_mix_even_with_distinct_forms_and_strong_evidence():
    ents = m4_entities()
    ents["AM_X"].fields = [*ents["AM_X"].fields, f4("TXT1", "Texto"), f4("TXT_2", "Texto")]        # forma 4_6 vs UNDERSCORE, mismo stem
    facts, ev = run(ents, "AM_X")
    same = next(f for f in facts if f.fact_id == "naming.same_normalized_label_multiple_technical_names")
    series = next(g for g in same.value["groups"] if g["label"] == "texto")
    assert series["is_numbered_series"] is True and series["artifacts_declaring_multiple_names"]    # el hecho M4 conserva la serie
    assert "texto" not in {x.detail["label"] for x in found(ev, "FIELD_VOCABULARY_MIX")}


def test_a_shared_label_with_the_same_mechanical_form_is_not_a_vocabulary_mix():
    ents = m4_entities()
    ents["AM_X"].fields = [*ents["AM_X"].fields, f4("AAAAA", "Zona"), f4("BBBBB", "Zona")]
    _, ev = run(ents, "AM_X")
    assert "zona" not in {x.detail["label"] for x in found(ev, "FIELD_VOCABULARY_MIX")}


# ── OPAQUE_FIELD_NAME ────────────────────────────────────────────────────────
def _opaque(fields_by_artifact):
    am = ent("AM_OP", up=["VF_OP"], cube=True, has_query=True, fields=fields_by_artifact["AM_OP"])
    vf = ent("VF_OP", up=["SRC_OP"], pattern="ANALYTICAL_FACT", has_query=True, fields=fields_by_artifact["VF_OP"])
    src = ent("SRC_OP", remote_entity="ABAP_CDS.SRC", remote_connection="C1", fields=fields_by_artifact["SRC_OP"])
    return pool(am, vf, src)


def test_opaque_name_is_one_item_per_field_and_label_listing_every_artifact_that_exposes_it():
    z = [f4("ZED_VETNOSIS", "EBIT"), f4("ZED_NIVEL_1", "Id Nivel 1"), f4("ZED_RESP_GESTION", "Resp. Gestión"), f4("ZUSTD_T156M", "ZUSTD_T156M"),
         f4("ZED_X1", "Algo"), f4("KSTAR", "Clase de coste"), f4("ZED_ANO", "Año")]
    _, ev = run(_opaque({"AM_OP": z, "VF_OP": z, "SRC_OP": z}), "AM_OP")
    f = found(ev, "OPAQUE_FIELD_NAME")
    assert len(f) == 1 and f[0].detail == {"field": "ZED_VETNOSIS", "label": "EBIT", "exposed_in": ["AM_OP", "SRC_OP", "VF_OP"]}
    assert f[0].subjects == ("AM_OP", "SRC_OP", "VF_OP")
    # compartir un token (tambien con acento/mayuscula distintos) -> no; etiqueta == nombre -> no; sin tokens comparables -> no; sin prefijo propio -> no detectado
    names = {x.detail["field"] for x in f}
    assert names.isdisjoint({"ZED_NIVEL_1", "ZED_RESP_GESTION", "ZUSTD_T156M", "ZED_X1", "KSTAR", "ZED_ANO"})   # ZED_ANO/"Año": solo el plegado de acentos lo evita


# ── LABEL_NOT_ENRICHING ──────────────────────────────────────────────────────
def test_label_not_enriching_requires_every_exposed_field_to_repeat_its_name_and_none_without_label():
    ents = _opaque({"AM_OP": [f4("A", "A"), f4("B", "B")], "VF_OP": [f4("A", "A"), f4("B", "Bee")], "SRC_OP": [f4("A", "A"), f4("B", "")]})
    _, ev = run(ents, "AM_OP")
    f = found(ev, "LABEL_NOT_ENRICHING")
    assert [x.subjects for x in f] == [("AM_OP",)] and f[0].detail == {"artifact": "AM_OP", "n_fields": 2, "label_equals_field": 2}
    r = [i for i in ev.review_items if i.review_id is m.ReviewId.LABEL_NOT_ENRICHING]
    assert len(r) == 1 and r[0].area is m.Area.NAMING


# ── AM_SEMANTIC_UNDERUSE_REVIEW ──────────────────────────────────────────────
def test_analytic_model_without_associations_is_a_context_dependent_review_and_one_with_associations_is_not():
    flat = _pt()
    _, ev = run(flat, "AM_PT")
    f = found(ev, "AM_SEMANTIC_UNDERUSE_REVIEW")
    assert [x.subjects for x in f] == [("AM_PT",)] and f[0].detail["n_associations"] == 0
    r = next(i for i in ev.review_items if i.review_id is m.ReviewId.AM_SEMANTIC_UNDERUSE_REVIEW)
    assert r.area is m.Area.ANALYTIC_MODEL and "flat" in r.missing_context
    rich = _pt()
    with_assocs(rich["AM_PT"], [("_x", "SRC_PT")])
    assert not found(run(rich, "AM_PT")[1], "AM_SEMANTIC_UNDERUSE_REVIEW")


# ── propiedades comunes ──────────────────────────────────────────────────────
def test_every_fact_ref_of_every_item_exists_among_the_emitted_facts(tmp_path):
    for ents, am, ev_files in ((m4_entities(), "AM_X", None), (m3_entities(), "AM_M", None), (_rl(), "4AM_RL", None)):
        facts, ev = run(ents, am, ev_files)
        known = {f.key for f in facts}
        for item in [*ev.observations, *ev.review_items, *ev.signals, *ev.findings]:
            assert set(item.fact_refs) <= known, item
    facts, ev = _m2_run(tmp_path)[1]
    assert {r for i in [*ev.observations, *ev.review_items] for r in i.fact_refs} <= {f.key for f in facts}


@pytest.mark.parametrize("ents_fn,am", [(m4_entities, "AM_X"), (_rl, "4AM_RL"), (m3_entities, "AM_M"), (_lb, "AM_LB")])
def test_evaluation_is_deterministic_and_independent_of_fact_order(ents_fn, am):
    facts = all_facts(ents_fn(), am)
    ref = S.evaluate_signals(facts)
    assert ref.findings
    rng = random.Random(3)
    for _ in range(4):
        shuffled = list(facts)
        rng.shuffle(shuffled)
        assert S.evaluate_signals(shuffled) == ref
    assert S.evaluate_signals(facts) == ref


def test_missing_or_not_applicable_facts_produce_nothing_and_never_crash():
    assert S.evaluate_signals([]) == S.SignalEvaluation(S.SIGNALS_RULESET, (), (), (), ())
    facts = all_facts(m4_entities(), "AM_X")
    only_naming = [f for f in facts if f.fact_id.startswith("naming.")]
    ev = S.evaluate_signals(only_naming)
    assert not any(s.signal_id in ("DIRECT_RAW_TABLE_ACCESS", "PASS_THROUGH_REVIEW", "AM_SEMANTIC_UNDERUSE_REVIEW") for s in ev.signals)
    system_only = [f for f in facts if f.assessability is m.Assessability.NOT_APPLICABLE]
    assert system_only and S.evaluate_signals(system_only).findings == ()


def test_no_signal_object_or_finding_carries_scoring_vocabulary_or_a_quality_judgment():
    for ents, am in ((m4_entities(), "AM_X"), (m3_entities(), "AM_M"), (_rl(), "4AM_RL")):
        _, ev = run(ents, am)
        assert ev.ruleset_version == S.SIGNALS_RULESET == "signal-rules/v1"
        for s in ev.signals:
            assert s.ruleset_version == S.SIGNALS_RULESET and not m.find_forbidden_tokens(s.signal_id)
        for f in ev.findings:
            blob = json.dumps(f.detail, ensure_ascii=False).lower()
            for word in ("score", "penalty", "bad", "weak", "defect", "maturity", "pleno", "bueno", "parcial"):
                assert not re.search(rf"\b{word}\b", blob), (f.signal_id, word)
            assert not [k for k in _keys(f.detail) if m.find_forbidden_tokens(k)]
        for i in ev.review_items:
            assert i.no_automatic_quality_judgment is True
        for o in ev.observations:
            assert o.resolution is m.Resolution.OPEN_CONTEXT_REQUIRED


def _keys(value):
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            out.append(k)
            out.extend(_keys(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_keys(v))
    return out


def test_signals_read_only_facts_never_entities_files_or_legacy_directly():
    text = (PKG / "signals.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mods.add(("." * node.level) + (node.module or ""))
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    assert mods <= {"__future__", "re", "unicodedata", "dataclasses", "typing", ".model"}, mods
    for needle in ("open(", "read_text", "json.load", "legacy_bridge", "generate_catalog"):
        assert needle not in text, needle
    assert not re.search(r"\bentities\b", text)                          # no recibe ni recorre entidades: solo hechos


def test_known_interpretation_limits_are_persisted_in_the_registry_texts():
    lim = {s.signal_id: " ".join(s.interpretation_limits) for s in S.SIGNAL_REGISTRY}
    assert "provisional shadow detection logic" in lim["FLAT_CENTRAL_ASSEMBLY_REVIEW"] and "M8" in lim["FLAT_CENTRAL_ASSEMBLY_REVIEW"]
    assert "not a maturity threshold" in lim["FLAT_CENTRAL_ASSEMBLY_REVIEW"] and "not evidence of a defect" in lim["FLAT_CENTRAL_ASSEMBLY_REVIEW"]
    assert "generic labels may still produce false-positive" in lim["FIELD_VOCABULARY_MIX"]
    assert "no enterprise vocabulary dictionary" in lim["OPAQUE_FIELD_NAME"]
    assert "no signal does NOT mean the pattern is absent" in lim["PASS_THROUGH_REVIEW"]


def test_the_flat_central_parameter_is_the_only_numeric_rule_parameter_and_is_declared():
    params = [(s.signal_id, dict(s.parameters)) for s in S.SIGNAL_REGISTRY if s.parameters]
    assert params == [("FLAT_CENTRAL_ASSEMBLY_REVIEW", {"minimum_fan_in": S.FLAT_CENTRAL_MINIMUM_FAN_IN})]


def test_a_review_never_states_which_side_is_right_and_every_review_names_legitimate_reasons():
    _, ev = run(m4_entities(), "AM_X")
    for i in ev.review_items:
        assert i.legitimate_reasons and i.missing_context and i.fact_refs and i.subjects
