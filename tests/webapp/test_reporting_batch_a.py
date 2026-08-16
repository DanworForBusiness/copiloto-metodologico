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


def test_get_batch_a_fetches_backbone_exactly_once_not_four_times(monkeypatch):
    # PERFORMANCE CORRECTIVE (owner live QA — Horas Vendidas >40s, ver
    # HANDOFF): ANTES `get_batch_a()` llamaba `get_batch_a_facts` UNA vez
    # POR experiencia (4 veces total) — un solo POST a /data/batch-a
    # disparaba 4x la latencia real de Graph. Ahora es UN fetch, shaping
    # puro de Python por separado.
    calls = {"count": 0}
    real_get_batch_a_facts = ReportingRepository.get_batch_a_facts

    def counted(self, access_token):
        calls["count"] += 1
        return real_get_batch_a_facts(self, access_token)

    monkeypatch.setattr(ReportingRepository, "get_batch_a_facts", counted)
    _patch(
        monkeypatch,
        horas=[{"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Marzo",
                "HsTrabajadas": "10", "HsVendidas": "12", "HsFacturadas": "11"}],
        proyectos=[{"id": "p1", "Title": "SADI v2", "TipodeProyecto": "Proyecto"}],
        clientes=[{"id": "c1", "Title": "YPF", "Frecuencia_x0020_Actualizacion": "Mensual", "Calendario_Act": "Marzo"}],
    )
    result = _service().get_batch_a("tok")
    assert calls["count"] == 1
    # Horas Vendidas Y Participación SALIERON de get_batch_a (scope
    # decoupling, owner directive) — ambas se sirven aparte con ventana
    # temporal propia (get_horas_vendidas/get_participacion).
    assert set(result.keys()) == {"calendario", "salud"}
    assert len(result["calendario"]) == 1
    assert len(result["salud"]) == 1


def test_get_batch_a_never_touches_time_logs_or_facturadas_xlsx(monkeypatch):
    # P7 (owner live QA de performance, Horas Vendidas >40s): Batch A (Horas
    # Vendidas/Participación/Calendario/Salud) es 100% backbone de Cartera
    # (listas SharePoint) — NUNCA debe tocar Time Logs (CSV, Batch B) ni la
    # XLSX de Facturadas (Cartera/Proyecto). "Batch A" es agrupamiento de
    # HISTORIA de desarrollo, no un límite de runtime real: esta prueba
    # confirma que ningún llamado cruza esa frontera.
    def boom_time_logs(self, access_token):
        raise AssertionError("get_batch_a nunca debe invocar Time Logs")

    def boom_facturadas(self, site_id):
        raise AssertionError("get_batch_a nunca debe invocar la XLSX de Facturadas")

    monkeypatch.setattr(ReportingRepository, "get_time_logs_facts", boom_time_logs)
    monkeypatch.setattr(ReportingRepository, "_facturadas_by_project", boom_facturadas)
    _patch(
        monkeypatch,
        horas=[{"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Marzo",
                "HsTrabajadas": "10", "HsVendidas": "12", "HsFacturadas": "11"}],
        proyectos=[{"id": "p1", "Title": "SADI v2", "TipodeProyecto": "Proyecto"}],
        clientes=[{"id": "c1", "Title": "YPF", "Frecuencia_x0020_Actualizacion": "Mensual", "Calendario_Act": "Marzo"}],
    )
    result = _service().get_batch_a("tok")
    assert set(result.keys()) == {"calendario", "salud"}


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


def test_horas_vendidas_projects_estado_from_project_status(monkeypatch):
    # Paso 1 de la migración Año/Mes/Estado (HANDOFF §-16 #8): el shape proyecta
    # `estado` desde Proyectos.Status (via el join ya resuelto) — insumo del futuro
    # facet Estado. Aditivo: no altera monthly/cp/tipo/cliente/proyecto.
    _patch(
        monkeypatch,
        horas=[{"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Enero", "HsVendidas": "5"}],
        proyectos=[{"id": "p1", "Title": "Rollout BI", "TipodeProyecto": "Proyecto", "Status": "En curso"}],
        clientes=[{"id": "c1", "Title": "Molinos"}],
    )
    rows = _service().get_horas_vendidas("tok")
    assert len(rows) == 1 and rows[0]["estado"] == "En curso"


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
# Participación — numerador crudo por Cliente Y AÑO (Año MULTI-SELECT +
# hot/cold, owner directive — la suma sobre los años tildados/el % contra el
# gran total se calculan en el FRONTEND, ver comercialParticipacionSelectedRows/
# comercialPctVendidas/comercialPctCertificadas)
# ============================================================================

