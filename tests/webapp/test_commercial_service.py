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

from datetime import date, timedelta

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


# ============================================================================
# Batch 3A / C2 — Actividades pendientes (`mail.activity`)
# ============================================================================

ACTIVITY_LEADS = [
    # Universo DELIBERADAMENTE heterogéneo: la capability representa TRABAJO
    # PENDIENTE, no "Oportunidades Activas". Entran actividades de leads en
    # etapa 0, activos, ganados, perdidos y ARCHIVADOS.
    {"id": 900, "name": "Prospecto Cero", "stage_id": 1, "active": True, "tag_ids": [10],
     "partner_id": [70, "Cliente Prospecto"]},
    {"id": 901, "name": "Activa Uno", "stage_id": 2, "active": True, "tag_ids": [11],
     "partner_id": [71, "Cliente Activo"]},
    {"id": 902, "name": "Ganada Uno", "stage_id": 4, "active": True, "tag_ids": [10, 11],
     "partner_id": [72, "Cliente Ganado"]},
    {"id": 903, "name": "Archivada Uno", "stage_id": 3, "active": False, "tag_ids": [],
     "partner_id": False},
]

ACTIVITIES = [
    {"id": 1, "res_id": 900, "res_model": "crm.lead", "date_deadline": "2026-01-05",
     "activity_type_id": [5, "Llamada"], "summary": "Primer contacto", "note": "<p>Llamar   al  <b>CTO</b></p>",
     "user_id": [20, "Ana Gómez"]},
    {"id": 2, "res_id": 901, "res_model": "crm.lead", "date_deadline": "2026-03-10",
     "activity_type_id": [6, "Reunión"], "summary": "", "note": False,
     "user_id": [21, "Bruno Paz"]},
    {"id": 3, "res_id": 902, "res_model": "crm.lead", "date_deadline": "2026-02-01",
     "activity_type_id": [5, "Llamada"], "summary": "Seguimiento", "note": "",
     "user_id": [20, "Ana Gómez"]},
    # Archivada + sin fecha + sin responsable: los tres bordes juntos.
    {"id": 4, "res_id": 903, "res_model": "crm.lead", "date_deadline": False,
     "activity_type_id": False, "summary": "Sin fecha", "note": "", "user_id": False},
]


def _activities_client(**kwargs):
    return FakeOdooClient(stages=STAGES, tags=TAGS, leads=ACTIVITY_LEADS, activities=ACTIVITIES, **kwargs)


def test_activities_query_targets_mail_activity_on_crm_lead_only():
    client = _activities_client()
    CommercialService(client).get_activities("todos")
    act_calls = [c for c in client.calls if c[0] == "mail.activity"]
    assert len(act_calls) == 1, "una sola consulta a mail.activity (las 3 vistas se calculan en memoria)"
    _model, domain, fields = act_calls[0]
    assert domain == [["res_model", "=", "crm.lead"]]
    assert set(fields) == {"res_id", "date_deadline", "activity_type_id", "summary", "note", "user_id"}


def test_activities_join_includes_archived_leads_on_purpose():
    # Si la oportunidad fue archivada pero la actividad sigue pendiente, la
    # tarea existe igual y debe verse — de ahí `active in [True, False]`.
    client = _activities_client()
    CommercialService(client).get_activities("todos")
    lead_calls = [c for c in client.calls if c[0] == "crm.lead"]
    assert lead_calls, "se consulta crm.lead para resolver nombre/cliente/tags"
    _model, domain, fields = lead_calls[-1]
    assert ["active", "in", [True, False]] in domain
    assert "tag_ids" in fields, "tag_ids es lo que habilita el particionado NN/BI en memoria"


def test_activities_universe_is_not_limited_to_active_stages():
    rows = CommercialService(_activities_client()).get_activities("todos")["activities"]
    names = {r["opportunity"] for r in rows}
    assert names == {"Prospecto Cero", "Activa Uno", "Ganada Uno", "Archivada Uno"}
    assert len(rows) == 4


def test_activities_are_ordered_by_deadline_with_undated_last():
    rows = CommercialService(_activities_client()).get_activities("todos")["activities"]
    assert [r["deadline"] for r in rows] == ["2026-01-05", "2026-02-01", "2026-03-10", None]


def test_activities_note_is_normalised_to_plain_text():
    rows = CommercialService(_activities_client()).get_activities("todos")["activities"]
    first = next(r for r in rows if r["deadline"] == "2026-01-05")
    assert first["note"] == "Llamar al CTO", "tags fuera y whitespace colapsado"
    assert next(r for r in rows if r["deadline"] == "2026-03-10")["note"] == ""


def test_activities_expose_iso_deadline_never_a_precomputed_days_until():
    # El cálculo de días es del CLIENTE: hacerlo acá lo dejaría congelado por
    # el TTL del cache y al cruzar la medianoche clasificaría mal el bucket.
    rows = CommercialService(_activities_client()).get_activities("todos")["activities"]
    assert all("days_until" not in r for r in rows)
    assert all(r["deadline"] is None or len(r["deadline"]) == 10 for r in rows)


def test_activities_relational_fields_are_flattened_with_safe_fallbacks():
    rows = CommercialService(_activities_client()).get_activities("todos")["activities"]
    undated = next(r for r in rows if r["deadline"] is None)
    assert undated["opportunity"] == "Archivada Uno"
    assert undated["client"] == ""   # partner_id False
    assert undated["user"] == ""     # user_id False
    assert undated["type"] == ""     # activity_type_id False


def test_activities_nn_bi_views_are_partitioned_in_memory_by_lead_tags():
    service = CommercialService(_activities_client())
    todos = service.get_activities("todos")
    nn = service.get_activities("nn")
    bi = service.get_activities("bi")
    assert todos["total"] == 4
    # NN = tag 10 → leads 900 y 902; BI = tag 11 → leads 901 y 902.
    assert {r["opportunity"] for r in nn["activities"]} == {"Prospecto Cero", "Ganada Uno"}
    assert {r["opportunity"] for r in bi["activities"]} == {"Activa Uno", "Ganada Uno"}
    assert nn["total"] == 2 and bi["total"] == 2


def test_activities_never_leak_the_internal_tag_helper_field():
    rows = CommercialService(_activities_client()).get_activities("todos")["activities"]
    assert all("_tag_ids" not in r for r in rows)


def test_activities_use_the_module_cache(monkeypatch):
    svc_module.clear_cache()
    client = _activities_client()
    service = CommercialService(client)
    service.get_activities("todos")
    service.get_activities("nn")
    assert len([c for c in client.calls if c[0] == "mail.activity"]) == 1, "la segunda vista sale del cache"
    svc_module.clear_cache()


