"""
test_commercial_forecast_classification.py — Commercial Pipeline Forecast:
clasificación de madurez ACTIVA/PROSPECTO de `_build_forecast` — MODELO A /
"Pipeline Maturity Forecast" (owner GO final, ver HANDOFF — semantic audit).

PROSPECTO/ACTIVA es PURA ETAPA — etapa "0-..." -> PROSPECTO, etapas 1-8 ->
ACTIVA — el MISMO criterio que ya gobiernan los tiles "Prospectos"/
"Oportunidades Activas" del Dashboard (`_stage_partition`). `res.partner`/
"Es Cliente?" NO participa: un modelo anterior que mezclaba etapa y relación
de cuenta fue revertido (rompía la reconciliación con el Dashboard en cuanto
existiera una cuenta no-cliente con pipeline fuera de la etapa 0 — semantic
audit, live QA). Estos tests prueban explícitamente que la relación de
cuenta es IRRELEVANTE para esta clasificación.

Vía `FakeOdooClient` (`_commercial_fake_odoo.py`) — CI nunca depende de Odoo
real.
"""
from __future__ import annotations

import pytest

from tests.webapp._commercial_fake_odoo import FakeOdooClient
from webapp.services import commercial_service as svc_module
from webapp.services.commercial_service import CommercialService

STAGES = [
    {"id": 1, "name": "0-Prospecto"},
    {"id": 2, "name": "1-Inicial"},
    {"id": 9, "name": "8-Propuesta formal Presentada"},
]
TAGS = [{"id": 10, "name": "New Names"}, {"id": 11, "name": "Base Instalada"}]

_CLIENTE_SELECTION = [["k_si", "Si"], ["k_no", "No"], ["k_ex", "Ex"]]


def _cliente_prop(value_key: str | None) -> list:
    if value_key is None:
        return []
    return [{"name": "es_cliente_tech", "string": "Es Cliente?", "value": value_key,
              "selection": _CLIENTE_SELECTION}]


def _hp(hours) -> list:
    return [{"string": "Horas", "value": str(hours)}]


# Cuentas deliberadamente "ruidosas" respecto de la etapa — ver D/E/F abajo:
# la clasificación de madurez debe ser IDÉNTICA sin importar qué diga la
# property de cuenta.
PARTNERS = [
    {"id": 1, "name": "Cuenta Sí", "properties": _cliente_prop("k_si")},
    {"id": 2, "name": "Cuenta No", "properties": _cliente_prop("k_no")},
    {"id": 3, "name": "Cuenta Blanco", "properties": _cliente_prop(None)},
]

LEADS = [
    # A: etapa 0 -> PROSPECTO (cuenta sin dato — caso "normal").
    {"id": 100, "type": "opportunity", "active": True, "stage_id": 1, "tag_ids": [10],
     "partner_id": [3, "Cuenta Blanco"], "lead_properties": _hp(10), "probability": 10, "expected_revenue": 0},
    # B: etapa 1 -> ACTIVA.
    {"id": 101, "type": "opportunity", "active": True, "stage_id": 2, "tag_ids": [10],
     "partner_id": [3, "Cuenta Blanco"], "lead_properties": _hp(20), "probability": 10, "expected_revenue": 0},
    # C: etapa 8 -> ACTIVA (extremo superior del universo 1-8).
    {"id": 102, "type": "opportunity", "active": True, "stage_id": 9, "tag_ids": [10],
     "partner_id": [3, "Cuenta Blanco"], "lead_properties": _hp(30), "probability": 10, "expected_revenue": 0},
    # D: etapa 0 + cuenta "Es Cliente?"=Sí -> sigue PROSPECTO (la cuenta no
    # participa en absoluto).
    {"id": 103, "type": "opportunity", "active": True, "stage_id": 1, "tag_ids": [10],
     "partner_id": [1, "Cuenta Sí"], "lead_properties": _hp(40), "probability": 10, "expected_revenue": 0},
    # E: etapa 4 (activa) + cuenta "Es Cliente?"=No -> sigue ACTIVA (la
    # property "No" NUNCA vuelve una oportunidad Prospecto fuera de la
    # etapa 0 bajo el Modelo A).
    {"id": 104, "type": "opportunity", "active": True, "stage_id": 2, "tag_ids": [10],
     "partner_id": [2, "Cuenta No"], "lead_properties": _hp(50), "probability": 10, "expected_revenue": 0},
    # F: etapa 0 + cuenta SIN la property siquiera -> sigue PROSPECTO.
    {"id": 105, "type": "opportunity", "active": True, "stage_id": 1, "tag_ids": [10],
     "partner_id": False, "lead_properties": _hp(60), "probability": 10, "expected_revenue": 0},
    # G: universo Base Instalada (tag 11, SIN tag New Names) — activa y
    # prospecto propios, para probar la reconciliación por tipo (owner GO
    # §5-E) con un tercer conjunto no trivial, no sólo "todos"/"nn".
    {"id": 106, "type": "opportunity", "active": True, "stage_id": 1, "tag_ids": [11],
     "partner_id": [3, "Cuenta Blanco"], "lead_properties": _hp(90), "probability": 10, "expected_revenue": 0},
    {"id": 107, "type": "opportunity", "active": True, "stage_id": 2, "tag_ids": [11],
     "partner_id": [3, "Cuenta Blanco"], "lead_properties": _hp(100), "probability": 10, "expected_revenue": 0},
]


