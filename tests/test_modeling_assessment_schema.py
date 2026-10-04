"""Contrato de datos del perfil (Fase 1, hito M0): esquema, vocabulario prohibido, invariantes
de estado, aritmetica de cobertura (estricta e inclusiva) y serializacion determinista.

El perfil de Fase 1 NO puede contener score / banda / peso / umbral / penalidad / bonus ni
veredictos (tampoco el legacy). Eso se verifica sobre dataclasses, enums, esquema generado,
valores de hechos y el perfil serializado.
"""
from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from enum import Enum

import pytest

from tools.catalog.modeling_assessment import model as m
from tools.catalog.modeling_assessment import schema as s

A, N = m.Assessability, m.Applicability


# ── helpers de construccion ────────────────────────────────────────────────
def _app(reason=None):
    return m.ApplicabilityDecision(N.NOT_APPLICABLE, reason) if reason else m.ApplicabilityDecision(N.APPLICABLE)


def _fact(fid="source.family_counts", area=m.Area.SOURCE_EXTRACTION, subject="closure", value=None,
          assess=A.ASSESSED, cause=None, app=None, plane=m.Plane.MODELING):
    return m.Fact(fid, area, subject, {"ideal": 13} if value is None else value, m.EvidenceClass.A, plane,
                  (m.EvidenceRef("AM_X", "remote_entity", "views/AM_X.json", plane),),
                  app or _app(), assess, cause)


def _cards(extra_facts=None, signals=None):
    extra_facts = extra_facts or {}
    cards = []
    for area in m.ASSESSED_AREAS:
        facts = tuple(extra_facts.get(area, ()))
        cards.append(m.AreaCard(area, _app(), A.ASSESSED if facts else A.NOT_ASSESSED, facts,
                                tuple((signals or {}).get(area, ())),
                                cause=None if facts else m.Cause.NOT_IN_SNAPSHOT))
    return tuple(cards)


def _profile(cards=None, observations=(), reviews=()):
    cards = cards or _cards()
    counts = m.CoverageCounts(assessed=1, not_assessed=5)
    cov = m.CoverageReport(
        counts, tuple(m.AreaCoverage(a, m.CoverageCounts()) for a in m.ASSESSED_AREAS),
        tuple(m.MacroDimensionStatus(d, m.MacroDimensionState.HAS_ASSESSED_EVIDENCE) for d in m.MacroDimension))
    return m.ModelingProfile(
        m.SCHEMA_VERSION, m.RULESET_VERSION,
        m.ProfileScope(_app(), "AM_X", 2, ("AM_X", "VF_X"), "sha256:abc"),
        cards, tuple(observations), tuple(reviews), cov,
        m.Confidence(m.ConfidenceLevel.MEDIUM, (
            m.ConfidenceDriver(m.ConfidenceDriverName.EPISTEMIC_RELIABILITY, m.ConfidenceLevel.LOW, "resolved provenance 0.5"),)))


def _sample_profile():
    f = _fact()
    sig = m.Signal("source_family_distribution", m.Area.SOURCE_EXTRACTION, m.SignalKind.DESCRIPTIVE,
                   ("remote_bearing",), (f.key,), ("una sola conexion no es merito",))
    obs = m.Observation(m.ObservationId.DIRECT_RAW_TABLE_ACCESS, m.Area.SOURCE_EXTRACTION, ("RAW_1",), (f.key,),
                        "direct_raw_table_justified_exception")
    rev = m.ReviewItem(m.ReviewId.PASS_THROUGH_REVIEW, m.Area.PROPAGATION, ("VF_X",), (f.key,),
                       "rol funcional del artefacto", ("serving boundary", "product boundary"))
    cards = _cards({m.Area.SOURCE_EXTRACTION: [f]}, {m.Area.SOURCE_EXTRACTION: [sig]})
    return _profile(cards, [obs], [rev])


# ── vocabulario prohibido ──────────────────────────────────────────────────
@pytest.mark.parametrize("name,hit", [
    ("score", True), ("global_score", True), ("bandGate", True), ("weights", True), ("THRESHOLD", True),
    ("penalty", True), ("non_penalties", True), ("bonus", True), ("PLENO", True), ("veredicto", True),
    ("legacy_verdict", True), ("maturity", True),
    ("ratio_strict", False), ("PARTIALLY_ASSESSED", False), ("DIRECT_RAW_TABLE_ACCESS", False),
    ("bandwidth_note", False), ("scoreboard", False),
])
def test_forbidden_token_detection(name, hit):
    assert bool(m.find_forbidden_tokens(name)) is hit


def _all_enums():
    return [v for v in vars(m).values() if isinstance(v, type) and issubclass(v, Enum) and v is not Enum
            and v.__module__ == m.__name__]


def _all_dataclasses():
    return [v for v in vars(m).values() if isinstance(v, type) and is_dataclass(v) and v.__module__ == m.__name__]


