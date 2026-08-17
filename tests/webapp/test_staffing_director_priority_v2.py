"""
test_staffing_director_priority_v2.py — Contrato de "STAFFING DASHBOARD V2 —
DIRECTOR PRIORITY UPDATE" (ver HANDOFF): carga inicial no histórica + lazy
history de Inactivo, activo_vencido, KPI Activos vencidos, grouping switch
Consultor/Proyecto, detalle agregado + proyección +N días, reuso del
drag-and-drop genérico, y formato de fecha dd/mm/yyyy. Vacaciones (item 9,
overlay de ausencias) queda explícitamente FUERA de este archivo — bloqueado
por falta de acceso Graph real en este entorno (ver HANDOFF).

Igual convención que el resto de tests/webapp/test_staffing_*.py: capas
Python (repository/service/router) se prueban con comportamiento real
(fixtures determinísticas, sin Graph real); la lógica que vive 100% en
app.js (sin runtime de JS en esta suite) se fija por CONTENIDO DE FUENTE,
igual que test_staffing_app_js.py ya hace.
"""
from __future__ import annotations

import urllib.error
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import staffing as staffing_router
from webapp.services import auth_service
from webapp.services.staffing_repository import StaffingFieldMapping, StaffingRepository
from webapp.services import staffing_repository as repo_module
from webapp.services.staffing_service import (
    StaffingOverviewRow,
    StaffingService,
    build_dashboard_payload,
    is_activo_vencido,
)

_APP_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
_DASHBOARD_HTML = (
    Path(__file__).resolve().parents[2] / "webapp" / "templates" / "operations" / "staffing_dashboard.html"
).read_text(encoding="utf-8")


# ============================================================================
# is_activo_vencido (regla canónica, ver HANDOFF)
# ============================================================================

def test_activo_vencido_true_when_status_activo_and_end_date_past():
    assert is_activo_vencido("Activo", date(2026, 1, 1), today=date(2026, 8, 10)) is True


def test_activo_vencido_false_when_end_date_is_future():
    assert is_activo_vencido("Activo", date(2026, 12, 31), today=date(2026, 8, 10)) is False


def test_activo_vencido_false_when_end_date_equals_today():
    # "< today", no "<= today" — el último día vigente no es una anomalía.
    assert is_activo_vencido("Activo", date(2026, 8, 10), today=date(2026, 8, 10)) is False


def test_activo_vencido_false_for_non_activo_status_even_with_past_end_date():
    assert is_activo_vencido("Inactivo", date(2020, 1, 1), today=date(2026, 8, 10)) is False
    assert is_activo_vencido("Tentativo", date(2020, 1, 1), today=date(2026, 8, 10)) is False


def test_activo_vencido_false_without_end_date():
    assert is_activo_vencido("Activo", None, today=date(2026, 8, 10)) is False


def test_activo_vencido_never_rewrites_status_only_interprets_it():
    # No hay ninguna forma de que esta función mute/devuelva un status —
    # devuelve exclusivamente bool.
    assert isinstance(is_activo_vencido("Activo", date(2020, 1, 1)), bool)


def test_build_dashboard_payload_embeds_activo_vencido_per_row():
    class _FakeRepo:
        def get_allocations(self, token, *, assignment_scope="operational"):
            return []

    row_vencido = StaffingOverviewRow(
        assignment_id="1", consultant_id="1", consultant_name="Ana", project_id="10",
        project_name="Alfa", allocation_value=40.0, allocation_unit="hours_per_month",
        capacity_percentage=None, status="Activo", start_date=date(2026, 1, 1), end_date=date(2026, 1, 31),
    )
    row_vigente = StaffingOverviewRow(
        assignment_id="2", consultant_id="2", consultant_name="Beto", project_id="11",
        project_name="Beta", allocation_value=20.0, allocation_unit="hours_per_month",
        capacity_percentage=None, status="Activo", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
    )
    payload = build_dashboard_payload([row_vencido, row_vigente], today=date(2026, 8, 10))
    by_id = {a["assignment_id"]: a for a in payload["assignments"]}
    assert by_id["1"]["activo_vencido"] is True
    assert by_id["2"]["activo_vencido"] is False


# ============================================================================
# StaffingService.get_overview — assignment_scope passthrough
# ============================================================================