def test_activities_unconfigured_odoo_raises_configuration_error():
    client = _activities_client(configured=False)
    svc_module.clear_cache()
    with pytest.raises(CommercialConfigurationError):
        CommercialService(client).get_activities("todos")


def test_activities_transport_failure_becomes_commercial_data_error():
    svc_module.clear_cache()
    client = _activities_client()
    client.raise_on("mail.activity", stage="execute")
    with pytest.raises(CommercialDataError) as excinfo:
        CommercialService(client).get_activities("todos")
    assert excinfo.value.stage == "execute"
    svc_module.clear_cache()


def test_activities_invalid_tipo_falls_back_to_todos():
    svc_module.clear_cache()
    service = CommercialService(_activities_client())
    assert service.get_activities("no-existe")["total"] == service.get_activities("todos")["total"]
    svc_module.clear_cache()


def test_activities_path_never_writes_to_odoo():
    client = _activities_client()
    CommercialService(client).get_activities("todos")
    assert not hasattr(client, "write"), "el fake no expone writes"
    # Sólo lecturas: cada call registrada es un search_read.
    assert all(model in ("crm.tag", "crm.lead", "mail.activity") for model, _d, _f in client.calls)


def test_iso_deadline_and_note_helpers_are_fail_soft():
    from webapp.services.commercial_service import iso_deadline, strip_html_note
    assert iso_deadline("2026-08-20") == "2026-08-20"
    assert iso_deadline("2026-08-20 10:00:00") == "2026-08-20"
    for bad in (None, False, True, "", "basura", "2026-13-45"):
        assert iso_deadline(bad) is None, bad
    assert strip_html_note("<p>a   b</p>") == "a b"
    for bad in (None, False, 0, 123):
        assert strip_html_note(bad) == ""


# ============================================================================
# Landing Homologation — segmentación NN/BI honesta
# ============================================================================

def test_segments_count_the_four_sets_explicitly_never_a_derived_remainder():
    # El fixture tiene, a propósito, una oportunidad con AMBOS tags y otra con
    # NINGUNO: es el caso que hace que NN + BI no reconcilie con el total.
    svc_module.clear_cache()
    leads = [
        {"id": 1, "type": "opportunity", "stage_id": 2, "active": True, "tag_ids": [10], "probability": 50,
         "expected_revenue": 0, "lead_properties": [{"string": "Horas", "value": 10}]},
        {"id": 2, "type": "opportunity", "stage_id": 2, "active": True, "tag_ids": [11], "probability": 50,
         "expected_revenue": 0, "lead_properties": [{"string": "Horas", "value": 20}]},
        {"id": 3, "type": "opportunity", "stage_id": 2, "active": True, "tag_ids": [10, 11], "probability": 50,
         "expected_revenue": 0, "lead_properties": [{"string": "Horas", "value": 30}]},
        {"id": 4, "type": "opportunity", "stage_id": 2, "active": True, "tag_ids": [], "probability": 50,
         "expected_revenue": 0, "lead_properties": [{"string": "Horas", "value": 40}]},
        # Una SEGUNDA sin tags: con una sola, el solapamiento (+1) y el hueco
        # (-1) se cancelan y nn+bi daría igual al total por casualidad,
        # escondiendo justo lo que este test quiere demostrar.
        {"id": 5, "type": "opportunity", "stage_id": 2, "active": True, "tag_ids": [], "probability": 50,
         "expected_revenue": 0, "lead_properties": [{"string": "Horas", "value": 50}]},
    ]
    client = FakeOdooClient(stages=STAGES, tags=TAGS, leads=leads)
    summary = CommercialService(client).get_summary("todos")
    seg = summary["segments"]["activas"]
    assert summary["activas"]["count"] == 5
    assert seg["nn"]["count"] == 2      # ids 1 y 3
    assert seg["bi"]["count"] == 2      # ids 2 y 3
    assert seg["both"]["count"] == 1    # id 3
    assert seg["neither"]["count"] == 2  # ids 4 y 5
    # La prueba de que NO es una partición: nn + bi != total.
    assert seg["nn"]["count"] + seg["bi"]["count"] != summary["activas"]["count"]
    # Y la identidad que SÍ vale.
    assert seg["nn"]["count"] + seg["bi"]["count"] - seg["both"]["count"] + seg["neither"]["count"] == 5
    assert seg["nn"]["hours"] == 40 and seg["neither"]["hours"] == 90
    svc_module.clear_cache()


def test_segments_only_exist_in_the_unfiltered_view():
    # Dentro de "New Name" un desglose NN/BI sería redundante o engañoso.
    svc_module.clear_cache()
    service = CommercialService(_build_client())
    assert "segments" in service.get_summary("todos")
    assert "segments" not in service.get_summary("nn")
    assert "segments" not in service.get_summary("bi")
    svc_module.clear_cache()


def test_segments_cover_every_landing_metric_that_shows_a_split():
    svc_module.clear_cache()
    segments = CommercialService(_build_client()).get_summary("todos")["segments"]
    assert set(segments) == {"prospectos", "pipeline", "activas", "ganadas", "perdidas", "suspendidas"}
    for name, seg in segments.items():
        assert set(seg) == {"nn", "bi", "both", "neither"}, name
    svc_module.clear_cache()


# ============================================================================
# Landing Homologation — Analíticas del Pipeline + Creadas
# ============================================================================

_TODAY = date.today()
_THIS_MONDAY = _TODAY - timedelta(days=_TODAY.weekday())
_LAST_MONDAY = _THIS_MONDAY - timedelta(days=7)


def _d(day):
    return day.isoformat()


ANALYTICS_STAGES = STAGES + [{"id": 6, "name": "Lead"}]