def test_participacion_sums_vendidas_certificadas_by_year_across_all_tipos(monkeypatch):
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
    rows = _service().get_participacion("tok", anios=["2026"])
    assert len(rows) == 1
    r = rows[0]
    # Suma SIN distinguir tipo (Mantenimiento + Proyecto) — % Hs Vendidas 2 /
    # % Hs Certificadas 2 no filtran por tipo en el PBIP fuente — POR AÑO.
    assert r["vendidasByYear"] == {"2026": 15} and r["certificadasByYear"] == {"2026": 12}
    assert r["idFrecuencia"] == "3 Trimestral"  # derivado: índice+1 en FRECUENCIA_ORDER
    assert r["permiteAdelanto"] == "Si" and r["condicionPago"] == "15"
    assert r["cliente"] == "YPF" and r["cp"] == "Diego Ramos"


class _HotYearsRecordingBatchARepo:
    """Fake repository — graba el `hot_years` recibido, MISMO patrón que
    `_HotYearsRecordingRepo` de test_reporting_cartera.py. El `$filter`
    OData real (Graph honrándolo o no) ya está cubierto en
    test_reporting_cartera.py::test_hot_years_filter_*; acá solo se prueba
    que el SERVICE de Participación pasa el scope correcto — MISMO
    contrato que `get_cartera_mantenimiento(anios=...)`."""

    def __init__(self):
        self.calls = []

    def get_batch_a_facts(self, access_token, *, hot_years=None):
        self.calls.append(hot_years)
        return {"project_facts": [], "cliente_dim": []}

    def get_horas_anio_estado_pairs(self, access_token):
        return []


def test_participacion_scopes_fetch_to_requested_anios_like_horas_vendidas():
    # SCOPE TEMPORAL (owner directive — "no mantener Participación en
    # full-history"): `anios` explícito viaja tal cual como `hot_years` —
    # MISMO contrato que `get_horas_vendidas(anios=...)`.
    repo = _HotYearsRecordingBatchARepo()
    ReportingService(repo).get_participacion("tok", anios=["2024"])
    assert repo.calls[-1] == ["2024"]


def test_participacion_default_anios_resolves_to_hot_window(monkeypatch):
    class _FixedDatetime:
        @staticmethod
        def now():
            import datetime as _dt
            return _dt.datetime(2026, 8, 15)

    import webapp.services.reporting_service as service_mod
    monkeypatch.setattr(service_mod, "datetime", _FixedDatetime)
    repo = _HotYearsRecordingBatchARepo()
    ReportingService(repo).get_participacion("tok")  # anios=None (primera hidratación)
    assert repo.calls[-1] == ["2026", "2025"]


def test_participacion_anios_delegates_to_horas_anio_estado_pairs_not_a_duplicate():
    # Reuso, no duplicación (owner directive) — MISMO endpoint/metadata que
    # Horas Vendidas/Rentabilidad (todos los tipos de proyecto).
    calls = []

    class _Repo:
        def get_horas_anio_estado_pairs(self, access_token):
            calls.append(access_token)
            return [{"anio": "2026", "estado": "En curso"}]

    result = ReportingService(_Repo()).get_participacion_anios("tok")
    assert calls == ["tok"]
    assert result == [{"anio": "2026", "estado": "En curso"}]


