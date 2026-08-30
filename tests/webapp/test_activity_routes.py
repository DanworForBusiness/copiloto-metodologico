"""
test_activity_routes.py — Activity Dashboard Hub-wide (Batch A2), contrato HTTP/UI.

Prueba la ruta `/admin/activity` (owner-only + diagnóstico) con datos Hub-wide
sembrados en un ACTIVITY_LOG_DIR temporal: render de los filtros Ámbito/Área
multi-select, supervivencia del estado en refresh HTMX, default all-checked,
faceting/selección inválida, gate de acceso sin cambios, paridad full-page/partial,
ausencia de métricas de negocio de Operations y reuso de la primitiva de checkbox.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from webapp.main import app
from webapp import access
from webapp.services import auth_service
from webapp.services import activity_analytics

# app.js (sin runtime de JS en esta suite) se fija por CONTENIDO DE FUENTE,
# mismo criterio que test_staffing_director_priority_v2.py ya usa para
# staffingDashboard().
_APP_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "app.js").read_text(encoding="utf-8")

_HUB_ROWS = [
    {"ts_utc": "2026-03-01T10:00:00+00:00", "event": "login", "user": "u@d.com", "name": "U", "scope": "platform", "area": "auth", "payload": {}},
    {"ts_utc": "2026-03-01T10:01:00+00:00", "event": "module_selected", "user": "u@d.com", "name": "U", "scope": "copilot", "area": "methodology", "payload": {"module": "01"}},
    {"ts_utc": "2026-03-01T10:02:00+00:00", "event": "staffing_overview_viewed", "user": "ops@d.com", "name": "Ops", "scope": "operations", "area": "staffing", "payload": {}},
]


def _seed(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    (tmp_path / "activity_20260301.jsonl").write_text(
        "\n".join(json.dumps(r) for r in _HUB_ROWS) + "\n", encoding="utf-8",
    )


def _owner_client(monkeypatch, *, diagnostic=True, email="owner@danwor.com", owner=True):
    # APP_OWNER vía monkeypatch (no asignación de módulo) para no pisar el owner
    # de otros archivos de test durante la colección.
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com" if owner else "someone-else@danwor.com")
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    session = auth_service.create_session(
        {"email": email, "name": "Owner", "first_name": "Own", "family_name": "Er"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    if diagnostic:
        c.cookies.set("dw-diagnostic", "on")
    return c


def _hx(c, url):
    return c.get(url, headers={"HX-Request": "true"})


# ── item 15/22: render de filtros Ámbito/Área con la primitiva compartida ─────

def test_dashboard_renders_ambito_area_multiselect(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    r = _hx(_owner_client(monkeypatch), "/admin/activity")
    assert r.status_code == 200
    t = r.text
    assert "<span>Ámbito</span>" in t and "<span>Área</span>" in t
    assert 'name="scope"' in t and 'name="area"' in t
    # item 22: primitiva de checkbox compartida reusada.
    assert "dashboard-checkbox-group" in t and "dashboard-checkbox-option" in t
    # item 23: sticky compartido.
    assert 'class="dashboard-sticky-header"' in t


# ── item 17: default = todos los ámbitos/áreas tildados ───────────────────────

def test_default_state_selects_all_scopes_and_areas(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    for scope in ("platform", "copilot", "operations"):
        assert f'name="scope" value="{scope}" checked' in t
    for area in ("auth", "methodology", "staffing"):
        assert f'name="area" value="{area}" checked' in t


# ── item 16: el estado de checkbox sobrevive el refresh HTMX ───────────────────

def test_checkbox_state_survives_htmx_refresh(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity?scope=copilot&scope=platform").text
    assert 'name="scope" value="copilot" checked' in t
    assert 'name="scope" value="platform" checked' in t
    # Ámbito es estable (nunca desaparece por la selección de Área/de sí mismo):
    # "operations" sigue OFRECIDO como opción, solo que destildado.
    assert 'name="scope" value="operations" >' in t
    assert 'name="scope" value="operations" checked' not in t


# ── item 18: selección inválida → fallback determinístico (todo) ──────────────

def test_invalid_scope_falls_back_to_all(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity?scope=zzz-inexistente").text
    # No se fabrica la opción inexistente…
    assert "zzz-inexistente" not in t
    # …y se cae a todos los ámbitos presentes, tildados.
    for scope in ("platform", "copilot", "operations"):
        assert f'name="scope" value="{scope}" checked' in t


# ── Reconciliación A2 (pending check #2): guard de zero-selection ─────────────
# Server-side ya cae a "todos" cuando llegan cero parámetros scope/area (ver
# test_default_state_selects_all_scopes_and_areas / test_invalid_scope_falls_back_to_all
# arriba — CUALQUIER request sin params, incluida la que produciría un
# "destildar todo" desde el form, se comporta igual). Lo que faltaba era la
# prevención en el propio checkbox: nunca dejar que el owner destilde la
# ÚLTIMA opción tildada de un grupo (Ámbito o Área), para que ese estado
# ambiguo ni siquiera llegue a enviarse. Sin runtime de JS en esta suite —
# se fija por fuente, igual que el resto de app.js.

def test_zero_selection_guard_prevents_unchecking_last_scope_or_area_option():
    assert 'if (input.name !== "scope" && input.name !== "area") return;' in _APP_JS
    assert "if (input.checked) return;" in _APP_JS
    assert 'input.closest(".dashboard-checkbox-group")' in _APP_JS
    assert 'group.querySelector(`input[name="${input.name}"]:checked`)' in _APP_JS
    assert "if (!stillChecked) input.checked = true;" in _APP_JS
    # CAPTURE (no bubble): tiene que ganarle la carrera al listener de htmx del
    # auto-apply (ver test_ambito_area_checkboxes_auto_apply_on_change abajo) —
    # si no, htmx podría leer el checkbox todavía destildado.
    assert "}, { capture: true });" in _APP_JS


# ── feedback del owner: destildar un Ámbito debe recalentar Área en el acto ───
# El faceting server-side (build_summary) ya excluye correctamente las áreas
# que no aplican al Ámbito seleccionado — lo que faltaba era que el checkbox
# de Área lo reflejara ANTES del submit manual ("Actualizar"), no solo después.
# Auto-aplicar el form al tildar/destildar Ámbito o Área (sin tocar date/user/
# módulo, que siguen requiriendo el botón) resuelve esto reusando el mismo
# faceting server-side, sin duplicar la relación ámbito↔área en JS.

def test_ambito_area_checkboxes_auto_apply_on_change(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert 'hx-trigger="submit, change from:.scope-area-checkbox"' in t
    assert 'class="scope-area-checkbox"' in t


def test_deselecting_ambito_disables_its_exclusive_area_without_removing_it(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    # Destildar Operaciones (solo scope=copilot+platform quedan) → "staffing"
    # (exclusiva de Operaciones) sigue OFRECIDA (mismo lugar en la grilla,
    # sin "salto" visual) pero disabled y destildada — nunca desaparece del DOM.
    t = _hx(_owner_client(monkeypatch), "/admin/activity?scope=copilot&scope=platform").text
    assert 'name="area" value="staffing"' in t
    assert 'name="area" value="staffing" checked' not in t
    assert 'name="area" value="staffing"  disabled' in t


# ── feedback del owner: Área queda muy alta en una sola columna ───────────────

def test_area_checkbox_group_uses_2column_grid_not_ambito(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert '<span>Área</span>\n      <div class="dashboard-checkbox-group dashboard-checkbox-group--grid-2">' in t
    assert '<span>Ámbito</span>\n      <div class="dashboard-checkbox-group">' in t


# ── feedback del owner: orden default de bloques ──────────────────────────────

def test_default_section_order_is_tendencia_modulos_eventos_preguntas_arboles(monkeypatch, tmp_path):
    rows = _HUB_ROWS + [
        {"ts_utc": "2026-03-01T10:03:00+00:00", "event": "assistant_query", "user": "u@d.com", "name": "U", "scope": "copilot", "area": "assistant", "payload": {"question": "¿Cómo funciona X?"}},
        {"ts_utc": "2026-03-01T10:04:00+00:00", "event": "tree_started", "user": "u@d.com", "name": "U", "scope": "copilot", "area": "methodology", "payload": {"tree_id": "t1", "tree_title": "Árbol de prueba"}},
    ]
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    (tmp_path / "activity_20260301.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8",
    )
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    ids = ["tendencia", "modulos-usuarios", "eventos-catalogo", "preguntas", "arboles"]
    positions = [t.index(f'data-section-id="{sid}"') for sid in ids]
    assert positions == sorted(positions), f"orden real por posición: {positions}"


# ── feedback del owner: el panel izquierdo no era redimensionable en esta página ─

def test_full_page_includes_sidebar_resize_handle(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    t = _owner_client(monkeypatch).get("/admin/activity").text
    assert 'data-resize="left"' in t


# ── item 19: gate owner-only + diagnóstico sin cambios ────────────────────────

def test_access_gate_unchanged(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    # Sin diagnóstico → 403.
    assert _owner_client(monkeypatch, diagnostic=False).get("/admin/activity").status_code == 403
    # Diagnóstico pero NO owner → 403.
    assert _owner_client(monkeypatch, owner=False).get("/admin/activity").status_code == 403
    # Owner + diagnóstico → 200.
    assert _owner_client(monkeypatch).get("/admin/activity").status_code == 200


# ── item 20: full-page y partial comparten semántica de filtro ────────────────

def test_full_page_and_partial_share_filter_semantics(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    c = _owner_client(monkeypatch)
    full = c.get("/admin/activity?scope=operations").text            # full page
    part = _hx(c, "/admin/activity?scope=operations").text           # HTMX fragment
    for t in (full, part):
        assert 'name="scope" value="operations" checked' in t
        # Área faceteada a staffing en ambos caminos; methodology sigue
        # ofrecida (mismo lugar en la grilla) pero disabled/destildada.
        assert 'name="area" value="staffing" checked' in t
        assert 'name="area" value="methodology" checked' not in t
        assert 'name="area" value="methodology"  disabled' in t


# ── item 21: sin métricas de negocio de Operations en el Activity Dashboard ───

def test_no_operations_business_metrics_invented(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity?scope=operations").text
    assert "module_viewed" in t                                      # sí: conteo de uso (canónico, corrective A2)
    # no: KPIs de negocio de Staffing (eso es el dashboard de Staffing, no éste).
    for label in ("Consultores", "Horas activas", "utilization", "capacity", "Disponibilidad de asignación"):
        assert label not in t


# ── corrective A2 — Transversal Module + Event Semantics (UI, items 36-41) ──

_TRANSVERSAL_MODULE_ROWS = _HUB_ROWS + [
    # Diagnóstico de Staffing histórico (raw, sin module/operation explícitos) —
    # debe normalizar a data_load_failed/assignments/operation=initial y NUNCA
    # aparecer como "Eventos de uso" ni inflar Top módulos.
    {"ts_utc": "2026-03-01T10:03:00+00:00", "event": "staffing_overview_error", "user": "ops@d.com", "name": "Ops", "scope": "operations", "area": "staffing", "payload": {"error_type": "ConnectionError"}},
]


def _seed_transversal(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    (tmp_path / "activity_20260301.jsonl").write_text(
        "\n".join(json.dumps(r) for r in _TRANSVERSAL_MODULE_ROWS) + "\n", encoding="utf-8",
    )


def test_module_filter_renders_asignaciones_for_operations(monkeypatch, tmp_path):
    # test 36/38: el filtro Módulo expone "Asignaciones" (label) para
    # module="OP01" (key canónica, MODULE IDENTIFIER CONVENTION V2 — Fase 2)
    # cuando hay actividad real de Staffing.
    _seed_transversal(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity?scope=operations&area=staffing").text
    assert '<option value="OP01"' in t
    assert ">Asignaciones<" in t


def test_operations_only_view_shows_nonzero_module_count(monkeypatch, tmp_path):
    # test 37: ya no dice "0 módulo(s)" para una vista Operations-only —
    # module_viewed (ex staffing_overview_viewed) cuenta como visita de módulo.
    _seed_transversal(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity?scope=operations&area=staffing").text
    assert "0 módulo(s)" not in t
    assert "1 módulo(s)" in t


def test_eventos_section_title_is_neutral(monkeypatch, tmp_path):
    # test 39: título de sección neutral (Hub-wide), ya no Copilot-centric.
    _seed_transversal(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert '<span class="dashboard-section__title">Eventos</span>' in t
    assert "Eventos y catálogo" not in t


# ── USAGE DASHBOARD SEMANTIC CLEANUP ──────────────────────────────────────────
# El Activity Dashboard responde "quién usa Danwor Hub", nunca "¿falló Graph?"
# ni "¿faltó config?" — el diagnóstico normalizado sigue persistido/
# clasificable (activity_service/activity_context), pero esta pantalla de uso
# no lo renderiza en ninguna forma: ni fila, ni heading, ni opción de
# drilldown. No hay tabs/accordion/query-string de diagnóstico en este batch.

def test_diagnostic_events_do_not_render_anywhere_in_activity_dashboard(monkeypatch, tmp_path):
    # tests 4/5/13/14: el evento diagnóstico normalizado (data_load_failed) no
    # aparece como fila de "Acciones de usuario", no aparece como opción de
    # drilldown (data-event=), y los headings viejos de split usage/diagnóstico
    # ("Eventos de uso" / "Diagnóstico" / "Eventos por tipo") ya no existen.
    _seed_transversal(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert "data_load_failed" not in t
    assert 'data-event="data_load_failed"' not in t
    assert "Eventos de uso" not in t
    assert "Diagnóstico" not in t
    assert "Eventos por tipo" not in t
    assert "Mostrar diagnóstico" not in t


def test_module_viewed_renders_with_visible_label(monkeypatch, tmp_path):
    # test 9: la key técnica canónica nunca se muestra cruda cuando hay label
    # — "module_viewed" se ve como "Vista de módulo".
    _seed_transversal(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert ">Vista de módulo<" in t
    assert ">module_viewed<" not in t


def test_event_drilldown_by_user_still_works_for_usage_event(monkeypatch, tmp_path):
    # test 15: el drilldown-por-usuario (panel derecho) sigue funcionando
    # para un evento de uso — mismo mecanismo data-breakdown/data-event de
    # siempre, ahora con la key técnica solo en atributos, nunca en texto.
    _seed_transversal(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert 'data-event="module_viewed"' in t
    assert 'data-breakdown="module_viewed"' in t
    assert ">Ops<" in t                                            # usuario del module_viewed sembrado


def test_diagnostic_only_user_does_not_appear_in_top_usuarios(monkeypatch, tmp_path):
    # test 6: un usuario que solo generó diagnóstico no debe parecer un
    # usuario activo del Hub.
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    rows = _HUB_ROWS + [
        {"ts_utc": "2026-03-01T10:05:00+00:00", "event": "staffing_overview_config_incomplete",
         "user": "diag-only@d.com", "name": "DiagOnly", "scope": "operations", "area": "staffing", "payload": {}},
    ]
    (tmp_path / "activity_20260301.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8",
    )
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    # DiagOnly SÍ puede aparecer en el <select> de filtro de usuario (facet
    # general, no una métrica de adopción) — lo que no debe pasar es que
    # aparezca como fila dentro de "Top usuarios".
    top_users_start = t.index(">Top usuarios<")
    top_users_block = t[top_users_start:top_users_start + 400]
    assert "DiagOnly" not in top_users_block


def test_copilot_usage_events_remain_visible(monkeypatch, tmp_path):
    # test 12: el corte usage-only no es Operations-específico — los eventos
    # de uso de Copiloto siguen visibles con su label.
    _seed_transversal(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity?scope=copilot").text
    assert ">Vista de módulo<" in t


# ── MODULE IDENTIFIER CONVENTION V2 — Fase 2 (Canonical Module Adoption) ─────

def test_top_modules_shows_product_label_not_raw_stem_for_copilot(monkeypatch, tmp_path):
    # test 13: el filtro Módulo y Top Módulos muestran el LABEL de producto
    # (via module_registry), nunca la key técnica cruda — ni el stem legacy
    # de Copiloto ni el module_id canónico donde exista un label.
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    rows = [
        {"ts_utc": "2026-03-01T10:00:00+00:00", "event": "module_viewed", "user": "u@d.com", "name": "U",
         "scope": "copilot", "area": "methodology", "module": "01_Arquitectura_y_Conectividad", "payload": {}},
    ]
    (tmp_path / "activity_20260301.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8",
    )
    t = _hx(_owner_client(monkeypatch), "/admin/activity?scope=copilot").text
    assert ">Arquitectura y Conectividad<" in t
    assert "01_Arquitectura_y_Conectividad" not in t
    assert '<option value="CP01"' in t


# ── USAGE METRICS DAILY TREND CORRECTIVE (owner ticket) ──────────────────────
# "Tendencia diaria" pasa de MM/DD a DD/MM y sus barras se vuelven clickeables
# (Desde=Hasta=día clickeado, MISMO form/contrato que "Actualizar" — nunca un
# segundo estado de filtro). `_seed_two_days` siembra DOS días bien distintos
# (03 y 28) a propósito: si mes/día se invirtieran seguirían siendo strings de
# 2 dígitos válidos y un bug de orden pasaría desapercibido con un solo día.

def _seed_two_days(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    day1 = [
        {"ts_utc": "2026-08-03T10:00:00+00:00", "event": "login", "user": "a@d.com", "name": "A", "scope": "platform", "area": "auth", "payload": {}},
    ]
    day2 = [
        {"ts_utc": "2026-08-28T10:00:00+00:00", "event": "login", "user": "b@d.com", "name": "B", "scope": "platform", "area": "auth", "payload": {}},
        {"ts_utc": "2026-08-28T11:00:00+00:00", "event": "login", "user": "c@d.com", "name": "C", "scope": "platform", "area": "auth", "payload": {}},
    ]
    (tmp_path / "activity_20260803.jsonl").write_text("\n".join(json.dumps(r) for r in day1) + "\n", encoding="utf-8")
    (tmp_path / "activity_20260828.jsonl").write_text("\n".join(json.dumps(r) for r in day2) + "\n", encoding="utf-8")


# A. DD/MM formatting — nunca MM/DD, mes y día no pueden invertirse.

def test_tendencia_diaria_labels_render_dd_mm_not_mm_dd(monkeypatch, tmp_path):
    _seed_two_days(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert '<span class="activity-chart__label">03/08</span>' in t
    assert '<span class="activity-chart__label">28/08</span>' in t
    assert '<span class="activity-chart__label">08/03</span>' not in t
    assert '<span class="activity-chart__label">08/28</span>' not in t


# B. la barra es la superficie de interacción — data-day canónico (YYYYMMDD,
# nunca derivado del label DD/MM) + primitiva accesible reusada, cero clase
# nueva inventada.

def test_tendencia_diaria_bars_are_clickable_and_carry_canonical_day(monkeypatch, tmp_path):
    _seed_two_days(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert 'data-day="20260803"' in t
    assert 'data-day="20260828"' in t
    assert 'class="activity-chart__col activity-bar-row--clickable' in t
    assert 'role="button"' in t and 'tabindex="0"' in t
    assert 'aria-label="Filtrar Métricas de uso por el 03/08"' in t


# C/D. el click reusa el MISMO form/contrato Desde-Hasta (nunca una fetch
# manual nueva) — probado por contenido de fuente, mismo criterio que el
# resto de este archivo para app.js (ver _APP_JS arriba, "sin runtime de JS
# en esta suite"). `.is-active` reconcilia server-side contra date_from/
# date_to ya existentes — sin estado de UI paralelo.

def test_daily_trend_click_reuses_existing_date_filter_contract_in_app_js():
    assert "function initActivityChartFilter(scope)" in _APP_JS
    assert 'scope.querySelectorAll(".activity-chart__col[data-day]")' in _APP_JS
    assert 'form.querySelector(\'input[name="date_from"]\')' in _APP_JS
    assert 'form.querySelector(\'input[name="date_to"]\')' in _APP_JS
    assert "from.value = iso;" in _APP_JS and "to.value = iso;" in _APP_JS
    assert "if (form.requestSubmit) form.requestSubmit(); else form.submit();" in _APP_JS
    # re-cableo tras cada swap HTMX — mismo patrón que initModuleFilter.
    assert 'document.body.addEventListener("htmx:load", () => initActivityChartFilter(document));' in _APP_JS


def test_selecting_a_day_marks_only_that_bar_active_and_second_click_replaces_it(monkeypatch, tmp_path):
    _seed_two_days(monkeypatch, tmp_path)
    c = _owner_client(monkeypatch)
    t1 = _hx(c, "/admin/activity?date_from=2026-08-03&date_to=2026-08-03").text
    col1 = t1.split('data-day="20260803"')[0].rsplit("<div class=", 1)[-1]
    assert "is-active" in col1
    col2 = t1.split('data-day="20260828"')[0].rsplit("<div class=", 1)[-1]
    assert "is-active" not in col2
    # segundo click (otro día) reemplaza limpiamente la selección — nunca dos
    # barras activas a la vez.
    t2 = _hx(c, "/admin/activity?date_from=2026-08-28&date_to=2026-08-28").text
    col1b = t2.split('data-day="20260803"')[0].rsplit("<div class=", 1)[-1]
    col2b = t2.split('data-day="20260828"')[0].rsplit("<div class=", 1)[-1]
    assert "is-active" not in col1b
    assert "is-active" in col2b


def test_selecting_a_day_narrows_dependent_content_and_echoes_date_controls(monkeypatch, tmp_path):
    # "todo lo dependiente reconcilia al día elegido" + los controles Desde/
    # Hasta visibles reflejan la selección (mismo `value=` de siempre).
    _seed_two_days(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity?date_from=2026-08-28&date_to=2026-08-28").text
    assert 'name="date_from" value="2026-08-28"' in t
    assert 'name="date_to" value="2026-08-28"' in t
    assert "2 evento(s)" in t  # Resumen reconcilia: día 28 tiene 2 eventos, no 3.
    assert 'title="20260803: 1 evento(s)"' not in t  # el 03 ya no aparece en la serie filtrada.


# D. el filtro normal (sin pasar por el gráfico) sigue funcionando exactamente
# igual después de este corrective — mismo contrato, ninguna regresión.

def test_normal_date_filter_still_works_after_chart_corrective(monkeypatch, tmp_path):
    _seed_two_days(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity?date_from=2026-08-01&date_to=2026-08-10").text
    assert 'data-day="20260803"' in t  # rango incluye el 03...
    assert 'data-day="20260828"' not in t  # ...y excluye el 28, como siempre.


# E. sin regresión al render no-click del gráfico (conteo/altura de barra).

def test_tendencia_diaria_bar_count_and_height_render_unchanged(monkeypatch, tmp_path):
    _seed_two_days(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert '<span class="activity-chart__count">1</span>' in t
    assert '<span class="activity-chart__count">2</span>' in t
    assert 'style="height:100%"' in t  # el día de 2 eventos es el pico → 100%.


# ── USAGE METRICS DAILY TREND — GUARDRAIL DE PAGINACIÓN (owner ticket) ───────
# Más de 25 días no entran legibles en el ancho fijo del gráfico de barras —
# a partir de ahí aparece un paginador (MISMO primitivo `.activity-pager` que
# Historia de preguntas/Recorridos), page 1 = los 25 días MÁS RECIENTES.

def _seed_n_days(monkeypatch, tmp_path, n, *, start="2026-07-01"):
    from datetime import date, timedelta
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    d0 = date.fromisoformat(start)
    for i in range(n):
        day = (d0 + timedelta(days=i)).strftime("%Y%m%d")
        row = {"ts_utc": f"{day[:4]}-{day[4:6]}-{day[6:8]}T10:00:00+00:00", "event": "login",
               "user": "u@d.com", "name": "U", "scope": "platform", "area": "auth", "payload": {}}
        (tmp_path / f"activity_{day}.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")


def test_no_pager_when_25_days_or_fewer(monkeypatch, tmp_path):
    _seed_n_days(monkeypatch, tmp_path, 25)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert 'data-day="20260701"' in t and 'data-day="20260725"' in t  # los 25 entran enteros...
    assert 'class="activity-pager"' not in t  # ...sin paginador — mismo comportamiento de siempre.


def test_pager_appears_past_25_days_and_defaults_to_most_recent_window(monkeypatch, tmp_path):
    _seed_n_days(monkeypatch, tmp_path, 30)  # 2026-07-01 .. 2026-07-30
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert 'class="activity-pager"' in t
    # page 1 = los 25 MÁS RECIENTES (06..30) — los 5 más viejos (01..05) no
    # entran en esta página.
    assert 'data-day="20260706"' in t and 'data-day="20260730"' in t
    assert 'data-day="20260701"' not in t and 'data-day="20260705"' not in t
    # primera/anterior deshabilitadas en la página 1; hay una página 2.
    assert 'title="Primera página"\n            disabled' in t
    assert 'title="Anterior"\n            disabled' in t
    assert 'trend_page=2' in t
    # header "{{ total_days }} día(s)" sigue siendo el TOTAL real, no el de la página.
    assert "30 día(s)" in t


def test_pager_page_2_shows_the_older_remainder_via_dedicated_fragment_route(monkeypatch, tmp_path):
    _seed_n_days(monkeypatch, tmp_path, 30)
    c = _owner_client(monkeypatch)
    t = _hx(c, "/admin/activity/trend?trend_page=2").text
    # fragment liviano: solo el body de Tendencia, MISMO patrón que
    # /admin/activity/stories y /admin/activity/tree-runs.
    assert t.strip().startswith('<div id="activity-daily-trend-body">')
    assert 'data-day="20260701"' in t and 'data-day="20260705"' in t
    assert 'data-day="20260706"' not in t  # esos ya se vieron en la página 1.
    # siguiente/última deshabilitadas en la última página; primera/anterior habilitadas.
    assert 'title="Siguiente"\n            disabled' in t
    assert 'title="Última página"\n            disabled' in t
    assert 'trend_page=1' in t


def test_daily_trend_pager_propagates_active_filters_like_stories_pager(monkeypatch, tmp_path):
    # MISMO contrato que Historia de preguntas: cambiar de página nunca
    # descarta el filtro de usuario/fecha ya aplicado (`base_qs`).
    _seed_n_days(monkeypatch, tmp_path, 30)
    t = _hx(_owner_client(monkeypatch), "/admin/activity?user=u%40d.com").text
    assert "user=u%40d.com" in t


