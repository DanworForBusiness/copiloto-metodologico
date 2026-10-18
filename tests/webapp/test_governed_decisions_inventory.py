"""Tests del inventario READ-ONLY por cliente (`governed_decisions_inventory`, v4.B / B4). Verifica el SINGLE-PASS (1 cross-scan
+ 1 list_client_keys), los estados en 3 dimensiones (RATIFIED/REVOKED/NONE × MATCH/STALE/UNKNOWN × CURRENT/DUE), evidencia
contradictoria (no rompe), contratos decision-only, y que la consulta NO escribe. Fake Spaces en memoria + Assessments reales en
tmp_path."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import config
from core import remote_store
from webapp.services import assessment_service as _assess
from webapp.services import governed_decisions as gd
from webapp.services import governed_decisions_inventory as inv
from webapp.services import governed_decisions_ops as ops


class _NoSuchKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}


class FakeSpaces:
    def __init__(self): self.objects = {}
    def put_object(self, Bucket, Key, Body): self.objects[Key] = bytes(Body)
    def get_object(self, Bucket, Key):
        if Key not in self.objects: raise _NoSuchKey()
        return {"Body": SimpleNamespace(read=lambda k=Key: self.objects[k])}
    def get_paginator(self, _n):
        b = self
        class _P:
            def paginate(self, Bucket, Prefix):
                return [{"Contents": [{"Key": k} for k in sorted(b.objects) if k.startswith(Prefix)]}]
        return _P()


class GovStore:
    def __init__(self, root): self.root = root
    def _dir(self, client):
        d = self.root / _assess.GOVERNED_LAYOUT_VERSION / (_assess._SAFE.sub("_", client).strip(".") or "_")
        d.mkdir(parents=True, exist_ok=True); return d
    def write(self, *, client="Kensing", am="AM_X", leaves):
        prof = {"canonical_evidence": {"source_contract_classes": {"leaves": list(leaves)}}}
        env = {"envelope_version": _assess.ENVELOPE_VERSION, "client": client, "terminal_am": am,
               "profile_json": prof, "assessment_artifact_signature": _assess._artifact_signature(prof)}
        (self._dir(client) / f"{am}.json").write_text(json.dumps(env), encoding="utf-8")


def _leaf(entity="ABAP_CDS.ZBILL", conn="S4H_PROD", cls="GOVERNED_SAP_CDS_CONTRACT", bases=("NAMESPACE",)):
    return {"leaf": "Z", "leaf_kind": "REMOTE_IMPORTED", "contract_class": cls, "classification_basis": bases[0],
            "classification_bases": list(bases), "boundary_classes": [], "source_systems": ["S4H"],
            "remote_entities": [entity], "remote_connection": conn, "definition": "x"}


@pytest.fixture()
def env(monkeypatch, tmp_path):
    b = FakeSpaces()
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(config, "SPACES_BUCKET", "fake")
    monkeypatch.setattr(config, "SPACES_PREFIX", "")
    monkeypatch.setattr(config, "PERSIST_PATHS", ["governed-assessments"])
    monkeypatch.setattr(remote_store, "_get_client", lambda: b)
    root = tmp_path / "governed"
    monkeypatch.setattr(_assess, "governed_dir", lambda: root)
    return SimpleNamespace(bucket=b, gov=GovStore(root), root=root)


IDENT = {"remote_connection": "S4H_PROD", "remote_entity": "ABAP_CDS.ZBILL"}
NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


def _ratify(client="Kensing", identity=None, rationale="x", now=NOW, eid="e1", rid="r1"):
    return ops.ratify(client=client, identity=identity or IDENT, rationale=rationale, actor="owner@danwor.com",
                      now=now, event_id=eid, request_id=rid)


def test_list_clients_distinct(env):
    env.gov.write(client="Kensing", am="A", leaves=[_leaf()])
    env.gov.write(client="Kensing", am="B", leaves=[_leaf(entity="ABAP_CDS.O")])
    env.gov.write(client="Otro", am="C", leaves=[_leaf()])
    assert inv.list_clients() == ["Kensing", "Otro"]


def test_single_pass_one_scan_one_list(env, monkeypatch):
    env.gov.write(am="A", leaves=[_leaf(entity="ABAP_CDS.ZBILL")])
    env.gov.write(am="B", leaves=[_leaf(entity="ABAP_CDS.OTRO")])
    _ratify()
    scans = {"n": 0}; lists = {"n": 0}
    real_scan = ops._iter_client_envelopes_strict
    real_list = inv.store.list_client_keys
    def scan(c): scans["n"] += 1; return list(real_scan(c))
    def lst(c): lists["n"] += 1; return real_list(c)
    monkeypatch.setattr(ops, "_iter_client_envelopes_strict", scan)
    monkeypatch.setattr(inv.store, "list_client_keys", lst)
    rows = inv.list_client_contracts("Kensing", NOW)
    assert len(rows) == 2                         # dos contratos (dos identidades)
    assert scans["n"] == 1 and lists["n"] == 1    # SINGLE-PASS: no repite por contrato


def test_ratified_contract_match_and_current(env):
    env.gov.write(leaves=[_leaf()])
    _ratify(now=datetime(2026, 10, 17, tzinfo=timezone.utc))
    rows = inv.list_client_contracts("Kensing", datetime(2026, 10, 20, tzinfo=timezone.utc))
    r = next(x for x in rows if x["identity"] == IDENT)
    assert r["decision"] == gd.DECISION_RATIFIED and r["evidence"] == gd.EVIDENCE_MATCH and r["review"] == gd.REVIEW_CURRENT


def test_revoked_contract(env):
    env.gov.write(leaves=[_leaf()])
    _ratify(eid="e1", rid="r1")
    ops.revoke(client="Kensing", identity=IDENT, rationale="corr", actor="owner@danwor.com",
               now=datetime(2026, 10, 18, tzinfo=timezone.utc), event_id="e2", request_id="r2")
    r = inv.list_client_contracts("Kensing", datetime(2026, 10, 19, tzinfo=timezone.utc))[0]
    assert r["decision"] == gd.DECISION_REVOKED


def test_stale_evidence_after_contract_change(env):
    env.gov.write(leaves=[_leaf(cls="GOVERNED_SAP_CDS_CONTRACT")])
    _ratify()
    env.gov.write(leaves=[_leaf(cls="EXTERNAL_PHYSICAL_OR_TECHNICAL")])   # cambia el slice ⇒ digest distinto
    r = inv.list_client_contracts("Kensing", datetime(2026, 10, 20, tzinfo=timezone.utc))[0]
    assert r["decision"] == gd.DECISION_RATIFIED and r["evidence"] == gd.EVIDENCE_STALE


def test_unknown_evidence_when_absent(env):
    env.gov.write(leaves=[_leaf()])
    _ratify()
    import shutil
    shutil.rmtree(env.root)                        # sin evidencia: el contrato sigue por sus eventos
    r = inv.list_client_contracts("Kensing", datetime(2026, 10, 20, tzinfo=timezone.utc))[0]
    assert r["decision"] == gd.DECISION_RATIFIED and r["evidence"] == gd.EVIDENCE_UNKNOWN


def test_review_due(env):
    env.gov.write(leaves=[_leaf()])
    _ratify(now=datetime(2026, 1, 1, tzinfo=timezone.utc))   # review_by +12m = 2027-01-01
    r = inv.list_client_contracts("Kensing", datetime(2027, 6, 1, tzinfo=timezone.utc))[0]
    assert r["review"] == gd.REVIEW_DUE            # vencida, pero sigue ratificada (no es caducidad)


def test_ambiguous_evidence_flagged_not_broken(env):
    env.gov.write(am="A", leaves=[_leaf(cls="GOVERNED_SAP_CDS_CONTRACT")])
    env.gov.write(am="B", leaves=[_leaf(cls="EXTERNAL_PHYSICAL_OR_TECHNICAL")])
    rows = inv.list_client_contracts("Kensing", NOW)
    r = next(x for x in rows if x["identity"] == IDENT)
    assert r["evidence_ambiguous"] is True and r["evidence_digest"] is None


def test_decision_only_contract_listed(env):
    # ratifica, luego borra la evidencia → el contrato queda solo por eventos; debe seguir en la lista
    env.gov.write(leaves=[_leaf()])
    _ratify()
    import shutil; shutil.rmtree(env.root)
    rows = inv.list_client_contracts("Kensing", NOW)
    assert any(r["identity"] == IDENT for r in rows)


def test_empty_client(env):
    env.gov.write(client="Kensing", leaves=[_leaf(entity="ABAP_CDS.SOLO_EVIDENCIA")])
    rows = inv.list_client_contracts("Kensing", NOW)
    assert len(rows) == 1 and rows[0]["decision"] == gd.DECISION_NONE   # evidencia sin decisión → NONE


def test_query_does_not_write(env):
    env.gov.write(leaves=[_leaf()])
    _ratify()
    before = dict(env.bucket.objects)
    inv.list_client_contracts("Kensing", NOW)
    assert env.bucket.objects == before          # SOLO LECTURA: no escribe eventos
