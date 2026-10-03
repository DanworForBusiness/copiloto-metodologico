"""M6 — assessability + cobertura + confianza sobre SUPERFICIES de evidencia requeridas (no sobre conteo de Facts/senales)."""
from __future__ import annotations

import ast
import random
from pathlib import Path

import pytest

from tests._modeling_assessment_m2_fixtures import m2_entities, write_pool_files
from tests._modeling_assessment_m3_fixtures import m3_entities, with_assocs
from tests._modeling_assessment_m4_fixtures import f4, m4_entities
from tests.test_modeling_assessment_graph import ent, pool
from tests.test_modeling_assessment_signals import _flat, _lb, _pt, _pt_internal, _redo, _rl, all_facts
from tools.catalog.modeling_assessment import assessability as A
from tools.catalog.modeling_assessment import model as m
from tools.catalog.modeling_assessment import signals as S
from tools.catalog.modeling_assessment.readers import SupplementaryEvidence
from tools.catalog.modeling_assessment.scope import AssessmentUniverse

PKG = Path(__file__).resolve().parent.parent / "tools" / "catalog" / "modeling_assessment"
AS, PA, NA_, NAP, IND = (m.Assessability.ASSESSED, m.Assessability.PARTIALLY_ASSESSED, m.Assessability.NOT_ASSESSED,
                         m.Assessability.NOT_APPLICABLE, m.Assessability.INDETERMINATE)


def compose(ents, am, evidence=None, facts=None, evaluation=None):
    u = AssessmentUniverse.from_entities(ents)
    return A.compose_assessment(u.scope_for(am), facts if facts is not None else all_facts(ents, am, evidence), evaluation)


def surface(comp, sid):
    return next(s for s in comp.surfaces if s.surface_id == sid)


def _local_chain():
    """Patron CASE 1: toda la hoja es una tabla LOCAL sin `remote_entity` (se materializa por Processing)."""
    f = lambda: [f4("A", "Alfa"), f4("B", "Beta")]                                      # noqa: E731
    return pool(ent("AM_C", up=["VF_C"], cube=True, has_query=True, fields=f()),
                ent("VF_C", up=["VR_C"], pattern="ANALYTICAL_FACT", has_query=True, fields=f()),
                ent("VR_C", up=["LOC_C"], has_query=True, fields=f()),
                ent("LOC_C", repository_type="DWC_LOCAL_TABLE", fields=f()))


# ── registro de superficies ──────────────────────────────────────────────────
def test_every_emitted_fact_family_has_a_surface_and_every_rule_signal_has_a_rule_surface(tmp_path):
    emitted = set()
    ev_files = SupplementaryEvidence.from_files(write_pool_files(tmp_path), root=tmp_path)
    for ents, am, ev in ((m2_entities(), "AM_P", ev_files), (m3_entities(), "AM_M", None), (m4_entities(), "AM_X", None)):
        emitted |= {f.fact_id for f in all_facts(ents, am, ev)}
    covered = {fid for s in A.SURFACE_REGISTRY if s.kind is A.SurfaceKind.FACT_EVIDENCE for fid in s.fact_ids}
    assert emitted <= covered, sorted(emitted - covered)
    rule_signals = {s.signal_id for s in A.SURFACE_REGISTRY if s.kind is A.SurfaceKind.RULE_EVALUATION}
    assert rule_signals == {x.value for x in m.ObservationId} | {x.value for x in m.ReviewId}
    ids = [s.surface_id for s in A.SURFACE_REGISTRY]
    assert len(ids) == len(set(ids)) and not any(m.find_forbidden_tokens(i) for i in ids)


# ── agregacion de estados ────────────────────────────────────────────────────
def _st(*xs):
    return [(a, c, None) for a, c in xs]


def test_aggregation_all_assessed_partial_none_and_not_applicable():
    C = m.Cause
    assert A._aggregate(_st((AS, None), (AS, None)))[:2] == (AS, None)
    state, cause, _, counts = A._aggregate(_st((AS, None), (NA_, C.PLANE_BOUNDARY)))
    assert (state, cause) == (PA, C.PLANE_BOUNDARY) and (counts.assessed, counts.not_assessed) == (1, 1)
    assert A._aggregate(_st((PA, C.NOT_IN_SNAPSHOT)))[:2] == (PA, C.NOT_IN_SNAPSHOT)
    assert A._aggregate(_st((NA_, C.PLANE_BOUNDARY), (NA_, C.PLANE_BOUNDARY), (NA_, C.PROVENANCE_UNKNOWN)))[:2] == (NA_, C.PLANE_BOUNDARY)   # causa dominante
    state, _, reason, counts = A._aggregate([(NAP, None, "SYSTEM_ARTIFACT"), (NAP, None, "SYSTEM_ARTIFACT")])
    assert state is NAP and reason == "SYSTEM_ARTIFACT" and counts.applicable == 0 and counts.not_applicable == 2
    assert A._aggregate([(NAP, None, "X"), (AS, None, None)])[0] is AS                  # NOT_APPLICABLE no cuenta contra la superficie
    assert A._aggregate(_st((IND, None)))[:2] == (IND, m.Cause.CONTRADICTORY_EVIDENCE)


