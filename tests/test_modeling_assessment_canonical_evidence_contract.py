"""
Gate 6 — contrato EXPLICITO de ``canonical_evidence`` (validador condicionado por version), match ``X_Delta`` -> ``X`` y compatibilidad de ids de senal.

El documento publico ``assessment/v1`` no tiene JSON Schema: su contrato para esta seccion es ``evidence_contract`` (vocabularios cerrados + invariantes),
condicionado por ``versions.canonical_evidence``. Fixtures SINTETICAS.
"""
from __future__ import annotations

import copy
import json

import pytest

from tests import test_modeling_assessment_derivation as TD
from tests import test_modeling_assessment_leaf_provenance as TL
from tools.catalog.modeling_assessment import evidence_contract as EC
from tools.catalog.modeling_assessment import model as M
from tools.catalog.modeling_assessment import presentation as PR
from tools.catalog.modeling_assessment import profile as P
from tools.catalog.modeling_assessment import render_md as R


# ───────────────────────────── documentos de partida ─────────────────────────────
def _leafdoc(scope=None, extra=None, leaves=None):
    return TL._doc(TL._pool(leaves, extra), scope)


def _derivdoc():
    root = TD._pool([TD.ref("K"), TD.dosis_base("DOSIS")], [TD.ref("K"), TD.case_sign_flip("DOSIS")], am_cols=[TD.ref("K"), TD.ref("DOSIS")])
    return TD._assess(root)


@pytest.fixture(scope="module")
def good_leaf():
    return _leafdoc(TL.SCOPE_ALL, {"Acme/replication-flows/RF_X.json": TL._rf("RF_X", ["LOCAL_X"], ["ZCDS_SRC"])})


@pytest.fixture(scope="module")
def good_notobs():
    return _leafdoc(TL.SCOPE_ALL)


@pytest.fixture(scope="module")
def good_deriv():
    return _derivdoc()


def _invalid(doc, needle=None):
    with pytest.raises(ValueError) as exc:
        P.validate_assessment(doc)
    if needle:
        assert needle in str(exc.value), str(exc.value)


# ───────────────────────────── 1-3. contrato condicionado por version ─────────────────────────────
def test_a_historical_profile_without_the_declared_version_may_omit_canonical_evidence_and_stays_valid(good_leaf):
    old = copy.deepcopy(good_leaf)
    old.pop("canonical_evidence")
    old["versions"].pop("canonical_evidence")
    P.validate_assessment(old)


def test_a_profile_declaring_the_current_version_must_carry_a_valid_payload(good_leaf):
    d = copy.deepcopy(good_leaf)
    d.pop("canonical_evidence")
    _invalid(d, "no trae la seccion")
    d2 = copy.deepcopy(good_leaf)
    d2["canonical_evidence"] = None
    _invalid(d2, "no trae la seccion")


def test_a_payload_without_a_compatible_declared_version_is_invalid_and_an_unknown_version_is_rejected(good_leaf):
    d = copy.deepcopy(good_leaf)
    d["versions"].pop("canonical_evidence")
    _invalid(d, "sin versions.canonical_evidence")
    d2 = copy.deepcopy(good_leaf)
    d2["versions"]["canonical_evidence"] = "canonical-evidence/v9"
    _invalid(d2, "no soportada")
    d3 = copy.deepcopy(good_leaf)
    d3["canonical_evidence"]["version"] = "canonical-evidence/v9"
    _invalid(d3, "no coincide con versions.canonical_evidence")                 # la seccion no puede declarar otra version que el bloque versions
    d4 = copy.deepcopy(good_leaf)
    d4["versions"]["canonical_evidence"] = d4["canonical_evidence"]["version"] = "canonical-evidence/v9"
    _invalid(d4, "no soportada")


def test_unknown_keys_and_missing_sections_inside_the_payload_are_rejected(good_leaf):
    d = copy.deepcopy(good_leaf)
    d["canonical_evidence"]["surprise"] = 1
    _invalid(d, "claves fuera del contrato")
    d2 = copy.deepcopy(good_leaf)
    d2["canonical_evidence"].pop("assembly_topology")
    _invalid(d2, "faltan claves")


