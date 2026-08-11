"""
test_staffing_vacation_repository.py — Contrato de StaffingRepository para
Vacaciones (Staffing Vacation Timeline Overlay, ver HANDOFF). Determinístico,
sin Graph real: monkeypatchea `core.microsoft365.graph_get_paged_values` con
fixtures fabricadas — mismo criterio que test_staffing_repository.py.

Runtime field discovery (corrective): a diferencia de las 3 listas de
Asignaciones (internal names vía `.env`, sin default), Vacaciones resuelve
sus 6 internal names EN RUNTIME contra `/lists/{id}/columns` (ver
`resolve_vacation_field_mapping`) — ya no hay `VacationFieldMapping.
from_config()`. `_TEST_VACATION_MAPPING` sigue siendo un override de test
(constructor `vacation_field_mapping=...`) para las pruebas de FILTRADO de
negocio (Motivo/Estado/fechas) que no necesitan ejercitar el resolver de
schema — las pruebas del resolver en sí usan fixtures de columnas Graph
(`_LIVE_COLUMNS`) más abajo.
"""
from __future__ import annotations

import pytest

from webapp.services import staffing_repository as repo_module
from webapp.services.staffing_repository import (
    StaffingRepository,
    VacationFieldMapping,
    VacationPeriod,
)

_SITE_ID = "test-site-id"
_VACA_LIST = "vaca-list-id"
_COLAB_LIST = "colab-list-id"

_TEST_VACATION_MAPPING = VacationFieldMapping(
    apellido="Apellido", nombre="Nombre", motivo="Motivo",
    desde="Desde", hasta="Hasta", estado="Estado",
)


def _make_repository(vacation_field_mapping=_TEST_VACATION_MAPPING):
    return StaffingRepository(
        site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST,
        vacaciones_list_id=_VACA_LIST, vacation_field_mapping=vacation_field_mapping,
    )


# --- Runtime field discovery (corrective, ver HANDOFF) ---------------------
# resolve_vacation_field_mapping / _resolve_vacation_field_mapping_from_columns
# — reemplaza el mecanismo anterior de 6 env vars sin default: los internal
# names se descubren en cada request contra /columns con el mismo
# access_token delegado, cacheados con TTL solo en éxito.

def _column(display_name, technical_name):
    return {"displayName": display_name, "name": technical_name}


_LIVE_COLUMNS = [
    _column("Fecha de Solicitud", "FechadeSolicitud"),
    _column("CUIL", "CUIL_x0020_"),
    _column("Legajo", "Legajo"),
    # Encoded/internal names deliberadamente DISTINTOS del display label —
    # prueba que el resolver usa `name`, nunca el propio displayName ni una
    # convención inferida.
    _column("Apellido", "field_2"),
    _column("Nombre", "field_3"),
    _column("Código", "Codigo"),
    _column("Motivo", "MotivoDeAusencia"),
    _column("Desde", "FechaInicioVacaciones"),
    _column("Hasta", "FechaFinVacaciones"),
    _column("Días", "Dias"),
    _column("Estado", "Status_x0020_Aprobacion"),
    _column("Comentario del empleado", "CommentEmpleado"),
    _column("Comentario del aprobador", "CommentAprobador"),
    _column("Etiquetas del empleado", "EtiquetasEmpleado"),
]


def test_resolves_all_six_display_names_to_internal_names():
    from webapp.services.staffing_repository import _resolve_vacation_field_mapping_from_columns
    mapping = _resolve_vacation_field_mapping_from_columns(_LIVE_COLUMNS)
    assert mapping == VacationFieldMapping(
        apellido="field_2", nombre="field_3", motivo="MotivoDeAusencia",
        desde="FechaInicioVacaciones", hasta="FechaFinVacaciones", estado="Status_x0020_Aprobacion",
    )


def test_resolved_internal_names_may_differ_from_display_label():
    # Ya cubierto arriba (field_2 != "Apellido") — test dedicado explícito
    # para el ítem 2 del batch: nunca se asume displayName == name.
    from webapp.services.staffing_repository import _resolve_vacation_field_mapping_from_columns
    mapping = _resolve_vacation_field_mapping_from_columns(_LIVE_COLUMNS)
    assert mapping.apellido != "Apellido"
    assert mapping.motivo != "Motivo"


def test_column_order_is_irrelevant():
    from webapp.services.staffing_repository import _resolve_vacation_field_mapping_from_columns
    shuffled = list(reversed(_LIVE_COLUMNS))
    assert _resolve_vacation_field_mapping_from_columns(shuffled) == \
        _resolve_vacation_field_mapping_from_columns(_LIVE_COLUMNS)