def test_cause_ties_are_broken_deterministically_by_enum_order():
    a = A._aggregate(_st((NA_, m.Cause.PROVENANCE_UNKNOWN), (NA_, m.Cause.NOT_IN_SNAPSHOT)))[1]
    b = A._aggregate(_st((NA_, m.Cause.NOT_IN_SNAPSHOT), (NA_, m.Cause.PROVENANCE_UNKNOWN)))[1]
    assert a == b == m.Cause.NOT_IN_SNAPSHOT


# ── cobertura: superficies requeridas, no Facts ni senales ───────────────────
def test_coverage_counts_one_entry_per_required_surface_by_aggregated_state():
    comp = compose(m4_entities(), "AM_X")
    req = [s for s in comp.surfaces if s.role is A.SurfaceRole.REQUIRED]
    by_state = {st: sum(1 for s in req if s.assessability is st) for st in m.Assessability}
    o = comp.coverage.overall
    assert (o.assessed, o.partially_assessed, o.not_assessed, o.indeterminate, o.not_applicable) == (
        by_state[AS], by_state[PA], by_state[NA_], by_state[IND], by_state[NAP])
    assert sum([o.assessed, o.partially_assessed, o.not_assessed, o.indeterminate, o.not_applicable]) == len(req)
    n_facts = len(all_facts(m4_entities(), "AM_X"))
    assert len(req) != n_facts                                                          # la cobertura NO es un conteo de Facts
    assert o.ratio_strict == o.assessed / o.applicable and o.ratio_inclusive == (o.assessed + o.partially_assessed) / o.applicable


def test_by_area_counts_sum_to_the_overall_counts():
    comp = compose(m3_entities(), "AM_M")
    tot = A._sum_counts(a.counts for a in comp.coverage.by_area)
    assert tot == comp.coverage.overall and [a.area for a in comp.coverage.by_area] == list(m.ASSESSED_AREAS)


def test_context_only_and_derived_view_surfaces_are_excluded_from_numerator_and_denominator():
    facts = all_facts(m4_entities(), "AM_X")
    full = compose(m4_entities(), "AM_X", facts=facts)
    ctx_only = {"naming.convention_context", "harmonization.reuse_context"}
    pure_views = {"naming.artifact_name_populations", "naming.field_vocabulary_summary", "source.family_distribution"}   # ninguna regla depende de ellos
    assert compose(m4_entities(), "AM_X", facts=[f for f in facts if f.fact_id not in ctx_only]).coverage == full.coverage
    assert compose(m4_entities(), "AM_X", facts=[f for f in facts if f.fact_id not in pure_views]).coverage == full.coverage
    assert compose(m4_entities(), "AM_X", facts=[f for f in facts if f.fact_id not in ctx_only | pure_views]).coverage == full.coverage
    # y la superficie de contexto existe, esta NOT_ASSESSED/CONTEXT_REQUIRED y NO cuenta
    conv = surface(full, "naming.convention_context")
    assert conv.role is A.SurfaceRole.CONTEXT_ONLY and conv.assessability is NA_ and conv.cause is m.Cause.CONTEXT_REQUIRED
    assert full.coverage.overall.not_assessed == sum(1 for s in full.surfaces if s.role is A.SurfaceRole.REQUIRED and s.assessability is NA_)


def test_not_applicable_is_removed_from_the_denominator():
    comp = compose(_pt(), "AM_PT")                                                      # un solo AM: sin par para CROSS_AM_REUSE
    cross = surface(comp, "harmonization.cross_am_reuse")
    assert cross.assessability is NAP
    o = comp.coverage.overall
    assert o.not_applicable >= 1 and o.applicable == o.assessed + o.partially_assessed + o.not_assessed + o.indeterminate


def test_processing_plane_evidence_never_changes_modeling_denominators(tmp_path):
    ents = m2_entities()
    ev_files = SupplementaryEvidence.from_files(write_pool_files(tmp_path), root=tmp_path)
    facts = all_facts(ents, "AM_P", ev_files)
    proc = [f for f in facts if f.plane is m.Plane.PROCESSING]
    assert proc
    with_p = compose(ents, "AM_P", facts=facts)
    without_p = compose(ents, "AM_P", facts=[f for f in facts if f.plane is not m.Plane.PROCESSING])
    assert with_p.coverage == without_p.coverage
    xpl = [s for s in with_p.surfaces if s.role is A.SurfaceRole.CROSS_PLANE_COMPLEMENT]
    assert {s.surface_id for s in xpl} == {"ingestion.dataflow_boundary", "ingestion.processing_context_inventory", "ingestion.processing_reference",
                                           "source.replication_mode"}
    assert surface(with_p, "ingestion.processing_reference").assessability is AS          # existe y se evalua, pero NO entra en la cobertura


def test_coverage_is_independent_of_the_number_of_signals_or_findings():
    ents = m4_entities()
    facts = all_facts(ents, "AM_X")
    real = compose(ents, "AM_X", facts=facts)
    none = compose(ents, "AM_X", facts=facts, evaluation=S.SignalEvaluation(S.SIGNALS_RULESET, (), (), (), ()))
    assert real.coverage == none.coverage
    assert [s.assessability for s in real.surfaces] == [s.assessability for s in none.surfaces]
    assert len(real.findings) > 0 and len(none.findings) == 0


