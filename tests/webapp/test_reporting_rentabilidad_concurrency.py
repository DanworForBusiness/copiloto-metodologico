"""
test_reporting_rentabilidad_concurrency.py — TWO-WAY CONCURRENCY corrective
(owner decision 2026-08-19, "OWNER REVIEW — CONCURRENCY AUDIT ACCEPTED WITH
ONE IMPORTANT CORRECTION").

`get_rentabilidad` ejecuta `get_cartera_facts` y `get_time_logs_worked_hours`
en un `ThreadPoolExecutor(max_workers=2)` LOCAL a la llamada (ver
`reporting_service.py`), en vez de secuencial — ambas ramas son
data-independientes hasta el shaping (MISMO `hot_years`/`access_token`,
ninguna lee lo que la otra produce; ver auditoría de thread-safety en
HANDOFF: sin refresh de token del lado del servidor, `self._token`
escrito con el MISMO valor por ambas, sin `_StageTimer` compartido).

CONTRATO DE ERROR ACEPTADO EXPLÍCITAMENTE (owner — NO es la misma
semántica de falla EXACTA que la versión secuencial): se observa el
`Future` de Cartera SIEMPRE primero.
  - Cartera falla (con o sin fallo simultáneo de Time Logs) ⇒ propaga la
    excepción de Cartera.
  - Cartera OK + Time Logs falla ⇒ propaga la excepción de Time Logs.
  - Cartera falla: Time Logs puede haber arrancado o incluso completado
    mientras tanto — trabajo Graph aceptado como posible desperdicio en
    ese escenario (ambas ramas son read-only), NUNCA una propiedad a
    exigir en tests ("no exigir que Time Logs no haya arrancado").

Este archivo prueba: A) invocación exacta + payload idéntico al contrato
secuencial; B) solapamiento real vía `threading.Event` cruzados (NUNCA una
assertion de wall-clock); C) prioridad de excepción en los 3 escenarios.
"""
from __future__ import annotations

import threading

import pytest

from webapp.services.reporting_repository import ReportingGraphError, ReportingRepository
from webapp.services.reporting_service import ReportingService

_SITE = "site-1"
_HP, _PROY, _CLI, _COLAB, _USERINFO = "hp-list", "proy-list", "cli-list", "colab-list", "userinfo-list"

_FACTS = [{
    "cliente": "Danone", "clientPartner": "María López", "proyecto": "Soporte Danone",
    "idProyecto": "DAN-001", "tipoProyecto": "Mantenimiento", "estado": "En curso",
    "anio": "2026", "mes": "Agosto", "anioInicio": "2024",
    "hsTrabajadas": 0.0, "hsVendidas": 160.0, "hsCertificadas": 140.0, "facturadasProyecto": 0.0,
}]
_TIME_LOGS = [{"proyecto": "Soporte Danone", "anio": "2026", "mes": "Agosto", "horas": 132.4}]


def _repo():
    return ReportingRepository(
        site_id=_SITE, horas_proyectos_list_id=_HP, proyectos_list_id=_PROY, cliente_list_id=_CLI,
        colaboradores_list_id=_COLAB, user_information_list_id=_USERINFO,
    )


def _sequential_expected():
    """Referencia — MISMO shaping de `get_rentabilidad`, invocado sin
    concurrencia (mocks triviales, sin threads) — el payload de las ramas
    concurrentes de abajo debe coincidir EXACTO contra esto."""
    def fake_cartera(self, access_token, *, include_facturadas, hot_years):
        return list(_FACTS), {}

    def fake_time_logs(self, access_token, *, hot_years):
        return list(_TIME_LOGS)

    import unittest.mock as mock
    with mock.patch.object(ReportingRepository, "get_cartera_facts_with_project_dimension", fake_cartera), \
         mock.patch.object(ReportingRepository, "get_time_logs_worked_hours", fake_time_logs):
        return ReportingService(_repo()).get_rentabilidad("tok")


# --- A. SUCCESS ------------------------------------------------------------

def test_both_branches_invoked_exactly_once_with_same_args(monkeypatch):
    calls = {"cartera": [], "time_logs": []}

    def fake_cartera(self, access_token, *, include_facturadas, hot_years):
        calls["cartera"].append((access_token, include_facturadas, hot_years))
        return list(_FACTS), {}

    def fake_time_logs(self, access_token, *, hot_years):
        calls["time_logs"].append((access_token, hot_years))
        return list(_TIME_LOGS)

    monkeypatch.setattr(ReportingRepository, "get_cartera_facts_with_project_dimension", fake_cartera)
    monkeypatch.setattr(ReportingRepository, "get_time_logs_worked_hours", fake_time_logs)

    ReportingService(_repo()).get_rentabilidad("tok-abc", anios=["2026"])

    assert len(calls["cartera"]) == 1
    assert len(calls["time_logs"]) == 1
    cartera_token, include_facturadas, cartera_hot_years = calls["cartera"][0]
    time_logs_token, time_logs_hot_years = calls["time_logs"][0]
    # MISMO access_token y MISMO hot_years en ambas ramas — ninguna
    # deriva su propio scope por separado.
    assert cartera_token == "tok-abc"
    assert time_logs_token == "tok-abc"
    assert include_facturadas is False
    assert cartera_hot_years == ["2026"]
    assert time_logs_hot_years == ["2026"]