def _analytics_client():
    # Etapas 1–8 del fixture base: id 2 ("1 Contacto") y id 3 ("5 Negociación").
    leads = [
        # Activa vieja y estancada (última transición hace 60 días).
        {"id": 1, "type": "opportunity", "stage_id": 3, "active": True, "tag_ids": [10], "probability": 50,
         "expected_revenue": 0, "lead_properties": [], "create_date": _d(_TODAY - timedelta(days=90))},
        # Activa reciente, con transición de ayer → NO estancada.
        {"id": 2, "type": "opportunity", "stage_id": 2, "active": True, "tag_ids": [11], "probability": 50,
         "expected_revenue": 0, "lead_properties": [], "create_date": _d(_TODAY - timedelta(days=10))},
        # Ganada este año: creada hace 40 días, cerrada hace 10.
        {"id": 3, "type": "opportunity", "stage_id": 4, "active": True, "tag_ids": [10], "probability": 100,
         "expected_revenue": 0, "lead_properties": [],
         "create_date": _d(_TODAY - timedelta(days=40)), "date_closed": _d(_TODAY - timedelta(days=10))},
        # Creada ESTA semana y ya en etapa 1 sin historial → cuenta por create_date.
        {"id": 4, "type": "opportunity", "stage_id": 2, "active": True, "tag_ids": [10], "probability": 10,
         "expected_revenue": 0, "lead_properties": [], "create_date": _d(_THIS_MONDAY)},
        # Creada la semana PASADA, archivada — se cuenta igual.
        {"id": 5, "type": "opportunity", "stage_id": 2, "active": False, "tag_ids": [11], "probability": 0,
         "expected_revenue": 0, "lead_properties": [], "create_date": _d(_LAST_MONDAY)},
    ]
    messages = [
        # id 1: entró a "5 Negociación" hace 60 días (desde "1 Contacto").
        {"id": 100, "res_id": 1, "date": _d(_TODAY - timedelta(days=60)), "tracked_field": "stage_id",
         "model": "crm.lead"},
        # id 2: entró a "1 Contacto" ayer.
        {"id": 101, "res_id": 2, "date": _d(_TODAY - timedelta(days=1)), "tracked_field": "stage_id",
         "model": "crm.lead"},
    ]
    tracking = [
        {"mail_message_id": 100, "field": "stage_id", "old_value_integer": 2, "new_value_integer": 3},
        {"mail_message_id": 101, "field": "stage_id", "old_value_integer": 1, "new_value_integer": 2},
    ]
    return FakeOdooClient(stages=ANALYTICS_STAGES, tags=TAGS, leads=leads,
                          messages=messages, tracking=tracking)


def test_analytics_pipeline_age_averages_days_open_of_active_opportunities():
    svc_module.clear_cache()
    a = CommercialService(_analytics_client()).get_analytics("todos")
    # Activas en etapas 1–8: ids 1 (90d), 2 (10d), 4 (0d desde el lunes).
    assert a["pipeline_age"]["count"] == 3
    assert a["pipeline_age"]["avg_days"] > 0
    svc_module.clear_cache()


def test_analytics_close_time_only_counts_this_years_won_opportunities():
    svc_module.clear_cache()
    a = CommercialService(_analytics_client()).get_analytics("todos")
    assert a["close_time"]["count"] == 1
    assert a["close_time"]["avg_days"] == 30  # creada hace 40, cerrada hace 10
    svc_module.clear_cache()


def test_analytics_stagnant_uses_last_stage_move_not_creation_date():
    svc_module.clear_cache()
    a = CommercialService(_analytics_client()).get_analytics("todos")
    # id 1 se movió hace 60 días (>30) → estancada.
    # id 2 se movió AYER aunque se creó hace 10 días → NO estancada.
    # id 4 se creó esta semana y no tiene historial → NO estancada.
    assert a["stagnant"]["count"] == 1
    assert a["stagnant"]["threshold_days"] == 30
    svc_module.clear_cache()


def test_analytics_stage_time_is_derived_from_transition_history():
    svc_module.clear_cache()
    a = CommercialService(_analytics_client()).get_analytics("todos")
    # id 1: entró a "1 Contacto" al crearse y salió 30 días después → hay
    # una etapa con dato.
    assert a["stage_time"]["stages_with_data"] >= 1
    assert a["stage_time"]["avg_days"] is not None
    svc_module.clear_cache()


def test_analytics_stage_conversion_reports_the_bottleneck_rate():
    svc_module.clear_cache()
    a = CommercialService(_analytics_client()).get_analytics("todos")
    assert a["stage_conversion"]["min_rate"] is not None
    assert 0 <= a["stage_conversion"]["min_rate"] <= 100
    svc_module.clear_cache()


def test_creadas_counts_pipeline_entry_by_monday_based_weeks():
    # Escenario AISLADO (auditoría "creadas" week-boundary, ver HANDOFF):
    # antes reutilizaba _analytics_client(), cuyo id 2 (creado hace 10 días,
    # con una transición real a "1 Contacto" fechada ayer) contamina el
    # conteo de "esta semana" de jueves a domingo — su create_date cae
    # justo sobre el límite de last_monday ese rango de días. Fixture propio
    # y mínimo para que el resultado sea invariante los 7 días de la
    # semana: this_monday/last_monday se derivan de HOY, nunca un offset
    # fijo como "hoy - 10" (ese offset fijo fue la causa raíz).
    svc_module.clear_cache()
    this_monday = date.today() - timedelta(days=date.today().weekday())
    last_monday = this_monday - timedelta(days=7)
    leads = [
        # A — creada ESTA semana, ya en "1 Contacto" (stage_one), sin
        # historial de transición → cuenta por create_date (fallback).
        {"id": 1, "type": "opportunity", "active": True, "stage_id": 2, "tag_ids": [],
         "expected_revenue": 0, "lead_properties": [], "create_date": this_monday.isoformat()},
        # B — creada la semana PASADA, archivada, ya en "1 Contacto", sin
        # historial → cuenta igual por create_date (fallback); archivada no
        # la excluye de "creadas".
        {"id": 2, "type": "opportunity", "active": False, "stage_id": 2, "tag_ids": [],
         "expected_revenue": 0, "lead_properties": [], "create_date": last_monday.isoformat()},
    ]
    client = FakeOdooClient(stages=STAGES, tags=TAGS, leads=leads)
    a = CommercialService(client).get_analytics("todos")
    assert a["creadas"]["this_week"] == 1
    assert a["creadas"]["last_week"] == 1
    svc_module.clear_cache()


def test_creadas_uses_stage_one_entry_transition_before_create_date():
    # Contrato de negocio (ver HANDOFF): "Creadas" es la entrada al
    # PIPELINE (etapa 1), no la fecha de alta — una oportunidad puede haber
    # nacido como prospecto la semana pasada y recién entrar a "1 Contacto"
    # esta semana; cuenta por la fecha de ENTRADA, no por create_date.
    # Escenario aislado, sin relación con _analytics_client()/id 2.
    svc_module.clear_cache()
    this_monday = date.today() - timedelta(days=date.today().weekday())
    last_monday = this_monday - timedelta(days=7)
    leads = [
        # Creada la semana PASADA (por create_date parecería "last_week"),
        # pero recién entró a "1 Contacto" (stage_one) ESTA semana.
        {"id": 1, "type": "opportunity", "active": True, "stage_id": 2, "tag_ids": [],
         "expected_revenue": 0, "lead_properties": [], "create_date": last_monday.isoformat()},
    ]
    messages = [
        {"id": 100, "res_id": 1, "date": this_monday.isoformat(), "tracked_field": "stage_id",
         "model": "crm.lead"},
    ]
    tracking = [
        {"mail_message_id": 100, "field": "stage_id", "old_value_integer": 1, "new_value_integer": 2},
    ]
    client = FakeOdooClient(stages=STAGES, tags=TAGS, leads=leads,
                            messages=messages, tracking=tracking)
    a = CommercialService(client).get_analytics("todos")
    assert a["creadas"]["this_week"] == 1
    assert a["creadas"]["last_week"] == 0
    svc_module.clear_cache()