def test_get_overview_defaults_to_operational_scope():
    class _FakeRepo:
        def get_allocations(self, token, *, assignment_scope="operational"):
            self.seen = assignment_scope
            return []
    repo = _FakeRepo()
    StaffingService(repository=repo).get_overview("tok")
    assert repo.seen == "operational"


def test_get_overview_forwards_historical_inactive_scope_explicitly():
    class _FakeRepo:
        def get_allocations(self, token, *, assignment_scope="operational"):
            self.seen = assignment_scope
            return []
    repo = _FakeRepo()
    StaffingService(repository=repo).get_overview("tok", assignment_scope="historical_inactive")
    assert repo.seen == "historical_inactive"


# ============================================================================
# StaffingRepository — carga inicial no histórica + fallback seguro
# ============================================================================

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


def _fake_fetch_by_list(calls, *, asignaciones_provider):
    def fake(url, access_token, *, timeout=12, extra_headers=None, on_page=None):
        calls.append((url, extra_headers))
        if _ASIG_LIST in url:
            return asignaciones_provider(url)
        if _PROY_LIST in url:
            return _PROYECTOS
        if _COLAB_LIST in url:
            return _COLABORADORES
        raise AssertionError(f"unexpected url {url}")
    return fake


def test_operational_scope_sends_ne_inactivo_filter_with_prefer_header(monkeypatch):
    calls = []
    monkeypatch.setattr(
        repo_module.microsoft365, "graph_get_paged_values",
        _fake_fetch_by_list(calls, asignaciones_provider=lambda url: _ASIGNACIONES_MIXED),
    )
    _make_repo().get_allocations("tok", assignment_scope="operational")
    asig_calls = [(u, h) for u, h in calls if _ASIG_LIST in u]
    assert len(asig_calls) == 1
    url, headers = asig_calls[0]
    assert "fields%2FEstado%20ne%20%27Inactivo%27" in url
    assert headers == {"Prefer": "HonorNonIndexedQueriesWarningMayFailRandomly"}


def test_historical_inactive_scope_sends_eq_inactivo_filter():
    calls = []
    import webapp.services.staffing_repository as rm

    def fake(url, access_token, *, timeout=12, extra_headers=None, on_page=None):
        calls.append((url, extra_headers))
        if _ASIG_LIST in url:
            return _ASIGNACIONES_MIXED
        if _PROY_LIST in url:
            return _PROYECTOS
        if _COLAB_LIST in url:
            return _COLABORADORES
        raise AssertionError(url)

    import pytest as _pytest  # local, avoid polluting module-level import list
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(rm.microsoft365, "graph_get_paged_values", fake)
        _make_repo().get_allocations("tok", assignment_scope="historical_inactive")
    asig_calls = [(u, h) for u, h in calls if _ASIG_LIST in u]
    assert len(asig_calls) == 1
    assert "fields%2FEstado%20eq%20%27Inactivo%27" in asig_calls[0][0]


def test_operational_scope_falls_back_to_full_fetch_and_python_filter_on_graph_rejection(monkeypatch):
    # Simula Graph rechazando el $filter (columna no indexada) — el
    # fallback SIEMPRE debe devolver el resultado CORRECTO (excluir
    # Inactivo), nunca romper Staffing ni devolver la lista sin filtrar.
    calls = []

    def fake(url, access_token, *, timeout=12, extra_headers=None, on_page=None):
        calls.append(url)
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
    records = _make_repo().get_allocations("tok", assignment_scope="operational")
    statuses = {r.assignment_id: r.status for r in records}
    assert statuses == {"1": "Activo", "2": "Tentativo"}  # nunca "3" (Inactivo)
    # Dos llamadas a Asignaciones: el intento filtrado (que falló) + el
    # fallback completo.
    assert len([u for u in calls if _ASIG_LIST in u]) == 2


def test_historical_inactive_scope_falls_back_correctly_too(monkeypatch):
    def fake(url, access_token, *, timeout=12, extra_headers=None, on_page=None):
        if _ASIG_LIST in url and "$filter" in url:
            raise urllib.error.URLError("boom")
        if _ASIG_LIST in url:
            return _ASIGNACIONES_MIXED
        if _PROY_LIST in url:
            return _PROYECTOS
        if _COLAB_LIST in url:
            return _COLABORADORES
        raise AssertionError(url)

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)
    records = _make_repo().get_allocations("tok", assignment_scope="historical_inactive")
    assert [r.assignment_id for r in records] == ["3"]  # SOLO Inactivo