def test_unrelated_columns_are_ignored():
    from webapp.services.staffing_repository import _resolve_vacation_field_mapping_from_columns
    mapping = _resolve_vacation_field_mapping_from_columns(_LIVE_COLUMNS)
    # CUIL/Legajo/comentarios/etiquetas no aparecen en el mapeo resuelto —
    # solo los 6 campos requeridos.
    assert set(mapping.__dataclass_fields__.keys()) == {
        "apellido", "nombre", "motivo", "desde", "hasta", "estado",
    }


@pytest.mark.parametrize("missing_display_name", ["Apellido", "Nombre", "Motivo", "Desde", "Hasta", "Estado"])
def test_missing_required_display_name_raises_schema_error(missing_display_name):
    from webapp.services.staffing_repository import (
        VacationSchemaError, _resolve_vacation_field_mapping_from_columns,
    )
    columns = [c for c in _LIVE_COLUMNS if c["displayName"] != missing_display_name]
    with pytest.raises(VacationSchemaError, match=missing_display_name):
        _resolve_vacation_field_mapping_from_columns(columns)


def test_duplicate_required_display_name_candidate_fails_safely():
    from webapp.services.staffing_repository import (
        VacationSchemaError, _resolve_vacation_field_mapping_from_columns,
    )
    columns = _LIVE_COLUMNS + [_column("Apellido", "SomeOtherInternalName")]
    with pytest.raises(VacationSchemaError, match="Apellido"):
        _resolve_vacation_field_mapping_from_columns(columns)


def test_no_internal_name_guessing_missing_and_ambiguous_never_pick_a_candidate():
    from webapp.services.staffing_repository import (
        VacationSchemaError, _resolve_vacation_field_mapping_from_columns,
    )
    # Ni "faltante" ni "ambiguo" devuelven jamás un VacationFieldMapping —
    # ambos casos levantan, nunca eligen un candidato al azar.
    with pytest.raises(VacationSchemaError):
        _resolve_vacation_field_mapping_from_columns([c for c in _LIVE_COLUMNS if c["displayName"] != "Nombre"])
    with pytest.raises(VacationSchemaError):
        _resolve_vacation_field_mapping_from_columns(_LIVE_COLUMNS + [_column("Nombre", "OtroNombre")])


def test_schema_error_is_not_a_runtime_error():
    # VacationSchemaError es un problema de RUNTIME/SOURCE-SCHEMA, nunca de
    # configuración de deployment — el router lo trata como cualquier otra
    # falla de Graph (graph_error), nunca como configuration_incomplete.
    from webapp.services.staffing_repository import VacationSchemaError
    assert not issubclass(VacationSchemaError, RuntimeError)


def test_schema_error_message_never_includes_the_access_token(monkeypatch):
    from webapp.services import staffing_repository as repo_module
    secret = "eyJ-super-secret-graph-token"

    def fake_columns(url, access_token, **kwargs):
        assert access_token == secret
        return [c for c in _LIVE_COLUMNS if c["displayName"] != "Nombre"]

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake_columns)
    repo = StaffingRepository(site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST, vacaciones_list_id=_VACA_LIST)
    with pytest.raises(Exception) as exc_info:
        repo.get_vacations(secret)
    assert secret not in str(exc_info.value)


# --- Integration flow: get_vacations resuelve schema luego consume items --

def test_get_vacations_resolves_metadata_then_fetches_items_with_resolved_names(monkeypatch):
    from webapp.services import staffing_repository as repo_module
    calls = []

    def fake(url, access_token, **kwargs):
        calls.append(url)
        if "columns" in url:
            return _LIVE_COLUMNS
        assert "field_2" in url and "field_3" in url and "MotivoDeAusencia" in url
        return [{
            "id": "1", "fields": {
                "field_2": "Gómez", "field_3": "Matías", "MotivoDeAusencia": "Vacaciones",
                "Status_x0020_Aprobacion": "Aprobada",
                "FechaInicioVacaciones": "2026-03-10", "FechaFinVacaciones": "2026-03-20",
            },
        }]

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)
    repo = StaffingRepository(site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST, vacaciones_list_id=_VACA_LIST)
    periods = repo.get_vacations("tok")
    assert len(calls) == 2 and "columns" in calls[0]
    assert periods == [VacationPeriod(
        source_id="1", consultant_name="Matías Gómez", start_date="2026-03-10", end_date="2026-03-20",
    )]


