"""
test_staffing_vacation_repository.py — Contrato de StaffingRepository para
Vacaciones (Staffing Vacation Timeline Overlay — AUTHORITATIVE SOURCE
CORRECTIVE: SharePoint XLSX, ver HANDOFF).

La suposición previa de una LISTA SharePoint (`/items` + discovery de
`/columns` + flag `¿Aprobado CCHH?`) era INCORRECTA y quedó RETIRADA. La
fuente real es el archivo `Solicitudes (2).xlsx` en la biblioteca de
documentos del sitio Operaciones. Estos tests son determinísticos, sin Graph
real: monkeypatchean `microsoft365.graph_get_json` (resolución de sitio) y
`microsoft365.graph_get_bytes` (bytes del workbook) con fixtures fabricadas —
el workbook se construye EN MEMORIA con openpyxl para ejercitar el parser
real. La semántica de negocio es del propio archivo: `Motivo == "Vacaciones"`
AND `Estado == "Aprobada"`.
"""
from __future__ import annotations

import io
from datetime import date, datetime

import openpyxl
import pytest

from webapp.services import staffing_repository as repo_module
from webapp.services.staffing_repository import (
    GRAPH_BASE,
    StaffingRepository,
    VacationGraphError,
    VacationPeriod,
    VacationSchemaError,
)

_SITE_ID = "test-site-id"
_COLAB_LIST = "colab-list-id"

# Header completo REAL del worksheet (14 columnas, evidencia histórica). Solo
# 6 forman el contrato de runtime — el resto (CUIL/Legajo/Días/comentarios/
# etiquetas) existe en el archivo pero NUNCA se retiene ni expone.
_HEADERS = [
    "Fecha de Solicitud", "CUIL", "Legajo", "Apellido", "Nombre", "Código",
    "Motivo", "Desde", "Hasta", "Días", "Estado",
    "Comentario del empleado", "Comentario del aprobador", "Etiquetas del empleado",
]

# Fila base ELEGIBLE (Motivo=Vacaciones, Estado=Aprobada). Los campos PII
# (CUIL/Legajo/comentarios/etiquetas) llevan valores distintivos a propósito,
# para probar que NUNCA aparecen en el payload ni en los diagnósticos.
_BASE = {
    "Fecha de Solicitud": "2026-03-01", "CUIL": "20-99887766-3", "Legajo": "LEG-4242",
    "Apellido": "Gómez", "Nombre": "Matías", "Código": "VAC",
    "Motivo": "Vacaciones", "Desde": "2026-03-10", "Hasta": "2026-03-20",
    "Días": 9, "Estado": "Aprobada",
    "Comentario del empleado": "COMENTARIO-EMPLEADO-SECRETO",
    "Comentario del aprobador": "COMENTARIO-APROBADOR-SECRETO",
    "Etiquetas del empleado": "ETIQUETA-SECRETA",
}


def _cells(**overrides) -> list:
    """Fila ordenada según `_HEADERS` a partir de `_BASE` + overrides."""
    data = dict(_BASE)
    data.update(overrides)
    return [data[h] for h in _HEADERS]


def _workbook_bytes(rows, *, sheet_name="Solicitudes", headers=_HEADERS, extra_sheets=None) -> bytes:
    """Serializa un workbook plano EN MEMORIA (header row + data rows), sin
    Tabla de Excel — igual que la fuente real."""
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_name
    if headers is not None:
        worksheet.append(headers)
    for row in rows:
        worksheet.append(row)
    for name, srows in (extra_sheets or {}).items():
        sheet = workbook.create_sheet(name)
        for r in srows:
            sheet.append(r)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _patch_source(monkeypatch, workbook_bytes, *, site=None):
    """Monkeypatchea el seam de Graph (json→sitio, bytes→workbook) y captura
    las URLs pedidas."""
    captured: dict = {}

    def fake_json(url, access_token, **kwargs):
        captured["site_url"] = url
        captured["site_token"] = access_token
        return {"id": "site-123"} if site is None else site

    def fake_bytes(url, access_token, **kwargs):
        captured["content_url"] = url
        captured["content_token"] = access_token
        if isinstance(workbook_bytes, Exception):
            raise workbook_bytes
        return workbook_bytes

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_bytes", fake_bytes)
    return captured


def _http_error(status_code):
    import urllib.error

    return urllib.error.HTTPError("https://graph", status_code, "boom", None, None)


# --- item 1/2: URL de Graph al sitio Operaciones + ruta del drive, vía seam --

