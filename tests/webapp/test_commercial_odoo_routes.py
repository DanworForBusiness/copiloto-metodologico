"""
test_commercial_odoo_routes.py — Commercial Hub Odoo Batch 1: contrato HTTP
de `GET /comercial/data/summary|pipeline` + contratos de fuente (loader
canónico, ausencia de auth/JWT legacy) — CI NUNCA depende de Odoo real
(mockea `webapp.routers.commercial.CommercialService` en el boundary del
router, MISMO patrón que `test_staffing_routes.py` con `StaffingService`).
"""
from __future__ import annotations

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
    assert "querySelector" not in _COMMERCIAL_JS
    assert "setInterval" not in _COMMERCIAL_JS  # el timer del progress vive SOLO en HubForegroundProgress


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
    assert 'commercialDashboardSection("pipeline", "Pipeline por Etapa",' in _COMMERCIAL_JS
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
    # Mismos 3 valores, mismo atributo de datos, mismo mecanismo de toggle —
    # el corrective es puramente estructural (contenedor), cero cambio de
    # comportamiento o copy.
    assert '[["todos", "Todos"], ["nn", "NN"], ["bi", "BI"]]' in _COMMERCIAL_JS
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
    assert 'commercialDashboardSection("pipeline", "Pipeline por Etapa",' in _COMMERCIAL_JS


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