def test_successful_metadata_resolution_is_cached_and_reused(monkeypatch):
    from webapp.services import staffing_repository as repo_module
    columns_calls = []

    def fake(url, access_token, **kwargs):
        if "columns" in url:
            columns_calls.append(url)
            return _LIVE_COLUMNS
        return []

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)
    repo = StaffingRepository(site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST, vacaciones_list_id=_VACA_LIST)
    repo.get_vacations("same-tok")
    repo.get_vacations("same-tok")
    assert len(columns_calls) == 1  # segunda llamada reusa la cache


def test_failed_metadata_discovery_is_not_cached_as_success(monkeypatch):
    from webapp.services import staffing_repository as repo_module
    from webapp.services.staffing_repository import VacationSchemaError
    columns_calls = []

    def fake(url, access_token, **kwargs):
        assert "columns" in url
        columns_calls.append(url)
        return [c for c in _LIVE_COLUMNS if c["displayName"] != "Nombre"]

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)
    repo = StaffingRepository(site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST, vacaciones_list_id=_VACA_LIST)
    with pytest.raises(VacationSchemaError):
        repo.get_vacations("tok-fails")
    with pytest.raises(VacationSchemaError):
        repo.get_vacations("tok-fails")
    assert len(columns_calls) == 2  # NUNCA se cachea una falla


def test_columns_endpoint_401_remains_fail_soft_through_get_vacations(monkeypatch):
    import urllib.error
    from webapp.services import staffing_repository as repo_module

    def fake(url, access_token, **kwargs):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", None, None)

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)
    repo = StaffingRepository(site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST, vacaciones_list_id=_VACA_LIST)
    with pytest.raises(urllib.error.HTTPError):
        repo.get_vacations("tok")


def test_get_consultants_raises_explicit_when_colaborador_title_missing(monkeypatch):
    from webapp.services.staffing_repository import StaffingFieldMapping
    repo = StaffingRepository(
        site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST,
        field_mapping=StaffingFieldMapping(
            colaborador_lookup_id="x", proyecto_lookup_id="x", allocation="x", status="x",
            start_date="x", end_date="x", proyecto_title="x", colaborador_title="",
        ),
    )
    with pytest.raises(RuntimeError):
        repo.get_consultants("tok")


# --- get_vacations: filtro Motivo=Vacaciones + Estado=Aprobada -------------

_APPROVED = {
    "id": "1", "fields": {
        "Nombre": "Matías", "Apellido": "Gómez", "Motivo": "Vacaciones",
        "Estado": "Aprobada", "Desde": "2026-03-10", "Hasta": "2026-03-20",
    },
}
_CANCELLED = {
    "id": "2", "fields": {
        "Nombre": "Ana", "Apellido": "Pérez", "Motivo": "Vacaciones",
        "Estado": "Cancelada", "Desde": "2026-11-01", "Hasta": "2026-11-10",
    },
}
_REJECTED = {
    "id": "3", "fields": {
        "Nombre": "Beto", "Apellido": "Ruiz", "Motivo": "Vacaciones",
        "Estado": "Rechazada", "Desde": "2026-05-01", "Hasta": "2026-05-05",
    },
}
_OTHER_REASON = {
    "id": "4", "fields": {
        "Nombre": "Carla", "Apellido": "Diaz", "Motivo": "Enfermedad",
        "Estado": "Aprobada", "Desde": "2026-05-01", "Hasta": "2026-05-05",
    },
}
_MISSING_END_DATE = {
    "id": "5", "fields": {
        "Nombre": "Dario", "Apellido": "Suarez", "Motivo": "Vacaciones",
        "Estado": "Aprobada", "Desde": "2026-06-01", "Hasta": "",
    },
}
_MISSING_NAME = {
    "id": "6", "fields": {
        "Nombre": "", "Apellido": "", "Motivo": "Vacaciones",
        "Estado": "Aprobada", "Desde": "2026-06-01", "Hasta": "2026-06-05",
    },
}
_SINGLE_DAY = {
    "id": "7", "fields": {
        "Nombre": "Elena", "Apellido": "Torres", "Motivo": "Vacaciones",
        "Estado": "Aprobada", "Desde": "2026-06-10", "Hasta": "2026-06-10",
    },
}
_SECOND_PERIOD_SAME_CONSULTANT = {
    "id": "8", "fields": {
        "Nombre": "Matías", "Apellido": "Gómez", "Motivo": "Vacaciones",
        "Estado": "Aprobada", "Desde": "2026-01-05", "Hasta": "2026-01-10",
    },
}