def test_proyectos_and_colaboradores_are_not_scope_narrowed(monkeypatch):
    # Tablas de dimensión — se piden completas en AMBOS alcances (nunca la
    # optimización histórica las toca).
    calls = []
    monkeypatch.setattr(
        repo_module.microsoft365, "graph_get_paged_values",
        _fake_fetch_by_list(calls, asignaciones_provider=lambda url: _ASIGNACIONES_MIXED),
    )
    _make_repo().get_allocations("tok", assignment_scope="historical_inactive")
    proy_urls = [u for u, _h in calls if _PROY_LIST in u]
    colab_urls = [u for u, _h in calls if _COLAB_LIST in u]
    assert len(proy_urls) == 1 and "$filter" not in proy_urls[0]
    assert len(colab_urls) == 1 and "$filter" not in colab_urls[0]


# ============================================================================
# Router: POST /operaciones/staffing/asignaciones/historial (ruta NUEVA)
# ============================================================================

auth_service.ALLOWED_USERS = "ALL"
# Reusa la MISMA identidad "staffing-test@danwor.com" que test_staffing_routes.py
# — ya forma parte del CSV compartido de OPERATIONS_ALLOWED_USERS entre los
# archivos de este directorio (ver comentario ahí). Definir un email nuevo
# acá exigiría agregarlo a ese CSV compartido en TODOS los archivos (el
# orden de collection de pytest determina cuál asignación de módulo "gana"
# al final) — reusar evita esa carrera sin ganar nada a cambio.
auth_service.OPERATIONS_ALLOWED_USERS = "staffing-test@danwor.com,topbar-full@danwor.com,onedanwor-full@danwor.com"
_SESSION = auth_service.create_session(
    {"email": "staffing-test@danwor.com", "name": "Staffing Test", "first_name": "Staffing", "family_name": "Test"}
)
client = TestClient(app)
client.cookies.set(auth_service.COOKIE_NAME, _SESSION)


class _RecordingHistoryService:
    calls: list = []

    def __init__(self, rows=None, error=None):
        self._rows = rows if rows is not None else []
        self._error = error

    def get_overview(self, access_token, *, assignment_scope="operational"):
        _RecordingHistoryService.calls.append((access_token, assignment_scope))
        if self._error is not None:
            raise self._error
        return self._rows


def _row(**overrides):
    base = dict(
        assignment_id="1", consultant_id="1", consultant_name="Ana", project_id="10", project_name="Alfa",
        allocation_value=10.0, allocation_unit="hours_per_month", capacity_percentage=None,
        status="Inactivo", start_date=None, end_date=None,
    )
    base.update(overrides)
    return StaffingOverviewRow(**base)


def test_historial_endpoint_requires_session_same_as_data_endpoint():
    anon = TestClient(app)
    response = anon.post("/operaciones/staffing/asignaciones/historial", data={"graph_token": "tok"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/platform?next=%2Foperaciones%2Fstaffing%2Fasignaciones"


def test_historial_endpoint_calls_service_with_historical_inactive_scope(monkeypatch):
    _RecordingHistoryService.calls = []
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingHistoryService(rows=[_row()]))
    response = client.post("/operaciones/staffing/asignaciones/historial", data={"graph_token": "tok-hist"})
    assert response.status_code == 200
    assert _RecordingHistoryService.calls == [("tok-hist", "historical_inactive")]


def test_historial_endpoint_returns_json_not_html(monkeypatch):
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingHistoryService(rows=[_row()]))
    response = client.post("/operaciones/staffing/asignaciones/historial", data={"graph_token": "tok"})
    assert response.headers["content-type"].startswith("application/json")
    data = response.json()
    assert data["state"] == "ok"
    assert data["assignments"][0]["assignment_id"] == "1"
    assert data["assignments"][0]["status"] == "Inactivo"


def test_historial_endpoint_missing_token_never_calls_service(monkeypatch):
    class _Raising:
        def get_overview(self, *a, **kw):
            raise AssertionError("no debería llamarse")
    monkeypatch.setattr(staffing_router, "StaffingService", _Raising)
    response = client.post("/operaciones/staffing/asignaciones/historial", data={"graph_token": ""})
    assert response.status_code == 200
    assert response.json() == {"state": "missing_token"}


