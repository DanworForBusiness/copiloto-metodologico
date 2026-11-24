"""v7 — ALCANCE DE CONSUMO de las ratificaciones (`applies_to`, extensión aditiva de `governed-decision/v1`).
Contrato puro · acreditación en `ratify` (modelo existente que CONSUME la identidad) · enlace solo a los consumidores declarados · replay P0.1 ·
compatibilidad con las ratificaciones históricas (sin `applies_to`). Datos SINTÉTICOS."""
from __future__ import annotations

import copy

import pytest

from tests.webapp.test_governed_decisions_ops import IDENT, NOW, _leaf, env  # noqa: F401 — fixture `env`
from webapp.services import assessment_service as svc
from webapp.services import governed_decisions as gd
from webapp.services import governed_decisions_ops as ops
from webapp.services import governed_decisions_store as store
from webapp.services import governed_ratification_link as link

UND = "EXTERNAL_CONTRACT_UNDETERMINED"
SUBJ = {"client": "Kensing", "technical_id": gd.cds_technical_id(IDENT)}


def _ratification(*, applies_to=None, event_id="gd-1", leaf=None, decided_at="2026-10-17T12:00:00+00:00"):
    lf = leaf or _leaf(cls=UND)
    return gd.build_ratification(event_id=event_id, subject=SUBJ, rationale="r", actor="owner", decided_at=decided_at,
                                 evidence_ref_override=gd.cds_evidence_ref(lf), applies_to=applies_to)


# ───────────── contrato puro ─────────────
def test_applies_to_is_canonical_and_optional():
    assert "applies_to" not in _ratification()                                                   # histórica: misma forma de siempre
    assert _ratification(applies_to=["AM_B", "AM_A"])["applies_to"] == ["AM_A", "AM_B"]


@pytest.mark.parametrize("bad", [[], "AM_A", ["AM_*"], ["AM_A", "AM_A"], [""], [None]])
def test_invalid_scopes_are_rejected(bad):
    with pytest.raises(gd.ValidationError):
        _ratification(applies_to=bad)


def test_unsorted_scope_and_scoped_revocations_are_rejected():
    ev = _ratification(applies_to=["AM_A", "AM_B"])
    ev["applies_to"] = ["AM_B", "AM_A"]
    with pytest.raises(gd.ValidationError):
        gd.validate_event(ev)
    rev = gd.build_revocation(event_id="gd-2", subject=SUBJ, revokes_event_id="gd-1", rationale="x", actor="owner", decided_at="2026-10-18T12:00:00+00:00")
    rev["applies_to"] = ["AM_A"]
    with pytest.raises(gd.ValidationError):
        gd.validate_event(rev)


# ───────────── acreditación en ratify ─────────────
def _world(env):                                                                                  # noqa: F811
    env.gov.write(am="AM_A", leaves=[_leaf(cls=UND, leaf="SRC")])
    env.gov.write(am="AM_B", leaves=[_leaf(cls=UND, leaf="SRC")])
    env.gov.write(am="AM_C", leaves=[_leaf(cls=UND, leaf="OTRA", entity="ABAP_CDS.OTRA")])          # existe, NO consume la identidad


def _ratify(**kw):
    base = dict(client="Kensing", identity=IDENT, rationale="aceptado para el consumo de AM_A", actor="owner", now=NOW, event_id="gd-a", request_id="req-a")
    return ops.ratify(**{**base, **kw})


def test_ratify_accredits_and_persists_the_declared_consumer(env):                               # noqa: F811
    _world(env)
    out = _ratify(applies_to=["AM_A"])
    assert out["outcome"] == "RATIFIED" and out["event"]["applies_to"] == ["AM_A"] and out["consumers"] == ["AM_A", "AM_B"]
    stored = store.load_events("Kensing", SUBJ["technical_id"])
    assert [e["applies_to"] for e in stored] == [["AM_A"]]


@pytest.mark.parametrize("scope,code", [(["AM_NOPE"], "MODEL_NOT_FOUND"), (["AM_C"], "CONSUMPTION_NOT_ACCREDITED"), (["AM_A", "AM_NOPE"], "MODEL_NOT_FOUND")])
def test_ratify_rejects_unknown_models_and_unaccredited_consumption_without_writing(env, scope, code):   # noqa: F811
    _world(env)
    with pytest.raises(ops.ApplicabilityRejected) as exc:
        _ratify(applies_to=scope)
    assert exc.value.code == code and store.load_events("Kensing", SUBJ["technical_id"]) == []


