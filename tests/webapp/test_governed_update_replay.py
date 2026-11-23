"""PR #320 fase A — la actualización gobernada desde el Hub (dry-run Y ejecución) regenera con el Juicio por REPLAY VERIFICADO (contrato P0.1,
``judgment_for_replacement`` de #323) y no con una instantánea vacía: no pierde las decisiones consumidas, no descubre otras, se ABSTIENE si alguna ya
no vale. El CLI firmado (``--decisions registry|none``) y las migraciones firmadas conservan su semántica."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from core.remote_store import RemoteReadError
from tests.webapp.test_governed_ratification_link import CLIENT, CRITICAL, NOW, leaf_rec, ratified, revoked
from tests.webapp.test_assessment_decision_replay import world  # noqa: F401 — fixture compartida (perfil + 4 ratificaciones decisivas + registro en memoria)
from webapp.services import assessment_service as svc
from webapp.services import governed_decisions_store as gds
from webapp.services import judgment_decisions as jd


def _decisive(j):
    return ((j or {}).get("status"), (j or {}).get("level"), (j or {}).get("level_rule"))


@pytest.fixture
def core(world, monkeypatch):  # noqa: F811
    """`_regenerate_core` real con el motor y el almacén sustituidos: previo CURRENT (OK, mismo pool y versiones) con el Juicio PERSISTIDO con decisiones."""
    res = svc.Resolution("OK", CLIENT, "AM_X", CLIENT, "d", "sig", None)
    previous = {**world.previous, "status": "OK", "input_pool_signature": "sig", "assessment_versions": dict(world.profile.get("versions") or {}), "profile_json": world.profile,
                "domain_classification": {"d": 1}, "business_classification": {"b": 1}}
    state = {"previous": previous, "doc": copy.deepcopy(world.profile)}
    monkeypatch.setattr(svc, "resolve_entry", lambda e: res)
    monkeypatch.setattr(svc, "current_assessment_versions", lambda: dict(world.profile.get("versions") or {}))
    monkeypatch.setattr(svc, "_load_envelope", lambda c, a: state["previous"])
    monkeypatch.setattr(svc, "_readable_previous", lambda p: True)
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: ({**state["doc"], "assessment_state": "ASSESSED",
                                                                 "scope": {**(state["doc"].get("scope") or {}), "pool_signature": "sig"}}, {}))
    monkeypatch.setattr(svc.ma_profile, "validate_assessment", lambda d: None)
    monkeypatch.setattr(svc, "closure_identity", lambda p: ("closure",))
    monkeypatch.setattr(svc, "_envelope_resolves_current", lambda env, r, v: True)
    monkeypatch.setattr(svc, "_store_envelope", lambda *a, **k: pytest.fail("la regeneración en memoria no escribe"))
    monkeypatch.setattr(svc, "judgment_for_replacement", lambda doc, prev, now=None, _f=svc.judgment_for_replacement: _f(doc, prev, now=NOW))
    return SimpleNamespace(world=world, state=state, entry=SimpleNamespace(entry_id="flow-x"))


# ───────────── selectiva (CURRENT): Hub = replay verificado; CLI = instantánea explícita, sin cambios ─────────────
def test_hub_selective_regeneration_keeps_the_level_its_decisions_and_previous_judgment(core):
    cand = svc.regenerate_current_evidence_replay(core.entry)
    assert _decisive(cand.envelope["judgment"]) == ("DETERMINED", "ADECUADA", "LVL-03")
    assert [i["event_id"] for i in jd.consumed(cand.envelope["judgment"])] == [i["event_id"] for i in jd.consumed(core.world.persisted)]   # las 4 decisiones
    assert cand.envelope["previous_judgment"] == core.world.previous["previous_judgment"]
    assert (cand.notes["new_judgment_level"], cand.notes["new_judgment_rule"]) == ("ADECUADA", "LVL-03")   # el plan proyecta el MISMO nivel (no una degradación)
    assert (cand.envelope["domain_classification"], cand.envelope["business_classification"]) == ({"d": 1}, {"b": 1})


def test_the_signed_cli_semantics_are_unchanged_empty_snapshot_has_no_decisions_and_no_carried_keys(core):
    cand = svc.regenerate_current_evidence(core.entry, jd.empty_snapshot())        # `--decisions none`: explícito del operador
    assert _decisive(cand.envelope["judgment"]) == ("NOT_DETERMINABLE", None, "LVL-05") and "previous_judgment" not in cand.envelope
    snap = jd.Snapshot(jd.MODE_REGISTRY, NOW, {CLIENT: tuple(core.world.events)})  # `--decisions registry`: enlaza la instantánea, como siempre
    assert _decisive(svc.regenerate_current_evidence(core.entry, snap).envelope["judgment"]) == ("DETERMINED", "ADECUADA", "LVL-03")


@pytest.mark.parametrize("setup, code", [
    (lambda w, s: w.state["events"].append(revoked(w.profile, CRITICAL[0], revokes=f"gd-{CRITICAL[0]}", event_id="gd-rev")), "RATIFICATION_NOT_ACTIVE"),
    (lambda w, s: w.state["events"].append(ratified(w.profile, CRITICAL[0], event_id="gd-newer", decided_at="2026-10-05T00:00:00Z")), "RATIFICATION_NOT_ACTIVE"),
    (lambda w, s: leaf_rec(s["doc"], CRITICAL[0]).__setitem__("classification_bases", ["OTRA_BASE"]), "RATIFIED_EVIDENCE_CHANGED"),
    (lambda w, s: leaf_rec(s["doc"], CRITICAL[0]).__setitem__("contract_class", "GOVERNED_SAP_APPLICATION_API"), "RATIFIED_LEAF_RECLASSIFIED"),
])
def test_hub_selective_regeneration_abstains_when_a_consumed_decision_no_longer_holds(core, setup, code):
    setup(core.world, core.state)
    with pytest.raises(svc.EnrichmentError) as exc:
        svc.regenerate_current_evidence_replay(core.entry)
    assert exc.value.code == code


def test_hub_selective_regeneration_abstains_when_the_registry_cannot_be_read(core, monkeypatch):
    monkeypatch.setattr(gds, "load_events", lambda *a, **k: (_ for _ in ()).throw(RemoteReadError("lectura remota no autorizada")))
    with pytest.raises(svc.EnrichmentError) as exc:
        svc.regenerate_current_evidence_replay(core.entry)
    assert exc.value.code == "DECISION_STATUS_UNVERIFIABLE"


# ───────────── migración de evidencia (v6 pendiente): ganchos firmados intactos + verificación P0.1 del candidato ─────────────
def _stale_candidate(core, judgment):
    env = {"client": CLIENT, "terminal_am": "AM_X", "judgment": judgment, "profile_json": core.state["doc"]}
    return svc.RegeneratedAssessment(CLIENT, "AM_X", core.state["doc"], env, "prev-sig", {"judgment_action": "judgment_current:x"})


def test_hub_migration_keeps_the_signed_hook_and_adds_the_verified_replay(core, monkeypatch):
    replayed = svc._derive_judgment(core.state["doc"], jd.consumed(core.world.persisted))       # lo que hace el gancho de rederivación (replay de consumidas)
    monkeypatch.setattr(svc, "regenerate_stale_evidence", lambda e, m: _stale_candidate(core, replayed))
    cand = svc.regenerate_stale_evidence_replay(core.entry, "evidence_source_application_api_v6")
    assert cand.envelope["judgment"] is replayed and cand.envelope["previous_judgment"] == core.world.previous["previous_judgment"]


def test_hub_migration_abstains_if_the_hook_judgment_differs_from_the_verified_replay(core, monkeypatch):
    monkeypatch.setattr(svc, "regenerate_stale_evidence", lambda e, m: _stale_candidate(core, svc._derive_judgment(core.state["doc"])))   # sin decisiones
    with pytest.raises(svc.EnrichmentError) as exc:
        svc.regenerate_stale_evidence_replay(core.entry, "evidence_source_application_api_v6")
    assert exc.value.code == "REPLAY_INCOHERENT"


def test_hub_migration_abstains_when_a_consumed_decision_was_revoked(core, monkeypatch):
    replayed = svc._derive_judgment(core.state["doc"], jd.consumed(core.world.persisted))
    monkeypatch.setattr(svc, "regenerate_stale_evidence", lambda e, m: _stale_candidate(core, replayed))
    core.world.state["events"].append(revoked(core.world.profile, CRITICAL[1], revokes=f"gd-{CRITICAL[1]}", event_id="gd-rev"))
    with pytest.raises(svc.EnrichmentError) as exc:
        svc.regenerate_stale_evidence_replay(core.entry, "evidence_source_application_api_v6")
    assert exc.value.code == "RATIFICATION_NOT_ACTIVE"


# ───────────── cableado: el dry-run y la ejecución usan la MISMA variante (coherencia + revalidación al escribir) ─────────────
def test_preview_and_execution_both_use_the_verified_replay_variants(monkeypatch):
    from webapp.services import assessment_backfill as ab
    from webapp.services import governed_update_approval as gua
    from webapp.services import governed_update_requests as gur
    seen: dict = {}
    monkeypatch.setattr(ab, "secured_regenerate_selected", lambda *a, **k: seen.setdefault("exec_selective", k["regenerate"]) and {})
    monkeypatch.setattr(ab, "secured_migrate", lambda *a, **k: seen.setdefault("exec_migration", k["regenerate"]) and {})
    monkeypatch.setattr(gur, "_entries_for", lambda c, a: [SimpleNamespace(entry_id="e")])
    monkeypatch.setattr(gua, "hub_authorizer", lambda *a, **k: None)
    rec = {"request_id": "r" * 32, "client_dir": CLIENT, "terminal_am": "AM_X"}
    appr = {"approval_id": "a" * 32, "declared_spaces": ["DEV"], "expected": {"plan_digest": "d"}}
    gua._execute(rec, appr, "SELECTIVE:x")
    gua._execute(rec, appr, "MIGRATION:evidence_source_application_api_v6")
    assert seen == {"exec_selective": svc.regenerate_current_evidence_replay, "exec_migration": svc.regenerate_stale_evidence_replay}
    calls: list[str] = []

    def fake_plan(regenerate):
        def run(entries, *a, **k):
            try:
                regenerate(entries[0], *(("m",) if "migration" in run.__name__ else (None,)))
                return {"plan": {"items": []}, "records": []}
            except svc.EnrichmentError as exc:
                return {"plan": {"items": []}, "records": [{"action": ab.ABSTAINED, "reason": exc.code}]}
        return run
    monkeypatch.setattr(gur, "envelope_sha", lambda c, a: "sha")
    monkeypatch.setattr(ab, "plan_selective_regeneration", lambda entries, roots, **k: fake_plan(k["regenerate"])(entries))
    monkeypatch.setattr(ab, "migrate_stale_evidence", lambda entries, migration, **k: fake_plan(lambda e, *_: k["regenerate"](e, migration))(entries))
    for pending, fn in ((None, "regenerate_current_evidence_replay"), ("m", "regenerate_stale_evidence_replay")):
        monkeypatch.setattr(svc, "status_for_entry", lambda e, _p=pending: SimpleNamespace(pending_migration=_p, state=svc.CURRENT, stale_targeted=None))
        monkeypatch.setattr(svc, fn, lambda *a, _n=fn, **k: calls.append(_n) or (_ for _ in ()).throw(svc.EnrichmentError("RATIFICATION_NOT_ACTIVE")))
        out = gur.compute_preview({"client_dir": CLIENT, "terminal_am": "AM_X", "governed": {"sha256": "sha"}, "source": {}})
        assert out["state"] == gur.PREVIEW_ABSTAINED and out["abstentions"] == ["RATIFICATION_NOT_ACTIVE"] and out["zero_writes"]
    assert calls == ["regenerate_current_evidence_replay", "regenerate_stale_evidence_replay"]
