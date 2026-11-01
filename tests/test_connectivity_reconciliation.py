"""F2.2a — `connectivity-observation/v1` + motor puro `connectivity-reconciliation/v1`, con fixtures SINTÉTICAS saneadas (sin SAP, sin datos de
clientes). Cubre identidad (tenant + Space + nombre; typeId atributo), tres estados de campo, fingerprints estables, técnico vs. operativo,
procedencia del Catálogo, alcance no gobernado, cobertura de F2.1 sin falsas altas/bajas y ausencia de credenciales."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from tools.catalog import generate_space_catalog as gsc
from tools.catalog.connectivity_governance import observation as ob
from tools.catalog.connectivity_governance import reconciliation as rc

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures" / "connectivity_reconciliation"
CONN = FIX / "Sintetico" / "Conexiones"
SCOPE = rc.GovernedScope("tnt-mrk", {"DEV": "DEV"})
TRAPS = ("mrk_user", "mrk_password_value", "mrk_token_value", "MRK_SNC_NAME", "MRK-INTERNAL-ID", "MRK-DIS-ID", "mrk_creator", "MRK-SPACE-ID")


def _raw():
    listing = json.loads((CONN / "conn_space_DEV.json").read_text(encoding="utf-8"))
    details = {("DEV", d["name"]): d for d in (json.loads(p.read_text(encoding="utf-8")) for p in sorted(CONN.glob("conn_space_DEV_*.json")))}
    return listing, details


def _obs(listing=None, details=None, *, status="COMPLETE", exhaustiveness="UNVERIFIABLE", listings=None, tenant="tnt-mrk",
         envs=None, snapshot="s1", at="2026-11-01T10:00:00Z"):
    base_listing, base_details = _raw()
    return ob.from_cli_documents(
        client="Sintetico", tenant_id=tenant, tenant_governed=True, snapshot_id=snapshot, captured_at=at, status=status,
        exhaustiveness=exhaustiveness, listings=listings if listings is not None else {"DEV": listing if listing is not None else base_listing},
        details=details if details is not None else base_details, space_environments={"DEV": "DEV"} if envs is None else envs)


def _item(result, name):
    return next(i for i in result["items"] if i["key"]["connection_name"] == name)


def _conn(doc, name):
    return next(c for c in doc["connections"] if c["key"]["connection_name"] == name)


# ═══════════════ contrato de observación ═══════════════
def test_identity_is_tenant_space_name_and_type_is_an_attribute():
    doc = _obs()
    c = _conn(doc, "ERP_ABAP")
    assert c["key"] == {"tenant_id": "tnt-mrk", "space": "DEV", "connection_name": "ERP_ABAP"}
    assert c["fields"]["type_id"] == {"state": "DECLARED", "value": "ABAP"} and c["environment"] == "DEV"
    assert doc["schema"] == "connectivity-observation/v1" and doc["identity_schema"] == "connectivity-identity/v1"
    assert ob.validate(doc) == []


def test_no_credentials_or_internal_identifiers_reach_the_observation():
    text = json.dumps(_obs(), ensure_ascii=False)
    for marker in TRAPS:
        assert marker not in text, marker
    j, o = _conn(_obs(), "PG_JDBC"), _conn(_obs(), "SVC_ODATA")
    assert j["fields"]["url_endpoint"]["value"] == "pg.invalid:5432/sales"
    assert o["fields"]["url_endpoint"]["value"] == "odata.invalid/sap/opu/odata4/svc" and o["fields"]["auth_type"]["value"] == "Basic"


@pytest.mark.parametrize("raw,expected", [
    ("jdbc:postgresql://pg.invalid:5432/db?user=a&password=b", "pg.invalid:5432/db"),
    ("jdbc:sqlserver://sql.invalid:1433;databaseName=x;password=y", "sql.invalid:1433"),
    ("https://u:p@svc.invalid/path/?token=t", "svc.invalid/path"),
    ("", None), (None, None),
])
def test_url_endpoint_keeps_only_host_port_and_path(raw, expected):
    assert ob.url_endpoint(raw) == expected


def test_three_field_states_and_fingerprints():
    listing, details = _raw()
    details = dict(details)
    details.pop(("DEV", "PG_JDBC"))                                         # listada pero sin detalle legible
    doc = _obs(listing, details)
    pg = _conn(doc, "PG_JDBC")
    assert pg["detail_state"] == "NOT_READ" and pg["fields"]["type_id"]["state"] == "DECLARED"
    assert pg["fields"]["host"]["state"] == "UNKNOWN" and pg["technical_fingerprint"] is None      # UNKNOWN ⇒ no comparable
    erp = _conn(doc, "ERP_ABAP")
    assert erp["fields"]["port"] == {"state": "ABSENT"} and erp["fields"]["sysid"]["state"] == "DECLARED"
    assert erp["technical_fingerprint"] and len(erp["technical_fingerprint"]) == 64


def test_fingerprints_are_stable_and_ignore_operational_state():
    a = _conn(_obs(), "ERP_ABAP")
    listing, details = _raw()
    d = copy.deepcopy(details)
    d[("DEV", "ERP_ABAP")]["agentStatus"] = "DISCONNECTED"
    d[("DEV", "ERP_ABAP")]["content"]["objects"]["ERP_ABAP"]["location"]["connected"] = False
    d[("DEV", "ERP_ABAP")]["realtimeReplicationStatus"] = "PAUSED"
    b = _conn(_obs(listing, d), "ERP_ABAP")
    assert a["technical_fingerprint"] == b["technical_fingerprint"] == _conn(_obs(), "ERP_ABAP")["technical_fingerprint"]


@pytest.mark.parametrize("mutate,match", [
    (lambda kw: kw.update(source_kind="otra"), "source_kind"),
    (lambda kw: kw.update(status="DONE"), "estado"),
    (lambda kw: kw["listings"]["DEV"].append({"name": "ERP_ABAP"}) or kw["listings"].update(OTRO=[{"name": "X"}]) or None, None),
])
def test_contract_rejects_out_of_contract_inputs(mutate, match):
    listing, details = _raw()
    kw = dict(client="S", tenant_id="t", snapshot_id="s", captured_at="x", status="COMPLETE", exhaustiveness="UNVERIFIABLE",
              listings={"DEV": listing}, details=details)
    mutate(kw)
    if match:
        with pytest.raises(ob.ObservationError, match=match):
            ob.from_cli_documents(**kw)
    else:                                                                    # nombres repetidos en un listado se toman una sola vez
        doc = ob.from_cli_documents(**kw)
        assert [c["key"]["connection_name"] for c in doc["connections"] if c["key"]["space"] == "DEV"].count("ERP_ABAP") == 1


def test_validate_flags_credentials_and_unknown_fields():
    doc = _obs()
    bad = copy.deepcopy(doc)
    bad["connections"][0]["fields"]["password"] = {"state": "DECLARED", "value": "x"}
    assert ob.validate(bad)
    bad = copy.deepcopy(doc)
    bad["connections"][0]["fields"]["host"]["value"] = "user=mrk"
    assert ob.validate(bad)


# ═══════════════ procedencia del Catálogo publicado (ida y vuelta por los MISMOS parsers) ═══════════════
def _catalog_obs(tmp_path):
    gsc.generate_all(FIX, tmp_path)
    sys.path.insert(0, str(REPO / "tools" / "catalog"))
    import catalog_service as cs  # noqa: PLC0415 — mismo cargador que usa el Hub
    return ob.from_published_catalog(cs.load_architecture_connections(tmp_path), client="Sintetico")


def test_catalog_round_trip_through_the_same_parsers_matches_without_changes(tmp_path):
    cat = _catalog_obs(tmp_path)
    assert cat["provenance"]["source_kind"] == "published_catalog" and cat["provenance"]["captured_at"] is None
    assert cat["coverage"]["status"] == "UNKNOWN" and _conn(cat, "ERP_ABAP")["fields"]["agent_connected"] == {"state": "UNKNOWN"}
    r = rc.reconcile(_obs(), cat, SCOPE)
    assert r["summary"]["MATCHED"] == 3 and r["summary"]["technical_changes"] == 0
    assert all(i["technical"] == "UNCHANGED" and i["parameters"] == "UNCHANGED" for i in r["items"])
    assert "baseline:PUBLISHED_CATALOG_WITHOUT_EVIDENCE_DATE_OR_COVERAGE" in r["limits"]


def test_a_connection_missing_from_the_catalog_is_not_claimed_as_new(tmp_path):
    cat = _catalog_obs(tmp_path)
    cat["connections"] = [c for c in cat["connections"] if c["key"]["connection_name"] != "SVC_ODATA"]
    it = _item(rc.reconcile(_obs(), cat, SCOPE), "SVC_ODATA")
    assert it["outcome"] == "ONLY_IN_CURRENT" and it["baseline_absence"]["strength"] == "UNKNOWN"


# ═══════════════ alcance gobernado ═══════════════
def test_without_governed_scope_nothing_is_concluded():
    cur, base = _obs(), _obs(snapshot="s0")
    assert {i["outcome"] for i in rc.reconcile(cur, base, None)["items"]} == {"SCOPE_UNRESOLVED"}
    assert {i["outcome"] for i in rc.reconcile(cur, base, rc.GovernedScope("tnt-mrk", {}))["items"]} == {"SCOPE_UNRESOLVED"}   # sin environment
    assert {i["outcome"] for i in rc.reconcile(cur, base, rc.GovernedScope("otro", {"DEV": "DEV"}))["items"]} == {"SCOPE_UNRESOLVED"}


def test_spaces_not_requested_now_are_out_of_scope():
    listing, details = _raw()
    base = _obs(listings={"DEV": listing, "QAS": [{"name": "Q1", "typeId": "ABAP"}]}, envs={"DEV": "DEV", "QAS": "QAS"}, snapshot="s0")
    r = rc.reconcile(_obs(), base, rc.GovernedScope("tnt-mrk", {"DEV": "DEV", "QAS": "QAS"}))
    assert _item(r, "Q1")["outcome"] == "OUT_OF_SCOPE"


# ═══════════════ técnico vs. operativo ═══════════════
def _changed(**edits):
    listing, details = _raw()
    d = copy.deepcopy(details)
    for path, value in edits.items():
        node = d[("DEV", "ERP_ABAP")]
        *parents, leaf = path.split(".")
        for p in parents:
            node = node[p]
        node[leaf] = value
    return _obs(listing, d, snapshot="s2", at="2026-11-02T10:00:00Z")


def test_operational_changes_are_notes_never_architecture_changes():
    cur = _changed(agentStatus="DISCONNECTED", realtimeReplicationStatus="PAUSED")
    it = _item(rc.reconcile(cur, _obs(), SCOPE), "ERP_ABAP")
    assert it["outcome"] == "MATCHED" and it["technical"] == "UNCHANGED"
    assert {n["field"] for n in it["operational_notes"]} == {"agent_status", "replication_status"}


@pytest.mark.parametrize("edits,group,technical", [
    ({"agentName": "MRK_AGENT_B"}, "connectivity", "CHANGED"),
    ({"typeId": "SAPS4HANAOP"}, "type", "CHANGED"),
    ({"content.objects.ERP_ABAP.configuration.ConnectionProperties.ConnectionInfo.host": "erp-b.invalid"}, "endpoint", "CHANGED"),
    ({"content.objects.ERP_ABAP.configuration.ConnectionFeatures.features.replicationflows": False}, "capabilities", "CHANGED"),
    ({"content.objects.ERP_ABAP.configuration.ConnectionProperties.ConnectionInfo.poolsize": "20"}, "parameters", "UNCHANGED"),
    ({"businessName": "Otro nombre"}, "descriptive", "UNCHANGED"),
])
def test_group_changes_are_classified_without_inventing_adds_or_removals(edits, group, technical):
    r = rc.reconcile(_changed(**edits), _obs(), SCOPE)
    it = _item(r, "ERP_ABAP")
    assert it["outcome"] == "MATCHED" and it["technical"] == technical          # mismo key: un cambio de tipo NO es alta + baja
    assert it["groups"][group]["result"] == "CHANGED"
    assert r["summary"]["ONLY_IN_CURRENT"] == r["summary"]["ONLY_IN_BASELINE"] == 0


def test_declared_to_absent_is_a_change_but_unknown_is_not_comparable():
    listing, details = _raw()
    d = copy.deepcopy(details)
    d[("DEV", "ERP_ABAP")]["content"]["objects"]["ERP_ABAP"]["configuration"]["ConnectionProperties"]["ConnectionInfo"]["sysid"] = ""
    it = _item(rc.reconcile(_obs(listing, d), _obs(), SCOPE), "ERP_ABAP")
    assert it["groups"]["endpoint"]["changed_fields"][0]["current"] == {"state": "ABSENT"}
    d2 = dict(details)
    d2.pop(("DEV", "ERP_ABAP"))
    it = _item(rc.reconcile(_obs(listing, d2), _obs(), SCOPE), "ERP_ABAP")
    assert it["technical"] == "NOT_COMPARABLE" and it["detail_state"]["current"] == "NOT_READ"


# ═══════════════ cobertura de F2.1: sin falsas altas ni bajas ═══════════════
def test_absence_is_evidence_not_removal():
    listing, details = _raw()
    cur = _obs([x for x in listing if x["name"] != "PG_JDBC"], details)
    it = _item(rc.reconcile(cur, _obs(snapshot="s0"), SCOPE), "PG_JDBC")
    assert it["outcome"] == "ONLY_IN_BASELINE" and it["current_absence"]["strength"] == "UNVERIFIABLE"
    assert "decision" not in it and "removal" not in json.dumps(it).lower()


@pytest.mark.parametrize("kw,side", [
    ({"listings": {"DEV": None}}, "current"),
    ({"status": "FAILED", "exhaustiveness": "INCOMPLETE", "listings": {"DEV": []}}, "current"),   # F2.1: FAILED no trae conexiones
])
def test_inaccessible_space_or_failed_run_gives_no_evidence(kw, side):
    it = _item(rc.reconcile(_obs(**kw), _obs(snapshot="s0"), SCOPE), "ERP_ABAP")
    assert it["outcome"] == "NO_EVIDENCE" and it["missing_side"] == side


def test_baseline_that_did_not_cover_the_space_is_no_evidence_not_an_add():
    listing, _ = _raw()
    base = _obs(listings={"DEV": None}, snapshot="s0")
    it = _item(rc.reconcile(_obs(), base, SCOPE), "ERP_ABAP")
    assert it["outcome"] == "NO_EVIDENCE" and it["missing_side"] == "baseline"


def test_partial_run_absence_is_only_incomplete_evidence():
    listing, details = _raw()
    cur = _obs([x for x in listing if x["name"] != "PG_JDBC"], details, status="PARTIAL", exhaustiveness="INCOMPLETE")
    assert _item(rc.reconcile(cur, _obs(snapshot="s0"), SCOPE), "PG_JDBC")["current_absence"]["strength"] == "INCOMPLETE"


def test_repeated_absence_raises_review_priority_but_is_never_deletion_evidence():
    listing, details = _raw()
    without = [x for x in listing if x["name"] != "PG_JDBC"]
    obs = [_obs(snapshot="s0", at="2026-10-01T00:00:00Z"),
           _obs(without, details, snapshot="s1", at="2026-10-15T00:00:00Z"),
           _obs(without, details, snapshot="s2", at="2026-11-01T00:00:00Z")]
    h = rc.absence_history(("DEV", "PG_JDBC"), obs)
    assert h == {"key": {"space": "DEV", "connection_name": "PG_JDBC"}, "consecutive_absences": 2, "review_priority": "HIGH",
                 "is_deletion_evidence": False}
    obs.append(_obs(without, details, status="PARTIAL", exhaustiveness="INCOMPLETE", snapshot="s3", at="2026-11-02T00:00:00Z"))
    assert rc.absence_history(("DEV", "PG_JDBC"), obs)["consecutive_absences"] == 0          # sin evidencia comparable la racha se corta


# ═══════════════ determinismo y entradas ═══════════════
def test_reconciliation_is_deterministic_and_does_not_mutate_inputs():
    cur, base = _changed(agentName="MRK_AGENT_B"), _obs()
    before = (copy.deepcopy(cur), copy.deepcopy(base))
    assert rc.reconcile(cur, base, SCOPE) == rc.reconcile(cur, base, SCOPE)
    assert (cur, base) == before
    assert set(rc.reconcile(cur, base, SCOPE)["summary"]) >= set(rc.OUTCOMES)


def test_reconcile_rejects_non_observations():
    with pytest.raises(rc.ReconciliationError):
        rc.reconcile({"schema": "otra"}, _obs(), SCOPE)