def _fake_fetch(rows):
    def fake(url, access_token, **kwargs):
        assert _SITE_ID in url and _VACA_LIST in url
        return rows
    return fake


# test 1: aprobada+Vacaciones se incluye.
def test_approved_vacation_becomes_vacation_period(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_APPROVED]))
    periods = _make_repository().get_vacations("tok")
    assert len(periods) == 1
    assert periods[0] == VacationPeriod(
        source_id="1", consultant_name="Matías Gómez", start_date="2026-03-10", end_date="2026-03-20",
    )


# test 2: Cancelada excluida.
def test_cancelled_vacation_excluded(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_CANCELLED]))
    assert _make_repository().get_vacations("tok") == []


# test 3: Rechazada excluida.
def test_rejected_vacation_excluded(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_REJECTED]))
    assert _make_repository().get_vacations("tok") == []


# test 4: otro Motivo (no Vacaciones) excluido.
def test_non_vacation_reason_excluded(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_OTHER_REASON]))
    assert _make_repository().get_vacations("tok") == []


# test 9: fecha faltante -> fila excluida, fail-soft (no rompe el resto).
def test_missing_end_date_row_excluded_fail_soft(monkeypatch):
    monkeypatch.setattr(
        repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_MISSING_END_DATE, _APPROVED]),
    )
    periods = _make_repository().get_vacations("tok")
    assert [p.source_id for p in periods] == ["1"]


def test_missing_consultant_name_row_excluded(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_MISSING_NAME]))
    assert _make_repository().get_vacations("tok") == []


# test 6: vacaciones de un solo día son válidas.
def test_single_day_vacation_is_valid(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_SINGLE_DAY]))
    periods = _make_repository().get_vacations("tok")
    assert len(periods) == 1
    assert periods[0].start_date == periods[0].end_date == "2026-06-10"


# test 7: múltiples períodos para el MISMO consultor se preservan por
# separado — nunca se colapsan en un solo rango.
def test_multiple_periods_for_same_consultant_preserved_separately(monkeypatch):
    monkeypatch.setattr(
        repo_module.microsoft365, "graph_get_paged_values",
        _fake_fetch([_APPROVED, _SECOND_PERIOD_SAME_CONSULTANT]),
    )
    periods = _make_repository().get_vacations("tok")
    assert len(periods) == 2
    assert {p.source_id for p in periods} == {"1", "8"}
    assert all(p.consultant_name == "Matías Gómez" for p in periods)


# test 8: source item ID preservado.
def test_source_id_preserved_from_graph_item_id(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_APPROVED]))
    periods = _make_repository().get_vacations("tok")
    assert periods[0].source_id == "1"


def test_get_vacations_diagnostics_never_include_names_or_pii(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_APPROVED]))
    repo = _make_repository()
    repo.get_vacations("tok-secret")
    diag = repo.last_diagnostics["vacaciones"]
    assert set(diag.keys()) == {"rows", "pages", "elapsed_ms"}
    assert diag["rows"] == 1


# --- get_consultants ---------------------------------------------------------

def test_get_consultants_reads_only_colaboradores(monkeypatch):
    from webapp.services.staffing_repository import StaffingFieldMapping
    calls = []

    def fake(url, access_token, **kwargs):
        calls.append(url)
        return [{"id": "1", "fields": {"Title": "Ana Pérez"}}, {"id": "2", "fields": {"Title": "Beto Ruiz"}}]

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)
    repo = StaffingRepository(
        site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST,
        field_mapping=StaffingFieldMapping(
            colaborador_lookup_id="x", proyecto_lookup_id="x", allocation="x", status="x",
            start_date="x", end_date="x", proyecto_title="x", colaborador_title="Title",
        ),
    )
    result = repo.get_consultants("tok")
    assert result == [("1", "Ana Pérez"), ("2", "Beto Ruiz")]
    assert len(calls) == 1
    assert _COLAB_LIST in calls[0]


def test_get_consultants_excludes_rows_without_id_or_name(monkeypatch):
    from webapp.services.staffing_repository import StaffingFieldMapping

    def fake(url, access_token, **kwargs):
        return [{"id": "1", "fields": {"Title": ""}}, {"id": "", "fields": {"Title": "Nadie"}}]

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)
    repo = StaffingRepository(
        site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST,
        field_mapping=StaffingFieldMapping(
            colaborador_lookup_id="x", proyecto_lookup_id="x", allocation="x", status="x",
            start_date="x", end_date="x", proyecto_title="x", colaborador_title="Title",
        ),
    )
    assert repo.get_consultants("tok") == []
