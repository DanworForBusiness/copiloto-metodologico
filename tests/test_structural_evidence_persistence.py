"""v7 — persistencia GOBERNADA del sidecar `structural-evidence/v1` (`join-key-profile/v1`) en el sobre y su consumo VERIFICADO por el Juicio v7.
Persistencia y readopción · ida y vuelta JSON · ausente · digest alterado · otro perfil / otro modelo · obsoleto · sobres históricos · v2–v6 intactos.
Perfiles y bloques SINTÉTICOS (sin datos de clientes)."""
from __future__ import annotations

import copy
import json

import pytest

from tests.test_methodological_judgment_v7_structural import _jkp, _profile
from tools.catalog.methodological_judgment import contract as C
from webapp.services import assessment_service as asvc
from webapp.services import judgment_decisions as jd
from webapp.services import structural_evidence as SE

AM = "AM"


def _meta(profile, block):
    return {"evidence_extensions": {"join_key_profile": block, "profile_binding": {"method": SE.BINDING_METHOD, "digest": asvc._artifact_signature(profile)}}}


def _setup(ruleset=C.RULESET_V7, **kw):
    p = _profile()
    blk = _jkp(vf_keys=("K",), **kw)["join_key_profile"]
    sc = SE.build_sidecar(_meta(p, blk), p, AM, "sig")
    return p, blk, sc


@pytest.fixture
def v7(monkeypatch):
    monkeypatch.setattr(asvc, "active_ruleset", lambda: C.RULESET_V7)


def _env(p, sc, **kw):
    return asvc._envelope("CLI", AM, status="OK", profile=p, pool_signature="sig", versions={}, structural_evidence=sc, **kw)


def _dims(j):
    return {d["dimension"]: d["state"] for d in j.get("dimension_states") or ()}


# ─── persistencia y readopción ───
def test_sidecar_carries_identity_provenance_version_digest_and_binding():
    p, blk, sc = _setup()
    assert sc["version"] == SE.SIDECAR_VERSION and sc["terminal_am"] == AM and sc["provenance"] == {"source": "CSN_POOL", "pool_signature": "sig"}
    assert sc["join_key_profile"]["version"] == "join-key-profile/v1" and sc["block_digest"] == SE.block_digest(blk)
    assert sc["binding"] == {"method": SE.BINDING_METHOD, "artifact_signature": asvc._artifact_signature(p)}
    assert "structural_evidence" not in p and "join_key_profile" not in json.dumps(p)          # FUERA del perfil: no es otra fuente de verdad ni toca sus firmas


def test_envelope_persists_and_v7_judgment_consumes_the_bound_sidecar(v7):
    p, _blk, sc = _setup()
    env = _env(p, sc)
    assert env["structural_evidence"] == sc and env["assessment_artifact_signature"] == sc["binding"]["artifact_signature"]
    j = env["judgment"]
    assert j["ruleset_version"] == C.RULESET_V7 and j["structural_evidence"]["e3"]["state"] == "DEMONSTRATED" and j["structural_evidence"]["e5"]["state"] == "DEFICIENT"
    assert _dims(j)[C.HARMONIZATION] == C.DS_NOT_APPLICABLE and "PUB-LOC-02" in {r["rule_id"] for r in j["reasons"]}


def test_round_trip_serialization_readopts_the_same_evidence_and_judgment(v7):
    p, _blk, sc = _setup()
    env = _env(p, sc)
    back = json.loads(json.dumps(env, ensure_ascii=False))
    assert SE.for_engine(back) == SE.for_engine(env) is not None
    again = asvc._derive_judgment(back["profile_json"], structural=asvc.structural_input(back))
    assert again["output_signature"] == env["judgment"]["output_signature"]
    assert asvc.classify_judgment_revalidation(back)[0] == asvc.JDG_CURRENT                   # revalidar no ve «divergencia» por la evidencia estructural


def test_classify_judgment_decisions_reads_the_verified_sidecar_only_for_v7(v7):
    p, _blk, sc = _setup()
    env = _env(p, sc)
    assert asvc.classify_judgment_decisions(env, ruleset=C.RULESET_V7)["action"] == asvc.JDG_CURRENT
    f6 = asvc.classify_judgment_decisions(env, ruleset=C.RULESET_V6)["fresh"]
    assert "structural_evidence" not in f6


def test_replacement_carries_the_structural_input_of_the_new_profile(v7):
    p, _blk, sc = _setup()
    _sc, se_in = asvc._structural_for(_meta(p, sc["join_key_profile"]), p, AM, "sig")
    j, _ = asvc.judgment_for_replacement(p, None, structural=se_in)
    assert j["structural_evidence"]["e3"]["state"] == "DEMONSTRATED"


