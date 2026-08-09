"""
test_staffing_repository.py — Contrato de StaffingRepository (Batch 2 de
aceleración post-Fase 2, con corrective pass). Determinístico, sin Graph
real: monkeypatchea `core.microsoft365.graph_get_paged_values` con fixtures
fabricadas.

⚠️ MAPEO DE CAMPOS DE FIXTURE DE TEST — NO SON NOMBRES REALES DE PRODUCCIÓN.
`_TEST_FIELD_MAPPING` abajo es un `StaffingFieldMapping` inventado para este
test únicamente (mismo estilo de nombrado que se usaba antes como default
de producción, ya removido en el corrective pass) — permite probar
end-to-end el repositorio con datos determinísticos, pero NO es evidencia
de los internal names reales del sitio Gestión. `StaffingFieldMapping.
from_config()` (producción) sigue vacío hasta que alguien lo confirme.
"""
from __future__ import annotations

import pytest

from webapp.services import staffing_repository as repo_module
from webapp.services.staffing_repository import StaffingFieldMapping, StaffingRepository

_SITE_ID = "test-site-id"
_ASIG_LIST = "asig-list-id"
_PROY_LIST = "proy-list-id"
_COLAB_LIST = "colab-list-id"

# Fixture de test — NO validado contra el sitio Gestión real (ver docstring).
_TEST_FIELD_MAPPING = StaffingFieldMapping(
    colaborador_lookup_id="ColaboradorLookupId",
    proyecto_lookup_id="ProyectoLookupId",
    allocation="Asignacion",
    status="Estado",
    start_date="FechaInicio",
    end_date="FechaFin",
    proyecto_title="Title",
    colaborador_title="Title",
)

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


def _make_repository(field_mapping=_TEST_FIELD_MAPPING):
    return StaffingRepository(
        site_id=_SITE_ID,
        asignaciones_list_id=_ASIG_LIST,
        proyectos_list_id=_PROY_LIST,
        colaboradores_list_id=_COLAB_LIST,
        field_mapping=field_mapping,
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
    assert resolved.allocation_unit == "hours_per_month"
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
        field_mapping=_TEST_FIELD_MAPPING,
    )
    # Construir el repositorio sin site_id NO rompe — recién falla al leer.
    with pytest.raises(RuntimeError, match="SHAREPOINT_GESTION_SITE_ID"):
        repository.get_allocations("tok")


_FIELD_CONFIG_NAMES = (
    "SHAREPOINT_ASIGNACIONES_FIELD_COLABORADOR_LOOKUP",
    "SHAREPOINT_ASIGNACIONES_FIELD_PROYECTO_LOOKUP",
    "SHAREPOINT_ASIGNACIONES_FIELD_ALLOCATION",
    "SHAREPOINT_ASIGNACIONES_FIELD_STATUS",
    "SHAREPOINT_ASIGNACIONES_FIELD_START_DATE",
    "SHAREPOINT_ASIGNACIONES_FIELD_END_DATE",
    "SHAREPOINT_PROYECTOS_FIELD_TITLE",
    "SHAREPOINT_COLABORADORES_FIELD_TITLE",
)


def _clear_field_mapping_config(monkeypatch):
    # Aísla el test del entorno local (p. ej. un .env real de Batch 3 con el
    # mapeo ya validado) — sin esto, from_config() dejaría de estar vacío
    # apenas alguien configure Staffing localmente, y este test perdería su
    # sentido (o peor: un repositorio "sin mapeo" pasaría a llamar a Graph
    # real con esos valores).
    import config as config_module
    for name in _FIELD_CONFIG_NAMES:
        monkeypatch.setattr(config_module, name, "")


def test_missing_field_mapping_fails_clearly_listing_what_is_missing(monkeypatch):
    _clear_field_mapping_config(monkeypatch)
    # Sin field_mapping explícito -> StaffingFieldMapping.from_config(),
    # forzado vacío arriba (aislado del entorno local real).
    repository = StaffingRepository(
        site_id=_SITE_ID,
        asignaciones_list_id=_ASIG_LIST,
        proyectos_list_id=_PROY_LIST,
        colaboradores_list_id=_COLAB_LIST,
    )
    with pytest.raises(RuntimeError) as exc_info:
        repository.get_allocations("tok")
    message = str(exc_info.value)
    for missing_key in ("colaborador_lookup_id", "proyecto_lookup_id", "allocation", "proyecto_title"):
        assert missing_key in message


