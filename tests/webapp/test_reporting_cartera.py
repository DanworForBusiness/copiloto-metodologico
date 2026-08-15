"""
test_reporting_cartera.py — Contrato de datos reales de Cartera (Reporting
Integral real-data Fase 1). Determinístico, sin Graph real: monkeypatchea
`core.microsoft365` (mismo criterio que los tests de Staffing) y construye un
XLSX real con la tabla `Tabla24` en memoria.
"""
from __future__ import annotations

import io

import openpyxl
import pytest
from openpyxl.worksheet.table import Table

from webapp.services import reporting_repository as repo_mod
from webapp.services.reporting_repository import (
    ReportingConfigurationError,
    ReportingGraphError,
    ReportingRepository,
    ReportingSourceError,
)
from webapp.services.reporting_service import ReportingService

_SITE, _HP, _PROY, _CLI = "site-1", "hp-list", "proy-list", "cli-list"


def _items(rows):
    return [{"id": r.get("id", ""), "fields": r} for r in rows]


def _facturadas_xlsx(pairs, *, headers=("Proyecto Base", "Suma de Cantidad"),
                     lead_rows=0, decoy_sheet=False):
    """Construye el XLSX de Facturadas. `lead_rows`/`decoy_sheet` permiten
    ubicar la tabla FUERA de A1 / en una hoja posterior, para probar que la
    extracción no asume posición (read_only no expone la metadata de tabla)."""
    wb = openpyxl.Workbook()
    if decoy_sheet:
        decoy = wb.active
        decoy.title = "Portada"
        decoy.append(["Reporte", "Horas Facturadas"])
        ws = wb.create_sheet("Facturado")
    else:
        ws = wb.active
        ws.title = "Facturado"
    for _ in range(lead_rows):
        ws.append(["", ""])  # filas de relleno antes del header
    header_row = len(ws["A"]) + 1 if lead_rows else 1
    ws.append(list(headers))
    for proyecto_base, cantidad in pairs:
        ws.append([proyecto_base, cantidad])
    ws.add_table(Table(displayName="Tabla24", ref=f"A{header_row}:B{header_row + len(pairs)}"))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_CP = "cp-list"


def _patch(monkeypatch, *, horas, proyectos, clientes, facturadas, cp_columns=None, client_partners=None):
    def fake_paged(url, access_token=None, **kw):
        if "/columns" in url:                       # metadata de columnas de Cliente
            return cp_columns if cp_columns is not None else []
        if _CP in url:                              # lista target de Client Partner
            return _items(client_partners or [])
        if _HP in url:
            return _items(horas)
        if _PROY in url:
            return _items(proyectos)
        if _CLI in url:
            return _items(clientes)
        return []

    def fake_bytes(url, access_token=None, **kw):
        if isinstance(facturadas, Exception):
            raise facturadas
        return facturadas

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)


_CP_COLUMNS = [{"name": "Title"}, {"name": "ClientPartner", "lookup": {"listId": _CP}}]


def _repo():
    return ReportingRepository(
        site_id=_SITE, horas_proyectos_list_id=_HP,
        proyectos_list_id=_PROY, cliente_list_id=_CLI,
    )


# --- Repository: normalización + joins ---------------------------------------

def test_repo_joins_lists_and_xlsx_and_normalizes(monkeypatch):
    # Nombres internos REALES (live smoke): A_x00f1_o, Status, ID_x0020_...,
    # Cliente.Title, ClientPartnerLookupId → target descubierto de /columns.
    _patch(
        monkeypatch,
        horas=[{"ProyectoLookupId": "p1", "EmpresaLookupId": "c1",
                "A_x00f1_o": "2026", "Mes": "Marzo", "HsTrabajadas": "100", "HsVendidas": "120", "HsFacturadas": "110"}],
        proyectos=[{"id": "p1", "Title": "Implementación SADI v2", "ID_x0020_Proyecto_x0020_Danwor": "DAN-1042",
                    "TipodeProyecto": "Proyecto", "FechadeInicio": "2026-01-15", "Status": "En curso"}],
        clientes=[{"id": "c1", "Title": "YPF", "ClientPartnerLookupId": "pm1"}],
        cp_columns=_CP_COLUMNS,
        client_partners=[{"id": "pm1", "Title": "Diego Ramos"}],
        facturadas=_facturadas_xlsx([("Implementación SADI v2", 290)]),
    )
    facts = _repo().get_cartera_facts("tok")
    assert len(facts) == 1
    f = facts[0]
    assert f["cliente"] == "YPF" and f["clientPartner"] == "Diego Ramos"
    assert f["proyecto"] == "Implementación SADI v2" and f["idProyecto"] == "DAN-1042"
    assert f["tipoProyecto"] == "Proyecto" and f["estado"] == "En curso" and f["anioInicio"] == "2026"
    assert f["hsTrabajadas"] == 100 and f["hsVendidas"] == 120 and f["hsCertificadas"] == 110
    assert f["facturadasProyecto"] == 290  # join XLSX por nombre normalizado