# ── ausencia de senal != ausencia evaluada ───────────────────────────────────
def test_zero_findings_with_unavailable_provenance_is_not_assessed_not_an_assessed_absence():
    """Patron CASE 1: no hay observaciones de tabla raw, pero la procedencia de la hoja local NO es observable."""
    comp = compose(_local_chain(), "AM_C")
    raw = surface(comp, "rule.direct_raw_table_access")
    assert raw.finding_count == 0 and raw.assessability is NA_ and raw.cause is m.Cause.PROVENANCE_UNKNOWN
    assert raw.result is A.RuleResult.ABSENCE_NOT_ESTABLISHED
    for sid in ("rule.duplicated_remote_import", "rule.layer_boundary_review"):
        assert surface(comp, sid).assessability is NA_ and surface(comp, sid).result is A.RuleResult.ABSENCE_NOT_ESTABLISHED


def test_zero_findings_with_available_evidence_is_an_assessed_absence():
    comp = compose(_flat(2), "AM_FL")                                                   # fuentes CDS remotas: ninguna es tabla fisica
    raw = surface(comp, "rule.direct_raw_table_access")
    assert raw.finding_count == 0 and raw.assessability is AS and raw.result is A.RuleResult.ASSESSED_ABSENCE
    flat = surface(comp, "rule.flat_central_assembly_review")
    assert flat.finding_count == 0 and flat.assessability is AS and flat.result is A.RuleResult.ASSESSED_ABSENCE


def test_the_same_zero_findings_can_mean_full_or_null_assessability_and_they_are_not_equivalent():
    assessed = surface(compose(_flat(2), "AM_FL"), "rule.direct_raw_table_access")
    not_assessed = surface(compose(_local_chain(), "AM_C"), "rule.direct_raw_table_access")
    assert assessed.finding_count == not_assessed.finding_count == 0
    assert assessed.assessability is not not_assessed.assessability and assessed.result is not not_assessed.result


def test_a_found_pattern_reports_pattern_found_even_when_the_surface_is_only_partially_assessed():
    ents = _pt_internal(t=lambda e: setattr(e["VR_PT"], "has_query", False))             # la transformacion VX->VR no expone el flag `derived`
    pt = surface(compose(ents, "AM_PT"), "rule.pass_through_review")
    assert pt.finding_count == 1 and pt.result is A.RuleResult.PATTERN_FOUND
    assert pt.assessability is PA and pt.cause is m.Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT      # parcial por una arista ELEGIBLE sin descriptores
    assert sorted(u.assessability.value for u in pt.units if u.assessability is not NAP) == ["ASSESSED", "NOT_ASSESSED"]


def test_pass_through_absence_is_assessed_for_eligible_edges_and_not_established_when_the_derivation_flag_is_invisible():
    def not_pass_through(e):
        e["VR_PT"].fields.append(f4("C", "C"))                                           # VX->VR agrega C
        e["VF_PT"].fields.extend([f4("C", "C"), f4("D", "D")])                           # VR->VF agrega D
    comp = compose(_pt_internal(t=not_pass_through), "AM_PT")
    pt = surface(comp, "rule.pass_through_review")
    assert pt.finding_count == 0 and pt.result is A.RuleResult.ASSESSED_ABSENCE and pt.assessability is AS    # ausencia EVALUADA: todas las elegibles se evaluaron

    def invisible(e):
        not_pass_through(e)
        e["VR_PT"].has_query = False
        e["VF_PT"].has_query = False                                                      # el flag `derived` no existe sin query visible
    pt2 = surface(compose(_pt_internal(t=invisible), "AM_PT"), "rule.pass_through_review")
    assert pt2.finding_count == 0 and pt2.result is A.RuleResult.ABSENCE_NOT_ESTABLISHED and pt2.assessability is NA_
    assert {u.cause for u in pt2.units if u.assessability is NA_} == {m.Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT}


def test_ineligible_edges_are_not_applicable_to_the_pass_through_rule_and_never_lower_its_assessability():
    pt = surface(compose(_pt_internal(), "AM_PT"), "rule.pass_through_review")
    na = {u.subject: u.not_applicable_reason for u in pt.units if u.assessability is NAP}
    assert na == {"SRC_PT->VX_PT": "EDGE_OUTSIDE_RULE_POPULATION", "VF_PT->AM_PT": "EDGE_OUTSIDE_RULE_POPULATION"}
    assert pt.assessability is AS and pt.cause is None and pt.open_gap_classes == ()
    assert (pt.counts.assessed, pt.counts.partially_assessed, pt.counts.not_assessed, pt.counts.not_applicable) == (2, 0, 0, 2)
    only = surface(compose(_pt(), "AM_PT"), "rule.pass_through_review")                   # SRC->VF y VF->AM: no hay NINGUNA transformacion interna
    assert only.assessability is NAP and only.not_applicable_reason == "EDGE_OUTSIDE_RULE_POPULATION" and only.result is A.RuleResult.NOT_APPLICABLE
    assert only.counts.applicable == 0                                                    # sale del denominador de cobertura


def test_rederivation_needs_a_visible_query_in_every_artifact():
    comp = compose(_redo(), "AM_RD")
    r = surface(comp, "rule.cross_layer_rederivation")
    assert r.finding_count == 1 and r.assessability is AS and r.result is A.RuleResult.PATTERN_FOUND
    ents = _redo()
    ents["VR_RD"].has_query = False
    r2 = surface(compose(ents, "AM_RD"), "rule.cross_layer_rederivation")
    assert r2.assessability is PA and r2.cause is m.Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT


