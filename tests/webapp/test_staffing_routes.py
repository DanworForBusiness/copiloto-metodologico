"""
test_staffing_routes.py — Contrato HTTP de Operaciones · Staffing ·
Asignaciones (rutas canónicas, batch Shared Workspace Shell + Operaciones
Structure) + alias de compatibilidad histórico en inglés (`/operations/...`,
de Batch 3 de aceleración). Mockea `webapp.routers.staffing.StaffingService`
en el boundary del router — sin Graph real, sin tocar StaffingRepository.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import staffing as staffing_router
from webapp.services import auth_service
from webapp.services.staffing_service import StaffingOverviewRow
from webapp import workspaces

auth_service.ALLOWED_USERS = "ALL"
# Operations Workspace Access Gate (ver HANDOFF): tier "full" YA NO alcanza
# por sí solo — se requiere además la allowlist explícita de Operations.
# El cliente principal de este archivo prueba COMPORTAMIENTO de Staffing,
# no la frontera de autorización en sí (ver test_operations_access.py para
# eso), así que se abre explícito con "ALL" — MISMO valor que usan otros
# archivos de test para esta variable (asignación plana de módulo, no
# monkeypatch: la última que se ejecute durante el collection de pytest
# "gana" para cualquier test sin su propio override — deben coincidir
# entre archivos para no pisarse entre sí).
auth_service.OPERATIONS_ALLOWED_USERS = "ALL"

_SESSION = auth_service.create_session(
    {"email": "staffing-test@danwor.com", "name": "Staffing Test", "first_name": "Staffing", "family_name": "Test"}
)

client = TestClient(app)
client.cookies.set(auth_service.COOKIE_NAME, _SESSION)


def _academy_client(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "otro@danwor.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "ALL")
    # Sin Operations explícito para ESTE usuario — a diferencia del "ALL"
    # de módulo (pensado para el cliente full de este archivo), este
    # fixture prueba "academy sin listar" (ver test_workspaces.py para el
    # caso NUEVO "academy + Operations listado").
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    session = auth_service.create_session(
        {"email": "academy@danwor.com", "name": "Academy", "first_name": "Academy", "family_name": "User"}
    )
    ro = TestClient(app)
    ro.cookies.set(auth_service.COOKIE_NAME, session)
    return ro


def _unlisted_full_client(monkeypatch):
    # Admitido full a la app (ALLOWED_USERS="ALL" del módulo), pero
    # override puntual (monkeypatch, se revierte solo) de
    # OPERATIONS_ALLOWED_USERS a un valor que NO incluye a este usuario —
    # Operations Workspace Access Gate: tier "full" ya no alcanza por sí solo.
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "staffing-test@danwor.com")
    session = auth_service.create_session(
        {"email": "unlisted-full@danwor.com", "name": "Unlisted", "first_name": "Unlisted", "family_name": "Full"}
    )
    ro = TestClient(app)
    ro.cookies.set(auth_service.COOKIE_NAME, session)
    return ro


class _RaisingIfCalledService:
    def get_overview(self, access_token):
        raise AssertionError("StaffingService.get_overview no debería llamarse")


class _RecordingService:
    calls: list = []

    def __init__(self, rows=None, error=None):
        self._rows = rows if rows is not None else []
        self._error = error

    def get_overview(self, access_token):
        _RecordingService.calls.append(access_token)
        if self._error is not None:
            raise self._error
        return self._rows


def _row(**overrides):
    base = dict(
        assignment_id="1", consultant_id="1", consultant_name="Ana Pérez",
        project_id="10", project_name="Proyecto Alfa",
        allocation_value=50.0, allocation_unit="hours_per_month", capacity_percentage=None,
        status="Active", start_date=None, end_date=None,
    )
    base.update(overrides)
    return StaffingOverviewRow(**base)


# --- GET /operaciones/staffing/asignaciones (canónica) ----------------------

def test_get_anonymous_redirects_to_login_preserving_canonical_next():
    # 303 (no 307): transición de autenticación, no canonicalización de
    # recurso — para un GET esto no cambia el comportamiento del cliente
    # (sigue siendo GET del otro lado), pero es la semántica correcta.
    anon = TestClient(app)
    response = anon.get("/operaciones/staffing/asignaciones", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/platform?next=%2Foperaciones%2Fstaffing%2Fasignaciones"


def test_get_authorized_full_tier_returns_200():
    response = client.get("/operaciones/staffing/asignaciones")
    assert response.status_code == 200


def test_get_academy_tier_is_denied(monkeypatch):
    ro = _academy_client(monkeypatch)
    response = ro.get("/operaciones/staffing/asignaciones")
    assert response.status_code == 403


# --- Operations Workspace Access Gate — enforcement server-side ------------
# La MISMA regla que gobierna el descubrimiento (workspaces.available_workspaces,
# ver test_workspaces.py) y el predicado puro (ver test_operations_access.py)
# también gobierna estas dos rutas canónicas — items J/K/L/M del batch.

def test_get_full_tier_but_not_operations_listed_is_denied(monkeypatch):
    ro = _unlisted_full_client(monkeypatch)
    response = ro.get("/operaciones/staffing/asignaciones")
    assert response.status_code == 403


def test_post_full_tier_but_not_operations_listed_is_denied(monkeypatch):
    ro = _unlisted_full_client(monkeypatch)
    response = ro.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert response.status_code == 403


def test_post_graph_token_never_bypasses_operations_authorization(monkeypatch):
    # Un graph_token válido/presente NUNCA debe saltear la autorización de
    # workspace — se prueba explícitamente con un StaffingService que
    # rompería el test si llegara a invocarse.
    ro = _unlisted_full_client(monkeypatch)
    monkeypatch.setattr(staffing_router, "StaffingService", _RaisingIfCalledService)
    response = ro.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "a-real-looking-token"})
    assert response.status_code == 403


def test_operations_allowed_users_blank_denies_the_previously_authorized_user(monkeypatch):
    # Fail-closed también a nivel de ruta: si OPERATIONS_ALLOWED_USERS
    # queda vacío, ni siquiera el usuario de sesión de este archivo
    # conserva acceso.
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    response = client.get("/operaciones/staffing/asignaciones")
    assert response.status_code == 403


def test_get_authorized_after_gate_continues_working(monkeypatch):
    # Item L: el camino autorizado sigue funcionando sin cambios — mismo
    # 200 que test_get_authorized_full_tier_returns_200, ahora pasando
    # explícitamente por la allowlist en vez de solo tier "full".
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "staffing-test@danwor.com")
    response = client.get("/operaciones/staffing/asignaciones")
    assert response.status_code == 200


def test_post_authorized_after_gate_continues_working(monkeypatch):
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "staffing-test@danwor.com")
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingService(rows=[_row()]))
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert response.status_code == 200


def test_get_does_not_call_staffing_service(monkeypatch):
    monkeypatch.setattr(staffing_router, "StaffingService", _RaisingIfCalledService)
    response = client.get("/operaciones/staffing/asignaciones")
    assert response.status_code == 200


def test_get_renders_operations_staffing_template():
    response = client.get("/operaciones/staffing/asignaciones")
    assert "Asignaciones" in response.text
    assert 'id="staffing-rows"' in response.text
    assert 'hx-post="/operaciones/staffing/asignaciones/data"' in response.text


def test_get_requires_no_graph_token():
    # El mismo GET de arriba nunca manda graph_token — confirma que no es
    # requerido (la request no lleva form/query graph_token en absoluto).
    response = client.get("/operaciones/staffing/asignaciones")
    assert response.status_code == 200
    assert "graph_token=" not in str(response.request.url)


# --- POST /operaciones/staffing/asignaciones/data (canónica) ----------------

def test_post_authorized_with_token_calls_service_with_token_explicit(monkeypatch):
    _RecordingService.calls = []
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingService(rows=[_row()]))

    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok-explicit-123"})

    assert response.status_code == 200
    assert _RecordingService.calls == ["tok-explicit-123"]


def test_post_successful_rows_render_fragment(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row(consultant_name="Zulema Diaz", project_name="Proyecto Z")]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert response.status_code == 200
    assert "Zulema Diaz" in response.text
    assert "Proyecto Z" in response.text


def test_post_renders_honest_hours_per_month_label_without_capacity_percentage(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row(allocation_value=40.0, allocation_unit="hours_per_month")]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert response.status_code == 200
    assert "40 h/mes" in response.text
    # Ninguna capacidad/porcentaje inventada — solo la unidad validada. El
    # único "%" legítimo en el fragmento es el binding CSS del marcador de
    # "hoy" (posición horizontal, corrective pass — no una métrica de
    # negocio), por eso se chequea la ausencia de ese % específicamente en
    # vez de prohibir el carácter "%" en todo el fragmento.
    assert "capacity" not in response.text.lower()
    assert "40%" not in response.text
    assert "40 %" not in response.text


def test_post_empty_rows_render_valid_empty_state(monkeypatch):
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingService(rows=[]))
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert response.status_code == 200
    assert "No hay asignaciones" in response.text


# --- Asignaciones Dashboard v1 (fragmento) -----------------------------------

def test_post_dashboard_fragment_contains_filter_kpi_timeline_availability_detail(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row(consultant_availability="Full Time")]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    text = response.text
    assert response.status_code == 200
    # Mount point del dashboard client-side — un solo dataset normalizado.
    assert "staffingDashboard(" in text
    # Filtros (Cliente confirmado contra Graph real, ver HANDOFF — Tipo de
    # Proyecto sigue deliberadamente fuera de alcance).
    assert "Consultor" in text and "Proyecto" in text and "Estado" in text
    assert "Disponibilidad de asignación" in text
    assert "<span>Cliente</span>" in text
    assert "Tipo de Proyecto" not in text
    # KPIs (sin capacidad/porcentaje — ver test dedicado más abajo).
    for label in ("Consultores", "Proyectos", "Asignaciones activas", "Asignaciones tentativas", "Horas activas"):
        assert label in text
    # Timeline + disponibilidad lado a lado.
    assert "Timeline de asignaciones" in text
    assert "Full Time" in text and "Part Time" in text
    # Detalle colapsable — preserva las columnas históricas.
    assert "Detalle de asignaciones" in text
    for column in ("Consultor", "Proyecto", "Asignación", "Estado", "Desde", "Hasta"):
        assert f"<th>{column}</th>" in text


# --- Corrective pass (revisión con dato real) --------------------------------

def test_post_dashboard_sticky_filters_reuse_activity_mechanism(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row()]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    text = response.text
    # Mismo mecanismo sticky que Actividad — no un sistema sticky paralelo.
    assert 'class="staffing-sticky-filters"' in text
    assert '<div class="whatsnew-filters">' in text
    # Filtros DENTRO del wrapper sticky (no al lado).
    sticky_idx = text.index('class="staffing-sticky-filters"')
    filters_idx = text.index('class="whatsnew-filters"')
    assert sticky_idx < filters_idx


def test_post_dashboard_clear_button_is_bar_action_not_a_filter_field(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row()]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    text = response.text
    # Corrección de alineación (ver HANDOFF): Limpiar vive FUERA de
    # .whatsnew-filters (ya no es "otro campo" al lado de Estado) — es una
    # acción de la barra, separada en .staffing-filter-bar, después de que
    # se cierra el <label> de Estado (el último campo).
    estado_marker_idx = text.index("'Estado ' + s")
    estado_field_close = text.index("</label>", estado_marker_idx)
    clear_idx = text.index("staffing-filter-bar__clear")
    assert clear_idx > estado_field_close
    assert 'class="staffing-filter-bar"' in text


def test_post_dashboard_kpis_and_timeline_are_collapsible_sections(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row()]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    text = response.text
    # Resumen (KPIs) y Timeline usan la MISMA grilla de sección colapsable
    # que Detalle (y que Actividad) — no un bloque suelto sin esa gramática.
    assert 'data-section-id="resumen"' in text
    assert 'data-section-id="timeline"' in text
    assert 'data-section-id="detalle"' in text
    assert text.count('class="dashboard-section"') == 3
    assert "Resumen" in text


def test_post_dashboard_active_hours_kpi_binding_carries_no_unit_suffix(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row()]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    text = response.text
    # Corrección visual (ver HANDOFF): el VALOR grande del KPI es solo el
    # número (con separador de miles) — la unidad ya está en la etiqueta
    # "Horas activas / mes", duplicarla ahí rompía la alineación vertical
    # con el resto de los KPIs. El template solo referencia el binding —
    # el formateo numérico en sí vive en kpis.activeHoursLabel (app.js).
    assert 'x-text="kpis.activeHoursLabel"' in text
    assert "kpis.activeHoursLabel + ' h/mes'" not in text
    assert "kpis.activeHoursLabel + \" h/mes\"" not in text


def test_post_dashboard_resumen_includes_availability_kpis_no_side_panel(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row()]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    text = response.text
    # Revisión PBI (ver HANDOFF): Full Time/Part Time/Sin dato son KPIs
    # generales del mismo universo filtrado — viven DENTRO de Resumen, ya
    # no en un panel lateral independiente. El filtro de Disponibilidad
    # sigue en la barra sticky (sin cambios ahí).
    resumen_idx = text.index('data-section-id="resumen"')
    timeline_idx = text.index('data-section-id="timeline"')
    # (búsqueda desde resumen_idx: "Sin dato" también aparece antes, dentro
    # del payload JSON embebido en x-data — no es el KPI que se busca acá).
    assert resumen_idx < text.index("Full Time", resumen_idx) < timeline_idx
    assert resumen_idx < text.index("Part Time", resumen_idx) < timeline_idx
    assert resumen_idx < text.index("Sin dato", resumen_idx) < timeline_idx
    # Sin grilla operativa 2/3·1/3 ni panel lateral — Timeline usa el ancho
    # completo del contenido del reporte.
    assert 'class="staffing-operational-grid"' not in text
    assert "Disponibilidad de asignación</p>" not in text


def test_post_dashboard_timeline_has_expand_collapse_controls(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row()]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    text = response.text
    assert "Expandir todo" in text
    assert "Contraer todo" in text
    assert "toggleConsultant(" in text
    assert "expandAllConsultants()" in text
    assert "collapseAllConsultants()" in text


def test_post_dashboard_bars_carry_deterministic_palette_and_status_separately(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row()]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    text = response.text
    # La barra combina paletteClass(row) (identidad) + is-status-* (estado)
    # como clases INDEPENDIENTES — nunca una reemplaza a la otra.
    assert "paletteClass(row)" in text
    assert "'is-status-' + statusSlug(row.status)" in text


def test_post_dashboard_row_label_shows_status_hours_and_client_project(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row()]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    text = response.text
    # Densidad restaurada (revisión PBI, ver HANDOFF): Estado(Horas) y
    # Cliente-Proyecto como DOS spans separados, nunca un string opaco
    # concatenado en el template — la composición (y la degradación con
    # gracia si no hay client_name) vive en los helpers JS.
    assert 'class="staffing-timeline__row-status"' in text
    assert "rowStatusHoursLabel(row)" in text
    assert 'class="staffing-timeline__row-clientproject"' in text
    assert "rowClientProjectLabel(row)" in text
    # Nunca un separador "Cliente - Proyecto" hardcodeado en el template —
    # si existiera, no degradaría con gracia cuando falta client_name.
    assert "<span> - </span>" not in text


def test_post_dashboard_bar_has_tooltip_with_canonical_facts(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row()]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    text = response.text
    # Tooltip visual: hover/focus vía CSS puro, contenido con x-text (nunca
    # x-html) — todos los hechos ya presentes en el objeto canónico, sin
    # pedir nada nuevo a Graph.
    assert 'class="staffing-timeline__tooltip" aria-hidden="true"' in text
    assert "row.consultant_name" in text
    assert "row.status" in text
    assert "row.allocation_label" in text
    assert "row.client_name" in text
    assert "row.project_name" in text
    assert "x-html" not in text
    # Foco por teclado, no solo hover — y un resumen accesible propio, no
    # solo el duplicado visual (que queda aria-hidden).
    assert 'tabindex="0"' in text
    assert "barAriaLabel(row)" in text


def test_post_dashboard_status_filter_is_compact_vertical_group(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row()]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    text = response.text
    # Revisión PBI: Estado deja de ser una fila de "pills" horizontales que
    # wrappeaban feo — ahora es un grupo compacto con clase dedicada y
    # labels accesibles por checkbox.
    assert 'class="whatsnew-filter whatsnew-filter--compact"' in text
    assert "'Estado ' + s" in text


def test_post_dashboard_client_filter_present_between_project_and_availability(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row()]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    text = response.text
    # Contrato Cliente confirmado (revisión PBI, ver HANDOFF): Proyecto →
    # EmpresaLookupId → Cliente.Title, LIVE-GRAPH-CONFIRMED. Filtro CLIENTE
    # entre Proyecto y Disponibilidad, mismo patrón que Consultor/Proyecto
    # (select + clientOptions), nunca un valor "(sin resolver)" sintético.
    assert 'x-model="filters.client"' in text
    assert "clientOptions" in text
    proyecto_idx = text.index("<span>Proyecto</span>")
    cliente_idx = text.index("<span>Cliente</span>")
    disponibilidad_idx = text.index("<span>Disponibilidad de asignación</span>")
    assert proyecto_idx < cliente_idx < disponibilidad_idx
    assert '<template x-for="c in clientOptions" :key="c.id">' in text


def test_post_dashboard_filtered_empty_state_component_still_present(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row()]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    text = response.text
    # Filtrado facetado (ver HANDOFF) reduce qué combinaciones IMPOSIBLES se
    # ofrecen, pero combinaciones legítimas que el usuario construye a
    # propósito (p. ej. Cliente + Disponibilidad sin intersección real)
    # siguen pudiendo vaciar el dashboard — el componente de estado vacío
    # filtrado NO se elimina.
    assert 'class="dashboard-empty"' in text
    assert "filteredAssignments.length === 0" in text
    assert "Sin resultados" in text


def test_post_dashboard_today_marker_present_and_not_a_capacity_percentage(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row()]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    text = response.text
    assert 'class="staffing-timeline__today-overlay"' in text
    assert 'class="staffing-timeline__today"' in text
    assert "todayMarker.visible" in text
    assert '"today_marker_pct":' in text  # viene del payload server-side
    # Decisión de producto explícita (revisión con dato real, ver HANDOFF):
    # SOLO la línea vertical — sin chip de texto "HOY". No reintroducir.
    assert "staffing-timeline__today-label" not in text
    assert ">HOY<" not in text


def test_post_dashboard_payload_embeds_real_availability_value(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row(consultant_availability="Part Time")]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert '"availability": "Part Time"' in response.text


def test_post_dashboard_payload_defaults_missing_availability_to_sin_dato(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row(consultant_availability=None)]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert '"availability": "Sin dato"' in response.text


def test_post_dashboard_never_introduces_capacity_semantics(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(rows=[_row(consultant_availability="Full Time")]),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    text_lower = response.text.lower()
    assert "capacity_percentage" not in text_lower.replace(" ", "")
    assert "utilization" not in text_lower
    assert "sobre-asignación" not in text_lower and "sobreasignacion" not in text_lower
    # El único "%" legítimo del fragmento es el binding CSS del marcador de
    # "hoy" (posición, corrective pass) — no una métrica de capacidad. Se
    # verifica su ausencia como métrica en vez de prohibir el carácter.
    assert "% capacity" not in text_lower
    assert "% libre" not in text_lower
    assert "% disponible" not in text_lower


def test_post_missing_config_renders_controlled_state(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(error=RuntimeError("Falta configurar el mapeo de campos de Staffing")),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert response.status_code == 200
    assert "configuration incomplete" in response.text.lower()
    assert "Traceback" not in response.text


def test_post_graph_failure_renders_controlled_error_state(monkeypatch):
    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(error=Exception("graph boom, sensitive detail")),
    )
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert response.status_code == 200
    assert "sensitive detail" not in response.text
    assert "graph boom" not in response.text
    assert "Traceback" not in response.text


def test_post_token_never_echoed_in_response(monkeypatch):
    secret_token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9-super-secret"
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingService(rows=[_row()]))
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": secret_token})
    assert secret_token not in response.text

    monkeypatch.setattr(
        staffing_router, "StaffingService",
        lambda: _RecordingService(error=RuntimeError("boom")),
    )
    error_response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": secret_token})
    assert secret_token not in error_response.text


def test_post_missing_token_renders_controlled_state_without_calling_service(monkeypatch):
    monkeypatch.setattr(staffing_router, "StaffingService", _RaisingIfCalledService)
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": ""})
    assert response.status_code == 200
    assert "token" in response.text.lower()


def test_post_unauthenticated_cannot_use_endpoint_even_with_a_token(monkeypatch):
    # (Test B) La transición de auth SIEMPRE se vuelve GET — 303, no
    # 307/308 — y el `next` apunta a la PÁGINA canónica, nunca al endpoint
    # de datos que disparó el redirect (que nunca se vuelve a golpear con
    # ese mismo POST). El `graph_token` original se descarta sin más: no
    # se lee, no se propaga a la URL de login.
    monkeypatch.setattr(staffing_router, "StaffingService", _RaisingIfCalledService)
    anon = TestClient(app)
    response = anon.post(
        "/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"}, follow_redirects=False,
    )
    assert response.status_code == 303  # nunca llega al service
    assert response.headers["location"] == "/platform?next=%2Foperaciones%2Fstaffing%2Fasignaciones"
    assert "graph_token" not in response.headers["location"]

    # Siguiendo la cadena completa (303 fuerza GET en el siguiente hop):
    # termina en el login de One Danwor, con `data-home` apuntando a la
    # PÁGINA canónica — la página real, al montarse, dispara su propio
    # POST de HTMX fresco (con un `graph_token` nuevo del browser).
    followed = anon.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert followed.status_code == 200
    assert 'data-home="/operaciones/staffing/asignaciones"' in followed.text


def test_post_academy_tier_cannot_use_endpoint_even_with_a_token(monkeypatch):
    monkeypatch.setattr(staffing_router, "StaffingService", _RaisingIfCalledService)
    ro = _academy_client(monkeypatch)
    response = ro.post("/operaciones/staffing/asignaciones/data", data={"graph_token": "tok"})
    assert response.status_code == 403


# --- Compatibilidad histórica: /operations/staffing(/data) -------------------
# Nombres en inglés de Batch 3 — quedan como alias, SIEMPRE 307 a la ruta
# canónica, sin volver a implementar ninguna lógica de acceso/negocio.

def test_get_compat_redirects_to_canonical_regardless_of_auth():
    anon = TestClient(app)
    response = anon.get("/operations/staffing", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/operaciones/staffing/asignaciones"


def test_get_compat_full_chain_reaches_canonical_page_when_authorized():
    response = client.get("/operations/staffing")  # sigue redirecciones por default
    assert response.status_code == 200
    assert 'id="staffing-rows"' in response.text


def test_get_compat_anonymous_eventually_reaches_onedanwor_login_with_canonical_next():
    anon = TestClient(app)
    response = anon.get("/operations/staffing")  # sigue la cadena completa
    assert response.status_code == 200
    assert 'data-home="/operaciones/staffing/asignaciones"' in response.text


def test_post_compat_redirects_to_canonical():
    response = client.post("/operations/staffing/data", data={"graph_token": "tok"}, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/operaciones/staffing/asignaciones/data"


def test_post_compat_preserves_method_and_body_through_redirect(monkeypatch):
    # 307 (no 302/303) preserva method + body — el POST con `graph_token`
    # sigue llegando al service tras la redirección, igual que HTMX lo haría.
    _RecordingService.calls = []
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _RecordingService(rows=[_row()]))
    response = client.post("/operations/staffing/data", data={"graph_token": "tok-compat"})
    assert response.status_code == 200
    assert _RecordingService.calls == ["tok-compat"]


def test_post_compat_unauthenticated_never_reaches_service(monkeypatch):
    monkeypatch.setattr(staffing_router, "StaffingService", _RaisingIfCalledService)
    anon = TestClient(app)
    response = anon.post("/operations/staffing/data", data={"graph_token": "tok"})
    assert response.status_code == 200  # termina en la landing, no en un 500/403 crudo


# --- (Tests C/D) /platform y /onedanwor son SOLO navegación, nunca acción ---
# Ver webapp/workspaces.py::platform_login_redirect — la transición de auth
# usa 303 (fuerza GET), así que ninguna de las dos rutas necesita aceptar
# POST para absorber una redirección de autenticación.

def test_platform_does_not_accept_post():
    anon = TestClient(app)
    response = anon.post("/platform")
    assert response.status_code == 405


def test_onedanwor_does_not_accept_post():
    anon = TestClient(app)
    response = anon.post("/onedanwor")
    assert response.status_code == 405


# --- Workspace home_url wiring ------------------------------------------------

def test_operations_workspace_home_url_is_canonical():
    assert workspaces.OPERATIONS.home_url == "/operaciones/staffing/asignaciones"
