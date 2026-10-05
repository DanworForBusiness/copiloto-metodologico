"""M7 — documento PUBLICO del assessment (`profile.py`): contrato versionado, razones publicas, evidencia normalizada, agrupacion y ejes separados."""
from __future__ import annotations

import ast
import copy
import json
import random
import re
from pathlib import Path

import pytest

from tests._modeling_assessment_m4_fixtures import m4_entities
from tests._modeling_assessment_m7_fixtures import scenario_pools
from tests.test_modeling_assessment_assessability import _ident_pool, _local_chain
from tests.test_modeling_assessment_signals import all_facts
from tools.catalog.modeling_assessment import assessability as A
from tools.catalog.modeling_assessment import model as m
from tools.catalog.modeling_assessment import profile as P
from tools.catalog.modeling_assessment import signals as S
from tools.catalog.modeling_assessment.scope import AssessmentUniverse

PKG = Path(__file__).resolve().parent.parent / "tools" / "catalog" / "modeling_assessment"


def build(ents, am, evidence=None, *, facts=None, client="Acme"):
    u = AssessmentUniverse.from_entities(ents)
    facts = facts if facts is not None else all_facts(ents, am, evidence)
    comp = A.compose_assessment(u.scope_for(am, pool_signature="sig"), facts)
    return P.build_assessment(comp, facts, client=client), comp, facts


@pytest.fixture(scope="module")
def scenarios(tmp_path_factory):
    out = []
    for ents, am, ev in scenario_pools(tmp_path_factory.mktemp("m7pool")):
        doc, comp, facts = build(ents, am, ev)
        out.append((am, doc, comp, facts))
    return out


def _groups(doc):
    return [*doc["observations"], *doc["review_required"], *doc["descriptive_signals"]]


# ── contrato y version ───────────────────────────────────────────────────────
def test_every_scenario_document_satisfies_the_public_contract(scenarios):
    assert len(scenarios) >= 9
    for am, doc, _c, _f in scenarios:
        P.validate_assessment(doc)
        assert doc["schema_version"] == P.ASSESSMENT_SCHEMA_VERSION == "modeling_assessment.assessment/v1"
        assert doc["assessment_state"] == "ASSESSED" and doc["scope"]["terminal_analytic_model"] == am
        assert set(P.REQUIRED_TOP_LEVEL_KEYS) <= set(doc)
        assert doc["versions"]["reason_codes"] == P.REASON_REGISTRY_VERSION and doc["versions"]["finding_evidence"] == P.FINDING_EVIDENCE_VERSION
        assert doc["versions"]["signals"] == S.SIGNALS_RULESET and doc["versions"]["confidence"] == A.CONFIDENCE_RULESET


def test_the_scenarios_together_exercise_every_signal_of_the_registry(scenarios):
    seen = {g["id"] for _a, doc, _c, _f in scenarios for g in _groups(doc)}
    assert seen == {s.signal_id for s in S.SIGNAL_REGISTRY}


def test_validate_rejects_contract_violations(scenarios):
    doc = scenarios[0][1]
    bad = copy.deepcopy(doc)
    del bad["summary"]
    with pytest.raises(ValueError, match="faltan claves"):
        P.validate_assessment(bad)
    bad = copy.deepcopy(doc)
    bad["schema_version"] = "x/v9"
    with pytest.raises(ValueError, match="schema_version"):
        P.validate_assessment(bad)
    bad = copy.deepcopy(doc)
    bad["summary"]["quality_score"] = 3
    with pytest.raises(ValueError, match="vocabulario prohibido"):
        P.validate_assessment(bad)
    bad = copy.deepcopy(doc)
    g = _groups(bad)[0]
    g["occurrence_count"] += 1
    with pytest.raises(ValueError, match="occurrence_count"):
        P.validate_assessment(bad)
    bad = copy.deepcopy(doc)
    _groups(bad)[0]["occurrences"][0]["values"]["internal_leak"] = 1
    with pytest.raises(ValueError, match="fuera del mapa publico"):
        P.validate_assessment(bad)
    bad = copy.deepcopy(doc)
    bad["scope"]["reason"] = {"reason_code": "SOME_INTERNAL_STRING", "reason_summary": "x"}
    with pytest.raises(ValueError, match="registro publico"):
        P.validate_assessment(bad)


# ── determinismo ─────────────────────────────────────────────────────────────
def test_json_is_deterministic_and_independent_of_fact_order(tmp_path):
    ents, am = m4_entities(), "AM_X"
    facts = all_facts(ents, am)
    a = P.canonical_assessment_json(build(ents, am, facts=facts)[0])
    shuffled = list(facts)
    random.Random(7).shuffle(shuffled)
    b = P.canonical_assessment_json(build(m4_entities(), am, facts=shuffled)[0])
    assert a == b and a.endswith("\n") and json.loads(a)["schema_version"] == P.ASSESSMENT_SCHEMA_VERSION
    assert "NaN" not in a and "Infinity" not in a


def test_canonical_json_has_sorted_keys_at_every_level(scenarios):
    def check(pairs):
        keys = [k for k, _ in pairs]
        assert keys == sorted(keys), keys
        return dict(pairs)
    for _a, doc, _c, _f in scenarios[:3]:
        json.loads(P.canonical_assessment_json(doc), object_pairs_hook=check)