def test_role_layer_divergence_applies_only_to_names_that_encode_a_layer():
    comp = compose(_rl(), "4AM_RL")
    r = surface(comp, "rule.role_layer_divergence")
    reasons = {u.subject: u.not_applicable_reason for u in r.units if u.assessability is NAP}
    assert reasons["VD_NODIGIT"] == "NAME_DOES_NOT_ENCODE_LAYER" and reasons["SAP.TIME.X"] == "SYSTEM_ARTIFACT"
    assert r.assessability is AS and r.finding_count == 3 and r.result is A.RuleResult.PATTERN_FOUND


def test_an_empty_rule_population_is_an_assessed_absence_not_a_gap():
    comp = compose(_pt(), "AM_PT")                                                      # ningun artefacto de composicion en la clausura
    flat = surface(comp, "rule.flat_central_assembly_review")
    assert flat.units == () and flat.assessability is AS and flat.finding_count == 0 and flat.result is A.RuleResult.ASSESSED_ABSENCE


def test_a_remote_leaf_without_its_contract_fact_is_not_evaluable_for_the_raw_table_rule():
    ents = _flat(2)
    facts = [f for f in all_facts(ents, "AM_FL") if f.fact_id != "source.contract"]
    raw = surface(compose(ents, "AM_FL", facts=facts), "rule.direct_raw_table_access")
    assert raw.assessability is NA_ and raw.cause is m.Cause.NOT_IN_SNAPSHOT and raw.result is A.RuleResult.ABSENCE_NOT_ESTABLISHED


def test_layer_boundary_needs_the_first_consumers_of_each_remote_leaf():
    ents = _lb()
    facts = [f for f in all_facts(ents, "AM_LB") if f.fact_id != "ingestion.first_consumers"]
    lb = surface(compose(ents, "AM_LB", facts=facts), "rule.layer_boundary_review")
    remote_units = [u for u in lb.units if u.assessability is not NAP]
    assert any(u.cause is m.Cause.NOT_IN_SNAPSHOT for u in remote_units)
    assert surface(compose(ents, "AM_LB"), "rule.layer_boundary_review").assessability in (AS, PA)


def test_a_declared_dimension_consumer_is_not_applicable_for_the_pass_through_rule():
    ents = _pt_internal()
    ents["VF_PT"].modeling_pattern = "ANALYTICAL_CUBE DIMENSION"
    comp = compose(ents, "AM_PT")
    pt = surface(comp, "rule.pass_through_review")
    assert next(u for u in pt.units if u.subject == "VR_PT->VF_PT").not_applicable_reason == "CONSUMER_IS_DECLARED_DIMENSION"
    assert not [f for f in comp.findings if f.signal_id == "PASS_THROUGH_REVIEW" and f.subjects == ("VR_PT", "VF_PT")]


def test_every_core_area_of_a_fully_extracted_closure_is_assessed_and_context_only_never_degrades_an_area():
    comp = compose(m4_entities(), "AM_X")
    cards = {c.area: c for c in comp.areas}
    assert cards[m.Area.NAMING].assessability is AS and cards[m.Area.ANALYTIC_MODEL].assessability is AS      # convention_context (CONTEXT_ONLY) no la degrada
    assert cards[m.Area.HARMONIZATION].assessability is AS                                                      # reuse_context (CONTEXT_ONLY) tampoco


# ── Q1 / macro-dimension ─────────────────────────────────────────────────────
def test_local_boundary_leaves_make_source_completely_not_assessed_never_not_applicable():
    comp = compose(_local_chain(), "AM_C")
    src = next(c for c in comp.areas if c.area is m.Area.SOURCE_EXTRACTION)
    assert src.assessability is NA_ and src.cause in (m.Cause.PROVENANCE_UNKNOWN, m.Cause.PLANE_BOUNDARY)
    macro = {x.macro_dimension: x.state for x in comp.coverage.macro_dimensions}
    assert macro[m.MacroDimension.SOURCE_EXTRACTION] is m.MacroDimensionState.COMPLETELY_NOT_ASSESSED
    assert macro[m.MacroDimension.NAMING] is m.MacroDimensionState.HAS_ASSESSED_EVIDENCE
    assert macro[m.MacroDimension.ARCHITECTURE_MODELING] is m.MacroDimensionState.HAS_ASSESSED_EVIDENCE      # tiene una brecha C2 pero NO esta completamente sin evaluar
    assert surface(comp, "source.connection_consistency").not_applicable_reason == "NO_REMOTE_SOURCE_IN_SCOPE"
    assert surface(comp, "source.contract").not_applicable_reason == "NO_UNITS_IN_SCOPE"
    assert surface(comp, "source.leaf_contract_status").assessability is NA_


def test_an_area_without_any_extracted_facts_is_not_assessed_not_in_snapshot_never_assumed_fine():
    ents = m4_entities()
    comp = compose(ents, "AM_X", facts=[f for f in all_facts(ents, "AM_X") if f.fact_id.startswith("naming.")])
    cards = {c.area: c for c in comp.areas}
    assert cards[m.Area.NAMING].assessability in (AS, PA)
    for a in (m.Area.SOURCE_EXTRACTION, m.Area.INGESTION, m.Area.PROPAGATION, m.Area.HARMONIZATION, m.Area.ANALYTIC_MODEL):
        assert cards[a].assessability is NA_ and cards[a].cause is m.Cause.NOT_IN_SNAPSHOT
    per_unit = surface(comp, "source.contract")
    assert per_unit.assessability is NA_ and per_unit.cause is m.Cause.NOT_IN_SNAPSHOT and per_unit.not_applicable_reason is None


