"""
test_commercial_service.py — Commercial Hub Odoo Batch 1: contrato de
`webapp.services.commercial_service` (semántica Summary/Pipeline, cache) vía
`FakeOdooClient` (`_commercial_fake_odoo.py`) — CI NUNCA depende de Odoo real.

Fixture única (`_build_client`) que cubre TODAS las reglas legacy-compatible
de una sola vez — valores elegidos a mano para que cada agregación (horas/
revenue/weighted/conversion) sea verificable por cálculo directo, sin
depender de que el test "adivine" lo que el servicio ya hizo.

Etapas: 1="0 Prospección" (prospecto), 2="1 Contacto"/3="5 Negociación"
(activas 1-8), 4="8 Ganado" (is_won), 5="Suspendido - Pausa" (suspendida).
Tags: 10="New Names" (NN), 11="Base Instalada" (BI).
"""
from __future__ import annotations

from datetime import date

import pytest

from tests.webapp._commercial_fake_odoo import FakeOdooClient
from webapp.services import commercial_service as svc_module
from webapp.services.commercial_service import (
    CommercialConfigurationError,
    CommercialDataError,
    CommercialService,
    get_property_num,
)
from webapp.services.odoo_client import OdooTransportError

THIS_YEAR = date.today().year
LAST_YEAR = THIS_YEAR - 1

STAGES = [
    {"id": 1, "name": "0 Prospección"},
    {"id": 2, "name": "1 Contacto"},
    {"id": 3, "name": "5 Negociación"},
    # Sin prefijo numérico a propósito: is_won NO implica estar en valid_ids
    # (1..8) — mantiene "Ganadas" totalmente aislada de "Activas" en el
    # fixture, evitando un solapamiento que complicaría verificar cada
    # bucket por separado (la regla real de valid_ids es sobre el PREFIJO
    # del nombre, no sobre is_won — ver `_stage_partition`).
    {"id": 4, "name": "Ganado", "is_won": True},
    {"id": 5, "name": "Suspendido - Pausa"},
]
TAGS = [
    {"id": 10, "name": "New Names"},
    {"id": 11, "name": "Base Instalada"},
    {"id": 12, "name": "VIP"},
]


def _hp(hours) -> list:
    return [{"string": "Horas", "value": str(hours)}]


LEADS = [
    # Prospectos (stage 1)
    {"id": 100, "type": "opportunity", "active": True, "stage_id": 1, "tag_ids": [10],
     "lead_properties": _hp(20), "probability": 10, "expected_revenue": 1000},
    {"id": 101, "type": "opportunity", "active": True, "stage_id": 1, "tag_ids": [11],
     "lead_properties": _hp(10), "probability": 20, "expected_revenue": 2000},
    # Activas (stages 2, 3)
    {"id": 200, "type": "opportunity", "active": True, "stage_id": 2, "tag_ids": [10],
     "lead_properties": _hp(50), "probability": 40, "expected_revenue": 10000},
    {"id": 201, "type": "opportunity", "active": True, "stage_id": 3, "tag_ids": [11],
     "lead_properties": _hp(30), "probability": 60, "expected_revenue": 5000},
    # Ganadas (stage 4, is_won) — 302 es del año pasado, debe excluirse.
    {"id": 300, "type": "opportunity", "active": True, "stage_id": 4, "tag_ids": [10],
     "lead_properties": _hp(15), "date_closed": f"{THIS_YEAR}-03-15"},
    {"id": 301, "type": "opportunity", "active": True, "stage_id": 4, "tag_ids": [11],
     "lead_properties": _hp(25), "date_closed": f"{THIS_YEAR}-06-20"},
    {"id": 302, "type": "opportunity", "active": True, "stage_id": 4, "tag_ids": [10],
     "lead_properties": _hp(99), "date_closed": f"{LAST_YEAR}-12-31"},
    # Perdidas — 401 excluida por motivo administrativo, 402 por año.
    {"id": 400, "type": "opportunity", "active": False, "probability": 0, "tag_ids": [10],
     "lead_properties": _hp(5), "date_closed": f"{THIS_YEAR}-02-10 10:00:00",
     "lost_reason_id": [900, "Sin presupuesto"]},
    {"id": 401, "type": "opportunity", "active": False, "probability": 0, "tag_ids": [11],
     "lead_properties": _hp(8), "date_closed": f"{THIS_YEAR}-05-11 09:00:00",
     "lost_reason_id": [901, "Cierre Administrativo"]},
    {"id": 402, "type": "opportunity", "active": False, "probability": 0, "tag_ids": [10],
     "lead_properties": _hp(50), "date_closed": f"{LAST_YEAR}-11-01 00:00:00",
     "lost_reason_id": [900, "Sin presupuesto"]},
    # Suspendidas (stage 5)
    {"id": 500, "type": "opportunity", "active": True, "stage_id": 5, "tag_ids": [10],
     "lead_properties": _hp(12)},
    {"id": 501, "type": "opportunity", "active": True, "stage_id": 5, "tag_ids": [],
     "lead_properties": _hp(3)},
]