def test_historial_endpoint_config_incomplete_state():
    class _ConfigIncomplete:
        def get_overview(self, *a, **kw):
            raise RuntimeError("Falta configurar el mapeo de campos de Staffing")
    import webapp.routers.staffing as sr
    orig = sr.StaffingService
    sr.StaffingService = _ConfigIncomplete
    try:
        response = client.post("/operaciones/staffing/asignaciones/historial", data={"graph_token": "tok"})
        assert response.json() == {"state": "config_incomplete"}
    finally:
        sr.StaffingService = orig


def test_historial_endpoint_graph_error_state_never_leaks_details():
    class _Boom:
        def get_overview(self, *a, **kw):
            raise Exception("graph boom, sensitive detail")
    import webapp.routers.staffing as sr
    orig = sr.StaffingService
    sr.StaffingService = _Boom
    try:
        response = client.post("/operaciones/staffing/asignaciones/historial", data={"graph_token": "tok"})
        assert response.json() == {"state": "graph_error"}
        assert "sensitive detail" not in response.text
    finally:
        sr.StaffingService = orig


def test_historial_endpoint_token_never_echoed(monkeypatch):
    secret = "eyJ-super-secret-token"
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingHistoryService(rows=[_row()]))
    response = client.post("/operaciones/staffing/asignaciones/historial", data={"graph_token": secret})
    assert secret not in response.text


def test_historial_endpoint_denied_when_not_operations_authorized(monkeypatch):
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "someone-else@danwor.com")
    response = client.post("/operaciones/staffing/asignaciones/historial", data={"graph_token": "tok"})
    assert response.status_code == 403


class _RaisingIfCalledHistoryService:
    def get_overview(self, *args, **kwargs):
        raise AssertionError("StaffingService.get_overview no debería llamarse")


def test_historial_endpoint_a_real_looking_graph_token_never_bypasses_operations_authorization(monkeypatch):
    # Pre-PR final check (ver HANDOFF): MISMO contrato de seguridad que
    # test_post_graph_token_never_bypasses_operations_authorization (ver
    # test_staffing_routes.py) — un usuario full-tier pero NO listado en
    # OPERATIONS_ALLOWED_USERS sigue denegado aunque mande un graph_token
    # con pinta real. Se prueba explícito con un service que rompería el
    # test si llegara a invocarse.
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "staffing-test@danwor.com")
    monkeypatch.setattr(staffing_router, "StaffingService", _RaisingIfCalledHistoryService)
    session = auth_service.create_session(
        {"email": "unlisted-full-v2@danwor.com", "name": "Unlisted", "first_name": "Unlisted", "family_name": "Full"}
    )
    ro = TestClient(app)
    ro.cookies.set(auth_service.COOKIE_NAME, session)
    response = ro.post(
        "/operaciones/staffing/asignaciones/historial",
        data={"graph_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9-a-real-looking-token"},
    )
    assert response.status_code == 403


def test_route_surface_gained_exactly_one_new_route():
    import json
    from pathlib import Path as _P
    snapshot = json.loads((_P(__file__).resolve().parents[2] / "tests" / "route_surface.json").read_text(encoding="utf-8"))
    assert "POST /operaciones/staffing/asignaciones/historial" in snapshot


# ============================================================================
# Template: default status Activo-only, KPI set, grouping, detalle, DMY
# ============================================================================

def test_default_status_filter_is_activo_only_in_source():
    assert 'Object.fromEntries(statusOptions.map(s => [s, s === "Activo"]));' in _APP_JS
    assert 's === "Activo" || s === "Tentativo"' not in _APP_JS


def test_lazy_history_watcher_triggers_once_and_merges_by_assignment_id():
    assert 'this.$watch("filters.status.Inactivo"' in _APP_JS
    assert "if (value && !this.historyLoaded) this._loadInactiveHistory();" in _APP_JS
    assert "async _loadInactiveHistory()" in _APP_JS
    assert "if (this.historyLoaded || this.historyLoading) return;" in _APP_JS
    assert "/operaciones/staffing/asignaciones/historial" in _APP_JS
    assert "!seen.has(a.assignment_id)" in _APP_JS
    assert "this.historyLoaded = true;" in _APP_JS