# ── scope no aplicable ───────────────────────────────────────────────────────
def test_scope_without_a_terminal_analytic_model_composes_everything_as_not_applicable():
    ents = pool(ent("V1", fields=[f4("A", "A")]), ent("V2", up=["V1"], has_query=True, fields=[f4("A", "A")]))
    u = AssessmentUniverse.from_entities(ents)
    comp = A.compose_assessment(u.scope_for(None), [])
    assert all(c.assessability is NAP and c.applicability.reason is m.ApplicabilityReason.NO_TERMINAL_ANALYTIC_MODEL for c in comp.areas)
    assert comp.coverage.overall.applicable == 0 and comp.coverage.overall.ratio_strict is None and comp.coverage.overall.ratio_inclusive is None
    assert {x.state for x in comp.coverage.macro_dimensions} == {m.MacroDimensionState.NOT_APPLICABLE}
    assert comp.surfaces == () and comp.confidence_factors == () and comp.confidence.level is m.ConfidenceLevel.LOW
    assert "not applicable" in comp.confidence.drivers[0].detail


# ── confianza ────────────────────────────────────────────────────────────────
def test_roles_context_derived_views_processing_and_optional_evidence_are_excluded_by_design():
    role = {s.surface_id: s.role for s in A.SURFACE_REGISTRY}
    assert {k for k, v in role.items() if v is A.SurfaceRole.CONTEXT_ONLY} == {"naming.convention_context", "harmonization.reuse_context"}
    assert {k for k, v in role.items() if v is A.SurfaceRole.CROSS_PLANE_COMPLEMENT} == {
        "ingestion.dataflow_boundary", "ingestion.processing_context_inventory", "ingestion.processing_reference", "source.replication_mode"}
    assert {k for k, v in role.items() if v is A.SurfaceRole.OPTIONAL_DEEPER_EVIDENCE} == {"propagation.derivation_semantics"}
    assert {k for k, v in role.items() if v is A.SurfaceRole.DERIVED_VIEW} == {
        "source.family_distribution", "naming.artifact_name_populations", "naming.field_vocabulary_summary",
        "naming.same_normalized_label_multiple_technical_names", "am.closure_composition"}
    # C2 NO es automaticamente "evidencia requerida faltante": las dos brechas C2 permanentes ya no son superficies REQUERIDAS
    assert role["propagation.derivation_semantics"] is not A.SurfaceRole.REQUIRED and role["source.replication_mode"] is not A.SurfaceRole.REQUIRED
    from collections import Counter
    assert Counter((s.kind.value, s.role.value) for s in A.SURFACE_REGISTRY) == {
        ("FACT_EVIDENCE", "REQUIRED"): 29, ("RULE_EVALUATION", "REQUIRED"): 11, ("FACT_EVIDENCE", "DERIVED_VIEW"): 5,
        ("FACT_EVIDENCE", "CROSS_PLANE_COMPLEMENT"): 4, ("FACT_EVIDENCE", "CONTEXT_ONLY"): 2, ("FACT_EVIDENCE", "OPTIONAL_DEEPER_EVIDENCE"): 1}


def test_permanent_optional_c2_surfaces_do_not_reduce_required_coverage_but_stay_visible_as_limitations(tmp_path):
    ents = m2_entities()
    ev_files = SupplementaryEvidence.from_files(write_pool_files(tmp_path), root=tmp_path)
    facts = all_facts(ents, "AM_P", ev_files)
    full = compose(ents, "AM_P", facts=facts)
    optional = {"propagation.derivation_semantics", "source.replication_mode"}
    without = compose(ents, "AM_P", facts=[f for f in facts if f.fact_id not in optional])
    assert without.coverage == full.coverage                                             # su ausencia/indisponibilidad no mueve la cobertura requerida
    deriv, repl = surface(full, "propagation.derivation_semantics"), surface(full, "source.replication_mode")
    assert deriv.assessability is NA_ and deriv.cause is m.Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT      # sigue siendo C2 / NOT OBSERVABLE
    assert repl.assessability is NA_ and repl.role is A.SurfaceRole.CROSS_PLANE_COMPLEMENT
    cards = {c.area: c for c in full.areas}
    assert m.Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT in cards[m.Area.PROPAGATION].not_observable       # la limitacion se conserva a la vista del area
    assert m.Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT in cards[m.Area.SOURCE_EXTRACTION].not_observable


def test_a_required_unavailable_surface_does_reduce_coverage_while_context_required_does_not():
    ents = m4_entities()
    facts = all_facts(ents, "AM_X")
    full = compose(ents, "AM_X", facts=facts)
    missing = compose(ents, "AM_X", facts=[f for f in facts if f.fact_id != "am.association_targets"])     # superficie REQUERIDA no disponible
    gap = surface(missing, "am.association_targets")
    assert gap.role is A.SurfaceRole.REQUIRED and gap.assessability is NA_ and gap.cause is m.Cause.NOT_IN_SNAPSHOT
    assert missing.coverage.overall.not_assessed == full.coverage.overall.not_assessed + 1
    assert missing.coverage.overall.ratio_strict < full.coverage.overall.ratio_strict
    ctx_only = compose(ents, "AM_X", facts=[f for f in facts if f.fact_id != "naming.convention_context"])
    assert ctx_only.coverage == full.coverage                                             # CONTEXT_REQUIRED != evidencia requerida faltante