def _build_client(**overrides) -> FakeOdooClient:
    return FakeOdooClient(**{"stages": STAGES, "tags": TAGS, "leads": LEADS, **overrides})


@pytest.fixture(autouse=True)
def _clear_commercial_cache():
    svc_module.clear_cache()
    yield
    svc_module.clear_cache()


# ============================================================================
# 3 — Odoo config ausente ⇒ fail-soft (nunca rompe el Hub)
# ============================================================================

def test_missing_odoo_config_raises_configuration_error_not_data_error():
    client = _build_client(configured=False)
    service = CommercialService(client=client)
    with pytest.raises(CommercialConfigurationError):
        service.get_summary()
    with pytest.raises(CommercialConfigurationError):
        service.get_pipeline()
    assert client.calls == []  # nunca intenta una consulta real sin config


# ============================================================================
# 4 — Etapas: Prospectos (stage "0") vs Activas (stages "1".."8")
# ============================================================================

def test_stage_partition_separates_prospects_from_active_1_to_8():
    service = CommercialService(client=_build_client())
    valid_ids, prospect_ids, suspended_ids = service._stage_partition()
    assert prospect_ids == [1]
    assert valid_ids == [2, 3]  # 4 ("Ganado") no tiene prefijo numérico — is_won ≠ 1..8
    assert suspended_ids == [5]


def test_summary_prospects_and_active_never_mix():
    summary = CommercialService(client=_build_client()).get_summary("todos")
    assert summary["prospectos"]["count"] == 2
    assert summary["prospectos"]["hours"] == 30  # 20 + 10
    assert summary["activas"]["count"] == 2
    assert summary["activas"]["hours"] == 80  # 50 + 30


# ============================================================================
# 5/6 — Resolución NN / BI
# ============================================================================

def test_nn_tag_resolved_by_name_containing_new_case_insensitive():
    client = _build_client(tags=[{"id": 77, "name": "Definitely NEW stuff"}, {"id": 78, "name": "Otro"}])
    service = CommercialService(client=client)
    nn_tag, bi_tag = service._nn_bi_tags()
    assert nn_tag["id"] == 77
    assert bi_tag is None


def test_bi_tag_requires_both_base_and_instal_substrings():
    client = _build_client(tags=[
        {"id": 1, "name": "Solo Base"}, {"id": 2, "name": "Solo Instalada"},
        {"id": 3, "name": "BASE Instalada"},
    ])
    service = CommercialService(client=client)
    _nn, bi_tag = service._nn_bi_tags()
    assert bi_tag["id"] == 3


def test_nn_bi_ids_never_hardcoded_resolved_fresh_from_fixture():
    summary = CommercialService(client=_build_client()).get_summary("todos")
    assert summary["labels"] == {"nn": "New Names", "bi": "Base Instalada"}


def test_missing_nn_or_bi_tag_falls_back_to_legacy_default_labels():
    client = _build_client(tags=[{"id": 12, "name": "VIP"}])
    summary = CommercialService(client=client).get_summary("todos")
    assert summary["labels"] == {"nn": "New Names", "bi": "Base Inst."}


# ============================================================================
# 7 — Horas desde lead_properties (legacy-compatible semantic resolution)
# ============================================================================

def test_get_property_num_reads_horas_by_label():
    assert get_property_num([{"string": "Horas", "value": "42"}], "Horas") == 42


@pytest.mark.parametrize("properties", [None, False, [], [{"string": "Otra", "value": "5"}]])
def test_get_property_num_returns_zero_when_missing(properties):
    assert get_property_num(properties, "Horas") == 0


def test_get_property_num_returns_zero_for_non_numeric_value():
    assert get_property_num([{"string": "Horas", "value": "no-numero"}], "Horas") == 0


# ============================================================================
# 8 — Weighted hours / weighted revenue
# ============================================================================

def test_active_weighted_hours_and_revenue_match_hand_computed_values():
    activas = CommercialService(client=_build_client()).get_summary("todos")["activas"]
    # 0.4*50 + 0.6*30 = 20 + 18 = 38.0 ; 0.4*10000 + 0.6*5000 = 4000+3000=7000.0
    assert activas["weighted_hours"] == 38.0
    assert activas["weighted_revenue"] == 7000.0
    assert activas["revenue"] == 15000.0