def test_participacion_keeps_clientes_without_any_project_facts_as_empty_row(monkeypatch):
    # POBLACIÓN (owner live QA — "suspiciously few clients", root cause):
    # un Cliente en la dimensión sin ningún hecho de Horas por proyectos
    # SIGUE siendo un Cliente válido — antes se omitía por completo,
    # perdiendo filas reales de `cliente_dim` (la fuente de verdad de qué
    # clientes existen). Ahora aparece con mapas vacíos (nunca fabricado con
    # atributos inventados: idFrecuencia sí se resuelve desde el dato real).
    _patch(
        monkeypatch,
        horas=[],
        proyectos=[],
        clientes=[{"id": "c1", "Title": "SinProyectos", "Frecuencia_x0020_Actualizacion": "Mensual"}],
    )
    rows = _service().get_participacion("tok")
    assert len(rows) == 1
    r = rows[0]
    assert r["cliente"] == "SinProyectos"
    assert r["vendidasByYear"] == {} and r["certificadasByYear"] == {}
    assert r["idFrecuencia"] == "1 Mensual"
    assert r["fecha"] == ""


def test_participacion_omits_clientes_without_a_resolved_name(monkeypatch):
    # Fila de dimensión sin nombre resuelto ⇒ sin identidad, la ÚNICA
    # omisión legítima (a diferencia de atributos opcionales en blanco,
    # que se preservan como "" — el frontend los muestra como "Sin dato").
    _patch(
        monkeypatch,
        horas=[],
        proyectos=[],
        clientes=[{"id": "c1", "Title": "", "Frecuencia_x0020_Actualizacion": "Mensual"}],
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


def test_batch_a_endpoint_and_lifecycle_gate_the_full_history_renderers():
    assert '"/operaciones/reporting/integral/data/batch-a"' in _REPORTING_JS
    assert "function loadBatchAData" in _REPORTING_JS
    assert "function renderBatchADataState" in _REPORTING_JS
    # Las 2 experiencias FULL-HISTORY que quedan (Calendario/Salud, categóricas
    # puras sobre cliente_dim) gatean con el MISMO status. Horas Vendidas Y
    # Participación SALIERON (scope decoupling — gatean con su propio
    # horasVendidasDataStatus/comercialParticipacionDataStatus, ver
    # test_reporting_cartera.py / test_participacion_has_its_own_scoped_
    # lifecycle_not_batch_a abajo). Horas Productivas usa batchBDataStatus
    # (Batch B, ver test_reporting_batch_b.py).
    #
    # Calendario NO tiene dispatcher (una sola función cubre sus 2
    # perspectivas internas) — se auto-gatea. Salud de Cliente SÍ vive
    # detrás de un dispatcher (`renderComercial`, MAIN-TAB HYDRATION
    # CONTRACT — comparte tab con Participación) — el trigger vive AHÍ, el
    # sub-renderer solo verifica el status (MISMO criterio que Rentabilidad/
    # HV, ver test_reporting_main_tab_hydration.py).
    calendario = _REPORTING_JS[_REPORTING_JS.index("function renderCalendario("):_REPORTING_JS.index("function renderCalendario(") + 400]
    assert 'state.batchADataStatus === "idle"' in calendario
    assert "loadBatchAData()" in calendario
    salud = _REPORTING_JS[_REPORTING_JS.index("function renderComercialSalud("):_REPORTING_JS.index("function renderComercial(")]
    assert "loadBatchAData()" not in salud
    assert 'state.batchADataStatus !== "ready"' in salud
    dispatcher = _REPORTING_JS[_REPORTING_JS.index("function renderComercial("):_REPORTING_JS.index("function renderComercial(") + 700]
    assert 'state.batchADataStatus === "idle"' in dispatcher
    assert "loadBatchAData()" in dispatcher


def test_batch_a_never_mixes_mock_and_real_data():
    # `replaceRows` puebla EN EL LUGAR los mismos arrays que ya usa
    # FACET_CONFIG — nunca un dataset paralelo para datos reales. Horas
    # Vendidas Y Participación ya NO se puebla acá (scope decoupling — su
    # propio loader scoped cada una).
    load_fn = _REPORTING_JS[_REPORTING_JS.index("async function loadBatchAData"):_REPORTING_JS.index("async function loadBatchAData") + 1000]
    assert "replaceRows(HORASVENDIDAS_ROWS" not in load_fn
    assert "replaceRows(COMERCIAL_PARTICIPACION_ROWS" not in load_fn
    assert "data.participacion" not in load_fn
    assert "replaceRows(CALENDARIO_ROWS, data.calendario)" in load_fn
    assert "replaceRows(COMERCIAL_SALUD_ROWS, data.salud)" in load_fn
    assert "data.horasProductivas" not in load_fn  # Batch B, no Batch A


def test_participacion_has_its_own_scoped_lifecycle_not_batch_a():
    # SCOPE DECOUPLING (owner directive — "Do NOT keep Participación on
    # full-history simply because old batch-a did") — MISMO criterio que
    # test_reporting_cartera.py::test_horas_vendidas_has_its_own_scoped_
    # lifecycle_not_batch_a: Participación se hidrata por su ciclo PROPIO
    # (ventana temporal), nunca por batch-a.
    fn = _REPORTING_JS[_REPORTING_JS.index("function renderComercial() {"):_REPORTING_JS.index("function renderComercial() {") + 700]
    assert "hydrateComercialParticipacionTab()" in fn
    detalle = _REPORTING_JS[_REPORTING_JS.index("function renderComercialParticipacion() {"):_REPORTING_JS.index("function renderComercialSalud(")]
    assert "loadBatchAData()" not in detalle
    assert 'state.comercialParticipacionDataStatus !== "ready"' in detalle


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
        body = _REPORTING_JS[_REPORTING_JS.index(f"function {fn}("):_REPORTING_JS.index(f"function {fn}(") + 4200]
        assert 'class="dashboard-table"' in body


def test_horas_vendidas_keeps_year_grain_separate(monkeypatch):
    # Migración Año/Mes/Estado: Julio-2026 y Julio-2025 quedan separados en
    # vendidasByYear (colapsado retrocompat en monthly); estado proyectado.
    _patch(
        monkeypatch,
        horas=[
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Julio", "HsVendidas": "20"},
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2025", "Mes": "Julio", "HsVendidas": "7"},
        ],
        proyectos=[{"id": "p1", "Title": "Rollout BI", "TipodeProyecto": "Proyecto", "Status": "En curso"}],
        clientes=[{"id": "c1", "Title": "Molinos"}],
    )
    rows = _service().get_horas_vendidas("tok")
    assert len(rows) == 1
    r = rows[0]
    assert r["vendidasByYear"]["2026"][6] == 20 and r["vendidasByYear"]["2025"][6] == 7  # Julio (idx6) por año
    assert r["monthly"][6] == 27          # colapsado retrocompat (20+7)
    assert r["estado"] == "En curso"


# ============================================================================
# Scope temporal desacoplado (owner directive — ventana caliente por tab)
# ============================================================================

def test_get_horas_vendidas_defaults_to_hot_window(monkeypatch):
    # HV pide al mismo adapter SÓLO su ventana caliente (año actual + anterior)
    # — nunca la historia completa que arrastra Participación.
    from datetime import datetime
    captured = {}

    def fake_facts(self, access_token, *, hot_years=None):
        captured["hot_years"] = hot_years
        return {"project_facts": [], "cliente_dim": []}

    monkeypatch.setattr(ReportingRepository, "get_batch_a_facts", fake_facts)
    _service().get_horas_vendidas("tok")
    y = datetime.now().year
    assert captured["hot_years"] == [str(y), str(y - 1)]


def test_get_horas_vendidas_cold_year_passes_explicit_scope(monkeypatch):
    # Un año frío puntual (lazy-load al elegirlo) viaja como scope explícito.
    captured = {}

    def fake_facts(self, access_token, *, hot_years=None):
        captured["hot_years"] = hot_years
        return {"project_facts": [], "cliente_dim": []}

    monkeypatch.setattr(ReportingRepository, "get_batch_a_facts", fake_facts)
    _service().get_horas_vendidas("tok", anios=["2024"])
    assert captured["hot_years"] == ["2024"]


def test_get_batch_a_calendario_salud_still_full_history_no_scope(monkeypatch):
    # Calendario/Salud (categóricas puras sobre cliente_dim, sin grano
    # temporal) NUNCA acotan por año — Participación SALIÓ de get_batch_a
    # (scope decoupling, ver test_participacion_scopes_fetch_to_requested_
    # anios_like_horas_vendidas arriba) y ya no es lo que este test cubre.
    captured = {"hot_years": "SENTINEL"}

    def fake_facts(self, access_token, *, hot_years=None):
        captured["hot_years"] = hot_years
        return {"project_facts": [], "cliente_dim": []}

    monkeypatch.setattr(ReportingRepository, "get_batch_a_facts", fake_facts)
    _service().get_batch_a("tok")
    assert captured["hot_years"] is None  # historia completa


def test_get_horas_vendidas_anios_includes_all_project_types(monkeypatch):
    # La metadata de años de HV abarca TODOS los tipos (Mantenimiento +
    # Proyecto) — a diferencia de la de Cartera Mantenimiento, que excluye
    # Proyecto. Un año que solo existe en un Proyecto DEBE ser seleccionable.
    _patch(
        monkeypatch,
        horas=[
            {"ProyectoLookupId": "p1", "A_x00f1_o": "2026"},
            {"ProyectoLookupId": "p2", "A_x00f1_o": "2024"},
        ],
        proyectos=[
            {"id": "p1", "TipodeProyecto": "Tiempo & Materiales", "Status": "En curso"},
            {"id": "p2", "TipodeProyecto": "Proyecto", "Status": "Finalizado"},
        ],
        clientes=[{"id": "c1", "Title": "YPF"}],
    )
    anios = {p["anio"] for p in _service().get_horas_vendidas_anios("tok")}
    assert anios == {"2026", "2024"}  # incluye el año del Proyecto
    # Contraste: la metadata de Cartera Mantenimiento excluye el año del Proyecto.
    cartera_anios = {p["anio"] for p in _service().get_cartera_mantenimiento_anios("tok")}
    assert cartera_anios == {"2026"}


# ============================================================================
# TEMPORAL DOMAIN CONTRACT (owner live QA — "Septiembre" apareció como
# opción imposible del <select> de Año en Horas Vendidas). Root cause: NADA
# en este pipeline validaba que A_x00f1_o fuera realmente un año de 4
# dígitos antes de este corrective — `_is_valid_anio` (reporting_
# repository.py) es el ÚNICO punto que decide, reusado acá y en
# reporting_service.py, NUNCA un `if anio:` suelto de nuevo.
# ============================================================================

def test_shape_horas_vendidas_rejects_non_year_anio_value(monkeypatch):
    _patch(
        monkeypatch,
        horas=[
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "Septiembre", "Mes": "Julio", "HsVendidas": "99"},
            {"ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Julio", "HsVendidas": "20"},
        ],
        proyectos=[{"id": "p1", "Title": "Rollout BI", "TipodeProyecto": "Proyecto", "Status": "En curso"}],
        clientes=[{"id": "c1", "Title": "Molinos"}],
    )
    rows = _service().get_horas_vendidas("tok")
    r = rows[0]
    assert "Septiembre" not in r["vendidasByYear"]
    assert set(r["vendidasByYear"].keys()) == {"2026"}
    # Colapsado retrocompat (`monthly`) SÍ sigue sumando ambos hechos — ese
    # campo nunca fue el dominio de Año, es el total histórico agregado por
    # mes-calendario (ver contrato de `_shape_horas_vendidas`).
    assert r["monthly"][6] == 119


