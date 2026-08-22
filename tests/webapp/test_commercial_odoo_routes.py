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
    # C3.1 (ver HANDOFF — scroll al abrir el análisis) agregó UNA búsqueda
    # acotada para ubicar la sección a la que hay que hacer scroll. C5a
    # retiró el switch Horas|Ponderadas (C3/C3.1) y con él la búsqueda de
    # `[data-commercial-dist]` que usaba para su propio re-render — el
    # Funnel ya no necesita re-renderizarse a sí mismo, sólo cambia con
    # `pipeline` (Todos/NN/BI). El corrective de tooltip dinámico (ver
    # HANDOFF) agrega una búsqueda LOCAL — `anchor.querySelector(".chart-
    # tooltip")`, nunca sobre `root`/`document` — para medir el tooltip
    # propio de esa columna, no un nodo ajeno. C5d (ver HANDOFF — cierre
    # explícito) agrega una búsqueda del MISMO tile `[data-event="activas"]`
    # que abre el panel, para simular su click y cerrar por el camino ya
    # existente. C4 (ver HANDOFF — detalle por oportunidad) agrega otra,
    # para el re-render quirúrgico de la tabla al ordenar una columna. C5e
    # (ver HANDOFF — filtro Etapa/Vendedor/Buscar) agrega las tres
    # restantes: `[data-commercial-funnel-area]` + `[data-commercial-
    # opportunities]` en `commercialRerenderAnalysisFunnelAndTable` (Etapa/
    # Vendedor tocan Funnel Y tabla, dos hosts distintos) y otra
    # `[data-commercial-opportunities]` en el delegado de búsqueda libre
    # (sólo tabla, nunca el Funnel — contrato explícito del owner). El
    # contrato sigue siendo explícito, no aproximado: se permiten
    # EXACTAMENTE estos siete hooks y nada más.
    queries = re.findall(r"\.querySelector[A-Za-z]*\(([^)]*)\)", _COMMERCIAL_JS)
    # C6 (ver HANDOFF — Forecast) agrega DOS hooks: los hosts estables del
    # panel de Forecast (`[data-commercial-forecast-body]` y su conteo) para
    # el re-render quirúrgico cuando resuelve el lazy load — un render
    # completo cerraría ese mismo panel. Y el hook de cierre pasó de
    # `[data-event="activas"]` literal a la key que trae el propio botón, para
    # que los DOS análisis (Activas y Forecast) usen el MISMO camino de cierre
    # en vez de uno cada uno. Todos siguen colgando de `root`, nunca de
    # `document`. "✕ Limpiar" (owner request) agrega los TRES controles de la
    # barra de filtros, buscados desde el propio botón (`closest` a la barra) y
    # sólo para ponerlos en su valor neutro: la barra NUNCA se regenera, así el
    # `<input>` no se recrea y el foco se queda en el botón. El C6 visual
    # corrective (ver HANDOFF) agrega el `querySelectorAll` de los triggers
    # HERMANOS que comparten `data-event` — para dejar `.is-active` sólo en el
    # que el usuario activó — y le pone `.is-active` al selector de la X, que
    # ahora resuelve el trigger ACTIVO del análisis en vez del primero del
    # DOM. C6b (tabla + filtros del Forecast, ver HANDOFF) agrega dos: el
    # trigger ACTIVO de forecast —de donde se LEE el modo Total/Ponderado, en
    # vez de guardarlo— y el host del título conmutable. El contrato sigue
    # siendo explícito, no aproximado: se permiten EXACTAMENTE estos quince
    # hooks y nada más.
    assert queries == [
        '".chart-tooltip"',
        '\'[data-event="forecast"].is-active\'',
        '`[data-breakdown="${trigger.dataset.event}"]`',
        '`[data-event="${trigger.dataset.event}"]`',
        '"[data-commercial-funnel-area]"',
        '"[data-commercial-opportunities]"',
        '"[data-commercial-forecast-body]"',
        '"[data-commercial-forecast-count]"',
        '"[data-commercial-forecast-title]"',
        '`[data-event="${key}"].is-active`',
        '"[data-commercial-stage-filter]"',
        '"[data-commercial-seller-filter]"',
        '"[data-commercial-search]"',
        '"[data-commercial-opportunities]"',
        '"[data-commercial-opportunities]"',
    ], queries
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
    # Landing Homologation: el Dashboard tiene ahora DOS familias de tiles —
    # Resumen (9 principales) y Analíticas del Pipeline (6 derivadas).
    assert 'commercialDashboardSection("analiticas", "Analíticas del Pipeline"' in _COMMERCIAL_JS
    # "Oportunidades Activas — Análisis" ya NO se arma con el helper: lleva
    # `data-breakdown` sobre la sección para poder aparecer/desaparecer
    # entera, así que se emite con su propio markup (ver test específico).
    assert 'data-section-id="activas-analisis" data-breakdown="activas"' in _COMMERCIAL_JS
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
    # listener de dragstart/dragover/drop, ni un botón/atajo propio para
    # expandir/colapsar un <details>.
    for banned in ("addEventListener(\"dragstart\"", "addEventListener(\"dragover\"", "addEventListener(\"drop\""):
        assert banned not in _COMMERCIAL_JS
    # Única excepción narrow a ".open =" (owner corrective, ver HANDOFF: la
    # X de cierre ahora vive en <summary> y sobrevive el colapso nativo del
    # `<details>` — pero eso deja la sección "visible pero colapsada" si el
    # owner la había colapsado a mano antes de cerrarla, porque
    # `initEventBreakdown` sólo gobierna `hidden` — mostrar/ocultar el
    # bloque — nunca `open` — expandido/colapsado ADENTRO de ese bloque:
    # son dos ejes independientes que sólo esta sección combina). Esto NO
    # reimplementa collapse (no hay listener propio de click en <summary>
    # para togglear `open` — eso lo sigue haciendo el navegador nativo):
    # sólo RESETEA `open` a un estado conocido-bueno exactamente cuando el
    # motor compartido activa la sección, dentro de la función puntual que
    # ya maneja esa transición (`commercialMaybeScrollToOpenedAnalysis`).
    # Se audita que sea EXACTAMENTE esa única línea, no una reimplementación
    # más amplia coleándose por el mismo hueco.
    assert _COMMERCIAL_JS.count(".open =") == 1
    scroll_fn = _COMMERCIAL_JS.split("function commercialMaybeScrollToOpenedAnalysis(", 1)[1].split("\nfunction ", 1)[0]
    assert "section.open = true;" in scroll_fn
    assert "addEventListener" not in scroll_fn


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