def test_ratify_without_scope_keeps_the_historical_event_shape(env):                             # noqa: F811
    _world(env)
    assert "applies_to" not in _ratify()["event"]


def test_scope_is_logical_content_for_idempotency(env):                                          # noqa: F811
    _world(env)
    first = _ratify(applies_to=["AM_A"])
    assert _ratify(applies_to=["AM_A"])["outcome"] == "IDEMPOTENT"
    with pytest.raises(ops.IdempotencyConflict):
        _ratify(applies_to=["AM_A", "AM_B"])                                                     # mismo request_id, otro alcance
    assert first["event"]["applies_to"] == ["AM_A"]


# ───────────── enlace: sin propagación a otros consumidores ─────────────
def _profile(am, leaf=None):
    return {"scope": {"terminal_analytic_model": am}, "canonical_evidence": {"source_contract_classes": {"leaves": [leaf or _leaf(cls=UND, leaf="SRC")]}}}


def _link(am, events, leaf=None):
    return link.link_ratifications(_profile(am, leaf), "Kensing", now=NOW, load_events=lambda c, t: copy.deepcopy(events))


def test_a_scoped_ratification_links_only_into_the_declared_consumer():
    evs = [_ratification(applies_to=["AM_A"], leaf=_leaf(cls=UND, leaf="SRC"))]
    a, b = _link("AM_A", evs), _link("AM_B", evs)
    assert a["ratified_contracts"][0]["applies_to"] == ["AM_A"]
    assert b["ratified_contracts"] == [] and b["report"][0]["reason"] == "OUT_OF_SCOPE"


def test_an_unscoped_historical_ratification_links_exactly_as_before():
    evs = [_ratification(leaf=_leaf(cls=UND, leaf="SRC"))]
    for am in ("AM_A", "AM_B"):
        item = _link(am, evs)["ratified_contracts"][0]
        assert set(item) == {"leaf", "identity", "technical_id", "event_id", "digest", "evidence_ref", "decided_at"}


def test_a_scoped_ratification_with_incompatible_evidence_does_not_link():
    evs = [_ratification(applies_to=["AM_A"], leaf=_leaf(cls=UND, leaf="SRC"))]
    changed = _leaf(cls=UND, leaf="SRC", bases=("OTRA_BASE",))                                     # el contrato observado ya no es el ratificado
    out = _link("AM_A", evs, leaf=changed)
    assert out["ratified_contracts"] == [] and out["report"][0]["reason"] == "EVIDENCE_STALE"


# ───────────── replay P0.1: el alcance consumido debe ser EXACTAMENTE el del evento ─────────────
@pytest.mark.parametrize("event_scope,item_scope,am,ok", [
    (None, None, "AM_A", True),                         # histórica
    (["AM_A"], ["AM_A"], "AM_A", True),
    (["AM_A"], None, "AM_A", False),                    # alcance omitido en el Juicio persistido
    (None, ["AM_A"], "AM_A", False),                    # alcance inventado
    (["AM_A"], ["AM_A", "AM_B"], "AM_A", False),        # alcance ampliado
    (["AM_B"], ["AM_B"], "AM_A", False),                # este modelo fuera del alcance
])
def test_replay_reverifies_the_scope_of_each_consumed_decision(monkeypatch, event_scope, item_scope, am, ok):
    lf = _leaf(cls=UND, leaf="SRC")
    ev = _ratification(applies_to=event_scope, leaf=lf)
    ident = gd.cds_identity(lf)
    item = {"leaf": "SRC", "identity": ident, "technical_id": gd.cds_technical_id(ident), "event_id": ev["event_id"], "digest": gd.cds_contract_digest(lf),
            "evidence_ref": gd.cds_evidence_ref(lf), **({"applies_to": item_scope} if item_scope else {})}
    monkeypatch.setattr(store, "reconstruct", lambda c, t, d, n: {"events": [ev], "status": gd.derive_status([ev], d, n)})
    call = lambda: svc._verify_consumed_decision("Kensing", item, {"SRC": lf}, NOW, am)          # noqa: E731
    if ok:
        call()
    else:
        with pytest.raises(svc.ReplacementAbstained) as exc:
            call()
        assert exc.value.code == "RATIFICATION_SCOPE_MISMATCH"