def test_anio_estado_pairs_rejects_non_year_anio_value(monkeypatch):
    _patch(
        monkeypatch,
        horas=[
            {"ProyectoLookupId": "p1", "A_x00f1_o": "Septiembre"},
            {"ProyectoLookupId": "p1", "A_x00f1_o": "2026"},
        ],
        proyectos=[{"id": "p1", "TipodeProyecto": "Proyecto", "Status": "En curso"}],
        clientes=[{"id": "c1", "Title": "YPF"}],
    )
    anios = {p["anio"] for p in _service().get_horas_vendidas_anios("tok")}
    assert anios == {"2026"}
    assert "Septiembre" not in anios


# ============================================================================
# Frontend — Participación Año MULTI-SELECT + Estado + hot/cold (owner
# directive: "Do NOT keep Participación on full-history", reusar la
# infraestructura de HV/Rentabilidad). Source-seam, mismo criterio que el
# resto de este archivo — el runtime del contrato temporal compartido
# (_isValidAnioValue/etc.) ya se prueba en reporting_temporal_contract.node.js.
# ============================================================================

def _fn_body(name: str, window: int = 1500) -> str:
    start = _REPORTING_JS.index(f"function {name}(")
    return _REPORTING_JS[start:start + window]


def test_participacion_anio_is_a_checkbox_group_never_a_single_select():
    # A diferencia de HV/Rentabilidad (Año single-select), Participación
    # agrega SOBRE el conjunto de años tildados — checkbox multi-select.
    fb = _fn_body("renderComercialFilterBar", 900)
    assert "comercialParticipacionAnioOptions()" in fb
    assert "toggleComercialParticipacionAnio" in fb
    assert "toggleComercialStatus" in fb
    assert '>Todos</option>' not in fb  # nunca una opción "Todos" en un checkbox-group


