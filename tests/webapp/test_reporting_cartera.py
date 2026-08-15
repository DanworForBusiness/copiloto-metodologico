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
_TOOLS_CSS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")


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
    # 0B, ahora del primitivo compartido `.dashboard-table` (Phase S, ver
    # HANDOFF): table-layout: fixed ⇒ expand/collapse no mueve columnas.
    # Reporting consume la clase compartida (reporting.js) + su propio
    # override de ancho (reporting.css, min-width de la matriz mensual).
    assert "table.dashboard-table { width: 100%; border-collapse: collapse; font-size: .76rem; table-layout: fixed; }" in _TOOLS_CSS
    assert 'class="dashboard-table"' in _REPORTING_JS
    assert ".reporting-integral-preview table.dashboard-table { min-width: 900px; }" in _REPORTING_CSS


def test_kpi_tile_has_overflow_safe_styles():
    # 0C, ahora del primitivo compartido `.activity-kpi`/`.activity-kpi__value`
    # (Phase S, ver HANDOFF): tiles no se solapan con valores de 5–7 dígitos
    # en NINGÚN consumidor (Asignaciones/Reporting/futuro Activity), no solo
    # Reporting.
    assert ".activity-kpi { min-width: 0;" in _TOOLS_CSS
    assert "white-space: nowrap; font-variant-numeric: tabular-nums" in _TOOLS_CSS


# ============================================================================
# Q1 — Rentabilidad / Certificadas vs Ejecutadas / Detalle por proyecto
# (owner live QA: Tipo/Proyecto comprimidas, columnas numéricas colisionan)
# ============================================================================

def test_rentabilidad_cert_ejec_detail_uses_compact_role_not_a_pixel_hack():
    # Tipo (categórico corto) usa el rol GENÉRICO `--compact` del vocabulario
    # compartido — Proyecto (descriptivo) sigue `.left` sin ancho fijo, así
    # absorbe el espacio remanente en vez de repartirlo en partes iguales con
    # Tipo. Nunca un ancho puntual/inline de Rentabilidad.
    detail_start = _REPORTING_JS.index("function renderRentabilidadCertEjecutadas")
    detail_body = _REPORTING_JS[detail_start:detail_start + 3000]
    assert '<th class="left dashboard-table-col--compact">Tipo</th><th class="left">Proyecto</th>' in detail_body
    assert "style=" not in detail_body[detail_body.index("<thead>"):detail_body.index("</thead>") + 8]
    assert "table.dashboard-table th.dashboard-table-col--compact { width: 100px; }" in _TOOLS_CSS


def test_dashboard_table_header_wraps_instead_of_colliding():
    # Un header numérico más largo que su columna fija de 88px (ej. "Margen
    # Relativo") ya no desborda sobre la columna vecina — el primitivo
    # compartido permite wrap a 2 líneas (geometría estable, sin JS de
    # medición). Cambio TRANSVERSAL en `.dashboard-table`, no un ajuste
    # puntual de Rentabilidad.
    assert "white-space: normal; line-height: 1.15;" in _TOOLS_CSS
    header_rule = _TOOLS_CSS[_TOOLS_CSS.index("table.dashboard-table th {"):_TOOLS_CSS.index("table.dashboard-table th {") + 400]
    assert "white-space: nowrap;" not in header_rule


# ============================================================================
# Corrective pre-Batch-A (owner live QA — Cartera es la plantilla canónica)
# ============================================================================

def test_cartera_estado_terminal_column_uses_shared_status_modifier():
    # S.1: Estado es la ÚLTIMA columna en ambas perspectivas de Cartera — usa
    # el primitivo compartido `.dashboard-table-col--status`/`--cell--status`
    # (tools.css), NUNCA un parche visual de un solo uso
    # (`.reporting-status-last` o similar). El modificador es OPCIONAL
    # (`c.statusTerminal`) — Asignaciones (Estado en el medio de su tabla) no
    # lo usa y sigue con el comportamiento `.left` default.
    assert "statusTerminal: true" in _REPORTING_JS
    assert '"dashboard-table-col--status" : (c.numeric ? "" : "left")' in _REPORTING_JS
    assert '"dashboard-table-cell--status" : (c.numeric ? "" : "left")' in _REPORTING_JS
    assert "table.dashboard-table th.dashboard-table-col--status { text-align: right;" in _TOOLS_CSS
    assert "table.dashboard-table td.dashboard-table-cell--status { text-align: right; }" in _TOOLS_CSS
    assert ".reporting-status-last" not in _TOOLS_CSS and ".reporting-status-last" not in _REPORTING_CSS
    # Ancho estándar de columna numérica restaurado (regresión de Phase S:
    # se perdió al fusionar tree-matrix/data-table) — sin esto, Estado
    # terminal comparte el ancho ancho de las columnas de medida.
    assert "table.dashboard-table th:not(.left):not(.row-label-col) { width: 88px; }" in _TOOLS_CSS