def test_download_targets_operaciones_site_then_drive_path(monkeypatch):
    cap = _patch_source(monkeypatch, _workbook_bytes([_cells()]))
    StaffingRepository().get_vacations("tok")
    assert cap["site_url"].endswith("/sites/danwor.sharepoint.com:/sites/Operaciones")
    assert cap["content_url"].endswith(
        "/sites/site-123/drive/root:/General/Template%20Dashboard/Solicitudes%20%282%29.xlsx:/content"
    )
    assert cap["content_url"].startswith(GRAPH_BASE)


def test_bytes_obtained_via_graph_get_bytes_seam(monkeypatch):
    # item 2: el workbook llega por graph_get_bytes; la lista NUNCA se toca.
    def boom_paged(*a, **k):
        raise AssertionError("graph_get_paged_values NO debe usarse para Vacaciones")

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", boom_paged)
    _patch_source(monkeypatch, _workbook_bytes([_cells()]))
    periods = StaffingRepository().get_vacations("tok")
    assert len(periods) == 1


def test_access_token_forwarded_never_logged(monkeypatch):
    cap = _patch_source(monkeypatch, _workbook_bytes([_cells()]))
    StaffingRepository().get_vacations("tok-secret")
    assert cap["site_token"] == cap["content_token"] == "tok-secret"


# --- item 3/4: worksheet "Solicitudes" seleccionado; worksheet plano funciona -

def test_selects_solicitudes_worksheet_even_with_decoy_sheets(monkeypatch):
    data = _workbook_bytes(
        [_cells()],
        extra_sheets={"Hoja1": [["basura", "no", "usar"]], "Otros": [_HEADERS, _cells(Nombre="NoDebe")]},
    )
    _patch_source(monkeypatch, data)
    periods = StaffingRepository().get_vacations("tok")
    assert [p.consultant_name for p in periods] == ["Matías Gómez"]


def test_flat_worksheet_without_excel_table_produces_periods(monkeypatch):
    _patch_source(monkeypatch, _workbook_bytes([_cells()]))
    assert StaffingRepository().get_vacations("tok") == [
        VacationPeriod(source_id="xlsx:1", consultant_name="Matías Gómez",
                       start_date="2026-03-10", end_date="2026-03-20"),
    ]


# --- item 5: headers validados ------------------------------------------------

@pytest.mark.parametrize("drop", ["Apellido", "Nombre", "Motivo", "Desde", "Hasta", "Estado"])
def test_missing_required_header_fails_soft_schema_error(monkeypatch, drop):
    headers = [h for h in _HEADERS if h != drop]
    # fila alineada al header recortado
    rows = [[v for h, v in zip(_HEADERS, _cells()) if h != drop]]
    _patch_source(monkeypatch, _workbook_bytes(rows, headers=headers))
    with pytest.raises(VacationSchemaError) as exc:
        StaffingRepository().get_vacations("tok")
    assert exc.value.stage == "headers" and drop in exc.value.missing_fields


def test_empty_worksheet_no_header_row_fails_soft(monkeypatch):
    _patch_source(monkeypatch, _workbook_bytes([], headers=None))
    with pytest.raises(VacationSchemaError) as exc:
        StaffingRepository().get_vacations("tok")
    assert exc.value.stage == "headers"


# --- item 6/7/8: filtro Motivo==Vacaciones AND Estado==Aprobada --------------

def test_vacaciones_aprobada_creates_period(monkeypatch):
    _patch_source(monkeypatch, _workbook_bytes([_cells()]))
    periods = StaffingRepository().get_vacations("tok")
    assert periods[0].consultant_name == "Matías Gómez"


@pytest.mark.parametrize("estado", ["Cancelada", "Rechazada", "Pendiente", "aprobada", "APROBADA", ""])
def test_vacaciones_non_aprobada_excluded(monkeypatch, estado):
    _patch_source(monkeypatch, _workbook_bytes([_cells(Estado=estado)]))
    assert StaffingRepository().get_vacations("tok") == []


@pytest.mark.parametrize("motivo", ["Enfermedad", "Maternidad", "Día de Estudio", "vacaciones", "Otros", ""])
def test_non_vacaciones_even_if_aprobada_excluded(monkeypatch, motivo):
    _patch_source(monkeypatch, _workbook_bytes([_cells(Motivo=motivo, Estado="Aprobada")]))
    assert StaffingRepository().get_vacations("tok") == []


# --- item 9/10/11: fechas nativas + dd/mm/yyyy; fin inclusive; un día --------

def test_native_excel_dates_parse_to_iso(monkeypatch):
    _patch_source(monkeypatch, _workbook_bytes([
        _cells(Desde=datetime(2026, 7, 1, 0, 0), Hasta=date(2026, 7, 15)),
    ]))
    period = StaffingRepository().get_vacations("tok")[0]
    assert (period.start_date, period.end_date) == ("2026-07-01", "2026-07-15")