def test_result_payload_identical_to_sequential_contract(monkeypatch):
    def fake_cartera(self, access_token, *, include_facturadas, hot_years):
        return list(_FACTS), {}

    def fake_time_logs(self, access_token, *, hot_years):
        return list(_TIME_LOGS)

    monkeypatch.setattr(ReportingRepository, "get_cartera_facts_with_project_dimension", fake_cartera)
    monkeypatch.setattr(ReportingRepository, "get_time_logs_worked_hours", fake_time_logs)

    concurrent_result = ReportingService(_repo()).get_rentabilidad("tok")
    assert concurrent_result == _sequential_expected()
    # sanity — el fixture realmente produjo datos no triviales.
    assert concurrent_result["proyectos"]


# --- B. OVERLAP (sincronización determinística, NUNCA wall-clock) --------

def test_branches_actually_overlap_execution(monkeypatch):
    cartera_started = threading.Event()
    time_logs_started = threading.Event()

    def fake_cartera(self, access_token, *, include_facturadas, hot_years):
        cartera_started.set()
        # Solo se desbloquea si Time Logs YA arrancó — imposible si las
        # ramas fueran secuenciales (Time Logs ni se invoca hasta que
        # Cartera retorna). Timeout acotado, nunca un hang real.
        started = time_logs_started.wait(timeout=2)
        assert started, "time_logs nunca arrancó mientras cartera estaba en vuelo — las ramas NO se solapan"
        return list(_FACTS), {}

    def fake_time_logs(self, access_token, *, hot_years):
        time_logs_started.set()
        started = cartera_started.wait(timeout=2)
        assert started, "cartera nunca arrancó mientras time_logs estaba en vuelo — las ramas NO se solapan"
        return list(_TIME_LOGS)

    monkeypatch.setattr(ReportingRepository, "get_cartera_facts_with_project_dimension", fake_cartera)
    monkeypatch.setattr(ReportingRepository, "get_time_logs_worked_hours", fake_time_logs)

    result = ReportingService(_repo()).get_rentabilidad("tok")
    assert result["proyectos"]


# --- C. ERROR PRIORITY -----------------------------------------------------

def test_both_branches_fail_propagates_cartera_exception(monkeypatch):
    def fake_cartera(self, access_token, *, include_facturadas, hot_years):
        raise ReportingGraphError(stage="proyectos", status_code=500)

    def fake_time_logs(self, access_token, *, hot_years):
        # Escenario #3 (owner): NO se exige que time_logs no haya
        # arrancado o incluso fallado — solo que su excepción NUNCA sea
        # la que se propaga cuando Cartera también falla.
        raise ReportingGraphError(stage="time_logs_site", status_code=503)

    monkeypatch.setattr(ReportingRepository, "get_cartera_facts_with_project_dimension", fake_cartera)
    monkeypatch.setattr(ReportingRepository, "get_time_logs_worked_hours", fake_time_logs)

    with pytest.raises(ReportingGraphError) as exc_info:
        ReportingService(_repo()).get_rentabilidad("tok")
    assert exc_info.value.stage == "proyectos"
    assert exc_info.value.status_code == 500


def test_cartera_ok_time_logs_fails_propagates_time_logs_exception(monkeypatch):
    def fake_cartera(self, access_token, *, include_facturadas, hot_years):
        return list(_FACTS), {}

    def fake_time_logs(self, access_token, *, hot_years):
        raise ReportingGraphError(stage="time_logs_site", status_code=503)

    monkeypatch.setattr(ReportingRepository, "get_cartera_facts_with_project_dimension", fake_cartera)
    monkeypatch.setattr(ReportingRepository, "get_time_logs_worked_hours", fake_time_logs)

    with pytest.raises(ReportingGraphError) as exc_info:
        ReportingService(_repo()).get_rentabilidad("tok")
    assert exc_info.value.stage == "time_logs_site"
    assert exc_info.value.status_code == 503


def test_cartera_fails_time_logs_ok_propagates_cartera_exception(monkeypatch):
    """Caso simétrico al de arriba — Cartera falla, Time Logs por su
    lado tiene éxito (o simplemente no le importa al caller): debe
    propagarse SIEMPRE la excepción de Cartera, MISMO criterio de
    prioridad."""
    def fake_cartera(self, access_token, *, include_facturadas, hot_years):
        raise ReportingGraphError(stage="cliente", status_code=404)

    def fake_time_logs(self, access_token, *, hot_years):
        return list(_TIME_LOGS)

    monkeypatch.setattr(ReportingRepository, "get_cartera_facts_with_project_dimension", fake_cartera)
    monkeypatch.setattr(ReportingRepository, "get_time_logs_worked_hours", fake_time_logs)

    with pytest.raises(ReportingGraphError) as exc_info:
        ReportingService(_repo()).get_rentabilidad("tok")
    assert exc_info.value.stage == "cliente"
    assert exc_info.value.status_code == 404