def test_pipeline_summary_field_combines_active_and_prospects():
    pipeline = CommercialService(client=_build_client()).get_summary("todos")["pipeline"]
    assert pipeline["hours"] == 110  # 80 (activas) + 30 (prospectos)
    # weighted: 38.0 (activas) + (0.1*20 + 0.2*10) = 38 + 4 = 42.0
    assert pipeline["weighted_hours"] == 42.0


# ============================================================================
# 9 — Ganadas: año calendario actual únicamente
# ============================================================================

def test_won_excludes_previous_year_and_counts_current_year_only():
    ganadas = CommercialService(client=_build_client()).get_summary("todos")["ganadas"]
    assert ganadas["count"] == 2  # 300, 301 — 302 (año pasado) excluida
    assert ganadas["hours"] == 40  # 15 + 25


# ============================================================================
# 10 — Perdidas: exclusión administrativa + año calendario actual
# ============================================================================

def test_lost_excludes_administrative_motivo_and_previous_year():
    perdidas = CommercialService(client=_build_client()).get_summary("todos")["perdidas"]
    assert perdidas["count"] == 1  # 400 — 401 (admin) y 402 (año pasado) excluidas
    assert perdidas["hours"] == 5


def test_lost_motivo_resolution_falls_back_across_odoo_versions():
    service = CommercialService(client=_build_client())
    motivos = service._resolve_lost_motivos([{"id": 400}, {"id": 401}])
    assert motivos[400] == "Sin presupuesto"
    assert motivos[401] == "Cierre Administrativo"


def test_excluded_lost_motivos_set_is_case_and_whitespace_insensitive():
    from webapp.services.commercial_service import _is_excluded_motivo
    assert _is_excluded_motivo("Cierre Administrativo") is True
    assert _is_excluded_motivo("  cierre administrativo  ") is True
    assert _is_excluded_motivo("CIERRE ADMINISTRATIVO") is True
    assert _is_excluded_motivo("Sin presupuesto") is False


# ============================================================================
# 11 — Suspendidas
# ============================================================================

def test_suspended_counts_and_hours_from_suspended_stage():
    suspendidas = CommercialService(client=_build_client()).get_summary("todos")["suspendidas"]
    assert suspendidas["count"] == 2
    assert suspendidas["hours"] == 15  # 12 + 3


# ============================================================================
# 12 — Fórmulas de conversión
# ============================================================================

def test_conversion_formulas_todos():
    summary = CommercialService(client=_build_client()).get_summary("todos")
    # won=2, lost=1, susp=2 -> 2/(2+1)=66.7 ; 2/(2+1+2)=40.0
    assert summary["conversion"] == 66.7
    assert summary["conversion_with_suspended"] == 40.0


def test_conversion_is_zero_when_denominator_is_zero():
    client = _build_client(leads=[])
    summary = CommercialService(client=client).get_summary("todos")
    assert summary["conversion"] == 0
    assert summary["conversion_with_suspended"] == 0


# ============================================================================
# 13 — Pipeline por etapa: orden por número inicial de etapa
# ============================================================================

def test_pipeline_stages_sorted_by_leading_stage_number():
    pipeline = CommercialService(client=_build_client()).get_pipeline("todos")
    names = [s["stage"] for s in pipeline["stages"]]
    assert names == ["1 Contacto", "5 Negociación"]


def test_pipeline_stage_aggregates_match_hand_computed_values():
    pipeline = CommercialService(client=_build_client()).get_pipeline("todos")
    contacto = next(s for s in pipeline["stages"] if s["stage"] == "1 Contacto")
    assert contacto == {
        "stage": "1 Contacto", "count": 1, "revenue": 10000.0,
        "hours": 50, "weighted_count": 0.4, "weighted_hours": 20.0,
    }
    assert pipeline["opportunities"] == 2
    assert pipeline["revenue"] == 15000.0


def test_pipeline_never_includes_prospect_stage_0():
    pipeline = CommercialService(client=_build_client()).get_pipeline("todos")
    assert pipeline["opportunities"] == 2  # NUNCA 4 (que incluiría los 2 prospectos)


# ============================================================================
# 14 — Filtro Todos / NN / BI (summary Y pipeline)
# ============================================================================

def test_summary_nn_view_matches_hand_computed_values():
    nn = CommercialService(client=_build_client()).get_summary("nn")
    assert nn["prospectos"] == {"count": 1, "hours": 20}
    assert nn["activas"]["count"] == 1
    assert nn["activas"]["hours"] == 50
    assert nn["activas"]["weighted_hours"] == 20.0
    assert nn["ganadas"] == {"count": 1, "hours": 15}
    assert nn["perdidas"] == {"count": 1, "hours": 5}
    assert nn["suspendidas"] == {"count": 1, "hours": 12}
    assert nn["conversion"] == 50.0
    assert round(nn["conversion_with_suspended"], 1) == 33.3


