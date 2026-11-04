"""KPIs operativos de Solicitudes: criterios literales y no excluyentes, sobre el conjunto filtrado completo (antes de paginar), con el mismo
snapshot de la landing y el primitivo `.activity-kpi` de CCHH. Datos sintéticos."""
from copy import deepcopy
from urllib.error import HTTPError

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import solicitudes as route
from webapp.services import solicitudes_service as service
from webapp.services import solicitudes_snapshot as snapshot

REQUESTED, APPROVED, REJECTED = "Solicitado", "Aprobado para Arquitectura", "Rechazado, Aplica Operaciones"


def row(i, status, funnel, **extra):
    return {"id": str(i), "fields": {"Title": f"Solicitud {i:03}", service.STATUS: status, service.FUNNEL: funnel, **extra}}


@pytest.mark.parametrize("status,funnel,expected", [
    ("Pendiente", REQUESTED, {"pendientes": 1, "sin_movimiento": 1}),                  # superposición deliberada
    ("Pendiente", APPROVED, {"aprobadas": 1, "pendientes": 1}),
    ("En curso", APPROVED, {"aprobadas": 1, "en_curso": 1}),
    ("En curso", REJECTED, {"en_curso": 1}),                                           # rechazada pero En curso: cuenta literalmente
    ("Pendiente", REJECTED, {"pendientes": 1}),
    ("Planificado", REQUESTED, {}),
    ("Finalizado", APPROVED, {"aprobadas": 1}),                                        # sin restricción por Arquitectura
    (None, None, {}),
])
def test_each_criterion_is_literal_and_counts_overlap(status, funnel, expected):
    counts = service.kpi_counts([row(1, status, funnel)])
    assert counts == {"aprobadas": 0, "en_curso": 0, "pendientes": 0, "sin_movimiento": 0, **expected}


def test_sin_movimiento_is_only_the_conjunction_of_both_states():
    rows = [row(1, "Pendiente", REQUESTED, **{service.PLANNED: "2026-11-10T00:00:00Z", "Colaborador": "Alguien"}),   # fechas o responsable NO lo excluyen
            row(2, "Planificado", REQUESTED), row(3, "Pendiente", APPROVED)]
    assert service.kpi_counts(rows)["sin_movimiento"] == 1


@pytest.fixture
def landing(monkeypatch):
    rows = ([row(i, "Pendiente", REQUESTED, Prioridad="Alta") for i in range(1, 61)]          # 60 pendientes sin movimiento (más de una página de 50)
            + [row(i, "En curso", APPROVED, Prioridad="Baja") for i in range(61, 71)]       # 10 en curso y aprobadas
            + [row(i, "Finalizado", APPROVED, Prioridad="Baja") for i in range(71, 76)]     # 5 cerradas aprobadas
            + [row(76, "En curso", REJECTED, Prioridad="Alta")])                             # 1 cerrada por rechazo, En curso
    calls = []
    monkeypatch.setattr(route, "_user", lambda request: {"sub": "actor"})

    def read(*args, **kwargs):
        calls.append(kwargs)
        return deepcopy(rows)
    monkeypatch.setattr(snapshot, "read", read)
    client = TestClient(app)

    def post(**data):
        response = client.post("/operaciones/solicitudes/data", data={"graph_token": "fixture", **data})
        assert response.status_code == 200
        soup = BeautifulSoup(response.text, "html.parser")
        return soup, {k["data-kpi"]: k.select_one(".activity-kpi__value").get_text() for k in soup.select("[data-solicitudes-kpis] [data-kpi]")}
    return post, calls


def test_the_four_kpis_count_the_whole_filtered_set_before_paging_once_per_read(landing):
    post, calls = landing
    soup, kpis = post(page_vigentes="2")
    assert kpis == {"aprobadas": "15", "en_curso": "11", "pendientes": "60", "sin_movimiento": "60"}        # no solo la página visible ni solo Vigentes
    assert len(calls) == 1                                                                                   # una lectura del snapshot, no cuatro consultas
    grid = soup.select_one("[data-solicitudes-kpis]")
    assert "activity-kpi-grid" in grid["class"] and [k.select_one(".activity-kpi__label").get_text() for k in grid.select("[data-kpi]")] == ["Aprobadas", "En curso", "Pendientes", "Sin movimiento"]
    assert [c for k in grid.select("[data-kpi]") for c in k["class"] if c.startswith("activity-kpi--")] == ["activity-kpi--positive", "activity-kpi--info", "activity-kpi--attention", "activity-kpi--alert"]
    order = str(soup)
    assert order.index("solicitudes-filters") < order.index("data-solicitudes-kpis") < order.index('id="solicitudes-vigentes"') < order.index('id="solicitudes-cerradas"')


def test_kpis_follow_filters_and_search_and_show_zero_without_matches(landing):
    post, _ = landing
    _, kpis = post(Prioridad="Baja")
    assert kpis == {"aprobadas": "15", "en_curso": "10", "pendientes": "0", "sin_movimiento": "0"}
    _, kpis = post(q="Solicitud 076")
    assert kpis == {"aprobadas": "0", "en_curso": "1", "pendientes": "0", "sin_movimiento": "0"}
    _, kpis = post(q="no existe")
    assert kpis == {"aprobadas": "0", "en_curso": "0", "pendientes": "0", "sin_movimiento": "0"}               # cero confirmado: la lectura funcionó


def test_a_failed_read_never_shows_zero_as_a_confirmed_result(monkeypatch):
    monkeypatch.setattr(route, "_user", lambda request: {"sub": "actor"})

    def failure(*args, **kwargs):
        raise HTTPError("", 503, "Fixture", {}, None)
    monkeypatch.setattr(snapshot, "read", failure)
    response = TestClient(app).post("/operaciones/solicitudes/data", data={"graph_token": "fixture"})
    soup = BeautifulSoup(response.text, "html.parser")
    grid = soup.select_one("[data-solicitudes-kpis]")
    assert grid.has_attr("data-kpis-unavailable") and {v.get_text() for v in grid.select(".activity-kpi__value")} == {"—"}


def test_a_refreshed_snapshot_recomputes_the_kpis(landing, monkeypatch):
    post, calls = landing
    _, first = post()
    _, again = post(refresh="1")
    assert first == again and calls[-1].get("refresh") is True                                                # el reintento relee el snapshot y recalcula