# ── confianza: solo FIABILIDAD EPISTEMICA; cobertura y contexto son ejes distintos ──
def _ident_pool(heuristic: bool):
    names = ["1TR_R1", "1TR_R2", "1TR_R3", "1TR_R4"] if heuristic else ["R1", "R2", "R3", "R4"]
    leaves = [ent(n, remote_entity=f"ABAP_CDS.R{i + 1}", remote_connection="C1", fields=[f4("K", "K")]) for i, n in enumerate(names)]
    vf = ent("3VF_I", up=names, pattern="ANALYTICAL_FACT", has_query=True, fields=[f4("K", "K")])
    am = with_assocs(ent("4AM_I", up=["3VF_I"], cube=True, has_query=True, fields=[f4("K", "K")]), [("_a", names[0])])
    return pool(am, vf, *leaves)


def _factor(comp, name):
    return next(f for f in comp.confidence_factors if f.name == name)


def test_aggregate_confidence_is_the_minimum_of_epistemic_contributors_only():
    comp = compose(m4_entities(), "AM_X")
    assert [d.name for d in comp.confidence.drivers] == [m.ConfidenceDriverName.EPISTEMIC_RELIABILITY]
    # el contrato no ofrece otro driver: cobertura, contexto abierto y plano cruzado no pueden expresarse como driver de confianza
    assert {d.value for d in m.ConfidenceDriverName} == {"EPISTEMIC_RELIABILITY"} and not hasattr(m.ConfidenceDriverName, "EVIDENCE_MIX")
    live = [f for f in comp.confidence_factors if f.contributes_to_confidence]
    assert live and all(f.axis is A.ConfidenceAxis.EPISTEMIC_RELIABILITY and f.level is not None for f in live)
    assert {f.name for f in live} == {"non_heuristic_identity", "resolved_provenance", "complete_rule_inputs"}
    order = [m.ConfidenceLevel.LOW, m.ConfidenceLevel.MEDIUM, m.ConfidenceLevel.HIGH]
    assert comp.confidence.level == min((f.level for f in live), key=order.index)
    driver = comp.confidence.drivers[0]
    assert driver.level == comp.confidence.level                                          # el unico driver ES la confianza agregada
    for f in live:
        assert f"{f.name}={f.value} {f.level.value}" in driver.detail                      # y explica sus componentes
    for f in comp.confidence_factors:
        if not f.contributes_to_confidence:
            assert f.level is None                                                       # un diagnostico no tiene nivel ordinal que confundir con confianza


def test_factors_are_classified_into_the_three_axes_and_only_epistemic_ones_contribute():
    comp = compose(m4_entities(), "AM_X")
    axis = {f.name: f.axis for f in comp.confidence_factors}
    assert axis["strict_coverage"] is A.ConfidenceAxis.COMPLETENESS and axis["inclusive_coverage"] is A.ConfidenceAxis.COMPLETENESS
    assert axis["open_evidence_gap_surfaces"] is A.ConfidenceAxis.COMPLETENESS
    assert axis["open_context_questions"] is A.ConfidenceAxis.CONTEXTUALITY and axis["open_context_occurrences"] is A.ConfidenceAxis.CONTEXTUALITY
    assert axis["processing_evidence_attached"] is A.ConfidenceAxis.CROSS_PLANE
    assert axis["directly_observed_surfaces"] is A.ConfidenceAxis.EPISTEMIC_RELIABILITY
    assert not _factor(comp, "directly_observed_surfaces").contributes_to_confidence                  # A-vs-B no es una marca de fiabilidad
    contributing_axes = {f.axis for f in comp.confidence_factors if f.contributes_to_confidence}
    assert contributing_axes == {A.ConfidenceAxis.EPISTEMIC_RELIABILITY}
    assert _factor(comp, "strict_coverage").value == comp.coverage.overall.ratio_strict               # el diagnostico refleja la cobertura sin gobernarla


def test_strict_coverage_cannot_alter_confidence():
    ents = _ident_pool(False)
    facts = all_facts(ents, "4AM_I")
    full = compose(ents, "4AM_I", facts=facts)
    less = compose(ents, "4AM_I", facts=[f for f in facts if f.fact_id not in ("am.association_targets", "ingestion.field_retention")])
    assert less.coverage.overall.ratio_strict < full.coverage.overall.ratio_strict                   # la cobertura SI cambia...
    assert less.confidence == full.confidence                                                        # ...la confianza NO (completitud ya esta en COVERAGE)
    assert _factor(less, "strict_coverage").value != _factor(full, "strict_coverage").value