def test_commercial_view_switch_lives_in_the_hero_next_to_the_subtitle():
    # CONTRATO ACTUALIZADO DELIBERADAMENTE (Landing Homologation). Hasta acá
    # el filtro global vivía en `.dashboard-sticky-header`, una franja propia
    # entre el hero y la navegación. El owner lo movió a la MISMA línea del
    # subtítulo: sigue siendo el mismo view control transversal, pero deja de
    # gastar una banda entera del landing. Ya no necesita el sticky header.
    filter_block = _COMMERCIAL_JS.split('function commercialFilterHtml()', 1)[1].split('\n}\n', 1)[0]
    assert '"dashboard-view-switch"' in filter_block
    assert '"dashboard-sticky-header"' not in _COMMERCIAL_JS
    # Se monta en el hero, FUERA del contenedor que se reemplaza al conmutar
    # de capacidad.
    assert 'getElementById("commercial-hero-filter")' in _COMMERCIAL_JS
    crm_html = (Path(__file__).resolve().parents[2] / "webapp" / "templates" / "commercial" / "crm.html").read_text(encoding="utf-8")
    assert 'id="commercial-hero-filter"' in crm_html
    assert crm_html.index("hero-lead-row") < crm_html.index('id="commercial-main-content"')


def test_capability_nav_and_capability_panel_are_the_only_things_inside_main_content():
    # El filtro global salió de `#commercial-main-content` (vive en el hero),
    # así que el shell queda con la navegación y el panel de la capacidad.
    render_fn = _COMMERCIAL_JS.split("function commercialRender(", 1)[1].split("\nfunction ", 1)[0]
    assert render_fn.index("commercialNavHtml()") < render_fn.index('id="commercial-capability-panel"')
    dashboard_fn = _COMMERCIAL_JS.split("function commercialDashboardHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert "commercialDashboardSections(" in dashboard_fn


def test_commercial_now_navigates_capabilities_with_the_shared_tab_primitive():
    # CONTRATO ACTUALIZADO DELIBERADAMENTE EN BATCH 3A. Hasta 2B este test
    # exigía que Comercial NO usara `experience-tabs` — era cierto mientras
    # existía una sola capability. Con Dashboard + Actividades la navegación
    # es real, y usa el primitivo COMPARTIDO, no una barra propia.
    nav_fn = _COMMERCIAL_JS.split("function commercialNavHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert 'class="experience-tabs"' in nav_fn
    # Sigue sin haber CSS propio de la vertical para la navegación.
    assert "commercial-tabs" not in _COMMERCIAL_JS
    assert "commercial-nav" not in _COMMERCIAL_JS


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
# Table Spine corrective — la tabla de detalle de Oportunidades Activas
# (C4, `commercialOpportunitiesTableHtml`) adopta el MISMO contrato de
# distribución de columnas ya compartido en tools.css (`.row-label-col` +
# ancho default de columnas métricas), en vez de que la columna descriptiva
# absorba todo el ancho sobrante bajo `table-layout:fixed`. Reemplaza el
# placeholder agrupado-por-etapa (Batch 2A, `commercialPipelineTableHtml`,
# retirado en C4) por el detalle real por oportunidad — ver
# commercial_opportunities_table.node.js para las invariantes de
# renderizado/orden reales (columnas, Etiquetas ausente, totales, sort).
# ============================================================================

def test_commercial_pipeline_table_placeholder_no_longer_exists():
    # C4 (ver HANDOFF) retira por completo el placeholder de Batch 2A —
    # nunca queda como código muerto detrás de la tabla real.
    assert "commercialPipelineTableHtml" not in _COMMERCIAL_JS


def test_commercial_opportunities_table_still_uses_the_shared_table_primitive():
    assert 'class="dashboard-table"' in _COMMERCIAL_JS


def test_commercial_opportunities_table_is_wrapped_in_the_shared_scroll_container():
    table_fn = _COMMERCIAL_JS.split("function commercialOpportunitiesTableHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert '<div class="matrix-scroll">' in table_fn
    assert table_fn.index('<div class="matrix-scroll">') < table_fn.index("<table")


def test_commercial_opportunities_descriptive_column_adopts_row_label_col():
    table_fn = _COMMERCIAL_JS.split("function commercialOpportunitiesTableHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert '"row-label-col"' in table_fn
    assert '<td class="row-label">${commercialEsc(o.opportunity)}</td>' in table_fn


def test_no_commercial_specific_css_for_table_column_distribution():
    _TOOLS_CSS = (_STATIC / "tools.css").read_text(encoding="utf-8")
    _APP_CSS = (_STATIC / "app.css").read_text(encoding="utf-8")
    for banned in (".commercial-table", ".commercial-opportunities-table", ".commercial-col"):
        assert banned not in _TOOLS_CSS
        assert banned not in _APP_CSS
        assert banned not in _COMMERCIAL_JS


def test_commercial_opportunities_numeric_alignment_still_inherited_not_overridden():
    # Ningún `text-align` inline ni override puntual — la alineación numérica
    # sigue viniendo del default compartido (`table.dashboard-table td`).
    table_fn = _COMMERCIAL_JS.split("function commercialOpportunitiesTableHtml(", 1)[1].split("\nfunction ", 1)[0]
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


def test_commercial_opportunities_table_has_a_totals_row():
    # MISMO patrón que Reporting: `tr.is-total`/`td.row-label`, rollup
    # client-side sobre las oportunidades ya recibidas — nunca un query/campo
    # de total propio en el backend. Sólo Hs/Hs Pond. tienen semántica
    # aditiva sin ambigüedad (ver comentario en la función) — Prob./Prior./
    # Días resp. deliberadamente NO se totalizan.
    table_fn = _COMMERCIAL_JS.split("function commercialOpportunitiesTableHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert '<tr class="is-total">' in table_fn
    assert 'rows.reduce(' in table_fn
    assert "${totalRow}" in table_fn


def test_commercial_opportunities_table_never_renders_an_etiquetas_column():
    # tag_ids sólo particiona todos/nn/bi en memoria — nunca viaja en la
    # fila (contrato ya decidido en el backend, `_build_opportunities`/
    # HANDOFF: "No Etiquetas in output UI contract"). La UI lo hereda: la
    # tabla nunca emite un header/celda literal para esa columna (se permite
    # que el nombre aparezca en prosa/comentarios explicando la decisión).
    table_fn = _COMMERCIAL_JS.split("function commercialOpportunitiesTableHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert ">Etiquetas<" not in table_fn
    assert "o.tag_ids" not in table_fn and "o.tags" not in table_fn


def test_commercial_lic_and_dias_resp_columns_are_intentionally_not_sortable():
    # MISMA decisión que el legacy (`renderTable`): esas dos columnas nunca
    # reciben `makeSortTh` — se preserva acá en vez de volver todo ordenable
    # por default.
    table_fn = _COMMERCIAL_JS.split("function commercialOpportunitiesTableHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert "<th>Lic.</th>" in table_fn
    assert "<th>Días resp.</th>" in table_fn
    assert 'data-commercial-sort="licencias"' not in table_fn
    assert 'data-commercial-sort="days_elapsed"' not in table_fn


def test_commercial_opportunities_data_acquisition_extends_load_data_without_removing_existing_fetches():
    # C4 extiende `commercialLoadData` con un 4to fetch en paralelo — MISMO
    # patrón ya establecido, nunca una segunda arquitectura de carga. Los 3
    # fetches existentes (summary/pipeline/analytics) siguen presentes.
    load_fn = _COMMERCIAL_JS.split("async function commercialLoadData(", 1)[1].split("\nasync function ", 1)[0]
    assert "fetch(`/comercial/data/summary?${qs}`)" in load_fn
    assert "fetch(`/comercial/data/pipeline?${qs}`)" in load_fn
    assert "fetch(`/comercial/data/analytics?${qs}`)" in load_fn
    assert "fetch(`/comercial/data/opportunities?${qs}`)" in load_fn
    assert "commercialState.opportunities = opportunitiesResp.state" in load_fn


def test_commercial_set_tipo_invalidates_opportunities_like_the_other_datasets():
    tipo_fn = _COMMERCIAL_JS.split("function commercialSetTipo(", 1)[1].split("\nfunction ", 1)[0]
    assert "commercialState.opportunities = null;" in tipo_fn


def test_commercial_dashboard_spine_and_view_switch_survive_table_corrective():
    # Este corrective es EXCLUSIVAMENTE table layout/detalle — el resto del
    # spine (secciones + view control) sigue intacto.
    assert '"dashboard-view-switch"' in _COMMERCIAL_JS
    assert 'commercialDashboardSection("resumen", "Resumen",' in _COMMERCIAL_JS
    assert 'Oportunidades Activas — Análisis' in _COMMERCIAL_JS


# ============================================================================
# Batch 2A — KPI → Contextual Drilldown (Oportunidades Activas). Audit live
# legacy confirmó que la tabla stages 1-8 corresponde semánticamente a
# Oportunidades Activas, no a Pipeline Total — este batch reposiciona esa
# tabla como el detalle contextual del ÚNICO KPI clickeable de este batch,
# reusando el motor shared `initEventBreakdown` (app.js), nunca un handler
# de click propio de Comercial.
# ============================================================================

def test_only_analyses_with_a_homologated_drilldown_are_clickable():
    # Landing Homologation + C6 (ver HANDOFF): los tiles se emiten con
    # `commercialRichKpi`, y `eventKey` es lo único que agrega la afordancia de
    # click. Lo reciben TRES tiles, sobre DOS análisis homologados —
    # "Oportunidades activas", y "Pipeline total"/"Pipeline ponderado", que
    # comparten la MISMA key porque son la misma población (0-8) medida con y
    # sin probabilidad y el panel de Forecast presenta las dos series juntas.
    # Los otros seis conservan su información SIN simular una acción
    # inexistente.
    dashboard_fn = _COMMERCIAL_JS.split("function commercialDashboardHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert dashboard_fn.count("eventKey:") == 3
    assert dashboard_fn.count('eventKey: "activas"') == 1
    assert dashboard_fn.count('eventKey: "forecast"') == 2
    # La afordancia (clase + role + tabindex) sale SÓLO cuando hay eventKey.
    rich = _COMMERCIAL_JS.split("function commercialRichKpi(", 1)[1].split("\nfunction ", 1)[0]
    assert "eventKey ?" in rich and "activity-kpi--clickable" in rich


def test_other_kpi_tiles_remain_display_only():
    # Ningún tile fuera de Activas y los dos Pipeline recibe `eventKey`, así
    # que ninguno queda clickeable — sin afordancia falsa.
    dashboard_fn = _COMMERCIAL_JS.split("function commercialDashboardHtml(", 1)[1].split("\nfunction ", 1)[0]
    for label in ("Prospectos", "Ganadas", "Perdidas", "Suspendidas",
                  "Creadas esta semana", "Conversión"):
        tile = dashboard_fn.split(f'commercialRichKpi("{label}"', 1)
        assert len(tile) == 2, label
        assert "eventKey" not in tile[1].split("}", 1)[0], label


def test_clickable_kpi_reuses_the_shared_activity_kpi_clickable_modifier():
    # `commercialClickableKpi` se fusionó en `commercialRichKpi` (Landing
    # Homologation): un solo generador de tile, y la afordancia sale sólo con
    # `eventKey`. El contrato que importa —reusar el modificador COMPARTIDO,
    # no inventar uno propio— sigue vigente.
    rich_fn = _COMMERCIAL_JS.split("function commercialRichKpi(", 1)[1].split("\nfunction ", 1)[0]
    assert "activity-kpi--clickable" in rich_fn
    assert "data-event=" in rich_fn
    assert 'role="button"' in rich_fn
    assert 'tabindex="0"' in rich_fn
    assert "aria-label=" in rich_fn


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
    # Landing Homologation: ya NO hay panel placeholder. `data-breakdown` va
    # sobre la SECCIÓN, que arranca `hidden`, así que el análisis no reserva
    # espacio en el landing y desaparece entero al deseleccionar.
    dashboard_fn = _COMMERCIAL_JS.split("function commercialDashboardHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert 'data-breakdown="activas" open hidden' in dashboard_fn
    assert 'data-breakdown=""' not in dashboard_fn
    assert "Hacé clic en" not in _COMMERCIAL_JS


def test_breakdown_group_attribute_wraps_the_dashboard_sections_container():
    sections_fn = _COMMERCIAL_JS.split("function commercialDashboardSections(", 1)[1].split("\nfunction ", 1)[0]
    assert "data-breakdown-group" in sections_fn
    # Coexiste con data-dashboard-key en el MISMO div — contratos ortogonales.
    assert "data-dashboard-key=" in sections_fn


def test_active_opportunities_analysis_contains_the_stage_1_8_table_not_pipeline_total():
    dashboard_fn = _COMMERCIAL_JS.split("function commercialDashboardHtml(", 1)[1].split("\nfunction ", 1)[0]
    # Batch 2B: el panel se COMPONE (gráfico arriba + tabla abajo) en
    # `commercialActivasAnalysisHtml`, que es quien invoca la tabla. Batch 3A
    # sólo movió ese bloque de `commercialRender` a `commercialDashboardHtml`
    # al aparecer la segunda capability — mismo universo, mismo dueño.
    assert "commercialActivasAnalysisHtml(p)" in dashboard_fn
    analysis_fn = _COMMERCIAL_JS.split("function commercialActivasAnalysisHtml(", 1)[1].split("\nfunction ", 1)[0]
    # C4 (ver HANDOFF): el placeholder agrupado-por-etapa fue reemplazado por
    # el detalle real por oportunidad. C5e (ver HANDOFF — filtro Etapa/
    # Vendedor/Buscar) intercala la derivación local entre `pipeline` y la
    # tabla: `commercialOpportunitiesBlockHtml` (leyenda + tabla juntas,
    # sobre las filas YA filtradas/buscadas) reemplaza la llamada directa a
    # `commercialOpportunitiesTableHtml(commercialState.opportunities)` —
    # ese acceso crudo es justo lo que un filtro activo necesita EVITAR.
    assert "commercialOpportunitiesBlockHtml(tableRows)" in analysis_fn
    assert "commercialDistribucionHtml(effectivePipeline)" in analysis_fn
    # Ya no existe la sección paralela "Pipeline por Etapa" ligada a Pipeline Total.
    assert "Pipeline por Etapa" not in _COMMERCIAL_JS
    assert 'commercialDashboardSection("pipeline",' not in _COMMERCIAL_JS


def test_funnel_carries_no_dimension_switch_state_or_wiring():
    # C5a retiró el switch Horas|Ponderadas (C3.1): sin estado de dimensión,
    # sin handler de click propio, sin atributo que lo reconstruya.
    assert "horasDim" not in _COMMERCIAL_JS
    assert "data-commercial-dim" not in _COMMERCIAL_JS
    assert "dimButton" not in _COMMERCIAL_JS


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
    # primitivo compartido o no dibuja. Los cuatro llevan el punto: son
    # nombres de CLASE CSS baneados, nunca un ban de substring a secas — sin
    # el punto, "commercial-funnel" también matchea el atributo LEGÍTIMO
    # `data-commercial-funnel-area` (C5e, ver HANDOFF: los `data-*` con
    # prefijo `commercial` SÍ están permitidos, sólo las clases CSS no).
    tools_css = (_STATIC / "tools.css").read_text(encoding="utf-8")
    for banned in (".commercial-funnel", ".commercial-chart", ".commercial-breakdown", ".commercial-analysis"):
        assert banned not in _COMMERCIAL_JS
        assert banned not in tools_css
    # OWNERSHIP (gate del corrective visual): la composición del gráfico
    # (`.category-plot`) NO es un primitivo elemental del Hub — es este
    # gráfico concreto: canaletas de eje ajustadas a sus rótulos, altura
    # calibrada a su footprint, color de cada serie acoplado al de su eje, y
    # una leyenda que sólo sabe expresar EXACTAMENTE tres series de barras
    # (Active Opportunities Phase 2 / C3/C5a — grouped bars, restaura el
    # Funnel legacy y muestra Oportunidades/Horas/Ponderadas simultáneas).
    # Vive en el CSS de la vertical, con el MISMO mecanismo que
    # `reporting.css`: stylesheet propio linkeado desde su template, nunca
    # `base.html`. Se promoverá el día que exista un segundo consumidor real.
    commercial_css = (_STATIC / "commercial.css").read_text(encoding="utf-8")
    for rule in (".category-plot {", ".category-plot__area {", ".category-plot__bar {",
                 ".category-plot__bar--count {", ".category-plot__bar--hours {",
                 ".category-plot__bar--weighted {",
                 ".category-plot__axis {", ".category-plot-legend {"):
        assert rule in commercial_css, rule
        assert rule not in tools_css, rule
    crm_html = (Path(__file__).resolve().parents[2] / "webapp" / "templates" / "commercial" / "crm.html").read_text(encoding="utf-8")
    assert "path='commercial.css'" in crm_html
    base_html = (Path(__file__).resolve().parents[2] / "webapp" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "commercial.css" not in base_html  # nunca global


def test_commercial_local_css_does_not_duplicate_shared_hub_primitives():
    # El CSS de la vertical aporta SOLO su composición: no re-declara los
    # primitivos que el Hub ya provee.
    # Se miran las REGLAS, no la prosa: el encabezado del archivo enumera a
    # propósito los primitivos compartidos que consume, y un scan del texto
    # crudo daría un falso positivo por esa misma lista (patrón ya conocido
    # en este repo — un comentario que menciona el literal prohibido).
    raw = (_STATIC / "commercial.css").read_text(encoding="utf-8")
    rules = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    # Un override POSICIONAL dentro de un contenedor propio es legítimo (lo
    # mismo hace reporting.css con `.dashboard-view-switch`); lo prohibido es
    # RE-DEFINIR el primitivo, o sea abrir un bloque con su selector desnudo.
    for shared in (".chart-tooltip", ".dashboard-view-switch", ".perspective-label",
                   ".staffing-timeline-toolbar", ".dashboard-table", ".matrix-scroll", ".activity-kpi"):
        for line in rules.splitlines():
            stripped = line.strip()
            assert not stripped.startswith(shared + " {"), (shared, stripped)
            assert not stripped.startswith(shared + "{"), (shared, stripped)


def test_commercial_charts_introduce_no_library_canvas_or_external_dependency():
    # Active Opportunities Phase 2 / C3/C5a (restore legacy funnel
    # representation + 3-series simultáneas): el chart es 100% DOM puro
    # (grouped bars, tres `<div>` por etapa) — cero SVG, cero canvas, cero
    # librería. La versión anterior ("A'", barra + línea con puntos) usaba
    # un SVG inline; se retiró junto con el resto de esa representación, no
    # queda ningún SVG en el archivo.
    for banned in ("<canvas", "<svg", "<polyline", "new Chart(", "Chart.js", "chart.js", "d3.", "import ", "require("):
        assert banned not in _COMMERCIAL_JS, banned


def test_grouped_bar_columns_are_the_sole_accessible_surface():
    # La superficie accesible por etapa es la COLUMNA (aria-label + tooltip
    # compartido) — las TRES barras (Oportunidades/Horas/Ponderadas, C5a)
    # son mudas, sin texto ni foco propio, para no duplicar la misma
    # magnitud varias veces en el lector de pantalla.
    col = _COMMERCIAL_JS.split("const cols = stages.map(", 1)[1].split("}).join", 1)[0]
    assert "category-plot__col" in col
    assert 'tabindex="0"' in col
    assert "aria-label=" in col
    assert "commercialChartTooltip(lines)" in col
    assert "category-plot__bar--count" in col and "category-plot__bar--hours" in col and "category-plot__bar--weighted" in col
    # UN solo tabindex/aria-label por columna — nunca uno por barra/serie.
    assert col.count('tabindex="0"') == 1
    assert col.count("aria-label=") == 1


def test_no_other_kpi_gained_a_drilldown_this_batch():
    # Ganadas, Perdidas, Prospectos, Suspendidas, Creadas, Target: ninguno gana
    # `data-event` ni panel propio — contar solo MARKUP real dentro de cada
    # función, no menciones en comentarios (que mencionan
    # `data-event`/`data-breakdown` varias veces).
    rich_fn = _COMMERCIAL_JS.split("function commercialRichKpi(", 1)[1].split("\nfunction ", 1)[0]
    assert rich_fn.count('data-event="') == 1  # un solo trigger posible por tile
    # DOS `data-breakdown` reales en el markup — la sección de Activas y, desde
    # C6 (ver HANDOFF), la de Forecast — + UNA referencia al MISMO atributo
    # dentro del selector que construye `commercialMaybeScrollToOpenedAnalysis`
    # (C3.1, ver HANDOFF — scroll al abrir) para ubicar la que corresponda.
    # Nunca un TERCER panel, y nunca un grupo de breakdown paralelo: los dos
    # viven en el MISMO `[data-breakdown-group]`, que es lo que hace que el
    # motor compartido muestre uno por vez. Se cuenta sobre el markup, no sobre
    # los comentarios, que mencionan el atributo en prosa varias veces.
    markup = "\n".join(line for line in _COMMERCIAL_JS.splitlines()
                       if not line.strip().startswith("//"))
    assert markup.count('data-breakdown="') == 3
    assert markup.count("data-breakdown-group>") == 1
    # Ningún otro helper (commercialKpi, el display-only) emite data-event.
    plain_kpi_fn = _COMMERCIAL_JS.split("function commercialKpi(", 1)[1].split("\n\n", 1)[0]
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


# ============================================================================
# Active Opportunities Phase 2 / C1 — GET /comercial/data/opportunities
# ============================================================================

def test_opportunities_endpoint_anonymous_redirects_to_login():
    anon = TestClient(app)
    response = anon.get("/comercial/data/opportunities", follow_redirects=False)
    assert response.status_code == 303


def test_opportunities_endpoint_unlisted_user_is_denied(monkeypatch):
    ro = _unlisted_client(monkeypatch)
    assert ro.get("/comercial/data/opportunities").status_code == 403


def test_opportunities_endpoint_returns_ok_state_and_payload(monkeypatch):
    class _OppStub(_StubService):
        def get_opportunities(self, tipo="todos"):
            return {"tipo": tipo, "opportunities": [], "total": 0}
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _OppStub())
    response = client.get("/comercial/data/opportunities?tipo=nn")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "ok"
    assert body["opportunities"]["tipo"] == "nn"


def test_opportunities_endpoint_is_fail_soft_like_the_rest_of_commercial(monkeypatch):
    class _Boom(_StubService):
        def get_opportunities(self, tipo="todos"):
            raise CommercialConfigurationError("no config")
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _Boom())
    response = client.get("/comercial/data/opportunities")
    assert response.status_code == 200
    assert response.json() == {"state": "config_incomplete"}


def test_opportunities_endpoint_source_error_carries_stage(monkeypatch):
    class _Boom(_StubService):
        def get_opportunities(self, tipo="todos"):
            raise CommercialDataError("boom", stage="execute")
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _Boom())
    response = client.get("/comercial/data/opportunities")
    assert response.status_code == 200
    assert response.json() == {"state": "source_error", "stage": "execute"}


def test_opportunities_endpoint_never_exposes_a_write_verb():
    for verb in ("post", "put", "patch", "delete"):
        response = getattr(client, verb)("/comercial/data/opportunities")
        assert response.status_code == 405, (verb, response.status_code)


# ============================================================================
# Active Opportunities Phase 2 / C2 — GET /comercial/data/forecast
# ============================================================================

def test_forecast_endpoint_anonymous_redirects_to_login():
    anon = TestClient(app)
    response = anon.get("/comercial/data/forecast", follow_redirects=False)
    assert response.status_code == 303


def test_forecast_endpoint_unlisted_user_is_denied(monkeypatch):
    ro = _unlisted_client(monkeypatch)
    assert ro.get("/comercial/data/forecast").status_code == 403


def test_forecast_endpoint_returns_ok_state_and_payload(monkeypatch):
    class _ForecastStub(_StubService):
        def get_forecast(self, tipo="todos"):
            return {"tipo": tipo, "months": []}
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _ForecastStub())
    response = client.get("/comercial/data/forecast?tipo=bi")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "ok"
    assert body["forecast"]["tipo"] == "bi"


def test_forecast_endpoint_is_fail_soft_like_the_rest_of_commercial(monkeypatch):
    class _Boom(_StubService):
        def get_forecast(self, tipo="todos"):
            raise CommercialConfigurationError("no config")
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _Boom())
    response = client.get("/comercial/data/forecast")
    assert response.status_code == 200
    assert response.json() == {"state": "config_incomplete"}


def test_forecast_endpoint_source_error_carries_stage(monkeypatch):
    class _Boom(_StubService):
        def get_forecast(self, tipo="todos"):
            raise CommercialDataError("boom", stage="execute")
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _Boom())
    response = client.get("/comercial/data/forecast")
    assert response.status_code == 200
    assert response.json() == {"state": "source_error", "stage": "execute"}


def test_forecast_endpoint_never_exposes_a_write_verb():
    for verb in ("post", "put", "patch", "delete"):
        response = getattr(client, verb)("/comercial/data/forecast")
        assert response.status_code == 405, (verb, response.status_code)


# ============================================================================
# Batch 3A / C3 — capability navigation
# ============================================================================

def test_only_implemented_capabilities_are_listed():
    registry = _COMMERCIAL_JS.split("const COMMERCIAL_CAPABILITIES = [", 1)[1].split("];", 1)[0]
    assert registry.count('id: "') == 2
    assert '{ id: "dashboard", label: "Dashboard" }' in registry
    assert '{ id: "actividades", label: "Actividades" }' in registry
    # Ninguna tab muerta: una capability sin funcionalidad detrás es una
    # promesa rota.
    for not_yet in ("licencias", "clientes", "leads", "mis-tareas", "historial", "prospectos"):
        assert f'id: "{not_yet}"' not in registry


def test_dashboard_is_the_default_capability():
    state = _COMMERCIAL_JS.split("const commercialState = {", 1)[1].split("\n};", 1)[0]
    assert 'capability: "dashboard"' in state
    assert 'activitiesStatus: "idle"' in state


def test_capability_nav_uses_shared_primitive_and_minimal_aria():
    nav = _COMMERCIAL_JS.split("function commercialNavHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert 'class="experience-tabs"' in nav
    assert 'aria-label="Capacidades comerciales"' in nav
    assert 'aria-current="true"' in nav
    assert 'aria-controls="commercial-capability-panel"' in nav
    # NO se declara un tablist ARIA incompleto (sin roving tabindex ni flechas).
    for banned in ('role="tablist"', 'role="tab"', 'aria-selected'):
        assert banned not in nav, banned


def test_global_filter_lives_outside_the_capability_panel():
    render = _COMMERCIAL_JS.split("function commercialRender(", 1)[1].split("\nfunction ", 1)[0]
    assert "commercialNavHtml()" in render
    assert "commercialFilterHtml()" in render
    assert 'id="commercial-capability-panel"' in render
    # El filtro global se emite UNA vez y fuera del panel: no se duplica
    # dentro de cada capability.
    assert render.count("commercialFilterHtml()") == 1
    capability = _COMMERCIAL_JS.split("function commercialCapabilityHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert "commercialFilterHtml" not in capability


def test_activities_are_lazy_and_never_fetched_for_the_dashboard():
    boot = _COMMERCIAL_JS.split('document.addEventListener("DOMContentLoaded"', 1)[1]
    assert "commercialLoadActivities" not in boot, "el arranque NUNCA pide actividades"
    assert "commercialLoadData();" in boot
    switch = _COMMERCIAL_JS.split("function commercialSetCapability(", 1)[1].split("\nfunction ", 1)[0]
    # Sólo se pide si nunca se pidió (o quedó stale): una re-entrada con el
    # dataset ya listo no repite el fetch.
    assert 'commercialState.activitiesStatus === "idle"' in switch
    assert "commercialLoadActivities()" in switch


def test_global_filter_change_invalidates_both_datasets_but_reloads_only_the_active_one():
    fn = _COMMERCIAL_JS.split("function commercialSetTipo(", 1)[1].split("\nfunction ", 1)[0]
    assert 'commercialState.status = "idle"' in fn
    assert 'commercialState.activitiesStatus = "idle"' in fn
    assert 'if (commercialState.capability === "actividades") commercialLoadActivities();' in fn
    assert "else commercialLoadData();" in fn


def test_activity_buckets_are_never_draggable_or_reorderable():
    fn = _COMMERCIAL_JS.split("function commercialStaticSection(", 1)[1].split("\nfunction ", 1)[0]
    assert "draggable" not in fn, "el orden Retrasadas → Pendientes es semántico"
    assert "dashboard-section__grip" not in fn
    # Y no se registran en el spine que wirea reorder/persistencia.
    actividades = _COMMERCIAL_JS.split("function commercialActividadesHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert "data-dashboard-key" not in actividades
    assert "commercialDashboardSections" not in actividades


def test_activity_local_filters_never_refetch_or_touch_global_state():
    handler = _COMMERCIAL_JS.split('const periodButton = event.target.closest("[data-commercial-act-period]");', 1)[1]
    branch = handler.split("const button = event.target.closest", 1)[0]
    assert "commercialRenderCapability()" in branch
    for banned in ("fetch(", "commercialLoadActivities", "commercialLoadData", "commercialState.tipo ="):
        assert banned not in branch, banned


def test_activity_rows_escape_every_odoo_sourced_field():
    fn = _COMMERCIAL_JS.split("function commercialActivityTableHtml(", 1)[1].split("\nfunction ", 1)[0]
    for field in ("act.type", "act.opportunity", "act.client", "act.user", "detail"):
        assert f"commercialEsc({field})" in fn, field
    # La normalización server-side del `note` NO reemplaza el escaping.
    assert "commercialEsc(commercialFmtDate(act.deadline))" in fn


def test_activities_capability_only_shows_the_two_supported_kpis():
    fn = _COMMERCIAL_JS.split("function commercialActividadesHtml(", 1)[1].split("\nfunction ", 1)[0]
    assert 'commercialKpi("Total pendientes"' in fn
    assert 'commercialKpi("Retrasadas"' in fn
    assert fn.count("commercialKpi(") == 2, "sin métricas inventadas"
    assert "Actividades Pendientes" in fn


# ============================================================================
# Landing Homologation — endpoint de Analíticas del Pipeline
# ============================================================================

def test_analytics_endpoint_anonymous_redirects_to_login():
    anon = TestClient(app)
    assert anon.get("/comercial/data/analytics", follow_redirects=False).status_code == 303


def test_analytics_endpoint_unlisted_user_is_denied(monkeypatch):
    ro = _unlisted_client(monkeypatch)
    assert ro.get("/comercial/data/analytics").status_code == 403


def test_analytics_endpoint_returns_ok_state_and_payload(monkeypatch):
    class _Stub(_StubService):
        def get_analytics(self, tipo="todos"):
            return {"tipo": tipo, "creadas": {"this_week": 2, "last_week": 3}}
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _Stub())
    response = client.get("/comercial/data/analytics?tipo=bi")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "ok"
    assert body["analytics"]["tipo"] == "bi"


def test_analytics_endpoint_is_fail_soft_like_the_rest_of_commercial(monkeypatch):
    class _Boom(_StubService):
        def get_analytics(self, tipo="todos"):
            raise CommercialDataError("boom", stage="execute")
    monkeypatch.setattr(commercial_router, "CommercialService", lambda: _Boom())
    response = client.get("/comercial/data/analytics")
    assert response.status_code == 200
    assert response.json() == {"state": "source_error", "stage": "execute"}


def test_analytics_endpoint_never_exposes_a_write_verb():
    for verb in ("post", "put", "patch", "delete"):
        assert getattr(client, verb)("/comercial/data/analytics").status_code == 405, verb


def test_dashboard_hydration_fetches_analytics_but_never_blocks_on_it():
    # Las analíticas son la consulta más pesada (historial de Odoo). Si
    # fallan, el landing se muestra igual y sólo ese bloque avisa.
    load_fn = _COMMERCIAL_JS.split("async function commercialLoadData(", 1)[1].split("\nasync function", 1)[0]
    assert "/comercial/data/analytics" in load_fn
    assert 'analyticsResp.state === "ok" ? analyticsResp.analytics : null' in load_fn
    # El estado ready NO depende de que las analíticas hayan salido bien.
    assert 'summaryResp.state === "ok" && pipelineResp.state === "ok"' in load_fn


def test_analytics_service_never_writes_and_reads_only_known_models():
    # Lectura pura: el service de analíticas no llama nada que no sea
    # search_read sobre los modelos del CRM/historial.
    analytics_block = _COMMERCIAL_SERVICE_PY.split("def _build_analytics(", 1)[1].split("\n    def get_analytics", 1)[0]
    for banned in (".create(", ".write(", ".unlink(", "execute_kw"):
        assert banned not in analytics_block, banned
    for model in ('"crm.stage"', '"crm.lead"', '"mail.message"'):
        assert model in _COMMERCIAL_SERVICE_PY, model