def test_no_scoring_vocabulary_in_dataclass_fields_or_class_names():
    bad = []
    for dc in _all_dataclasses():
        bad += [f"{dc.__name__}" for _ in [0] if m.find_forbidden_tokens(dc.__name__)]
        bad += [f"{dc.__name__}.{f.name}" for f in fields(dc) if m.find_forbidden_tokens(f.name)]
    assert bad == []


def test_no_scoring_vocabulary_in_enum_names_or_values():
    bad = []
    for e in _all_enums():
        bad += [e.__name__] if m.find_forbidden_tokens(e.__name__) else []
        for member in e:
            if m.find_forbidden_tokens(member.name) or m.find_forbidden_tokens(str(member.value)):
                bad.append(f"{e.__name__}.{member.name}")
    assert bad == []


def test_no_scoring_vocabulary_in_generated_schema():
    names = s.iter_schema_names(s.build_profile_schema())
    assert names, "el esquema no expone propiedades"
    assert [n for n in names if m.find_forbidden_tokens(n)] == []


def test_fact_values_cannot_smuggle_scoring_keys():
    for bad_key in ("score", "band", "weight", "threshold", "penalty", "bonus", "legacy_verdict"):
        with pytest.raises(ValueError, match="vocabulario prohibido"):
            _fact(value={"nested": {bad_key: 1}})


def test_fact_id_and_signal_id_cannot_carry_scoring_vocabulary():
    with pytest.raises(ValueError):
        _fact(fid="source.quality_score")
    with pytest.raises(ValueError):
        m.Signal("raw_penalty", m.Area.SOURCE_EXTRACTION, m.SignalKind.REVIEW, (), (), ())


def test_phase1_enums_do_not_contain_a_band_or_verdict_concept():
    values = {str(member.value).upper() for e in _all_enums() for member in e}
    assert not (values & {"PLENO", "BUENO", "PARCIAL", "PLENA", "BUENA", "BAND", "NOT_AVAILABLE"})


def test_serialized_profile_has_no_forbidden_keys_or_enum_values():
    data = m.to_jsonable(_sample_profile())

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert not m.find_forbidden_tokens(k), k
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(data)


# ── esquema generado ↔ contrato ────────────────────────────────────────────
def test_schema_golden_file_in_sync_with_contract():
    assert s.SCHEMA_FILE.exists()
    assert s.SCHEMA_FILE.read_text(encoding="utf-8") == s.schema_text(), \
        "regenerar con: python -m tools.catalog.modeling_assessment.schema --write (y revisar el diff)"


def test_schema_declares_version_and_profile_shape():
    sch = s.build_profile_schema()
    assert sch["$id"] == m.SCHEMA_VERSION == "modeling_assessment.profile/v1"
    assert set(sch["required"]) == {f.name for f in fields(m.ModelingProfile)}
    assert sch["additionalProperties"] is False


def test_schema_covers_every_dataclass_field_and_computed_ratio():
    defs = s.build_profile_schema()["$defs"]
    for dc in _all_dataclasses():
        if dc is m.ModelingProfile:
            continue
        if dc.__name__ in defs:
            assert {f.name for f in fields(dc)} <= set(defs[dc.__name__]["properties"])
    cc = defs["CoverageCounts"]["properties"]
    assert {"ratio_strict", "ratio_inclusive", "applicable"} <= set(cc)


def _conforms(node, sch, defs, path="$"):
    """Validador minimo (tipos/props/enums) — suficiente para detectar deriva contrato↔serializacion."""
    if "$ref" in sch:
        return _conforms(node, defs[sch["$ref"].split("/")[-1]], defs, path)
    if "anyOf" in sch:
        errs = [_conforms(node, a, defs, path) for a in sch["anyOf"]]
        assert any(e is None for e in errs), f"{path}: {errs}"
        return None
    if sch == {}:
        return None
    t = sch.get("type")
    types = t if isinstance(t, list) else [t]
    py = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "null": type(None),
          "array": list, "object": dict}
    ok = [x for x in types if isinstance(node, py[x]) and not (x == "integer" and isinstance(node, bool))]
    if not ok:
        return f"{path}: tipo {type(node).__name__} no esta en {types}"
    if "enum" in sch and node not in sch["enum"]:
        return f"{path}: {node!r} fuera del enum"
    if isinstance(node, dict) and "properties" in sch:
        extra = set(node) - set(sch["properties"])
        assert not extra, f"{path}: propiedades fuera del esquema {extra}"
        assert set(sch.get("required", [])) <= set(node), f"{path}: faltan {set(sch['required']) - set(node)}"
        for k, v in node.items():
            err = _conforms(v, sch["properties"][k], defs, f"{path}.{k}")
            assert err is None, err
    if isinstance(node, list) and "items" in sch:
        for i, v in enumerate(node):
            err = _conforms(v, sch["items"], defs, f"{path}[{i}]")
            assert err is None, err
    return None