def test_participacion_hot_cold_lifecycle_mirrors_horas_vendidas():
    # MISMO ciclo de vida que Horas Vendidas/Rentabilidad (hidratación con
    # ventana caliente, metadata de años en paralelo, lazy-load de años
    # fríos, merge sin perder lo ya hidratado) — nunca reinventado.
    for fn_name in (
        "hydrateComercialParticipacionTab", "loadComercialParticipacionData",
        "loadComercialParticipacionAnios", "loadComercialParticipacionColdYears",
        "mergeComercialParticipacionRows", "comercialParticipacionAnioOptions",
        "comercialParticipacionDefaultAnios", "pruneComercialParticipacionAnios",
    ):
        assert f"function {fn_name}(" in _REPORTING_JS, f"falta {fn_name}"


def test_participacion_cold_years_bundle_multiple_years_in_one_request():
    # Año MULTI-SELECT (a diferencia del single-select de HV/Rentabilidad):
    # tildar VARIOS años fríos de un saque debe bundlearlos en UN request
    # `anios=y1,y2`, nunca N requests separados.
    fn = _fn_body("loadComercialParticipacionColdYears")
    assert '"/operaciones/reporting/integral/data/participacion"' in fn
    assert "anios: anios.join(\",\")" in fn


def test_participacion_anios_disponibles_endpoint_wired():
    fn = _fn_body("loadComercialParticipacionAnios")
    assert '"/operaciones/reporting/integral/data/participacion/anios-disponibles"' in fn


