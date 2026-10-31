"""F2.1 — extracción read-only de conexiones → inventario normalizado, contra un cliente Datasphere SIMULADO.

Cubre los gates del owner: inventario completo+paginado, parcial por errores de API, conexiones sin DP Agent,
aislamiento multi-tenant, OAuth/secretos protegidos, identidades estables, y cero escrituras.
Sin red, sin tenants reales. Fixtures sintéticas (tests/fixtures/connectivity_governance).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.catalog.connectivity_governance import (
    Exhaustiveness,
    InventoryStatus,
    UNASSIGNED_ENVIRONMENT,
    from_snapshot,
    RetryPolicy,
    SimulatedConnectionSource,
    SourceError,
    extract_inventory,
    rate_limited,
    sanitize,
    timed_out,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "connectivity_governance"
GOV_MAP = {"DESARROLLO": "DEV", "CALIDAD": "QAS"}
TENANT = "tenant-sample"
HOST = "https://sim.example.invalid"


def _doc(space, name):
    return json.loads((FIXTURES / f"{space}__{name}.json").read_text(encoding="utf-8"))


def _spaces_data():
    return {
        "DESARROLLO": [_doc("DESARROLLO", "BW_DS"), _doc("DESARROLLO", "S4ABAP"), _doc("DESARROLLO", "S4PREM")],
        "CALIDAD": [_doc("CALIDAD", "BW_DS"), _doc("CALIDAD", "S4ABAP"), _doc("CALIDAD", "S4PREM"),
                    _doc("CALIDAD", "PostgreSQL"), _doc("CALIDAD", "SQL_SERVER_2019_onpremise")],
    }


def _extract(source, **kw):
    kw.setdefault("client", "ClienteDemo")
    kw.setdefault("tenant_id", TENANT)
    kw.setdefault("tenant_host", HOST)
    kw.setdefault("space_environment_map", GOV_MAP)
    kw.setdefault("expected_spaces", ["DESARROLLO", "CALIDAD"])
    return extract_inventory(source, **kw)


# ── Gate: inventario completo y paginado ──────────────────────────────────────
def test_complete_and_paginated():
    src = SimulatedConnectionSource(_spaces_data(), page_size=2)
    inv = _extract(src)
    assert inv.status == InventoryStatus.COMPLETE
    assert inv.coverage.discovered_connections == 8 == inv.coverage.extracted_connections
    assert set(inv.coverage.accessible_spaces) == {"DESARROLLO", "CALIDAD"}
    assert inv.coverage.errors == ()
    # hubo paginación real: más de una página listada para CALIDAD (5 conexiones / page_size 2)
    cal_pages = [c for c in src.calls if c[0] == "list" and c[1] == "CALIDAD"]
    assert len(cal_pages) >= 3
    # serializa e incluye cobertura + estado
    d = inv.to_dict()
    assert d["status"] == "COMPLETE" and d["version"] == "connectivity-inventory/v1"
    assert d["coverage"]["extracted_connections"] == 8


# ── Gate: inventario parcial por errores de API ───────────────────────────────
def test_partial_when_a_space_is_inaccessible():
    src = SimulatedConnectionSource(_spaces_data(), inaccessible={"DESARROLLO"})
    inv = _extract(src)
    assert inv.status == InventoryStatus.PARTIAL
    assert "DESARROLLO" in inv.coverage.inaccessible_spaces
    assert inv.coverage.extracted_connections == 5  # solo CALIDAD
    assert any(e.code == "SPACE_INACCESSIBLE" and e.scope == "space:DESARROLLO" for e in inv.coverage.errors)


def test_partial_when_one_get_fails():
    faults = {("get", "CALIDAD", "PostgreSQL"): [timed_out()] * 5}  # agota reintentos
    src = SimulatedConnectionSource(_spaces_data(), faults=faults)
    inv = _extract(src)
    assert inv.status == InventoryStatus.PARTIAL
    assert inv.coverage.discovered_connections == 8
    assert inv.coverage.extracted_connections == 7
    assert any(e.scope == "connection:CALIDAD/PostgreSQL" and e.code == "TIMEOUT" for e in inv.coverage.errors)


# ── Gate: límites de API (429 + Retry-After) ──────────────────────────────────
def test_rate_limit_is_retried_honoring_retry_after():
    slept = []
    retry = RetryPolicy(max_attempts=4, sleep=lambda s: slept.append(s))
    # la primera página de CALIDAD devuelve 429 una vez (Retry-After=2.0), luego responde
    faults = {("list", "CALIDAD", 0): [rate_limited(retry_after=2.0)]}
    src = SimulatedConnectionSource(_spaces_data(), page_size=2, faults=faults, retry=retry)
    inv = _extract(src)
    assert inv.status == InventoryStatus.COMPLETE          # el reintento recuperó la página
    assert slept == [2.0]                                   # respetó Retry-After, sin esperas reales
    assert inv.coverage.extracted_connections == 8


def test_failed_preflight_is_failed_status():
    src = SimulatedConnectionSource(_spaces_data(), preflight_error=SourceError("OAUTH_FAILED", "token 401"))
    inv = _extract(src)
    assert inv.status == InventoryStatus.FAILED
    assert inv.connections == ()
    assert any(e.scope == "preflight" and e.code == "OAUTH_FAILED" for e in inv.coverage.errors)


# ── Gate: conexiones sin DP Agent (agentName/agentStatus opcionales) ──────────
def test_connection_without_agent_is_extracted_without_error():
    data = _spaces_data()
    no_agent = copy.deepcopy(_doc("CALIDAD", "PostgreSQL"))
    no_agent["name"] = "DIRECT_NO_AGENT"
    no_agent["agentName"] = None
    no_agent["agentStatus"] = None
    no_agent["content"]["objects"]["DIRECT_NO_AGENT"] = no_agent["content"]["objects"].pop("PostgreSQL")
    data["CALIDAD"].append(no_agent)
    src = SimulatedConnectionSource(data)
    inv = _extract(src)
    assert inv.status == InventoryStatus.COMPLETE          # sin agente NO es un error
    target = next(c for c in inv.connections if c.identity.connection_name == "DIRECT_NO_AGENT")
    assert target.agent.agent_name is None and target.agent.status is None
    assert not any("DIRECT_NO_AGENT" in e.scope for e in inv.coverage.errors)


def test_agent_group_name_absent_is_tolerated():
    # agentGroupName nunca viene poblado en las fixtures (0/12 real) → debe quedar None sin romper.
    src = SimulatedConnectionSource(_spaces_data())
    inv = _extract(src)
    assert all(c.agent.agent_group is None for c in inv.connections)


# ── Gate: aislamiento multi-tenant (sin reutilizar sesiones entre clientes) ───
def test_multi_tenant_isolation():
    data_a = {"DESARROLLO": [_doc("DESARROLLO", "BW_DS")]}
    data_b = {"CALIDAD": [_doc("CALIDAD", "S4ABAP")]}
    src_a = SimulatedConnectionSource(data_a, tenant="tenant-A")
    src_b = SimulatedConnectionSource(data_b, tenant="tenant-B")
    inv_a = _extract(src_a, tenant_id="tenant-A", expected_spaces=["DESARROLLO"])
    inv_b = _extract(src_b, tenant_id="tenant-B", expected_spaces=["CALIDAD"])
    assert inv_a.tenant_id == "tenant-A" and inv_b.tenant_id == "tenant-B"
    assert inv_a.snapshot_id != inv_b.snapshot_id
    # estado independiente: ninguna llamada de A toca el tenant B (sesiones no compartidas)
    assert all(c[1] != "tenant-B" for c in src_a.calls)
    assert all(c[1] != "tenant-A" for c in src_b.calls)
    assert all(c.identity.tenant == "tenant-A" for c in inv_a.connections)


# ── Gate: OAuth y secretos protegidos ─────────────────────────────────────────
def test_sanitize_redacts_secrets_and_tokens():
    s = sanitize("datasphere login --client-secret SUPER_SECRET_123 --host x")
    assert "SUPER_SECRET_123" not in s and "[REDACTED]" in s
    assert "[REDACTED]" in sanitize("Authorization: Bearer abc.def.ghijklmnop")
    assert "[REDACTED]" in sanitize('{"access_token": "zzz9token"}')
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    assert jwt not in sanitize(f"token={jwt}")


def test_secret_never_leaks_into_inventory_errors():
    leaky = SourceError("OAUTH_FAILED", "login failed: --client-secret LEAK_ME_999")
    src = SimulatedConnectionSource(_spaces_data(), preflight_error=leaky)
    inv = _extract(src)
    blob = json.dumps(inv.to_dict())
    assert "LEAK_ME_999" not in blob and "[REDACTED]" in blob


# ── Gate: identidades técnicas estables ───────────────────────────────────────
def test_stable_identity_across_reextraction():
    ids1 = sorted(c.identity.key for c in _extract(SimulatedConnectionSource(_spaces_data())).connections)
    ids2 = sorted(c.identity.key for c in _extract(SimulatedConnectionSource(_spaces_data())).connections)
    assert ids1 == ids2 and len(ids1) == 8


# ── Gate: no escritura en Datasphere / SharePoint / Spaces ───────────────────
def test_extraction_is_read_only():
    src = SimulatedConnectionSource(_spaces_data())
    _extract(src)
    read_verbs = {"preflight", "list_spaces", "list", "get"}
    assert src.calls, "debió registrar llamadas de lectura"
    assert all(c[0] in read_verbs for c in src.calls)       # nunca create/update/delete/write


# ── exhaustividad SEPARADA del estado de ejecución ───────────────────────────
def test_complete_execution_is_not_exhaustive_when_source_cannot_prove_it():
    # Fuente que imita el CLI (array sin cursor/total): todas las llamadas OK → COMPLETE, pero la
    # exhaustividad NO es verificable → UNVERIFIABLE. Una ejecución correcta NO es un inventario exhaustivo.
    src = SimulatedConnectionSource(_spaces_data(), guarantees_exhaustive_list=False)
    inv = _extract(src)
    assert inv.status == InventoryStatus.COMPLETE
    assert inv.exhaustiveness == Exhaustiveness.UNVERIFIABLE
    assert inv.to_dict()["exhaustiveness"] == "UNVERIFIABLE"


def test_complete_execution_is_verified_when_source_guarantees_completeness():
    # El simulado conoce el conjunto completo → puede garantizar exhaustividad → VERIFIED.
    src = SimulatedConnectionSource(_spaces_data())  # guarantees_exhaustive_list=True por defecto
    inv = _extract(src)
    assert inv.status == InventoryStatus.COMPLETE
    assert inv.exhaustiveness == Exhaustiveness.VERIFIED


def test_partial_and_failed_executions_are_incomplete_exhaustiveness():
    partial = _extract(SimulatedConnectionSource(_spaces_data(), inaccessible={"DESARROLLO"}))
    assert partial.status == InventoryStatus.PARTIAL and partial.exhaustiveness == Exhaustiveness.INCOMPLETE
    failed = _extract(SimulatedConnectionSource(_spaces_data(),
                                                preflight_error=SourceError("OAUTH_FAILED", "401")))
    assert failed.status == InventoryStatus.FAILED and failed.exhaustiveness == Exhaustiveness.INCOMPLETE


# ── cobertura: una página incompleta NUNCA produce COMPLETE ──────────────────
def test_incomplete_page_cannot_yield_complete():
    # A mitad de la paginación de CALIDAD falla una página (error no-retriable) → el espacio no se puede
    # listar completo → inaccesible → PARTIAL. La página parcial NO se acepta silenciosamente como completa.
    faults = {("list", "CALIDAD", 1): [SourceError("SERVER_ERROR", "500 Internal Server Error")]}
    src = SimulatedConnectionSource(_spaces_data(), page_size=2, faults=faults)
    inv = _extract(src)
    assert inv.status == InventoryStatus.PARTIAL
    assert inv.status != InventoryStatus.COMPLETE
    assert "CALIDAD" in inv.coverage.inaccessible_spaces           # no se reporta como 0 conexiones ni completo
    assert all(c.identity.space != "CALIDAD" for c in inv.connections)


# ── environment = GOBIERNO (F2.2), no requisito técnico: sin clasificación se extrae igual, sin inventar ──────────
def test_unclassified_space_is_extracted_with_null_environment():
    src = SimulatedConnectionSource(_spaces_data())
    inv = _extract(src, space_environment_map={"CALIDAD": "QAS"})  # DESARROLLO sin clasificación gobernada
    assert inv.status == InventoryStatus.COMPLETE                   # la ausencia de environment no degrada la extracción
    assert not inv.coverage.errors and not inv.coverage.inaccessible_spaces
    dev = [c for c in inv.connections if c.identity.space == "DESARROLLO"]
    assert dev and all(c.identity.environment == UNASSIGNED_ENVIRONMENT for c in dev)   # marcador interno, no un environment
    d = inv.to_dict()
    envs = {(c["identity"]["space"], c["identity"]["environment"]) for c in d["connections"]}
    assert ("DESARROLLO", None) in envs and ("CALIDAD", "QAS") in envs                   # contrato público: null, nunca inferido
    assert all(c["identity"]["environment"] in (None, "QAS") for c in d["connections"])
    # identidad física intacta: no depende de la clasificación (mismo documento ⇒ misma identidad física y de agente)
    docs = {c["name"]: c for c in _spaces_data()["DESARROLLO"]}
    for c in dev:
        ref = from_snapshot(docs[c.identity.connection_name], tenant=TENANT, environment="cualquiera")
        assert c.physical == ref.physical and c.agent == ref.agent


def test_no_map_at_all_extracts_everything_without_errors():
    inv = _extract(SimulatedConnectionSource(_spaces_data()), space_environment_map=None)
    assert inv.status == InventoryStatus.COMPLETE and not inv.coverage.errors
    assert all(c["identity"]["environment"] is None for c in inv.to_dict()["connections"])