def test_serialized_sample_conforms_to_generated_schema():
    sch = s.build_profile_schema()
    assert _conforms(m.to_jsonable(_sample_profile()), {k: v for k, v in sch.items() if k != "$defs"},
                     sch["$defs"]) is None


# ── invariantes de aplicabilidad / assessability ───────────────────────────
def test_applicability_first_not_applicable_requires_reason_and_vice_versa():
    with pytest.raises(ValueError):
        m.ApplicabilityDecision(N.NOT_APPLICABLE)
    with pytest.raises(ValueError):
        m.ApplicabilityDecision(N.APPLICABLE, m.ApplicabilityReason.SYSTEM_ARTIFACT)
    assert m.ApplicabilityDecision(N.NOT_APPLICABLE, m.ApplicabilityReason.SYSTEM_ARTIFACT)


def test_not_applicable_state_must_match_between_applicability_and_assessability():
    na = _app(m.ApplicabilityReason.SYSTEM_ARTIFACT)
    _fact(app=na, assess=A.NOT_APPLICABLE)                                      # coherente
    with pytest.raises(ValueError):
        _fact(app=na, assess=A.ASSESSED)                                        # aplicabilidad NA, assessability no
    with pytest.raises(ValueError):
        _fact(assess=A.NOT_APPLICABLE)                                          # al reves


def test_cause_rules():
    _fact(assess=A.NOT_ASSESSED, cause=m.Cause.PLANE_BOUNDARY)
    _fact(assess=A.INDETERMINATE, cause=m.Cause.COLLISION_OR_LOAD_ERROR)
    _fact(assess=A.PARTIALLY_ASSESSED)                                          # causa opcional
    with pytest.raises(ValueError):
        _fact(assess=A.NOT_ASSESSED)                                            # exige causa
    with pytest.raises(ValueError):
        _fact(assess=A.INDETERMINATE)
    with pytest.raises(ValueError):
        _fact(assess=A.ASSESSED, cause=m.Cause.CONTEXT_REQUIRED)                # ASSESSED no admite causa


def test_missing_remote_entity_is_never_not_applicable_by_itself():
    """Q1 (owner): sin remote_entity NO implica NOT_APPLICABLE. Una hoja cuyo contrato de origen
    no es observable es NOT_ASSESSED; NOT_APPLICABLE exige una razon explicita (sistema / objeto
    interno estatico CONOCIDO). El contrato las separa por construccion."""
    unobservable = _fact(assess=A.NOT_ASSESSED, cause=m.Cause.PROVENANCE_UNKNOWN)
    assert unobservable.applicability.state is N.APPLICABLE
    assert {r.value for r in m.ApplicabilityReason} == {"SYSTEM_ARTIFACT", "INTERNAL_STATIC_OBJECT", "NO_TERMINAL_ANALYTIC_MODEL",
                                                                         "NO_OTHER_TERMINAL_ANALYTIC_MODEL", "ROOT_NOT_ANALYTIC_MODEL"}   # + raiz generica (vista/tabla)
    assert "NO_REMOTE_ENTITY" not in {r.value for r in m.ApplicabilityReason}


def test_processing_plane_evidence_is_representable_but_flagged():
    f = _fact(plane=m.Plane.PROCESSING)
    assert f.plane is m.Plane.PROCESSING and f.applicability.state is N.APPLICABLE


def test_review_item_can_never_assert_an_automatic_quality_judgment():
    with pytest.raises(ValueError):
        m.ReviewItem(m.ReviewId.PASS_THROUGH_REVIEW, m.Area.PROPAGATION, ("VF_X",), (), "ctx", ("x",),
                     no_automatic_quality_judgment=False)


def test_observations_are_always_open_in_phase1():
    with pytest.raises(ValueError):
        m.Observation(m.ObservationId.DIRECT_RAW_TABLE_ACCESS, m.Area.SOURCE_EXTRACTION, ("RAW_1",), (), "k",
                      resolution="JUSTIFIED_EXCEPTION")  # type: ignore[arg-type]
    assert [o.value for o in m.Resolution] == ["OPEN_CONTEXT_REQUIRED"]


# ── scope y perfil ─────────────────────────────────────────────────────────
def test_scope_not_applicable_only_for_no_terminal_am():
    ok = m.ProfileScope(_app(m.ApplicabilityReason.NO_TERMINAL_ANALYTIC_MODEL), None, 0, ())
    assert ok.closure_size == 0
    with pytest.raises(ValueError):
        m.ProfileScope(_app(m.ApplicabilityReason.SYSTEM_ARTIFACT), None, 0, ())
    with pytest.raises(ValueError):
        m.ProfileScope(_app(m.ApplicabilityReason.NO_TERMINAL_ANALYTIC_MODEL), "AM_X", 1, ("AM_X",))
    with pytest.raises(ValueError):
        m.ProfileScope(_app(), None, 0, ())
    with pytest.raises(ValueError):
        m.ProfileScope(_app(), "AM_X", 3, ("AM_X",))


