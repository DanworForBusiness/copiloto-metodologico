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
    InventoryStatus,
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


# ── environment gobernado: espacio sin mapeo → hueco, no se inventa ──────────
def test_ungoverned_space_is_a_gap_not_invented():
    src = SimulatedConnectionSource(_spaces_data())
    inv = _extract(src, space_environment_map={"CALIDAD": "QAS"})  # falta DESARROLLO
    assert inv.status == InventoryStatus.PARTIAL
    assert any(e.code == "ENVIRONMENT_UNGOVERNED" and e.scope == "space:DESARROLLO" for e in inv.coverage.errors)
    assert all(c.identity.space != "DESARROLLO" for c in inv.connections)