def test_summary_bi_view_matches_hand_computed_values():
    bi = CommercialService(client=_build_client()).get_summary("bi")
    assert bi["prospectos"] == {"count": 1, "hours": 10}
    assert bi["activas"]["count"] == 1
    assert bi["activas"]["hours"] == 30
    assert bi["ganadas"] == {"count": 1, "hours": 25}
    # 401 (BI, perdida) fue excluida por motivo administrativo.
    assert bi["perdidas"] == {"count": 0, "hours": 0}
    assert bi["suspendidas"] == {"count": 0, "hours": 0}
    assert bi["conversion"] == 100.0


def test_pipeline_nn_and_bi_views_isolate_their_own_tag():
    nn_pipeline = CommercialService(client=_build_client()).get_pipeline("nn")
    bi_pipeline = CommercialService(client=_build_client()).get_pipeline("bi")
    assert nn_pipeline["opportunities"] == 1
    assert nn_pipeline["stages"][0]["stage"] == "1 Contacto"
    assert bi_pipeline["opportunities"] == 1
    assert bi_pipeline["stages"][0]["stage"] == "5 Negociación"


def test_unknown_tipo_falls_back_to_todos():
    fallback = CommercialService(client=_build_client()).get_summary("unknown-tipo")
    todos = CommercialService(client=_build_client()).get_summary("todos")
    assert fallback == todos


# ============================================================================
# 15 — Cache: evita llamadas Odoo duplicadas dentro del TTL
# ============================================================================

def test_cache_avoids_duplicate_odoo_calls_across_tipos():
    client = _build_client()
    service = CommercialService(client=client)
    service.get_summary("todos")
    calls_after_first = len(client.calls)
    assert calls_after_first > 0
    service.get_summary("nn")  # MISMO cache — no debe disparar nuevas consultas
    service.get_summary("bi")
    assert len(client.calls) == calls_after_first


def test_cache_is_isolated_between_summary_and_pipeline():
    client = _build_client()
    service = CommercialService(client=client)
    service.get_summary("todos")
    calls_after_summary = len(client.calls)
    service.get_pipeline("todos")
    assert len(client.calls) > calls_after_summary  # pipeline es un cache KEY distinto


def test_clear_cache_forces_a_fresh_odoo_call():
    client = _build_client()
    service = CommercialService(client=client)
    service.get_summary("todos")
    calls_before = len(client.calls)
    svc_module.clear_cache()
    service.get_summary("todos")
    assert len(client.calls) > calls_before


# ============================================================================
# 16 — Falla real de Odoo (nunca de configuración)
# ============================================================================

def test_odoo_transport_failure_surfaces_as_commercial_data_error_with_stage():
    client = _build_client()
    client.raise_on("crm.stage", stage="execute")
    service = CommercialService(client=client)
    with pytest.raises(CommercialDataError) as exc_info:
        service.get_summary()
    assert exc_info.value.stage == "execute"


def test_lost_reason_resolution_failure_is_best_effort_never_breaks_summary():
    # `_resolve_lost_motivos` prueba lost_reason_id (Attempt 1, Many2one),
    # luego lost_reason_ids (Attempt 2, Many2many) — una falla de transporte
    # en Attempt 1 debe ser absorbida (best-effort) y el código sigue
    # resolviendo el motivo administrativo vía Attempt 2, nunca romper el
    # summary completo. Lead 401 tiene AMBOS campos poblados en este test
    # específico (a diferencia del fixture global) para poder demostrar la
    # caída real de un mecanismo al otro, no solo "no explota".
    leads_with_m2m = [dict(lead) for lead in LEADS]
    lead_401 = next(l for l in leads_with_m2m if l["id"] == 401)
    lead_401["lost_reason_ids"] = [901]
    client = _build_client(leads=leads_with_m2m, lost_reasons=[{"id": 901, "name": "Cierre Administrativo"}])
    original_search_read = client.search_read

    def _flaky_search_read(model, domain=None, fields=None, limit=None, order=None):
        if model == "crm.lead" and fields == ["id", "lost_reason_id"]:
            raise OdooTransportError("forced", stage="execute")
        return original_search_read(model, domain=domain, fields=fields, limit=limit, order=order)

    client.search_read = _flaky_search_read
    summary = CommercialService(client=client).get_summary("todos")
    # 400 (sin motivo administrativo) se mantiene; 401 se resuelve vía
    # Attempt 2 (lost_reason_ids) pese a que Attempt 1 falló, y sigue
    # excluido por ser "Cierre Administrativo".
    assert summary["perdidas"]["count"] == 1