def test_cartera_asignaciones_status_column_placement_is_not_globally_forced():
    # S.1 (regla explícita del owner): el modificador de status compacto/
    # derecha NUNCA se fuerza globalmente — Asignaciones sigue usando
    # `class="left"` para su columna Estado (en el medio de la tabla, no
    # terminal), no `dashboard-table-col--status`.
    from pathlib import Path
    staffing_html = (
        Path(__file__).resolve().parents[2] / "webapp" / "templates" / "operations" / "staffing_dashboard.html"
    ).read_text(encoding="utf-8")
    assert '<th class="left">Estado</th>' in staffing_html
    assert "dashboard-table-col--status" not in staffing_html


def test_cartera_proyecto_hierarchy_shows_business_description_not_id():
    # S.2: la jerarquía de Cartera/Proyecto YA NO tiene a `idProyecto` como
    # nivel visible (mostraba el ID técnico, ej. "PROJ-0206", en vez de la
    # descripción de negocio que el filtro Proyecto sí muestra) — Cliente ›
    # Proyecto (2 niveles). `idProyecto` sigue en cada fila como campo
    # interno (join key / desambiguación), nunca se descartó del dato.
    proyecto_block = _REPORTING_JS[_REPORTING_JS.index('"Proyecto": {'):_REPORTING_JS.index('"Proyecto": {') + 900]
    assert 'keys: ["cliente", "proyecto"]' in proyecto_block
    assert 'labels: ["Cliente", "Proyecto"]' in proyecto_block
    assert '"idProyecto"' not in proyecto_block.split("keys:")[1].split("\n")[0]
    # El facet/filtro de Proyecto sigue usando el mismo campo `proyecto`
    # (descripción de negocio) que la jerarquía — dropdown y tabla nunca
    # divergen.
    assert 'facetSelectField("cartera", "proyecto", "Proyecto")' in _REPORTING_JS


def test_cartera_diff_hs_kpi_recomputes_from_filtered_leaf_rows_not_double_formatted():
    # S.3: `carteraKpiTiles` NO debe pasar `col.rollup(rows)` (HTML de
    # `diffCell`, pensado para la celda de tabla) por `fmtNum` de nuevo —
    # eso da NaN → "—" aunque el detalle filtrado tenga valores reales. El
    # tile recalcula el número CRUDO con el mismo `sumField` aditivo
    # (SUM(diffs) == SUM(Certificadas) − SUM(Trabajadas)) y lo formatea
    # directo con el tipo semántico "signed".
    tiles_fn = _REPORTING_JS[_REPORTING_JS.index("function carteraKpiTiles"):_REPORTING_JS.index("function carteraKpiTiles") + 900]
    assert 'k.key === "diff" ? fmtNum(sumField("diff")(rows), "signed")' in tiles_fn
    # Patrón viejo (rollup de diff, ya HTML, vuelto a pasar por fmtNum como
    # si fuera un número) no debe quedar.
    assert "fmtNum(col.rollup(rows), \"hours\")" not in tiles_fn.replace(
        'k.key === "diff" ? fmtNum(sumField("diff")(rows), "signed") : fmtNum(col.rollup(rows), "hours")', ""
    )