# ───────────────────────────── 4-8. vocabularios e invariantes ─────────────────────────────
def test_invalid_provenance_state_and_basis_are_rejected(good_leaf):
    d = copy.deepcopy(good_leaf)
    d["canonical_evidence"]["leaf_provenance"][0]["provenance_state"] = "ASSUMED"
    _invalid(d, "provenance_state invalido")
    d = copy.deepcopy(good_leaf)
    d["canonical_evidence"]["leaf_provenance"][0]["basis"] = "NAME_INFERENCE"
    _invalid(d, "basis invalido")


def test_provenance_invariants_state_basis_producer_and_origin_must_agree(good_leaf):
    for mutate, needle in (
        (lambda l: l.update(producer_flows=[]), "ESTABLISHED exige un productor"),
        (lambda l: l.update(origin=None), "origin"),
        (lambda l: l.update(assessability="NOT_ASSESSED"), "provenance_state nulo"),
        (lambda l: l.update(cause_code="CONTRADICTORY_EVIDENCE"), "cause_code solo para NOT_ASSESSED"),
        (lambda l: l["producer_flows"][0].update(target_match="FUZZY"), "target_match"),
    ):
        d = copy.deepcopy(good_leaf)
        mutate(d["canonical_evidence"]["leaf_provenance"][0])
        _invalid(d, needle)


def test_invalid_derivation_relation_and_reason_are_rejected(good_deriv):
    d = copy.deepcopy(good_deriv)
    d["canonical_evidence"]["derivation_semantics"]["repeated_derivations"][0]["relation"] = "COPIED"
    _invalid(d, "relation invalida")
    d = copy.deepcopy(good_deriv)
    d["canonical_evidence"]["derivation_semantics"]["repeated_derivations"][0]["relation_reason"] = "WHATEVER"
    _invalid(d, "relation_reason incoherente")


def test_not_observable_requires_a_conclusive_scope(good_notobs):
    leaf = good_notobs["canonical_evidence"]["leaf_provenance"][0]
    assert leaf["provenance_state"] == "NOT_OBSERVABLE" and leaf["scope"]["conclusive"] is True
    P.validate_assessment(good_notobs)
    for mutate in (lambda s: s.update(conclusive=False, limitation="SCOPE_NOT_DECLARED", declared=False, producer_families_acquired=[]),
                   lambda s: s.update(producer_families_acquired=["data-flows"])):
        d = copy.deepcopy(good_notobs)
        mutate(d["canonical_evidence"]["leaf_provenance"][0]["scope"])
        _invalid(d)


def test_scope_completeness_invariants(good_notobs):
    for mutate in (lambda s: s.update(limitation="UNREADABLE_POOL_FILES"), lambda s: s.update(limitation="MADE_UP"), lambda s: s.update(declared="yes")):
        d = copy.deepcopy(good_notobs)
        mutate(d["canonical_evidence"]["leaf_provenance"][0]["scope"])
        _invalid(d)


def test_exposed_by_root_requires_its_basis_everywhere_it_is_persisted(good_deriv):
    ds = good_deriv["canonical_evidence"]["derivation_semantics"]
    assert ds["repeated_derivations"][0]["exposure_basis"] == "SAME_NAME_IN_ROOT_CONTRACT"
    assert all(f["exposure_basis"] == "SAME_NAME_IN_ROOT_CONTRACT" for f in ds["fields"])
    d = copy.deepcopy(good_deriv)
    d["canonical_evidence"]["derivation_semantics"]["repeated_derivations"][0].pop("exposure_basis")
    _invalid(d)
    d = copy.deepcopy(good_deriv)
    d["canonical_evidence"]["derivation_semantics"]["fields"][0]["exposure_basis"] = "PROVEN_FIELD_LINEAGE"        # otra base NO es aceptable
    _invalid(d, "SAME_NAME_IN_ROOT_CONTRACT")


