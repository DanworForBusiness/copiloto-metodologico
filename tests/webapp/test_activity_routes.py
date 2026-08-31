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
from webapp.services import activity_context

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
    ids = ["tendencia", "modulos-usuarios", "eventos-catalogo", "reacciones", "preguntas", "arboles"]
    positions = [t.index(f'data-section-id="{sid}"') for sid in ids]
    assert positions == sorted(positions), f"orden real por posición: {positions}"


# ── feedback del owner: el panel izquierdo no era redimensionable en esta página ─

def test_full_page_includes_sidebar_resize_handle(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    t = _owner_client(monkeypatch).get("/admin/activity").text
    assert 'data-resize="left"' in t


# ── item 19 (SUPERSEDED — owner ticket "HUB METRICS — CENTRALIZE ACCESS AND
# ENABLE BY VERTICAL"): el gate owner-only + diagnóstico se reemplazó por la
# autorización CENTRALIZADA de Metrics (`webapp.hub_metrics_access`), que
# YA NO exige modo diagnóstico -- justamente para que Operations pueda
# llegar hasta acá sin ser "modo diagnóstico" de Copiloto (ver
# test_hub_metrics_access.py para la cobertura A-J completa del nuevo
# contrato). Sin `HUB_METRICS_ALLOWED_USERS` configurado (caso de ESTE
# archivo/`_owner_client`, que nunca la toca), el comportamiento OBSERVABLE
# para owner/no-owner es idéntico al histórico -- eso es lo que se sigue
# probando acá. ──────────────────────────────────────────────────────────

def test_access_gate_unchanged(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    # Sin modo diagnóstico YA NO importa -- Metrics no depende de eso.
    assert _owner_client(monkeypatch, diagnostic=False).get("/admin/activity").status_code == 200
    # NO owner (y sin HUB_METRICS_ALLOWED_USERS configurado) → 403, MISMO
    # resultado observable que el gate histórico.
    assert _owner_client(monkeypatch, owner=False).get("/admin/activity").status_code == 403
    # Owner → 200, MISMO resultado observable que el gate histórico.
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


# ── USAGE METRICS REACTIONS DRILLDOWN (owner ticket) ──────────────────────────
# "Reacciones": izquierda = sección (área) + total, derecha = usuarios de esa
# sección — MISMO motor que "Eventos" (`data-breakdown-group`/
# initEventBreakdown, ver app.js — sin cambios, cero test nuevo de JS: la
# cobertura existente de Eventos ya prueba el engine). Fuente: los eventos de
# feedback transversal YA existentes (reporting.feedback/staffing.feedback/
# commercial.feedback), señal = payload.feedback_sentiment.

_REACTION_ROWS = [
    {"ts_utc": "2026-03-01T10:05:00+00:00", "event": "reporting.feedback", "user": "a@d.com", "name": "A",
     "scope": "operations", "area": "reporting", "module": "reporting",
     "payload": {"report_tab": "cartera", "feedback_sentiment": "positive"}},
    {"ts_utc": "2026-03-01T10:06:00+00:00", "event": "staffing.feedback", "user": "b@d.com", "name": "B",
     "scope": "operations", "area": "staffing", "module": "asignaciones",
     "payload": {"feedback_sentiment": "negative"}},
    {"ts_utc": "2026-03-01T10:07:00+00:00", "event": "staffing.feedback", "user": "a@d.com", "name": "A",
     "scope": "operations", "area": "staffing", "module": "asignaciones",
     "payload": {"feedback_sentiment": "positive"}},
    # día distinto (para probar el filtro de fecha / integración con Tendencia).
    {"ts_utc": "2026-03-05T09:00:00+00:00", "event": "commercial.feedback", "user": "c@d.com", "name": "C",
     "scope": "commercial", "area": "crm", "module": "CM01",
     "payload": {"capability": "dashboard", "feedback_sentiment": "positive"}},
]


def _seed_reactions(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    # RELEASE-EVIDENCE COROLLARY (owner audit — "prove the committed tree, not
    # the dirty worktree"): `commercial.feedback` ya se emite (routers/
    # commercial.py) pero su registro en `_USAGE_EVENTS`/`EVENT_LABELS`
    # (activity_context.py) sigue DELIBERADAMENTE sin commitear — pertenece
    # a un ticket propio (Commercial feedback route homologation), ver
    # HANDOFF/commit ce82abf. Este test prueba que Reacciones agrega
    # CUALQUIER evento `<x>.feedback` clasificado `usage` (la garantía
    # genérica que build_summary ya ofrece, ver su propio docstring —
    # nunca una lista hardcodeada), así que se registra acá, LOCAL al test,
    # en vez de depender de que ese otro commit ya haya aterrizado — nunca
    # se toca el archivo real de producción.
    monkeypatch.setattr(
        activity_context, "_USAGE_EVENTS",
        activity_context._USAGE_EVENTS | {"commercial.feedback"},
    )
    # `EVENT_LABELS` se parchea en `activity_analytics` (no en
    # `activity_context`): ese módulo lo importa por VALOR
    # (`from ...activity_context import (..., EVENT_LABELS)`), así que
    # reasignar el dict en `activity_context` después de ese import no
    # afecta el binding local que `build_summary` realmente lee
    # (`"event_labels": EVENT_LABELS`, ver activity_analytics.py) —
    # parchear el módulo equivocado dejaría este fixture pasando en
    # falso.
    monkeypatch.setattr(
        activity_analytics, "EVENT_LABELS",
        {**activity_analytics.EVENT_LABELS, "commercial.feedback": "Comercial — feedback de capability"},
    )
    # ISOLACIÓN: `_read_reaction_comments` lee FEEDBACK_LOG_DIR — sin esto,
    # un test sin comentario propio terminaría leyendo el FEEDBACK_LOG_DIR
    # REAL de este entorno (mismo criterio que `_isolate_remote_store_
    # persistence`/`_isolate_runtime_logs` en tests/conftest.py). Un vacío
    # propio por default; los tests de comentario lo pisan explícitamente
    # DESPUÉS de llamar acá (gana el último monkeypatch, mismo orden).
    empty_feedback_dir = tmp_path / "_no_feedback_yaml"
    empty_feedback_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(activity_analytics, "FEEDBACK_LOG_DIR", empty_feedback_dir)
    day1 = [r for r in _REACTION_ROWS if r["ts_utc"].startswith("2026-03-01")]
    day2 = [r for r in _REACTION_ROWS if r["ts_utc"].startswith("2026-03-05")]
    (tmp_path / "activity_20260301.jsonl").write_text("\n".join(json.dumps(r) for r in day1) + "\n", encoding="utf-8")
    (tmp_path / "activity_20260305.jsonl").write_text("\n".join(json.dumps(r) for r in day2) + "\n", encoding="utf-8")


# A + B: el colapsable renderiza y agrega por sección, descendente por conteo
# (Staffing 2, Reporting 1, CRM 1 — mismo criterio most_common() que Eventos).

def test_reacciones_collapsible_renders_and_aggregates_by_section(monkeypatch, tmp_path):
    _seed_reactions(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert 'data-section-id="reacciones"' in t
    assert '<span class="dashboard-section__title">Reacciones</span>' in t
    assert "4 reacción(es)" in t
    assert 'data-event="staffing"' in t and 'data-event="reporting"' in t and 'data-event="crm"' in t
    # Staffing tiene 2 reacciones, más que Reporting/CRM (1 c/u) — aparece
    # primero (most_common(), MISMO orden que Eventos).
    assert t.index('data-event="staffing"') < t.index('data-event="reporting"')


# C + D: cada panel de sección contiene SOLO sus propios usuarios — sin
# contaminación cruzada (la selección/reemplazo en sí es JS ya probado por
# Eventos, initEventBreakdown, sin cambios).

def _panel_html(t, key):
    # Aísla SOLO el contenido de un panel data-breakdown="<key>" hasta el
    # PRÓXIMO marcador data-breakdown=, sin depender de indentación exacta.
    after = t.split(f'data-breakdown="{key}"', 1)[1]
    return after.split('data-breakdown="')[0]


def test_reacciones_right_panel_scoped_to_its_own_section_users_only(monkeypatch, tmp_path):
    _seed_reactions(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    staffing_panel = _panel_html(t, "staffing")
    assert 'activity-bar-label" title="a@d.com"' in staffing_panel
    assert 'activity-bar-label" title="b@d.com"' in staffing_panel
    assert 'title="c@d.com"' not in staffing_panel  # C reaccionó en CRM, nunca en Staffing.
    reporting_panel = _panel_html(t, "reporting")
    assert 'activity-bar-label" title="a@d.com"' in reporting_panel
    assert 'title="b@d.com"' not in reporting_panel


# E + F: el filtro de fecha activo (incluida la integración con el click-to-
# day de Tendencia diaria, que setea date_from=date_to=día) recorta izquierda
# Y derecha — mismo universo `events` que ya usa Eventos, nunca un segundo
# filtro paralelo.

def test_reacciones_respects_active_date_filter_and_daily_trend_click(monkeypatch, tmp_path):
    _seed_reactions(monkeypatch, tmp_path)
    # "click en 05/03 en Tendencia diaria" == date_from=date_to=2026-03-05.
    t = _hx(_owner_client(monkeypatch), "/admin/activity?date_from=2026-03-05&date_to=2026-03-05").text
    assert "1 reacción(es)" in t
    assert 'data-event="crm"' in t
    assert 'data-event="staffing"' not in t and 'data-event="reporting"' not in t


# G: 0 reacciones bajo el filtro actual es un estado VÁLIDO, no un error —
# nunca se confunde con "No disponible".

def test_reacciones_empty_state_is_valid_not_an_error(monkeypatch, tmp_path):
    _seed_reactions(monkeypatch, tmp_path)
    # Un día CON actividad de uso (así el dashboard entero no cae al estado
    # "Sin actividad" — ver `{% if summary.total_events == 0 %}` — eso es una
    # capa DISTINTA, ya cubierta por test_no_operations_business_metrics_
    # invented y otros) pero SIN reacciones — exactamente el caso que este
    # ticket pide distinguir de "No disponible".
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    (tmp_path / "activity_20260310.jsonl").write_text(
        json.dumps({"ts_utc": "2026-03-10T09:00:00+00:00", "event": "login", "user": "d@d.com", "name": "D",
                    "scope": "platform", "area": "auth", "payload": {}}) + "\n",
        encoding="utf-8",
    )
    t = _hx(_owner_client(monkeypatch), "/admin/activity?date_from=2026-03-10&date_to=2026-03-10").text
    assert 'data-section-id="reacciones"' in t  # la sección sigue ahí...
    assert "0 reacción(es)" in t
    assert '<span class="dashboard-empty__label">Sin reacciones</span>' in t
    assert "No disponible" not in t


# H: Eventos/Preguntas/Árboles no regresionan — los 3 eventos de feedback
# siguen apareciendo TAMBIÉN en "Eventos" (Reacciones es una lente adicional,
# nunca un reemplazo).

def test_existing_eventos_section_still_lists_feedback_events_unchanged(monkeypatch, tmp_path):
    _seed_reactions(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert ">Reporting — feedback de reporte<" in t
    assert ">Asignaciones — feedback de reporte<" in t
    assert ">Comercial — feedback de capability<" in t


# ── Reacciones: tooltip 👍/👎 + detalle de usuario estilo "Historia de
# preguntas" (owner, follow-up mid-ticket) ────────────────────────────────

def test_reaction_bar_hover_tooltip_shows_like_dislike_breakdown(monkeypatch, tmp_path):
    _seed_reactions(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    # Staffing: A up + B down = 1 👍, 1 👎, de 2 totales.
    assert 'title="Staffing: 1 👍 · 1 👎 de 2"' in t
    # Reporting: solo A, up.
    assert 'title="Reporting: 1 👍 · 0 👎 de 1"' in t


def test_reaction_user_row_shows_like_dislike_emoji_counts(monkeypatch, tmp_path):
    _seed_reactions(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    staffing_panel = _panel_html(t, "staffing")
    assert "👍 1</span><span title=\"No le gustó\">👎 0" in staffing_panel  # A, up
    assert "👍 0</span><span title=\"No le gustó\">👎 1" in staffing_panel  # B, down


def _seed_reaction_comment(monkeypatch, tmp_path, *, feedback_type, user, ts, rating, comment=None):
    import yaml as _yaml
    from webapp.services import activity_analytics as _aa
    monkeypatch.setattr(_aa, "FEEDBACK_LOG_DIR", tmp_path)
    payload = {"type": feedback_type, "user": user, "ts": ts, "rating": rating}
    if comment:
        payload["comment"] = comment
    (tmp_path / f"{feedback_type}_{user.replace('@', '_')}.yaml").write_text(
        _yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8",
    )


def test_reaction_user_with_comment_is_expandable_and_shows_it(monkeypatch, tmp_path):
    activity_dir = tmp_path / "activity"
    activity_dir.mkdir()
    feedback_dir = tmp_path / "feedback"
    feedback_dir.mkdir()
    _seed_reactions(monkeypatch, activity_dir)
    _seed_reaction_comment(
        monkeypatch, feedback_dir, feedback_type="reporting_report", user="a@d.com",
        ts="2026-03-01T10:05:00+00:00", rating="up", comment="Buenísimo el nuevo tab de Cartera",
    )
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    reporting_panel = _panel_html(t, "reporting")
    assert "<details class=\"assistant-story\">" in reporting_panel
    assert "Buenísimo el nuevo tab de Cartera" in reporting_panel
    assert "2026-03-01 10:05:00" in reporting_panel


def test_reaction_user_without_comment_is_a_plain_non_expandable_row(monkeypatch, tmp_path):
    # `_seed_reactions` ya aísla FEEDBACK_LOG_DIR a un directorio vacío por
    # default (ver ahí) — B (staffing, down) nunca dejó comentario, cero YAML
    # de feedback para user=b@d.com a propósito.
    _seed_reactions(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    staffing_panel = _panel_html(t, "staffing")
    assert "<details class=\"assistant-story\">" not in staffing_panel


# ── Reacciones: Copiloto (owner — "el copiloto tiene un sinnúmero de
# reacciones que debe ser tenido en cuenta si el checkbox de copiloto esta
# encendido o sus derivados donde se ofrezcan reacciones") ───────────────────
# module.feedback/section.feedback (área methodology) y catalog.feedback/
# catalog_section.feedback (área catalog) — MISMA señal genérica
# (payload.feedback_sentiment) que reporting.feedback/staffing.feedback/
# commercial.feedback ya usan, cero cambio en build_summary: la fuente
# (webapp/routers/feedback.py, ver test_feedback_activity_reactions.py) es lo
# que faltaba, no la agregación.

_COPILOT_REACTION_ROWS = [
    {"ts_utc": "2026-03-01T11:00:00+00:00", "event": "module.feedback", "user": "d@d.com", "name": "D",
     "scope": "copilot", "area": "methodology", "module": "CP01",
     "payload": {"feedback_sentiment": "positive"}},
    {"ts_utc": "2026-03-01T11:01:00+00:00", "event": "section.feedback", "user": "d@d.com", "name": "D",
     "scope": "copilot", "area": "methodology", "module": "CP01",
     "payload": {"feedback_sentiment": "negative"}},
    {"ts_utc": "2026-03-01T11:02:00+00:00", "event": "catalog.feedback", "user": "e@d.com", "name": "E",
     "scope": "copilot", "area": "catalog", "payload": {"feedback_sentiment": "positive"}},
]


def _seed_copilot_reactions(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    empty_feedback_dir = tmp_path / "_no_feedback_yaml"
    empty_feedback_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(activity_analytics, "FEEDBACK_LOG_DIR", empty_feedback_dir)
    (tmp_path / "activity_20260301.jsonl").write_text(
        "\n".join(json.dumps(r) for r in _COPILOT_REACTION_ROWS) + "\n", encoding="utf-8",
    )


def test_copilot_module_and_section_feedback_aggregate_under_metodologia(monkeypatch, tmp_path):
    _seed_copilot_reactions(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert "3 reacción(es)" in t
    assert 'data-event="methodology"' in t
    # D reaccionó 2 veces (module up + section down) en Metodología.
    assert 'title="Metodología: 1 👍 · 1 👎 de 2"' in t


def test_copilot_catalog_feedback_aggregates_under_catalogo(monkeypatch, tmp_path):
    _seed_copilot_reactions(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert 'data-event="catalog"' in t
    assert 'title="Catálogo: 1 👍 · 0 👎 de 1"' in t
    catalog_panel = _panel_html(t, "catalog")
    assert 'title="e@d.com"' in catalog_panel
    assert 'title="d@d.com"' not in catalog_panel  # D reaccionó en Metodología, nunca en Catálogo.


def test_unchecking_copilot_ambito_excludes_its_reactions_from_the_left_panel(monkeypatch, tmp_path):
    # "debe ser tenido en cuenta SI el checkbox de copiloto esta encendido" —
    # el reverso también aplica: destildar Copiloto en Ámbito debe recortar
    # Reacciones exactamente como recorta Eventos/Tendencia (MISMO universo
    # `events` ya filtrado, nunca un segundo filtro paralelo para Reacciones).
    # Mezcla copilot + operations a propósito: una selección que NO
    # intersecta nada del universo presente cae al fallback "todo tildado"
    # (guard existente, ver build_summary `sel_scopes`) — con SOLO eventos
    # copilot sembrados, pedir operations/commercial daría un falso "excluido"
    # por ese fallback, no por el filtro en sí.
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    empty_feedback_dir = tmp_path / "_no_feedback_yaml"
    empty_feedback_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(activity_analytics, "FEEDBACK_LOG_DIR", empty_feedback_dir)
    mixed_rows = _COPILOT_REACTION_ROWS + [
        {"ts_utc": "2026-03-01T11:03:00+00:00", "event": "staffing.feedback", "user": "f@d.com", "name": "F",
         "scope": "operations", "area": "staffing", "module": "asignaciones",
         "payload": {"feedback_sentiment": "positive"}},
    ]
    (tmp_path / "activity_20260301.jsonl").write_text(
        "\n".join(json.dumps(r) for r in mixed_rows) + "\n", encoding="utf-8",
    )
    t = _hx(_owner_client(monkeypatch), "/admin/activity?scope=operations").text
    assert 'data-event="methodology"' not in t
    assert 'data-event="catalog"' not in t
    assert 'data-event="staffing"' in t
    assert "1 reacción(es)" in t


# ── Reacciones: Copiloto, segunda ronda — Academy/Asistente/Árboles (owner
# AUDIT: "el copiloto tiene un sinnúmero de reacciones", cobertura completa)
# ─────────────────────────────────────────────────────────────────────────
# academy.feedback (área academy, NUEVA que Reacciones puede mostrar),
# assistant.feedback (área assistant, ídem) y tree.feedback (área
# methodology — MISMA área/bucket que module.feedback/section.feedback, ver
# _COPILOT_REACTION_ROWS arriba: prueba que "Tree pertenece a methodology"
# se cumple de punta a punta, no solo en el mapeo). Fixture SEPARADA para no
# tener que reajustar los conteos hardcodeados de los tests de la primera
# ronda arriba.

_COPILOT_REACTION_ROWS_2 = [
    {"ts_utc": "2026-04-01T09:00:00+00:00", "event": "academy.feedback", "user": "g@d.com", "name": "G",
     "scope": "copilot", "area": "academy", "module": "ACAD01_Paradigmas",
     "payload": {"feedback_sentiment": "positive"}},
    {"ts_utc": "2026-04-01T09:01:00+00:00", "event": "assistant.feedback", "user": "h@d.com", "name": "H",
     "scope": "copilot", "area": "assistant", "module": "CP01",
     "payload": {"feedback_sentiment": "negative", "reply_id": "reply-abc", "conversation_id": "conv-abc"}},
    {"ts_utc": "2026-04-01T09:02:00+00:00", "event": "tree.feedback", "user": "d@d.com", "name": "D",
     "scope": "copilot", "area": "methodology",
     "payload": {"feedback_sentiment": "positive", "run_id": "run-1", "tree_id": "tree-1"}},
    # día distinto — MISMA prueba de integración con Tendencia diaria/filtro
    # de fecha que ya hace test_reacciones_respects_active_date_filter_and_
    # daily_trend_click, ahora también sobre un productor de la 2ª ronda.
    {"ts_utc": "2026-04-05T10:00:00+00:00", "event": "academy.feedback", "user": "g@d.com", "name": "G",
     "scope": "copilot", "area": "academy", "module": "ACAD02_Datasphere",
     "payload": {"feedback_sentiment": "positive"}},
]


def _seed_copilot_reactions_2(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    empty_feedback_dir = tmp_path / "_no_feedback_yaml_2"
    empty_feedback_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(activity_analytics, "FEEDBACK_LOG_DIR", empty_feedback_dir)
    day1 = [r for r in _COPILOT_REACTION_ROWS_2 if r["ts_utc"].startswith("2026-04-01")]
    day2 = [r for r in _COPILOT_REACTION_ROWS_2 if r["ts_utc"].startswith("2026-04-05")]
    (tmp_path / "activity_20260401.jsonl").write_text("\n".join(json.dumps(r) for r in day1) + "\n", encoding="utf-8")
    (tmp_path / "activity_20260405.jsonl").write_text("\n".join(json.dumps(r) for r in day2) + "\n", encoding="utf-8")


def test_academy_feedback_aggregates_under_academy_area(monkeypatch, tmp_path):
    _seed_copilot_reactions_2(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert 'data-event="academy"' in t
    academy_panel = _panel_html(t, "academy")
    assert 'title="g@d.com"' in academy_panel


def test_assistant_feedback_aggregates_under_assistant_area(monkeypatch, tmp_path):
    _seed_copilot_reactions_2(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert 'data-event="assistant"' in t
    assistant_panel = _panel_html(t, "assistant")
    assert 'title="h@d.com"' in assistant_panel
    assert 'title="Asistente: 0 👍 · 1 👎 de 1"' in t


def test_tree_feedback_aggregates_under_methodology_same_bucket_as_module_section(monkeypatch, tmp_path):
    _seed_copilot_reactions_2(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert 'data-event="methodology"' in t
    methodology_panel = _panel_html(t, "methodology")
    assert 'title="d@d.com"' in methodology_panel  # D reaccionó vía tree.feedback, MISMO bucket.


def test_second_round_producers_are_classified_usage_and_labeled():
    from webapp.services import activity_context
    for event in ("academy.feedback", "assistant.feedback", "tree.feedback"):
        assert activity_context.event_class(event) == activity_context.EVENT_CLASS_USAGE
        assert activity_context.event_label(event) != event  # tiene label propio, nunca el nombre crudo.


def test_daily_trend_click_filters_second_round_producers_to_their_original_day(monkeypatch, tmp_path):
    # O: el filtro de fecha (incluida la integración con Tendencia diaria)
    # también recorta correctamente los productores de la 2ª ronda — MISMO
    # universo `events`, nunca un camino de filtro paralelo.
    _seed_copilot_reactions_2(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity?date_from=2026-04-05&date_to=2026-04-05").text
    assert 'data-event="academy"' in t
    assert "1 reacción(es)" in t  # solo la reacción del 05, no las 3 del 01.
    assert 'data-event="assistant"' not in t
    assert 'data-event="methodology"' not in t


# ── Reacciones: objeto reaccionado (owner — "no poder ver exactamente dónde
# likeó es raro") ───────────────────────────────────────────────────────────
# A/F/G/H/I/K del ticket "REACCIONES — PER-USER REACTED-OBJECT BREAKDOWN":
# el detalle expandible ahora muestra A QUÉ objeto concreto reaccionó cada
# persona (módulo/sección/reporte/etc.), no solo el total 👍/👎 de su área.

_SUBJECT_ROWS = [
    # I: usuario D reacciona en DOS módulos distintos dentro de Metodología
    # -> dos filas de "objeto" separadas (A).
    {"ts_utc": "2026-05-01T10:00:00+00:00", "event": "module.feedback", "user": "i@d.com", "name": "I",
     "scope": "copilot", "area": "methodology", "module": "CP01",
     "payload": {"feedback_sentiment": "positive"}},
    {"ts_utc": "2026-05-01T10:01:00+00:00", "event": "module.feedback", "user": "i@d.com", "name": "I",
     "scope": "copilot", "area": "methodology", "module": "CP02",
     "payload": {"feedback_sentiment": "negative"}},
    # G: MISMO usuario I también reacciona en Staffing (área DISTINTA, sin
    # sub-identidad real) -- cuenta en el total de Staffing, nunca fabrica
    # un "objeto" para esa reacción.
    {"ts_utc": "2026-05-01T10:02:00+00:00", "event": "staffing.feedback", "user": "i@d.com", "name": "I",
     "scope": "operations", "area": "staffing", "module": "OP01",
     "payload": {"feedback_sentiment": "positive"}},
    # día distinto -- integración con el filtro de fecha (H).
    {"ts_utc": "2026-05-05T09:00:00+00:00", "event": "module.feedback", "user": "i@d.com", "name": "I",
     "scope": "copilot", "area": "methodology", "module": "CP01",
     "payload": {"feedback_sentiment": "positive"}},
    # K: registro HISTÓRICO backfilleado (module.feedback, MISMO shape que
    # backfill_copilot_reactions.py produce — con `backfill_marker`, sin
    # ningún campo enriquecido más allá de `module`) -- prueba que Reacciones
    # SÍ resuelve subject para lo que el backfill actual ya alcanza a cubrir.
    {"ts_utc": "2026-05-01T11:00:00+00:00", "event": "module.feedback", "user": "j@d.com", "name": "J",
     "scope": "copilot", "area": "methodology", "module": "CP03",
     "payload": {"feedback_sentiment": "positive", "backfill_marker": "abc123", "backfill_source_type": "module_content"}},
]


def _seed_subject_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    empty_feedback_dir = tmp_path / "_no_feedback_yaml_subjects"
    empty_feedback_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(activity_analytics, "FEEDBACK_LOG_DIR", empty_feedback_dir)
    day1 = [r for r in _SUBJECT_ROWS if r["ts_utc"].startswith("2026-05-01")]
    day2 = [r for r in _SUBJECT_ROWS if r["ts_utc"].startswith("2026-05-05")]
    (tmp_path / "activity_20260501.jsonl").write_text("\n".join(json.dumps(r) for r in day1) + "\n", encoding="utf-8")
    (tmp_path / "activity_20260505.jsonl").write_text("\n".join(json.dumps(r) for r in day2) + "\n", encoding="utf-8")


def test_user_with_reactions_in_two_modules_shows_two_subject_rows(monkeypatch, tmp_path):
    _seed_subject_rows(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    methodology_panel = _panel_html(t, "methodology")
    # I aparece expandible (tiene subjects, aunque nunca dejó comentario) y
    # con DOS filas de objeto dentro de su detalle.
    assert 'title="i@d.com"' in methodology_panel
    assert '<div class="reaction-subjects">' in methodology_panel
    # F: reconciliación -- I tiene 2 reacciones en methodology (día 1) y
    # AMBAS resolvieron subject -- el total del usuario (👍1 👎1 en el día 1
    # + 👍1 el día 5 = 👍2 👎1) debe coincidir con la suma de sus objetos.
    assert '👍 2</span><span title="No le gustó">👎 1' in methodology_panel


def test_unattributable_reaction_stays_in_total_without_fake_subject(monkeypatch, tmp_path):
    _seed_subject_rows(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    staffing_panel = _panel_html(t, "staffing")
    # G: staffing.feedback nunca tiene objeto -- ni "General" ni "Otro", el
    # usuario I NO debería ser expandible en Staffing (sin comentario NI
    # subjects), aunque SÍ cuenta en el total de arriba de Staffing.
    assert 'data-event="staffing"' in t
    assert 'title="Staffing: 1 👍 · 0 👎 de 1"' in t  # el total SÍ cuenta la reacción.
    assert 'General' not in staffing_panel and 'Otro' not in staffing_panel


def test_historical_backfilled_reaction_resolves_subject_when_module_is_present(monkeypatch, tmp_path):
    # K: un evento con la MISMA forma que backfill_copilot_reactions.py
    # produce hoy (solo `module`, sin campos enriquecidos) SÍ resuelve
    # subject para module.feedback -- porque ese tipo nunca necesitó más
    # que `module`, que el backfill YA copia.
    _seed_subject_rows(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    methodology_panel = _panel_html(t, "methodology")
    assert 'title="j@d.com"' in methodology_panel


def test_daily_trend_click_updates_subject_detail_to_the_selected_day(monkeypatch, tmp_path):
    # H: cambiar de día recalcula IZQUIERDA→DERECHA→detalle de objeto desde
    # la MISMA población filtrada -- nunca un segundo estado de filtro.
    _seed_subject_rows(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity?date_from=2026-05-05&date_to=2026-05-05").text
    methodology_panel = _panel_html(t, "methodology")
    assert 'title="i@d.com"' in methodology_panel
    # el día 5 tiene UNA sola reacción (👍 CP01) -- el usuario ya no debería
    # mostrar la reacción negativa de CP02 (esa fue el día 1).
    assert '👍 1</span><span title="No le gustó">👎 0' in methodology_panel


def test_changing_selected_area_never_leaves_stale_subject_rows(monkeypatch, tmp_path):
    # I: el panel default ("Sin selección") nunca arrastra subjects de una
    # sección previamente elegida -- MISMO mecanismo JS que ya aísla
    # data-breakdown="" (initEventBreakdown, sin cambios), solo confirmamos
    # que el panel default sigue sin contenido de ningún área concreta.
    _seed_subject_rows(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    default_panel = t.split('data-breakdown=""')[1].split('data-breakdown="')[0]
    assert 'reaction-subjects' not in default_panel


# ── ANONYMOUS REACTION DETAIL (owner ticket) ────────────────────────────────
# Reacciones module/section/catalog/academy NO exigen sesión (POST anónimo ->
# 200) -- una reacción sin `user` sigue siendo real y hasta este corrective
# contaba en el total de área (izquierda) pero desaparecía del desglose por
# usuario (derecha, "Sin desglose"). MISMO shape crudo que _SUBJECT_ROWS.
_ANON_ROWS = [
    # 1+4: anónimo, methodology, CP05 positive -- resuelve subject (module.feedback
    # siempre resuelve con solo `module`, MISMO criterio que K en _SUBJECT_ROWS).
    {"ts_utc": "2026-06-01T10:00:00+00:00", "event": "module.feedback", "user": "", "name": "",
     "scope": "copilot", "area": "methodology", "module": "CP05",
     "payload": {"feedback_sentiment": "positive"}},
    # 2: SEGUNDA reacción anónima, MISMO día, área, sentimiento negativo, OTRO
    # módulo -- prueba reconciliación 👍/👎 del bucket anónimo (item 3) y una
    # segunda fila de objeto dentro del mismo desglose anónimo.
    {"ts_utc": "2026-06-01T10:01:00+00:00", "event": "module.feedback", "user": "", "name": "",
     "scope": "copilot", "area": "methodology", "module": "CP07",
     "payload": {"feedback_sentiment": "negative"}},
    # 6: usuario AUTENTICADO, MISMA área y día -- control de regresión: su
    # fila debe seguir con su email real, sin verse afectada por el anónimo.
    {"ts_utc": "2026-06-01T10:02:00+00:00", "event": "module.feedback", "user": "k@d.com", "name": "K",
     "scope": "copilot", "area": "methodology", "module": "CP06",
     "payload": {"feedback_sentiment": "negative"}},
    # 5: anónimo en Staffing -- staffing.feedback NUNCA resuelve subject
    # (ver test_unattributable_reaction_stays_in_total_without_fake_subject);
    # la fila anónima debe seguir apareciendo, SIN expandir, SIN fabricar nada.
    {"ts_utc": "2026-06-01T10:03:00+00:00", "event": "staffing.feedback", "user": "", "name": "",
     "scope": "operations", "area": "staffing", "module": "OP01",
     "payload": {"feedback_sentiment": "positive"}},
    # 7: anónimo en OTRO día -- prueba que el filtro de fecha sigue aplicando
    # igual al bucket anónimo que al resto.
    {"ts_utc": "2026-06-05T09:00:00+00:00", "event": "module.feedback", "user": "", "name": "",
     "scope": "copilot", "area": "methodology", "module": "CP08",
     "payload": {"feedback_sentiment": "positive"}},
]


def _seed_anon_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    empty_feedback_dir = tmp_path / "_no_feedback_yaml_anon"
    empty_feedback_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(activity_analytics, "FEEDBACK_LOG_DIR", empty_feedback_dir)
    day1 = [r for r in _ANON_ROWS if r["ts_utc"].startswith("2026-06-01")]
    day2 = [r for r in _ANON_ROWS if r["ts_utc"].startswith("2026-06-05")]
    (tmp_path / "activity_20260601.jsonl").write_text("\n".join(json.dumps(r) for r in day1) + "\n", encoding="utf-8")
    (tmp_path / "activity_20260605.jsonl").write_text("\n".join(json.dumps(r) for r in day2) + "\n", encoding="utf-8")


def test_anonymous_reaction_still_increments_area_total(monkeypatch, tmp_path):
    # 1: el total de IZQUIERDA (área) sigue contando la reacción anónima --
    # sin cambios de comportamiento respecto de antes de este corrective.
    _seed_anon_rows(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    # 4 reacciones en total en methodology (CP05+ · CP07- · CP06- día 1, CP08+ día 5).
    assert 'title="Metodología: 2 👍 · 2 👎 de 4"' in t


def test_anonymous_row_appears_on_the_right_panel(monkeypatch, tmp_path):
    # 2: la fila anónima ya no desaparece del desglose derecho -- aparece
    # bajo la etiqueta "Sin usuario identificado", NUNCA con un sentinel
    # crudo visible ni un tooltip vacío.
    _seed_anon_rows(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    methodology_panel = _panel_html(t, "methodology")
    assert "Sin usuario identificado" in methodology_panel
    assert "\x00anonymous" not in t  # el sentinel interno NUNCA llega al HTML.


def test_anonymous_row_up_down_totals_reconcile(monkeypatch, tmp_path):
    # 3: la fila anónima agregada (CP05 positive + CP07 negative día 1, CP08
    # positive día 5) reconcilia 👍2 👎1 -- MISMA suma que sus 3 objetos
    # (CP05 👍1, CP07 👎1, CP08 👍1) -- MISMO criterio que F en _SUBJECT_ROWS.
    _seed_anon_rows(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    methodology_panel = _panel_html(t, "methodology")
    anon_block = methodology_panel.split("Sin usuario identificado</span>", 1)[1]
    assert '👍 2</span><span title="No le gustó">👎 1' in anon_block


def test_anonymous_row_expands_with_subject_when_resolvable(monkeypatch, tmp_path):
    # 4: module.feedback SIEMPRE resuelve subject con solo `module` -- el
    # bucket anónimo debe quedar expandible con DOS filas de objeto (CP05, CP07).
    _seed_anon_rows(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    methodology_panel = _panel_html(t, "methodology")
    assert '<details class="assistant-story">' in methodology_panel
    anon_block = methodology_panel.split("Sin usuario identificado</span>", 1)[1]
    assert '<div class="reaction-subjects">' in anon_block.split("</details>", 1)[0]


def test_anonymous_row_without_resolvable_subject_is_not_fabricated(monkeypatch, tmp_path):
    # 5: staffing.feedback NUNCA resuelve subject -- el bucket anónimo de
    # Staffing debe seguir apareciendo (cuenta en el total) pero SIN expandir
    # y SIN "General"/"Otro" fabricado, MISMO criterio que G en _SUBJECT_ROWS.
    _seed_anon_rows(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    staffing_panel = _panel_html(t, "staffing")
    assert "Sin usuario identificado" in staffing_panel
    assert 'General' not in staffing_panel and 'Otro' not in staffing_panel
    anon_block = staffing_panel.split("Sin usuario identificado</span>", 1)[1]
    # línea plana (activity-bar-row), NUNCA envuelta en <details> expandible.
    assert not anon_block.lstrip().startswith("</summary>")
    assert 'reaction-subjects' not in staffing_panel


def test_authenticated_user_reaction_is_unaffected_by_anonymous_bucket(monkeypatch, tmp_path):
    # 6: el usuario autenticado en la MISMA área/día sigue con su propia fila
    # (email real en el tooltip, su propio subject CP06) -- coexiste con el
    # bucket anónimo sin fusionarse ni perder datos.
    _seed_anon_rows(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    methodology_panel = _panel_html(t, "methodology")
    assert 'title="k@d.com"' in methodology_panel
    auth_block = methodology_panel.split('title="k@d.com"', 1)[1].split("</details>", 1)[0]
    assert '👍 0</span><span title="No le gustó">👎 1' in auth_block


def test_anonymous_bucket_respects_date_and_area_filtering(monkeypatch, tmp_path):
    # 7: filtrar por el día 5 (solo la reacción anónima de CP08) no debe
    # arrastrar las reacciones anónimas del día 1 -- MISMO mecanismo de
    # filtro que ya prueba H en _SUBJECT_ROWS, ahora sobre el bucket anónimo.
    _seed_anon_rows(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity?date_from=2026-06-05&date_to=2026-06-05").text
    assert 'title="Metodología: 1 👍 · 0 👎 de 1"' in t
    methodology_panel = _panel_html(t, "methodology")
    anon_block = methodology_panel.split("Sin usuario identificado</span>", 1)[1]
    assert '👍 1</span><span title="No le gustó">👎 0' in anon_block