# ============================================================================
# Creadas Legacy Parity (ver HANDOFF) — drill mensual + ranking por vendedor,
# paridad con el legacy `/crm/opp-by-month`. `_created_entry_events` es el
# ÚNICO algoritmo de entrada al pipeline — el KPI semanal (arriba) y el
# drill (acá) le pasan universos DISTINTOS a la MISMA función, nunca la
# reimplementan por separado.
# ============================================================================

def _creadas_detail_client():
    """Fixture única que cubre las 6 poblaciones canónicas de una sola vez:
    transición histórica, fallback por create_date, archivada (Perdida),
    ganada, activa "normal" (status = nombre de etapa) y exclusión por
    etapa 0 (revertida a Prospecto pese a tener historial de entrada)."""
    stages = [
        {"id": 1, "name": "0 Prospección"},
        {"id": 2, "name": "1 Contacto"},   # stage_one
        {"id": 3, "name": "5 Negociación"},
    ]
    leads = [
        # id 1 — transición histórica a stage_one (Ene), sigue activa en
        # otra etapa válida (3) → status = nombre de esa etapa.
        {"id": 1, "type": "opportunity", "active": True, "stage_id": 3, "tag_ids": [10],
         "create_date": "2024-03-01", "probability": 40, "partner_id": [70, "Cliente Uno"],
         "user_id": [20, "Ana Gómez"], "source_id": [30, "Referido"], "lead_properties": _hp(12)},
        # id 2 — SIN historial, create_date fallback (Feb), currently en
        # stage_one — MISMO mes que id 3 (para el test de agrupación
        # mensual/ranking), vendedor DISTINTO.
        {"id": 2, "type": "opportunity", "active": True, "stage_id": 2, "tag_ids": [],
         "create_date": "2024-02-10", "probability": 20, "partner_id": [71, "Cliente Dos"],
         "user_id": [21, "Bruno Paz"], "source_id": False, "lead_properties": []},
        # id 3 — ARCHIVADA + probability 0 → "Perdida"; sin historial,
        # create_date fallback, mismo mes que id 2, MISMO vendedor que id 2
        # (para que el ranking sume 2 bajo "Bruno Paz").
        {"id": 3, "type": "opportunity", "active": False, "stage_id": 3, "tag_ids": [],
         "create_date": "2024-02-15", "probability": 0, "partner_id": [72, "Cliente Tres"],
         "user_id": [21, "Bruno Paz"], "source_id": [31, "Web"], "lead_properties": []},
        # id 4 — probability 100 → "Ganada".
        {"id": 4, "type": "opportunity", "active": True, "stage_id": 3, "tag_ids": [],
         "create_date": "2024-02-20", "probability": 100, "partner_id": [73, "Cliente Cuatro"],
         "user_id": [20, "Ana Gómez"], "source_id": False, "lead_properties": []},
        # id 5 — TENÍA una transición histórica a stage_one, pero revirtió a
        # etapa 0 (Prospección) — excluida del drill (guard explícito
        # portado del legacy: "la etapa ACTUAL puede haber revertido a
        # prospecto pese a tener un evento de entrada histórico").
        {"id": 5, "type": "opportunity", "active": True, "stage_id": 1, "tag_ids": [],
         "create_date": "2024-01-01", "probability": 10, "partner_id": False,
         "user_id": False, "source_id": False, "lead_properties": []},
    ]
    messages = [
        {"id": 900, "res_id": 1, "date": "2024-01-15", "tracked_field": "stage_id", "model": "crm.lead"},
        {"id": 901, "res_id": 5, "date": "2024-01-05", "tracked_field": "stage_id", "model": "crm.lead"},
    ]
    tracking = [
        {"mail_message_id": 900, "field": "stage_id", "old_value_integer": 1, "new_value_integer": 2},
        {"mail_message_id": 901, "field": "stage_id", "old_value_integer": 1, "new_value_integer": 2},
    ]
    return FakeOdooClient(stages=stages, tags=TAGS, leads=leads, messages=messages, tracking=tracking)


def test_creadas_detail_covers_all_canonical_populations_and_derives_status():
    svc_module.clear_cache()
    detail = CommercialService(_creadas_detail_client()).get_creadas_detail("todos")
    months = {m["month"]: m for m in detail["months"]}
    by_id = {o["id"]: o for m in months.values() for o in m["opportunities"]}

    # id 5 (revertida a etapa 0) NUNCA aparece — ninguna población canónica
    # la incluye pese a tener historial de transición.
    assert 5 not in by_id
    assert len(by_id) == 4

    # id 1 — transición histórica (Ene 15), status = nombre de etapa actual.
    assert by_id[1]["entry_date"] == "2024-01-15"
    assert by_id[1]["status"] == "5 Negociación"
    assert by_id[1]["seller"] == "Ana Gómez"
    assert by_id[1]["client"] == "Cliente Uno"
    assert by_id[1]["origen"] == "Referido"
    assert by_id[1]["horas"] == 12

    # id 2 — create_date fallback (Feb 10), status = nombre de etapa (stage_one).
    assert by_id[2]["entry_date"] == "2024-02-10"
    assert by_id[2]["status"] == "1 Contacto"
    assert by_id[2]["origen"] == "Sin definir"  # source_id False
    assert by_id[2]["horas"] is None  # sin lead_properties, get_property_num → 0 → None

    # id 3 — archivada + probability 0 → "Perdida" (NUNCA desaparece del
    # drill por estar archivada — MISMO criterio que el KPI semanal).
    assert by_id[3]["status"] == "Perdida"

    # id 4 — probability 100 → "Ganada".
    assert by_id[4]["status"] == "Ganada"


def test_creadas_detail_groups_by_month_descending_and_orders_opportunities_ascending():
    svc_module.clear_cache()
    detail = CommercialService(_creadas_detail_client()).get_creadas_detail("todos")
    months = detail["months"]
    # Ene (id 1) y Feb (id 2, 3, 4) — descendente: Feb antes que Ene.
    assert [m["month"] for m in months] == ["2024-02", "2024-01"]
    feb = months[0]
    assert feb["count"] == 3
    # Orden ASCENDENTE por entry_date dentro del mes — MISMO criterio legacy.
    assert [o["entry_date"] for o in feb["opportunities"]] == ["2024-02-10", "2024-02-15", "2024-02-20"]


