"""M3 — propiedades COMUNES de los extractores de AM / Propagation / Harmonization (contrato Fase 1, determinismo, limites)."""
from __future__ import annotations

import ast
import json
import random
from pathlib import Path

import pytest

from tests._modeling_assessment_m3_fixtures import m3_entities
from tools.catalog.modeling_assessment import model as m
from tools.catalog.modeling_assessment.facts_am import am_facts
from tools.catalog.modeling_assessment.facts_harmonization import harmonization_facts
from tools.catalog.modeling_assessment.facts_propagation import propagation_facts
from tools.catalog.modeling_assessment.identity import classify_universe
from tools.catalog.modeling_assessment.scope import AssessmentUniverse

PKG = Path(__file__).resolve().parent.parent / "tools" / "catalog" / "modeling_assessment"
EXTRACTORS = {m.Area.ANALYTIC_MODEL: (am_facts, "am"), m.Area.PROPAGATION: (propagation_facts, "propagation"),
              m.Area.HARMONIZATION: (harmonization_facts, "harmonization")}


def _run(ents=None, am="AM_M"):
    ents = ents or m3_entities()
    u = AssessmentUniverse.from_entities(ents)
    return {area: fn(u, ents, am) for area, (fn, _) in EXTRACTORS.items()}


def test_facts_fit_the_phase1_area_cards_and_serialize_to_pure_json():
    for area, facts in _run().items():
        assert facts
        card = m.AreaCard(area, m.ApplicabilityDecision(m.Applicability.APPLICABLE), m.Assessability.PARTIALLY_ASSESSED, facts, ())
        json.dumps(m.to_jsonable(card), sort_keys=True, allow_nan=False)


def test_fact_ids_are_namespaced_by_their_area_and_classes_are_a_b_or_c2_only():
    for area, facts in _run().items():
        prefix = EXTRACTORS[area][1]
        assert all(f.fact_id.startswith(prefix + ".") and f.area is area for f in facts)
        assert {f.evidence_class for f in facts} <= {m.EvidenceClass.A, m.EvidenceClass.B, m.EvidenceClass.C2}


def test_no_fact_declares_a_quality_state_signal_observation_or_review_item():
    forbidden = {"signal", "observation", "review", "quality", "good", "bad", "weak", "strong", "mature", "peripheral"}
    for facts in _run().values():
        for f in facts:
            tokens = set(m.tokenize_identifier(f.fact_id)) if hasattr(m, "tokenize_identifier") else set()
            assert not (tokens & forbidden), f.fact_id
            blob = json.dumps(m.to_jsonable(f)).lower()
            assert "peripheral" not in blob and "irrelevant" not in blob and "does not feed" not in blob


def test_output_is_independent_of_entity_insertion_order():
    ref = {a: [m.to_jsonable(f) for f in fs] for a, fs in _run().items()}
    items = list(m3_entities().items())
    rng = random.Random(5)
    for _ in range(4):
        rng.shuffle(items)
        got = {a: [m.to_jsonable(f) for f in fs] for a, fs in _run(dict(items)).items()}
        assert got == ref


def test_precomputed_identity_is_equivalent_to_internal_identity_for_all_extractors():
    ents = m3_entities()
    u = AssessmentUniverse.from_entities(ents)
    ident = classify_universe(ents)
    for _, (fn, _) in EXTRACTORS.items():
        assert fn(u, ents, "AM_M") == fn(u, ents, "AM_M", identity=ident)


def test_processing_context_and_orphans_never_appear_in_any_m3_fact():
    ents = m3_entities()
    for facts in _run(ents).values():
        blob = repr([(f.subject, f.value) for f in facts])
        assert "DF_T" not in blob and "ONLY_VIA_DF" not in blob


def test_all_facts_are_modeling_plane_and_never_processing():
    for facts in _run().values():
        assert {f.plane for f in facts} == {m.Plane.MODELING}
        for f in facts:
            assert {r.plane for r in f.refs} <= {m.Plane.MODELING}


def test_m3_extractors_read_only_the_loaded_entities_never_raw_files_or_legacy_directly():
    for name in ("contract.py", "facts_am.py", "facts_propagation.py", "facts_harmonization.py"):
        tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mods.add(("." * node.level) + (node.module or ""))
            elif isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
        assert not any("readers" in x or "open" == x for x in mods), (name, mods)
        assert not any(x.startswith("tools.") or x.startswith("webapp") for x in mods), (name, mods)
        text = (PKG / name).read_text(encoding="utf-8")
        assert "open(" not in text and "read_text" not in text and "json.load" not in text


@pytest.mark.parametrize("area", list(EXTRACTORS))
def test_every_extractor_is_pure_it_does_not_mutate_the_entities(area):
    ents = m3_entities()
    before = {n: (list(e.fields), list(e.measures), list(e.associations), list(e.upstreams), list(e.assoc_upstreams))
              for n, e in ents.items()}
    snapshot = repr(before)
    u = AssessmentUniverse.from_entities(ents)
    EXTRACTORS[area][0](u, ents, "AM_M")
    after = {n: (list(e.fields), list(e.measures), list(e.associations), list(e.upstreams), list(e.assoc_upstreams))
             for n, e in ents.items()}
    assert repr(after) == snapshot


def test_the_layer_label_is_inferred_layer_with_its_basis_in_every_m2_and_m3_fact():
    """`name_derived_layer` era impreciso (el criterio es COMPUESTO: remote_entity, §28, rol declarado, prefijo)."""
    from tests._modeling_assessment_m2_fixtures import m2_entities
    from tools.catalog.modeling_assessment.facts_ingestion import ingestion_facts
    ents2 = m2_entities()
    ing = ingestion_facts(AssessmentUniverse.from_entities(ents2), ents2, "AM_P")
    allowed = {"REMOTE_ENTITY", "SECTION_28_NAME", "DECLARED_ROLE", "NAME_PREFIX", "UNKNOWN"}
    found = 0

    def walk(v):
        nonlocal found
        if isinstance(v, dict):
            if "inferred_layer" in v:
                found += 1
                assert v["inferred_layer_basis"] in allowed          # la capa siempre viaja con su base
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    for facts in [*_run().values(), ing]:
        for f in facts:
            assert "name_derived" not in f.fact_id and "name_derived" not in json.dumps(f.value)
            walk(f.value)
    assert found > 20