# ─── rechazos: el Juicio v7 se ABSTIENE (E3/E5 sin efecto) ───
def _abstains(env):
    base = asvc._derive_judgment(env["profile_json"])                                         # v7 sin evidencia estructural
    j = asvc._derive_judgment(env["profile_json"], structural=asvc.structural_input(env))
    return asvc.structural_input(env) is None and j == base and "structural_evidence" not in j


def test_absent_sidecar_and_historical_envelopes_keep_their_shape_and_meaning(v7):
    p = _profile()
    env = _env(p, None)
    assert "structural_evidence" not in env                                                    # sobre histórico: misma forma de siempre
    assert SE.verify(None, p, AM) == (None, "ABSENT") and _abstains(env)
    assert SE.build_sidecar({}, p, AM, "sig") is None and SE.build_sidecar(None, p, AM, "sig") is None


@pytest.mark.parametrize("mutate,why", [
    (lambda sc, p: sc["join_key_profile"]["keys"]["VF"].__setitem__("declared", ["K", "ITEM"]), "DIGEST_MISMATCH"),     # alterado
    (lambda sc, p: sc.__setitem__("terminal_am", "OTRO_AM"), "OTHER_MODEL"),                                         # otro modelo
    (lambda sc, p: sc.__setitem__("version", "structural-evidence/v0"), "VERSION_UNKNOWN"),
    (lambda sc, p: sc["binding"].__setitem__("artifact_signature", "0" * 64), "PROFILE_MISMATCH"),                   # otro perfil
    (lambda sc, p: (sc["join_key_profile"].__setitem__("version", "join-key-profile/v9"),
                    sc.__setitem__("block_digest", SE.block_digest(sc["join_key_profile"]))), "BLOCK_VERSION_UNKNOWN"),
    (lambda sc, p: (sc["join_key_profile"]["views"].__setitem__("AJENA", {}),
                    sc.__setitem__("block_digest", SE.block_digest(sc["join_key_profile"]))), "OUTSIDE_CLOSURE"),      # describe artefactos fuera del cierre
])
def test_tampered_or_foreign_evidence_is_rejected_and_v7_abstains(v7, mutate, why):
    p, _blk, sc = _setup()
    env = _env(p, None)
    bad = copy.deepcopy(sc)
    mutate(bad, p)
    env["structural_evidence"] = bad
    assert SE.verify(bad, p, AM, env["assessment_artifact_signature"])[1] == why
    assert _abstains(env)


def test_obsolete_sidecar_of_a_previous_profile_is_rejected(v7):
    p_old, _blk, sc_old = _setup()
    p_new = copy.deepcopy(p_old)
    p_new["scope"]["pool_signature"] = "sig2"                                                 # el perfil cambió (regeneración) y el sidecar quedó atrás
    env = _env(p_new, None)
    env["structural_evidence"] = sc_old
    assert SE.verify(sc_old, p_new, AM, env["assessment_artifact_signature"])[1] == "PROFILE_MISMATCH" and _abstains(env)


def test_sidecar_of_another_evaluation_is_never_persisted():
    p, blk, _sc = _setup()
    other = copy.deepcopy(p)
    other["scope"]["pool_signature"] = "otra"
    assert SE.build_sidecar(_meta(other, blk), p, AM, "sig") is None                          # vínculo del ciclo ≠ perfil a persistir


def test_envelope_signature_must_match_the_profile_too(v7):
    p, _blk, sc = _setup()
    env = _env(p, sc)
    env["assessment_artifact_signature"] = "f" * 64
    assert asvc.structural_input(env) is None


# ─── v2–v6 intactos ───
def test_v2_engine_ignores_the_structural_input():
    from tools.catalog.methodological_judgment import evaluate_judgment
    p, blk, _sc = _setup()
    assert evaluate_judgment(copy.deepcopy(p), ruleset=C.RULESET_V2, structural_evidence={"join_key_profile": blk}) == evaluate_judgment(copy.deepcopy(p), ruleset=C.RULESET_V2)


@pytest.mark.parametrize("rs", [C.RULESET_V3, C.RULESET_V4, C.RULESET_V5, C.RULESET_V6])
def test_v3_to_v6_never_receive_the_structural_input(rs):
    p, blk, _sc = _setup()
    s = {"join_key_profile": blk}
    assert asvc._derive_judgment(p, ruleset=rs, structural=s) == asvc._derive_judgment(p, ruleset=rs)
    snap = jd.empty_snapshot()
    assert jd.derive(p, "CLI", snap, ruleset=rs, structural_evidence=s)[0] == jd.derive(p, "CLI", snap, ruleset=rs)[0]