def test_open_context_count_cannot_alter_confidence():
    ents = _ident_pool(False)
    facts = all_facts(ents, "4AM_I")
    ev = S.evaluate_signals(facts)
    extras = tuple(m.Signal(f"EXTRA_QUESTION_{i}", m.Area.NAMING, m.SignalKind.REVIEW, ("field",), (), ("not a defect",)) for i in range(9))
    many = S.SignalEvaluation(ev.ruleset_version, (*ev.signals, *extras), ev.observations, ev.review_items, ev.findings)
    base, more = compose(ents, "4AM_I", facts=facts, evaluation=ev), compose(ents, "4AM_I", facts=facts, evaluation=many)
    assert _factor(more, "open_context_questions").value == _factor(base, "open_context_questions").value + 9    # mas preguntas abiertas...
    assert more.confidence == base.confidence and more.coverage == base.coverage                                  # ...ni confianza ni cobertura cambian


def test_a_high_confidence_observation_can_coexist_with_an_open_context_interpretation(tmp_path):
    comp = compose(*_m2_with_files(tmp_path))
    raw = surface(comp, "rule.direct_raw_table_access")
    assert raw.finding_count == 2 and comp.observations                                  # observacion con metadata objetiva...
    assert all(o.resolution is m.Resolution.OPEN_CONTEXT_REQUIRED for o in comp.observations)       # ...cuya interpretacion sigue abierta
    assert _factor(comp, "open_context_questions").value >= 1 and not _factor(comp, "open_context_questions").contributes_to_confidence


def _m2_with_files(tmp_path):
    ents = m2_entities()
    return ents, "AM_P", SupplementaryEvidence.from_files(write_pool_files(tmp_path), root=tmp_path)


def test_evidence_reliability_does_alter_confidence():
    exact, heur = compose(_ident_pool(False), "4AM_I"), compose(_ident_pool(True), "4AM_I")
    assert _factor(exact, "non_heuristic_identity").level is m.ConfidenceLevel.HIGH
    assert _factor(heur, "non_heuristic_identity").level is m.ConfidenceLevel.LOW and _factor(heur, "non_heuristic_identity").value < 0.5
    assert exact.confidence.level is m.ConfidenceLevel.HIGH and heur.confidence.level is m.ConfidenceLevel.LOW
    assert exact.confidence.drivers[0].level is m.ConfidenceLevel.HIGH and heur.confidence.drivers[0].level is m.ConfidenceLevel.LOW    # driver = agregado
    assert heur.confidence.drivers[0].name is m.ConfidenceDriverName.EPISTEMIC_RELIABILITY
    assert exact.coverage.overall.ratio_strict == heur.coverage.overall.ratio_strict      # misma cobertura, distinta fiabilidad: ejes distintos


def test_unresolved_provenance_lowers_confidence_independently_of_coverage():
    ents = _ident_pool(False)
    for n in ("3VF_I", "4AM_I"):
        ents[n].has_query = False
        ents[n].repository_type = "DWC_LOCAL_TABLE"                                       # identidad UNKNOWN (tabla local sin query)
    comp = compose(ents, "4AM_I")
    f = _factor(comp, "resolved_provenance")
    assert f.value is not None and f.value < 1 and f.level is not m.ConfidenceLevel.HIGH
    assert comp.confidence.level is not m.ConfidenceLevel.HIGH


def test_partial_rule_inputs_lower_the_confidence_of_the_conclusions_reached(tmp_path):
    comp = compose(*_m2_with_files(tmp_path))                                              # hallazgos de tabla raw junto a hojas locales no observables
    f = _factor(comp, "complete_rule_inputs")
    concl = [s for s in comp.surfaces if s.kind is A.SurfaceKind.RULE_EVALUATION and s.result in (A.RuleResult.PATTERN_FOUND, A.RuleResult.ASSESSED_ABSENCE)]
    partial_found = [s for s in concl if s.assessability is not AS]
    assert partial_found and all(s.result is A.RuleResult.PATTERN_FOUND for s in partial_found)   # patron hallado con entradas parciales
    assert f.value == round(sum(1 for s in concl if s.assessability is AS) / len(concl), 6) and f.value < 1
    assert f.contributes_to_confidence and f.level is not None
    # una regla SIN conclusion (ausencia no establecida) no entra en este factor: eso es completitud, ya esta en COVERAGE
    assert all(s.result in (A.RuleResult.PATTERN_FOUND, A.RuleResult.ASSESSED_ABSENCE) for s in concl)


def test_cross_plane_evidence_is_neither_a_global_reward_nor_a_penalty(tmp_path):
    ents, am, ev_files = _m2_with_files(tmp_path)
    facts = all_facts(ents, am, ev_files)
    with_p = compose(ents, am, facts=facts)
    without_p = compose(ents, am, facts=[f for f in facts if f.plane is not m.Plane.PROCESSING])
    assert with_p.confidence == without_p.confidence and with_p.coverage == without_p.coverage
    fp, fn = _factor(with_p, "processing_evidence_attached"), _factor(without_p, "processing_evidence_attached")
    assert fp.value > fn.value == 0 and not fp.contributes_to_confidence and fp.axis is A.ConfidenceAxis.CROSS_PLANE
    assert "0 Modeling facts cite Processing evidence" in fp.detail                       # ningun hecho de Modeling depende explicitamente de ella
    assert surface(with_p, "source.leaf_contract_status").assessability is surface(without_p, "source.leaf_contract_status").assessability


def test_processing_facts_with_no_references_are_not_counted_as_attached_evidence():
    comp = compose(m4_entities(), "AM_X")
    assert _factor(comp, "processing_evidence_attached").value == 0