def test_graph_form_lookup_ids_and_title_from_joined_project(monkeypatch):
    # Reproduce el bug del live-smoke (resultado vacío): Graph entrega los
    # lookups como ProyectoLookupId/EmpresaLookupId (no ProyectoId/EmpresaId) y
    # NO trae el título en la fila de Horas — el join debe resolver igual.
    _patch(
        monkeypatch,
        horas=[{"ProyectoLookupId": "7", "EmpresaLookupId": "3", "A_x00f1_o": "2026", "Mes": "Marzo",
                "HsTrabajadas": "100", "HsVendidas": "120", "HsFacturadas": "110"}],
        proyectos=[{"id": "7", "Title": "Implementación SADI v2", "ID_x0020_Proyecto_x0020_Danwor": "DAN-1042",
                    "TipodeProyecto": "Proyecto", "FechadeInicio": "2026-01-15", "Status": "En curso"}],
        clientes=[{"id": "3", "Title": "YPF", "ClientPartnerLookupId": "pm1"}],
        cp_columns=_CP_COLUMNS,
        client_partners=[{"id": "pm1", "Title": "Diego Ramos"}],
        facturadas=_facturadas_xlsx([("Implementación SADI v2", 290)]),
    )
    facts = _repo().get_cartera_facts("tok")
    assert len(facts) == 1
    f = facts[0]
    assert f["proyecto"] == "Implementación SADI v2" and f["cliente"] == "YPF"
    assert f["clientPartner"] == "Diego Ramos" and f["idProyecto"] == "DAN-1042"
    assert f["tipoProyecto"] == "Proyecto" and f["facturadasProyecto"] == 290


def test_tiempo_y_materiales_maps_to_mantenimiento(monkeypatch):
    _patch(
        monkeypatch,
        horas=[{"ProyectoId": "p1", "EmpresaId": "c1", "Proyecto": "Mant BI",
                "Año": "2026", "Mes": "Junio", "HsTrabajadas": "60", "HsVendidas": "60", "HsFacturadas": "60"}],
        proyectos=[{"id": "p1", "TipodeProyecto": "Tiempo & Materiales", "Estado": "En curso"}],
        clientes=[{"id": "c1", "Cliente": "Molinos", "Client Partner": "María López"}],
        facturadas=_facturadas_xlsx([("Otro", 0)]),
    )
    assert _repo().get_cartera_facts("tok")[0]["tipoProyecto"] == "Mantenimiento"


def test_facturadas_zero_when_project_absent_in_xlsx(monkeypatch):
    _patch(
        monkeypatch,
        horas=[{"ProyectoId": "p1", "EmpresaId": "c1", "Proyecto": "SADI",
                "Año": "2026", "Mes": "1", "HsTrabajadas": "10", "HsVendidas": "10", "HsFacturadas": "10"}],
        proyectos=[{"id": "p1", "TipodeProyecto": "Proyecto", "Estado": "En curso"}],
        clientes=[{"id": "c1", "Cliente": "YPF", "Client Partner": "Diego Ramos"}],
        facturadas=_facturadas_xlsx([("Proyecto Distinto", 999)]),
    )
    assert _repo().get_cartera_facts("tok")[0]["facturadasProyecto"] == 0.0


def test_missing_referenced_project_or_client_yields_empty_dims(monkeypatch):
    _patch(
        monkeypatch,
        horas=[{"ProyectoId": "MISSING", "EmpresaId": "MISSING", "Proyecto": "Huérfano",
                "Año": "2026", "Mes": "5", "HsTrabajadas": "5", "HsVendidas": "5", "HsFacturadas": "5"}],
        proyectos=[{"id": "p1", "TipodeProyecto": "Proyecto"}],
        clientes=[{"id": "c1", "Cliente": "YPF"}],
        facturadas=_facturadas_xlsx([("x", 0)]),
    )
    f = _repo().get_cartera_facts("tok")[0]
    assert f["cliente"] == "" and f["clientPartner"] == "" and f["tipoProyecto"] == ""
    assert f["proyecto"] == "Huérfano"  # el título viene de la fila mensual