def test_participacion_dispatcher_gates_cold_load_before_rendering_tree():
    # renderComercialParticipacion (sub-renderer, MAIN-TAB HYDRATION
    # CONTRACT) dispara el lazy-load de años faltantes ANTES de construir
    # el árbol — nunca renderiza una jerarquía a medio hidratar.
    fn = _fn_body("renderComercialParticipacion", 1200)
    assert "loadComercialParticipacionColdYears(missingYears)" in fn
    assert "comercialParticipacionLoadedYears" in fn


def test_participacion_selected_rows_sums_only_checked_anios_and_estados():
    # `comercialParticipacionSelectedRows` es la ÚNICA función que colapsa
    # vendidasByYear/certificadasByYear (mapas año→estado→horas) a los
    # `horasVendidas`/`horasCertificadas` planos que consume el resto del
    # motor (FACET_CONFIG/rollup/gran total) — nunca un segundo cálculo
    # paralelo que pueda divergir.
    fn = _fn_body("comercialParticipacionSelectedRows")
    assert "state.comercialParticipacionAnios" in fn
    assert "state.comercialStatus" in fn
    assert "horasVendidas: vendidas, horasCertificadas: certificadas" in fn
    # El gran total fijo (denominador REMOVEFILTERS) y el FACET_CONFIG de
    # Participación consumen ESTA misma función — nunca el array crudo.
    assert "comercialParticipacionSelectedRows()" in _fn_body("comercialParticipacionTotalVendidas", 200)
    facet_cfg = _REPORTING_JS[_REPORTING_JS.index("comercial: () => state.perspective.comercial"):_REPORTING_JS.index("comercial: () => state.perspective.comercial") + 400]
    assert "comercialParticipacionSelectedRows()" in facet_cfg
