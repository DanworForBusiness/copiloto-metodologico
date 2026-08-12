"""
test_staffing_vacation_repository.py — Contrato FINAL de StaffingRepository
para Vacaciones (Staffing Vacation Timeline Overlay, ver HANDOFF).
Determinístico, sin Graph real: monkeypatchea
`core.microsoft365.graph_get_paged_values` con fixtures fabricadas — mismo
criterio que test_staffing_repository.py.

Contrato FINAL (owner live smoke #4, autoritativo): 5 columnas semánticas
reales — Colaborador (texto simple, NO lookup), Fecha de inicio
(TravelStartDate, dateOnly), Fecha de finalización (TravelEndDate,
dateOnly), Tipo de Licencia (TipodeLicencia, choice), ¿Aprobado? (Approved,
boolean). El export offline XLSX (Apellido/Nombre/Motivo/Desde/Hasta/
Estado) NUNCA fue el contrato real de la lista SharePoint — retirado del
runtime por completo.

Runtime field discovery sigue vigente: los 5 internal names se resuelven en
RUNTIME contra `/lists/{id}/columns` (ver `resolve_vacation_field_mapping`)
— sin `VacationFieldMapping.from_config()`. `_TEST_VACATION_MAPPING` usa los
internal names REALES confirmados por el owner (Colaborador/TipodeLicencia/
TravelStartDate/TravelEndDate/Approved) como fixture/referencia de test —
NUNCA hardcodeados como default de producción (siguen sin default en
config.py).
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

# Internal names REALES confirmados por el owner (smoke #4) — usados como
# fixture/referencia de test, NUNCA como default de producción.
_TEST_VACATION_MAPPING = VacationFieldMapping(
    consultant_name="Colaborador", leave_type="TipodeLicencia",
    start_date="TravelStartDate", end_date="TravelEndDate", approved="Approved",
)


@pytest.fixture(autouse=True)
def _clear_vacation_schema_cache():
    # El cache de resolve_vacation_field_mapping es un dict a nivel de
    # MÓDULO (mismo criterio que core/sharepoint_cases.py) — sin esto, un
    # test que resuelve exitosamente contra _SITE_ID/_VACA_LIST con un
    # token reusado (p. ej. "tok") deja cacheado ese resultado para
    # cualquier test posterior que reuse la misma combinación, aunque
    # mockee una respuesta distinta.
    repo_module._fetch_vacation_field_mapping_cached.clear()
    yield
    repo_module._fetch_vacation_field_mapping_cached.clear()


def _make_repository(vacation_field_mapping=_TEST_VACATION_MAPPING):
    return StaffingRepository(
        site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST,
        vacaciones_list_id=_VACA_LIST, vacation_field_mapping=vacation_field_mapping,
    )


def _http_error_fake(status_code):
    import urllib.error

    def fake(url, access_token, **kwargs):
        raise urllib.error.HTTPError(url, status_code, "boom", None, None)
    return fake


# --- Runtime field discovery — resolver contra el contrato REAL -----------

def _column(display_name, technical_name, *, hidden=False):
    return {"displayName": display_name, "name": technical_name, "hidden": hidden}


_LIVE_COLUMNS = [
    _column("Solicitud", "Title"),
    _column("Detalle", "ReasonForTravel"),
    _column("Solicitante", "Requester"),
    _column("Colaborador", "Colaborador"),
    _column("Fecha de inicio", "TravelStartDate"),
    _column("Fecha de finalización", "TravelEndDate"),
    _column("Duración", "TravelDuration"),
    _column("¿Aprobado?", "Approved"),
    _column("Aprobador", "Aprobador"),
    _column("Tipo de Licencia", "TipodeLicencia"),
    _column("Comentarios", "Comentarios"),
    _column("¿Aprobado CCHH?", "_x00bf_AprobadoCCHH_x003f_"),
    _column("Remplazo", "Remplazo"),
    _column("ID", "id", hidden=True),
]


# test 1
def test_colaborador_resolves_to_real_internal_name():
    from webapp.services.staffing_repository import _resolve_vacation_field_mapping_from_columns
    mapping = _resolve_vacation_field_mapping_from_columns(_LIVE_COLUMNS)
    assert mapping.consultant_name == "Colaborador"


# test 2
def test_fecha_de_inicio_resolves_to_travel_start_date():
    from webapp.services.staffing_repository import _resolve_vacation_field_mapping_from_columns
    mapping = _resolve_vacation_field_mapping_from_columns(_LIVE_COLUMNS)
    assert mapping.start_date == "TravelStartDate"


# test 3
def test_fecha_de_finalizacion_resolves_to_travel_end_date():
    from webapp.services.staffing_repository import _resolve_vacation_field_mapping_from_columns
    mapping = _resolve_vacation_field_mapping_from_columns(_LIVE_COLUMNS)
    assert mapping.end_date == "TravelEndDate"


# test 4
def test_tipo_de_licencia_resolves_to_internal_name():
    from webapp.services.staffing_repository import _resolve_vacation_field_mapping_from_columns
    mapping = _resolve_vacation_field_mapping_from_columns(_LIVE_COLUMNS)
    assert mapping.leave_type == "TipodeLicencia"


# test 5
def test_aprobado_resolves_to_approved():
    from webapp.services.staffing_repository import _resolve_vacation_field_mapping_from_columns
    mapping = _resolve_vacation_field_mapping_from_columns(_LIVE_COLUMNS)
    assert mapping.approved == "Approved"


# test 6/7/8: el contrato XLSX (first/last name, Estado, Motivo) ya no
# existe en absoluto — VacationFieldMapping no tiene esos campos.
def test_first_name_last_name_fields_no_longer_exist():
    assert not hasattr(_TEST_VACATION_MAPPING, "apellido")
    assert not hasattr(_TEST_VACATION_MAPPING, "nombre")


def test_estado_field_no_longer_required():
    assert not hasattr(_TEST_VACATION_MAPPING, "estado")


def test_motivo_field_no_longer_required():
    assert not hasattr(_TEST_VACATION_MAPPING, "motivo")


def test_vacation_field_mapping_shape_is_exactly_the_five_real_fields():
    assert {f.name for f in __import__("dataclasses").fields(VacationFieldMapping)} == {
        "consultant_name", "leave_type", "start_date", "end_date", "approved",
    }


@pytest.mark.parametrize("missing_display_name", [
    "Colaborador", "Fecha de inicio", "Fecha de finalización", "Tipo de Licencia", "¿Aprobado?",
])
def test_missing_required_display_name_raises_schema_error(missing_display_name):
    from webapp.services.staffing_repository import VacationSchemaError, _resolve_vacation_field_mapping_from_columns
    columns = [c for c in _LIVE_COLUMNS if c["displayName"] != missing_display_name]
    with pytest.raises(VacationSchemaError, match=missing_display_name):
        _resolve_vacation_field_mapping_from_columns(columns)


def test_duplicate_required_display_name_candidate_fails_safely():
    from webapp.services.staffing_repository import VacationSchemaError, _resolve_vacation_field_mapping_from_columns
    columns = _LIVE_COLUMNS + [_column("Colaborador", "SomeOtherInternalName")]
    with pytest.raises(VacationSchemaError, match="Colaborador"):
        _resolve_vacation_field_mapping_from_columns(columns)


def test_no_internal_name_guessing_missing_and_ambiguous_never_pick_a_candidate():
    from webapp.services.staffing_repository import VacationSchemaError, _resolve_vacation_field_mapping_from_columns
    with pytest.raises(VacationSchemaError):
        _resolve_vacation_field_mapping_from_columns([c for c in _LIVE_COLUMNS if c["displayName"] != "¿Aprobado?"])
    with pytest.raises(VacationSchemaError):
        _resolve_vacation_field_mapping_from_columns(_LIVE_COLUMNS + [_column("¿Aprobado?", "OtroApproved")])


def test_schema_error_no_longer_carries_temporary_discovery_surfaces():
    from webapp.services.staffing_repository import VacationSchemaError
    exc = VacationSchemaError(stage="columns", missing_fields=["Colaborador"], ambiguous_fields=[])
    assert not hasattr(exc, "available_columns")
    assert not hasattr(exc, "semantic_columns")


def test_schema_error_is_not_a_runtime_error():
    from webapp.services.staffing_repository import VacationGraphError, VacationSchemaError
    assert not issubclass(VacationSchemaError, RuntimeError)
    assert not issubclass(VacationSchemaError, VacationGraphError)


def test_schema_error_message_never_includes_the_access_token():
    from webapp.services.staffing_repository import VacationSchemaError
    exc = VacationSchemaError(stage="columns", missing_fields=["Colaborador"], ambiguous_fields=[])
    assert "tok" not in str(exc) or "token" not in str(exc).lower()


# --- Graph transport failures — stage x status matrix ----------------------

def test_columns_http_403_reports_stage_columns_status_403(monkeypatch):
    from webapp.services.staffing_repository import VacationGraphError
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _http_error_fake(403))
    repo = StaffingRepository(site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST, vacaciones_list_id=_VACA_LIST)
    with pytest.raises(VacationGraphError) as exc_info:
        repo.get_vacations("tok")
    assert (exc_info.value.stage, exc_info.value.status_code) == ("columns", 403)


def test_columns_http_404_reports_stage_columns_status_404(monkeypatch):
    from webapp.services.staffing_repository import VacationGraphError
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _http_error_fake(404))
    repo = StaffingRepository(site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST, vacaciones_list_id=_VACA_LIST)
    with pytest.raises(VacationGraphError) as exc_info:
        repo.get_vacations("tok")
    assert (exc_info.value.stage, exc_info.value.status_code) == ("columns", 404)


def test_items_http_403_reports_stage_items_status_403(monkeypatch):
    import urllib.error
    from webapp.services.staffing_repository import VacationGraphError

    def fake(url, access_token, **kwargs):
        if "columns" in url:
            return _LIVE_COLUMNS
        raise urllib.error.HTTPError(url, 403, "boom", None, None)

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)
    repo = StaffingRepository(site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST, vacaciones_list_id=_VACA_LIST)
    with pytest.raises(VacationGraphError) as exc_info:
        repo.get_vacations("tok")
    assert (exc_info.value.stage, exc_info.value.status_code) == ("items", 403)


def test_items_http_404_reports_stage_items_status_404(monkeypatch):
    import urllib.error
    from webapp.services.staffing_repository import VacationGraphError

    def fake(url, access_token, **kwargs):
        if "columns" in url:
            return _LIVE_COLUMNS
        raise urllib.error.HTTPError(url, 404, "boom", None, None)

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)
    repo = StaffingRepository(site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST, vacaciones_list_id=_VACA_LIST)
    with pytest.raises(VacationGraphError) as exc_info:
        repo.get_vacations("tok")
    assert (exc_info.value.stage, exc_info.value.status_code) == ("items", 404)


def test_get_vacations_resolves_metadata_then_fetches_items_with_resolved_names(monkeypatch):
    calls = []

    def fake(url, access_token, **kwargs):
        calls.append(url)
        if "columns" in url:
            return _LIVE_COLUMNS
        assert "Colaborador" in url and "TipodeLicencia" in url and "TravelStartDate" in url
        assert "TravelEndDate" in url and "Approved" in url
        return []

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)
    repo = StaffingRepository(site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST, vacaciones_list_id=_VACA_LIST)
    repo.get_vacations("tok")
    assert len(calls) == 2 and "columns" in calls[0]


def test_successful_metadata_resolution_is_cached_and_reused(monkeypatch):
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
    assert len(columns_calls) == 1


def test_failed_metadata_discovery_is_not_cached_as_success(monkeypatch):
    from webapp.services.staffing_repository import VacationSchemaError
    columns_calls = []

    def fake(url, access_token, **kwargs):
        assert "columns" in url
        columns_calls.append(url)
        return [c for c in _LIVE_COLUMNS if c["displayName"] != "Colaborador"]

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)
    repo = StaffingRepository(site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST, vacaciones_list_id=_VACA_LIST)
    with pytest.raises(VacationSchemaError):
        repo.get_vacations("tok-fails")
    with pytest.raises(VacationSchemaError):
        repo.get_vacations("tok-fails")
    assert len(columns_calls) == 2  # NUNCA se cachea una falla


# --- get_vacations: filtro Tipo de Licencia == Vacaciones + Approved=True -

_APPROVED = {
    "id": "1", "fields": {
        "Colaborador": "Matías Gómez", "TipodeLicencia": "Vacaciones",
        "Approved": True, "TravelStartDate": "2026-03-10", "TravelEndDate": "2026-03-20",
    },
}


def _row(_id="1", **field_overrides):
    fields = dict(_APPROVED["fields"])
    fields.update(field_overrides)
    return {"id": _id, "fields": fields}


def _fake_fetch(rows):
    def fake(url, access_token, **kwargs):
        assert _SITE_ID in url and _VACA_LIST in url
        return rows
    return fake


# test 9
def test_vacaciones_true_creates_vacation_period(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_row()]))
    periods = _make_repository().get_vacations("tok")
    assert periods == [VacationPeriod(
        source_id="1", consultant_name="Matías Gómez", start_date="2026-03-10", end_date="2026-03-20",
    )]


# test 10
def test_approved_false_excluded(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_row(Approved=False)]))
    assert _make_repository().get_vacations("tok") == []


# test 11
def test_approved_none_excluded(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_row(Approved=None)]))
    assert _make_repository().get_vacations("tok") == []


# test 12
def test_approved_missing_entirely_excluded(monkeypatch):
    row = _row()
    del row["fields"]["Approved"]
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([row]))
    assert _make_repository().get_vacations("tok") == []


# test 13
def test_enfermedad_approved_true_excluded(monkeypatch):
    monkeypatch.setattr(
        repo_module.microsoft365, "graph_get_paged_values",
        _fake_fetch([_row(TipodeLicencia="Enfermedad")]),
    )
    assert _make_repository().get_vacations("tok") == []


# test 14
def test_dia_de_estudio_approved_true_excluded(monkeypatch):
    monkeypatch.setattr(
        repo_module.microsoft365, "graph_get_paged_values",
        _fake_fetch([_row(TipodeLicencia="Día de Estudio")]),
    )
    assert _make_repository().get_vacations("tok") == []


# test 15: cualquier otro tipo de licencia real conocido queda excluido.
@pytest.mark.parametrize("leave_type", [
    "Día de Cumpleaños", "Maternidad", "Paternidad", "Otros", "Mudanzas", "Danwor Day",
])
def test_other_known_leave_types_excluded(monkeypatch, leave_type):
    monkeypatch.setattr(
        repo_module.microsoft365, "graph_get_paged_values",
        _fake_fetch([_row(TipodeLicencia=leave_type)]),
    )
    assert _make_repository().get_vacations("tok") == []


# test 16: Aprobado CCHH (presente o no en la fila) NUNCA afecta la
# inclusión V1 — ni se lee, ni se selecciona en la URL de fetch.
def test_aprobado_cchh_does_not_affect_v1_inclusion(monkeypatch):
    calls = []
    row_cchh_true = _row(_id="1", **{"_x00bf_AprobadoCCHH_x003f_": True})
    row_cchh_false = _row(_id="2", **{"_x00bf_AprobadoCCHH_x003f_": False})

    def fake(url, access_token, **kwargs):
        calls.append(url)
        return [row_cchh_true, row_cchh_false]

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)
    periods = _make_repository().get_vacations("tok")
    assert {p.source_id for p in periods} == {"1", "2"}  # ambas incluidas, CCHH ignorado
    assert not any("AprobadoCCHH" in url or "_x00bf_" in url for url in calls)  # nunca seleccionado


# --- Colaborador es texto simple — sin composición nombre/apellido ---------

# test 17
def test_colaborador_text_used_directly_as_consultant_name(monkeypatch):
    monkeypatch.setattr(
        repo_module.microsoft365, "graph_get_paged_values",
        _fake_fetch([_row(Colaborador="Ana María Pérez")]),
    )
    periods = _make_repository().get_vacations("tok")
    assert periods[0].consultant_name == "Ana María Pérez"


def test_missing_colaborador_row_excluded(monkeypatch):
    row = _row()
    row["fields"]["Colaborador"] = ""
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([row]))
    assert _make_repository().get_vacations("tok") == []


# --- Fechas: dateOnly, un solo día válido, fin inclusive vive en el servicio

# test 22/24
def test_single_day_vacation_is_valid(monkeypatch):
    monkeypatch.setattr(
        repo_module.microsoft365, "graph_get_paged_values",
        _fake_fetch([_row(TravelStartDate="2026-06-10", TravelEndDate="2026-06-10")]),
    )
    periods = _make_repository().get_vacations("tok")
    assert periods[0].start_date == periods[0].end_date == "2026-06-10"


def test_missing_end_date_row_excluded_fail_soft(monkeypatch):
    missing_end = _row(_id="5", TravelEndDate="")
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([missing_end, _row()]))
    periods = _make_repository().get_vacations("tok")
    assert [p.source_id for p in periods] == ["1"]


def test_multiple_periods_for_same_consultant_preserved_separately(monkeypatch):
    second = _row(_id="8", TravelStartDate="2026-01-05", TravelEndDate="2026-01-10")
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_row(), second]))
    periods = _make_repository().get_vacations("tok")
    assert {p.source_id for p in periods} == {"1", "8"}


# test 25: Duración nunca se selecciona ni se usa — solo TravelStartDate/
# TravelEndDate definen el período.
def test_duracion_field_never_fetched_or_used(monkeypatch):
    calls = []

    def fake(url, access_token, **kwargs):
        calls.append(url)
        return [_row()]

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)
    _make_repository().get_vacations("tok")
    assert not any("TravelDuration" in url or "Duraci" in url for url in calls)


def test_source_id_preserved_from_graph_item_id(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_row()]))
    periods = _make_repository().get_vacations("tok")
    assert periods[0].source_id == "1"


_VACATION_DIAGNOSTICS_KEYS = {
    "rows", "pages", "elapsed_ms", "raw_rows", "leave_type_vacaciones_rows",
    "approved_true_rows", "approved_false_rows", "approved_missing_or_null_rows",
    "approved_other_rows", "required_fields_present_rows",
    "missing_consultant_rows", "missing_start_date_rows", "missing_end_date_rows",
}


def test_get_vacations_diagnostics_never_include_names_or_pii(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_row()]))
    repo = _make_repository()
    repo.get_vacations("tok-secret")
    diag = repo.last_diagnostics["vacaciones"]
    assert set(diag.keys()) == _VACATION_DIAGNOSTICS_KEYS
    assert diag["rows"] == 1
    # test 10: solo conteos — ningún valor del fixture ("Matías Gómez",
    # "2026-03-10", etc.) aparece en el diccionario de diagnóstico.
    for value in diag.values():
        assert not isinstance(value, str)


# --- Empty Success — Repository Filter Funnel Diagnostics (tests 1-9) -----

# test 1
def test_raw_rows_counts_every_fetched_item(monkeypatch):
    rows = [_row(), _row(_id="2", TipodeLicencia="Enfermedad"), _row(_id="3", Approved=False)]
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch(rows))
    repo = _make_repository()
    repo.get_vacations("tok")
    assert repo.last_diagnostics["vacaciones"]["raw_rows"] == 3


# test 2/9: leave_type_vacaciones_rows cuenta SOLO "Vacaciones" exacto —
# otros tipos (incluyendo Enfermedad) no lo contaminan.
def test_leave_type_vacaciones_rows_counts_only_exact_vacaciones(monkeypatch):
    rows = [
        _row(_id="1", TipodeLicencia="Vacaciones"),
        _row(_id="2", TipodeLicencia="Enfermedad"),
        _row(_id="3", TipodeLicencia="Día de Estudio"),
    ]
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch(rows))
    repo = _make_repository()
    repo.get_vacations("tok")
    assert repo.last_diagnostics["vacaciones"]["leave_type_vacaciones_rows"] == 1


# test 3
def test_approved_true_rows_counts_exact_boolean_true_on_vacation_rows(monkeypatch):
    rows = [_row(_id="1", Approved=True), _row(_id="2", Approved=True)]
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch(rows))
    repo = _make_repository()
    repo.get_vacations("tok")
    assert repo.last_diagnostics["vacaciones"]["approved_true_rows"] == 2


# test 4
def test_approved_false_rows_counts_exact_boolean_false(monkeypatch):
    rows = [_row(_id="1", Approved=False)]
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch(rows))
    repo = _make_repository()
    repo.get_vacations("tok")
    diag = repo.last_diagnostics["vacaciones"]
    assert diag["approved_false_rows"] == 1
    assert diag["approved_true_rows"] == 0


# test 5: ausente y None cuentan juntos, de forma segura.
def test_approved_missing_or_null_rows_counts_missing_and_none(monkeypatch):
    row_none = _row(_id="1", Approved=None)
    row_missing = _row(_id="2")
    del row_missing["fields"]["Approved"]
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([row_none, row_missing]))
    repo = _make_repository()
    repo.get_vacations("tok")
    assert repo.last_diagnostics["vacaciones"]["approved_missing_or_null_rows"] == 2


# test 6: una representación no-booleana en runtime se detecta, NUNCA se
# acepta como aprobación (el filtro de negocio sigue siendo `is True`).
def test_approved_other_rows_detects_non_boolean_representation_without_accepting_it(monkeypatch):
    row_string_true = _row(_id="1", Approved="true")
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([row_string_true]))
    repo = _make_repository()
    periods = repo.get_vacations("tok")
    diag = repo.last_diagnostics["vacaciones"]
    assert diag["approved_other_rows"] == 1
    assert diag["approved_true_rows"] == 0
    assert periods == []  # NUNCA aceptado como aprobación


# test 7/8: required_fields_present_rows == periods_rows (rows) — mismo
# criterio, expuesto con dos nombres por contrato de diagnóstico.
def test_required_fields_present_rows_matches_final_periods_count(monkeypatch):
    rows = [_row(_id="1"), _row(_id="2", Colaborador="")]
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch(rows))
    repo = _make_repository()
    periods = repo.get_vacations("tok")
    diag = repo.last_diagnostics["vacaciones"]
    assert diag["required_fields_present_rows"] == diag["rows"] == len(periods) == 1
    assert diag["missing_consultant_rows"] == 1
    assert diag["missing_start_date_rows"] == 0
    assert diag["missing_end_date_rows"] == 0


# test 26: el contrato XLSX temporal está completamente ausente del código
# de producción — ninguno de sus nombres aparece como atributo/constante.
def test_xlsx_field_contract_is_absent_from_production_runtime():
    assert not hasattr(repo_module, "VACATION_MOTIVO_VALUE")
    assert not hasattr(repo_module, "VACATION_ESTADO_APROBADA_VALUE")
    assert repo_module.VACATION_LEAVE_TYPE_VALUE == "Vacaciones"


# --- get_consultants (sin cambios de contrato) ------------------------------

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
