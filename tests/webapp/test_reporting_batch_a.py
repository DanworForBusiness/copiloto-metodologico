"""
test_reporting_batch_a.py — Contrato de datos reales de Batch A (Horas
Vendidas / Participación / Calendario / Salud de Cliente, Reporting
Integral real-data — backbone reuse de Cartera). Determinístico, sin Graph
real: monkeypatchea `core.microsoft365` (mismo criterio que
test_reporting_cartera.py). Horas Productivas (AMBAS perspectivas) es
Batch B (CSV de Time Logs, fuente distinta — ver test_reporting_batch_b.py).
"""
from __future__ import annotations

from webapp.services import reporting_repository as repo_mod
from webapp.services.reporting_repository import ReportingRepository
from webapp.services.reporting_service import ReportingService

_SITE, _HP, _PROY, _CLI, _CP = "site-1", "hp-list", "proy-list", "cli-list", "cp-list"


def _items(rows):
    return [{"id": r.get("id", ""), "fields": r} for r in rows]


def _patch(monkeypatch, *, horas, proyectos, clientes, cp_columns=None, client_partners=None):
    def fake_paged(url, access_token=None, **kw):
        if "/columns" in url:
            return cp_columns if cp_columns is not None else []
        if _CP in url:
            return _items(client_partners or [])
        if _HP in url:
            return _items(horas)
        if _PROY in url:
            return _items(proyectos)
        if _CLI in url:
            return _items(clientes)
        return []

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)


_CP_COLUMNS = [{"name": "Title"}, {"name": "ClientPartner", "lookup": {"listId": _CP}}]


def _repo():
    return ReportingRepository(
        site_id=_SITE, horas_proyectos_list_id=_HP,
        proyectos_list_id=_PROY, cliente_list_id=_CLI,
    )


def _service():
    return ReportingService(_repo())


# ============================================================================
# Repositorio — un solo fetch consolidado
# ============================================================================

def test_get_batch_a_facts_joins_project_facts_and_cliente_dim(monkeypatch):
    _patch(
        monkeypatch,
        horas=[{"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Marzo",
                "HsTrabajadas": "10", "HsVendidas": "12", "HsFacturadas": "11"}],
        proyectos=[{"id": "p1", "Title": "Implementación SADI v2", "TipodeProyecto": "Proyecto"}],
        clientes=[{"id": "c1", "Title": "YPF", "ClientPartnerLookupId": "pm1",
                   "Frecuencia_x0020_Actualizacion": "Mensual", "Permite_x0020_Adelanto": "Si",
                   "Condicion_x0020_de_x0020_Pago": "30", "Calendario_Act": "Marzo, Julio"}],
        cp_columns=_CP_COLUMNS,
        client_partners=[{"id": "pm1", "Title": "Diego Ramos"}],
    )
    raw = _repo().get_batch_a_facts("tok")
    assert len(raw["project_facts"]) == 1
    pf = raw["project_facts"][0]
    assert pf["cliente"] == "YPF" and pf["clientPartner"] == "Diego Ramos" and pf["clienteId"] == "c1"
    assert pf["proyecto"] == "Implementación SADI v2" and pf["tipoProyecto"] == "Proyecto"
    assert pf["hsVendidas"] == 12

    assert len(raw["cliente_dim"]) == 1
    dim = raw["cliente_dim"][0]
    assert dim["cliente"] == "YPF" and dim["clientPartner"] == "Diego Ramos"
    assert dim["frecuenciaActualizacion"] == "Mensual"
    assert dim["permiteAdelanto"] == "Si" and dim["condicionPago"] == "30"
    assert dim["calendarioAct"] == "Marzo, Julio"


# ============================================================================
# Horas Vendidas — aditivo por mes, agrupado por CP/Tipo/Cliente/Proyecto
# ============================================================================

