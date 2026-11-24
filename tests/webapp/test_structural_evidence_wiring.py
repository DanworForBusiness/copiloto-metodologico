"""v7 — CABLEADO de `structural-evidence/v1` en los PUNTOS DE ESCRITURA reales del sobre gobernado (motor y almacén sustituidos; sin pool ni disco):
generate (OK) · FAILED (perfil retenido) · publicación (`prepare_regeneration`) · `_regenerate_core` (sin gancho y con gancho).
Comprueba persistencia consistente y que una regeneración con evidencia AUSENTE u OBSOLETA NO reutiliza el sidecar anterior. Perfiles sintéticos."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from tests.test_methodological_judgment_v7_structural import _jkp, _profile
from tools.catalog.methodological_judgment import contract as C
from webapp.services import assessment_service as svc
from webapp.services import structural_evidence as SE

CLIENT, AM = "CLI", "AM"


def _doc(pool="sig"):
    d = _profile()
    d["scope"]["pool_signature"] = pool
    return {**d, "assessment_state": "ASSESSED", "versions": {}}


def _meta(doc, bound_to=None):
    blk = _jkp(vf_keys=("K",))["join_key_profile"]
    return {"evidence_extensions": {"join_key_profile": blk,
                                    "profile_binding": {"method": SE.BINDING_METHOD, "digest": svc._artifact_signature(bound_to if bound_to is not None else doc)}}}


@pytest.fixture
def wired(monkeypatch):
    entry = SimpleNamespace(entry_id="flow", cliente=CLIENT, dominio="", artefactos=[AM])
    res = svc.Resolution("OK", CLIENT, AM, CLIENT, "d", "sig", None)
    w = SimpleNamespace(entry=entry, res=res, stored=[], previous=None, engine=None)
    monkeypatch.setattr(svc, "resolve_entry", lambda e: res)
    monkeypatch.setattr(svc, "_status_from", lambda e, r, v: svc.AssessmentStatus(svc.STALE, entry.entry_id, CLIENT, AM))
    monkeypatch.setattr(svc, "_load_envelope", lambda c, a: w.previous)
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: w.engine())
    monkeypatch.setattr(svc.ma_profile, "validate_assessment", lambda d: None)
    monkeypatch.setattr(svc, "_derive_domain_classification", lambda e, a: None)
    monkeypatch.setattr(svc, "_derive_business_classification", lambda e, a: None)
    monkeypatch.setattr(svc, "closure_signature", lambda r: None)
    monkeypatch.setattr(svc, "current_assessment_versions", lambda: {})
    monkeypatch.setattr(svc, "_valid_profile", lambda env: True)
    monkeypatch.setattr(svc, "_envelope_resolves_current", lambda env, r, v: True)
    monkeypatch.setattr(svc, "_store_envelope", lambda env, **k: w.stored.append(env) or "local")
    return w


def _bound(env):
    sc = env.get("structural_evidence")
    return sc is not None and SE.verify(sc, env["profile_json"], AM, env["assessment_artifact_signature"])[1] == "VERIFIED"


def _write(w, point):
    """Ejecuta UN punto de escritura y devuelve el sobre resultante."""
    if point == "generate":
        svc._generate_locked(w.entry, w.res, {})
        return w.stored[-1]
    if point == "publication":
        return svc.prepare_regeneration(w.entry, w.res).envelope
    hook = None
    if point == "regenerate_hook":
        def hook(doc, previous, **kw):
            return {"judgment": svc._derive_judgment(doc, **({"structural": kw["structural"]} if "structural" in kw else {}))}, {}
    return svc._regenerate_core(w.entry, precondition=lambda *a: None, postcheck=lambda *a: None, log_label="t", envelope_hook=hook).envelope


POINTS = ["generate", "publication", "regenerate", "regenerate_hook"]


@pytest.mark.parametrize("point", POINTS)
@pytest.mark.parametrize("rs", [C.RULESET_V5, C.RULESET_V7])
def test_every_write_point_persists_the_bound_sidecar_consistently(wired, monkeypatch, point, rs):
    monkeypatch.setattr(svc, "active_ruleset", lambda: rs)
    doc = _doc()
    wired.engine = lambda: (copy.deepcopy(doc), _meta(doc))
    env = _write(wired, point)
    assert _bound(env) and env["structural_evidence"]["provenance"]["pool_signature"] == "sig"
    j = env["judgment"]
    if rs == C.RULESET_V7:
        assert j["structural_evidence"]["e3"]["state"] == "DEMONSTRATED" and j["structural_evidence"]["e5"]["state"] == "DEFICIENT"
    else:
        assert "structural_evidence" not in j and j == svc._derive_judgment(env["profile_json"], ruleset=rs)   # v5: persistida pero NUNCA consumida


@pytest.mark.parametrize("point", POINTS)
@pytest.mark.parametrize("case", ["absent", "obsolete"])
def test_regeneration_never_silently_reuses_a_previous_sidecar(wired, monkeypatch, point, case):
    monkeypatch.setattr(svc, "active_ruleset", lambda: C.RULESET_V7)
    old = _doc(pool="sig-old")
    old_sc = SE.build_sidecar(_meta(old), old, AM, "sig-old")
    wired.previous = {"client": CLIENT, "terminal_am": AM, "judgment": None, "profile_json": old, "structural_evidence": old_sc,
                      "assessment_artifact_signature": svc._artifact_signature(old), "input_pool_signature": "sig-old"}
    doc = _doc()
    meta = {} if case == "absent" else _meta(doc, bound_to=old)                       # obsoleta: el bloque del ciclo está ligado a OTRO perfil
    wired.engine = lambda: (copy.deepcopy(doc), meta)
    env = _write(wired, point)
    assert "structural_evidence" not in env                                              # ni el del ciclo (no ligado) ni el anterior
    assert "structural_evidence" not in env["judgment"] and svc.structural_input(env) is None


def test_failed_keeps_the_retained_profile_and_its_still_bound_sidecar(wired, monkeypatch):
    monkeypatch.setattr(svc, "active_ruleset", lambda: C.RULESET_V7)
    old = _doc()
    sc = SE.build_sidecar(_meta(old), old, AM, "sig")
    wired.previous = {"client": CLIENT, "terminal_am": AM, "status": "OK", "assessment_schema_version": svc.ma_profile.ASSESSMENT_SCHEMA_VERSION,
                      "profile_json": old, "judgment": {"level": "X"}, "structural_evidence": sc, "input_pool_signature": "sig",
                      "assessment_versions": {}, "assessment_artifact_signature": svc._artifact_signature(old)}
    wired.engine = lambda: (_ for _ in ()).throw(RuntimeError("motor"))
    svc._generate_locked(wired.entry, wired.res, {})
    env = wired.stored[-1]
    assert env["status"] == "FAILED" and env["structural_evidence"] == sc and _bound(env) and env["judgment"] == {"level": "X"}