def test_the_public_field_relations_payload_carries_the_exposure_basis(good_deriv):
    group = next(g for g in good_deriv["review_required"] if g["id"] == "REPEATED_DERIVATION_IN_LINEAGE")
    for rel in group["occurrences"][0]["values"]["field_relations"]:
        assert rel["exposed_by_root"] is True and rel["exposure_basis"] == "SAME_NAME_IN_ROOT_CONTRACT"


def test_same_name_in_root_contract_is_descriptive_and_never_field_lineage_proof():
    """El AM expone un campo HOMONIMO calculado desde OTRA cosa: ``exposed_by_root`` sigue siendo True (coincidencia de nombre), con su base; no se afirma linaje."""
    root = TD._pool([TD.ref("K"), TD.dosis_base("DOSIS")], [TD.ref("K"), TD.case_sign_flip("DOSIS")],
                    am_cols=[TD.ref("K"), {"as": "DOSIS", "xpr": [TD.ref("K"), "||", TD.val("x")]}])
    rep = TD._repeated(TD._assess(root))[0]
    assert rep["exposed_by_root"] is True and rep["exposure_basis"] == "SAME_NAME_IN_ROOT_CONTRACT"
    assert EC.EXPOSURE_BASIS_SAME_NAME == "SAME_NAME_IN_ROOT_CONTRACT" and "LINEAGE" not in EC.EXPOSURE_BASIS_SAME_NAME
    from tools.catalog.modeling_assessment import derivation
    assert "NO es linaje de campo probado" in open(derivation.__file__, encoding="utf-8").read()


# ───────────────────────────── 9. refinamiento encadenado nunca es duplicacion ─────────────────────────────
def test_chained_refinement_can_never_masquerade_as_duplication(good_deriv):
    d = copy.deepcopy(good_deriv)
    r = d["canonical_evidence"]["derivation_semantics"]["repeated_derivations"][0]
    assert r["relation"] == "CHAINED_REFINEMENT"
    r["relation"] = "DUPLICATED_TRANSFORMATION"                              # relacion cambiada pero la razon sigue siendo la del refinamiento
    _invalid(d, "relation_reason incoherente")
    r["relation_reason"] = "IDENTICAL_STRUCTURE"                             # razon coherente con A, pero el consumidor LEE el campo derivado
    d["canonical_evidence"]["derivation_semantics"]["relation_counts"] = {"DUPLICATED_TRANSFORMATION": 1, "CHAINED_REFINEMENT": 0, "DISTINCT_TRANSFORMATION": 0, "UNKNOWN": 0}
    _invalid(d, "NO lea el campo derivado")
    e = copy.deepcopy(good_deriv)
    e["canonical_evidence"]["derivation_semantics"]["repeated_derivations"][0]["relation_reason"] = "IDENTICAL_STRUCTURE"
    _invalid(e, "relation_reason incoherente")                               # un CHAINED con razon de duplicacion tampoco vale
    f = copy.deepcopy(good_deriv)
    f["canonical_evidence"]["derivation_semantics"]["relation_counts"]["DUPLICATED_TRANSFORMATION"] = 1
    _invalid(f, "relation_counts inconsistente")


def test_a_valid_gate4_payload_is_accepted(good_leaf, good_notobs, good_deriv):
    for d in (good_leaf, good_notobs, good_deriv):
        P.validate_assessment(d)
        EC.validate_canonical_evidence(d["canonical_evidence"])
        from tools.catalog.modeling_assessment.canonical_sections import V2_SECTIONS, V3_SECTIONS
        assert set(d["canonical_evidence"]) == {"version", *EC.EXPECTED_SECTIONS, *V2_SECTIONS, *V3_SECTIONS}
    assert good_deriv["canonical_evidence"]["derivation_semantics"]["relation_counts"]["CHAINED_REFINEMENT"] == 1


def test_the_validator_vocabularies_come_from_the_package_constants_not_from_copies():
    from tools.catalog.modeling_assessment import derivation as D, provenance as PV, readers as RD
    assert EC.PROVENANCE_STATES == {PV.PROV_ESTABLISHED, PV.PROV_DECLARED, PV.PROV_NOT_OBSERVABLE}
    assert EC.RELATION_REASONS is D.RELATION_REASONS and set(D.RELATION_REASONS) == set(D.RELATIONS)
    assert EC.PRODUCER_FAMILIES is RD.PRODUCER_FAMILIES and EC.CANONICAL_EVIDENCE_VERSION == P.CANONICAL_EVIDENCE_VERSION