def test_horas_vendidas_sums_monthly_values_at_the_right_index(monkeypatch):
    _patch(
        monkeypatch,
        horas=[
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Marzo",
             "HsVendidas": "15", "HsTrabajadas": "0", "HsFacturadas": "0"},
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Julio",
             "HsVendidas": "20", "HsTrabajadas": "0", "HsFacturadas": "0"},
        ],
        proyectos=[{"id": "p1", "Title": "Rollout BI", "TipodeProyecto": "Proyecto"}],
        clientes=[{"id": "c1", "Title": "Molinos", "ClientPartnerLookupId": "pm1"}],
        cp_columns=_CP_COLUMNS,
        client_partners=[{"id": "pm1", "Title": "María López"}],
    )
    rows = _service().get_horas_vendidas("tok")
    assert len(rows) == 1
    r = rows[0]
    assert r["cp"] == "María López" and r["tipo"] == "Proyecto" and r["cliente"] == "Molinos" and r["proyecto"] == "Rollout BI"
    assert r["monthly"][2] == 15  # Marzo == índice 2
    assert r["monthly"][6] == 20  # Julio == índice 6
    assert sum(r["monthly"]) == 35
    assert all(v == 0 for i, v in enumerate(r["monthly"]) if i not in (2, 6))
    assert r["fecha"] == "2026-03-01"  # primer mes con datos


def test_horas_vendidas_groups_distinct_projects_separately(monkeypatch):
    _patch(
        monkeypatch,
        horas=[
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Enero", "HsVendidas": "5"},
            {"ProyectoLookupId": "p2", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Enero", "HsVendidas": "7"},
        ],
        proyectos=[
            {"id": "p1", "Title": "Proyecto Uno", "TipodeProyecto": "Proyecto"},
            {"id": "p2", "Title": "Proyecto Dos", "TipodeProyecto": "Tiempo & Materiales"},
        ],
        clientes=[{"id": "c1", "Title": "Andesmar"}],
    )
    rows = _service().get_horas_vendidas("tok")
    assert len(rows) == 2
    by_proyecto = {r["proyecto"]: r for r in rows}
    assert by_proyecto["Proyecto Uno"]["tipo"] == "Proyecto" and by_proyecto["Proyecto Uno"]["monthly"][0] == 5
    assert by_proyecto["Proyecto Dos"]["tipo"] == "Mantenimiento" and by_proyecto["Proyecto Dos"]["monthly"][0] == 7


# ============================================================================
# Participación — numerador crudo por Cliente (el % se calcula en el frontend
# contra el gran total, ver comercialPctVendidas/comercialPctCertificadas)
# ============================================================================

def test_participacion_sums_vendidas_certificadas_across_all_projects_and_tipos(monkeypatch):
    _patch(
        monkeypatch,
        horas=[
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Enero",
             "HsVendidas": "10", "HsFacturadas": "8"},
            {"ProyectoLookupId": "p2", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Febrero",
             "HsVendidas": "5", "HsFacturadas": "4"},
        ],
        proyectos=[
            {"id": "p1", "Title": "Mant", "TipodeProyecto": "Tiempo & Materiales"},
            {"id": "p2", "Title": "Proy", "TipodeProyecto": "Proyecto"},
        ],
        clientes=[{"id": "c1", "Title": "YPF", "ClientPartnerLookupId": "pm1",
                   "Frecuencia_x0020_Actualizacion": "Trimestral", "Permite_x0020_Adelanto": "Si",
                   "Condicion_x0020_de_x0020_Pago": "15"}],
        cp_columns=_CP_COLUMNS,
        client_partners=[{"id": "pm1", "Title": "Diego Ramos"}],
    )
    rows = _service().get_participacion("tok")
    assert len(rows) == 1
    r = rows[0]
    # Suma SIN distinguir tipo (Mantenimiento + Proyecto) — % Hs Vendidas 2 /
    # % Hs Certificadas 2 no filtran por tipo en el PBIP fuente.
    assert r["horasVendidas"] == 15 and r["horasCertificadas"] == 12
    assert r["idFrecuencia"] == "3 Trimestral"  # derivado: índice+1 en FRECUENCIA_ORDER
    assert r["permiteAdelanto"] == "Si" and r["condicionPago"] == "15"
    assert r["cliente"] == "YPF" and r["cp"] == "Diego Ramos"