def _build_client(**overrides) -> FakeOdooClient:
    return FakeOdooClient(**{"stages": STAGES, "tags": TAGS, "leads": LEADS, "partners": PARTNERS, **overrides})


@pytest.fixture(autouse=True)
def _clear_commercial_cache():
    svc_module.clear_cache()
    yield
    svc_module.clear_cache()


def _classification_by_hours(forecast: dict) -> dict[int, str]:
    by_hours: dict[int, str] = {}
    for month in forecast["months"]:
        for opp in month["opportunities"]:
            by_hours[opp["hours"]] = opp["classification"]
    return by_hours


# ── A/B/C: mapeo puro por etapa ─────────────────────────────────────────────

def test_stage_zero_classifies_as_prospecto():
    forecast = CommercialService(client=_build_client()).get_forecast("todos")
    assert _classification_by_hours(forecast)[10] == "PROSPECTO"


def test_stage_one_classifies_as_activa():
    forecast = CommercialService(client=_build_client()).get_forecast("todos")
    assert _classification_by_hours(forecast)[20] == "ACTIVA"


def test_stage_eight_classifies_as_activa():
    forecast = CommercialService(client=_build_client()).get_forecast("todos")
    assert _classification_by_hours(forecast)[30] == "ACTIVA"


# ── D/E/F: la relación de cuenta es IRRELEVANTE ─────────────────────────────

def test_stage_zero_with_cliente_account_still_prospecto():
    forecast = CommercialService(client=_build_client()).get_forecast("todos")
    assert _classification_by_hours(forecast)[40] == "PROSPECTO"


def test_active_stage_with_explicit_no_account_still_activa():
    forecast = CommercialService(client=_build_client()).get_forecast("todos")
    assert _classification_by_hours(forecast)[50] == "ACTIVA"


def test_stage_zero_with_missing_partner_still_prospecto():
    forecast = CommercialService(client=_build_client()).get_forecast("todos")
    assert _classification_by_hours(forecast)[60] == "PROSPECTO"


# ── Sólo dos clasificaciones existen, nunca una tercera ─────────────────────

def test_only_two_classifications_exist():
    forecast = CommercialService(client=_build_client()).get_forecast("todos")
    all_opps = [o for m in forecast["months"] for o in m["opportunities"]]
    assert len(all_opps) == 8
    assert all("classification" in o for o in all_opps)
    assert {o["classification"] for o in all_opps} == {"PROSPECTO", "ACTIVA"}


# ── Performance: cero consulta a res.partner desde Forecast ────────────────

def test_forecast_never_queries_res_partner():
    client = _build_client()
    CommercialService(client=client).get_forecast("todos")
    assert not [c for c in client.calls if c[0] == "res.partner"], (
        "Modelo A no usa la relación de cuenta — Forecast no debe consultar res.partner en absoluto"
    )


# ── Reconciliación: Activa + Prospecto == Pipeline Total, por definición ───

def test_active_plus_prospect_equals_pipeline_total():
    forecast = CommercialService(client=_build_client()).get_forecast("todos")
    all_opps = [o for m in forecast["months"] for o in m["opportunities"]]
    active_hours = sum(o["hours"] for o in all_opps if o["classification"] == "ACTIVA")
    prospect_hours = sum(o["hours"] for o in all_opps if o["classification"] == "PROSPECTO")
    assert active_hours + prospect_hours == sum(o["hours"] for o in all_opps)
    assert active_hours == 20 + 30 + 50 + 100  # B + C + E + G(activa)
    assert prospect_hours == 10 + 40 + 60 + 90  # A + D + F + G(prospecto)


# ── Owner GO §5-E: la reconciliación vale para CADA tipo, no sólo "todos" ──
# (New Names = leads 100-105, Base Instalada = leads 106-107 — conjuntos
# separados en el fixture a propósito, ver arriba).

def test_reconciliation_holds_for_new_name_tipo():
    forecast = CommercialService(client=_build_client()).get_forecast("nn")
    all_opps = [o for m in forecast["months"] for o in m["opportunities"]]
    active_hours = sum(o["hours"] for o in all_opps if o["classification"] == "ACTIVA")
    prospect_hours = sum(o["hours"] for o in all_opps if o["classification"] == "PROSPECTO")
    assert active_hours == 20 + 30 + 50
    assert prospect_hours == 10 + 40 + 60
    assert active_hours + prospect_hours == sum(o["hours"] for o in all_opps)


def test_reconciliation_holds_for_base_instalada_tipo():
    forecast = CommercialService(client=_build_client()).get_forecast("bi")
    all_opps = [o for m in forecast["months"] for o in m["opportunities"]]
    active_hours = sum(o["hours"] for o in all_opps if o["classification"] == "ACTIVA")
    prospect_hours = sum(o["hours"] for o in all_opps if o["classification"] == "PROSPECTO")
    assert active_hours == 100
    assert prospect_hours == 90
    assert active_hours + prospect_hours == sum(o["hours"] for o in all_opps)