# ───────────────────────────── 11-17. X_Delta -> X ─────────────────────────────
def _flows(**rfs):
    return {f"Acme/replication-flows/{n}.json": TL._rf(n, tg, ["ZCDS_SRC"] * len(tg)) for n, tg in rfs.items()}


def test_an_exact_target_match_always_wins_over_the_delta_suffix():
    doc = _leafdoc(extra=_flows(RF_A=["LOCAL_X"], RF_B=["LOCAL_X_Delta"]))
    leaf = TL._leaf(doc)
    assert [(p["name"], p["target_match"]) for p in leaf["producer_flows"]] == [("RF_A", "EXACT")] and leaf["provenance_state"] == "ESTABLISHED"


def test_the_delta_suffix_is_tried_only_when_no_exact_match_exists_and_its_basis_is_persisted():
    leaf = TL._leaf(_leafdoc(extra=_flows(RF_B=["LOCAL_X_Delta"])))
    assert leaf["provenance_state"] == "ESTABLISHED" and leaf["producer_flows"][0]["target_match"] == "DELTA_SHADOW_SUFFIX"


def test_the_normalized_candidate_must_be_unique_otherwise_provenance_is_not_established():
    doc = _leafdoc(TL.SCOPE_ALL, _flows(RF_B=["LOCAL_X_Delta"], RF_C=["LOCAL_X_Delta"]))
    leaf = TL._leaf(doc)
    assert leaf["provenance_state"] is None and leaf["assessability"] == "NOT_ASSESSED" and leaf["producer_flows"] == []
    assert leaf["match_ambiguity"] == "MULTIPLE_DELTA_CANDIDATES" and leaf["cause_code"] == "CONTRADICTORY_EVIDENCE"
    P.validate_assessment(doc)                                              # la ambiguedad es una LIMITACION de evidencia explicita, no un valor inventado
    assert any(l["surface_id"] == "source.leaf_provenance" for l in doc["evidence_limitations"])      # y no se vuelve "ausente evaluado" aun con alcance concluyente


def test_an_ambiguous_candidate_cannot_be_forced_into_an_established_record(good_leaf):
    d = _leafdoc(extra=_flows(RF_B=["LOCAL_X_Delta"], RF_C=["LOCAL_X_Delta"]))
    d["canonical_evidence"]["leaf_provenance"][0].update(provenance_state="ESTABLISHED")
    _invalid(d)


def test_a_real_local_entity_named_x_delta_is_not_stolen_by_x():
    doc = _leafdoc(TL.SCOPE_ALL, _flows(RF_D=["LOCAL_X_Delta"]), leaves={"LOCAL_X": None, "LOCAL_X_Delta": None})
    x, xd = TL._leaf(doc, "LOCAL_X"), TL._leaf(doc, "LOCAL_X_Delta")
    assert x["producer_flows"] == [] and x["provenance_state"] == "NOT_OBSERVABLE"                     # el flujo es de X_Delta, no de X
    assert xd["provenance_state"] == "ESTABLISHED" and xd["producer_flows"][0]["target_match"] == "EXACT"


@pytest.mark.parametrize("target", ["LOCAL_X_delta", "LOCAL_X_DELTA", "LOCAL_X_Delta2", "LOCAL_XDelta", "LOCAL_X_Delt", "PRE_LOCAL_X_Delta", "LOCAL_X_Deltas"])
def test_there_is_no_fuzzy_or_general_suffix_matching(target):
    leaf = TL._leaf(_leafdoc(TL.SCOPE_ALL, _flows(RF_Z=[target])))
    assert leaf["producer_flows"] == [] and leaf["provenance_state"] == "NOT_OBSERVABLE"


def test_the_delta_suffix_convention_applies_only_to_replication_flows():
    root = TL._pool(extra={"Acme/data-flows/DF_B.json": TL._df("DF_B", ["SRC"], ["LOCAL_X_Delta"])})
    assert TL._leaf(TL._doc(root, TL.SCOPE_ALL))["producer_flows"] == []