def test_participacion_omits_clientes_without_any_project_facts(monkeypatch):
    # Un Cliente en la dimensión sin ningún hecho de Horas por proyectos no
    # aporta participación medible — se omite, nunca una fila 0/0 fabricada.
    _patch(
        monkeypatch,
        horas=[],
        proyectos=[],
        clientes=[{"id": "c1", "Title": "SinProyectos", "Frecuencia_x0020_Actualizacion": "Mensual"}],
    )
    assert _service().get_participacion("tok") == []


# ============================================================================
# Salud de Cliente — categórico puro, internal names REALES dados por el
# owner (diagnóstico Graph en vivo), NUNCA agregación numérica.
# ============================================================================

def test_salud_reads_the_six_categorical_fields_verbatim(monkeypatch):
    _patch(
        monkeypatch,
        horas=[], proyectos=[],
        clientes=[{"id": "c1", "Title": "Andesmar", "ClientPartnerLookupId": "pm1",
                   "Salud_x0020_Financiera": "Verde", "Salud_x0020_Relaci_x00f3_n": "Amarillo",
                   "Salud_x0020_Operativo": "Verde", "Salud_x0020_Continuidad": "Rojo",
                   "Salud_x0020_Estrategico": "Verde", "Salud": "Amarillo"}],
        cp_columns=_CP_COLUMNS,
        client_partners=[{"id": "pm1", "Title": "María López"}],
    )
    rows = _service().get_salud("tok")
    assert rows == [{
        "cp": "María López", "cliente": "Andesmar",
        "saludFinanciera": "Verde", "saludRelacion": "Amarillo", "saludOperativo": "Verde",
        "saludContinuidad": "Rojo", "saludEstrategico": "Verde", "saludFinal": "Amarillo",
    }]


def test_salud_omits_clientes_without_a_name(monkeypatch):
    _patch(monkeypatch, horas=[], proyectos=[], clientes=[{"id": "c1", "Title": ""}])
    assert _service().get_salud("tok") == []


# ============================================================================
# Calendario — presencia BOOLEANA por mes, nunca conteo
# ============================================================================

def test_calendario_marks_only_months_present_in_calendario_act(monkeypatch):
    _patch(
        monkeypatch,
        horas=[],
        proyectos=[],
        clientes=[{"id": "c1", "Title": "Andesmar", "ClientPartnerLookupId": "pm1",
                   "Frecuencia_x0020_Actualizacion": "Semanal", "Calendario_Act": "Marzo, Julio, Noviembre"}],
        cp_columns=_CP_COLUMNS,
        client_partners=[{"id": "pm1", "Title": "María López"}],
    )
    rows = _service().get_calendario("tok")
    assert len(rows) == 1
    r = rows[0]
    assert r["frecuencia"] == "Semanal" and r["cliente"] == "Andesmar" and r["cp"] == "María López"
    present = {i for i, v in enumerate(r["monthly"]) if v == 1}
    assert present == {2, 6, 10}  # Marzo=2, Julio=6, Noviembre=10
    # Booleano estricto: nunca > 1 aunque el texto repita un mes.
    assert set(r["monthly"]) <= {0, 1}


def test_calendario_normalizes_frecuencia_casing_to_canonical_label(monkeypatch):
    _patch(
        monkeypatch,
        horas=[], proyectos=[],
        clientes=[{"id": "c1", "Title": "Danone", "Frecuencia_x0020_Actualizacion": "trimestral", "Calendario_Act": "Enero"}],
    )
    rows = _service().get_calendario("tok")
    assert rows[0]["frecuencia"] == "Trimestral"  # capitalización canónica, no la cruda


def test_calendario_omits_clientes_without_recognizable_frecuencia(monkeypatch):
    _patch(
        monkeypatch,
        horas=[], proyectos=[],
        clientes=[{"id": "c1", "Title": "SinFrecuencia", "Frecuencia_x0020_Actualizacion": "", "Calendario_Act": "Enero"}],
    )
    assert _service().get_calendario("tok") == []