def test_repo_consumes_all_paged_items(monkeypatch):
    _patch(
        monkeypatch,
        horas=[{"ProyectoId": "p1", "EmpresaId": "c1", "Proyecto": "A", "Año": "2026", "Mes": "1",
                "HsTrabajadas": "1", "HsVendidas": "1", "HsFacturadas": "1"} for _ in range(5)],
        proyectos=[{"id": "p1", "TipodeProyecto": "Proyecto"}],
        clientes=[{"id": "c1", "Cliente": "YPF"}],
        facturadas=_facturadas_xlsx([("A", 3)]),
    )
    assert len(_repo().get_cartera_facts("tok")) == 5


def test_facturadas_table_found_when_not_at_a1_and_not_first_sheet(monkeypatch):
    # Regresión del live-smoke hardening: la tabla se ancla al header real, no
    # a A1/primera hoja (read_only no expone ws.tables). Aún así debe unir.
    _patch(
        monkeypatch,
        horas=[{"ProyectoId": "p1", "EmpresaId": "c1", "Proyecto": "SADI",
                "Año": "2026", "Mes": "1", "HsTrabajadas": "10", "HsVendidas": "10", "HsFacturadas": "10"}],
        proyectos=[{"id": "p1", "TipodeProyecto": "Proyecto", "Estado": "En curso"}],
        clientes=[{"id": "c1", "Cliente": "YPF", "Client Partner": "Diego Ramos"}],
        facturadas=_facturadas_xlsx([("SADI", 290)], lead_rows=3, decoy_sheet=True),
    )
    assert _repo().get_cartera_facts("tok")[0]["facturadasProyecto"] == 290


def test_facturadas_table_missing_header_raises_source_error(monkeypatch):
    bad = _facturadas_xlsx([("SADI", 1)], headers=("Otra Cosa", "Valor"))
    _patch(monkeypatch, horas=[], proyectos=[], clientes=[], facturadas=bad)
    with pytest.raises(ReportingSourceError) as exc:
        _repo().get_cartera_facts("tok")
    assert exc.value.stage == "table"


def test_malformed_workbook_raises_source_error(monkeypatch):
    _patch(
        monkeypatch,
        horas=[], proyectos=[], clientes=[],
        facturadas=b"esto no es un xlsx",
    )
    with pytest.raises(ReportingSourceError) as exc:
        _repo().get_cartera_facts("tok")
    assert exc.value.stage == "workbook"


def test_missing_site_id_raises_configuration_error():
    repo = ReportingRepository(site_id="", horas_proyectos_list_id=_HP,
                               proyectos_list_id=_PROY, cliente_list_id=_CLI)
    with pytest.raises(ReportingConfigurationError):
        repo.get_cartera_facts("tok")


def test_graph_http_error_wrapped(monkeypatch):
    import urllib.error

    def boom(url, access_token=None, **kw):
        raise urllib.error.HTTPError(url, 403, "boom", None, None)

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", boom)
    with pytest.raises(ReportingGraphError) as exc:
        _repo().get_cartera_facts("tok")
    assert exc.value.status_code == 403


def test_client_partner_resolved_from_discovered_lookup_target(monkeypatch):
    # El target del lookup CP se DESCUBRE de /columns (lookup.listId), no se
    # asume; se carga una vez y se mapea id→Title.
    _patch(
        monkeypatch,
        horas=[{"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "1",
                "HsTrabajadas": "1", "HsVendidas": "1", "HsFacturadas": "1"}],
        proyectos=[{"id": "p1", "TipodeProyecto": "Proyecto"}],
        clientes=[{"id": "c1", "Title": "YPF", "ClientPartnerLookupId": "pm9"}],
        cp_columns=_CP_COLUMNS,
        client_partners=[{"id": "pm9", "Title": "Ana Gómez"}],
        facturadas=_facturadas_xlsx([("x", 0)]),
    )
    # Protege el descubrimiento del target de CP (lookup.listId de /columns).
    assert _repo().get_cartera_facts("tok")[0]["clientPartner"] == "Ana Gómez"