def test_creadas_detail_seller_field_supports_ranking_aggregation():
    # El ranking por vendedor se agrega client-side (JS) sobre este campo —
    # el backend sólo necesita exponerlo correctamente por oportunidad.
    svc_module.clear_cache()
    detail = CommercialService(_creadas_detail_client()).get_creadas_detail("todos")
    sellers = [o["seller"] for m in detail["months"] for o in m["opportunities"]]
    assert sellers.count("Bruno Paz") == 2  # ids 2 y 3
    assert sellers.count("Ana Gómez") == 2  # ids 1 y 4


def test_creadas_detail_partitions_by_tag_same_criterion_as_the_kpi():
    svc_module.clear_cache()
    service = CommercialService(_creadas_detail_client())
    todos = service.get_creadas_detail("todos")
    nn = service.get_creadas_detail("nn")
    todos_ids = {o["id"] for m in todos["months"] for o in m["opportunities"]}
    nn_ids = {o["id"] for m in nn["months"] for o in m["opportunities"]}
    assert todos_ids == {1, 2, 3, 4}
    assert nn_ids == {1}  # SOLO id 1 tiene tag_ids=[10] (New Names)


def test_creadas_detail_enrichment_is_one_batched_query_never_per_opportunity():
    # Owner directive — "No per-opportunity Odoo calls. No N+1": UNA sola
    # consulta a crm.lead con `id in [...]` para el enriquecimiento, nunca
    # una por oportunidad del conjunto canónico.
    svc_module.clear_cache()
    client = _creadas_detail_client()
    CommercialService(client).get_creadas_detail("todos")
    lead_calls = [c for c in client.calls if c[0] == "crm.lead"]
    enrichment_calls = [c for c in lead_calls if any(cond[0] == "id" and cond[1] == "in" for cond in c[1])]
    assert len(enrichment_calls) == 1, enrichment_calls


def test_creadas_kpi_and_drill_reconcile_for_the_same_period():
    # Trust invariant (owner directive — "the drill must explain the
    # tile"): para un período comparable, el conteo del KPI semanal debe
    # coincidir con la cantidad de eventos canónicos del drill en ESE
    # período — AMBOS derivan de `_created_entry_events`, nunca de
    # algoritmos distintos que puedan divergir.
    svc_module.clear_cache()
    this_monday = date.today() - timedelta(days=date.today().weekday())
    leads = [
        {"id": 1, "type": "opportunity", "active": True, "stage_id": 2, "tag_ids": [],
         "create_date": this_monday.isoformat(), "probability": 10, "partner_id": [70, "Cliente"],
         "user_id": [20, "Ana"], "source_id": False, "lead_properties": []},
        {"id": 2, "type": "opportunity", "active": False, "stage_id": 2, "tag_ids": [],
         "create_date": this_monday.isoformat(), "probability": 0, "partner_id": [71, "Cliente 2"],
         "user_id": [21, "Bruno"], "source_id": False, "lead_properties": []},
    ]
    stages = [{"id": 1, "name": "0 Prospección"}, {"id": 2, "name": "1 Contacto"}]
    client = FakeOdooClient(stages=stages, tags=TAGS, leads=leads)
    service = CommercialService(client)
    kpi_this_week = service.get_analytics("todos")["creadas"]["this_week"]
    svc_module.clear_cache()
    detail = service.get_creadas_detail("todos")
    drill_this_week = sum(
        1 for m in detail["months"] for o in m["opportunities"]
        if o["entry_date"] >= this_monday.isoformat()
    )
    assert kpi_this_week == drill_this_week == 2
    # Ninguna población (activa/archivada) desapareció del drill.
    all_ids = {o["id"] for m in detail["months"] for o in m["opportunities"]}
    assert all_ids == {1, 2}


def test_creadas_detail_empty_without_valid_stages_or_events():
    svc_module.clear_cache()
    # Sin etapas 1-8 configuradas.
    client = FakeOdooClient(stages=[{"id": 1, "name": "0 Prospección"}], tags=TAGS, leads=[])
    assert CommercialService(client).get_creadas_detail("todos") == {"months": []}
    svc_module.clear_cache()
    # Etapas válidas, pero cero oportunidades.
    client2 = FakeOdooClient(stages=STAGES, tags=TAGS, leads=[])
    assert CommercialService(client2).get_creadas_detail("todos") == {"months": []}


def test_creadas_detail_requires_configuration_and_wraps_transport_errors():
    svc_module.clear_cache()
    unconfigured = FakeOdooClient(stages=STAGES, tags=TAGS, leads=[], configured=False)
    with pytest.raises(CommercialConfigurationError):
        CommercialService(unconfigured).get_creadas_detail("todos")
    svc_module.clear_cache()
    failing = FakeOdooClient(stages=STAGES, tags=TAGS, leads=[])
    failing.raise_on("crm.stage", stage="authenticate")
    with pytest.raises(CommercialDataError) as exc_info:
        CommercialService(failing).get_creadas_detail("todos")
    assert exc_info.value.stage == "authenticate"


def test_analytics_views_are_partitioned_by_tag_without_extra_queries():
    svc_module.clear_cache()
    client = _analytics_client()
    service = CommercialService(client)
    service.get_analytics("todos")
    service.get_analytics("nn")
    service.get_analytics("bi")
    # Una sola construcción: las tres vistas salen del mismo cache.
    assert len([c for c in client.calls if c[0] == "mail.message"]) <= 2
    svc_module.clear_cache()
    nn = CommercialService(_analytics_client()).get_analytics("nn")
    svc_module.clear_cache()
    bi = CommercialService(_analytics_client()).get_analytics("bi")
    # NN tiene las activas 1 y 4; BI sólo la 2.
    assert nn["pipeline_age"]["count"] == 2
    assert bi["pipeline_age"]["count"] == 1
    svc_module.clear_cache()


def test_analytics_without_valid_stages_degrades_instead_of_crashing():
    svc_module.clear_cache()
    client = FakeOdooClient(stages=[{"id": 1, "name": "0 Prospección"}], tags=TAGS, leads=[])
    a = CommercialService(client).get_analytics("todos")
    assert a["pipeline_age"] == {"avg_days": 0, "count": 0}
    assert a["stage_time"]["avg_days"] is None
    assert a["stage_conversion"]["min_rate"] is None
    assert a["creadas"] == {"this_week": 0, "last_week": 0}
    svc_module.clear_cache()


