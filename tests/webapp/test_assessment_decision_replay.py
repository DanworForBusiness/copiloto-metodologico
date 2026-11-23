"""P0.1 — un REEMPLAZO del sobre gobernado (generate / publicación / enriquecimiento) reaplica, VERIFICADAS, las decisiones de gobierno que el Juicio vigente
consumió, o se ABSTIENE sin escribir. Nunca descubre ni agrega decisiones; consulta el registro SOLO para los contratos consumidos (vigencia/revocación).

Caso testigo: Biogenesis SELL_OUT en producción — `generate()` derivaba el Juicio sin decisiones y una ratificación vigente (ADECUADA/LVL-03) se perdía (ND/LVL-05)."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from core.remote_store import RemoteReadError
from tests.webapp.test_governed_ratification_link import CLIENT, CRITICAL, NOW, gp, leaf_rec, ratified, revoked, store
from webapp.services import assessment_service as svc
from webapp.services import governed_decisions_store as gds
from webapp.services import judgment_decisions as jd

def _decisive(j):
    return ((j or {}).get("status"), (j or {}).get("level"), (j or {}).get("level_rule"))


@pytest.fixture
def world(monkeypatch):
    """Perfil con 4 hojas decision-críticas ratificadas + Juicio PERSISTIDO derivado por el operador con la instantánea (como en producción)."""
    p = gp()
    events = [ratified(p, leaf, event_id=f"gd-{leaf}") for leaf in CRITICAL]
    snap = jd.Snapshot(jd.MODE_REGISTRY, NOW, {CLIENT: tuple(events)})
    persisted, _rep = jd.derive(p, CLIENT, snap)
    reads: list[str] = []

    def load(client, tid):
        reads.append(tid)
        return store(*state["events"])(client, tid)

    state = {"events": list(events), "reads": reads}
    monkeypatch.setattr(gds, "load_events", load)
    previous = {"client": CLIENT, "terminal_am": "AM_SELLOUT_GP", "judgment": persisted,
                "previous_judgment": {"status": "NOT_DETERMINABLE", "level": None, "level_rule": "LVL-05", "ruleset_version": "judgment-rules/v4"}}
    return SimpleNamespace(profile=p, events=events, persisted=persisted, previous=previous, state=state)


# ───────────── contrato del replay ─────────────
def test_the_witness_case_without_decisions_loses_the_level_and_the_verified_replay_preserves_it(world):
    assert _decisive(world.persisted) == ("DETERMINED", "ADECUADA", "LVL-03")
    assert _decisive(svc._derive_judgment(world.profile)) == ("NOT_DETERMINABLE", None, "LVL-05")          # lo que escribía generate()
    judgment, carried = svc.judgment_for_replacement(copy.deepcopy(world.profile), world.previous, now=NOW)
    assert judgment["output_signature"] == world.persisted["output_signature"]                            # mismo Juicio, mismas decisiones
    assert [i["event_id"] for i in jd.consumed(judgment)] == [i["event_id"] for i in jd.consumed(world.persisted)]
    assert carried == {"previous_judgment": world.previous["previous_judgment"]}                          # se conserva tal cual (no se redefine)


def test_only_the_consumed_contracts_are_read_and_no_new_decision_is_discovered(world):
    p = world.profile
    extra = ratified(p, "ART_0016", event_id="gd-extra") if any(r["leaf"] == "ART_0016" for r in p["canonical_evidence"]["source_contract_classes"]["leaves"]) else None
    if extra:
        world.state["events"].append(extra)
    judgment, _ = svc.judgment_for_replacement(copy.deepcopy(p), world.previous, now=NOW)
    consumed_tids = {i["technical_id"] for i in jd.consumed(world.persisted)}
    assert set(world.state["reads"]) == consumed_tids                                                     # solo los contratos consumidos
    assert {i["event_id"] for i in jd.consumed(judgment)} == {i["event_id"] for i in jd.consumed(world.persisted)}


@pytest.mark.parametrize("mutate, code", [
    (lambda p: leaf_rec(p, CRITICAL[0]).__setitem__("contract_class", "GOVERNED_SAP_APPLICATION_API"), "RATIFIED_LEAF_RECLASSIFIED"),
    (lambda p: leaf_rec(p, CRITICAL[0]).__setitem__("leaf_kind", "LOCAL_TABLE"), "RATIFIED_LEAF_RECLASSIFIED"),
    (lambda p: leaf_rec(p, CRITICAL[0]).__setitem__("remote_connection", "OTRA_CONEXION"), "RATIFIED_IDENTITY_CHANGED"),
    (lambda p: leaf_rec(p, CRITICAL[0]).__setitem__("classification_bases", ["OTRA_BASE"]), "RATIFIED_EVIDENCE_CHANGED"),
    (lambda p: p["canonical_evidence"]["source_contract_classes"].__setitem__(
        "leaves", [r for r in p["canonical_evidence"]["source_contract_classes"]["leaves"] if r["leaf"] != CRITICAL[0]]), "RATIFIED_LEAF_MISSING"),
])
def test_a_ratification_whose_contract_no_longer_holds_makes_the_replacement_abstain(world, mutate, code):
    p = copy.deepcopy(world.profile)
    mutate(p)
    with pytest.raises(svc.ReplacementAbstained) as exc:
        svc.judgment_for_replacement(p, world.previous, now=NOW)
    assert exc.value.code == code


def test_a_revoked_consumed_decision_makes_the_replacement_abstain(world):
    world.state["events"].append(revoked(world.profile, CRITICAL[0], revokes=f"gd-{CRITICAL[0]}", event_id="gd-rev"))
    with pytest.raises(svc.ReplacementAbstained) as exc:
        svc.judgment_for_replacement(copy.deepcopy(world.profile), world.previous, now=NOW)
    assert exc.value.code == "RATIFICATION_NOT_ACTIVE"


def test_a_newer_ratification_that_supersedes_the_consumed_one_is_not_incorporated_the_replacement_abstains(world):
    world.state["events"].append(ratified(world.profile, CRITICAL[0], event_id="gd-newer", decided_at="2026-10-05T00:00:00Z"))
    with pytest.raises(svc.ReplacementAbstained) as exc:
        svc.judgment_for_replacement(copy.deepcopy(world.profile), world.previous, now=NOW)
    assert exc.value.code == "RATIFICATION_NOT_ACTIVE"


def test_an_unreadable_registry_makes_the_replacement_abstain(world, monkeypatch):
    def boom(*_a, **_k):
        raise RemoteReadError("lectura remota no autorizada")
    monkeypatch.setattr(gds, "load_events", boom)
    with pytest.raises(svc.ReplacementAbstained) as exc:
        svc.judgment_for_replacement(copy.deepcopy(world.profile), world.previous, now=NOW)
    assert exc.value.code == "DECISION_STATUS_UNVERIFIABLE"


@pytest.mark.parametrize("key, code", [("judgment_review", "DECISION_PENDING_REEVALUATION"), ("judgment_candidate", "RULESET_ACTIVATION_IN_PROGRESS")])
def test_a_pending_reevaluation_or_an_activation_in_progress_makes_the_replacement_abstain(world, key, code):
    with pytest.raises(svc.ReplacementAbstained) as exc:
        svc.judgment_for_replacement(copy.deepcopy(world.profile), {**world.previous, key: {"state": "X"}}, now=NOW)
    assert exc.value.code == code


def test_creation_and_envelopes_without_consumed_decisions_never_read_the_registry(world, monkeypatch):
    monkeypatch.setattr(gds, "load_events", lambda *_a, **_k: pytest.fail("no debe leer el registro"))
    j_new, carried_new = svc.judgment_for_replacement(world.profile, None)
    assert _decisive(j_new) == ("NOT_DETERMINABLE", None, "LVL-05") and carried_new == {}                # creación: como siempre
    plain = {"client": CLIENT, "judgment": svc._derive_judgment(world.profile), "previous_judgment": {"level": "PARCIAL"}}
    j_plain, carried_plain = svc.judgment_for_replacement(world.profile, plain)
    assert j_plain["output_signature"] == plain["judgment"]["output_signature"] and carried_plain == {"previous_judgment": {"level": "PARCIAL"}}


# ───────────── cableado: generate / publicación / enriquecimiento ─────────────
@pytest.fixture
def wired(world, monkeypatch):
    """`generate()` real sobre un STALE con el motor y el almacén sustituidos (sin pool ni disco): solo el cableado del reemplazo."""
    entry = SimpleNamespace(entry_id="flow-gp", cliente=CLIENT, dominio="", artefactos=["AM_SELLOUT_GP"])
    res = svc.Resolution("OK", CLIENT, "AM_SELLOUT_GP", CLIENT, "d", "sig-new", None)
    stored: list[dict] = []
    stale = svc.AssessmentStatus(svc.STALE, entry.entry_id, CLIENT, "AM_SELLOUT_GP")
    doc = copy.deepcopy(world.profile)
    doc.setdefault("scope", {})["pool_signature"] = "sig-new"
    monkeypatch.setattr(svc, "resolve_entry", lambda e: res)
    monkeypatch.setattr(svc, "_status_from", lambda e, r, v: stale)
    monkeypatch.setattr(svc, "_load_envelope", lambda c, a: world.previous)
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: ({**doc, "assessment_state": "ASSESSED", "versions": {}}, {}))
    monkeypatch.setattr(svc.ma_profile, "validate_assessment", lambda d: None)
    monkeypatch.setattr(svc, "_derive_domain_classification", lambda e, a: None)
    monkeypatch.setattr(svc, "_derive_business_classification", lambda e, a: None)
    monkeypatch.setattr(svc, "closure_signature", lambda r: None)
    monkeypatch.setattr(svc, "_store_envelope", lambda env, **k: stored.append(env) or "local")
    return SimpleNamespace(entry=entry, res=res, stored=stored, world=world)


def test_generate_persists_the_replayed_judgment_and_carries_previous_judgment(wired, monkeypatch):
    monkeypatch.setattr(svc, "judgment_for_replacement", lambda doc, prev, now=None, _f=svc.judgment_for_replacement: _f(doc, prev, now=NOW))
    svc._generate_locked(wired.entry, wired.res, {})
    assert len(wired.stored) == 1
    env = wired.stored[0]
    assert _decisive(env["judgment"]) == ("DETERMINED", "ADECUADA", "LVL-03")
    assert env["previous_judgment"] == wired.world.previous["previous_judgment"]


def test_generate_abstains_without_writing_and_reports_the_cause(wired):
    wired.world.state["events"].append(revoked(wired.world.profile, CRITICAL[0], revokes=f"gd-{CRITICAL[0]}", event_id="gd-rev"))
    out = svc._generate_locked(wired.entry, wired.res, {})
    assert wired.stored == []                                                                             # NADA escrito: ni OK ni FAILED
    assert out.state == svc.STALE and out.error_code == "RATIFICATION_NOT_ACTIVE" and out.reason


def test_a_generation_failure_keeps_the_persisted_judgment_and_its_traces(wired, monkeypatch):
    prev = {**wired.world.previous, "status": "OK", "assessment_schema_version": svc.ma_profile.ASSESSMENT_SCHEMA_VERSION, "profile_json": wired.world.profile,
            "judgment_review": {"state": "PENDING_REEVALUATION"}}
    monkeypatch.setattr(svc, "_load_envelope", lambda c, a: prev)
    monkeypatch.setattr(svc, "_valid_profile", lambda env: True)
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("motor")))
    svc._generate_locked(wired.entry, wired.res, {})
    env = wired.stored[0]
    assert env["status"] == "FAILED" and env["judgment"] is prev["judgment"]                              # no se re-deriva sin decisiones
    assert env["previous_judgment"] == prev["previous_judgment"] and env["judgment_review"] == prev["judgment_review"]


def test_publication_prepare_validates_before_writing_and_abstains_as_a_prepare_error(wired, monkeypatch):
    monkeypatch.setattr(svc, "current_assessment_versions", lambda: {})
    monkeypatch.setattr(svc, "_valid_profile", lambda env: True)
    monkeypatch.setattr(svc, "judgment_for_replacement", lambda doc, prev, now=None, _f=svc.judgment_for_replacement: _f(doc, prev, now=NOW))
    prep = svc.prepare_regeneration(wired.entry, wired.res)
    assert _decisive(prep.envelope["judgment"]) == ("DETERMINED", "ADECUADA", "LVL-03") and prep.envelope["previous_judgment"]
    wired.world.state["events"].append(revoked(wired.world.profile, CRITICAL[0], revokes=f"gd-{CRITICAL[0]}", event_id="gd-rev"))
    with pytest.raises(svc.PrepareError) as exc:
        svc.prepare_regeneration(wired.entry, wired.res)
    assert exc.value.code == "RATIFICATION_NOT_ACTIVE" and wired.stored == []