def test_flows_touching_and_target_flows_share_one_matching_algorithm():
    from tools.catalog.modeling_assessment.readers import SupplementaryEvidence
    root = TL._pool(extra=_flows(RF_B=["LOCAL_X_Delta"], RF_C=["LOCAL_X_Delta"]))
    ev = SupplementaryEvidence.from_files(sorted(root.rglob("*.json")), root=root)
    assert ev.target_flows("LOCAL_X", {"LOCAL_X"})[1] == "MULTIPLE_DELTA_CANDIDATES" and ev.flows_touching("LOCAL_X", {"LOCAL_X"}) == ()
    assert ev.target_flows("LOCAL_X", {"LOCAL_X", "LOCAL_X_Delta"}) == ((), None)


# ───────────────────────────── 18-21. compatibilidad del id de senal ─────────────────────────────
def test_signal_id_normalization_is_one_shared_helper_with_parity_in_the_pure_renderer():
    assert M.canonical_signal_id("CROSS_LAYER_REDERIVATION") == "REPEATED_DERIVATION_IN_LINEAGE"
    assert M.canonical_signal_id("REPEATED_DERIVATION_IN_LINEAGE") == "REPEATED_DERIVATION_IN_LINEAGE" and M.canonical_signal_id("OTHER") == "OTHER"
    assert R._LEGACY_SIGNAL_IDS == M.LEGACY_SIGNAL_ALIASES                    # el renderer es puro (no importa el modelo): paridad fijada por test
    assert PR.canonical_signal_id("CROSS_LAYER_REDERIVATION") == "REPEATED_DERIVATION_IN_LINEAGE"


def _as_old_profile(doc):
    old = copy.deepcopy(doc)
    old.pop("canonical_evidence")
    old["versions"].pop("canonical_evidence")
    for g in old["review_required"]:
        if g["id"] == "REPEATED_DERIVATION_IN_LINEAGE":
            g["id"], g["title"] = "CROSS_LAYER_REDERIVATION", "Campo derivado de nuevo en otra capa"
            for o in g["occurrences"]:
                o["values"].pop("field_relations", None)
    for r in old["rule_results"]:
        if r["rule"] == "REPEATED_DERIVATION_IN_LINEAGE":
            r["rule"], r["title"] = "CROSS_LAYER_REDERIVATION", "Campo derivado de nuevo en otra capa"
    for a in old["areas"]:
        a["finding_groups"] = ["CROSS_LAYER_REDERIVATION" if g == "REPEATED_DERIVATION_IN_LINEAGE" else g for g in a["finding_groups"]]
    return old


def test_an_old_cross_layer_profile_loads_unchanged_on_disk_and_renders_with_the_current_label(good_deriv):
    old = _as_old_profile(good_deriv)
    before = json.dumps(old, sort_keys=True)
    P.validate_assessment(old)
    md = R.render_markdown(old)
    assert "Derivación repetida en el linaje" in md and "Campo derivado de nuevo en otra capa" not in md
    assert R.display_title("CROSS_LAYER_REDERIVATION", "Campo derivado de nuevo en otra capa") == "Derivación repetida en el linaje"
    assert json.dumps(old, sort_keys=True) == before                          # la normalizacion es de LECTURA: el perfil persistido no se modifica


def test_consumers_normalize_before_semantic_comparison():
    from webapp.services import assessment_presenter as presenter
    assert presenter.canonical_signal_id("CROSS_LAYER_REDERIVATION") == presenter.canonical_signal_id("REPEATED_DERIVATION_IN_LINEAGE")


def test_new_profiles_persist_only_the_canonical_id(good_deriv):
    text = json.dumps(good_deriv)
    assert "CROSS_LAYER_REDERIVATION" not in text and "rule.cross_layer_rederivation" not in text
    assert any(g["id"] == "REPEATED_DERIVATION_IN_LINEAGE" for g in good_deriv["review_required"])
    assert any(r["rule"] == "REPEATED_DERIVATION_IN_LINEAGE" for r in good_deriv["rule_results"])