def test_production_config_does_not_silently_synthesize_field_names(monkeypatch):
    _clear_field_mapping_config(monkeypatch)
    # StaffingFieldMapping.from_config() NO debe inventar ningún nombre —
    # sin las env vars de producción configuradas, todo queda vacío.
    mapping = repo_module.StaffingFieldMapping.from_config()
    assert mapping.missing_fields() == [
        "colaborador_lookup_id",
        "proyecto_lookup_id",
        "allocation",
        "status",
        "start_date",
        "end_date",
        "proyecto_title",
        "colaborador_title",
    ]


# --- Enriquecimiento OPCIONAL FT/PT (Asignaciones Dashboard v1) ------------
# `colaborador_disponibilidad` NUNCA debe poder romper la carga de
# Staffing — a diferencia de los 8 campos de arriba, su ausencia degrada a
# None, nunca a RuntimeError.

def test_optional_availability_field_is_never_a_missing_required_field():
    # Sin configurar (default "") — no debe aparecer en missing_fields(),
    # a diferencia de los 8 campos requeridos.
    mapping = StaffingFieldMapping(
        colaborador_lookup_id="ColaboradorLookupId",
        proyecto_lookup_id="ProyectoLookupId",
        allocation="Asignacion",
        status="Estado",
        start_date="FechaInicio",
        end_date="FechaFin",
        proyecto_title="Title",
        colaborador_title="Title",
        # colaborador_disponibilidad deliberadamente omitido (default "").
    )
    assert mapping.missing_fields() == []


def test_availability_resolves_when_mapping_configured_and_source_has_value(monkeypatch):
    colaboradores = [
        {"id": "1", "fields": {"Title": "Ana Pérez", "Disponibilidad": "Full Time"}},
        {"id": "2", "fields": {"Title": "Beto Ruiz", "Disponibilidad": "Part Time"}},
    ]

    def fake(url, access_token, **kwargs):
        if _ASIG_LIST in url:
            return _ASIGNACIONES
        if _PROY_LIST in url:
            return _PROYECTOS
        if _COLAB_LIST in url:
            return colaboradores
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)

    mapping_with_availability = StaffingFieldMapping(
        **{**_TEST_FIELD_MAPPING.__dict__, "colaborador_disponibilidad": "Disponibilidad"}
    )
    records = _make_repository(field_mapping=mapping_with_availability).get_allocations("tok")
    by_consultant = {r.consultant_id: r for r in records}

    assert by_consultant["1"].consultant_availability == "Full Time"
    assert by_consultant["2"].consultant_availability == "Part Time"


def test_availability_is_none_when_source_value_is_blank(monkeypatch):
    colaboradores = [{"id": "1", "fields": {"Title": "Ana Pérez", "Disponibilidad": ""}}]

    def fake(url, access_token, **kwargs):
        if _ASIG_LIST in url:
            return _ASIGNACIONES[:1]
        if _PROY_LIST in url:
            return _PROYECTOS
        if _COLAB_LIST in url:
            return colaboradores
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)

    mapping_with_availability = StaffingFieldMapping(
        **{**_TEST_FIELD_MAPPING.__dict__, "colaborador_disponibilidad": "Disponibilidad"}
    )
    records = _make_repository(field_mapping=mapping_with_availability).get_allocations("tok")
    assert records[0].consultant_availability is None


def test_availability_is_none_when_mapping_not_configured_and_rows_still_load(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([]))

    # _TEST_FIELD_MAPPING no incluye colaborador_disponibilidad (default "").
    records = _make_repository().get_allocations("tok")

    assert len(records) == 2  # las filas existentes siguen cargando sin romper
    assert all(r.consultant_availability is None for r in records)


def test_display_fields_are_mapping_driven_not_hardcoded_to_title(monkeypatch):
    # Si Proyectos/Colaboradores usaran una columna DISTINTA de "Title" para
    # el nombre, el repositorio debe seguir el mapeo — no asumir "Title".
    proyectos = [{"id": "10", "fields": {"NombreProyecto": "Proyecto Custom"}}]
    colaboradores = [{"id": "1", "fields": {"NombreColaborador": "Consultor Custom"}}]

    def fake(url, access_token, **kwargs):
        if _ASIG_LIST in url:
            return _ASIGNACIONES[:1]
        if _PROY_LIST in url:
            return proyectos
        if _COLAB_LIST in url:
            return colaboradores
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)

    custom_mapping = StaffingFieldMapping(
        colaborador_lookup_id="ColaboradorLookupId",
        proyecto_lookup_id="ProyectoLookupId",
        allocation="Asignacion",
        status="Estado",
        start_date="FechaInicio",
        end_date="FechaFin",
        proyecto_title="NombreProyecto",
        colaborador_title="NombreColaborador",
    )
    records = _make_repository(field_mapping=custom_mapping).get_allocations("tok")

    assert records[0].project_name == "Proyecto Custom"
    assert records[0].consultant_name == "Consultor Custom"
