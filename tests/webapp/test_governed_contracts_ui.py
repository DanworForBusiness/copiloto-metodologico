"""Contrato HTTP de la vista "Contratos Gobernados" (v4.B / B4) + navegación contextual.

Cubre: acceso de CONSULTA para cualquier autenticado (Política B), escritura owner-only intacta, listado/detalle/HTMX, acciones
visibles sólo para el owner, lectura sin efectos laterales, y el enlace contextual desde el informe de Assessment. Fake Spaces +
Assessments reales en tmp_path."""
from __future__ import annotations

import json
from datetime import datetime, timezone
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
from webapp.services import governed_decisions_ops as ops
from webapp.services import governed_decisions_presenter as gpres

ORIGIN = {"origin": "http://testserver"}


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


def _leaf(entity="ABAP_CDS.ZBILL", conn="S4H_PROD", cls="GOVERNED_SAP_CDS_CONTRACT"):
    return {"leaf": "Z", "leaf_kind": "REMOTE_IMPORTED", "contract_class": cls, "classification_basis": "NAMESPACE",
            "classification_bases": ["NAMESPACE"], "boundary_classes": [], "source_systems": ["S4H"],
            "remote_entities": [entity], "remote_connection": conn, "definition": "x"}


def _write_env(root, leaves, client="Kensing", am="AM_X"):
    prof = {"canonical_evidence": {"source_contract_classes": {"leaves": list(leaves)}}}
    env = {"envelope_version": _assess.ENVELOPE_VERSION, "client": client, "terminal_am": am,
           "profile_json": prof, "assessment_artifact_signature": _assess._artifact_signature(prof)}
    d = root / _assess.GOVERNED_LAYOUT_VERSION / (_assess._SAFE.sub("_", client).strip(".") or "_")
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{am}.json").write_text(json.dumps(env), encoding="utf-8")


IDENT = {"remote_connection": "S4H_PROD", "remote_entity": "ABAP_CDS.ZBILL"}


@pytest.fixture()
def env(monkeypatch, tmp_path):
    b = FakeSpaces()
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(config, "SPACES_BUCKET", "fake")
    monkeypatch.setattr(config, "SPACES_PREFIX", "")
    monkeypatch.setattr(config, "PERSIST_PATHS", ["governed-assessments"])
    monkeypatch.setattr(remote_store, "_get_client", lambda: b)
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    root = tmp_path / "governed"
    monkeypatch.setattr(_assess, "governed_dir", lambda: root)
    _write_env(root, [_leaf()])
    # un contrato ratificado para el detalle/listado
    ops.ratify(client="Kensing", identity=IDENT, rationale="contrato gobernado", actor="owner@danwor.com",
               now=datetime(2026, 10, 17, tzinfo=timezone.utc), event_id="e1", request_id="r1")
    return SimpleNamespace(bucket=b, root=root)


def _client(email):
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session(
        {"email": email, "name": email, "first_name": "T", "family_name": "U"}))
    return c


@pytest.fixture()
def owner(): return _client("owner@danwor.com")
@pytest.fixture()
def member(): return _client("member@danwor.com")           # autenticado, NO owner


_DETAIL = {"client": "Kensing", "remote_entity": "ABAP_CDS.ZBILL", "remote_connection": "S4H_PROD"}


# ── acceso de consulta (Política B) ──
def test_list_requires_session(env):
    assert TestClient(app).get("/governed-contracts").status_code == 401


def test_list_ok_for_any_authenticated(env, member):
    r = member.get("/governed-contracts", params={"client": "Kensing"})
    assert r.status_code == 200 and "ABAP_CDS.ZBILL" in r.text


def test_list_empty_without_client(env, member):
    r = member.get("/governed-contracts")
    assert r.status_code == 200 and "Elegí un cliente" in r.text


def test_detail_ok_for_any_authenticated(env, member):
    r = member.get("/governed-contracts/detail", params=_DETAIL)
    assert r.status_code == 200
    assert "ABAP_CDS.ZBILL" in r.text and "GOVERNED_SAP_CDS_CONTRACT" in r.text   # identidad + evidencia observada
    assert "Ratificada" in r.text and "Coincide" in r.text                        # 3 dimensiones


def test_detail_htmx_returns_partial(env, member):
    r = member.get("/governed-contracts/detail", params=_DETAIL, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "<html" not in r.text.lower()                  # fragmento, no página completa


def test_detail_shows_consumers_as_traceability(env, member):
    r = member.get("/governed-contracts/detail", params=_DETAIL)
    assert "AM_X" in r.text and "trazabilidad" in r.text


# ── acciones owner-only en la UI ──
def test_owner_sees_actions(env, owner):
    r = owner.get("/governed-contracts/detail", params=_DETAIL)
    assert "data-gc-actions" in r.text and "Ratificar" in r.text


def test_member_does_not_see_actions(env, member):
    r = member.get("/governed-contracts/detail", params=_DETAIL)
    assert "data-gc-actions" not in r.text


# ── escritura sigue owner-only (B3) ──
def _form(**o):
    base = {"client": "Kensing", "remote_connection": "S4H_PROD", "remote_entity": "ABAP_CDS.ZBILL",
            "rationale": "x", "request_id": "rq-ui-1"}
    base.update(o); return base


def test_member_can_read_status_but_not_write(env, member):
    assert member.get("/governed-decisions/status", params=_DETAIL).status_code == 200       # Política B: lee
    assert member.post("/governed-decisions/ratify", data=_form(), headers=ORIGIN).status_code == 403  # jamás escribe
    assert member.post("/governed-decisions/revoke", data=_form(request_id="rq-ui-2"), headers=ORIGIN).status_code == 403


def test_status_still_401_without_session(env):
    assert TestClient(app).get("/governed-decisions/status", params=_DETAIL).status_code == 401


# ── lectura sin efectos laterales ──
def test_get_views_do_not_write(env, member):
    before = dict(env.bucket.objects)
    member.get("/governed-contracts", params={"client": "Kensing"})
    member.get("/governed-contracts/detail", params=_DETAIL)
    assert env.bucket.objects == before


# ── navegación contextual (presenter) ──
def test_contextual_link_for_remote_imported():
    profile = {"canonical_evidence": {"source_contract_classes": {"leaves": [
        _leaf(entity="ABAP_CDS.ZBILL"),
        {"leaf": "L", "leaf_kind": "LOCAL_FLOW_PRODUCED", "remote_entities": [], "remote_connection": None},
    ]}}}
    links = gpres.governed_contract_links(profile, "Kensing")
    assert len(links) == 1                                   # sólo la hoja REMOTE_IMPORTED
    assert links[0]["remote_entity"] == "ABAP_CDS.ZBILL"
    assert "/governed-contracts/detail?" in links[0]["url"] and "ABAP_CDS.ZBILL" in links[0]["url"]


def test_contextual_link_skips_ambiguous_identity():
    profile = {"canonical_evidence": {"source_contract_classes": {"leaves": [
        {"leaf": "L", "leaf_kind": "REMOTE_IMPORTED", "contract_class": "X", "classification_bases": [],
         "remote_entities": ["A", "B"], "remote_connection": "C"},   # 2 entidades → identidad ambigua
    ]}}}
    assert gpres.governed_contract_links(profile, "Kensing") == []