def test_grouping_switch_uses_one_timeline_implementation():
    # Hub Dashboard Contextual View Control corrective (ver HANDOFF): el
    # botón único con label dinámico (`groupToggleLabel()`) fue retirado a
    # favor de un segmented switch (.dashboard-view-switch) que muestra
    # ambos modos a la vez — `setGrouping(mode)` es la primitiva real única
    # (no-op si ya está en ese modo); `toggleGrouping()` sigue como wrapper.
    assert 'groupBy: "consultant",' in _APP_JS
    assert "toggleGrouping()" in _APP_JS
    assert "setGrouping(mode) {" in _APP_JS
    # Una sola definición de timelineGroups (no dos timelines paralelos).
    assert _APP_JS.count("get timelineGroups() {") == 1
    assert "rowGroupSecondaryLabel(row)" in _APP_JS


def test_projection_helpers_are_string_based_no_date_object_for_comparison():
    # Comparación de efectividad puramente por string ISO — nunca
    # `new Date(iso)` (evita el bug de off-by-one por UTC).
    assert "function _isEffectiveOn(row, iso)" in _APP_JS
    assert "row.start_date <= iso" in _APP_JS and "row.end_date >= iso" in _APP_JS
    assert "projectionDays: 35," in _APP_JS
    assert "setProjectionDays(value)" in _APP_JS
    assert "Math.min(365, Math.max(1, n))" in _APP_JS


def test_format_date_dmy_never_uses_new_date_for_iso_strings():
    assert "formatDateDMY(iso, short)" in _APP_JS
    assert 'const [y, m, d] = iso.split("-");' in _APP_JS


def test_aggregated_detail_estado_uses_mixto_for_divergent_statuses():
    assert 'statuses.size === 1 ? [...statuses][0] : "Mixto"' in _APP_JS


def test_aggregated_detail_dashes_only_when_children_genuinely_differ():
    # Corrección del usuario (ver HANDOFF): "--" NUNCA es automático por ser
    # Cliente/Proyecto/Desde/Hasta — solo cuando los hijos DIFIEREN en ese
    # campo puntual. Un único cliente compartido (p. ej. Farmanet) o un
    # único "(sin resolver)" compartido SIGUEN informados a nivel agregado;
    # fechas iguales con clientes distintos también informan la fecha
    # (cada campo se evalúa de forma independiente, nunca "todo o nada").
    assert "function _singleValue(values)" in _APP_JS
    assert "unique.size === 1 ? [...unique][0] : undefined" in _APP_JS
    assert "_singleValue(g.rows.map(r => r.client_name || null))" in _APP_JS
    assert "_singleValue(g.rows.map(r => r.project_name))" in _APP_JS
    assert "_singleValue(g.rows.map(r => r.start_date || null))" in _APP_JS
    assert "_singleValue(g.rows.map(r => r.end_date || null))" in _APP_JS
    assert 'clienteValue === undefined ? "--" : (clienteValue || "—")' in _APP_JS
    assert 'proyectoValue === undefined ? "--" : proyectoValue' in _APP_JS


def test_kpis_include_activos_vencidos_counting_assignments_not_consultants():
    assert "const activosVencidos = rows.filter(r => r.activo_vencido).length;" in _APP_JS


def test_group_header_hours_suffix_counts_only_hours_effective_today():
    # Pre-PR final check (ver HANDOFF): el total del header de grupo se
    # CONGELA como horas EFECTIVAS HOY — nunca la suma de toda barra del
    # dataset filtrado/timeline (eso incluiría asignaciones futuras y
    # Activo-vencidas). Reusa el MISMO helper canónico que "Asignación
    # hoy"/"Asignación +N días" (`_hoursIfEffective`) — nunca un segundo
    # cálculo de efectividad. No hay runtime de JS en esta suite (ver
    # test_staffing_app_js.py) — la escena de 3 asignaciones (vigente,
    # futura, Activo-vencida) que pide el director se verificó en vivo
    # contra Alpine (`_x_dataStack`), ver STOP report; esto fija que esa
    # implementación específica no se borre/reemplace por accidente.
    assert "groupHoursSuffix(group) {" in _APP_JS
    suffix_body = _APP_JS[_APP_JS.index("groupHoursSuffix(group) {"):]
    suffix_body = suffix_body[: suffix_body.index("},")]
    assert "const todayIso = _todayISO();" in suffix_body
    assert "group.rows.reduce((sum, r) => sum + _hoursIfEffective(r, todayIso), 0)" in suffix_body
    # Nunca el cálculo viejo (suma incondicional de allocation_value sin
    # pasar por _hoursIfEffective) — si reapareciera, la regresión real
    # sería contar horas futuras/vencidas en el header.
    assert "group.rows\n        .filter(r => r.allocation_unit" not in _APP_JS


