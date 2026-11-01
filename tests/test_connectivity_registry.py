"""F2.2b — registro gobernado `connectivity-registry/v1` (cliente → tenant → Space → environment) y su almacén en Spaces (FALSO en memoria).
Cubre identidad estable, dimensiones separadas, asignaciones gobernadas sin inferencias, conflictos, reasignaciones/revocaciones con historia,
aislamiento entre clientes, autorización, idempotencia, confirmación por bytes y replay determinista. Sin SAP, sin red."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import config
from core import remote_store
from tools.catalog.connectivity_governance import reconciliation as rc
from tools.catalog.connectivity_governance import registry as reg
from webapp.services import connectivity_registry_store as store

C = {"client": "Biogenesis", "actor": "owner@danwor.com", "rationale": "alta inicial", "provenance": {"kind": "owner_declaration"}}


def _c(eid, at, **over):
    return {**C, "event_id": eid, "decided_at": at, **over}


def _base():
    return [reg.register_tenant(tenant_id="TNT-BIO-1", display_name="Biogénesis DEV/QA", **_c("e1", "2026-11-01T10:00:00Z")),
            reg.bind_host(tenant_id="TNT-BIO-1", host="bio-devqa.eu10.hcs.cloud.sap", **_c("e2", "2026-11-01T10:01:00Z")),
            reg.assign_space(tenant_id="TNT-BIO-1", space="DESARROLLO", environment="DEV", **_c("e3", "2026-11-01T10:02:00Z")),
            reg.assign_space(tenant_id="TNT-BIO-1", space="CALIDAD", environment="QAS", **_c("e4", "2026-11-01T10:03:00Z"))]


# ═══════════════ identidad y dimensiones ═══════════════
def test_client_tenant_space_environment_relation():
    st = reg.replay(_base())
    assert st["rejected"] == []
    assert [t["tenant_id"] for t in reg.tenants_of(st, "Biogenesis")] == ["TNT-BIO-1"]
    assert reg.space_environments(st, "Biogenesis", "TNT-BIO-1") == {"CALIDAD": "QAS", "DESARROLLO": "DEV"}
    scope = reg.governed_scope(st, "Biogenesis", "TNT-BIO-1")
    assert isinstance(scope, rc.GovernedScope) and scope.space_environments == {"CALIDAD": "QAS", "DESARROLLO": "DEV"}


def test_tenant_identity_is_independent_of_host_name_and_environment():
    events = _base() + [reg.bind_host(tenant_id="TNT-BIO-1", host="bio-nuevo.eu10.hcs.cloud.sap", **_c("e5", "2026-11-02T00:00:00Z"))]
    st = reg.replay(events)
    t = st["tenants"]["TNT-BIO-1"]
    assert [h["host"] for h in t["hosts"]] == ["bio-devqa.eu10.hcs.cloud.sap", "bio-nuevo.eu10.hcs.cloud.sap"]   # dos accesos, una identidad
    assert reg.tenant_for_host(st, "BIO-NUEVO.eu10.hcs.cloud.sap.") == "TNT-BIO-1"                       # coincidencia exacta normalizada
    assert reg.tenant_for_host(st, "bio-devqa-x.eu10.hcs.cloud.sap") is None                            # sin inferencias por parecido
    assert "environment" not in t and t["display_name"] == "Biogénesis DEV/QA"                          # el nombre no define el ambiente


def test_environment_is_never_inferred_from_names_or_urls():
    st = reg.replay([_base()[0], reg.bind_host(tenant_id="TNT-BIO-1", host="prd-bio.eu10.hcs.cloud.sap", **_c("e2", "2026-11-01T10:01:00Z"))])
    assert reg.space_environments(st, "Biogenesis", "TNT-BIO-1") == {}                                   # «prd» en el host no asigna nada
    assert reg.governed_scope(st, "Biogenesis", "TNT-BIO-1").space_environments == {}
    with pytest.raises(reg.RegistryError, match="vocabulario"):
        reg.assign_space(tenant_id="TNT-BIO-1", space="DESARROLLO", environment="Desarrollo", **_c("x", "2026-11-01T10:00:00Z"))


def test_registry_keeps_physical_destination_and_logical_family_out():
    for kind_fields in ({"tenant_id": "T", "display_name": None, "sysid": "EAD"}, {"tenant_id": "T", "display_name": None, "logical_system_id": "S4"}):
        ev = {**_c("x", "2026-11-01T10:00:00Z"), "schema": reg.SCHEMA, "kind": reg.TENANT_REGISTERED, **kind_fields}
        with pytest.raises(reg.RegistryError, match="campos fuera del contrato"):
            reg.validate_event(ev)


@pytest.mark.parametrize("host", ["https://bio.eu10.hcs.cloud.sap", "bio.eu10.hcs.cloud.sap:443", "bio.eu10.hcs.cloud.sap/x", "localhost", ""])
def test_hosts_must_be_plain_hostnames(host):
    with pytest.raises(reg.RegistryError, match="host"):
        reg.bind_host(tenant_id="TNT-BIO-1", host=host, **_c("x", "2026-11-01T10:00:00Z"))


# ═══════════════ conflictos ═══════════════
def _reasons(st):
    return {r["event_id"]: r["reason"] for r in st["rejected"]}


def test_uniqueness_conflicts_are_rejected_and_not_applied():
    events = _base() + [
        reg.register_tenant(tenant_id="TNT-BIO-1", **_c("d1", "2026-11-02T00:00:00Z")),
        reg.assign_space(tenant_id="TNT-BIO-1", space="DESARROLLO", environment="PRD", **_c("d2", "2026-11-02T00:01:00Z")),
        reg.assign_space(tenant_id="TNT-XX", space="S", environment="DEV", **_c("d3", "2026-11-02T00:02:00Z")),
        reg.revoke_assignment(tenant_id="TNT-BIO-1", revokes_event_id="nope", **_c("d4", "2026-11-02T00:03:00Z")),
        reg.assign_space(tenant_id="TNT-BIO-1", space="NUEVO", environment="DEV", supersedes_event_id="e3", **_c("d5", "2026-11-02T00:04:00Z")),
    ]
    st = reg.replay(events)
    assert _reasons(st) == {"d1": "TENANT_ALREADY_REGISTERED", "d2": "SPACE_ALREADY_ASSIGNED", "d3": "TENANT_NOT_REGISTERED",
                            "d4": "REVOKES_NON_ACTIVE_ASSIGNMENT", "d5": "SUPERSEDES_WITHOUT_ACTIVE_ASSIGNMENT"}
    assert reg.space_environments(st, "Biogenesis", "TNT-BIO-1") == {"CALIDAD": "QAS", "DESARROLLO": "DEV"}    # nada rechazado se aplicó


def test_duplicate_event_ids_are_idempotent_or_conflicting():
    base = _base()
    st = reg.replay(base + [dict(base[2])])
    assert st["rejected"] == []
    other = dict(base[2], environment="PRD")
    st = reg.replay(base + [other])
    assert _reasons(st) == {"e3": "DUPLICATE_ID_DIFFERENT_CONTENT"}


# ═══════════════ reasignaciones y revocaciones ═══════════════
def test_reassignment_requires_the_active_assignment_and_keeps_history():
    events = _base() + [reg.assign_space(tenant_id="TNT-BIO-1", space="DESARROLLO", environment="QAS", supersedes_event_id="e3",
                                         **_c("r1", "2026-11-03T00:00:00Z", rationale="el Space pasó a calidad"))]
    st = reg.replay(events)
    assert st["rejected"] == [] and reg.space_environments(st, "Biogenesis", "TNT-BIO-1")["DESARROLLO"] == "QAS"
    hist = [h for h in st["tenants"]["TNT-BIO-1"]["history"] if h["space"] == "DESARROLLO"]
    assert [(h["event_id"], h["environment"], h["supersedes_event_id"]) for h in hist] == [("e3", "DEV", None), ("r1", "QAS", "e3")]
    stale = reg.assign_space(tenant_id="TNT-BIO-1", space="DESARROLLO", environment="PRD", supersedes_event_id="e3", **_c("r2", "2026-11-04T00:00:00Z"))
    assert _reasons(reg.replay(events + [stale])) == {"r2": "SPACE_ALREADY_ASSIGNED"}                      # ya no es la vigente


def test_revocation_removes_the_assignment_and_allows_a_new_one():
    events = _base() + [reg.revoke_assignment(tenant_id="TNT-BIO-1", revokes_event_id="e4", **_c("v1", "2026-11-03T00:00:00Z")),
                        reg.assign_space(tenant_id="TNT-BIO-1", space="CALIDAD", environment="PRD", **_c("v2", "2026-11-04T00:00:00Z"))]
    st = reg.replay(events)
    assert st["rejected"] == [] and reg.space_environments(st, "Biogenesis", "TNT-BIO-1")["CALIDAD"] == "PRD"


def test_replay_is_deterministic_regardless_of_arrival_order():
    events = _base() + [reg.assign_space(tenant_id="TNT-BIO-1", space="DESARROLLO", environment="QAS", supersedes_event_id="e3", **_c("r1", "2026-11-03T00:00:00Z"))]
    assert reg.replay(events) == reg.replay(list(reversed(events)))


# ═══════════════ aislamiento entre clientes ═══════════════
def test_clients_are_isolated():
    other = {**C, "client": "Loma"}
    events = _base() + [
        reg.register_tenant(tenant_id="TNT-LOMA-1", **{**other, "event_id": "l1", "decided_at": "2026-11-01T11:00:00Z"}),
        reg.assign_space(tenant_id="TNT-BIO-1", space="ROBADO", environment="DEV", **{**other, "event_id": "l2", "decided_at": "2026-11-01T11:01:00Z"}),
        reg.register_tenant(tenant_id="TNT-BIO-1", **{**other, "event_id": "l3", "decided_at": "2026-11-01T11:02:00Z"}),
        reg.bind_host(tenant_id="TNT-LOMA-1", host="bio-devqa.eu10.hcs.cloud.sap", **{**other, "event_id": "l4", "decided_at": "2026-11-01T11:03:00Z"}),
    ]
    st = reg.replay(events)
    assert _reasons(st) == {"l2": "TENANT_BELONGS_TO_ANOTHER_CLIENT", "l3": "TENANT_BELONGS_TO_ANOTHER_CLIENT", "l4": "HOST_BOUND_TO_ANOTHER_TENANT"}
    assert [t["tenant_id"] for t in reg.tenants_of(st, "Loma")] == ["TNT-LOMA-1"]
    assert reg.space_environments(st, "Loma", "TNT-BIO-1") == {} and reg.governed_scope(st, "Loma", "TNT-BIO-1") is None
    assert reg.tenant_for_host(st, "bio-devqa.eu10.hcs.cloud.sap", client="Loma") is None


# ═══════════════ almacén en Spaces (falso) ═══════════════
class _NoSuchKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}


class FakeSpaces:
    def __init__(self):
        self.objects, self.corrupt = {}, False

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = b"CORRUPTO" if self.corrupt else bytes(Body)

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


@pytest.fixture()
def spaces(monkeypatch):
    bucket = FakeSpaces()
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(config, "SPACES_BUCKET", "fake-bucket")
    monkeypatch.setattr(config, "SPACES_PREFIX", "")
    monkeypatch.setattr(config, "PERSIST_PATHS", ["governed-assessments"])
    monkeypatch.setattr(remote_store, "_get_client", lambda: bucket)
    return bucket


def test_store_persists_in_the_existing_governed_namespace_and_rebuilds_state(spaces):
    for ev in _base():
        assert store.append_event(ev, authorized_owner=True)["stored"] is True
    assert all(k.startswith("governed-decisions/_connectivity-registry/Biogenesis/") for k in spaces.objects)
    st = store.load_state()
    assert reg.space_environments(st, "Biogenesis", "TNT-BIO-1") == {"CALIDAD": "QAS", "DESARROLLO": "DEV"} and st["rejected"] == []


def test_store_requires_owner_and_rejects_conflicts_before_writing(spaces):
    base = _base()
    with pytest.raises(store.NotAuthorized):
        store.append_event(base[0], authorized_owner=False)
    assert spaces.objects == {}
    for ev in base:
        store.append_event(ev, authorized_owner=True)
    n = len(spaces.objects)
    with pytest.raises(store.RegistryConflict, match="SPACE_ALREADY_ASSIGNED"):
        store.append_event(reg.assign_space(tenant_id="TNT-BIO-1", space="DESARROLLO", environment="PRD", **_c("x1", "2026-11-05T00:00:00Z")),
                           authorized_owner=True)
    with pytest.raises(store.RegistryConflict, match="TENANT_BELONGS_TO_ANOTHER_CLIENT"):
        store.append_event(reg.assign_space(tenant_id="TNT-BIO-1", space="S", environment="DEV",
                                            **{**C, "client": "Loma", "event_id": "x2", "decided_at": "2026-11-05T00:00:00Z"}), authorized_owner=True)
    assert len(spaces.objects) == n                                                     # no se escribió nada


def test_store_is_idempotent_and_never_overwrites(spaces):
    ev = _base()[0]
    store.append_event(ev, authorized_owner=True)
    assert store.append_event(ev, authorized_owner=True) == {"stored": False, "idempotent": True, "key": store.event_key(ev)}
    with pytest.raises(store.RegistryConflict, match="contenido distinto"):
        store.append_event({**ev, "display_name": "otro"}, authorized_owner=True)


def test_store_confirms_by_bytes_and_fails_closed_on_corruption(spaces):
    spaces.corrupt = True
    with pytest.raises(store.StorageUnconfirmed):
        store.append_event(_base()[0], authorized_owner=True)
    with pytest.raises(store.HistoryIntegrityError):
        store.load_state()


def test_store_rejects_an_event_stored_under_another_clients_path(spaces):
    ev = _base()[0]
    key = store.client_prefix("Loma") + "20261101T100000Z-e1.json"
    spaces.objects[key] = json.dumps(ev).encode("utf-8")
    with pytest.raises(store.HistoryIntegrityError, match="cliente"):
        store.load_state()


def test_registry_namespace_does_not_collide_with_judgment_ratifications():
    assert store.PREFIX.startswith(remote_store.GOVERNED_DECISIONS_NAMESPACE + "/_connectivity-registry/")
    assert "_connectivity-registry" not in {"Biogenesis", "Loma"}
