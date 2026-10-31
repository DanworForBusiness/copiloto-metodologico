"""F2.1c — guards de la sonda DEV/QA, OFFLINE (sin conectar a ningún tenant).

Prueba los controles de seguridad de ejecución (paquete `connectivity_governance.safety`) que usa la sonda
`_handoffs/connectivity_devqa_probe.py`: allowlist exacta, confirmación DEV/QA, config obligatoria, límites
globales (solicitudes/duración/reintentos) y limpieza efímera. Todo con el cliente SIMULADO; cero red.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.catalog.connectivity_governance import (
    Budget,
    BudgetedSource,
    BudgetError,
    Exhaustiveness,
    InventoryStatus,
    RetryPolicy,
    SafetyError,
    SimulatedConnectionSource,
    assert_allowlisted,
    assert_devqa_confirmed,
    assert_required,
    ephemeral_workdir,
    extract_inventory,
    rate_limited,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "connectivity_governance"
GOV_MAP = {"DESARROLLO": "DEV", "CALIDAD": "QAS"}


def _doc(space, name):
    return json.loads((FIXTURES / f"{space}__{name}.json").read_text(encoding="utf-8"))


def _data():
    return {"DESARROLLO": [_doc("DESARROLLO", "BW_DS"), _doc("DESARROLLO", "S4ABAP"), _doc("DESARROLLO", "S4PREM")],
            "CALIDAD": [_doc("CALIDAD", "BW_DS"), _doc("CALIDAD", "S4ABAP"), _doc("CALIDAD", "S4PREM"),
                        _doc("CALIDAD", "PostgreSQL"), _doc("CALIDAD", "SQL_SERVER_2019_onpremise")]}


def _extract(source, budget=None):
    retry = RetryPolicy(sleep=budget.retry_sleep(lambda s: None)) if budget else RetryPolicy(sleep=lambda s: None)
    return extract_inventory(source, client="probe", tenant_id="t", tenant_host="https://h.devqa.invalid",
                             space_environment_map=GOV_MAP, expected_spaces=["DESARROLLO", "CALIDAD"], retry=retry)


# ── confirmación DEV/QA + config obligatoria ──────────────────────────────────
def test_devqa_confirmation_required():
    with pytest.raises(SafetyError) as ei:
        assert_devqa_confirmed(None)
    assert ei.value.code == "DEVQA_NOT_CONFIRMED"
    with pytest.raises(SafetyError):
        assert_devqa_confirmed("PRD")
    assert_devqa_confirmed("DEVQA")        # no lanza


def test_required_config_incomplete():
    env = {"DSP_HOST": "h", "DSP_TOKEN_URL": ""}   # token vacío = falta
    with pytest.raises(SafetyError) as ei:
        assert_required(env, ["DSP_HOST", "DSP_TOKEN_URL", "DSP_CLIENT_ID"])
    assert ei.value.code == "CONFIG_INCOMPLETE"
    assert "DSP_TOKEN_URL" in ei.value.message and "DSP_CLIENT_ID" in ei.value.message


# ── allowlist exacta (sin substrings ni inferencias) ─────────────────────────
def test_allowlist_exact_match_only():
    allow_h = ["https://dev.example.invalid"]
    allow_t = ["https://dev.example.invalid/oauth/token"]
    # exacto OK
    assert_allowlisted("https://dev.example.invalid", "https://dev.example.invalid/oauth/token",
                       allowed_hosts=allow_h, allowed_token_urls=allow_t)
    # host no autorizado
    with pytest.raises(SafetyError) as ei:
        assert_allowlisted("https://prd.example.invalid", "https://dev.example.invalid/oauth/token",
                           allowed_hosts=allow_h, allowed_token_urls=allow_t)
    assert ei.value.code == "HOST_NOT_ALLOWLISTED"
    # token endpoint no autorizado
    with pytest.raises(SafetyError) as ei:
        assert_allowlisted("https://dev.example.invalid", "https://evil.example.invalid/oauth/token",
                           allowed_hosts=allow_h, allowed_token_urls=allow_t)
    assert ei.value.code == "TOKEN_URL_NOT_ALLOWLISTED"
    # NO acepta coincidencia parcial (substring)
    with pytest.raises(SafetyError):
        assert_allowlisted("https://dev.example.invalid.attacker.com", "https://dev.example.invalid/oauth/token",
                           allowed_hosts=allow_h, allowed_token_urls=allow_t)
    # allowlist vacía ⇒ rechaza
    with pytest.raises(SafetyError) as ei:
        assert_allowlisted("x", "y", allowed_hosts=[], allowed_token_urls=[])
    assert ei.value.code == "ALLOWLIST_EMPTY"


# ── límites globales: solicitudes ─────────────────────────────────────────────
def test_request_budget_exhausted_aborts_not_degrades():
    budget = Budget(max_requests=2, max_duration_s=1e9, max_total_retries=100)
    budget.start()
    src = BudgetedSource(SimulatedConnectionSource(_data()), budget, skip_preflight=True)
    with pytest.raises(BudgetError) as ei:          # propaga (no es SourceError) → aborta, no degrada a PARTIAL
        _extract(src, budget)
    assert ei.value.code == "REQUESTS_EXCEEDED"


def test_within_budget_completes():
    budget = Budget(max_requests=1000, max_duration_s=1e9, max_total_retries=100)
    budget.start()
    src = BudgetedSource(SimulatedConnectionSource(_data()), budget, skip_preflight=True)
    inv = _extract(src, budget)
    assert inv.status == InventoryStatus.COMPLETE
    assert inv.exhaustiveness == Exhaustiveness.VERIFIED       # el simulado garantiza completitud


# ── límites globales: duración ────────────────────────────────────────────────
def test_duration_budget_exhausted():
    ticks = iter([0.0, 0.0, 1000.0, 2000.0, 3000.0, 4000.0])   # el reloj salta más allá del máximo
    budget = Budget(max_requests=1000, max_duration_s=10.0, max_total_retries=100,
                    clock=lambda: next(ticks))
    budget.start()
    src = BudgetedSource(SimulatedConnectionSource(_data()), budget, skip_preflight=True)
    with pytest.raises(BudgetError) as ei:
        _extract(src, budget)
    assert ei.value.code == "DURATION_EXCEEDED"


# ── límites globales: reintentos totales ──────────────────────────────────────
def test_total_retries_budget_exhausted():
    # Un get que siempre devuelve 429 (retriable): cada reintento cobra al presupuesto global de reintentos.
    faults = {("get", "DESARROLLO", "BW_DS"): [rate_limited(0.0)] * 50}
    budget = Budget(max_requests=1000, max_duration_s=1e9, max_total_retries=2)
    budget.start()
    src = BudgetedSource(SimulatedConnectionSource(_data(), faults=faults), budget, skip_preflight=True)
    with pytest.raises(BudgetError) as ei:
        _extract(src, budget)
    assert ei.value.code == "RETRIES_EXCEEDED"


# ── limpieza efímera garantizada ante error ──────────────────────────────────
def test_ephemeral_workdir_cleans_up_on_error():
    captured = {}
    with pytest.raises(RuntimeError):
        with ephemeral_workdir("probe-test-") as d:
            captured["path"] = d
            assert d.exists()
            raise RuntimeError("boom durante la extracción")
    assert not captured["path"].exists()       # se borró incluso ante error