def test_client_partner_blank_when_target_not_discoverable_no_guess(monkeypatch):
    # Sin columna lookup ClientPartner en /columns → NO se asume target → CP en
    # blanco (nunca se fabrica).
    _patch(
        monkeypatch,
        horas=[{"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "1",
                "HsTrabajadas": "1", "HsVendidas": "1", "HsFacturadas": "1"}],
        proyectos=[{"id": "p1", "TipodeProyecto": "Proyecto"}],
        clientes=[{"id": "c1", "Title": "YPF", "ClientPartnerLookupId": "pm9"}],
        cp_columns=[{"name": "Title"}],  # sin lookup ClientPartner
        client_partners=[{"id": "pm9", "Title": "Ana Gómez"}],
        facturadas=_facturadas_xlsx([("x", 0)]),
    )
    assert _repo().get_cartera_facts("tok")[0]["clientPartner"] == ""


# --- Service: derivaciones + shaping -----------------------------------------

class _FakeRepo:
    def __init__(self, facts):
        self._facts = facts

    def get_cartera_facts(self, access_token):
        return self._facts


def _mant_fact(**kw):
    base = dict(tipoProyecto="Mantenimiento", cliente="Andesmar", clientPartner="María López",
                proyecto="Mantenimiento Costeo Directo", idProyecto="", estado="En curso",
                anio="2026", mes="Agosto", anioInicio="2026",
                hsTrabajadas=134.0, hsVendidas=140.0, hsCertificadas=128.0, facturadasProyecto=0.0)
    base.update(kw)
    return base


def _proy_fact(**kw):
    base = dict(tipoProyecto="Proyecto", cliente="YPF", clientPartner="Diego Ramos",
                proyecto="Implementación SADI v2", idProyecto="DAN-1042", estado="En curso",
                anio="2026", mes="Enero", anioInicio="2026",
                hsTrabajadas=200.0, hsVendidas=220.0, hsCertificadas=190.0, facturadasProyecto=290.0)
    base.update(kw)
    return base


def test_service_mantenimiento_shape_diff_and_fecha():
    out = ReportingService(_FakeRepo([_mant_fact()])).get_cartera("tok")
    assert out["proyecto"] == []
    m = out["mantenimiento"][0]
    assert m["diff"] == -6                       # 128 - 134
    assert m["facturadas"] == m["certificadas"] == 128   # Mantenimiento: facturadas = certificadas
    assert m["fecha"] == "2026-08-01"            # primer día del mes
    assert m["cp"] == "María López" and m["vendidasMensuales"] == 140 and m["status"] == "En curso"


def test_service_proyecto_aggregates_over_months_facturadas_not_resummed():
    facts = [
        _proy_fact(mes="Enero", hsTrabajadas=200.0, hsVendidas=220.0, hsCertificadas=190.0),
        _proy_fact(mes="Febrero", hsTrabajadas=140.0, hsVendidas=140.0, hsCertificadas=132.0),
    ]
    out = ReportingService(_FakeRepo(facts)).get_cartera("tok")
    assert out["mantenimiento"] == []
    assert len(out["proyecto"]) == 1
    p = out["proyecto"][0]
    assert p["trabajadas"] == 340 and p["certificadas"] == 322 and p["vendidas"] == 360
    assert p["facturadas"] == 290                # XLSX total, NUNCA re-sumado por mes
    assert p["diff"] == -18                       # 322 - 340
    assert p["idProyecto"] == "DAN-1042" and p["anioInicio"] == "2026"


def test_service_never_leaks_internal_field_names():
    out = ReportingService(_FakeRepo([_mant_fact(), _proy_fact()])).get_cartera("tok")
    blob = repr(out)
    for internal in ("HsFacturadas", "TipodeProyecto", "EmpresaId", "ProyectoId", "Suma de Cantidad"):
        assert internal not in blob


# --- Router: auth + éxito + fail-soft localizado -----------------------------

from fastapi.testclient import TestClient  # noqa: E402

from webapp.main import app  # noqa: E402
from webapp.routers import reporting as reporting_router  # noqa: E402
from webapp.services import auth_service  # noqa: E402

