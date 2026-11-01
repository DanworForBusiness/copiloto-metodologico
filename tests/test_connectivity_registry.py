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
    assert store.append_event(ev, authorized_owner=True) == {"stored": False, "idempotent": True, "applied": True, "key": store.event_key(ev)}
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


# ═══════════════ F2.2c.1 — desvinculación de hosts, desactivación de tenants, relectura posterior ═══════════════
def test_host_unbound_frees_the_host_and_keeps_history():
    events = _base() + [reg.unbind_host(tenant_id="TNT-BIO-1", unbinds_event_id="e2", **_c("u1", "2026-11-02T10:00:00Z", rationale="host equivocado"))]
    st = reg.replay(events)
    assert st["rejected"] == [] and st["tenants"]["TNT-BIO-1"]["hosts"] == [] and st["hosts"] == {}
    kinds = [(h["kind"], h["host"]) for h in st["tenants"]["TNT-BIO-1"]["history"] if h["host"]]
    assert kinds == [("HOST_BOUND", "bio-devqa.eu10.hcs.cloud.sap"), ("HOST_UNBOUND", "bio-devqa.eu10.hcs.cloud.sap")]
    # el host liberado puede vincularse a OTRO tenant (corrección de una asociación equivocada)
    events += [reg.register_tenant(tenant_id="TNT-BIO-2", **_c("t2", "2026-11-02T10:01:00Z")),
               reg.bind_host(tenant_id="TNT-BIO-2", host="bio-devqa.eu10.hcs.cloud.sap", **_c("b2", "2026-11-02T10:02:00Z"))]
    st = reg.replay(events)
    assert st["rejected"] == [] and reg.tenant_for_host(st, "BIO-DEVQA.eu10.hcs.cloud.sap.") == "TNT-BIO-2"


def test_host_unbound_requires_an_active_binding_of_that_tenant():
    events = _base() + [reg.unbind_host(tenant_id="TNT-BIO-1", unbinds_event_id="e3", **_c("u1", "2026-11-02T10:00:00Z")),       # no es un vínculo
                        reg.unbind_host(tenant_id="TNT-BIO-1", unbinds_event_id="e2", **_c("u2", "2026-11-02T10:01:00Z")),
                        reg.unbind_host(tenant_id="TNT-BIO-1", unbinds_event_id="e2", **_c("u3", "2026-11-02T10:02:00Z"))]      # ya desvinculado
    assert _reasons(reg.replay(events)) == {"u1": "UNBINDS_NON_ACTIVE_BINDING", "u3": "UNBINDS_NON_ACTIVE_BINDING"}
    with pytest.raises(reg.RegistryError):
        reg.unbind_host(tenant_id="TNT-BIO-1", unbinds_event_id="e2", host="x.y.z", **_c("u4", "2026-11-02T10:03:00Z"))


def test_tenant_deactivation_is_blocked_while_bindings_or_assignments_are_active():
    base = _base()
    st = reg.replay(base + [reg.deactivate_tenant(tenant_id="TNT-BIO-1", **_c("d1", "2026-11-02T10:00:00Z"))])
    assert _reasons(st) == {"d1": "TENANT_HAS_ACTIVE_BINDINGS"} and st["tenants"]["TNT-BIO-1"]["active"] is True
    cleanup = [reg.unbind_host(tenant_id="TNT-BIO-1", unbinds_event_id="e2", **_c("u1", "2026-11-02T10:01:00Z")),
               reg.revoke_assignment(tenant_id="TNT-BIO-1", revokes_event_id="e3", **_c("r1", "2026-11-02T10:02:00Z")),
               reg.revoke_assignment(tenant_id="TNT-BIO-1", revokes_event_id="e4", **_c("r2", "2026-11-02T10:03:00Z")),
               reg.deactivate_tenant(tenant_id="TNT-BIO-1", **_c("d2", "2026-11-02T10:04:00Z", rationale="tenant_id mal tipeado"))]
    st = reg.replay(base + cleanup)
    t = st["tenants"]["TNT-BIO-1"]
    assert st["rejected"] == [] and t["active"] is False and t["deactivation"]["event_id"] == "d2"
    assert reg.governed_scope(st, "Biogenesis", "TNT-BIO-1") is None
    assert len(t["history"]) == len(base + cleanup)                                    # nada se borra