# ============================================================================
# Frontend — ciclo de vida real-data + protección del primitivo compartido
# (source-seam, sin runtime JS — mismo criterio que test_reporting_cartera.py)
# ============================================================================

from pathlib import Path  # noqa: E402

_REPORTING_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")


def test_batch_a_endpoint_and_lifecycle_gate_all_four_renderers():
    assert '"/operaciones/reporting/integral/data/batch-a"' in _REPORTING_JS
    assert "function loadBatchAData" in _REPORTING_JS
    assert "function renderBatchADataState" in _REPORTING_JS
    # Los 4 renderers gatean con el MISMO status — nunca un ciclo de vida
    # paralelo por experiencia. Horas Productivas usa su PROPIO status
    # (batchBDataStatus, ver test_reporting_batch_b.py) — Batch B, no Batch A.
    for fn in ("renderHorasVendidas", "renderComercialParticipacion", "renderCalendario", "renderComercialSalud"):
        body = _REPORTING_JS[_REPORTING_JS.index(f"function {fn}("):_REPORTING_JS.index(f"function {fn}(") + 400]
        assert 'state.batchADataStatus === "idle"' in body
        assert "loadBatchAData()" in body


def test_batch_a_never_mixes_mock_and_real_data():
    # `replaceRows` puebla EN EL LUGAR los mismos arrays que ya usa
    # FACET_CONFIG — nunca un dataset paralelo para datos reales.
    load_fn = _REPORTING_JS[_REPORTING_JS.index("async function loadBatchAData"):_REPORTING_JS.index("async function loadBatchAData") + 1000]
    assert "replaceRows(HORASVENDIDAS_ROWS, data.horasVendidas)" in load_fn
    assert "replaceRows(COMERCIAL_PARTICIPACION_ROWS, data.participacion)" in load_fn
    assert "replaceRows(CALENDARIO_ROWS, data.calendario)" in load_fn
    assert "replaceRows(COMERCIAL_SALUD_ROWS, data.salud)" in load_fn
    assert "data.horasProductivas" not in load_fn  # Batch B, no Batch A


def test_capacidad_productivas_denominator_recomputes_not_a_stale_constant():
    # Mismo corrective que Participación: `CAPACIDAD_PRODUCTIVAS_TOTAL` era
    # una `const` calculada UNA vez contra el mock — ahora es una función,
    # recalculada contra el array vigente (mock o real).
    assert "function capacidadProductivasTotal()" in _REPORTING_JS
    assert "const CAPACIDAD_PRODUCTIVAS_TOTAL =" not in _REPORTING_JS


def test_comercial_participacion_denominator_recomputes_not_a_stale_constant():
    # Corrective (ver HANDOFF): antes de Batch A, el gran total de
    # Participación era una `const` calculada UNA vez al cargar el script
    # contra el mock — tras `replaceRows` habría quedado stale para
    # siempre. Ahora son funciones, recalculadas contra el array ACTUAL.
    assert "function comercialParticipacionTotalVendidas()" in _REPORTING_JS
    assert "function comercialParticipacionTotalCertificadas()" in _REPORTING_JS
    assert "const COMERCIAL_PARTICIPACION_TOTAL_VENDIDAS =" not in _REPORTING_JS
    assert "const COMERCIAL_PARTICIPACION_TOTAL_CERTIFICADAS =" not in _REPORTING_JS


def test_batch_a_renderers_use_shared_dashboard_table_not_a_local_variant():
    # Ninguna de las 4 experiencias reconectadas debe introducir una clase
    # de tabla/status/hierarchy propia — todas siguen consumiendo el
    # primitivo compartido de Phase S.
    for fn in ("renderHorasVendidas", "renderComercialParticipacion", "renderCalendario", "renderComercialSalud"):
        body = _REPORTING_JS[_REPORTING_JS.index(f"function {fn}("):_REPORTING_JS.index(f"function {fn}(") + 3300]
        assert 'class="dashboard-table"' in body