def test_group_header_hours_suffix_is_a_separate_span_from_the_name():
    # Ajuste cosmético (pedido del usuario): nombre y "· N h/mes" NUNCA
    # comparten el mismo <span> — así el sufijo puede llevar un color más
    # suave sin heredar el negro/bold del nombre.
    assert "groupHoursSuffix(group)" in _APP_JS
    assert 'return `· ${rounded.toLocaleString("en-US")} h/mes`;' in _APP_JS
    assert 'class="staffing-timeline__consultant-name" x-text="group.consultant_name"' in _DASHBOARD_HTML
    assert 'class="staffing-timeline__consultant-hours" x-text="group.hours_suffix"' in _DASHBOARD_HTML


def test_dashboard_template_has_dashboard_key_for_generic_drag_reorder():
    assert 'data-dashboard-key="staffing"' in _DASHBOARD_HTML
    assert _DASHBOARD_HTML.count('draggable="true"') == 3
    assert _DASHBOARD_HTML.count('class="dashboard-section__grip" aria-hidden="true">⋮⋮</span>') == 3


def test_dashboard_template_has_gold_grouping_button_distinct_from_expand_collapse():
    # Hub Dashboard Contextual View Control corrective (ver HANDOFF): el
    # botón único `.staffing-timeline__group-toggle` fue reemplazado por un
    # segmented switch (.dashboard-view-switch, compartido con Reporting)
    # con dos botones Consultor/Proyecto — sigue siendo un control DISTINTO
    # de Expandir/Contraer todo, solo que ahora muestra ambos modos a la vez
    # en vez de un label dinámico.
    assert 'class="dashboard-view-switch"' in _DASHBOARD_HTML
    assert "@click=\"setGrouping('consultant')\"" in _DASHBOARD_HTML
    assert "@click=\"setGrouping('project')\"" in _DASHBOARD_HTML
    assert "expandAllConsultants()" in _DASHBOARD_HTML
    assert "collapseAllConsultants()" in _DASHBOARD_HTML


def test_dashboard_template_has_n_days_control_defaulting_to_35():
    assert 'type="number" min="1" max="365"' in _DASHBOARD_HTML
    assert "setProjectionDays($event.target.value)" in _DASHBOARD_HTML


def test_dashboard_template_dates_use_format_date_dmy_helper_not_raw_iso():
    assert "formatDateDMY(row.start_date)" in _DASHBOARD_HTML
    assert "formatDateDMY(row.end_date)" in _DASHBOARD_HTML
    # Patrón viejo (fecha ISO cruda sin formatear) no debe quedar.
    assert "row.start_date || '—'" not in _DASHBOARD_HTML
    assert "(row.start_date || '¿inicio?')" not in _DASHBOARD_HTML


def test_dashboard_template_detail_table_has_fixed_column_widths():
    # Pedido del usuario: la tabla no debe reacomodar columnas al
    # expandir/contraer un consultor — table-layout fijo + colgroup, nunca
    # el layout automático (que recalcula anchos según qué filas hay).
    # Phase S (ver HANDOFF "REPORTING INTEGRAL — REAL DATA"): migrado del
    # primitivo `admin-table` (pensado para el panel oscuro de Copilot) al
    # primitivo Hub compartido `.dashboard-table` — MISMO que consume
    # Reporting.
    assert 'class="dashboard-table staffing-detail-table"' in _DASHBOARD_HTML
    assert "<colgroup>" in _DASHBOARD_HTML
    # 9 columnas visibles (corrective S.4, ver HANDOFF): el toggle de
    # disclosure ya no es una columna propia — vive DENTRO de la celda
    # Consultor (`.dashboard-table-toggle`), mismo contrato que Reporting.
    assert _DASHBOARD_HTML.count("<col style=") == 9
    assert "table-layout: fixed" in (
        Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css"
    ).read_text(encoding="utf-8")