def test_analytics_lead_conversion_measures_unique_companies():
    svc_module.clear_cache()
    leads = [
        # Dos leads de la MISMA empresa: cuentan como una sola.
        {"id": 20, "type": "lead", "active": True, "tag_ids": [10], "partner_id": [70, "ACME"],
         "stage_id": 6, "probability": 0, "expected_revenue": 0, "lead_properties": []},
        {"id": 21, "type": "lead", "active": True, "tag_ids": [10], "partner_id": [70, "ACME"],
         "stage_id": 6, "probability": 0, "expected_revenue": 0, "lead_properties": []},
        # Lead convertido a oportunidad activa en etapa 1–8.
        {"id": 22, "type": "opportunity", "active": True, "tag_ids": [10], "partner_id": [71, "Globex"],
         "stage_id": 2, "probability": 30, "expected_revenue": 0, "lead_properties": []},
    ]
    messages = [{"id": 200, "res_id": 22, "date": _d(_TODAY), "tracked_field": "type", "model": "crm.lead"}]
    tracking = [{"mail_message_id": 200, "field": "type",
                 "old_value_char": "Lead", "new_value_char": "Oportunidad"}]
    client = FakeOdooClient(stages=ANALYTICS_STAGES, tags=TAGS, leads=leads,
                            messages=messages, tracking=tracking)
    lc = CommercialService(client).get_analytics("todos")["lead_conversion"]
    assert lc["companies"] == 2, "ACME (2 leads) + Globex = 2 empresas"
    assert lc["any_conv"] == 1 and lc["rate_any_conv"] == 50.0
    assert lc["adv_opp"] == 1
    svc_module.clear_cache()


def test_analytics_path_is_read_only_and_fail_soft():
    svc_module.clear_cache()
    client = _analytics_client()
    client.raise_on("crm.lead", stage="execute")
    with pytest.raises(CommercialDataError):
        CommercialService(client).get_analytics("todos")
    svc_module.clear_cache()
    unconfigured = FakeOdooClient(stages=STAGES, tags=TAGS, leads=[], configured=False)
    with pytest.raises(CommercialConfigurationError):
        CommercialService(unconfigured).get_analytics("todos")
    svc_module.clear_cache()


# ============================================================================
# Active Opportunities Phase 2 / C1 — Detalle de Oportunidades Activas
# (`get_opportunities`, `GET /comercial/data/opportunities`). Universo: MISMO
# que el KPI "Oportunidades activas" (stages 1-8) — NUNCA Pipeline Total
# (0-8, ver `_build_forecast`, C2). weighted_hours se centraliza en el
# service; tag_ids sólo particiona en memoria, nunca viaja en la fila.
# ============================================================================

OPP_STAGES = [
    {"id": 1, "name": "0 Prospección"},
    {"id": 2, "name": "1 Contacto"},
    {"id": 3, "name": "5 Negociación"},
]
OPP_TAGS = [
    {"id": 10, "name": "New Names"},
    {"id": 11, "name": "Base Instalada"},
]


def _prop_horas(value):
    return {"string": "Horas", "value": str(value)}


def _prop_date(label, iso):
    return {"string": label, "type": "date", "value": iso}


def _prop_selection(label, value, options):
    return {"string": label, "value": value, "selection": options}


OPP_LEADS = [
    # Etapa 1 (activa 1-8) — el caso "completo": todas las properties.
    {"id": 700, "name": "Opp Uno", "type": "opportunity", "active": True, "stage_id": 2,
     "tag_ids": [10], "partner_id": [80, "Cliente Uno"], "user_id": [50, "Vendedor Uno"],
     "source_id": [60, "Google Ads"], "probability": 40, "priority": "2", "expected_revenue": 25000,
     "lead_properties": [
         _prop_horas(100),
         _prop_date("Fecha de Ultima Respuesta", "2026-08-01"),
         _prop_selection("Licencias", "si", [["si", "Si"], ["no", "No"]]),
     ]},
    # Etapa 5 (activa 1-8) — sin source_id, sin properties (todo lo opcional
    # en su default honesto: "Sin definir"/"Sin definir"/None/None).
    {"id": 701, "name": "Opp Dos", "type": "opportunity", "active": True, "stage_id": 3,
     "tag_ids": [11], "partner_id": [81, "Cliente Dos"], "user_id": [51, "Vendedor Dos"],
     "source_id": False, "probability": 25, "priority": "0", "lead_properties": []},
    # Etapa 0 (Prospecto) — debe quedar AFUERA del universo 1-8.
    {"id": 702, "name": "Prospecto Cero", "type": "opportunity", "active": True, "stage_id": 1,
     "tag_ids": [10, 11], "partner_id": False, "user_id": False, "source_id": False,
     "probability": 10, "priority": "0", "lead_properties": []},
]

OPP_ACTIVITIES = [
    # Opp Uno tiene DOS actividades pendientes — el legacy retiene ambas,
    # nunca sólo "la próxima" (ver HANDOFF).
    {"id": 1, "res_id": 700, "res_model": "crm.lead", "date_deadline": "2026-08-10",
     "activity_type_id": [5, "Llamada"], "summary": "Seguimiento"},
    {"id": 2, "res_id": 700, "res_model": "crm.lead", "date_deadline": "2026-08-05",
     "activity_type_id": [6, "Reunión"], "summary": "Presentar propuesta"},
]


def _opportunities_client(**kwargs):
    return FakeOdooClient(stages=OPP_STAGES, tags=OPP_TAGS, leads=OPP_LEADS, activities=OPP_ACTIVITIES, **kwargs)


def test_opportunities_universe_is_stages_1_8_never_prospect_or_pipeline_total():
    svc_module.clear_cache()
    rows = CommercialService(_opportunities_client()).get_opportunities("todos")["opportunities"]
    names = {r["opportunity"] for r in rows}
    assert names == {"Opp Uno", "Opp Dos"}, "Prospecto Cero (etapa 0) queda afuera"
    svc_module.clear_cache()


def test_opportunities_row_never_exposes_tag_ids_or_etiquetas():
    svc_module.clear_cache()
    rows = CommercialService(_opportunities_client()).get_opportunities("todos")["opportunities"]
    for row in rows:
        assert "tag_ids" not in row and "etiquetas" not in row and "tags" not in row
    svc_module.clear_cache()


def test_opportunities_weighted_hours_is_centralized_in_service():
    svc_module.clear_cache()
    rows = CommercialService(_opportunities_client()).get_opportunities("todos")["opportunities"]
    uno = next(r for r in rows if r["opportunity"] == "Opp Uno")
    assert uno["hours"] == 100
    assert uno["weighted_hours"] == 40.0, "probability 40% * 100hs"
    svc_module.clear_cache()


