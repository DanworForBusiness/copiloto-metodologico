"""v7 — CARRILES de vigencia de las ratificaciones: una ratificación acotada (`applies_to`) NUNCA reemplaza ni revoca implícitamente la decisión
histórica (sin alcance) ni la de otro consumidor. Coexistencia · renovación · revocación dirigida · replay · regeneración. Datos SINTÉTICOS
(nombres de modelo ilustrativos)."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from tests.webapp.test_governed_decisions_ops import IDENT, _leaf, env  # noqa: F401 — fixture `env`
from webapp.services import assessment_service as svc
from webapp.services import governed_decisions as gd
from webapp.services import governed_decisions_ops as ops
from webapp.services import governed_decisions_store as store
from webapp.services import governed_ratification_link as link

UND = "EXTERNAL_CONTRACT_UNDETERMINED"
HOJ, NC, OTHER = "AM_SELL_OUT_HOJOBAR", "AM_SELL_OUT_HOJOBAR_NC", "AM_SELL_OUT"
SUBJ = {"client": "Kensing", "technical_id": gd.cds_technical_id(IDENT)}
T0 = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)
LEAF = _leaf(cls=UND, leaf="SRC")


def _t(h):
    return (T0 + timedelta(hours=h)).isoformat()


def R(eid, h, scope=None):
    return gd.build_ratification(event_id=eid, subject=SUBJ, rationale="r", actor="owner", decided_at=_t(h), evidence_ref_override=gd.cds_evidence_ref(LEAF), applies_to=scope)


def X(eid, h, target):
    return gd.build_revocation(event_id=eid, subject=SUBJ, revokes_event_id=target, rationale="x", actor="owner", decided_at=_t(h))


NOW = T0 + timedelta(days=1)
DIG = gd.cds_contract_digest(LEAF)


def _st(evs, lane=gd.UNSCOPED_LANE):
    s = gd.derive_status(evs, DIG, NOW, lane=lane)
    return s["decision"], s["ratified_event_id"]


def _legacy(evs):
    """Algoritmo ANTERIOR (v4.B), para comparar el carril histórico byte a byte."""
    d, a = gd.DECISION_NONE, None
    for e in sorted(evs, key=lambda e: (gd.parse_utc(e["decided_at"]), e["event_id"])):
        d, a = (gd.DECISION_RATIFIED, e["event_id"]) if e["event"] == gd.EVENT_RATIFIED else (gd.DECISION_REVOKED, None)
    return d, a


# ───────────── contrato puro: carriles ─────────────
@pytest.mark.parametrize("evs", [
    [R("h1", 0)], [R("h1", 0), X("v1", 1, "h1")], [R("h1", 0), X("v1", 1, "h1"), R("h2", 2)], [R("h1", 0), R("h2", 1)], [X("v0", 0, "nada")]])
def test_historical_lane_is_identical_to_the_previous_algorithm(evs):
    assert _st(evs) == _legacy(evs)
    assert gd.derive_status(evs, DIG, NOW) == gd.derive_status(evs, DIG, NOW, lane=None)


def test_a_scoped_ratification_does_not_replace_the_historical_one():
    evs = [R("h1", 0), R("s1", 1, [HOJ])]
    assert _st(evs) == (gd.DECISION_RATIFIED, "h1")                       # histórico intacto para TODOS los consumidores
    assert _st(evs, HOJ) == (gd.DECISION_RATIFIED, "s1") and _st(evs, NC) == (gd.DECISION_NONE, None)


def test_two_acceptances_for_distinct_consumers_coexist():
    evs = [R("s1", 0, [HOJ]), R("s2", 1, [NC])]
    assert _st(evs, HOJ) == (gd.DECISION_RATIFIED, "s1") and _st(evs, NC) == (gd.DECISION_RATIFIED, "s2") and _st(evs) == (gd.DECISION_NONE, None)


def test_renewal_replaces_only_the_same_consumer_lane_and_revoking_the_superseded_one_is_inert():
    evs = [R("s1", 0, [HOJ, NC]), R("s2", 1, [HOJ]), X("v1", 2, "s1")]
    assert _st(evs, HOJ) == (gd.DECISION_RATIFIED, "s2")                  # la renovación sobrevive a la revocación de la reemplazada
    assert _st(evs, NC) == (gd.DECISION_REVOKED, None)                    # en NC la revocada SÍ era la vigente


@pytest.mark.parametrize("evs,lane,expected", [
    ([R("h1", 0), R("s1", 1, [HOJ]), X("v1", 2, "s1")], None, (gd.DECISION_RATIFIED, "h1")),     # revocar la acotada no toca la histórica
    ([R("h1", 0), R("s1", 1, [HOJ]), X("v1", 2, "s1")], HOJ, (gd.DECISION_REVOKED, None)),
    ([R("h1", 0), R("s1", 1, [HOJ]), X("v1", 2, "h1")], None, (gd.DECISION_REVOKED, None)),        # revocar la histórica no toca la acotada
    ([R("h1", 0), R("s1", 1, [HOJ]), X("v1", 2, "h1")], HOJ, (gd.DECISION_RATIFIED, "s1")),
    ([R("s1", 0, [HOJ]), X("v1", 1, "s1"), R("s2", 2, [HOJ])], HOJ, (gd.DECISION_RATIFIED, "s2")),  # re-aceptación tras revocar
])
def test_revocations_act_only_on_the_lane_of_their_target(evs, lane, expected):
    assert _st(evs, lane) == expected


# ───────────── enlace (scoring): sin efecto sobre otros consumidores ─────────────
def _profile(am):
    return {"scope": {"terminal_analytic_model": am}, "canonical_evidence": {"source_contract_classes": {"leaves": [copy.deepcopy(LEAF)]}}}


def _linked(am, evs):
    out = link.link_ratifications(_profile(am), "Kensing", now=NOW, load_events=lambda c, t: copy.deepcopy(evs))
    return [(i["event_id"], i.get("applies_to")) for i in out["ratified_contracts"]], [r["reason"] for r in out["report"]]


def test_link_prefers_the_own_lane_and_never_displaces_the_historical_decision_of_others():
    evs = [R("h1", 0), R("s1", 1, [HOJ])]
    assert _linked(HOJ, evs)[0] == [("s1", [HOJ])]
    for am in (NC, OTHER):
        assert _linked(am, evs)[0] == [("h1", None)]                       # siguen consumiendo la histórica, exactamente como antes


def test_link_falls_back_to_the_historical_lane_when_the_own_lane_is_revoked():
    evs = [R("h1", 0), R("s1", 1, [HOJ]), X("v1", 2, "s1")]
    assert _linked(HOJ, evs)[0] == [("h1", None)] and _linked(NC, evs)[0] == [("h1", None)]


def test_only_other_consumers_lanes_means_out_of_scope():
    linked, reasons = _linked(NC, [R("s1", 0, [HOJ])])
    assert linked == [] and reasons == ["OUT_OF_SCOPE"]


def test_without_scoped_events_the_link_is_unchanged():
    for evs in ([R("h1", 0)], [R("h1", 0), X("v1", 1, "h1")], []):
        for am in (HOJ, NC):
            linked, reasons = _linked(am, evs)
            assert all(a is None for _e, a in linked) and "OUT_OF_SCOPE" not in reasons


# ───────────── operación revoke dirigida (registro gobernado real en memoria) ─────────────
def _world(env):                                                                                  # noqa: F811
    env.gov.write(am=HOJ, leaves=[_leaf(cls=UND, leaf="SRC")])
    env.gov.write(am=NC, leaves=[_leaf(cls=UND, leaf="SRC")])


def _ratify(eid, h, scope=None):
    return ops.ratify(client="Kensing", identity=IDENT, rationale=f"r-{eid}", actor="owner", now=T0 + timedelta(hours=h), event_id=eid, request_id=f"q-{eid}", applies_to=scope)


def _revoke(eid, h, scope=None):
    return ops.revoke(client="Kensing", identity=IDENT, rationale=f"x-{eid}", actor="owner", now=T0 + timedelta(hours=h), event_id=eid, request_id=f"q-{eid}", applies_to=scope)


def _lanes():
    rec = store.reconstruct("Kensing", SUBJ["technical_id"], None, NOW)
    return rec["status"]["decision"], {m: s["decision"] for m, s in (rec.get("scoped_status") or {}).items()}


def test_targeted_revocation_of_a_scoped_acceptance_keeps_the_historical_decision(env):           # noqa: F811
    _world(env)
    _ratify("h1", 0)
    _ratify("s1", 1, [HOJ])
    out = _revoke("v1", 2, [HOJ])
    assert out["revoked_event_id"] == "s1" and _lanes() == (gd.DECISION_RATIFIED, {HOJ: gd.DECISION_REVOKED})
    assert _revoke("v1", 2, [HOJ])["outcome"] == "IDEMPOTENT"


def test_unscoped_revocation_targets_only_the_historical_decision(env):                          # noqa: F811
    _world(env)
    _ratify("h1", 0)
    _ratify("s1", 1, [HOJ])
    assert _revoke("v1", 2)["revoked_event_id"] == "h1" and _lanes() == (gd.DECISION_REVOKED, {HOJ: gd.DECISION_RATIFIED})


@pytest.mark.parametrize("scope", [[NC], [HOJ, NC]])
def test_a_revocation_never_infers_or_widens_the_scope(env, scope):                              # noqa: F811
    _world(env)
    _ratify("s1", 0, [HOJ])
    with pytest.raises(ops.RevocationNotAllowed):
        _revoke("v1", 1, scope)
    with pytest.raises(ops.RevocationNotAllowed):
        _revoke("v2", 1)                                                                        # no hay histórica: no se revoca la acotada por defecto


def test_coexisting_acceptances_are_revoked_independently(env):                                  # noqa: F811
    _world(env)
    _ratify("s1", 0, [HOJ])
    _ratify("s2", 1, [NC])
    _revoke("v1", 2, [NC])
    assert _lanes() == (gd.DECISION_NONE, {HOJ: gd.DECISION_RATIFIED, NC: gd.DECISION_REVOKED})


# ───────────── replay P0.1 / regeneración ─────────────
def _item(ev):
    ident = gd.cds_identity(LEAF)
    return {"leaf": "SRC", "identity": ident, "technical_id": gd.cds_technical_id(ident), "event_id": ev["event_id"], "digest": DIG,
            "evidence_ref": gd.cds_evidence_ref(LEAF), **({"applies_to": ev["applies_to"]} if ev.get("applies_to") else {})}


@pytest.mark.parametrize("history,consumed,am,ok", [
    ([R("h1", 0), R("s1", 1, [HOJ])], "h1", NC, True),          # una aceptación acotada posterior NO invalida la histórica consumida por otro
    ([R("h1", 0), R("s1", 1, [HOJ])], "s1", HOJ, True),
    ([R("s1", 0, [HOJ]), R("h2", 1)], "s1", HOJ, True),          # una re-ratificación histórica posterior NO invalida la acotada
    ([R("s1", 0, [HOJ]), R("s2", 1, [HOJ])], "s1", HOJ, False),  # renovada: el Juicio consumió la anterior ⇒ abstención (reevaluar)
    ([R("h1", 0), R("s1", 1, [HOJ]), X("v1", 2, "s1")], "s1", HOJ, False),
    ([R("h1", 0), R("s1", 1, [HOJ]), X("v1", 2, "s1")], "h1", NC, True),
])
def test_replay_verifies_each_consumed_decision_in_its_own_lane(monkeypatch, history, consumed, am, ok):
    monkeypatch.setattr(store, "load_events", lambda c, t: copy.deepcopy(history))
    ev = next(e for e in history if e["event_id"] == consumed)
    call = lambda: svc._verify_consumed_decision("Kensing", _item(ev), {"SRC": LEAF}, NOW, am)     # noqa: E731
    if ok:
        call()
    else:
        with pytest.raises(svc.ReplacementAbstained) as exc:
            call()
        assert exc.value.code == "RATIFICATION_NOT_ACTIVE"


# ───────────── presentación (inventario / ficha) y trazabilidad del evento usado ─────────────
def test_summary_status_is_identical_without_scoped_ratifications():
    for evs in ([], [R("h1", 0)], [R("h1", 0), X("v1", 1, "h1")]):
        assert gd.summary_status(evs, DIG, NOW) == gd.derive_status(evs, DIG, NOW)


def test_a_source_with_only_scoped_acceptances_is_not_presented_as_none():
    s = gd.summary_status([R("s1", 0, [HOJ])], DIG, NOW)
    assert s["decision"] == gd.DECISION_RATIFIED_SCOPED and s["lanes"] == "SCOPED_ONLY" and s["scoped"][HOJ]["ratified_event_id"] == "s1"
    assert s["evidence"] == gd.EVIDENCE_MATCH and s["principal"] == gd.DECISION_RATIFIED_SCOPED
    from webapp.services import governed_decisions_presenter as pr
    assert pr.dimension_chips(s)["decision"]["label"] == "Ratificada con alcance"
    r = gd.summary_status([R("s1", 0, [HOJ]), X("v1", 1, "s1")], DIG, NOW)
    assert r["decision"] == gd.DECISION_REVOKED and r["scoped"][HOJ]["decision"] == gd.DECISION_REVOKED
    st = gd.summary_status([R("s1", 0, [HOJ])], "f" * 64, NOW)
    assert st["evidence"] == gd.EVIDENCE_STALE and st["principal"] == gd.PRINCIPAL_EVIDENCE_STALE


def test_with_historical_and_scoped_the_historical_state_is_kept_and_lanes_are_added():
    s = gd.summary_status([R("h1", 0), R("s1", 1, [HOJ])], DIG, NOW)
    assert s["decision"] == gd.DECISION_RATIFIED and s["ratified_event_id"] == "h1" and s["lanes"] == "HISTORICAL_AND_SCOPED"


def test_inventory_rows_for_scoped_only_sources(env, monkeypatch):                                 # noqa: F811
    from webapp.services import governed_decisions_inventory as inv
    _world(env)
    monkeypatch.setattr(inv, "_load_all_client_events", lambda c: {SUBJ["technical_id"]: [R("s1", 0, [HOJ])]})
    row = next(r for r in inv.list_client_contracts("Kensing", NOW) if r["technical_id"] == SUBJ["technical_id"])
    assert row["decision"] == gd.DECISION_RATIFIED_SCOPED and row["lanes"] == "SCOPED_ONLY" and list(row["scoped"]) == [HOJ]
    monkeypatch.setattr(inv, "_load_all_client_events", lambda c: {SUBJ["technical_id"]: [R("h1", 0)]})
    row = next(r for r in inv.list_client_contracts("Kensing", NOW) if r["technical_id"] == SUBJ["technical_id"])
    assert row["decision"] == gd.DECISION_RATIFIED and "scoped" not in row and "lanes" not in row      # fila histórica: misma forma


def _report(am, evs):
    return link.link_ratifications(_profile(am), "Kensing", now=NOW, load_events=lambda c, t: copy.deepcopy(evs))["report"][0]


def test_precedence_of_the_scoped_acceptance_is_traceable_for_the_declared_consumer_only():
    evs = [R("h1", 0), R("s1", 1, [HOJ])]
    rh, rn = _report(HOJ, evs), _report(NC, evs)
    assert (rh["event_id"], rh["lane"], rh["precedes_historical_event_id"]) == ("s1", "SCOPED", "h1")
    assert (rn["event_id"], rn["lane"]) == ("h1", "HISTORICAL") and "precedes_historical_event_id" not in rn
    assert set(_report(HOJ, [R("h1", 0)])) == {"leaf", "outcome", "reason", "technical_id", "event_id", "review"}   # sin carriles: informe histórico idéntico