def test_profile_requires_the_six_areas_in_canonical_order_and_resolvable_fact_refs():
    cards = _cards()
    assert [c.area.value for c in cards] == ["ANALYTIC_MODEL", "PROPAGATION", "HARMONIZATION",
                                             "INGESTION", "NAMING", "SOURCE_EXTRACTION"]
    with pytest.raises(ValueError):
        _profile(cards[:-1])                                                    # faltan areas
    with pytest.raises(ValueError):
        _profile(tuple(reversed(cards)))                                        # orden no canonico
    dangling = m.ReviewItem(m.ReviewId.ROLE_LAYER_DIVERGENCE, m.Area.INGESTION, ("X",), ("no.existe@X",),
                            "ctx", ("r",))
    with pytest.raises(ValueError, match="no existen"):
        _profile(reviews=[dangling])


def test_scope_is_not_an_assessable_area():
    assert m.Area.SCOPE not in m.ASSESSED_AREAS
    with pytest.raises(ValueError):
        _fact(area=m.Area.SCOPE)
    with pytest.raises(ValueError):
        m.AreaCard(m.Area.SCOPE, _app(), A.ASSESSED, (), ())


def test_macro_dimensions_group_the_six_areas_as_approved():
    assert m.MACRO_DIMENSION_OF_AREA == {
        m.Area.ANALYTIC_MODEL: m.MacroDimension.ARCHITECTURE_MODELING,
        m.Area.PROPAGATION: m.MacroDimension.ARCHITECTURE_MODELING,
        m.Area.HARMONIZATION: m.MacroDimension.ARCHITECTURE_MODELING,
        m.Area.INGESTION: m.MacroDimension.ARCHITECTURE_MODELING,
        m.Area.NAMING: m.MacroDimension.NAMING,
        m.Area.SOURCE_EXTRACTION: m.MacroDimension.SOURCE_EXTRACTION,
    }


# ── cobertura: estricta (gate futuro) e inclusiva (diagnostica) ────────────
def test_coverage_reports_both_strict_and_inclusive():
    c = m.CoverageCounts(assessed=6, partially_assessed=2, not_assessed=1, indeterminate=1, not_applicable=4)
    assert c.applicable == 10                                  # NOT_APPLICABLE fuera del denominador
    assert c.ratio_strict == pytest.approx(0.6)                # solo ASSESSED
    assert c.ratio_inclusive == pytest.approx(0.8)             # ASSESSED + PARTIALLY_ASSESSED
    assert c.ratio_strict <= c.ratio_inclusive


def test_coverage_not_applicable_leaves_denominator_and_not_assessed_reduces_it():
    base = m.CoverageCounts(assessed=4, not_assessed=1)
    assert base.ratio_strict == pytest.approx(0.8)
    assert m.CoverageCounts(assessed=4, not_assessed=1, not_applicable=100).ratio_strict == pytest.approx(0.8)
    assert m.CoverageCounts(assessed=4, not_assessed=2).ratio_strict < base.ratio_strict
    assert m.CoverageCounts(assessed=4, not_assessed=1, indeterminate=1).ratio_strict < base.ratio_strict


def test_coverage_with_nothing_applicable_is_none_not_zero():
    c = m.CoverageCounts(not_applicable=3)
    assert c.applicable == 0 and c.ratio_strict is None and c.ratio_inclusive is None


def test_coverage_report_has_no_band_or_gate_field_in_phase1():
    names = {f.name for f in fields(m.CoverageReport)} | {f.name for f in fields(m.CoverageCounts)}
    assert not any(("gate" in n) or ("eligib" in n) or ("band" in n) for n in names)
    assert {"overall", "by_area", "macro_dimensions"} == {f.name for f in fields(m.CoverageReport)}


def test_negative_counts_rejected():
    with pytest.raises(ValueError):
        m.CoverageCounts(assessed=-1)


# ── determinismo ───────────────────────────────────────────────────────────
def test_canonical_json_is_deterministic_and_valid_json():
    a, b = m.canonical_json(_sample_profile()), m.canonical_json(_sample_profile())
    assert a == b and a.endswith("\n")
    data = json.loads(a)
    assert data["schema_version"] == m.SCHEMA_VERSION
    assert data["coverage"]["overall"]["ratio_strict"] == pytest.approx(1 / 6)
    assert list(data) == sorted(data)                           # claves ordenadas


def test_canonical_json_rejects_non_finite_values():
    with pytest.raises(ValueError):
        _fact(value={"x": float("nan")})
