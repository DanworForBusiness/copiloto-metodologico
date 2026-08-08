"""
test_staffing_repository.py — Contrato de StaffingRepository (Batch 2 de
aceleración post-Fase 2). Determinístico, sin Graph real: monkeypatchea
`core.microsoft365.graph_get_paged_values` con fixtures fabricadas.

Mapeo canónico de test: usa EXACTAMENTE los mismos internal names que
`staffing_repository._ASIGNACION_FIELDS`/`_TITLE_FIELD` (ColaboradorLookupId,
ProyectoLookupId, Asignacion, Estado, FechaInicio, FechaFin, Title) — esos
nombres NO están confirmados contra el sitio Gestión real (ver docstring del
módulo); acá se documentan como el mapeo asumido para que el repositorio
sea probable end-to-end sobre datos determinísticos.
"""
from __future__ import annotations

import pytest

from webapp.services import staffing_repository as repo_module
from webapp.services.staffing_repository import StaffingRepository

_SITE_ID = "test-site-id"
_ASIG_LIST = "asig-list-id"
_PROY_LIST = "proy-list-id"
_COLAB_LIST = "colab-list-id"

_ASIGNACIONES = [
    {
        "id": "101",
        "fields": {
            "ColaboradorLookupId": 1,
            "ProyectoLookupId": 10,
            "Asignacion": "50",
            "Estado": "Active",
            "FechaInicio": "2026-01-01",
            "FechaFin": "2026-06-30",
        },
    },
    {
        "id": "102",
        "fields": {
            "ColaboradorLookupId": 2,
            "ProyectoLookupId": 999,  # no existe en _PROYECTOS -> unresolved
            "Asignacion": "100",
            "Estado": "Tentative",
            "FechaInicio": "2026-02-01",
            "FechaFin": "",
        },
    },
]
_PROYECTOS = [{"id": "10", "fields": {"Title": "Proyecto Alfa"}}]
_COLABORADORES = [
    {"id": "1", "fields": {"Title": "Ana Pérez"}},
    {"id": "2", "fields": {"Title": "Beto Ruiz"}},
]


def _make_repository():
    return StaffingRepository(
        site_id=_SITE_ID,
        asignaciones_list_id=_ASIG_LIST,
        proyectos_list_id=_PROY_LIST,
        colaboradores_list_id=_COLAB_LIST,
    )


def _fake_fetch(calls, *, fail_on=None):
    def fake(url, access_token, **kwargs):
        calls.append((url, access_token))
        if fail_on and fail_on in url:
            raise RuntimeError("graph boom")
        if _ASIG_LIST in url:
            return _ASIGNACIONES
        if _PROY_LIST in url:
            return _PROYECTOS
        if _COLAB_LIST in url:
            return _COLABORADORES
        raise AssertionError(f"unexpected url {url}")

    return fake


def test_requests_the_three_expected_source_urls(monkeypatch):
    calls = []
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch(calls))

    _make_repository().get_allocations("tok-abc")

    urls = [url for url, _token in calls]
    assert any(_ASIG_LIST in u and _SITE_ID in u and "/items?$expand=fields" in u for u in urls)
    assert any(_PROY_LIST in u and _SITE_ID in u for u in urls)
    assert any(_COLAB_LIST in u and _SITE_ID in u for u in urls)
    assert len(urls) == 3


def test_access_token_passed_explicitly_to_every_call(monkeypatch):
    calls = []
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch(calls))

    _make_repository().get_allocations("tok-xyz")

    assert all(token == "tok-xyz" for _url, token in calls)


def test_fields_normalize_and_joins_resolve_correctly(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([]))

    records = _make_repository().get_allocations("tok")
    by_id = {r.assignment_id: r for r in records}

    resolved = by_id["101"]
    assert resolved.consultant_id == "1"
    assert resolved.consultant_name == "Ana Pérez"
    assert resolved.project_id == "10"
    assert resolved.project_name == "Proyecto Alfa"
    assert resolved.allocation_value == 50.0
    assert resolved.allocation_unit == "unknown"
    assert resolved.status == "Active"
    assert resolved.start_date == "2026-01-01"
    assert resolved.end_date == "2026-06-30"


def test_unresolved_lookup_degrades_without_dropping_the_row(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([]))

    records = _make_repository().get_allocations("tok")
    by_id = {r.assignment_id: r for r in records}

    unresolved = by_id["102"]
    # La fila SIGUE presente (no se descarta el resultado completo); el
    # lookup crudo se conserva, el nombre resuelto queda en None.
    assert unresolved.project_id == "999"
    assert unresolved.project_name is None
    assert unresolved.consultant_name == "Beto Ruiz"
    assert len(records) == 2


def test_graph_transport_errors_are_not_silently_swallowed(monkeypatch):
    calls = []
    monkeypatch.setattr(
        repo_module.microsoft365, "graph_get_paged_values", _fake_fetch(calls, fail_on=_PROY_LIST)
    )

    with pytest.raises(RuntimeError, match="graph boom"):
        _make_repository().get_allocations("tok")


def test_missing_site_id_fails_clearly_only_when_actually_used():
    repository = StaffingRepository(
        site_id="",
        asignaciones_list_id=_ASIG_LIST,
        proyectos_list_id=_PROY_LIST,
        colaboradores_list_id=_COLAB_LIST,
    )
    # Construir el repositorio sin site_id NO rompe — recién falla al leer.
    with pytest.raises(RuntimeError, match="SHAREPOINT_GESTION_SITE_ID"):
        repository.get_allocations("tok")