def test_textual_ddmmyyyy_dates_parse_to_iso(monkeypatch):
    _patch_source(monkeypatch, _workbook_bytes([_cells(Desde="3/7/2026", Hasta="15/07/2026")]))
    period = StaffingRepository().get_vacations("tok")[0]
    assert (period.start_date, period.end_date) == ("2026-07-03", "2026-07-15")


def test_single_day_vacation_is_valid(monkeypatch):
    _patch_source(monkeypatch, _workbook_bytes([_cells(Desde="2026-06-10", Hasta="2026-06-10")]))
    period = StaffingRepository().get_vacations("tok")[0]
    assert period.start_date == period.end_date == "2026-06-10"


def test_row_missing_date_excluded_fail_soft(monkeypatch):
    _patch_source(monkeypatch, _workbook_bytes([_cells(Hasta=""), _cells()]))
    periods = StaffingRepository().get_vacations("tok")
    assert [p.start_date for p in periods] == ["2026-03-10"]


def test_unparseable_date_excluded_fail_soft(monkeypatch):
    _patch_source(monkeypatch, _workbook_bytes([_cells(Desde="no-es-fecha")]))
    assert StaffingRepository().get_vacations("tok") == []


# --- item 12: Días se ignora (no define el largo de la barra) ----------------

def test_dias_column_is_ignored(monkeypatch):
    # Días absurdo NO cambia las fechas ni la elegibilidad.
    _patch_source(monkeypatch, _workbook_bytes([_cells(Días=99999)]))
    period = StaffingRepository().get_vacations("tok")[0]
    assert (period.start_date, period.end_date) == ("2026-03-10", "2026-03-20")


# --- item 13: consultant_name = "Nombre Apellido" ----------------------------

def test_consultant_name_is_nombre_then_apellido(monkeypatch):
    _patch_source(monkeypatch, _workbook_bytes([_cells(Nombre="Ana María", Apellido="Pérez López")]))
    assert StaffingRepository().get_vacations("tok")[0].consultant_name == "Ana María Pérez López"


def test_whitespace_in_name_is_collapsed_on_trim(monkeypatch):
    _patch_source(monkeypatch, _workbook_bytes([_cells(Nombre="  Juan  ", Apellido="  Díaz ")]))
    assert StaffingRepository().get_vacations("tok")[0].consultant_name == "Juan Díaz"


def test_row_missing_name_excluded_fail_soft(monkeypatch):
    _patch_source(monkeypatch, _workbook_bytes([_cells(Nombre="", Apellido="")]))
    assert StaffingRepository().get_vacations("tok") == []


# --- item 14: el payload NUNCA retiene CUIL/Legajo/comentarios/etiquetas/Días -

def test_vacation_period_shape_is_exactly_four_fields():
    import dataclasses
    assert {f.name for f in dataclasses.fields(VacationPeriod)} == {
        "source_id", "consultant_name", "start_date", "end_date",
    }


def test_no_pii_leaks_into_payload(monkeypatch):
    _patch_source(monkeypatch, _workbook_bytes([_cells()]))
    period = StaffingRepository().get_vacations("tok")[0]
    blob = repr(period)
    for secret in ("20-99887766-3", "LEG-4242", "COMENTARIO-EMPLEADO-SECRETO",
                   "COMENTARIO-APROBADOR-SECRETO", "ETIQUETA-SECRETA", "99999"):
        assert secret not in blob


# --- item 15/16: workbook/worksheet inválidos fallan soft --------------------

def test_malformed_workbook_bytes_fail_soft(monkeypatch):
    _patch_source(monkeypatch, b"esto no es un xlsx")
    with pytest.raises(VacationSchemaError) as exc:
        StaffingRepository().get_vacations("tok")
    assert exc.value.stage == "workbook"


def test_missing_worksheet_fails_soft(monkeypatch):
    _patch_source(monkeypatch, _workbook_bytes([_cells()], sheet_name="OtroNombre"))
    with pytest.raises(VacationSchemaError) as exc:
        StaffingRepository().get_vacations("tok")
    assert exc.value.stage == "worksheet"


# --- transporte: sitio/descarga → VacationGraphError seguro -------------------

def test_site_http_403_reports_stage_site(monkeypatch):
    def fake_json(url, access_token, **kwargs):
        raise _http_error(403)

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_json", fake_json)
    with pytest.raises(VacationGraphError) as exc:
        StaffingRepository().get_vacations("tok")
    assert (exc.value.stage, exc.value.status_code) == ("site", 403)


def test_download_http_404_reports_stage_download(monkeypatch):
    _patch_source(monkeypatch, _http_error(404))
    with pytest.raises(VacationGraphError) as exc:
        StaffingRepository().get_vacations("tok")
    assert (exc.value.stage, exc.value.status_code) == ("download", 404)


