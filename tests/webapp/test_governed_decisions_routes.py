"""Contrato HTTP del router `governed_decisions` (v4.B / B3): ratify/revoke/status autenticados por el APP_OWNER.

Verifica la cadena de seguridad (sesión 401 → same-origin 403 → APP_OWNER 403), que `actor`/`event_id`/`decided_at` los IMPONE
el backend (nunca el navegador), el mapeo de errores de la capa funcional a códigos HTTP, idempotencia A, y la independencia de
Judgment. Spaces FALSO en memoria + almacén de Assessments gobernados REAL en tmp_path; no hay Graph/Spaces real, no toca prod."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
from core import remote_store
from webapp import access
from webapp.main import app
from webapp.services import assessment_service as _assess
from webapp.services import auth_service
from webapp.services import governed_decisions as gd

ORIGIN = {"origin": "http://testserver"}


class _NoSuchKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}


class FakeSpaces:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.corrupt_puts = False

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = b"CORRUPTO" if self.corrupt_puts else bytes(Body)

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _NoSuchKey()
        return {"Body": SimpleNamespace(read=lambda k=Key: self.objects[k])}

    def get_paginator(self, _name):
        bucket = self

        class _P:
            def paginate(self, Bucket, Prefix):
                return [{"Contents": [{"Key": k} for k in sorted(bucket.objects) if k.startswith(Prefix)]}]

        return _P()


def _leaf(entity="ABAP_CDS.ZBILL", conn="S4H_PROD", cls="GOVERNED_SAP_CDS_CONTRACT", bases=("NAMESPACE",)):
    return {"leaf": "ZBILL", "leaf_kind": "REMOTE_IMPORTED", "contract_class": cls,
            "classification_basis": bases[0], "classification_bases": list(bases), "boundary_classes": [],
            "source_systems": ["S4H"], "remote_entities": [entity], "remote_connection": conn, "definition": "x"}


def _write_env(root, leaves, client="Kensing", am="AM_X"):
    profile = {"canonical_evidence": {"source_contract_classes": {"leaves": list(leaves)}}}
    env = {"envelope_version": _assess.ENVELOPE_VERSION, "client": client, "terminal_am": am,
           "profile_json": profile, "assessment_artifact_signature": _assess._artifact_signature(profile)}
    d = root / _assess.GOVERNED_LAYOUT_VERSION / (_assess._SAFE.sub("_", client).strip(".") or "_")
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{am}__{abs(hash(am)) % 10**8:08d}.json").write_text(json.dumps(env), encoding="utf-8")


def _write_raw(root, text, client="Kensing", name="broken.json"):
    d = root / _assess.GOVERNED_LAYOUT_VERSION / (_assess._SAFE.sub("_", client).strip(".") or "_")
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")


@pytest.fixture()
def bucket(monkeypatch, tmp_path):
    b = FakeSpaces()
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(config, "SPACES_BUCKET", "fake-bucket")
    monkeypatch.setattr(config, "SPACES_PREFIX", "")
    monkeypatch.setattr(config, "PERSIST_PATHS", ["governed-assessments"])
    monkeypatch.setattr(remote_store, "_get_client", lambda: b)
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    gov_root = tmp_path / "governed"
    monkeypatch.setattr(_assess, "governed_dir", lambda: gov_root)
    _write_env(gov_root, [_leaf()])                                # evidencia válida por defecto para Kensing/ABAP_CDS.ZBILL
    b.gov_root = gov_root
    return b


def _client(email):
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session(
        {"email": email, "name": email, "first_name": "T", "family_name": "U"}))
    return c


@pytest.fixture()
def owner():
    return _client("owner@danwor.com")


@pytest.fixture()
def intruder():
    return _client("intruso@danwor.com")


def _form(**over):
    base = {"client": "Kensing", "remote_connection": "S4H_PROD", "remote_entity": "ABAP_CDS.ZBILL",
            "rationale": "contrato gobernado", "request_id": "req-1"}
    base.update(over)
    return base


# ─────────────────────────── cadena de seguridad ───────────────────────────

def test_ratify_requires_session(bucket):
    assert TestClient(app).post("/governed-decisions/ratify", data=_form(), headers=ORIGIN).status_code == 401


def test_ratify_requires_owner(bucket, intruder):
    r = intruder.post("/governed-decisions/ratify", data=_form(), headers=ORIGIN)
    assert r.status_code == 403 and r.json()["detail"] == "No autorizado"


def test_ratify_requires_same_origin(bucket, owner):
    r = owner.post("/governed-decisions/ratify", data=_form(), headers={"origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["detail"] == "Origen no permitido"


def test_status_requires_owner(bucket, intruder):
    r = intruder.get("/governed-decisions/status", params={"client": "Kensing", "remote_entity": "ABAP_CDS.ZBILL", "remote_connection": "S4H_PROD"})
    assert r.status_code == 403


def test_status_requires_session(bucket):
    r = TestClient(app).get("/governed-decisions/status", params={"client": "Kensing", "remote_entity": "ABAP_CDS.ZBILL"})
    assert r.status_code == 401


# ─────────────────────────── ratificación feliz + identidad backend ───────────────────────────

def test_ratify_ok_and_actor_from_session(bucket, owner):
    r = owner.post("/governed-decisions/ratify", data=_form(), headers=ORIGIN)
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "RATIFIED"
    assert body["consumers"] == ["AM_X"]
    assert len(body["digest"]) == 64
    assert body["event_id"].startswith("gd-")               # generado por backend
    # el actor persistido es la identidad de sesión, jamás algo del cuerpo
    key = next(iter(bucket.objects))
    assert b'"actor":"owner@danwor.com"' in bucket.objects[key]   # JSON canónico compacto


def test_ratify_ignores_actor_from_body(bucket, owner):
    r = owner.post("/governed-decisions/ratify", data=_form(actor="intruso@danwor.com", event_id="forjado", decided_at="1999-01-01T00:00:00+00:00"), headers=ORIGIN)
    assert r.status_code == 200
    blob = bucket.objects[next(iter(bucket.objects))]
    assert b"intruso@danwor.com" not in blob and b"forjado" not in blob and b"1999" not in blob


def test_ratify_evidence_insufficient_404(bucket, owner):
    r = owner.post("/governed-decisions/ratify", data=_form(remote_entity="ABAP_CDS.NO_EXISTE"), headers=ORIGIN)
    assert r.status_code == 404


def test_ratify_evidence_ambiguous_409(bucket, owner):
    _write_env(bucket.gov_root, [_leaf(cls="EXTERNAL_PHYSICAL_OR_TECHNICAL")], am="AM_B")   # divergente del default
    r = owner.post("/governed-decisions/ratify", data=_form(), headers=ORIGIN)
    assert r.status_code == 409


def test_ratify_evidence_unverifiable_409(bucket, owner):
    _write_raw(bucket.gov_root, "{ corrupto")                       # sobre ilegible → fail-closed
    r = owner.post("/governed-decisions/ratify", data=_form(), headers=ORIGIN)
    assert r.status_code == 409


def test_ratify_idempotent_retry(bucket, owner):
    owner.post("/governed-decisions/ratify", data=_form(), headers=ORIGIN)
    n = len(bucket.objects)
    r = owner.post("/governed-decisions/ratify", data=_form(), headers=ORIGIN)   # mismo request_id + contenido
    assert r.status_code == 200 and r.json()["outcome"] == "IDEMPOTENT"
    assert len(bucket.objects) == n


def test_ratify_idempotency_conflict_409(bucket, owner):
    owner.post("/governed-decisions/ratify", data=_form(rationale="A"), headers=ORIGIN)
    r = owner.post("/governed-decisions/ratify", data=_form(rationale="DISTINTO"), headers=ORIGIN)
    assert r.status_code == 409


# ─────────────────────────── revocación ───────────────────────────

def test_revoke_vigente(bucket, owner):
    owner.post("/governed-decisions/ratify", data=_form(), headers=ORIGIN)
    r = owner.post("/governed-decisions/revoke", data=_form(rationale="se corrige", request_id="rev-1"), headers=ORIGIN)
    assert r.status_code == 200 and r.json()["outcome"] == "REVOKED"


def test_revoke_idempotent_retry(bucket, owner):
    owner.post("/governed-decisions/ratify", data=_form(), headers=ORIGIN)
    owner.post("/governed-decisions/revoke", data=_form(rationale="se corrige", request_id="rev-1"), headers=ORIGIN)
    n = len(bucket.objects)
    r = owner.post("/governed-decisions/revoke", data=_form(rationale="se corrige", request_id="rev-1"), headers=ORIGIN)
    assert r.status_code == 200 and r.json()["outcome"] == "IDEMPOTENT"
    assert len(bucket.objects) == n


def test_revoke_without_ratification_409(bucket, owner):
    r = owner.post("/governed-decisions/revoke", data=_form(rationale="x", request_id="rev-1"), headers=ORIGIN)
    assert r.status_code == 409


def test_revoke_requires_same_origin(bucket, owner):
    r = owner.post("/governed-decisions/revoke", data=_form(request_id="rev-1"), headers={"origin": "http://evil.example"})
    assert r.status_code == 403


# ─────────────────────────── consulta técnica ───────────────────────────

def test_status_three_dimensions(bucket, owner):
    owner.post("/governed-decisions/ratify", data=_form(), headers=ORIGIN)
    r = owner.get("/governed-decisions/status", params={"client": "Kensing", "remote_entity": "ABAP_CDS.ZBILL", "remote_connection": "S4H_PROD"})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == gd.DECISION_RATIFIED
    assert set(body) >= {"decision", "evidence", "review", "principal", "events", "consumers"}
    assert len(body["events"]) == 1


def test_status_none_when_empty(bucket, owner):
    r = owner.get("/governed-decisions/status", params={"client": "Kensing", "remote_entity": "ABAP_CDS.ZBILL", "remote_connection": "S4H_PROD"})
    assert r.status_code == 200 and r.json()["decision"] == gd.DECISION_NONE


def test_status_get_has_no_write_side_effects(bucket, owner):
    before = dict(bucket.objects)
    owner.get("/governed-decisions/status", params={"client": "Kensing", "remote_entity": "ABAP_CDS.ZBILL", "remote_connection": "S4H_PROD"})
    assert bucket.objects == before                                 # GET no escribe nada


def test_storage_unconfirmed_returns_503_not_false_success(bucket, owner):
    bucket.corrupt_puts = True
    r = owner.post("/governed-decisions/ratify", data=_form(), headers=ORIGIN)
    assert r.status_code == 503                                     # confirmación por bytes falla → jamás 200


def test_error_detail_does_not_leak_internals(bucket, owner):
    r = owner.post("/governed-decisions/ratify", data=_form(remote_entity="ABAP_CDS.NO_EXISTE"), headers=ORIGIN)
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail == "Evidencia de contrato insuficiente o identidad inexistente"   # mensaje fijo, sin trazas/rutas
    assert "Traceback" not in detail and "/" not in detail and "\\" not in detail


def test_status_same_origin_not_required_for_get(bucket, owner):
    # GET no es escritura: no exige same-origin (no manda Origin) y responde normal
    r = owner.get("/governed-decisions/status", params={"client": "Kensing", "remote_entity": "ABAP_CDS.ZBILL", "remote_connection": "S4H_PROD"})
    assert r.status_code == 200