auth_service.ALLOWED_USERS = "ALL"
auth_service.OPERATIONS_ALLOWED_USERS = "staffing-test@danwor.com,topbar-full@danwor.com,onedanwor-full@danwor.com"
_SESSION = auth_service.create_session(
    {"email": "staffing-test@danwor.com", "name": "Rep Test", "first_name": "Rep", "family_name": "Test"}
)
_client = TestClient(app)
_client.cookies.set(auth_service.COOKIE_NAME, _SESSION)

_OK_CARTERA = {"mantenimiento": [{"cliente": "YPF"}], "proyecto": []}


class _RecordingReportingService:
    def __init__(self, cartera=None, error=None):
        self._cartera = cartera if cartera is not None else _OK_CARTERA
        self._error = error

    def get_cartera(self, access_token):
        if self._error is not None:
            raise self._error
        return self._cartera


def test_cartera_endpoint_requires_operations_session():
    anon = TestClient(app)
    r = anon.post("/operaciones/reporting/integral/data/cartera", data={"graph_token": "tok"}, follow_redirects=False)
    assert r.status_code == 303


def test_cartera_endpoint_success_returns_normalized(monkeypatch):
    monkeypatch.setattr(reporting_router, "ReportingService", lambda: _RecordingReportingService())
    r = _client.post("/operaciones/reporting/integral/data/cartera", data={"graph_token": "tok"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "ok"
    assert body["cartera"]["mantenimiento"][0]["cliente"] == "YPF"


def test_cartera_endpoint_missing_token():
    r = _client.post("/operaciones/reporting/integral/data/cartera", data={"graph_token": ""})
    assert r.json() == {"state": "missing_token"}


def test_cartera_endpoint_success_response_is_only_state_and_cartera(monkeypatch):
    # Cerrada la investigación: la respuesta ok NO expone diagnostics (internal
    # names) — solo state + cartera normalizada.
    monkeypatch.setattr(reporting_router, "ReportingService", lambda: _RecordingReportingService())
    r = _client.post("/operaciones/reporting/integral/data/cartera", data={"graph_token": "tok"})
    body = r.json()
    assert body["state"] == "ok"
    assert set(body.keys()) == {"state", "cartera"}


def test_cartera_endpoint_source_error_is_localized_fail_soft(monkeypatch):
    monkeypatch.setattr(
        reporting_router, "ReportingService",
        lambda: _RecordingReportingService(error=ReportingSourceError(stage="workbook")),
    )
    r = _client.post("/operaciones/reporting/integral/data/cartera", data={"graph_token": "tok"})
    assert r.status_code == 200  # nunca 5xx: no tumba la página
    assert r.json() == {"state": "source_error", "stage": "workbook"}


def test_cartera_endpoint_missing_config_is_config_incomplete(monkeypatch):
    monkeypatch.setattr(
        reporting_router, "ReportingService",
        lambda: _RecordingReportingService(error=ReportingConfigurationError("site no configurado")),
    )
    r = _client.post("/operaciones/reporting/integral/data/cartera", data={"graph_token": "tok"})
    assert r.status_code == 200
    assert r.json() == {"state": "config_incomplete"}


def test_cartera_endpoint_arbitrary_runtime_error_is_not_config_incomplete(monkeypatch):
    # Un RuntimeError genérico (parseo/join/etc.) NUNCA se lee como config
    # incompleta — es un source_error/unexpected, fail-soft, con log server-side.
    monkeypatch.setattr(
        reporting_router, "ReportingService",
        lambda: _RecordingReportingService(error=RuntimeError("boom en el join")),
    )
    r = _client.post("/operaciones/reporting/integral/data/cartera", data={"graph_token": "tok"})
    assert r.status_code == 200
    assert r.json() == {"state": "source_error", "stage": "unexpected"}
    assert "boom en el join" not in r.text  # nunca el texto crudo al browser


def test_cartera_endpoint_graph_error_exposes_safe_stage_status(monkeypatch):
    monkeypatch.setattr(
        reporting_router, "ReportingService",
        lambda: _RecordingReportingService(error=ReportingGraphError(stage="proyectos", status_code=403)),
    )
    r = _client.post("/operaciones/reporting/integral/data/cartera", data={"graph_token": "tok"})
    assert r.status_code == 200
    assert r.json() == {"state": "source_error", "stage": "proyectos", "http_status": 403}


def test_cartera_endpoint_never_echoes_token(monkeypatch):
    secret = "eyJ-super-secret-token"
    monkeypatch.setattr(reporting_router, "ReportingService", lambda: _RecordingReportingService())
    r = _client.post("/operaciones/reporting/integral/data/cartera", data={"graph_token": secret})
    assert secret not in r.text


# --- Browser wiring (source-seam, sin runtime JS) ----------------------------

from pathlib import Path  # noqa: E402

_REPORTING_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")


def test_cartera_fetches_the_backend_endpoint():
    assert '"/operaciones/reporting/integral/data/cartera"' in _REPORTING_JS
    assert 'sessionStorage.getItem("dw-graph-token")' in _REPORTING_JS


def test_cartera_populates_existing_arrays_in_place_not_reassign():
    # Preserva el motor: muta los arrays que CARTERA_CONFIG referencia.
    load_block = _REPORTING_JS[_REPORTING_JS.index("async function loadCarteraData"):_REPORTING_JS.index("function renderCarteraDataState")]
    assert "replaceRows(CARTERA_MANTENIMIENTO_ROWS, data.cartera.mantenimiento)" in load_block
    assert "replaceRows(CARTERA_PROYECTO_ROWS, data.cartera.proyecto)" in load_block
    assert "state.carteraAnioInicio = carteraDefaultAnioInicio()" in load_block  # re-deriva default real


def test_cartera_render_is_gated_by_data_status():
    render_block = _REPORTING_JS[_REPORTING_JS.index("function renderCartera()"):_REPORTING_JS.index("const stateKey")]
    assert 'if (state.carteraDataStatus === "idle") loadCarteraData();' in render_block
    assert 'if (state.carteraDataStatus !== "ready") return renderCarteraDataState();' in render_block


def test_cartera_failure_is_localized_fail_soft():
    load_block = _REPORTING_JS[_REPORTING_JS.index("async function loadCarteraData"):_REPORTING_JS.index("function renderCarteraDataState")]
    assert 'state.carteraDataStatus = "error"' in load_block
    assert "catch (e)" in load_block
    # El estado de error de Cartera NO menciona otras experiencias como rotas.
    assert "siguen disponibles" in _REPORTING_JS


def test_filter_bar_hidden_until_data_ready():
    fb = _REPORTING_JS[_REPORTING_JS.index("function renderCarteraFilterBar()"):_REPORTING_JS.index("function renderCartera()")]
    assert 'if (state.carteraDataStatus !== "ready") return "";' in fb


# ============================================================================
# Phase 0 — hardening transversal (loader / geometría de matriz / formateo KPI)
# ============================================================================

_REPORTING_CSS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.css").read_text(encoding="utf-8")


def test_loader_reuses_shared_hub_primitive_not_a_reporting_copy():
    # 0A: Reporting reusa el MISMO primitivo de Asignaciones, no un empty-panel.
    assert 'reportingLoadingHtml("Cargando datos de Cartera' in _REPORTING_JS
    helper = _REPORTING_JS[_REPORTING_JS.index("function reportingLoadingHtml"):_REPORTING_JS.index("function reportingSourceErrorHtml")]
    assert "section-analysis-loading" in helper and "section-analysis-spinner" in helper


def test_semantic_number_formatter_exists_and_kpis_use_it():
    # 0C: fmtNum semántico; los tiles KPI ya no renderizan valores crudos.
    assert "function fmtNum(value, type" in _REPORTING_JS
    assert "Intl.NumberFormat" in _REPORTING_JS
    assert 'activity-kpi__value">${fmtNum(' in _REPORTING_JS
    # diffCell usa el formateo con signo (sin ruido de punto flotante).
    diff = _REPORTING_JS[_REPORTING_JS.index("function diffCell"):_REPORTING_JS.index("function diffPlain")]
    assert 'fmtNum(diff, "signed")' in diff


def test_matrix_geometry_is_fixed_layout_for_stable_columns():
    # 0B: table-layout: fixed ⇒ expand/collapse no mueve columnas.
    assert "table.tree-matrix { width: 100%; border-collapse: collapse; font-size: .76rem; min-width: 900px; table-layout: fixed; }" in _REPORTING_CSS


def test_kpi_tile_has_overflow_safe_styles():
    # 0C: tiles no se solapan con valores de 5–7 dígitos.
    assert ".activity-kpi { min-width: 0; }" in _REPORTING_CSS
    assert "white-space: nowrap; font-variant-numeric: tabular-nums" in _REPORTING_CSS