def test_confidence_cuts_are_provisional_versioned_and_only_for_reliable_share():
    assert set(A.CONFIDENCE_CUTS) == {"reliable_share"} and A.CONFIDENCE_RULESET == "confidence-cuts/v1"
    cuts = A.CONFIDENCE_CUTS["reliable_share"]
    for value, expected in ((None, "LOW"), (0.0, "LOW"), (0.49, "LOW"), (0.5, "MEDIUM"), (0.79, "MEDIUM"), (0.8, "HIGH"), (1.0, "HIGH")):
        assert A._level(value, cuts).value == expected


def test_confidence_without_any_reliability_marker_is_low_and_says_so():
    c = A._confidence((A.ConfidenceFactor(A.ConfidenceAxis.COMPLETENESS, "strict_coverage", 1.0, "x", False),), True)
    assert c.level is m.ConfidenceLevel.LOW and "no assessed evidence carries a reliability marker" in c.drivers[0].detail
    na = A._confidence((), False)
    assert na.level is m.ConfidenceLevel.LOW and "scope not applicable" in na.drivers[0].detail


def test_open_evidence_gaps_are_a_completeness_diagnostic_not_a_confidence_input():
    comp = compose(m4_entities(), "AM_X")
    req = [s for s in comp.surfaces if s.role is A.SurfaceRole.REQUIRED and s.assessability is not NAP]
    open_gap = [s for s in req if s.assessability is not AS and s.open_gap_classes]
    assert open_gap and all(set(s.open_gap_classes) <= {m.EvidenceClass.C1, m.EvidenceClass.C2, m.EvidenceClass.D} for s in open_gap)
    f = _factor(comp, "open_evidence_gap_surfaces")
    assert f.value == len(open_gap) and f.axis is A.ConfidenceAxis.COMPLETENESS and not f.contributes_to_confidence


# ── listo para M7 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ents_fn,am", [(m4_entities, "AM_X"), (m3_entities, "AM_M"), (_pt, "AM_PT"), (_rl, "4AM_RL"), (_lb, "AM_LB"), (_local_chain, "AM_C")])
def test_the_composition_builds_a_valid_modeling_profile_without_reinterpreting_facts(ents_fn, am):
    comp = compose(ents_fn(), am)
    profile = m.ModelingProfile(m.SCHEMA_VERSION, m.RULESET_VERSION, comp.scope, comp.areas, comp.observations, comp.review_required,
                                comp.coverage, comp.confidence)
    first = m.canonical_json(profile)
    assert first == m.canonical_json(m.ModelingProfile(m.SCHEMA_VERSION, m.RULESET_VERSION, comp.scope, comp.areas, comp.observations,
                                                       comp.review_required, comp.coverage, comp.confidence))
    assert '"ratio_strict"' in first and '"ratio_inclusive"' in first


def test_the_composition_exposes_everything_m7_needs_to_render():
    comp = compose(m4_entities(), "AM_X")
    assert comp.scope.terminal_am == "AM_X" and [c.area for c in comp.areas] == list(m.ASSESSED_AREAS)
    assert comp.surfaces and comp.observations is not None and comp.review_required and comp.findings
    assert comp.assessability_ruleset == A.ASSESSABILITY_RULESET == "assessability-rules/v1"
    assert comp.signals_ruleset == S.SIGNALS_RULESET and comp.confidence_ruleset == A.CONFIDENCE_RULESET
    rules = [s for s in comp.surfaces if s.kind is A.SurfaceKind.RULE_EVALUATION]
    assert len(rules) == 11 and all(s.result is not None and s.finding_count is not None for s in rules)
    for card in comp.areas:
        assert all(f.area is card.area for f in card.facts) and all(s.area is card.area for s in card.signals)


# ── propiedades comunes ──────────────────────────────────────────────────────
@pytest.mark.parametrize("ents_fn,am", [(m4_entities, "AM_X"), (_rl, "4AM_RL"), (_local_chain, "AM_C")])
def test_composition_is_deterministic_and_independent_of_fact_order(ents_fn, am):
    ents = ents_fn()
    facts = all_facts(ents, am)
    ref = compose(ents, am, facts=facts)
    rng = random.Random(7)
    for _ in range(3):
        shuffled = list(facts)
        rng.shuffle(shuffled)
        assert compose(ents, am, facts=shuffled) == ref


def test_no_scoring_vocabulary_in_the_composition_objects():
    comp = compose(m4_entities(), "AM_X")
    for s in comp.surfaces:
        assert not m.find_forbidden_tokens(s.surface_id)
    jsonable = m.to_jsonable(comp.coverage)
    keys = []

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                keys.append(k)
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(jsonable)
    walk(m.to_jsonable(comp.confidence))
    assert not [k for k in keys if m.find_forbidden_tokens(k)]
    assert not any(f.name in ("score", "band", "weight") for f in __import__("dataclasses").fields(A.SurfaceAssessment))


def test_assessability_module_reads_only_facts_signals_and_the_model():
    text = (PKG / "assessability.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mods.add(("." * node.level) + (node.module or ""))
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    assert mods <= {"__future__", "collections", "dataclasses", "enum", "typing", ".model", ".signals"}, mods
    for needle in ("open(", "read_text", "json.load", "legacy_bridge", "generate_catalog"):
        assert needle not in text, needle