# ── registro de razones publicas ─────────────────────────────────────────────
def _na_reason_strings_in_assessability() -> set[str]:
    """Toda razon interna de NOT_APPLICABLE que M6 puede emitir: argumento de `Unit(..., NOT_APPLICABLE, None, "X")` o constante de `na_when`/`_no_remote`."""
    tree = ast.parse((PKG / "assessability.py").read_text(encoding="utf-8"))
    found = {"NO_UNITS_IN_SCOPE"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Unit" and len(node.args) >= 4:
            st, reason = node.args[1], node.args[3]
            if isinstance(st, ast.Attribute) and st.attr == "NOT_APPLICABLE" and isinstance(reason, ast.Constant) and isinstance(reason.value, str):
                found.add(reason.value)
        if isinstance(node, ast.FunctionDef) and node.name == "_no_remote":
            found |= {c.value for c in ast.walk(node) if isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value.isupper()}
    return found


def test_every_internal_not_applicable_reason_has_a_public_code():
    internal = _na_reason_strings_in_assessability()
    assert {"SYSTEM_ARTIFACT", "NO_REMOTE_SOURCE_IN_SCOPE", "CONSUMER_IS_DECLARED_DIMENSION", "NAME_DOES_NOT_ENCODE_LAYER", "DECLARED_DIMENSION",
            "NO_UNITS_IN_SCOPE"} <= internal
    internal |= {r.value for r in m.ApplicabilityReason}
    for reason in sorted(internal):
        out = P.public_reason(m.Assessability.NOT_APPLICABLE, None, reason)
        assert out["reason_code"] in P.PUBLIC_REASONS and out["reason_summary"] == P.PUBLIC_REASONS[out["reason_code"]]


def test_internal_to_public_mapping_is_pinned_exactly():
    """El mapeo es CONTRATO: cambiar un codigo publico (o cruzar dos) debe ser una decision explicita y visible."""
    NA = m.Assessability.NOT_APPLICABLE
    expected_na = {"NO_TERMINAL_ANALYTIC_MODEL": "NO_TERMINAL_ANALYTIC_MODEL", "NO_REMOTE_SOURCE_IN_SCOPE": "NO_REMOTE_SOURCE_IN_SCOPE",
                   "NO_UNITS_IN_SCOPE": "NO_UNITS_IN_SCOPE", "SYSTEM_ARTIFACT": "SYSTEM_PROVIDED_ARTIFACT",
                   "CONSUMER_IS_DECLARED_DIMENSION": "CONSUMER_IS_DECLARED_DIMENSION", "NAME_DOES_NOT_ENCODE_LAYER": "NAME_DOES_NOT_ENCODE_LAYER",
                   "DECLARED_DIMENSION": "DECLARED_DIMENSION_ARTIFACT", "NO_OTHER_TERMINAL_ANALYTIC_MODEL": "NO_PEER_ANALYTIC_MODEL",
                   "INTERNAL_STATIC_OBJECT": "INTERNAL_STATIC_OBJECT"}
    assert {k: P.public_reason(NA, None, k)["reason_code"] for k in expected_na} == expected_na
    assert P.public_reason(NA, None, None)["reason_code"] == "NO_UNITS_IN_SCOPE"
    expected_cause = {"NOT_OBSERVABLE_IN_STANDARD_EXPORT": "NOT_OBSERVABLE_IN_STANDARD_EXPORT", "NOT_IN_SNAPSHOT": "SUPPLEMENTARY_EVIDENCE_NOT_PROVIDED",
                      "CLASSIFIER_RULE_GAP": "RULE_DOES_NOT_EVALUATE_UNIT", "PLANE_BOUNDARY": "PROCESSING_PLANE_BOUNDARY",
                      "C1_NOT_YET_PARSED": "EVIDENCE_NOT_YET_PARSED", "C2_EXPERT_ONLY": "EXPERT_ONLY_EVIDENCE", "CONTEXT_REQUIRED": "CONTEXT_REQUIRED",
                      "PROVENANCE_UNKNOWN": "SOURCE_PROVENANCE_NOT_OBSERVABLE", "COLLISION_OR_LOAD_ERROR": "POOL_LOAD_ERROR",
                      "CONTRADICTORY_EVIDENCE": "CONTRADICTORY_EVIDENCE"}
    assert {c.value: P.public_reason(m.Assessability.NOT_ASSESSED, c, None)["reason_code"] for c in m.Cause} == expected_cause


def test_every_cause_has_a_public_code_and_unmapped_reasons_fail_loudly():
    for cause in m.Cause:
        for state in (m.Assessability.NOT_ASSESSED, m.Assessability.PARTIALLY_ASSESSED, m.Assessability.INDETERMINATE):
            assert P.public_reason(state, cause, None)["reason_code"] in P.PUBLIC_REASONS
    assert P.public_reason(m.Assessability.NOT_ASSESSED, None, None)["reason_code"] == "REQUIRED_EVIDENCE_UNAVAILABLE"
    assert P.public_reason(m.Assessability.ASSESSED, None, None) is None
    with pytest.raises(P.UnmappedReasonError):
        P.public_reason(m.Assessability.NOT_APPLICABLE, None, "SOME_FUTURE_INTERNAL_REASON")


def test_registry_is_versioned_and_well_formed():
    assert P.REASON_REGISTRY_VERSION == "reason-codes/v1"
    for code, summary in P.PUBLIC_REASONS.items():
        assert re.fullmatch(r"[A-Z][A-Z_]+", code) and summary.endswith(".") and len(summary) > 20
    assert {"NO_TERMINAL_ANALYTIC_MODEL", "NO_REMOTE_SOURCE_IN_SCOPE", "NO_UNITS_IN_SCOPE", "REQUIRED_EVIDENCE_UNAVAILABLE"} <= set(P.PUBLIC_REASONS)


def test_documents_only_carry_public_reason_codes_never_internal_strings(scenarios):
    codes = set()

    def walk(v):
        if isinstance(v, dict):
            if "reason_code" in v:
                codes.add(v["reason_code"])
                assert {"reason_code", "reason_summary"} <= set(v) and v["reason_summary"] == P.PUBLIC_REASONS[v["reason_code"]]
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    for _a, doc, _c, _f in scenarios:
        walk(doc)
    assert codes and codes <= set(P.PUBLIC_REASONS)
    text = json.dumps([d for _a, d, _c, _f in scenarios])
    for internal in ("COLLISION_OR_LOAD_ERROR", "CLASSIFIER_RULE_GAP", "C1_NOT_YET_PARSED", "C2_EXPERT_ONLY", "NOT_IN_SNAPSHOT", "PROVENANCE_UNKNOWN",
                     "SYSTEM_ARTIFACT", "PLANE_BOUNDARY", "NO_OTHER_TERMINAL_ANALYTIC_MODEL", "DECLARED_DIMENSION"):
        assert text.count(f'"{internal}"') == 0, internal                           # ni como causa cruda ni como razon interna (valor JSON exacto)


# ── evidencia normalizada de los hallazgos ───────────────────────────────────
def test_value_map_covers_exactly_the_signal_registry_and_uses_clean_public_keys():
    assert set(P._VALUE_MAP) == {s.signal_id for s in S.SIGNAL_REGISTRY} == set(P.DISPLAY)
    for sid, keys in P.PUBLIC_VALUE_KEYS.items():
        assert len(keys) == len(set(keys)) and keys
        assert not any(m.find_forbidden_tokens(k) for k in keys), sid


def test_no_internal_finding_detail_key_is_silently_dropped_or_leaked(scenarios):
    for _a, doc, comp, _f in scenarios:
        by_signal = {}
        for f in comp.findings:
            by_signal.setdefault(f.signal_id, []).append(f)
        for sid, findings in by_signal.items():
            mapped_internal = {internal for _p, internal, _t in P._VALUE_MAP[sid]}
            for f in findings:
                assert set(f.detail) <= mapped_internal, (sid, sorted(set(f.detail) - mapped_internal))   # una clave nueva obliga a decidir si es publica
    for _a, doc, _c, _f in scenarios:
        for g in _groups(doc):
            for o in g["occurrences"]:
                assert set(o["values"]) <= set(P.PUBLIC_VALUE_KEYS[g["id"]])


def test_normalizer_whitelists_and_does_not_passthrough_unmapped_keys():
    out = P.normalize_finding_values("LABEL_NOT_ENRICHING", {"artifact": "X", "n_fields": 3, "label_equals_field": 3, "surprise_internal_key": {"a": 1}})
    assert out == {"artifact": "X", "field_count": 3, "fields_with_label_equal_to_name": 3}
    assert "surprise_internal_key" not in out
    f = P.normalize_finding_values("SOURCE_FILTER_PRESENT", {"where": [{"ref": ["L"]}, "=", {"val": "S"}]})
    assert f == {"filter_expression_json": '[{"ref":["L"]},"=",{"val":"S"}]'}                  # estructura libre normalizada a texto canonico


def test_serialised_documents_do_not_contain_finding_detail_or_internal_names(scenarios):
    keys = []
    for _a, doc, _c, _f in scenarios:
        P._walk_keys(doc, keys)
    assert "detail" not in keys and "fact_refs" not in keys and "subjects" not in keys
    for internal in ("transitive_lineage_dependents_in_scope", "name_encoded_layer", "provider_inferred_layer", "n_associations", "technical_names_raw"):
        assert internal not in keys


# ── agrupacion con ocurrencias preservadas ───────────────────────────────────
def test_groups_preserve_every_occurrence_and_their_counts_match_the_engine(scenarios):
    for _a, doc, comp, facts in scenarios:
        engine = {}
        for f in comp.findings:
            engine.setdefault(f.signal_id, []).append(f)
        assert {g["id"] for g in _groups(doc)} == set(engine)
        known_facts = {f["fact"] for f in doc["fact_index"]}
        for g in _groups(doc):
            fs = engine[g["id"]]
            assert g["occurrence_count"] == len(g["occurrences"]) == len(fs)
            assert sorted(o["affected_artifacts"] for o in g["occurrences"]) == sorted(list(f.subjects) for f in fs)
            assert g["affected_artifacts"] == sorted({a for f in fs for a in f.subjects})
            ids = [o["occurrence_id"] for o in g["occurrences"]]
            assert ids == [f"{g['id']}-{i:04d}" for i in range(1, len(ids) + 1)]
            for o in g["occurrences"]:
                assert o["evidence_facts"] and set(o["evidence_facts"]) <= known_facts
        assert sum(g["occurrence_count"] for g in doc["observations"]) == len(comp.observations)
        assert sum(g["occurrence_count"] for g in doc["review_required"]) == len(comp.review_required)


def test_repeated_findings_collapse_into_one_group_per_signal(scenarios):
    multi = [(a, g) for a, doc, _c, _f in scenarios for g in _groups(doc) if g["occurrence_count"] > 1]
    assert multi
    for _a, doc, _c, _f in scenarios:
        ids = [g["id"] for g in _groups(doc)]
        assert len(ids) == len(set(ids))


def test_kinds_context_and_resolution_follow_the_signal_registry(scenarios):
    kinds = {s.signal_id: s for s in S.SIGNAL_REGISTRY}
    for _a, doc, _c, _f in scenarios:
        for g in doc["observations"]:
            assert kinds[g["id"]].kind is m.SignalKind.OBSERVATION and g["resolution"] == "OPEN_CONTEXT_REQUIRED"
        for g in doc["review_required"]:
            assert kinds[g["id"]].kind is m.SignalKind.REVIEW and "resolution" not in g
        for g in doc["descriptive_signals"]:
            assert kinds[g["id"]].kind is m.SignalKind.DESCRIPTIVE and "context_required" not in g and "legitimate_reasons" not in g
        for g in (*doc["observations"], *doc["review_required"]):
            assert g["context_required"]["question"] == P.SIGNAL_QUESTIONS[g["id"]] and g["context_required"]["context_needed"] == P.CONTEXT_NEEDED[g["id"]]
            assert g["interpretation_limits"] == P.public_limits(kinds[g["id"]])


def test_every_signal_states_why_it_is_shown(scenarios):
    assert set(P.WHY_SHOWN) == set(P.DISPLAY)
    assert all(v.startswith("Se muestra ") and v.endswith(".") for v in P.WHY_SHOWN.values())
    for _a, doc, _c, _f in scenarios:
        assert all(g["why_shown"] == P.WHY_SHOWN[g["id"]] for g in _groups(doc))


def test_limitations_have_a_readable_title_and_name_their_affected_units(scenarios):
    seen_units = False
    for _a, doc, comp, _f in scenarios:
        surfaces = {s.surface_id: s for s in comp.surfaces}
        for lim in doc["evidence_limitations"]:
            s = surfaces[lim["surface_id"]]
            assert lim["title"] and lim["title"] != lim["surface_id"]
            expected = sorted(u.subject for u in s.units if u.assessability not in (m.Assessability.ASSESSED, m.Assessability.NOT_APPLICABLE))
            assert lim["affected_units"] == expected
            seen_units |= bool(expected)
            if lim["surface_id"].startswith("rule."):
                assert lim["title"].startswith("Regla: ")
    assert seen_units


def test_every_context_signal_has_a_human_question():
    ctx = {s.signal_id for s in S.SIGNAL_REGISTRY if s.kind is not m.SignalKind.DESCRIPTIVE}
    assert set(P.SIGNAL_QUESTIONS) == ctx
    assert all(q.startswith("¿") and q.endswith("?") for q in P.SIGNAL_QUESTIONS.values())
    emitted = set()
    for f in all_facts(m4_entities(), "AM_X"):
        if f.fact_id == "naming.convention_context":
            emitted |= set(f.value["open_context_questions"])
    assert emitted and emitted <= set(P.CONTEXT_QUESTION_TEXT) and all(q.startswith("¿") and q.endswith("?") for q in P.CONTEXT_QUESTION_TEXT.values())


# ── vocabulario: sin score, banda ni ranking ─────────────────────────────────
def test_documents_have_no_maturity_score_band_or_ranking(scenarios):
    banned_text = re.compile(r"\b(score|scores|scoring|band|bands|weight|weights|penalty|penalties|rating|ranking|PLENO|BUENO|PARCIAL|grade|maturity|"
                             r"puntaje|puntajes|puntuaci[oó]n|banda|bandas|penalizaci[oó]n|penalidad|ponderaci[oó]n|madurez)\b", re.I)
    for _a, doc, _c, _f in scenarios:
        keys = []
        P._walk_keys(doc, keys)
        assert not [k for k in keys if m.find_forbidden_tokens(k)]
        assert not banned_text.search(P.canonical_assessment_json(doc))
        assert re.search(r"\"(overall|dimension)_?\w*\": *[1-5]\b", P.canonical_assessment_json(doc)) is None


# ── tres ejes separados ──────────────────────────────────────────────────────
def test_coverage_confidence_and_context_are_three_separate_blocks(scenarios):
    for _a, doc, comp, _f in scenarios:
        s = doc["summary"]
        assert {"coverage", "confidence", "context_required"} <= set(s)
        assert s["coverage"]["strict"] == pytest.approx(comp.coverage.overall.ratio_strict, abs=1e-4)
        assert s["coverage"]["inclusive"] == pytest.approx(comp.coverage.overall.ratio_inclusive, abs=1e-4)
        assert s["confidence"]["level"] == comp.confidence.level.value and s["confidence"]["driver"] == "EPISTEMIC_RELIABILITY"
        assert "strict" not in s["confidence"] and "open_questions" not in s["confidence"] and "level" not in s["coverage"]
        ids = {c["id"] for c in s["confidence"]["components"]}
        assert ids <= {"non_heuristic_identity", "resolved_provenance", "complete_rule_inputs"} and ids
        assert len({e["id"] for e in s["confidence"]["excluded_from_confidence"]}) == len(s["confidence"]["excluded_from_confidence"])    # sin duplicados
        excluded = {e["id"]: e for e in s["confidence"]["excluded_from_confidence"]}
        assert excluded["strict_coverage"]["axis"] == "COMPLETENESS" and excluded["open_context_questions"]["axis"] == "CONTEXTUALITY"
        assert excluded["open_context_questions"]["value"] is None and excluded["open_context_questions"]["reported_in"] == "summary.context_required"
        assert not ({c["id"] for c in s["confidence"]["components"]} & set(excluded))
        q = s["context_required"]
        assert q["open_questions"] == len(q["questions"]) and q["occurrences"] == len(comp.observations) + len(comp.review_required)


def test_context_questions_come_from_signal_groups_and_convention_context():
    doc, comp, _f = build(m4_entities(), "AM_X")
    qs = doc["summary"]["context_required"]["questions"]
    origins = {q["origin"] for q in qs}
    assert "CONTEXT_ONLY" in origins and ("REVIEW" in origins or "OBSERVATION" in origins)
    assert all(q["question"].endswith("?") for q in qs)
    for g in (*doc["observations"], *doc["review_required"]):
        assert any(q["id"] == g["id"] and q["occurrence_count"] == g["occurrence_count"] for q in qs)


def test_confidence_main_limitation_is_human_and_absent_when_every_component_is_high():
    exact, _c, _f = build(_ident_pool(False), "4AM_I")
    heur, _c2, _f2 = build(_ident_pool(True), "4AM_I")
    assert exact["summary"]["confidence"]["level"] == "HIGH" and exact["summary"]["confidence"]["main_limitation"] is None
    h = heur["summary"]["confidence"]
    assert h["level"] == "LOW" and h["main_limitation"]["component"] == "non_heuristic_identity"
    assert "heurística" in h["main_limitation"]["summary"] and not re.search(r"\d\.\d", h["main_limitation"]["summary"])
    assert exact["summary"]["coverage"]["strict"] == heur["summary"]["coverage"]["strict"]       # misma cobertura, distinta confianza


def test_main_limitation_is_the_component_with_the_lowest_level_then_the_lowest_value():
    import dataclasses
    doc0, comp, _f = build(m4_entities(), "AM_X")
    E = A.ConfidenceAxis.EPISTEMIC_RELIABILITY
    lvl = m.ConfidenceLevel

    def limitation(*triples):
        factors = tuple(A.ConfidenceFactor(E, n, v, "d", True, lv) for n, v, lv in triples)
        return P._confidence_json(dataclasses.replace(comp, confidence_factors=factors))["main_limitation"]
    # CASE 2 real: dos componentes MEDIUM; la limitacion es la de valor mas bajo (no la alfabeticamente primera)
    assert limitation(("complete_rule_inputs", 0.64, lvl.MEDIUM), ("non_heuristic_identity", 0.62, lvl.MEDIUM), ("resolved_provenance", 0.9, lvl.HIGH))["component"] == "non_heuristic_identity"
    assert limitation(("complete_rule_inputs", 0.62, lvl.MEDIUM), ("non_heuristic_identity", 0.64, lvl.MEDIUM), ("resolved_provenance", 0.9, lvl.HIGH))["component"] == "complete_rule_inputs"
    assert limitation(("complete_rule_inputs", 0.7, lvl.MEDIUM), ("non_heuristic_identity", 0.3, lvl.LOW), ("resolved_provenance", 0.9, lvl.HIGH))["component"] == "non_heuristic_identity"   # el nivel manda
    assert limitation(("complete_rule_inputs", 0.7, lvl.MEDIUM), ("non_heuristic_identity", 0.7, lvl.MEDIUM), ("resolved_provenance", 0.9, lvl.HIGH))["component"] == "complete_rule_inputs"    # empate total: nombre
    assert limitation(("complete_rule_inputs", 0.9, lvl.HIGH), ("non_heuristic_identity", 0.95, lvl.HIGH)) is None


def test_context_and_coverage_changes_never_move_the_confidence_block(scenarios):
    ents, am = m4_entities(), "AM_X"
    base, comp, facts = build(ents, am)
    extra = [f for f in facts if f.fact_id != "naming.convention_context"]
    other, _c, _f = build(m4_entities(), am, facts=extra)
    assert base["summary"]["confidence"]["level"] == other["summary"]["confidence"]["level"]
    assert base["summary"]["confidence"]["components"] == other["summary"]["confidence"]["components"]


# ── ausencia evaluada != ausencia no establecida ─────────────────────────────
def test_rule_results_distinguish_assessed_absence_from_absence_not_established(scenarios):
    seen = set()
    for _a, doc, _c, _f in scenarios + [("AM_C", build(_local_chain(), "AM_C")[0], None, None)]:
        for r in doc["rule_results"]:
            seen.add(r["result"])
            assert r["title"] == P.DISPLAY[r["rule"]][0]
            if r["result"] == "ABSENCE_NOT_ESTABLISHED":
                assert r["finding_count"] == 0 and r["reason"] is not None and r["assessability"] != "ASSESSED"
            if r["result"] == "ASSESSED_ABSENCE":
                assert r["finding_count"] == 0 and r["assessability"] == "ASSESSED" and r["reason"] is None
            if r["result"] == "PATTERN_FOUND":
                assert r["finding_count"] > 0
    assert {"PATTERN_FOUND", "ASSESSED_ABSENCE", "ABSENCE_NOT_ESTABLISHED"} <= seen


def test_a_client_whose_whole_source_area_is_not_assessed_says_so_in_the_macro_dimensions():
    doc, _c, _f = build(_local_chain(), "AM_C")
    macro = {x["id"]: x["state"] for x in doc["summary"]["macro_dimensions"]}
    assert macro["SOURCE_EXTRACTION"] == "COMPLETELY_NOT_ASSESSED"
    assert macro["ARCHITECTURE_MODELING"] == "HAS_ASSESSED_EVIDENCE"
    src = next(a for a in doc["areas"] if a["id"] == "SOURCE_EXTRACTION")
    assert src["assessability"] == "NOT_ASSESSED" and src["reason"]["reason_code"] in P.PUBLIC_REASONS
    limits = {x["surface_id"]: x for x in doc["evidence_limitations"]}
    assert limits["source.leaf_provenance"]["counts_toward_coverage"] is True
    assert limits["source.leaf_provenance"]["reason"]["reason_code"] == "SUPPLEMENTARY_EVIDENCE_NOT_PROVIDED"


def test_optional_and_cross_plane_limitations_are_shown_but_do_not_count_toward_coverage(scenarios):
    for _a, doc, comp, _f in scenarios:
        roles = {s.surface_id: s.role for s in comp.surfaces}
        for lim in doc["evidence_limitations"]:
            assert lim["counts_toward_coverage"] == (roles[lim["surface_id"]] is A.SurfaceRole.REQUIRED and lim["assessability"] != "NOT_APPLICABLE")
        assert all(roles[x["surface_id"]] is not A.SurfaceRole.DERIVED_VIEW for x in doc["evidence_limitations"])


def test_derived_views_never_appear_as_limitations_even_when_they_are_not_assessed():
    ents, am = m4_entities(), "AM_X"
    facts = all_facts(ents, am)
    views = {s.surface_id for s in A.SURFACE_REGISTRY if s.role is A.SurfaceRole.DERIVED_VIEW}
    assert views
    stripped = [f for f in facts if f.fact_id not in views]
    doc, comp, _f = build(m4_entities(), am, facts=stripped)
    not_assessed = {s.surface_id for s in comp.surfaces if s.role is A.SurfaceRole.DERIVED_VIEW and s.assessability is not m.Assessability.ASSESSED}
    assert not_assessed                                                               # las vistas derivadas SI quedaron sin evaluar...
    assert not (not_assessed & {x["surface_id"] for x in doc["evidence_limitations"]})   # ...pero no son una limitacion (ya cuentan en sus superficies de origen)


# ── NOT_APPLICABLE / INDETERMINATE ───────────────────────────────────────────
def _no_terminal_scope_doc():
    ents = AssessmentUniverse.from_entities  # noqa: F841 - documentation of intent
    from tests.test_modeling_assessment_graph import ent, pool
    pool_ = pool(ent("V1", up=["T1"], has_query=True), ent("T1"))
    u = AssessmentUniverse.from_entities(pool_)
    scope = u.scope_for(None)
    comp = A.compose_assessment(scope, [])
    return P.build_assessment(comp, [], client="Solo")


def test_a_client_without_a_terminal_analytic_model_gets_a_not_applicable_document():
    doc = _no_terminal_scope_doc()
    P.validate_assessment(doc)
    assert doc["assessment_state"] == "NOT_APPLICABLE" and doc["scope"]["terminal_analytic_model"] is None
    assert doc["scope"]["reason"]["reason_code"] == "NO_TERMINAL_ANALYTIC_MODEL"
    assert all(a["assessability"] == "NOT_APPLICABLE" and a["reason"]["reason_code"] == "NO_TERMINAL_ANALYTIC_MODEL" for a in doc["areas"])
    assert doc["observations"] == doc["review_required"] == doc["descriptive_signals"] == [] and doc["summary"]["coverage"]["strict"] is None
    assert doc["summary"]["context_required"]["open_questions"] == 0
    assert [x["state"] for x in doc["summary"]["macro_dimensions"]] == ["NOT_APPLICABLE"] * 3


def test_indeterminate_document_for_a_pool_that_could_not_load():
    doc = P.build_indeterminate_assessment(client="Clash", requested_model="4AM_X", message="colision de entidades", pool_signature="abc")
    P.validate_assessment(doc)
    assert doc["assessment_state"] == "INDETERMINATE" and doc["scope"]["reason"]["reason_code"] == "POOL_LOAD_ERROR"
    assert doc["summary"]["error"] == "colision de entidades" and doc["areas"] == [] and doc["scope"]["terminal_analytic_model"] == "4AM_X"
    assert doc["summary"]["coverage"] is None and doc["summary"]["confidence"] is None


# ── el documento sale de la composicion (no relee hechos) ────────────────────
def test_profile_module_is_stdlib_and_package_only_and_never_touches_files_or_legacy():
    tree = ast.parse((PKG / "profile.py").read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add(("." * n.level) + (n.module or ""))
    assert mods <= {"__future__", "json", "typing", ".", ".assessability", ".evidence_contract", ".identity", ".model", ".signals"}, mods
    text = (PKG / "profile.py").read_text(encoding="utf-8")
    for needle in ("open(", "read_text", "write_text", "legacy_bridge", "generate_catalog"):
        assert needle not in text, needle


# ── textos publicos en espanol ───────────────────────────────────────────────
SPANISH_BANNED = re.compile(r"\b(score|scores|scoring|band|bands|weight|weights|penalty|penalties|rating|ranking|PLENO|BUENO|PARCIAL|grade|maturity|"
                            r"puntaje|puntajes|puntuaci[oó]n|banda|bandas|penalizaci[oó]n|penalidad|ponderaci[oó]n|madurez)\b", re.I)


def test_public_translations_cover_the_signal_registry_position_by_position():
    sig = {s.signal_id: s for s in S.SIGNAL_REGISTRY}
    assert set(P.INTERPRETATION_LIMITS) == set(P.LEGITIMATE_REASONS) == set(P.OCCURRENCE_UNIT) == set(sig)
    assert set(P.CONTEXT_NEEDED) == {k for k, s in sig.items() if s.kind is not m.SignalKind.DESCRIPTIVE}
    for sid, spec in sig.items():
        assert len(P.INTERPRETATION_LIMITS[sid]) == len(spec.interpretation_limits), sid        # un limite nuevo en el registro obliga a traducirlo
        assert len(P.LEGITIMATE_REASONS[sid]) == len(spec.legitimate_reasons), sid
        assert P.public_limits(spec) == list(P.INTERPRETATION_LIMITS[sid]) and P.public_legitimate_reasons(spec) == list(P.LEGITIMATE_REASONS[sid])
        assert all(t and t != orig for t, orig in zip(P.INTERPRETATION_LIMITS[sid], spec.interpretation_limits))      # nunca se publica el texto en ingles del registro
        assert all(t != orig for t, orig in zip(P.LEGITIMATE_REASONS[sid], spec.legitimate_reasons))


def test_surface_titles_cover_every_fact_surface_and_are_spanish():
    fact_surfaces = {s.surface_id for s in A.SURFACE_REGISTRY if s.kind is A.SurfaceKind.FACT_EVIDENCE}
    assert set(P.SURFACE_TITLES) == fact_surfaces
    assert not any(SPANISH_BANNED.search(t) for t in P.SURFACE_TITLES.values())


def test_all_human_presentation_text_is_free_of_unpublished_vocabulary_in_both_languages():
    pools = [P.PUBLIC_REASONS.values(), (t for pair in P.DISPLAY.values() for t in pair), P.WHY_SHOWN.values(), P.SIGNAL_QUESTIONS.values(),
             P.CONTEXT_NEEDED.values(), P.CONTEXT_QUESTION_TEXT.values(), P.OCCURRENCE_UNIT.values(), P.SURFACE_TITLES.values(),
             (t for ts in P.INTERPRETATION_LIMITS.values() for t in ts), (t for ts in P.LEGITIMATE_REASONS.values() for t in ts),
             P._COMPONENT_LIMITATION.values(), P._COMPONENT_TEXT.values(), P._EXCLUDED_WHY.values(),
             [P.CONFIDENCE_MEANING, P.COVERAGE_MEANING, P.NOT_APPLICABLE_CONFIDENCE_MEANING]]
    for texts in pools:
        for t in texts:
            assert not SPANISH_BANNED.search(t), t


def test_public_reason_summaries_and_texts_are_spanish_while_codes_stay_technical():
    assert P.PUBLIC_REASONS["SOURCE_PROVENANCE_NOT_OBSERVABLE"] == "El origen de un artefacto de frontera local no puede observarse desde la exportación estándar de Modelado."
    assert P.PUBLIC_REASONS["NO_TERMINAL_ANALYTIC_MODEL"].startswith("El cliente no tiene un modelo analítico terminal")
    english_markers = re.compile(r"\b(the|is|are|of|not|and|this|that|for)\b")
    texts = [*P.PUBLIC_REASONS.values(), *(t for pair in P.DISPLAY.values() for t in pair), *P.WHY_SHOWN.values(), *P.SIGNAL_QUESTIONS.values(),
             *P.CONTEXT_NEEDED.values(), *P.CONTEXT_QUESTION_TEXT.values(), *(t for ts in P.INTERPRETATION_LIMITS.values() for t in ts),
             *(t for ts in P.LEGITIMATE_REASONS.values() for t in ts), *P.SURFACE_TITLES.values(), P.CONFIDENCE_MEANING, P.COVERAGE_MEANING]
    assert not [t for t in texts if len(english_markers.findall(t)) >= 2]                    # dos o mas palabras funcionales inglesas = texto sin traducir
    assert all(code == code.upper() and " " not in code for code in P.PUBLIC_REASONS)         # el codigo no se traduce


def test_documents_carry_spanish_human_text_and_no_internal_english_rationale(scenarios):
    for _a, doc, _c, _f in scenarios:
        assert all("rationale" not in s for s in doc["surfaces"]) and all("what_it_means" not in lim for lim in doc["evidence_limitations"])
        for g in _groups(doc):
            assert g["title"] == P.DISPLAY[g["id"]][0] and g["explanation"] == P.DISPLAY[g["id"]][1] and g["occurrence_unit"] == P.OCCURRENCE_UNIT[g["id"]]
            if "legitimate_reasons" in g:
                assert g["legitimate_reasons"] == list(P.LEGITIMATE_REASONS[g["id"]])
        assert doc["summary"]["coverage"]["meaning"] == P.COVERAGE_MEANING and doc["summary"]["confidence"]["meaning"] == P.CONFIDENCE_MEANING
        for lim in doc["evidence_limitations"]:
            assert lim["title"].startswith("Regla: ") if lim["surface_id"].startswith("rule.") else lim["title"] == P.SURFACE_TITLES[lim["surface_id"]]


# ── artefactos de frontera: los subtotales concilian con el total ────────────
def test_boundary_artifact_breakdown_reconciles_to_the_total_for_every_scenario(scenarios):
    seen = 0
    for _a, doc, _c, facts in scenarios:
        boundary = next((f for f in facts if f.fact_id == "ingestion.boundary_artifacts"), None)
        ing = next(a for a in doc["areas"] if a["id"] == "INGESTION")
        row = next((h for h in ing["highlights"] if h["id"] == "boundary_artifacts"), None)
        if boundary is None or boundary.value is None:
            continue
        seen += 1
        assert row is not None and row["value"] == boundary.value["count"] == len(boundary.value["artifacts"])
        assert sum(b["count"] for b in row["breakdown"]) == row["value"]                       # lo mostrado concilia con el total (sin hardcodear cifras)
        assert [b["name"] for b in row["breakdown"]] == ["con origen remoto observado", "locales: origen no observable", "provistos por la plataforma (sistema)"]
        assert all(b["count"] >= 0 for b in row["breakdown"])
    assert seen >= 5


def test_boundary_breakdown_is_an_exhaustive_and_exclusive_partition():
    def art(remote, identity="PROJECT_ASSIGNED", impl="REMOTE_IMPORTED"):
        return {"has_remote_entity": remote, "identity": identity, "implementation": impl}
    rows = [art(True), art(True, "UNKNOWN"), art(False, "UNKNOWN", "LOCAL_TABLE"), art(False, "UNKNOWN", "STRUCTURE_ONLY"), art(False, "SYSTEM", "SYSTEM_PROVIDED"),
            art(True, "SYSTEM", "SYSTEM_PROVIDED"),                                           # un artefacto de sistema con entidad remota cuenta UNA sola vez (como sistema)
            art(False, "UNKNOWN", "SYSTEM_PROVIDED")]                                         # marcadores contradictorios: tambien UNA sola vez (sistema), nunca doble
    out = {b["name"]: b["count"] for b in P._boundary_breakdown(rows)}
    assert out == {"con origen remoto observado": 2, "locales: origen no observable": 2, "provistos por la plataforma (sistema)": 3}
    assert sum(out.values()) == len(rows)
    assert [b["count"] for b in P._boundary_breakdown([])] == [0, 0, 0]


def test_representative_surface_titles_are_pinned_in_spanish():
    """Las evidencias que suelen aparecer como no evaluadas en los informes: su titulo es contrato publico (espanol)."""
    assert P.SURFACE_TITLES["source.replication_mode"] == "Modo de replicación (federado o replicado)"
    assert P.SURFACE_TITLES["source.leaf_provenance"] == "Procedencia de las hojas locales sin origen remoto"
    assert P.SURFACE_TITLES["propagation.derivation_semantics"] == "Semántica estructural de las derivaciones"
    assert P.SURFACE_TITLES["naming.convention_context"] == "Contexto de la convención de nombres"
    assert P.SURFACE_TITLES["ingestion.boundary_artifacts"] == "Artefactos de frontera"


# ═══ M8 calibracion 1: contratos de las decisiones del owner ═══════════════════
def test_not_applicable_surfaces_are_never_evidence_limitations_and_are_published_separately(scenarios):
    seen_na = 0
    for _a, doc, comp, _f in scenarios:
        assert all(lim["assessability"] in ("NOT_ASSESSED", "PARTIALLY_ASSESSED", "INDETERMINATE") for lim in doc["evidence_limitations"])
        assert all(lim["counts_toward_coverage"] is (lim["role"] == "REQUIRED") for lim in doc["evidence_limitations"])
        expected = {s.surface_id for s in comp.surfaces if s.assessability is m.Assessability.NOT_APPLICABLE and s.role is not A.SurfaceRole.DERIVED_VIEW}
        assert {x["surface_id"] for x in doc["not_applicable"]} == expected
        assert not ({x["surface_id"] for x in doc["not_applicable"]} & {x["surface_id"] for x in doc["evidence_limitations"]})      # no se mezclan
        for x in doc["not_applicable"]:
            assert x["reason"]["reason_code"] in P.PUBLIC_REASONS and x["title"]
        seen_na += len(expected)
    assert seen_na


def test_pass_through_edges_outside_the_rule_population_get_a_public_reason():
    out = P.public_reason(m.Assessability.NOT_APPLICABLE, None, "EDGE_OUTSIDE_RULE_POPULATION")
    assert out["reason_code"] == "EDGE_OUTSIDE_RULE_POPULATION" and out["reason_summary"].startswith("Esta arista no pertenece a la población de la regla")


def _convention_keys(facts):
    return {k for f in facts if f.fact_id == "naming.convention_context" and f.value for k in f.value["open_context_questions"]}


def test_context_questions_are_deduplicated_by_an_explicit_semantic_registry(scenarios):
    assert set(P.CONTEXT_QUESTION_COVERED_BY) == set(P.CONTEXT_QUESTION_TEXT)
    real = {s.signal_id for s in S.SIGNAL_REGISTRY if s.kind is not m.SignalKind.DESCRIPTIVE}
    assert all(set(v) <= real for v in P.CONTEXT_QUESTION_COVERED_BY.values())                       # solo senales que realmente formulan una pregunta
    assert P.CONTEXT_QUESTION_COVERED_BY["local_convention_for_section_28_deviations"] == ()         # pregunta independiente: nunca se deduplica
    deduped_somewhere = shown_somewhere = False
    for _a, doc, _c, facts in scenarios:
        keys = _convention_keys(facts)
        ctx = doc["summary"]["context_required"]
        active = {g["id"] for g in (*doc["observations"], *doc["review_required"])}
        expected = {k for k in keys if set(P.CONTEXT_QUESTION_COVERED_BY[k]) & active}
        assert {d["id"] for d in ctx["deduplicated_questions"]} == expected
        assert all(d["covered_by"] in active and d["covered_by"] in P.CONTEXT_QUESTION_COVERED_BY[d["id"]] for d in ctx["deduplicated_questions"])
        shown = {q["id"] for q in ctx["questions"] if q["origin"] == "CONTEXT_ONLY"}
        assert shown == keys - expected and "local_convention_for_section_28_deviations" in shown  # cada pregunta semantica aparece UNA sola vez
        assert ctx["open_questions"] == len(ctx["questions"]) == len([g for g in (*doc["observations"], *doc["review_required"])]) + len(shown)
        deduped_somewhere |= bool(expected)
        shown_somewhere |= bool(shown - {"local_convention_for_section_28_deviations"})
    assert deduped_somewhere and shown_somewhere


def test_presentation_priority_is_one_explicit_versioned_registry_independent_of_results(scenarios):
    assert P.PRESENTATION_ORDER_VERSION == "presentation-order/v1" and set(P.PRESENTATION_PRIORITY) == set(P.DISPLAY)
    pr = P.PRESENTATION_PRIORITY
    assert pr["DIRECT_RAW_TABLE_ACCESS"] < min(v for k, v in pr.items() if k not in ("DIRECT_RAW_TABLE_ACCESS",))        # observaciones primero
    arch = ["REPEATED_DERIVATION_IN_LINEAGE", "FLAT_CENTRAL_ASSEMBLY_REVIEW", "DUPLICATED_REMOTE_IMPORT", "ROLE_LAYER_DIVERGENCE", "LAYER_BOUNDARY_REVIEW",
            "PASS_THROUGH_REVIEW", "AM_SEMANTIC_UNDERUSE_REVIEW"]
    naming = ["FIELD_VOCABULARY_MIX", "OPAQUE_FIELD_NAME", "LABEL_NOT_ENRICHING"]
    assert max(pr[k] for k in arch) < min(pr[k] for k in naming) and max(pr[k] for k in naming) < pr["SOURCE_FILTER_PRESENT"]
    order = {s.signal_id: i for i, s in enumerate(S.SIGNAL_REGISTRY)}
    for _a, doc, _c, _f in scenarios:
        assert doc["versions"]["presentation_order"] == P.PRESENTATION_ORDER_VERSION
        for kind in ("observations", "review_required", "descriptive_signals"):
            groups = doc[kind]
            assert [g["presentation_priority"] for g in groups] == sorted(g["presentation_priority"] for g in groups)
            assert all(g["presentation_priority"] == pr[g["id"]] for g in groups)                      # no depende de ocurrencias ni de resultados del cliente
            keys = [(g["presentation_priority"], order[g["id"]]) for g in groups]
            assert keys == sorted(keys)
        for area in doc["areas"]:
            pri = [pr[i] for i in area["finding_groups"]]
            assert pri == sorted(pri)
    ids = {g["id"]: g["occurrence_count"] for _a, doc, _c, _f in scenarios for g in _groups(doc)}
    assert len(ids) == len(set(P.PRESENTATION_PRIORITY) & set(ids))                                    # prioridad por senal: el mismo orden con cualquier conteo


def test_closure_composition_is_published_as_descriptive_architecture_context(scenarios):
    seen_assoc = False
    for _a, doc, comp, facts in scenarios:
        am = doc["scope"]["terminal_analytic_model"]
        row = next(h for a in doc["areas"] if a["id"] == "ANALYTIC_MODEL" for h in a["highlights"] if h["id"] == "semantic_closure")
        assert row["value"] == doc["scope"]["closure_size"] == sum(b["count"] for b in row["breakdown"])           # el cierre concilia con sus dos partes
        fact = next(f for f in facts if f.fact_id == "am.closure_composition" and f.subject == am)
        assert [b["count"] for b in row["breakdown"]] == [fact.value["strict_lineage_size"], fact.value["association_mediated_size"]]
        assert "linaje estricto" in row["breakdown"][0]["name"] and "asociación" in row["breakdown"][1]["name"]
        seen_assoc |= fact.value["association_mediated_size"] > 0
        assert not any(w in json.dumps(row, ensure_ascii=False).lower() for w in ("bueno", "malo", "defecto", "calidad"))   # contexto, nunca un juicio
    assert seen_assoc


def test_numeric_non_measure_attributes_are_published_descriptively(scenarios):
    seen = 0
    for _a, doc, _c, facts in scenarios:
        am = doc["scope"]["terminal_analytic_model"]
        fact = next(f for f in facts if f.fact_id == "am.numeric_attributes_not_declared_as_measures" and f.subject == am)
        hs = {h["id"]: h for a in doc["areas"] if a["id"] == "ANALYTIC_MODEL" for h in a["highlights"]}
        n = len(fact.value["fields"])
        assert hs["numeric_non_measure_attributes"]["value"] == n
        if n:
            sample = hs["numeric_non_measure_fields"]["value"]
            assert len(sample) == min(n, 10) + (1 if n > 10 else 0) and sample[0].startswith(fact.value["fields"][0]["field"])
            seen += 1
        else:
            assert "numeric_non_measure_fields" not in hs
        assert not [g for g in _groups(doc) if "NUMERIC" in g["id"]]                                  # NO es una senal ni una revision automatica
        assert all(not any(m.find_forbidden_tokens(k) for k in h) for h in hs.values())
    assert seen


def test_direct_raw_evidence_keeps_distance_and_dependents_without_any_severity(scenarios):
    seen = 0
    for _a, doc, _c, _f in scenarios:
        g = next((x for x in doc["observations"] if x["id"] == "DIRECT_RAW_TABLE_ACCESS"), None)
        if not g:
            continue
        for o in g["occurrences"]:
            seen += 1
            v = o["values"]
            assert {"remote_entity", "remote_connection", "source_filter", "identity_provenance", "reaches_terminal_by_strict_lineage", "lineage_distance",
                    "transitive_lineage_dependents"} <= set(v)
            assert not [k for k in v if "sever" in k or "rank" in k or "priority" in k]
        assert not [k for k in g if "sever" in k]
    assert seen


def test_presentation_text_has_no_remaining_known_anglicisms():
    texts = [*(t for ts in P.LEGITIMATE_REASONS.values() for t in ts), *(t for ts in P.INTERPRETATION_LIMITS.values() for t in ts), *P.WHY_SHOWN.values(),
             *(t for pair in P.DISPLAY.values() for t in pair), *P.SIGNAL_QUESTIONS.values(), *P.CONTEXT_NEEDED.values(), *P.PUBLIC_REASONS.values()]
    assert not [t for t in texts if re.search(r"\b(staging|reporting)\b", t, re.I)]
    assert "objeto intermedio materializado" in " ".join(P.LEGITIMATE_REASONS["ROLE_LAYER_DIVERGENCE"])


def test_numeric_non_measure_sample_is_bounded_and_states_how_many_more_exist():
    from tests._modeling_assessment_m3_fixtures import m3_entities
    ents = m3_entities()
    base = len(next(f for f in all_facts(ents, "AM_M") if f.fact_id == "am.numeric_attributes_not_declared_as_measures").value["fields"])
    for i in range(12):
        ents["AM_M"].fields.append({"campo": f"NUM{i:02d}", "label": "", "tipo": "Decimal", "longitud": "13,3", "key": False, "medida": False, "assoc": "",
                                    "derived": False, "cast": ""})
    doc, _comp, _facts = build(ents, "AM_M")
    hs = {h["id"]: h for a in doc["areas"] if a["id"] == "ANALYTIC_MODEL" for h in a["highlights"]}
    total = base + 12
    assert hs["numeric_non_measure_attributes"]["value"] == total and total > 10
    sample = hs["numeric_non_measure_fields"]["value"]
    assert len(sample) == 11 and sample[-1] == f"… (+{total - 10})" and all("(" in x for x in sample[:10])         # 10 elementos + cuantos mas existen