def test_opportunities_row_includes_additive_revenue_field_without_a_new_odoo_call():
    # C5e (ver HANDOFF — filtro Etapa/Vendedor del detalle de Oportunidades,
    # owner decisión "preservar Revenue en el Funnel filtrado"): `revenue`
    # se agrega como campo ADITIVO al MISMO `search_read` de `crm.lead` que
    # ya existía — `expected_revenue` es el campo estándar que el resto del
    # servicio ya usa para Revenue (`_build_pipeline`/`_aggregate_full`).
    # Este test es la evidencia de "no nueva llamada a Odoo": cuenta las
    # queries a `crm.lead` (deben seguir siendo UNA sola) y confirma que esa
    # única query YA pide `expected_revenue`.
    svc_module.clear_cache()
    client = _opportunities_client()
    rows = CommercialService(client).get_opportunities("todos")["opportunities"]
    lead_calls = [c for c in client.calls if c[0] == "crm.lead"]
    assert len(lead_calls) == 1, "una sola query a crm.lead — revenue no agrega una segunda"
    assert "expected_revenue" in lead_calls[0][2], "el campo se pide en la MISMA query existente"
    uno = next(r for r in rows if r["opportunity"] == "Opp Uno")
    dos = next(r for r in rows if r["opportunity"] == "Opp Dos")
    assert uno["revenue"] == 25000.0
    assert dos["revenue"] == 0.0, "sin expected_revenue en el lead fuente, revenue cae a 0 (no rompe)"
    svc_module.clear_cache()


def test_opportunities_source_defaults_to_sin_definir_when_missing():
    svc_module.clear_cache()
    rows = CommercialService(_opportunities_client()).get_opportunities("todos")["opportunities"]
    assert next(r for r in rows if r["opportunity"] == "Opp Uno")["source"] == "Google Ads"
    assert next(r for r in rows if r["opportunity"] == "Opp Dos")["source"] == "Sin definir"
    svc_module.clear_cache()


def test_opportunities_licencias_resolved_from_selection_property():
    svc_module.clear_cache()
    rows = CommercialService(_opportunities_client()).get_opportunities("todos")["opportunities"]
    assert next(r for r in rows if r["opportunity"] == "Opp Uno")["licencias"] == "Si"
    assert next(r for r in rows if r["opportunity"] == "Opp Dos")["licencias"] == "Sin definir"
    svc_module.clear_cache()


def test_opportunities_last_response_and_days_elapsed_from_date_property():
    svc_module.clear_cache()
    rows = CommercialService(_opportunities_client()).get_opportunities("todos")["opportunities"]
    uno = next(r for r in rows if r["opportunity"] == "Opp Uno")
    dos = next(r for r in rows if r["opportunity"] == "Opp Dos")
    assert uno["last_response"] == "2026-08-01"
    assert uno["days_elapsed"] == (date.today() - date.fromisoformat("2026-08-01")).days
    assert dos["last_response"] is None and dos["days_elapsed"] is None
    svc_module.clear_cache()


def test_opportunities_activities_are_grouped_per_opportunity_all_not_just_next():
    svc_module.clear_cache()
    rows = CommercialService(_opportunities_client()).get_opportunities("todos")["opportunities"]
    uno = next(r for r in rows if r["opportunity"] == "Opp Uno")
    dos = next(r for r in rows if r["opportunity"] == "Opp Dos")
    assert len(uno["activities"]) == 2, "AMBAS actividades pendientes, no sólo la próxima"
    assert {a["deadline"] for a in uno["activities"]} == {"2026-08-10", "2026-08-05"}
    assert dos["activities"] == []
    svc_module.clear_cache()


def test_opportunities_partitions_by_nn_bi_tag_never_double_query():
    svc_module.clear_cache()
    client = _opportunities_client()
    service = CommercialService(client)
    todos = {r["opportunity"] for r in service.get_opportunities("todos")["opportunities"]}
    nn = {r["opportunity"] for r in service.get_opportunities("nn")["opportunities"]}
    bi = {r["opportunity"] for r in service.get_opportunities("bi")["opportunities"]}
    assert todos == {"Opp Uno", "Opp Dos"}
    assert nn == {"Opp Uno"}
    assert bi == {"Opp Dos"}
    assert len([c for c in client.calls if c[0] == "crm.lead"]) == 1, "una sola consulta a crm.lead"
    svc_module.clear_cache()


def test_opportunities_use_the_module_cache():
    svc_module.clear_cache()
    client = _opportunities_client()
    service = CommercialService(client)
    service.get_opportunities("todos")
    service.get_opportunities("nn")
    assert len([c for c in client.calls if c[0] == "mail.activity"]) == 1, "la segunda vista sale del cache"
    svc_module.clear_cache()


def test_opportunities_invalid_tipo_falls_back_to_todos():
    svc_module.clear_cache()
    rows = CommercialService(_opportunities_client()).get_opportunities("not-a-real-tipo")["opportunities"]
    assert {r["opportunity"] for r in rows} == {"Opp Uno", "Opp Dos"}
    svc_module.clear_cache()


def test_opportunities_path_is_fail_soft_on_transport_and_config():
    svc_module.clear_cache()
    client = _opportunities_client()
    client.raise_on("crm.lead", stage="execute")
    with pytest.raises(CommercialDataError) as excinfo:
        CommercialService(client).get_opportunities("todos")
    assert excinfo.value.stage == "execute"
    svc_module.clear_cache()
    unconfigured = _opportunities_client(configured=False)
    with pytest.raises(CommercialConfigurationError):
        CommercialService(unconfigured).get_opportunities("todos")
    svc_module.clear_cache()


# ============================================================================
# Active Opportunities Phase 2 / C2 — Forecast por mes (`get_forecast`,
# `GET /comercial/data/forecast`). Universo: stages 0-8 (Pipeline Total/
# Ponderado) — NUNCA 1-8 (ese es Oportunidades Activas, C1). Semántica
# legacy INTENCIONAL preservada: NO es el mismo drilldown.
# ============================================================================

FORECAST_STAGES = [
    {"id": 1, "name": "0 Prospección"},   # incluida en 0-8 (a diferencia de C1)
    {"id": 2, "name": "1 Contacto"},
    {"id": 3, "name": "5 Negociación"},
]
FORECAST_TAGS = [
    {"id": 10, "name": "New Names"},
    {"id": 11, "name": "Base Instalada"},
]

