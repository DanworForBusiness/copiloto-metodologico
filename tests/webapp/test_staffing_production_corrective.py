"""
test_staffing_production_corrective.py — "STAFFING DIRECTOR V2 — PRODUCTION
BEHAVIOR CORRECTIVE" (ver HANDOFF). El owner reportó, con datos reales:

  1. el tile KPI "Asignaciones tentativas" seguía visible aunque Tentativo
     no estuviera tildado;
  2. tildar Tentativo "no hacía nada" visible;
  3. tildar Inactivo mostraba resultados sin feedback de carga perceptible;
  4. la carga inicial seguía ~6s, similar al comportamiento pre-optimización.

Este archivo prueba lo que SÍ está bajo control de este repo (código,
contrato cliente/servidor, instrumentación) — NUNCA el estado real del
tenant de SharePoint, que este entorno no puede consultar (sin Graph token
delegado real). Ver HANDOFF: "REAL GRAPH FILTER — PENDING OWNER/PRODUCTION
SMOKE" para el límite explícito de qué NO se puede probar acá.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import staffing as staffing_router
from webapp.services import auth_service
from webapp.services.staffing_repository import StaffingFieldMapping, StaffingRepository
from webapp.services import staffing_repository as repo_module
from webapp.services.staffing_service import StaffingOverviewRow, StaffingService

_APP_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
_DASHBOARD_HTML = (
    Path(__file__).resolve().parents[2] / "webapp" / "templates" / "operations" / "staffing_dashboard.html"
).read_text(encoding="utf-8")


# ============================================================================
# PART A — Status-dependent KPI visibility (tests 1-4)
# ============================================================================

def _kpi_block(label: str) -> str:
    idx = _DASHBOARD_HTML.index(f">{label}</span>")
    start = _DASHBOARD_HTML.rindex('<div class="activity-kpi', 0, idx)
    return _DASHBOARD_HTML[start:idx]


def test_1_default_activo_only_does_not_render_tentativas_kpi_tile():
    # El tile en sí lleva x-show ligado al MISMO estado canónico de status
    # que gobierna el dashboard — con Activo-only default, Tentativo es
    # false, así que Alpine nunca lo muestra (display:none, sale del grid).
    block = _kpi_block("Asignaciones tentativas")
    assert 'x-show="filters.status.Tentativo"' in block


def test_2_and_3_tentativas_kpi_visibility_is_driven_by_the_canonical_status_filter():
    # Tildar/destildar Tentativo es el ÚNICO input que decide la
    # visibilidad — no hay una copia de estado paralela ni un flag propio.
    block = _kpi_block("Asignaciones tentativas")
    assert 'x-show="filters.status.Tentativo"' in block
    assert "showTentativoKpis" not in _DASHBOARD_HTML  # sin getter derivado redundante


def test_4_active_specific_kpis_follow_activo_selection():
    for label in ("Asignaciones activas", "Horas activas / mes", "Activos vencidos"):
        block = _kpi_block(label)
        assert 'x-show="filters.status.Activo"' in block, f"{label} debe depender de filters.status.Activo"


def test_generic_kpis_always_visible_never_gated_by_status():
    for label in ("Consultores", "Proyectos", "Full Time", "Part Time"):
        block = _kpi_block(label)
        assert "x-show=" not in block, f"{label} debe estar siempre visible"


def test_kpi_grid_uses_x_show_so_hidden_tiles_leave_the_grid_flow():
    # x-show pone display:none — un grid CSS auto-placement salta esos
    # elementos por completo (nunca ocupan un slot vacío). Nunca
    # visibility:hidden/opacity, que SÍ dejarían un hueco.
    assert "visibility:hidden" not in _DASHBOARD_HTML
    assert "opacity:0" not in _DASHBOARD_HTML.replace(" ", "")


# ============================================================================
# PART B/C — Pipeline trace: initial payload contents (tests 5-6)
# ============================================================================
# NUNCA se fabrica "el estado real del tenant" como prueba de qué hay en
# SharePoint (prohibido explícitamente por el director) — se prueba la
# CADENA de código real (router → service → repository) con fixtures
# determinísticas, exactamente igual que el resto de esta suite.

_SITE_ID = "test-site-id"
_ASIG_LIST = "asig-list-id"
_PROY_LIST = "proy-list-id"
_COLAB_LIST = "colab-list-id"
_MAPPING = StaffingFieldMapping(
    colaborador_lookup_id="ColaboradorLookupId", proyecto_lookup_id="ProyectoLookupId",
    allocation="Asignacion", status="Estado", start_date="FechaInicio", end_date="FechaFin",
    proyecto_title="Title", colaborador_title="Title",
)
_ASIGNACIONES_MIXED = [
    {"id": "1", "fields": {"ColaboradorLookupId": 1, "ProyectoLookupId": 10, "Asignacion": "10", "Estado": "Activo"}},
    {"id": "2", "fields": {"ColaboradorLookupId": 1, "ProyectoLookupId": 10, "Asignacion": "20", "Estado": "Tentativo"}},
    {"id": "3", "fields": {"ColaboradorLookupId": 1, "ProyectoLookupId": 10, "Asignacion": "30", "Estado": "Inactivo"}},
]
_PROYECTOS = [{"id": "10", "fields": {"Title": "Alfa"}}]
_COLABORADORES = [{"id": "1", "fields": {"Title": "Ana"}}]


def _make_repo():
    return StaffingRepository(
        site_id=_SITE_ID, asignaciones_list_id=_ASIG_LIST, proyectos_list_id=_PROY_LIST,
        colaboradores_list_id=_COLAB_LIST, field_mapping=_MAPPING,
    )


def test_5_and_6_pipeline_trace_default_scope_is_operational_and_excludes_inactivo_preserves_tentativo(monkeypatch):
    # Eslabón 1: router llama get_overview(token) SIN scope explícito para
    # la carga inicial (ver webapp/routers/staffing.py::staffing_data).
    class _FakeRepo:
        def get_allocations(self, token, *, assignment_scope="operational"):
            self.seen_scope = assignment_scope
            return []
    repo = _FakeRepo()
    StaffingService(repository=repo).get_overview("tok")
    assert repo.seen_scope == "operational"

    # Eslabón 2: con assignment_scope="operational", el filtro Graph pedido
    # es "ne Inactivo" — si Graph lo rechaza, el fallback filtra en Python
    # con el MISMO criterio (nunca devuelve Inactivo, siempre preserva
    # Tentativo). Se prueba el camino filtrado feliz Y el de fallback.
    def fake_filtered(url, access_token, *, timeout=12, extra_headers=None, on_page=None):
        if _ASIG_LIST in url:
            assert "Inactivo" in url  # el $filter pide excluir Inactivo
            return [r for r in _ASIGNACIONES_MIXED if r["fields"]["Estado"] != "Inactivo"]
        if _PROY_LIST in url:
            return _PROYECTOS
        if _COLAB_LIST in url:
            return _COLABORADORES
        raise AssertionError(url)

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake_filtered)
    records = _make_repo().get_allocations("tok", assignment_scope="operational")
    statuses = {r.status for r in records}
    assert "Inactivo" not in statuses
    assert "Tentativo" in statuses
    assert "Activo" in statuses


def test_initial_data_endpoint_never_passes_a_non_default_scope(monkeypatch):
    # Prueba end-to-end a nivel router: /data SIEMPRE usa el scope default
    # ("operational") — nunca "historical_inactive" en la carga inicial.
    # `monkeypatch` (nunca asignación directa al módulo) — revierte solo
    # al terminar el test, sin filtrar estado global hacia otros archivos
    # de esta misma sesión de pytest.
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "staffing-test@danwor.com")
    session = auth_service.create_session(
        {"email": "staffing-test@danwor.com", "name": "Staffing Test", "first_name": "Staffing", "family_name": "Test"}
    )
    client = TestClient(app)
    client.cookies.set(auth_service.COOKIE_NAME, session)

    seen_scopes = []

    class _RecordingService:
        def get_overview(self, token, **kwargs):
            seen_scopes.append(kwargs.get("assignment_scope", "operational"))
            return []
        last_diagnostics = {}

    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingService())
    client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert seen_scopes == ["operational"]


# ============================================================================
# PART C — Tentativo toggle never calls Graph (test 7)
# ============================================================================

def test_7_tentativo_toggle_has_no_watcher_and_no_fetch_path():
    # El ÚNICO watcher que dispara un fetch está en filters.status.Inactivo
    # — nunca existe un equivalente para Tentativo/Activo. Si alguna vez se
    # agrega un fetch condicionado a Tentativo, esto debe fallar a
    # propósito.
    assert 'this.$watch("filters.status.Inactivo"' in _APP_JS
    assert 'this.$watch("filters.status.Tentativo"' not in _APP_JS
    assert 'this.$watch("filters.status.Activo"' not in _APP_JS
    # _loadInactiveHistory (el ÚNICO método que hace fetch de historial) se
    # llama SOLO desde ese watcher — buscamos que no haya una segunda
    # invocación en ningún otro lugar del archivo.
    assert _APP_JS.count("_loadInactiveHistory()") == 2  # la definición async + la única llamada


# ============================================================================
# PART D — Lazy Inactivo state machine (tests 8-13) + visible loading
# ============================================================================

def test_8_first_inactivo_toggle_performs_exactly_one_history_fetch():
    assert "if (value && !this.historyLoaded) this._loadInactiveHistory();" in _APP_JS
    assert "/operaciones/staffing/asignaciones/historial" in _APP_JS


def test_9_history_loading_state_is_true_only_during_the_fetch():
    body = _APP_JS[_APP_JS.index("async _loadInactiveHistory() {"): _APP_JS.index("_reconcileSelections() {")]
    assert "if (this.historyLoaded || this.historyLoading) return;" in body
    assert "this.historyLoading = true;" in body
    assert "this.historyLoading = false;" in body
    # El true precede al fetch; el false vive en `finally` — nunca se
    # queda pegado en true si el fetch falla.
    assert body.index("this.historyLoading = true;") < body.index("await fetch(")
    assert "finally {" in body and body.index("finally {") < body.index("this.historyLoading = false;")


def test_10_successful_fetch_marks_history_loaded():
    body = _APP_JS[_APP_JS.index("async _loadInactiveHistory() {"): _APP_JS.index("_reconcileSelections() {")]
    assert 'if (data.state === "ok") {' in body
    ok_block = body[body.index('if (data.state === "ok") {'):]
    assert "this.historyLoaded = true;" in ok_block[: ok_block.index("} else {")]


def test_11_failed_fetch_never_marks_history_loaded():
    body = _APP_JS[_APP_JS.index("async _loadInactiveHistory() {"): _APP_JS.index("_reconcileSelections() {")]
    # "historyLoaded = true" aparece EXACTAMENTE una vez en toda la
    # función — dentro del branch "ok". Si una falla también lo marcara,
    # aparecería una segunda vez.
    assert body.count("this.historyLoaded = true;") == 1
    assert "this.historyLoadError = true;" in body


def test_12_second_toggle_never_refetches_due_to_history_loaded_guard():
    assert "if (this.historyLoaded || this.historyLoading) return;" in _APP_JS


def test_13_merge_deduplicates_by_assignment_id():
    assert "const seen = new Set(this.assignments.map(a => a.assignment_id));" in _APP_JS
    assert "!seen.has(a.assignment_id)" in _APP_JS


def test_visible_loading_indicator_exists_and_reuses_existing_spinner_primitive():
    # Pedido explícito del owner: sin indicación visible, un fetch real de
    # varios segundos "parece" instantáneo. Reusa section-analysis-loading/
    # -spinner (mismo primitivo que la carga inicial de /data) — no se
    # inventa un sistema de diseño nuevo.
    assert 'class="section-analysis-loading" x-show="historyLoading"' in _DASHBOARD_HTML
    assert 'class="section-analysis-spinner"' in _DASHBOARD_HTML
    assert "Cargando historial" in _DASHBOARD_HTML


def test_failure_shows_visible_error_without_blocking_the_rest_of_the_dashboard():
    assert 'x-show="historyLoadError && !historyLoading"' in _DASHBOARD_HTML


# ============================================================================
# PART E — Diagnostics never leak token/PII (test 14) + fallback observability
# ============================================================================

def test_14_flatten_diagnostics_never_produces_non_primitive_or_token_like_values():
    diagnostics = {
        "asignaciones": {"rows": 143, "pages": 2, "elapsed_ms": 4213.7, "filter_used": False, "fallback_used": True},
        "proyectos": {"rows": 12, "pages": 1, "elapsed_ms": 88.2},
        "colaboradores": {"rows": 40, "pages": 1, "elapsed_ms": 120.5},
        "total_elapsed_ms": 4500.1,
    }
    flat = staffing_router._flatten_staffing_diagnostics(diagnostics)
    assert flat == {
        "asignaciones_rows": 143, "asignaciones_pages": 2, "asignaciones_elapsed_ms": 4213.7,
        "asignaciones_filter_used": False, "asignaciones_fallback_used": True,
        "proyectos_rows": 12, "proyectos_pages": 1, "proyectos_elapsed_ms": 88.2,
        "colaboradores_rows": 40, "colaboradores_pages": 1, "colaboradores_elapsed_ms": 120.5,
        "total_elapsed_ms": 4500.1,
    }
    for key, value in flat.items():
        assert "token" not in key.lower()
        assert isinstance(value, (bool, int, float))  # nunca str/list/dict — nunca payload/PII


def test_diagnostics_logged_on_successful_data_load_contains_no_token_or_assignment_keys(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "staffing-test@danwor.com")
    session = auth_service.create_session(
        {"email": "staffing-test@danwor.com", "name": "Staffing Test", "first_name": "Staffing", "family_name": "Test"}
    )
    client = TestClient(app)
    client.cookies.set(auth_service.COOKIE_NAME, session)

    class _RecordingService:
        last_diagnostics = {
            "asignaciones": {"rows": 5, "pages": 1, "elapsed_ms": 3000.0, "filter_used": True, "fallback_used": False},
            "total_elapsed_ms": 3100.0,
        }

        def get_overview(self, token, **kwargs):
            return []

    calls = []

    def _fake_log_verbose(event_type, user=None, *, scope=None, area=None, **payload):
        calls.append((event_type, payload))

    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingService())
    monkeypatch.setattr(staffing_router, "log_verbose", _fake_log_verbose)
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "super-secret-tok"})
    assert response.status_code == 200

    # `data_load_diagnostics` (corrective A2) reemplaza al histórico
    # `staffing_data_diagnostics`. El fake no declara `module`/`operation`
    # como params explícitos, así que caen en `**payload` junto al resto.
    diag_calls = [c for c in calls if c[0] == "data_load_diagnostics"]
    assert len(diag_calls) == 1
    payload = diag_calls[0][1]
    assert payload == {
        "module": "OP01", "operation": "initial",
        "asignaciones_rows": 5, "asignaciones_pages": 1, "asignaciones_elapsed_ms": 3000.0,
        "asignaciones_filter_used": True, "asignaciones_fallback_used": False,
        "total_elapsed_ms": 3100.0,
    }
    assert "super-secret-tok" not in str(payload)
    assert "graph_token" not in payload


# ============================================================================
# CORRECTIVE A2 — Transversal Module + Event Semantics: nuevos productores
# canónicos de Staffing (tests 5/6/7/8 del corrective — ver HANDOFF)
# ============================================================================

def _owner_client_and_calls(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "staffing-test@danwor.com")
    session = auth_service.create_session(
        {"email": "staffing-test@danwor.com", "name": "Staffing Test", "first_name": "Staffing", "family_name": "Test"}
    )
    client = TestClient(app)
    client.cookies.set(auth_service.COOKIE_NAME, session)
    calls = []

    def _fake_log_verbose(event_type, user=None, *, scope=None, area=None, module=None, **payload):
        calls.append((event_type, module, payload))

    monkeypatch.setattr(staffing_router, "log_verbose", _fake_log_verbose)
    return client, calls


def test_initial_load_error_persists_canonical_data_load_failed_operation_initial(monkeypatch):
    # test 5 del corrective: error de Graph en la carga INICIAL persiste el
    # evento canónico `data_load_failed` con `module=assignments` y
    # `payload.operation=initial` — reemplaza al histórico
    # `staffing_overview_error`.
    client, calls = _owner_client_and_calls(monkeypatch)

    class _RaisingService:
        def get_overview(self, token, **kwargs):
            # NO RuntimeError: ese tipo lo captura la rama de config
            # incompleta (ver staffing.py) — un error de Graph/transporte
            # genuino es otra excepción cualquiera.
            raise ConnectionError("graph boom")

    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RaisingService())
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert response.status_code == 200
    failed = [c for c in calls if c[0] == "data_load_failed"]
    assert len(failed) == 1
    _, module, payload = failed[0]
    assert module == "OP01"
    assert payload["operation"] == "initial"
    assert payload["error_type"] == "ConnectionError"
    assert "tok" not in str(payload)


def test_historical_load_error_persists_canonical_data_load_failed_operation_history(monkeypatch):
    # test 6 del corrective: error de Graph en la carga de HISTORIAL persiste
    # el MISMO evento canónico, distinguible por `operation=history` —
    # reemplaza al histórico `staffing_historial_error`.
    client, calls = _owner_client_and_calls(monkeypatch)

    class _RaisingHistoryService:
        def get_overview(self, token, **kwargs):
            raise ConnectionError("graph boom historial")

    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RaisingHistoryService())
    response = client.post("/operaciones/staffing/asignaciones/historial", data={"graph_token": "tok"})
    assert response.status_code == 200
    failed = [c for c in calls if c[0] == "data_load_failed"]
    assert len(failed) == 1
    _, module, payload = failed[0]
    assert module == "OP01"
    assert payload["operation"] == "history"
    assert payload["error_type"] == "ConnectionError"


def test_historial_diagnostics_persist_canonical_data_load_diagnostics_operation_history(monkeypatch):
    # Análogo de data_load_diagnostics/operation=initial ya probado arriba —
    # el camino exitoso del ENDPOINT de historial normaliza
    # `staffing_historial_diagnostics` al mismo evento canónico, con
    # operation=history (extensión consistente de este corrective, ver HANDOFF).
    client, calls = _owner_client_and_calls(monkeypatch)

    class _RecordingHistoryService:
        last_diagnostics = {
            "asignaciones": {"rows": 3, "pages": 1, "elapsed_ms": 500.0, "filter_used": True, "fallback_used": False},
            "total_elapsed_ms": 520.0,
        }

        def get_overview(self, token, **kwargs):
            return []

    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingHistoryService())
    response = client.post("/operaciones/staffing/asignaciones/historial", data={"graph_token": "tok"})
    assert response.status_code == 200
    diag = [c for c in calls if c[0] == "data_load_diagnostics"]
    assert len(diag) == 1
    _, module, payload = diag[0]
    assert module == "OP01"
    assert payload["operation"] == "history"
    assert "tok" not in str(payload)


def test_initial_config_incomplete_persists_canonical_configuration_incomplete_operation_initial(monkeypatch):
    # Consistency check post-corrective: `staffing_overview_config_incomplete`
    # seguía siendo un productor CURRENT (no solo histórico) — normaliza al
    # evento canónico reusable `configuration_incomplete` con
    # `module=assignments`/`operation=initial`. NO se fuerza a
    # `data_load_failed`: config incompleta no es un fallo de Graph/transporte.
    client, calls = _owner_client_and_calls(monkeypatch)

    class _ConfigIncompleteService:
        def get_overview(self, token, **kwargs):
            raise RuntimeError("config de Staffing incompleta")

    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _ConfigIncompleteService())
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert response.status_code == 200
    incomplete = [c for c in calls if c[0] == "configuration_incomplete"]
    assert len(incomplete) == 1
    _, module, payload = incomplete[0]
    assert module == "OP01"
    assert payload["operation"] == "initial"
    assert "tok" not in str(payload)


def test_historial_config_incomplete_persists_canonical_configuration_incomplete_operation_history(monkeypatch):
    # Análogo de arriba para el endpoint de historial — mismo evento
    # canónico, distinguible por `operation=history`.
    client, calls = _owner_client_and_calls(monkeypatch)

    class _ConfigIncompleteHistoryService:
        def get_overview(self, token, **kwargs):
            raise RuntimeError("config de Staffing incompleta")

    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _ConfigIncompleteHistoryService())
    response = client.post("/operaciones/staffing/asignaciones/historial", data={"graph_token": "tok"})
    assert response.status_code == 200
    incomplete = [c for c in calls if c[0] == "configuration_incomplete"]
    assert len(incomplete) == 1
    _, module, payload = incomplete[0]
    assert module == "OP01"
    assert payload["operation"] == "history"
    assert "tok" not in str(payload)


def test_fallback_usage_is_observable_in_diagnostics_flag(monkeypatch):
    # "We need to know whether production is doing filtered query→success
    # or filtered query→failure→full fetch fallback" — el flag
    # fallback_used es exactamente esa observabilidad.
    import urllib.error

    def fake(url, access_token, *, timeout=12, extra_headers=None, on_page=None):
        if _ASIG_LIST in url and "$filter" in url:
            raise urllib.error.HTTPError(url, 400, "Bad Request", {}, None)
        if _ASIG_LIST in url:
            return _ASIGNACIONES_MIXED
        if _PROY_LIST in url:
            return _PROYECTOS
        if _COLAB_LIST in url:
            return _COLABORADORES
        raise AssertionError(url)

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)
    repo = _make_repo()
    repo.get_allocations("tok", assignment_scope="operational")
    assert repo.last_diagnostics["asignaciones"]["fallback_used"] is True
    assert repo.last_diagnostics["asignaciones"]["filter_used"] is False
    assert "total_elapsed_ms" in repo.last_diagnostics


def test_successful_filter_reports_filter_used_true_fallback_false(monkeypatch):
    def fake(url, access_token, *, timeout=12, extra_headers=None, on_page=None):
        if _ASIG_LIST in url:
            return [r for r in _ASIGNACIONES_MIXED if r["fields"]["Estado"] != "Inactivo"]
        if _PROY_LIST in url:
            return _PROYECTOS
        if _COLAB_LIST in url:
            return _COLABORADORES
        raise AssertionError(url)

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)
    repo = _make_repo()
    repo.get_allocations("tok", assignment_scope="operational")
    assert repo.last_diagnostics["asignaciones"]["filter_used"] is True
    assert repo.last_diagnostics["asignaciones"]["fallback_used"] is False


# ============================================================================
# PART 15 — route surface unchanged
# ============================================================================

def test_15_route_surface_still_104():
    # CI GATE CORRECTIVE (reconciliación de contratos stale, ver HANDOFF):
    # este test se había quedado pinneado en 84 mientras el snapshot real
    # (tests/route_surface.json, la fuente de verdad exhaustiva — ver
    # test_route_contract.py::test_route_surface_matches_snapshot) ya había
    # crecido a 98 en varios commits legítimos sin actualizar ESTE número.
    # Historial documentado del conteo:
    #   83 -> 84: Staffing Vacation Timeline Overlay original (POST
    #     .../vacaciones), luego RENOMBRADO one-for-one a .../ausencias por
    #     el Approved Absence Overlay (generalización) — sin alias de
    #     compatibilidad, sigue siendo 84.
    #   84 -> 86 (commit 2ea606f, "route_surface 84->86 (+GET reporting V0
    #     que faltaba snapshotear, +POST cartera), regenerado
    #     deliberadamente"): el shell GET de Reporting Integral que faltaba
    #     snapshotear + el primer endpoint POST de datos (Cartera).
    #   86 -> 98: el resto de la epopeya Reporting Integral, todos POST ya
    #     productivos (ver webapp/routers/reporting.py): data/cartera/
    #     anios-disponibles, data/rentabilidad(+anios-disponibles),
    #     data/batch-a, data/horas-vendidas(+anios-disponibles),
    #     data/participacion(+anios-disponibles), data/batch-b, feedback,
    #     telemetry — más POST /operaciones/staffing/feedback. Nunca
    #     regenerado en este archivo puntual hasta ahora.
    #   98 -> 99 (Commercial Hub Workspace Foundation, Batch 0): +GET
    #     /comercial, el shell mínimo del tercer workspace (ver
    #     webapp/routers/commercial.py) — un único endpoint nuevo, sin dato/
    #     Odoo real todavía.
    #   99 -> 101 (Commercial Hub Odoo Batch 1 — Summary + Pipeline, ver
    #     HANDOFF): +GET /comercial/data/summary, +GET /comercial/data/
    #     pipeline — el primer vertical slice real (Odoo read-only), sin
    #     `graph_token`/Form (Odoo es un service account server-side, ver
    #     config.py/webapp.services.odoo_client — nunca un token del browser).
    #   101 -> 102 (Commercial Batch 3A — capability Actividades, ver
    #     HANDOFF): +GET /comercial/data/activities, la segunda capability
    #     del workspace (`mail.activity` sobre `crm.lead`, read-only, mismo
    #     contrato fail-soft y misma gate de entitlement que summary/
    #     pipeline).
    #   102 -> 103 (Commercial Landing Homologation, ver HANDOFF): +GET
    #     /comercial/data/analytics, que alimenta el bloque "Analíticas del
    #     Pipeline" del landing (seis métricas derivadas del historial de
    #     etapas) y el tile "Creadas" — read-only, mismo contrato fail-soft.
    #   103 -> 104 (Active Opportunities Phase 2 / C1, ver HANDOFF): +GET
    #     /comercial/data/opportunities, detalle por oportunidad del universo
    #     de "Oportunidades activas" (stages 1-8) — alimenta la tabla de
    #     detalle y el desglose por Origen (derivado client-side, sin
    #     endpoint propio) — read-only, mismo contrato fail-soft.
    # Esta prueba SIGUE cubriendo lo que le importa a Staffing
    # específicamente (historial/ausencias presentes, vacaciones sin alias
    # de compatibilidad) — el conteo total ya lo vigila el snapshot exhaustivo.
    import json
    snapshot = json.loads(
        (Path(__file__).resolve().parents[2] / "tests" / "route_surface.json").read_text(encoding="utf-8")
    )
    assert len(snapshot) == 104
    assert "POST /operaciones/staffing/asignaciones/historial" in snapshot
    assert "POST /operaciones/staffing/asignaciones/ausencias" in snapshot
    assert "POST /operaciones/staffing/asignaciones/vacaciones" not in snapshot


# ============================================================================
# PART F — Preserve collapse state across grouping switch (tests 1-4)
# ============================================================================

def test_f1_collapse_all_in_consultant_mode_then_switch_to_project_keeps_all_collapsed():
    # Hub Dashboard Contextual View Control corrective (ver HANDOFF): el
    # control visible pasó de un botón único con label dinámico
    # (`groupToggleLabel()`, retirado) a un segmented switch
    # (.dashboard-view-switch) — `setGrouping(mode)` es ahora la primitiva
    # real donde vive este invariante; `toggleGrouping()` queda como wrapper
    # fino sobre ella. La ventana cubre AMBAS.
    body = _APP_JS[_APP_JS.index("setGrouping(mode) {"): _APP_JS.index("rowGroupSecondaryLabel(row) {")]
    assert 'if (this.timelineGlobalCollapseIntent === "collapsed") {' in body
    assert "this.timelineGroups.forEach(g => { next[g.key] = true; });" in body


def test_f2_and_f3_global_intent_state_set_by_collapse_and_expand_all():
    collapse_body = _APP_JS[_APP_JS.index("collapseAllConsultants() {"): _APP_JS.index("expandAllConsultants() {")]
    assert 'this.timelineGlobalCollapseIntent = "collapsed";' in collapse_body
    expand_body = _APP_JS[_APP_JS.index("expandAllConsultants() {"): _APP_JS.index("get filteredAssignments")]
    assert 'this.timelineGlobalCollapseIntent = "expanded";' in expand_body


def test_f_manual_individual_toggle_clears_the_global_intent_to_avoid_stale_mixed_state():
    toggle_body = _APP_JS[_APP_JS.index("toggleConsultant(key) {"): _APP_JS.index("collapseAllConsultants() {")]
    assert "this.timelineGlobalCollapseIntent = null;" in toggle_body


def test_f4_grouping_switch_never_issues_a_graph_request():
    # Ver comentario en test_f1 — misma ventana setGrouping/toggleGrouping.
    body = _APP_JS[_APP_JS.index("setGrouping(mode) {"): _APP_JS.index("rowGroupSecondaryLabel(row) {")]
    assert "fetch(" not in body
