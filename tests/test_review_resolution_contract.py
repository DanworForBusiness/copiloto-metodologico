"""Pruebas FOCALIZADAS del contrato de resolución de revisiones (Iteración II, kind REVIEW_RESOLUTION).

Cubre: regresión CDS (CONTRACT_RATIFICATION intacto), firma del perfil / coherencia del sidecar, sidecar desfasado → UNKNOWN,
identidad estable por familia, estados de gestión, bifurcación + reconciliación + re-bifurcación, validación del estado real del
subject, frontera de autorización explícita, y persistencia/relectura del envelope (SIMULADO, nunca escribe en Spaces real).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from tools.catalog.modeling_assessment import review_evidence as rev
from webapp.services import governed_decisions as gd
from webapp.services import governed_decisions_store as store_mod
from webapp.services import review_resolution_ops as ops
from webapp.services import review_resolutions as rr

UTC = timezone.utc
T0 = "2026-11-09T00:00:00Z"
NOW = datetime(2026, 11, 10, tzinfo=UTC)
OWNER = ops.AuthContext(actor="owner@danwor.com", is_owner=True, same_origin=True)


# ─────────────────────────── store simulado (usa la lógica de claves REAL, sin I/O) ───────────────────────────

class FakeStore:
    """Almacén en memoria que reusa `event_key`/`subject_prefix` reales + `validate_event`. No toca Spaces."""

    def __init__(self):
        self.objs: dict[str, bytes] = {}

    def append_event(self, event: dict) -> dict:
        gd.validate_event(event)
        key = store_mod.event_key(event)
        data = gd.canonical_json(event).encode("utf-8")
        if key in self.objs:
            if self.objs[key] == data:
                return {"stored": False, "idempotent": True, "key": key}
            raise store_mod.DecisionConflict(key)
        self.objs[key] = data
        return {"stored": True, "idempotent": False, "key": key}

    def load_events(self, client: str, technical_id: str) -> list[dict]:
        out = []
        for data in self.objs.values():
            ev = json.loads(data.decode("utf-8"))
            gd.validate_event(ev)
            subj = ev.get("subject") or {}
            if subj.get("client") == client and subj.get("technical_id") == technical_id:
                out.append(ev)
        return out


# ─────────────────────────── helpers de perfil/sidecar sintéticos ───────────────────────────

class _Fact:
    def __init__(self, key, value):
        self.key, self.value = key, value


def _doc(*, pool_sig="POOL-1", versions=None, groups=None, observations=None):
    return {"scope": {"pool_signature": pool_sig}, "versions": versions or {"signals": "signal-rules/v5"},
            "review_required": groups or [], "observations": observations or []}


def _tf_doc_and_facts(*, materialized="2TR", source_view="2VR", direct_source=True):
    fkey = f"harmonization.contract_character::{materialized}"
    occ = {"affected_artifacts": [materialized], "evidence_facts": [fkey],
           "values": {"materialized_table": materialized, "source_view": source_view, "data_flows": ["DF1"],
                      "mapping_counts": {"IDENTITY": 5}, "viability_verified": False}}
    doc = _doc(groups=[{"id": "TRANSFORMATION_FLOW_CONSOLIDATION_OPPORTUNITY", "occurrences": [occ]}])
    ds = {"source": source_view, "is_query_view": True, "layer2_by_section28_name": True, "remote": False} if direct_source else None
    facts = [_Fact(fkey, {"implementation": "TABLE_PRODUCED_BY_FLOW", "sources": [source_view], "producer_flows": ["DF:DF1"],
                          "mapping_counts": {"IDENTITY": 5}, "character": {"value": "MIXED"}, "direct_source": ds})]
    return doc, facts


def _vocab_doc_and_facts(*, label="Monto", artifacts=("A", "B")):
    fkey = f"naming.vocabulary_mix::{label}"
    occ = {"affected_artifacts": list(artifacts), "evidence_facts": [fkey],
           "values": {"label": label, "technical_names": list(artifacts), "distinct_forms": 2}}
    doc = _doc(groups=[{"id": "FIELD_VOCABULARY_MIX", "occurrences": [occ]}])
    facts = [_Fact(fkey, {"label": label, "technical_names": sorted(artifacts)})]
    return doc, facts


def _vocab_identity(doc, artifacts=("A", "B")):
    return rev.review_identity("FIELD_VOCABULARY_MIX", "AM_X", doc["review_required"][0]["occurrences"][0]["values"], list(artifacts))


def _tf_identity():
    return rev.review_identity("TRANSFORMATION_FLOW_CONSOLIDATION_OPPORTUNITY", "AM_X", {"materialized_table": "2TR"}, ["2TR"])


# ═══════════════════════════ 1 · regresión CDS (CONTRACT_RATIFICATION intacto) ═══════════════════════════

def _cds_leaf():
    return {"leaf_kind": "REMOTE_IMPORTED", "remote_entities": ["EntityX"], "remote_connection": "ConnA",
            "contract_class": "EXTERNAL_CONTRACT_UNDETERMINED", "classification_bases": ["X"]}


def test_cds_ratification_still_validates_and_kind_unchanged():
    leaf = _cds_leaf()
    ref = gd.cds_evidence_ref(leaf)
    assert ref["fields"] == list(gd.CDS_DIGEST_FIELDS)
    ev = gd.build_ratification(event_id="cds-1", subject={"client": "C", "technical_id": gd.cds_technical_id(gd.cds_identity(leaf))},
                               rationale="r", actor="owner", decided_at=T0, evidence_ref_override=ref)
    gd.validate_event(ev)
    assert ev["kind"] == gd.KIND_CONTRACT_RATIFICATION
    assert gd.cds_contract_digest(leaf) == gd.cds_contract_digest(dict(leaf))


def test_cds_rejects_review_evidence_fields():
    leaf = _cds_leaf()
    ev = gd.build_ratification(event_id="cds-2", subject={"client": "C", "technical_id": gd.cds_technical_id(gd.cds_identity(leaf))},
                               rationale="r", actor="owner", decided_at=T0, evidence_ref_override=gd.cds_evidence_ref(leaf))
    ev["evidence_ref"]["fields"] = list(gd.REVIEW_DIGEST_FIELDS)
    with pytest.raises(gd.ValidationError):
        gd.validate_event(ev)


# ═══════════════════════════ 2 · contrato REVIEW_RESOLUTION ═══════════════════════════

def test_disposition_validated_by_family():
    tf_id, vocab_id = _tf_identity(), _vocab_identity(_vocab_doc_and_facts()[0])
    digest = "a" * 64
    gd.build_review_resolution(event_id="r1", subject={"client": "C", "technical_id": rev.review_technical_id(tf_id)},
                               klass=tf_id["klass"], disposition="ACCEPTED", digest=digest, rationale="r", actor="o", decided_at=T0)
    with pytest.raises(gd.ValidationError):
        gd.build_review_resolution(event_id="r2", subject={"client": "C", "technical_id": rev.review_technical_id(tf_id)},
                                   klass=tf_id["klass"], disposition="CORRECTION_REQUIRED", digest=digest, rationale="r", actor="o", decided_at=T0)
    with pytest.raises(gd.ValidationError):
        gd.build_review_resolution(event_id="r3", subject={"client": "C", "technical_id": rev.review_technical_id(vocab_id)},
                                   klass=vocab_id["klass"], disposition="ACCEPTED", digest=digest, rationale="r", actor="o", decided_at=T0)
    gd.build_review_resolution(event_id="r4", subject={"client": "C", "technical_id": rev.review_technical_id(vocab_id)},
                               klass=vocab_id["klass"], disposition="CORRECTION_REQUIRED", digest=digest, rationale="r", actor="o", decided_at=T0)


def test_klass_must_match_identity():
    tf_id = _tf_identity()
    with pytest.raises(gd.ValidationError):
        gd.build_review_resolution(event_id="r5", subject={"client": "C", "technical_id": rev.review_technical_id(tf_id)},
                                   klass=gd.KLASS_MODEL_REVIEW, disposition="VALIDATED_DESIGN", digest="a" * 64,
                                   rationale="r", actor="o", decided_at=T0)


def test_review_rejects_non_review_evidence_fields():
    tf_id = _tf_identity()
    ev = gd.build_review_resolution(event_id="r6", subject={"client": "C", "technical_id": rev.review_technical_id(tf_id)},
                                    klass=tf_id["klass"], disposition="ACCEPTED", digest="a" * 64, rationale="r", actor="o", decided_at=T0)
    ev["evidence_ref"]["fields"] = list(gd.CDS_DIGEST_FIELDS)
    with pytest.raises(gd.ValidationError):
        gd.validate_event(ev)


# ═══════════════════════════ 3 · firma del perfil / coherencia del sidecar ═══════════════════════════

def test_sidecar_binds_profile_and_resolves_digest():
    doc, facts = _tf_doc_and_facts()
    sidecar = rev.build_sidecar(doc, facts, "AM_X")
    assert sidecar["version"] == rev.REVIEW_EVIDENCE_VERSION
    assert sidecar["profile_signature"] == rev.profile_signature(doc)
    assert sidecar["input_signature"] == rev.input_signature(doc)
    tid = rev.review_technical_id(_tf_identity())
    assert rev.current_footprint_digest(tid, sidecar, doc) == sidecar["footprints"][tid]["digest"]


def test_profile_signature_independent_of_envelope_siblings():
    doc, facts = _tf_doc_and_facts()
    sig_before = rev.profile_signature(doc)
    envelope = {"profile_json": doc, "review_evidence": rev.build_sidecar(doc, facts, "AM_X")}
    assert rev.profile_signature(envelope["profile_json"]) == sig_before


# ═══════════════════════════ 4 · sidecar desfasado → UNKNOWN ═══════════════════════════

def test_stale_sidecar_degrades_to_unknown():
    doc, facts = _tf_doc_and_facts()
    sidecar = rev.build_sidecar(doc, facts, "AM_X")
    tid = rev.review_technical_id(_tf_identity())
    doc2 = _doc(pool_sig="POOL-1", groups=doc["review_required"])
    doc2["scope"]["extra"] = "changed"
    assert rev.current_footprint_digest(tid, sidecar, doc2) is None
    assert rev.current_footprint_digest(tid, dict(sidecar, input_signature="otra"), doc) is None
    assert rev.current_footprint_digest(tid, None, doc) is None


def test_derive_status_unknown_never_match_by_default():
    tf_id = _tf_identity()
    ev = gd.build_review_resolution(event_id="r7", subject={"client": "C", "technical_id": rev.review_technical_id(tf_id)},
                                    klass=tf_id["klass"], disposition="ACCEPTED", digest="a" * 64, rationale="r", actor="o", decided_at=T0)
    st = rr.derive_review_status([ev], current_footprint_digest=None, now=NOW, presence="PRESENT")
    assert st["evidence"] == rr.EVIDENCE_UNKNOWN


# ═══════════════════════════ 5 · identidad estable por familia ═══════════════════════════

def test_vocab_identity_stable_when_artifacts_grow():
    doc1, facts1 = _vocab_doc_and_facts(label="Monto", artifacts=("A", "B"))
    doc2, facts2 = _vocab_doc_and_facts(label="Monto", artifacts=("A", "B", "C"))
    id1, id2 = _vocab_identity(doc1, ("A", "B")), _vocab_identity(doc2, ("A", "B", "C"))
    assert rev.review_technical_id(id1) == rev.review_technical_id(id2)
    sc1, sc2 = rev.build_sidecar(doc1, facts1, "AM_X"), rev.build_sidecar(doc2, facts2, "AM_X")
    tid = rev.review_technical_id(id1)
    assert sc1["footprints"][tid]["digest"] != sc2["footprints"][tid]["digest"]


def test_tf_identity_includes_artifact():
    a = rev.review_identity("TRANSFORMATION_FLOW_CONSOLIDATION_OPPORTUNITY", "AM_X", {"materialized_table": "2TR"}, ["2TR"])
    b = rev.review_identity("TRANSFORMATION_FLOW_CONSOLIDATION_OPPORTUNITY", "AM_X", {"materialized_table": "9ZZ"}, ["9ZZ"])
    assert rev.review_technical_id(a) != rev.review_technical_id(b)


# ═══════════════════════════ 6 · estados de gestión ═══════════════════════════

def _resolve(store, doc, facts, identity, disposition, *, auth=OWNER, **kw):
    sidecar = rev.build_sidecar(doc, facts, "AM_X")
    return ops.resolve_review(client="C", identity=identity, disposition=disposition, rationale="r",
                              decided_at=kw.pop("decided_at", T0), event_id=kw.pop("event_id", "ev-1"),
                              sidecar=sidecar, profile=doc, auth=auth, store=store, **kw)


def test_management_states_pending_vs_settled():
    store = FakeStore()
    doc, facts = _vocab_doc_and_facts()
    vid = _vocab_identity(doc)
    tid = rev.review_technical_id(vid)
    _resolve(store, doc, facts, vid, "CORRECTION_REQUIRED", event_id="m1")
    st = ops.review_status(client="C", technical_id=tid, sidecar=rev.build_sidecar(doc, facts, "AM_X"), profile=doc, now=NOW, presence="PRESENT", store=store)
    assert st["management"] == gd.MGMT_RESOLVED_PENDING_ACTION and st["evidence"] == rr.EVIDENCE_MATCH


def test_accepted_opportunity_present_is_not_implemented():
    store = FakeStore()
    doc, facts = _tf_doc_and_facts()
    tid = rev.review_technical_id(_tf_identity())
    _resolve(store, doc, facts, _tf_identity(), "ACCEPTED", event_id="a1")
    sc = rev.build_sidecar(doc, facts, "AM_X")
    present = ops.review_status(client="C", technical_id=tid, sidecar=sc, profile=doc, now=NOW, presence="PRESENT", store=store)
    assert present["management"] == gd.MGMT_RESOLVED_SETTLED and present["presence"] == "PRESENT" and present["dormant"] is False
    absent = ops.review_status(client="C", technical_id=tid, sidecar=sc, profile=doc, now=NOW, presence="ABSENT", store=store)
    assert absent["dormant"] is True and absent["presence"] == "ABSENT"


# ═══════════════════════════ 7 · bifurcación, reconciliación, re-bifurcación, estado real ═══════════════════════════

def _mk_resolution(identity, disposition, event_id, decided_at, digest, *, supersedes=None, base=None):
    """Construye una resolución directamente (para simular escritores CONCURRENTES que validaron contra el mismo snapshot)."""
    return gd.build_review_resolution(event_id=event_id, subject={"client": "C", "technical_id": rev.review_technical_id(identity)},
                                      klass=identity["klass"], disposition=disposition, digest=digest, rationale="r",
                                      actor="owner@danwor.com", decided_at=decided_at, supersedes_event_ids=supersedes, base_event_id=base)


def test_concurrent_fork_contested_then_reconcile_then_refork():
    store = FakeStore()
    doc, facts = _vocab_doc_and_facts()
    vid = _vocab_identity(doc)
    tid = rev.review_technical_id(vid)
    sc = rev.build_sidecar(doc, facts, "AM_X")
    digest = sc["footprints"][tid]["digest"]
    # Dos escritores CONCURRENTES (ambos vieron cabeza vacía) ⇒ append directo (la precondición es best-effort, no atómica).
    store.append_event(_mk_resolution(vid, "VALIDATED_DESIGN", "f1", "2026-11-09T00:00:00Z", digest, base=None))
    store.append_event(_mk_resolution(vid, "CORRECTION_REQUIRED", "f2", "2026-11-09T00:05:00Z", digest, base=None))
    st = ops.review_status(client="C", technical_id=tid, sidecar=sc, profile=doc, now=NOW, presence="PRESENT", store=store)
    assert st["management"] == gd.MGMT_CONTESTED and set(st["contested_event_ids"]) == {"f1", "f2"}
    # Reconciliación que referencia AMBAS ramas vigentes ⇒ determinable de nuevo; historial conservado.
    ops.reconcile_review(client="C", identity=vid, disposition="VALIDATED_DESIGN", rationale="reconcilia", decided_at="2026-11-09T01:00:00Z",
                         event_id="f3", sidecar=sc, profile=doc, supersedes_event_ids=["f1", "f2"], auth=OWNER, store=store)
    st2 = ops.review_status(client="C", technical_id=tid, sidecar=sc, profile=doc, now=NOW, presence="PRESENT", store=store)
    assert st2["management"] == gd.MGMT_RESOLVED_SETTLED and st2["active_event_id"] == "f3"
    assert len(store.load_events("C", tid)) == 3
    # Bifurcación POSTERIOR: dos escritores concurrentes que superan f3 (ambos vieron f3) ⇒ vuelve a CONTESTED.
    store.append_event(_mk_resolution(vid, "VALIDATED_DESIGN", "f4", "2026-11-09T02:00:00Z", digest, supersedes=["f3"]))
    store.append_event(_mk_resolution(vid, "NOT_APPLICABLE", "f5", "2026-11-09T02:05:00Z", digest, supersedes=["f3"]))
    st3 = ops.review_status(client="C", technical_id=tid, sidecar=sc, profile=doc, now=NOW, presence="PRESENT", store=store)
    assert st3["management"] == gd.MGMT_CONTESTED and set(st3["contested_event_ids"]) == {"f4", "f5"}
    assert len(store.load_events("C", tid)) == 5                                  # historial completo preservado


def test_operations_validate_real_subject_state():
    store = FakeStore()
    doc, facts = _vocab_doc_and_facts()
    vid = _vocab_identity(doc)
    tid = rev.review_technical_id(vid)
    _resolve(store, doc, facts, vid, "VALIDATED_DESIGN", event_id="s1")
    # revocar algo NO vigente ⇒ error de estado real
    with pytest.raises(ops.SubjectStateError):
        ops.revoke_review(client="C", technical_id=tid, revokes_event_id="inexistente", rationale="r", decided_at=T0, event_id="s2", auth=OWNER, store=store)
    # reconciliar referenciando un evento inexistente ⇒ error
    with pytest.raises(ops.SubjectStateError):
        ops.reconcile_review(client="C", identity=vid, disposition="VALIDATED_DESIGN", rationale="r", decided_at=T0, event_id="s3",
                             sidecar=rev.build_sidecar(doc, facts, "AM_X"), profile=doc, supersedes_event_ids=["noexiste"], auth=OWNER, store=store)
    # base_event_id desfasado ⇒ error
    with pytest.raises(ops.SubjectStateError):
        _resolve(store, doc, facts, vid, "NOT_APPLICABLE", event_id="s4", base_event_id="viejo")


def test_revoke_reopens():
    store = FakeStore()
    doc, facts = _vocab_doc_and_facts()
    vid = _vocab_identity(doc)
    tid = rev.review_technical_id(vid)
    _resolve(store, doc, facts, vid, "VALIDATED_DESIGN", event_id="v1")
    ops.revoke_review(client="C", technical_id=tid, revokes_event_id="v1", rationale="r", decided_at="2026-11-09T02:00:00Z", event_id="v2", auth=OWNER, store=store)
    st = ops.review_status(client="C", technical_id=tid, sidecar=rev.build_sidecar(doc, facts, "AM_X"), profile=doc, now=NOW, presence="PRESENT", store=store)
    assert st["management"] == gd.MGMT_OPEN and st["decision"] == rr.DECISION_REVOKED


# ═══════════════════════════ 8 · frontera de autorización ═══════════════════════════

def test_resolve_refuses_without_verifiable_footprint():
    store = FakeStore()
    doc, facts = _tf_doc_and_facts()
    sidecar = rev.build_sidecar(doc, facts, "AM_X")
    with pytest.raises(ops.FootprintUnavailable):
        ops.resolve_review(client="C", identity=_tf_identity(), disposition="ACCEPTED", rationale="r", decided_at=T0,
                           event_id="x1", sidecar=sidecar, profile=_doc(pool_sig="OTRA"), auth=OWNER, store=store)


def test_authorization_requires_explicit_context_and_owner():
    with pytest.raises(ops.AuthorizationDenied):
        ops.authorize_resolution(None)                                            # sin contexto ⇒ nunca autorizado por defecto
    with pytest.raises(ops.AuthorizationDenied):
        ops.authorize_resolution(ops.AuthContext(actor="x", is_owner=False, same_origin=True))
    with pytest.raises(ops.AuthorizationDenied):
        ops.authorize_resolution(ops.AuthContext(actor="x", is_owner=True, same_origin=False))
    with pytest.raises(ops.AuthorizationDenied):
        ops.authorize_resolution(ops.AuthContext(actor="  ", is_owner=True, same_origin=True))
    ops.authorize_resolution(OWNER)


def test_resolve_blocked_without_authorization():
    store = FakeStore()
    doc, facts = _vocab_doc_and_facts()
    with pytest.raises(ops.AuthorizationDenied):
        _resolve(store, doc, facts, _vocab_identity(doc), "VALIDATED_DESIGN", auth=ops.AuthContext(actor="x", is_owner=False, same_origin=True))
    assert store.load_events("C", rev.review_technical_id(_vocab_identity(doc))) == []   # no escribió


def test_actor_is_derived_from_auth_not_caller():
    store = FakeStore()
    doc, facts = _vocab_doc_and_facts()
    vid = _vocab_identity(doc)
    res = _resolve(store, doc, facts, vid, "VALIDATED_DESIGN", event_id="ac1", auth=ops.AuthContext(actor="real@danwor.com", is_owner=True, same_origin=True))
    assert res["event"]["actor"] == "real@danwor.com"                             # el actor SIEMPRE sale del contexto autenticado


def test_audit_is_complementary_event_is_authoritative():
    store = FakeStore()
    seen = []
    doc, facts = _vocab_doc_and_facts()
    vid = _vocab_identity(doc)
    _resolve(store, doc, facts, vid, "VALIDATED_DESIGN", event_id="au1", audit=lambda ev, res: seen.append(ev["event_id"]))
    assert seen == ["au1"]
    assert len(store.load_events("C", rev.review_technical_id(vid))) == 1


# ═══════════════════════════ 9 · persistencia / relectura (envelope sintético) ═══════════════════════════

def test_envelope_roundtrip_binds_and_reconstructs():
    doc, facts = _tf_doc_and_facts()
    sidecar = rev.build_sidecar(doc, facts, "AM_X")
    env = {"profile_json": doc, "review_evidence": sidecar}                        # serialización → relectura
    env2 = json.loads(json.dumps(env))
    prof, sc = env2["profile_json"], env2["review_evidence"]
    assert rev.sidecar_binds_profile(sc, prof)                                     # vínculo perfil/sidecar tras relectura
    tid = rev.review_technical_id(_tf_identity())
    assert rev.current_footprint_digest(tid, sc, prof) == sidecar["footprints"][tid]["digest"]   # huella reconstruida


def test_historical_envelope_without_sidecar_is_unknown():
    store = FakeStore()
    doc, facts = _tf_doc_and_facts()
    tid = rev.review_technical_id(_tf_identity())
    # Resolución registrada cuando SÍ había sidecar...
    _resolve(store, doc, facts, _tf_identity(), "ACCEPTED", event_id="h1")
    # ...pero un Assessment histórico se relee SIN sidecar ⇒ legible, pero evidencia UNKNOWN (nunca MATCH por defecto).
    hist = {"profile_json": doc}                                                   # sin clave review_evidence
    st = ops.review_status(client="C", technical_id=tid, sidecar=hist.get("review_evidence"), profile=hist["profile_json"], now=NOW, presence="PRESENT", store=store)
    assert st["decision"] == rr.DECISION_RATIFIED and st["evidence"] == rr.EVIDENCE_UNKNOWN


def test_envelope_builder_and_backfill_preserve_review_evidence(monkeypatch):
    import webapp.services.assessment_service as asvc
    sc = {"version": rev.REVIEW_EVIDENCE_VERSION, "input_signature": "i", "profile_signature": "p", "footprints": {}}
    # (a) _envelope transporta el sidecar y sobrevive a json
    env = asvc._envelope("C", "AM", status="OK", profile=None, pool_signature="P", versions={}, review_evidence=sc)
    assert json.loads(json.dumps(env))["review_evidence"] == sc
    # (b) un backfill de clasificación hermana NO descarta el sidecar (usa dict(env))
    captured = {}
    monkeypatch.setattr(asvc, "_store_envelope", lambda e, **kw: (captured.update(e), "memory")[1])
    asvc.apply_domain_classification_backfill(dict(env), {"version": "domain/v1", "input_signature": "z"})
    assert captured.get("review_evidence") == sc
