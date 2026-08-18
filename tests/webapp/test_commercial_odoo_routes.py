"""
test_commercial_odoo_routes.py — Commercial Hub Odoo Batch 1: contrato HTTP
de `GET /comercial/data/summary|pipeline` + contratos de fuente (loader
canónico, ausencia de auth/JWT legacy) — CI NUNCA depende de Odoo real
(mockea `webapp.routers.commercial.CommercialService` en el boundary del
router, MISMO patrón que `test_staffing_routes.py` con `StaffingService`).
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import commercial as commercial_router
from webapp.services import auth_service
from webapp.services.commercial_service import CommercialConfigurationError, CommercialDataError

_STATIC = Path(__file__).resolve().parents[2] / "webapp" / "static"
_APP_JS = (_STATIC / "app.js").read_text(encoding="utf-8")
_COMMERCIAL_JS = (_STATIC / "commercial.js").read_text(encoding="utf-8")
_COMMERCIAL_PY = (Path(__file__).resolve().parents[2] / "webapp" / "routers" / "commercial.py").read_text(encoding="utf-8")
_COMMERCIAL_SERVICE_PY = (
    Path(__file__).resolve().parents[2] / "webapp" / "services" / "commercial_service.py"
).read_text(encoding="utf-8")
_ODOO_CLIENT_PY = (Path(__file__).resolve().parents[2] / "webapp" / "services" / "odoo_client.py").read_text(encoding="utf-8")

auth_service.ALLOWED_USERS = "ALL"
# CSV compartido con test_commercial_hub_foundation.py — ver su comentario
# (mismo riesgo de "asignación de módulo, la última que se ejecute gana"
# ya documentado en test_staffing_routes.py para OPERATIONS_ALLOWED_USERS).
auth_service.COMMERCIAL_ALLOWED_USERS = "commercial-test@danwor.com,commercial-odoo-test@danwor.com"

_SESSION = auth_service.create_session(
    {"email": "commercial-odoo-test@danwor.com", "name": "Odoo Test", "first_name": "Odoo", "family_name": "Test"}
)

client = TestClient(app)
client.cookies.set(auth_service.COOKIE_NAME, _SESSION)


class _StubService:
    def __init__(self, summary=None, pipeline=None, raises=None):
        self._summary = summary or {"ok": True}
        self._pipeline = pipeline or {"ok": True}
        self._raises = raises

    def get_summary(self, tipo="todos"):
        if self._raises:
            raise self._raises
        return {**self._summary, "tipo": tipo}

    def get_pipeline(self, tipo="todos"):
        if self._raises:
            raise self._raises
        return {**self._pipeline, "tipo": tipo}


def _unlisted_client(monkeypatch):
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "commercial-odoo-test@danwor.com")
    session = auth_service.create_session(
        {"email": "unlisted-odoo@danwor.com", "name": "Unlisted", "first_name": "Unlisted", "family_name": "User"}
    )
    ro = TestClient(app)
    ro.cookies.set(auth_service.COOKIE_NAME, session)
    return ro


# ============================================================================
# 1 — Auth Hub obligatoria
# ============================================================================

def test_summary_anonymous_redirects_to_login():
    anon = TestClient(app)
    response = anon.get("/comercial/data/summary", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/platform?next=")


def test_pipeline_anonymous_redirects_to_login():
    anon = TestClient(app)
    response = anon.get("/comercial/data/pipeline", follow_redirects=False)
    assert response.status_code == 303


# ============================================================================
# 2 — Entitlement Comercial fail-closed a nivel de ruta
# ============================================================================

def test_summary_unlisted_authorized_app_user_is_denied(monkeypatch):
    ro = _unlisted_client(monkeypatch)
    response = ro.get("/comercial/data/summary")
    assert response.status_code == 403


def test_pipeline_unlisted_authorized_app_user_is_denied(monkeypatch):
    ro = _unlisted_client(monkeypatch)
    response = ro.get("/comercial/data/pipeline")
    assert response.status_code == 403


def test_commercial_allowed_users_blank_denies_previously_authorized_user(monkeypatch):
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    response = client.get("/comercial/data/summary")
    assert response.status_code == 403


# ============================================================================
# Camino autorizado — contrato de datos
# ============================================================================

def test_summary_authorized_returns_ok_state_and_payload(monkeypatch):
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _StubService(summary={"pipeline": {"hours": 10}}))
    response = client.get("/comercial/data/summary?tipo=nn")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "ok"
    assert body["summary"]["tipo"] == "nn"


def test_pipeline_authorized_returns_ok_state_and_payload(monkeypatch):
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _StubService(pipeline={"stages": []}))
    response = client.get("/comercial/data/pipeline?tipo=bi")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "ok"
    assert body["pipeline"]["tipo"] == "bi"


def test_invalid_tipo_query_param_falls_back_to_todos(monkeypatch):
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _StubService())
    response = client.get("/comercial/data/summary?tipo=not-a-real-tipo")
    assert response.json()["summary"]["tipo"] == "todos"


# ============================================================================
# Fail-soft — config incompleta / error de Odoo / inesperado (SIEMPRE 200)
# ============================================================================

def test_config_incomplete_returns_200_with_config_incomplete_state(monkeypatch):
    monkeypatch.setattr(
        commercial_router, "CommercialService",
        lambda: _StubService(raises=CommercialConfigurationError("no config")),
    )
    response = client.get("/comercial/data/summary")
    assert response.status_code == 200
    assert response.json() == {"state": "config_incomplete"}


def test_odoo_data_error_returns_200_with_source_error_and_stage(monkeypatch):
    monkeypatch.setattr(
        commercial_router, "CommercialService",
        lambda: _StubService(raises=CommercialDataError("boom", stage="execute")),
    )
    response = client.get("/comercial/data/pipeline")
    assert response.status_code == 200
    assert response.json() == {"state": "source_error", "stage": "execute"}


def test_unexpected_failure_never_leaks_traceback_to_browser(monkeypatch):
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _StubService(raises=RuntimeError("secret internal detail")))
    response = client.get("/comercial/data/summary")
    assert response.status_code == 200
    body = response.json()
    assert body == {"state": "source_error", "stage": "unexpected"}
    assert "secret internal detail" not in response.text


# ============================================================================
# 17/18 — Loader canónico del Hub para la espera foreground de Odoo, NUNCA
# un loader propio de Comercial
# ============================================================================

def test_commercial_js_notifies_the_canonical_hub_foreground_progress():
    assert 'HubForegroundProgress.begin("commercial"' in _COMMERCIAL_JS
    assert 'HubForegroundProgress.complete("commercial")' in _COMMERCIAL_JS
    assert 'HubForegroundProgress.fail("commercial")' in _COMMERCIAL_JS


def test_commercial_js_never_touches_the_progress_dom_or_defines_its_own_loader():
    assert "read-progress" not in _COMMERCIAL_JS
    assert "hub-progress" not in _COMMERCIAL_JS
    assert "setInterval" not in _COMMERCIAL_JS  # el timer del progress vive SOLO en HubForegroundProgress
    # El ban original de `querySelector` a secas era un PROXY de la intención
    # real: "no salgas a buscar nodos ajenos (los del progress) en el DOM".
    # Batch 2B necesita UNA búsqueda acotada al propio subárbol para el
    # re-render quirúrgico del switch, así que el contrato se vuelve explícito
    # en vez de aproximado — se permite exactamente ese hook y nada más, y
    # siempre colgando de `root`, nunca de `document`.
    queries = re.findall(r"\.querySelector[A-Za-z]*\(([^)]*)\)", _COMMERCIAL_JS)
    assert queries == ['"[data-commercial-dist]"'], queries
    assert "document.querySelector" not in _COMMERCIAL_JS


def test_hub_foreground_progress_boundary_itself_is_untouched_by_this_batch():
    # El boundary compartido (app.js) no ganó una key "commercial" hardcodeada
    # NI ningún caso especial — recibe la key como cualquier otro caller
    # (Reporting/Asignaciones), sin bifurcar comportamiento por vertical.
    assert '"commercial"' not in _APP_JS


# ============================================================================
# UI Spine corrective — Commercial adopta el backbone de dashboard
# compartido (.dashboard-sections/.dashboard-section, wireado por
# app.js::initDashboardSections) en vez de un KPI grid/tabla sueltos.
# ============================================================================

def test_commercial_js_uses_the_shared_dashboard_sections_container():
    # `data-dashboard-key` se interpola en runtime (helper parametrizado,
    # ver test de abajo) — la marca ESTÁTICA a verificar es el atributo
    # dentro del template del helper + la key real pasada en el call site.
    assert '"dashboard-sections"' in _COMMERCIAL_JS
    assert "data-dashboard-key=" in _COMMERCIAL_JS
    # Key propia de Comercial — nunca la de otra vertical (Reporting usa
    # "reporting-<id>", Staffing usa "staffing").
    assert 'commercialDashboardSections("commercial",' in _COMMERCIAL_JS


def test_commercial_summary_and_pipeline_each_live_inside_their_own_dashboard_section():
    # El helper `commercialDashboardSection` es un template PARAMETRIZADO
    # (una sola definición, ver test del helper local más abajo) — lo que
    # se verifica acá son los DOS call sites reales (Resumen/Pipeline),
    # cada uno con su propio `data-section-id`, no una plantilla duplicada
    # a mano por sección.
    assert 'commercialDashboardSection("resumen", "Resumen",' in _COMMERCIAL_JS
    # KPI → Contextual Drilldown corrective: "pipeline"/"Pipeline por Etapa"
    # se renombró a "activas-analisis"/"Oportunidades Activas — Análisis" —
    # la tabla stages 1-8 es semánticamente el detalle de Oportunidades
    # Activas, no de Pipeline Total (ver test específico más abajo).
    assert 'commercialDashboardSection("activas-analisis", "Oportunidades Activas — Análisis",' in _COMMERCIAL_JS
    assert 'data-section-id="${id}"' in _COMMERCIAL_JS
    # El KPI grid y la tabla siguen siendo los MISMOS primitives de siempre
    # (sin tocar tiles/columnas) — solo ahora viven dentro del body de cada
    # sección en vez de sueltos en el root.
    assert '<div class="activity-kpi-grid">' in _COMMERCIAL_JS
    assert 'class="dashboard-table"' in _COMMERCIAL_JS


def test_commercial_js_calls_the_shared_init_dashboard_sections():
    assert "initDashboardSections(document)" in _COMMERCIAL_JS


def test_commercial_local_section_helper_defined_once_mirrors_reporting_shape():
    # Helper LOCAL mínimo (auditado: no existe un renderer compartido
    # global — Reporting lo define local, Staffing lo arma directo en su
    # template Alpine) — una sola definición, reusada por los dos call
    # sites (Resumen/Pipeline), nunca una plantilla copiada por sección.
    assert _COMMERCIAL_JS.count("function commercialDashboardSection(") == 1
    assert _COMMERCIAL_JS.count("function commercialDashboardSections(") == 1
    assert '"dashboard-section__head"' in _COMMERCIAL_JS
    assert '"dashboard-section__title"' in _COMMERCIAL_JS
    assert '"dashboard-section__body"' in _COMMERCIAL_JS


def test_commercial_js_never_reimplements_drag_or_collapse_itself():
    # El comportamiento de drag/collapse/orden vive ÚNICAMENTE en
    # app.js::initDashboardSections — Comercial nunca define su propio
    # listener de dragstart/dragover/drop ni maneja el estado open/closed
    # de un <details> a mano.
    for banned in ("addEventListener(\"dragstart\"", "addEventListener(\"dragover\"", "addEventListener(\"drop\"", ".open ="):
        assert banned not in _COMMERCIAL_JS


def test_commercial_pipeline_heading_dead_class_is_gone():
    assert "commercial-pipeline-heading" not in _COMMERCIAL_JS
    _TOOLS_CSS = (_STATIC / "tools.css").read_text(encoding="utf-8")
    _APP_CSS = (_STATIC / "app.css").read_text(encoding="utf-8")
    assert "commercial-pipeline-heading" not in _TOOLS_CSS
    assert "commercial-pipeline-heading" not in _APP_CSS


def test_commercial_kpi_tiles_and_table_and_switch_primitives_unchanged():
    # Los primitives de nivel TILE (nunca tocados por este corrective, solo
    # el contenedor que los envuelve cambió).
    assert 'class="activity-kpi"' in _COMMERCIAL_JS
    assert 'class="dashboard-table"' in _COMMERCIAL_JS
    assert 'class="dashboard-view-switch"' in _COMMERCIAL_JS


# ============================================================================
# View Controls Spine corrective — Todos/NN/BI es un VIEW CONTROL page-level
# (filtra Resumen Y Pipeline a la vez), no navegación entre capacidades —
# vive dentro de `.dashboard-sticky-header`, el mismo contenedor page-level
# que Reporting/Staffing, nunca suelto contra el hero.
# ============================================================================

def test_commercial_view_switch_lives_inside_the_shared_sticky_header():
    assert '"dashboard-sticky-header"' in _COMMERCIAL_JS
    sticky_block = _COMMERCIAL_JS.split('function commercialFilterHtml()', 1)[1].split('\n}\n', 1)[0]
    assert '"dashboard-sticky-header"' in sticky_block
    assert '"dashboard-view-switch"' in sticky_block


def test_commercial_sticky_header_appears_before_dashboard_sections_in_render():
    render_fn = _COMMERCIAL_JS.split("function commercialRender()", 1)[1].split("\nasync function", 1)[0]
    assert render_fn.index("commercialFilterHtml()") < render_fn.index("commercialDashboardSections(")


def test_commercial_did_not_adopt_experience_tabs():
    # Una única capability hoy — no navegación por tabs todavía.
    assert "experience-tabs" not in _COMMERCIAL_JS


def test_commercial_did_not_adopt_staffing_filter_bar_layout():
    # Un solo control simple — `.staffing-filter-bar` es el layout de
    # Staffing para VARIOS controles + botón Limpiar en una fila, no
    # necesario acá; no emular Staffing sin una necesidad real.
    assert "staffing-filter-bar" not in _COMMERCIAL_JS


def test_commercial_todos_nn_bi_selector_behavior_is_unchanged():
    # Mismos 3 valores TÉCNICOS (data attribute/query param), mismo mecanismo
    # de toggle — el corrective de spine era puramente estructural (cero
    # cambio de comportamiento). El COPY visible sí cambió después (ver
    # test_commercial_filter_copy_shows_new_name_and_base_instalada), pero
    # el contrato de datos sigue siendo exactamente todos/nn/bi.
    assert '["todos", "Todos"]' in _COMMERCIAL_JS
    assert "data-commercial-tipo=" in _COMMERCIAL_JS
    assert "commercialState.tipo === key" in _COMMERCIAL_JS


# ============================================================================
# Table Spine corrective — la tabla de Pipeline adopta el contrato de
# distribución de columnas ya compartido en tools.css (`.row-label-col` +
# ancho default de columnas métricas), en vez de que la columna descriptiva
# absorba todo el ancho sobrante bajo `table-layout:fixed`.
# ============================================================================

def test_commercial_pipeline_table_still_uses_the_shared_table_primitive():
    assert 'class="dashboard-table"' in _COMMERCIAL_JS


def test_commercial_pipeline_table_is_wrapped_in_the_shared_scroll_container():
    table_fn = _COMMERCIAL_JS.split("function commercialPipelineTableHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert '<div class="matrix-scroll">' in table_fn
    assert table_fn.index('<div class="matrix-scroll">') < table_fn.index("<table")


def test_commercial_pipeline_descriptive_column_adopts_row_label_col():
    assert '<th class="row-label-col">Etapa</th>' in _COMMERCIAL_JS
    # Ya no vive bajo `.left` (ese solo daba text-align, nunca el ancho).
    assert '<th class="left">Etapa</th>' not in _COMMERCIAL_JS


def test_commercial_pipeline_row_label_cell_adopts_shared_row_label_class():
    assert '<td class="row-label">${commercialEsc(stage.stage)}</td>' in _COMMERCIAL_JS


def test_commercial_metric_columns_receive_no_commercial_specific_width():
    # Las columnas métricas (Oportunidades/Horas/Ponderadas/Revenue) siguen
    # sin clase — heredan el ancho default (88px) del primitive compartido,
    # nunca un ancho puntual de Comercial.
    table_fn = _COMMERCIAL_JS.split("function commercialPipelineTableHtml(", 1)[1].split("\nfunction ", 1)[0]
    for header in ("Oportunidades", "Horas", "Ponderadas (hs)", "Revenue"):
        assert f"<th>{header}</th>" in table_fn


def test_no_commercial_specific_css_for_table_column_distribution():
    _TOOLS_CSS = (_STATIC / "tools.css").read_text(encoding="utf-8")
    _APP_CSS = (_STATIC / "app.css").read_text(encoding="utf-8")
    for banned in (".commercial-table", ".commercial-pipeline-table", ".commercial-col"):
        assert banned not in _TOOLS_CSS
        assert banned not in _APP_CSS
        assert banned not in _COMMERCIAL_JS


def test_commercial_pipeline_numeric_alignment_still_inherited_not_overridden():
    # Ningún `text-align` inline ni override puntual — la alineación numérica
    # sigue viniendo del default compartido (`table.dashboard-table td`).
    table_fn = _COMMERCIAL_JS.split("function commercialPipelineTableHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert "text-align" not in table_fn


def test_matrix_scroll_is_promoted_to_an_unscoped_shared_hub_primitive():
    # `.matrix-scroll` vivía scoped a `.reporting-integral-preview` (única
    # regla real, sin semántica propia de Reporting) — promovido a
    # tools.css sin scope, para que cualquier vertical con una tabla ancha
    # obtenga scroll real en vez de que un ancestro con overflow la recorte.
    _TOOLS_CSS = (_STATIC / "tools.css").read_text(encoding="utf-8")
    _REPORTING_CSS = (_STATIC / "reporting.css").read_text(encoding="utf-8")
    assert ".matrix-scroll { overflow-x: auto; }" in _TOOLS_CSS
    assert ".reporting-integral-preview .matrix-scroll" not in _REPORTING_CSS
    assert ".matrix-scroll {" not in _REPORTING_CSS
    # Una sola declaración real en todo el CSS del Hub — nunca duplicada.
    assert _TOOLS_CSS.count(".matrix-scroll {") == 1


def test_commercial_pipeline_table_has_a_totals_row():
    # MISMO patrón que Reporting: `tr.is-total`/`td.row-label`, rollup
    # client-side sobre las etapas ya recibidas — nunca un query/campo de
    # total propio en el backend.
    table_fn = _COMMERCIAL_JS.split("function commercialPipelineTableHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert '<tr class="is-total">' in table_fn
    assert '<td class="row-label">Total</td>' in table_fn
    assert "pipeline.stages.reduce(" in table_fn
    assert "${totalRow}" in table_fn


def test_commercial_dashboard_spine_and_view_switch_survive_table_corrective():
    # Este corrective es EXCLUSIVAMENTE table layout — el resto del spine
    # (secciones + view control) sigue intacto.
    assert '"dashboard-sticky-header"' in _COMMERCIAL_JS
    assert '"dashboard-view-switch"' in _COMMERCIAL_JS
    assert 'commercialDashboardSection("resumen", "Resumen",' in _COMMERCIAL_JS
    assert 'commercialDashboardSection("activas-analisis", "Oportunidades Activas — Análisis",' in _COMMERCIAL_JS


# ============================================================================
# Batch 2A — KPI → Contextual Drilldown (Oportunidades Activas). Audit live
# legacy confirmó que la tabla stages 1-8 corresponde semánticamente a
# Oportunidades Activas, no a Pipeline Total — este batch reposiciona esa
# tabla como el detalle contextual del ÚNICO KPI clickeable de este batch,
# reusando el motor shared `initEventBreakdown` (app.js), nunca un handler
# de click propio de Comercial.
# ============================================================================

def test_only_active_opportunities_kpi_is_clickable():
    # Único call site de `commercialClickableKpi` en todo el archivo — el
    # resto de los 5 KPIs del grid siguen siendo `commercialKpi` (display-only).
    assert 'commercialClickableKpi("Oportunidades activas", commercialFmtInt(s.activas.count), "activas")' in _COMMERCIAL_JS
    assert _COMMERCIAL_JS.count("commercialClickableKpi(") == 2  # definición + un único call site


def test_other_kpi_tiles_remain_display_only():
    for label in ("Pipeline horas", "Pipeline ponderado (hs)", "Ganadas", "Perdidas", "Conversión"):
        assert f'commercialKpi("{label}"' in _COMMERCIAL_JS
    # Ninguno de los otros 5 pasa por la variante clickeable.
    for banned_call in (
        'commercialClickableKpi("Pipeline horas"', 'commercialClickableKpi("Pipeline ponderado',
        'commercialClickableKpi("Ganadas"', 'commercialClickableKpi("Perdidas"',
        'commercialClickableKpi("Conversión"',
    ):
        assert banned_call not in _COMMERCIAL_JS


def test_clickable_kpi_reuses_the_shared_activity_kpi_clickable_modifier():
    clickable_fn = _COMMERCIAL_JS.split("function commercialClickableKpi(", 1)[1].split("\nfunction ", 1)[0]
    assert 'class="activity-kpi activity-kpi--clickable"' in clickable_fn
    assert "data-event=" in clickable_fn
    assert 'role="button"' in clickable_fn
    assert 'tabindex="0"' in clickable_fn
    assert "aria-label=" in clickable_fn


def test_commercial_reuses_shared_init_event_breakdown_never_a_parallel_handler():
    assert "initEventBreakdown(document" in _COMMERCIAL_JS
    # Nunca un listener de click propio sobre el trigger — el motor shared
    # ya resuelve click + keydown + toggle/deselect.
    for banned in ("addEventListener(\"click\", () => show", "function commercialShowActive", "function commercialToggleActive"):
        assert banned not in _COMMERCIAL_JS


def test_init_event_breakdown_gains_an_opt_out_of_auto_open_without_changing_default():
    # Firma nueva con default `true` — CERO cambio de comportamiento para el
    # consumidor histórico ("Eventos y catálogo"), que sigue llamando
    # `initEventBreakdown(document)` sin opciones.
    assert "function initEventBreakdown(scope, { openFirst = true } = {})" in _APP_JS
    assert 'document.body.addEventListener("htmx:load", () => initEventBreakdown(document));' in _APP_JS
    assert "if (openFirst && rows.length) show(rows[0].dataset.event);" in _APP_JS


def test_commercial_opts_out_of_auto_open_for_its_own_group():
    assert "initEventBreakdown(document, { openFirst: false })" in _COMMERCIAL_JS


def test_analysis_panel_starts_hidden_not_via_default_auto_open():
    # El estado inicial "cerrado" está codificado en el propio markup (panel
    # "" visible, panel "activas" hidden) — NO depende de que la función lo
    # calcule, porque con openFirst:false la función no llama a show() en absoluto.
    render_fn = _COMMERCIAL_JS.split("function commercialRender()", 1)[1].split("\nasync function", 1)[0]
    assert 'data-breakdown=""' in render_fn
    assert 'data-breakdown="activas" hidden' in render_fn


def test_breakdown_group_attribute_wraps_the_dashboard_sections_container():
    sections_fn = _COMMERCIAL_JS.split("function commercialDashboardSections(", 1)[1].split("\nfunction ", 1)[0]
    assert "data-breakdown-group" in sections_fn
    # Coexiste con data-dashboard-key en el MISMO div — contratos ortogonales.
    assert "data-dashboard-key=" in sections_fn


def test_active_opportunities_analysis_contains_the_stage_1_8_table_not_pipeline_total():
    render_fn = _COMMERCIAL_JS.split("function commercialRender()", 1)[1].split("\nasync function", 1)[0]
    # Batch 2B: el panel ahora se COMPONE (gráfico arriba + tabla abajo) en
    # `commercialActivasAnalysisHtml`, que es quien invoca la tabla. La tabla
    # sigue siendo exactamente la misma y sigue perteneciendo a este panel —
    # cambió el punto de composición, no el universo ni el dueño.
    assert "commercialActivasAnalysisHtml(p)" in render_fn
    analysis_fn = _COMMERCIAL_JS.split("function commercialActivasAnalysisHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert "commercialPipelineTableHtml(pipeline)" in analysis_fn
    assert "commercialDistribucionHtml(pipeline" in analysis_fn
    # Ya no existe la sección paralela "Pipeline por Etapa" ligada a Pipeline Total.
    assert "Pipeline por Etapa" not in _COMMERCIAL_JS
    assert 'commercialDashboardSection("pipeline",' not in _COMMERCIAL_JS


def test_dimension_switch_does_not_refetch_rerender_globally_or_rewire_the_shared_engine():
    # Invariante central del batch: el switch vive DENTRO del panel que abre
    # `initEventBreakdown`, y ese motor guarda el estado abierto/cerrado sólo
    # en el DOM. Un re-render global (o un re-wire del motor) cerraría el
    # panel justo al conmutar la dimensión.
    branch = _COMMERCIAL_JS.split(
        'const dimButton = event.target.closest("[data-commercial-dim]");', 1
    )[1].split("const button = event.target.closest", 1)[0]
    assert "commercialDistribucionHtml(" in branch
    for banned in ("commercialLoadData", "commercialRender(", "initEventBreakdown", "fetch("):
        assert banned not in branch, banned


def test_stage_table_shared_primitives_and_totals_row_untouched_by_this_batch():
    # Este batch es EXCLUSIVAMENTE reposicionamiento — la tabla en sí (Table
    # Spine corrective) no cambia: row-label-col, matrix-scroll, tr.is-total.
    assert '<th class="row-label-col">Etapa</th>' in _COMMERCIAL_JS
    assert '<div class="matrix-scroll">' in _COMMERCIAL_JS
    assert '<tr class="is-total">' in _COMMERCIAL_JS
    assert "pipeline.stages.reduce(" in _COMMERCIAL_JS


def test_commercial_filter_copy_shows_new_name_and_base_instalada_not_abbreviations():
    assert '[["todos", "Todos"], ["nn", "New Name"], ["bi", "Base Instalada"]]' in _COMMERCIAL_JS
    assert '"nn", "NN"' not in _COMMERCIAL_JS
    assert '"bi", "BI"' not in _COMMERCIAL_JS


def test_commercial_technical_tipo_values_are_unchanged_by_the_copy_relabel(monkeypatch):
    # El copy visible cambió; el contrato de datos (query param real que
    # llega al backend) sigue siendo exactamente todos/nn/bi — sin tocar
    # service/endpoint.
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _StubService())
    for tipo in ("todos", "nn", "bi"):
        response = client.get(f"/comercial/data/summary?tipo={tipo}")
        assert response.json()["summary"]["tipo"] == tipo


def test_bar_primitive_is_promoted_hub_wide_and_reporting_keeps_its_domain_colors():
    # CONTRATO ACTUALIZADO DELIBERADAMENTE EN BATCH 2B. Hasta 2A este test
    # exigía que `.bar-row` siguiera scopeado a Reporting ("no promovido") —
    # ese contrato quedó obsoleto por decisión arquitectónica explícita del
    # owner: los small multiples de Comercial son el SEGUNDO consumidor real,
    # y como base.html no carga reporting.css, sin promoción el primitivo era
    # literalmente inerte fuera de Reporting.
    tools_css = (_STATIC / "tools.css").read_text(encoding="utf-8")
    reporting_css = (_STATIC / "reporting.css").read_text(encoding="utf-8")
    # La ESTRUCTURA vive ahora en tools.css, sin scope de vertical.
    for rule in (".bar-row {", ".bar-row .bar-label {", ".bar-track {", ".bar-fill {", ".bar-value {", ".perspective-label {"):
        assert rule in tools_css, rule
        assert f".reporting-integral-preview {rule}" not in reporting_css, rule
    # El COLOR por semántica de negocio sigue siendo de Reporting, no primitivo.
    for modifier in ("vendidas", "certificadas", "ejecutadas", "positivo", "negativo"):
        assert f".reporting-integral-preview .bar-fill.{modifier}" in reporting_css, modifier
        assert f".bar-fill.{modifier}" not in tools_css, modifier
    # Reporting fija su propia superficie clara SIEMPRE: sin este pin, el
    # override de tema oscuro del primitivo (que cuelga de <html>) le pintaría
    # un track claro sobre su panel blanco.
    assert "--bar-track-bg" in reporting_css
    assert ".content-theme-dark { --bar-track-bg" in tools_css


def test_no_commercial_specific_chart_css_or_class_was_invented():
    # Sigue vigente de 2A: Comercial no gana clases propias — consume el
    # primitivo compartido o no dibuja.
    tools_css = (_STATIC / "tools.css").read_text(encoding="utf-8")
    for banned in ("commercial-funnel", "commercial-chart", ".commercial-breakdown", ".commercial-analysis"):
        assert banned not in _COMMERCIAL_JS
        assert banned not in tools_css


def test_commercial_charts_introduce_no_library_canvas_or_svg():
    # La decisión de diseño es CSS/DOM sobre el primitivo compartido.
    for banned in ("<canvas", "<svg", "new Chart(", "Chart.js", "chart.js", "d3.", "import "):
        assert banned not in _COMMERCIAL_JS, banned


def test_no_other_kpi_gained_a_drilldown_this_batch():
    # Pipeline Total/Ponderado, Ganadas, Perdidas, Prospectos, Suspendidas,
    # Creadas, Target: ninguno gana `data-event` ni panel propio en este batch
    # — contar solo MARKUP real dentro de cada función, no menciones en
    # comentarios (que mencionan `data-event`/`data-breakdown` varias veces).
    clickable_fn = _COMMERCIAL_JS.split("function commercialClickableKpi(", 1)[1].split("\nfunction ", 1)[0]
    assert clickable_fn.count('data-event="') == 1  # un solo trigger posible por tile
    # Aislar el LITERAL de los paneles (no el comentario que lo precede,
    # que también menciona `data-breakdown` en prosa).
    panels_literal = _COMMERCIAL_JS.split("const analysisPanels = `", 1)[1].split("\n  const sections =", 1)[0]
    assert panels_literal.count('data-breakdown="') == 2  # "" (vacío) + "activas"
    # Ningún otro helper (commercialKpi, el display-only) emite data-event.
    plain_kpi_fn = _COMMERCIAL_JS.split("function commercialKpi(", 1)[1].split("\n\n// KPI", 1)[0]
    assert "data-event" not in plain_kpi_fn


def test_loader_and_odoo_business_logic_untouched_by_this_batch():
    assert 'HubForegroundProgress.begin("commercial"' in _COMMERCIAL_JS
    assert 'HubForegroundProgress.complete("commercial")' in _COMMERCIAL_JS
    assert 'HubForegroundProgress.fail("commercial")' in _COMMERCIAL_JS
    assert "/comercial/data/summary" in _COMMERCIAL_JS
    assert "/comercial/data/pipeline" in _COMMERCIAL_JS


# ============================================================================
# 19 — Ningún auth/JWT/DB del CRM legacy
# ============================================================================

def test_no_legacy_crm_auth_primitives_anywhere_in_commercial_surface():
    haystacks = [_COMMERCIAL_PY, _COMMERCIAL_SERVICE_PY, _ODOO_CLIENT_PY, _COMMERCIAL_JS]
    banned = (
        "AUTH_SECRET_KEY", "jwt.encode", "jwt.decode", "users.db", "sqlite3",
        "bcrypt", "passlib", "SMTP_PASSWORD", "reset-password", "MICROSOFT_CLIENT_ID",
    )
    for text in haystacks:
        for term in banned:
            assert term not in text, term


def test_commercial_data_endpoints_never_accept_a_graph_token_or_odoo_credential_param():
    # Odoo es un service account server-side (config.py) — a diferencia de
    # Staffing/Reporting (Graph delegado), estos endpoints NUNCA reciben
    # token/credencial alguna del browser.
    for banned in ("graph_token", "api_key", "odoo_token", "password"):
        assert banned not in _COMMERCIAL_PY


# ============================================================================
# 20/21 — Copiloto y Operations intactos
# ============================================================================

def test_root_copilot_route_still_serves_copilot_unchanged():
    anon = TestClient(app)
    response = anon.get("/")
    assert response.status_code == 200
    assert "Copiloto Metodológico" in response.text


def test_operations_staffing_route_unaffected_by_odoo_batch(monkeypatch):
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "commercial-odoo-test@danwor.com")
    response = client.get("/operaciones/staffing/asignaciones")
    assert response.status_code == 200
    assert '<p class="eyebrow">Staffing</p>' in response.text


# ============================================================================
# Batch 3A / C1 — `.experience-tabs` promovido a primitivo Hub
# ============================================================================

def test_experience_tabs_promoted_hub_wide_and_reporting_pins_its_own_colors():
    tools_css = (_STATIC / "tools.css").read_text(encoding="utf-8")
    reporting_css = (_STATIC / "reporting.css").read_text(encoding="utf-8")
    # La ESTRUCTURA vive en tools.css sin scope de vertical: base.html no
    # carga reporting.css, así que scopeada era inerte fuera de Reporting.
    for rule in (".experience-tabs {", ".experience-tabs button {"):
        assert rule in tools_css, rule
        assert f".reporting-integral-preview nav{rule}" not in reporting_css, rule
    assert "nav.experience-tabs" not in reporting_css
    # Los colores NO se copiaron tal cual: `--ink` existe Hub-wide con el
    # significado INVERTIDO (#e4e4ec, texto claro para fondo oscuro) y
    # `--ink-faint` sólo existe dentro de Reporting. Salen por token propio
    # con fallback + override de tema oscuro.
    assert "var(--tab-ink-muted, #7a7a82)" in tools_css
    assert "var(--tab-ink, #1f1f24)" in tools_css
    assert ".content-theme-dark { --tab-ink:" in tools_css
    # Reporting fuerza su panel claro en AMBOS temas: sin el pin, el override
    # de tema oscuro le pondría texto claro sobre fondo blanco.
    assert "--tab-ink: #1f1f24;" in reporting_css
    assert "--tab-ink-muted: #7a7a82;" in reporting_css


def test_experience_tabs_primitive_carries_no_reporting_navigation_logic():
    tools_css = (_STATIC / "tools.css").read_text(encoding="utf-8")
    tabs_block = tools_css.split(".experience-tabs {", 1)[1].split("/* HUB", 1)[0]
    for leaked in ("EXPERIENCES", "setExperience", "reporting-experience-tabs", "TAB_HYDRATION"):
        assert leaked not in tabs_block, leaked
    # La navegación de Reporting sigue siendo suya. Se mira CÓDIGO, no prosa:
    # el comentario de commercial.js nombra a propósito lo que NO reusa, y un
    # scan del texto crudo daría un falso positivo por esa misma mención.
    reporting_js = (_STATIC / "reporting.js").read_text(encoding="utf-8")
    assert "function setExperience(" in reporting_js
    commercial_code = re.sub(r"//[^\n]*", "", _COMMERCIAL_JS)
    for leaked in ("setExperience(", "EXPERIENCES", "TAB_HYDRATION_TRIGGERS"):
        assert leaked not in commercial_code, leaked


def test_experience_tabs_stay_responsive_without_horizontal_scroll():
    # `flex-wrap: wrap` ya venía de Reporting y es lo que evita el scroll
    # horizontal en viewport angosto — se preserva al promover.
    tools_css = (_STATIC / "tools.css").read_text(encoding="utf-8")
    tabs_rule = tools_css.split(".experience-tabs {", 1)[1].split("}", 1)[0]
    assert "flex-wrap: wrap" in tabs_rule


def test_section_drag_affordance_is_scoped_to_actually_draggable_heads():
    # El cursor `grab` colgaba de `.dashboard-section__head` a secas y por lo
    # tanto lo anunciaba también en secciones deliberadamente NO reordenables
    # (los buckets de Actividades). El drag SIEMPRE fue opt-in por markup;
    # ahora el cursor también lo es.
    tools_css = (_STATIC / "tools.css").read_text(encoding="utf-8")
    assert '.dashboard-section__head[draggable="true"] { cursor: grab; }' in tools_css
    assert ".dashboard-section__head { cursor: grab; }" not in tools_css


# ============================================================================
# Batch 3A / C2 — endpoint de Actividades
# ============================================================================

def test_activities_endpoint_anonymous_redirects_to_login():
    anon = TestClient(app)
    response = anon.get("/comercial/data/activities", follow_redirects=False)
    assert response.status_code == 303


def test_activities_endpoint_unlisted_user_is_denied(monkeypatch):
    ro = _unlisted_client(monkeypatch)
    assert ro.get("/comercial/data/activities").status_code == 403


def test_activities_endpoint_returns_ok_state_and_payload(monkeypatch):
    class _ActStub(_StubService):
        def get_activities(self, tipo="todos"):
            return {"tipo": tipo, "activities": [], "total": 0}
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _ActStub())
    response = client.get("/comercial/data/activities?tipo=nn")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "ok"
    assert body["activities"]["tipo"] == "nn"


def test_activities_endpoint_is_fail_soft_like_the_rest_of_commercial(monkeypatch):
    class _Boom(_StubService):
        def get_activities(self, tipo="todos"):
            raise CommercialConfigurationError("no config")
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _Boom())
    response = client.get("/comercial/data/activities")
    assert response.status_code == 200
    assert response.json() == {"state": "config_incomplete"}


def test_activities_endpoint_never_exposes_a_write_verb():
    # Read-only de punta a punta: cualquier verbo de escritura no existe.
    for verb in ("post", "put", "patch", "delete"):
        response = getattr(client, verb)("/comercial/data/activities")
        assert response.status_code == 405, (verb, response.status_code)