def test_hierarchy_disclosure_is_one_shared_helper_not_duplicated_inline():
    # S.4: UN solo helper (`hierarchyToggleHtml`) genera el control de
    # disclosure para AMBOS renderers de árbol (renderTreeRows/
    # renderBusinessTreeRows) — nunca dos bloques de markup casi-idénticos
    # copiados a mano (el estado previo a este corrective).
    assert _REPORTING_JS.count("function hierarchyToggleHtml") == 1
    assert _REPORTING_JS.count("hierarchyToggleHtml(stateKey, node.pathKey, node.label, hasChildren, isOpen, caretChar)") == 2
    # El markup inline viejo (dos <span class="dashboard-table-toggle"> con
    # el mismo onclick repetido) no debe quedar.
    assert '<span class="dashboard-table-toggle" ${hasChildren' not in _REPORTING_JS


def test_hierarchy_disclosure_control_has_children_is_a_real_accessible_button():
    # S.4 (owner live QA): clic en el triángulo O en el label deben disparar
    # el MISMO toggle — un solo control, no dos manejadores separados que
    # puedan doble-disparar. `<button>` real ⇒ foco/teclado (Enter/Space)
    # gratis del browser, `aria-expanded` sincronizado con el estado.
    helper_fn = _REPORTING_JS[_REPORTING_JS.index("function hierarchyToggleHtml"):_REPORTING_JS.index("function hierarchyToggleHtml") + 900]
    assert '<button type="button" class="dashboard-table-toggle"' in helper_fn
    assert "onclick=\"toggleTreeNode(" in helper_fn
    assert 'aria-expanded="${isOpen}"' in helper_fn


def test_hierarchy_leaf_rows_are_not_falsely_expandable():
    # S.4 (owner live QA: "leaf rows must not appear expandable"): sin
    # hijos, el control es un `<span>` NO interactivo — nunca un `<button>`,
    # nunca un `onclick`, nunca affordance de puntero engañosa.
    helper_fn = _REPORTING_JS[_REPORTING_JS.index("function hierarchyToggleHtml"):_REPORTING_JS.index("function hierarchyToggleHtml") + 900]
    assert '<span class="dashboard-table-toggle dashboard-table-toggle--leaf">' in helper_fn
    assert ".dashboard-table-toggle.dashboard-table-toggle--leaf { cursor: default; }" in _TOOLS_CSS
    assert "button.dashboard-table-toggle { cursor: pointer; }" in _TOOLS_CSS


def test_fmt_num_distinguishes_zero_from_missing():
    # Validación explícita del owner: 0 es un resultado numérico válido
    # (debe renderizar "0"), el em dash es SOLO para null/undefined/"".
    fmt_num_fn = _REPORTING_JS[_REPORTING_JS.index("function fmtNum"):_REPORTING_JS.index("function fmtNum") + 400]
    assert 'if (value === null || value === undefined || value === "") return "—";' in fmt_num_fn
    # 0 no cae en ninguna de esas tres ramas — sigue de largo hasta el
    # formateo real (Number(0) es finito, nunca dispara el fallback "—").


# ============================================================================
# Año de Inicio facet respeta Estado + otros filtros (corrective multidireccional)
# ============================================================================

def test_anio_inicio_options_are_faceted_through_the_shared_engine():
    block = _REPORTING_JS[_REPORTING_JS.index("function carteraAnioInicioOptions"):_REPORTING_JS.index("function carteraDefaultAnioInicio")]
    # Reusa el motor faceteado (facetRowsExcept) sobre las filas ya filtradas por
    # Estado (carteraStatus) — NUNCA distinctValues sobre las filas crudas.
    assert "facetRowsExcept(statusRows, state.filters.cartera" in block
    assert "CARTERA_PROYECTO_ROWS.filter(r => state.carteraStatus[r.status])" in block
    assert "distinctValues(CARTERA_PROYECTO_ROWS" not in block  # ya no ignora Estado


def test_stale_anio_inicio_is_pruned_on_facet_change():
    # Al cambiar Estado u otro facet, se reconcilia la selección obsoleta de año.
    assert "function pruneCarteraAnioInicio()" in _REPORTING_JS
    assert 'if (expId === "cartera") pruneCarteraAnioInicio();' in _REPORTING_JS
    prune = _REPORTING_JS[_REPORTING_JS.index("function pruneCarteraAnioInicio"):]
    prune = prune[:prune.index("\n}")]
    assert "available.has(y)" in prune  # conserva solo los válidos, nunca reselecciona
