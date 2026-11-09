"""Pruebas FOCALIZADAS de aislamiento entre sujetos de resolución de revisiones (Iteración III).

Origen: en aceptación, «Primer consumidor funcional…» apareció «Reabierta» sin intervención del owner. Diagnóstico: NO fue un
defecto de identidad/presenter/store, sino eventos REALES (resolución + revocación) escritos por el harness de verificación, que
compartía el directorio local de decisiones con el Hub de aceptación. Estas pruebas fijan las garantías que el diagnóstico verificó:

  · dos revisiones distintas ⇒ identidades de gobierno y claves de almacenamiento distintas;
  · resolver/revocar un sujeto no altera otro;
  · sin eventos ⇒ «Sin resolver» (nunca «Reabierta»);
  · «Reabierta» exige una revocación REAL de una resolución previa;
  · releer desde el store (≡ recargar el informe) conserva estados independientes.

Usan el store LOCAL REAL (`governed_decisions_store` + backend local de `core.remote_store`) sobre un `tmp_path` propio de cada
prueba: no tocan el directorio del owner ni Spaces, y no dejan residuos (pytest descarta el tmp_path).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import config
from tools.catalog.modeling_assessment import review_evidence as rev
from webapp.services import governed_decisions as gd
from webapp.services import governed_decisions_store as store
from webapp.services import review_resolution_ops as ops
from webapp.services import review_resolution_presenter as presenter
from webapp.services import review_resolutions as rr

NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)
OWNER = ops.AuthContext(actor="owner@danwor.com", is_owner=True, same_origin=True)
CLIENT = "Biogenesis"
ROOT = "AM_X"


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    """Store local REAL aislado en tmp_path (opt-in del backend local, modo `local`)."""
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setenv("DANWOR_GOVERNED_DECISIONS_DIR", str(tmp_path / "gd"))
    monkeypatch.setenv("DANWOR_GOVERNED_DECISIONS_LOCAL", "1")
    assert config.governed_decisions_local_enabled()
    yield tmp_path / "gd"


class _Fact:
    def __init__(self, key, value):
        self.key, self.value = key, value


def _report():
    """Perfil sintético con DOS revisiones resolubles distintas (TF + LAYER_BOUNDARY_REVIEW), como en Biogénesis."""
    tf_key, lb_key = "harmonization.contract_character@2TR", "ingestion.first_consumers@1TR_MARC"
    groups = [
        {"id": "TRANSFORMATION_FLOW_CONSOLIDATION_OPPORTUNITY", "occurrences": [
            {"occurrence_id": "TRANSFORMATION_FLOW_CONSOLIDATION_OPPORTUNITY-0000", "affected_artifacts": ["2TR"], "evidence_facts": [tf_key], "values": {"materialized_table": "2TR"}}]},
        {"id": "LAYER_BOUNDARY_REVIEW", "occurrences": [
            {"occurrence_id": "LAYER_BOUNDARY_REVIEW-0000", "affected_artifacts": ["1TR_MARC", "3VF"], "evidence_facts": [lb_key], "values": {"source": "1TR_MARC"}}]},
    ]
    doc = {"scope": {"pool_signature": "POOL"}, "versions": {"signals": "signal-rules/v5"}, "review_required": groups, "observations": []}
    facts = [_Fact(tf_key, {"sources": ["2VR"]}), _Fact(lb_key, {"consumers": ["3VF"]})]
    sidecar = rev.build_sidecar(doc, facts, ROOT)
    tf = rev.review_identity("TRANSFORMATION_FLOW_CONSOLIDATION_OPPORTUNITY", ROOT, {"materialized_table": "2TR"}, ["2TR"])
    lb = rev.review_identity("LAYER_BOUNDARY_REVIEW", ROOT, {"source": "1TR_MARC"}, ["1TR_MARC", "3VF"])
    return doc, sidecar, tf, lb


def _status(doc, sidecar, identity):
    tid = rev.review_technical_id(identity)
    return ops.review_status(client=CLIENT, technical_id=tid, sidecar=sidecar, profile=doc, now=NOW, presence="PRESENT", store=store)


def _label(st) -> str:
    return presenter.STATE_LABELS[presenter._state_key(st)][0]


def _resolve(doc, sidecar, identity, disposition, event_id, at):
    return ops.resolve_review(client=CLIENT, identity=identity, disposition=disposition, rationale="r", decided_at=at,
                              event_id=event_id, sidecar=sidecar, profile=doc, auth=OWNER, store=store)


def _revoke(identity, revokes, event_id, at):
    return ops.revoke_review(client=CLIENT, technical_id=rev.review_technical_id(identity), revokes_event_id=revokes, rationale="r",
                             decided_at=at, event_id=event_id, auth=OWNER, store=store)


def test_distinct_reviews_have_distinct_governance_identity_and_storage_keys(local_store):
    _doc, _sc, tf, lb = _report()
    tf_tid, lb_tid = rev.review_technical_id(tf), rev.review_technical_id(lb)
    assert tf_tid != lb_tid
    assert store.subject_prefix({"client": CLIENT, "technical_id": tf_tid}) != store.subject_prefix({"client": CLIENT, "technical_id": lb_tid})
    # la identidad NO usa el índice ordinal de la ocurrencia (`<regla>-0000`): ambas son la ocurrencia 0 de su regla
    assert "0000" not in tf_tid and "0000" not in lb_tid


def test_new_review_without_events_is_unresolved_never_reopened(local_store):
    doc, sc, tf, lb = _report()
    for ident in (tf, lb):
        st = _status(doc, sc, ident)
        assert st["decision"] == rr.DECISION_NONE and st["management"] == gd.MGMT_OPEN
        assert _label(st) == "Sin resolver"


def test_resolve_and_revoke_one_subject_does_not_touch_the_other(local_store):
    doc, sc, tf, lb = _report()
    _resolve(doc, sc, tf, "DISCARDED", "tf-1", "2026-11-09T13:40:00Z")
    assert _label(_status(doc, sc, tf)) == "Cerrada"
    assert _label(_status(doc, sc, lb)) == "Sin resolver"
    _revoke(tf, "tf-1", "tf-2", "2026-11-09T13:41:00Z")
    _resolve(doc, sc, tf, "DISCARDED", "tf-3", "2026-11-09T13:42:00Z")      # ciclo del owner: resolver → revocar → volver a resolver
    assert _label(_status(doc, sc, tf)) == "Cerrada"
    st_lb = _status(doc, sc, lb)
    assert st_lb["events_considered"] == 0 and _label(st_lb) == "Sin resolver"
    assert store.load_events(CLIENT, rev.review_technical_id(lb)) == []


def test_reopened_requires_a_real_revocation_of_a_prior_resolution(local_store):
    doc, sc, _tf, lb = _report()
    _resolve(doc, sc, lb, "VALIDATED_DESIGN", "lb-1", "2026-11-09T14:42:00Z")
    assert _label(_status(doc, sc, lb)) == "Cerrada"                          # resolución vigente ≠ «Reabierta»
    _revoke(lb, "lb-1", "lb-2", "2026-11-09T14:44:00Z")
    st = _status(doc, sc, lb)
    assert st["decision"] == rr.DECISION_REVOKED and _label(st) == "Reabierta"
    events = store.load_events(CLIENT, rev.review_technical_id(lb))
    assert [e["event"] for e in sorted(events, key=lambda e: e["decided_at"])] == [gd.EVENT_RATIFIED, gd.EVENT_REVOKED]


def test_rereading_the_store_preserves_independent_states(local_store):
    doc, sc, tf, lb = _report()
    _resolve(doc, sc, tf, "ACCEPTED", "tf-1", "2026-11-09T10:00:00Z")
    _resolve(doc, sc, lb, "CORRECTION_REQUIRED", "lb-1", "2026-11-09T10:01:00Z")
    first = (_label(_status(doc, sc, tf)), _label(_status(doc, sc, lb)))
    second = (_label(_status(doc, sc, tf)), _label(_status(doc, sc, lb)))     # relectura desde disco ≡ recargar el informe
    assert first == second == ("Cerrada", "Pendiente de remediación")
    # cada sujeto vive SOLO en su carpeta de contrato
    dirs = sorted(p.name for p in (local_store / CLIENT).iterdir())
    assert len(dirs) == 2


def test_presenter_projects_each_occurrence_from_its_own_events(local_store):
    doc, sc, tf, lb = _report()
    _resolve(doc, sc, tf, "DISCARDED", "tf-1", "2026-11-09T13:40:00Z")
    _revoke(tf, "tf-1", "tf-2", "2026-11-09T13:41:00Z")

    class _St:                                                                  # AssessmentStatus mínimo
        profile, review_evidence, client, terminal_am = doc, sc, CLIENT, ROOT

    view = {"review_required": [{"id": g["id"], "occurrences": [{"id": o["occurrence_id"]} for o in g["occurrences"]]}
                                for g in doc["review_required"]]}
    presenter.enrich(view, _St(), is_owner=True, now=NOW, store=store)
    by_rule = {g["id"]: g["occurrences"][0]["resolution"] for g in view["review_required"]}
    assert by_rule["TRANSFORMATION_FLOW_CONSOLIDATION_OPPORTUNITY"]["state_label"] == "Reabierta"
    assert by_rule["LAYER_BOUNDARY_REVIEW"]["state_label"] == "Sin resolver"
    assert by_rule["LAYER_BOUNDARY_REVIEW"]["has_events"] is False