FORECAST_LEADS = [
    # "Cierre Ajustado" tiene prioridad sobre date_deadline.
    {"id": 800, "name": "Forecast Uno", "type": "opportunity", "active": True, "stage_id": 2,
     "tag_ids": [10], "partner_id": [90, "Cliente A"], "user_id": [55, "Vendedor A"],
     "probability": 50, "expected_revenue": 10000, "date_deadline": "2026-09-15",
     "lead_properties": [_prop_horas(80), _prop_date("Cierre Ajustado", "2026-10-01")]},
    # Sin "Cierre Ajustado" -> cae a date_deadline, MISMO mes que Forecast Uno
    # si coincidiera, pero acá es distinto a propósito para separar buckets.
    {"id": 801, "name": "Forecast Dos", "type": "opportunity", "active": True, "stage_id": 3,
     "tag_ids": [11], "partner_id": [91, "Cliente B"], "user_id": [56, "Vendedor B"],
     "probability": 20, "expected_revenue": 5000, "date_deadline": "2026-09-20",
     "lead_properties": [_prop_horas(40)]},
    # Etapa 0 (Prospecto) — DEBE estar incluida en Forecast (0-8).
    {"id": 802, "name": "Forecast Prospecto", "type": "opportunity", "active": True, "stage_id": 1,
     "tag_ids": [10], "partner_id": [92, "Cliente C"], "user_id": [57, "Vendedor C"],
     "probability": 10, "expected_revenue": 2000, "date_deadline": "2026-09-01",
     "lead_properties": [_prop_horas(20)]},
    # Sin ninguna fecha -> bucket "Sin fecha".
    {"id": 803, "name": "Forecast Sin Fecha", "type": "opportunity", "active": True, "stage_id": 2,
     "tag_ids": [], "partner_id": False, "user_id": False,
     "probability": 30, "expected_revenue": 1000, "date_deadline": False,
     "lead_properties": [_prop_horas(10)]},
]


def _forecast_client(**kwargs):
    return FakeOdooClient(stages=FORECAST_STAGES, tags=FORECAST_TAGS, leads=FORECAST_LEADS, **kwargs)


def test_forecast_universe_includes_prospect_stage_0():
    svc_module.clear_cache()
    months = CommercialService(_forecast_client()).get_forecast("todos")["months"]
    names = {o["opportunity"] for m in months for o in m["opportunities"]}
    assert "Forecast Prospecto" in names, "Forecast es 0-8 — Prospecto SIEMPRE incluido"
    svc_module.clear_cache()


def test_forecast_prefers_cierre_ajustado_over_date_deadline():
    svc_module.clear_cache()
    months = CommercialService(_forecast_client()).get_forecast("todos")["months"]
    uno = next(o for m in months for o in m["opportunities"] if o["opportunity"] == "Forecast Uno")
    assert uno["deadline"] == "2026-10-01", "Cierre Ajustado gana sobre date_deadline (2026-09-15)"
    october = next(m for m in months if m["month"] == "2026-10")
    assert october["count"] == 1
    svc_module.clear_cache()


def test_forecast_falls_back_to_date_deadline_without_cierre_ajustado():
    svc_module.clear_cache()
    months = CommercialService(_forecast_client()).get_forecast("todos")["months"]
    dos = next(o for m in months for o in m["opportunities"] if o["opportunity"] == "Forecast Dos")
    assert dos["deadline"] == "2026-09-20"
    svc_module.clear_cache()


def test_forecast_undated_bucket_is_named_sin_fecha_and_sorted_last():
    svc_module.clear_cache()
    months = CommercialService(_forecast_client()).get_forecast("todos")["months"]
    assert months[-1]["month"] == "Sin fecha"
    assert months[-1]["count"] == 1
    assert [m["month"] for m in months[:-1]] == sorted(m["month"] for m in months[:-1])
    svc_module.clear_cache()


def test_forecast_bucket_aggregates_match_hand_computed_values():
    svc_module.clear_cache()
    months = CommercialService(_forecast_client()).get_forecast("todos")["months"]
    september = next(m for m in months if m["month"] == "2026-09")
    # Forecast Dos (40hs, prob 20%) + Forecast Prospecto (20hs, prob 10%).
    assert september["count"] == 2
    assert september["hours"] == 60
    assert september["weighted_hours"] == round(40 * 0.20 + 20 * 0.10, 1)
    assert september["revenue"] == 5000 + 2000
    svc_module.clear_cache()


def test_forecast_month_bucket_embeds_its_own_opportunities_no_drill_endpoint():
    svc_module.clear_cache()
    months = CommercialService(_forecast_client()).get_forecast("todos")["months"]
    september = next(m for m in months if m["month"] == "2026-09")
    assert {o["opportunity"] for o in september["opportunities"]} == {"Forecast Dos", "Forecast Prospecto"}
    for o in september["opportunities"]:
        assert set(o) >= {"opportunity", "client", "seller", "stage", "deadline", "revenue", "hours", "probability"}
    svc_module.clear_cache()


def test_forecast_partitions_by_nn_bi_tag():
    svc_module.clear_cache()
    service = CommercialService(_forecast_client())
    nn_names = {o["opportunity"] for m in service.get_forecast("nn")["months"] for o in m["opportunities"]}
    bi_names = {o["opportunity"] for m in service.get_forecast("bi")["months"] for o in m["opportunities"]}
    assert nn_names == {"Forecast Uno", "Forecast Prospecto"}
    assert bi_names == {"Forecast Dos"}
    svc_module.clear_cache()


def test_forecast_uses_the_module_cache():
    svc_module.clear_cache()
    client = _forecast_client()
    service = CommercialService(client)
    service.get_forecast("todos")
    service.get_forecast("bi")
    assert len([c for c in client.calls if c[0] == "crm.lead"]) == 1, "la segunda vista sale del cache"
    svc_module.clear_cache()


def test_forecast_invalid_tipo_falls_back_to_todos():
    svc_module.clear_cache()
    months = CommercialService(_forecast_client()).get_forecast("not-a-real-tipo")["months"]
    assert sum(m["count"] for m in months) == 4
    svc_module.clear_cache()


def test_forecast_path_is_fail_soft_on_transport_and_config():
    svc_module.clear_cache()
    client = _forecast_client()
    client.raise_on("crm.lead", stage="execute")
    with pytest.raises(CommercialDataError) as excinfo:
        CommercialService(client).get_forecast("todos")
    assert excinfo.value.stage == "execute"
    svc_module.clear_cache()
    unconfigured = _forecast_client(configured=False)
    with pytest.raises(CommercialConfigurationError):
        CommercialService(unconfigured).get_forecast("todos")
    svc_module.clear_cache()