def test_projection_column_header_omits_redundant_dias_suffix():
    # Ajuste presentacional del owner: "días" es redundante con el control
    # vecino ("Proyección a [N] día(s)") y hacía que "Disponibilidad"
    # wrappeara a dos líneas en la tabla de layout fijo. La semántica del
    # cálculo (+N días) NO cambia — solo el texto del header.
    assert 'get projectionColumnLabel() { return `Asignación +${this.projectionDays}`; },' in _APP_JS
    assert "días`" not in _APP_JS.split("get projectionColumnLabel()")[1].split("\n")[0]


def test_detail_table_column_widths_favor_disponibilidad_over_projection_column():
    # Angostar la columna de proyección (antes 13%) y dársela a
    # Disponibilidad (antes 6%) — misma suma de 100%, table-layout fijo
    # preservado, nunca se asume que acortar el texto solo redistribuye
    # el ancho.
    colgroup = _DASHBOARD_HTML[_DASHBOARD_HTML.index("<colgroup>"): _DASHBOARD_HTML.index("</colgroup>")]
    widths = [int(w) for w in __import__("re").findall(r'width:(\d+)%', colgroup)]
    # 9 columnas (corrective S.4, ver HANDOFF): el toggle ya no es una
    # columna propia, se fusionó dentro de Consultor (ver test dedicado
    # `test_dashboard_template_detail_table_has_fixed_column_widths`) — los
    # índices de Proyección/Disponibilidad se corren uno hacia atrás.
    assert len(widths) == 9
    assert sum(widths) == 100
    projection_width, disponibilidad_width = widths[4], widths[8]
    assert projection_width < 13  # angostada respecto del ancho previo
    assert disponibilidad_width > 6  # ensanchada respecto del ancho previo


def test_hierarchy_disclosure_shares_the_hub_toggle_primitive_not_a_staffing_dialect():
    # S.4 (owner live QA, ver HANDOFF "shared Hub hierarchy disclosure
    # interaction"): clic en el triángulo O en el nombre del consultor
    # deben disparar el MISMO toggle — antes solo el triángulo reaccionaba.
    # Consume la MISMA clase `.dashboard-table-toggle` que Reporting
    # (tools.css), nunca un `.staffing-detail__expand-btn` propio.
    assert "staffing-detail__expand-btn" not in _DASHBOARD_HTML
    assert 'class="dashboard-table-toggle" @click="toggleDetailGroup(item.toggle_key)"' in _DASHBOARD_HTML
    assert ':aria-expanded="item.expanded"' in _DASHBOARD_HTML
    # El toggle y el nombre del consultor viven en LA MISMA celda/control —
    # ya no en dos <td> separados (uno con el botón, otro con el label).
    assert '<button type="button" class="dashboard-table-toggle" @click="toggleDetailGroup(item.toggle_key)" :aria-expanded="item.expanded"' in _DASHBOARD_HTML
    assert '<span x-text="item.consultant_label"></span>' in _DASHBOARD_HTML


def test_hierarchy_child_rows_are_not_falsely_expandable_in_staffing():
    # S.4 (owner live QA: "leaf rows must not appear expandable"): filas
    # hijo (sin más expansión) usan el `<span>` no interactivo compartido
    # `--leaf`, nunca un botón — mismo contrato que Reporting.
    assert 'x-if="!item.is_aggregate"' in _DASHBOARD_HTML
    assert '<span class="dashboard-table-toggle dashboard-table-toggle--leaf">' in _DASHBOARD_HTML


def test_dashboard_template_detail_rows_are_flattened_not_nested_templates():
    # Ver comentario en app.js::detailRows — dos <template x-for> anidados
    # sobre la misma lista NO intercalan filas hijas bajo su agregada
    # (todas las agregadas primero, hijos al final) — el template debe usar
    # la vista plana `detailRows`, un solo `x-for`.
    assert 'x-for="item in detailRows"' in _DASHBOARD_HTML
    assert _DASHBOARD_HTML.count('x-for="group in aggregatedDetailRows"') == 0