def test_site_without_id_fails_soft_schema_error(monkeypatch):
    _patch_source(monkeypatch, _workbook_bytes([_cells()]), site={})
    with pytest.raises(VacationSchemaError) as exc:
        StaffingRepository().get_vacations("tok")
    assert exc.value.stage == "site"


def test_graph_error_message_never_includes_the_access_token(monkeypatch):
    def fake_json(url, access_token, **kwargs):
        raise _http_error(500)

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_json", fake_json)
    with pytest.raises(VacationGraphError) as exc:
        StaffingRepository().get_vacations("tok-do-not-log")
    assert "tok-do-not-log" not in str(exc.value)


# --- múltiples períodos + fila totalmente vacía -------------------------------

def test_multiple_eligible_periods_preserved(monkeypatch):
    rows = [
        _cells(Nombre="Ana", Apellido="Uno", Desde="2026-01-05", Hasta="2026-01-10"),
        _cells(Nombre="Beto", Apellido="Dos", Desde="2026-02-05", Hasta="2026-02-10"),
    ]
    _patch_source(monkeypatch, _workbook_bytes(rows))
    periods = StaffingRepository().get_vacations("tok")
    assert {p.consultant_name for p in periods} == {"Ana Uno", "Beto Dos"}
    assert len({p.source_id for p in periods}) == 2


def test_fully_empty_rows_are_skipped(monkeypatch):
    rows = [_cells(), [None] * len(_HEADERS), ["", "", "", "", "", "", "", "", "", "", "", "", "", ""]]
    _patch_source(monkeypatch, _workbook_bytes(rows))
    diag = _get_diag(monkeypatch, rows)
    assert diag["xlsx_rows"] == 1


# --- item 19: funnel de diagnóstico seguro (conteos puros, sin PII) ----------

_VACATION_DIAGNOSTICS_KEYS = {
    "worksheet_found", "xlsx_rows", "vacaciones_rows", "aprobadas_rows",
    "eligible_periods", "elapsed_ms",
}


def _get_diag(monkeypatch, rows):
    _patch_source(monkeypatch, _workbook_bytes(rows))
    repo = StaffingRepository()
    repo.get_vacations("tok")
    return repo.last_diagnostics["vacaciones"]


def test_diagnostics_keys_are_exactly_the_safe_funnel(monkeypatch):
    diag = _get_diag(monkeypatch, [_cells()])
    assert set(diag.keys()) == _VACATION_DIAGNOSTICS_KEYS


def test_diagnostics_funnel_counts_each_stage(monkeypatch):
    rows = [
        _cells(Nombre="A", Apellido="Uno"),                              # Vacaciones+Aprobada → eligible
        _cells(Nombre="B", Apellido="Dos", Estado="Cancelada"),          # Vacaciones no-Aprobada
        _cells(Nombre="C", Apellido="Tres", Motivo="Enfermedad"),        # no-Vacaciones
    ]
    diag = _get_diag(monkeypatch, rows)
    assert diag["worksheet_found"] == 1
    assert diag["xlsx_rows"] == 3
    assert diag["vacaciones_rows"] == 2       # A + B (Motivo=Vacaciones)
    assert diag["aprobadas_rows"] == 1        # solo A (Estado=Aprobada)
    assert diag["eligible_periods"] == 1


def test_diagnostics_never_include_names_or_pii(monkeypatch):
    diag = _get_diag(monkeypatch, [_cells()])
    for value in diag.values():
        assert isinstance(value, (int, float)) and not isinstance(value, bool)


# --- item 18: el modelo-lista quedó completamente RETIRADO del runtime -------

def test_retired_list_model_symbols_are_absent():
    for gone in ("VacationFieldMapping", "resolve_vacation_field_mapping",
                 "_resolve_vacation_field_mapping_from_columns", "_VACATION_DISPLAY_NAMES",
                 "_ttl_cache", "_fetch_vacation_field_mapping_cached"):
        assert not hasattr(repo_module, gone), gone


def test_repository_constructor_no_longer_takes_vacation_list_args():
    import inspect
    params = set(inspect.signature(StaffingRepository.__init__).parameters)
    assert "vacaciones_list_id" not in params
    assert "vacation_field_mapping" not in params


def test_leave_type_and_estado_values_are_authoritative():
    assert repo_module.VACATION_LEAVE_TYPE_VALUE == "Vacaciones"
    assert repo_module.VACATION_ESTADO_VALUE == "Aprobada"


# --- get_consultants (sin cambios de contrato: sigue leyendo la lista) -------

def test_get_consultants_raises_explicit_when_colaborador_title_missing():
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