def test_deactivated_tenant_accepts_no_events_and_its_id_is_never_reused():
    events = [reg.register_tenant(tenant_id="TNT-X", **_c("a1", "2026-11-01T10:00:00Z")),
              reg.deactivate_tenant(tenant_id="TNT-X", **_c("a2", "2026-11-01T10:01:00Z")),
              reg.bind_host(tenant_id="TNT-X", host="x.hana.ondemand.com", **_c("a3", "2026-11-01T10:02:00Z")),
              reg.assign_space(tenant_id="TNT-X", space="S", environment="DEV", **_c("a4", "2026-11-01T10:03:00Z")),
              reg.deactivate_tenant(tenant_id="TNT-X", **_c("a5", "2026-11-01T10:04:00Z")),
              reg.register_tenant(tenant_id="TNT-X", **_c("a6", "2026-11-01T10:05:00Z")),
              reg.register_tenant(tenant_id="TNT-X", **{**_c("a7", "2026-11-01T10:06:00Z"), "client": "Loma"})]
    assert _reasons(reg.replay(events)) == {"a3": "TENANT_DEACTIVATED", "a4": "TENANT_DEACTIVATED", "a5": "TENANT_DEACTIVATED",
                                            "a6": "TENANT_ALREADY_REGISTERED", "a7": "TENANT_BELONGS_TO_ANOTHER_CLIENT"}


def test_new_events_cannot_cross_clients():
    events = _base() + [reg.unbind_host(tenant_id="TNT-BIO-1", unbinds_event_id="e2", **{**_c("x1", "2026-11-02T10:00:00Z"), "client": "Loma"}),
                        reg.deactivate_tenant(tenant_id="TNT-BIO-1", **{**_c("x2", "2026-11-02T10:01:00Z"), "client": "Loma"})]
    assert _reasons(reg.replay(events)) == {"x1": "TENANT_BELONGS_TO_ANOTHER_CLIENT", "x2": "TENANT_BELONGS_TO_ANOTHER_CLIENT"}


def test_registry_events_reject_homologation_fields():
    """Asignar un ambiente NO homologa conexiones: ningún evento admite campos de `connectivity-homologation/v1`."""
    ev = _base()[2]
    for field in ("logical_system_id", "related_connections", "expected_agent", "expected_physical", "connection_name"):
        with pytest.raises(reg.RegistryError, match="campos fuera del contrato"):
            reg.validate_event({**ev, field: "x"})


def test_store_retry_with_same_event_id_is_idempotent_even_with_another_date(spaces):
    ev = _base()[0]
    store.append_event(ev, authorized_owner=True)
    out = store.append_event({**ev, "decided_at": "2026-11-09T00:00:00Z"}, authorized_owner=True)
    assert out == {"stored": False, "idempotent": True, "applied": True, "key": store.event_key(ev)} and len(spaces.objects) == 1


def _race(monkeypatch, competitor):
    """Simula OTRA escritura que entra entre el chequeo previo y el PUT (check-then-PUT no es atómico)."""
    real_put = remote_store.put_governed_decision_object

    def put(key, data):
        real_put(store.event_key(competitor), reg.canonical_json(competitor).encode("utf-8"))
        monkeypatch.setattr(remote_store, "put_governed_decision_object", real_put)
        return real_put(key, data)
    monkeypatch.setattr(remote_store, "put_governed_decision_object", put)


def test_concurrent_write_that_loses_the_replay_is_reported_not_applied(spaces, monkeypatch):
    for ev in _base()[:2]:
        store.append_event(ev, authorized_owner=True)
    rival = reg.assign_space(tenant_id="TNT-BIO-1", space="SANDBOX", environment="DEV", **_c("rival", "2026-11-02T10:00:00Z"))
    mine = reg.assign_space(tenant_id="TNT-BIO-1", space="SANDBOX", environment="PRD", **_c("mine", "2026-11-02T10:00:05Z"))
    _race(monkeypatch, rival)
    with pytest.raises(store.ConcurrentConflict) as info:
        store.append_event(mine, authorized_owner=True)
    assert info.value.reason == "SPACE_ALREADY_ASSIGNED" and info.value.key == store.event_key(mine)
    st = store.load_state()                                                             # quedó guardado, pero NO aplicado
    assert store.event_key(mine) in spaces.objects and reg.space_environments(st, "Biogenesis", "TNT-BIO-1")["SANDBOX"] == "DEV"
    assert {r["event_id"]: r["reason"] for r in st["rejected"]} == {"mine": "SPACE_ALREADY_ASSIGNED"}


def test_concurrent_write_that_wins_the_replay_is_applied(spaces, monkeypatch):
    for ev in _base()[:2]:
        store.append_event(ev, authorized_owner=True)
    rival = reg.bind_host(tenant_id="TNT-BIO-1", host="other.hana.ondemand.com", **_c("rival", "2026-11-02T10:00:05Z"))
    mine = reg.unbind_host(tenant_id="TNT-BIO-1", unbinds_event_id="e2", **_c("mine", "2026-11-02T10:00:00Z"))
    _race(monkeypatch, rival)
    assert store.append_event(mine, authorized_owner=True)["applied"] is True
