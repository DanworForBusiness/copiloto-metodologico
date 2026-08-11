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


def test_deselecting_ambito_deselects_its_exclusive_area(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    # Destildar Operaciones (solo scope=copilot+platform quedan) → Área ya no
    # debe ofrecer "staffing" (exclusiva de Operaciones), ni tildada ni como opción.
    t = _hx(_owner_client(monkeypatch), "/admin/activity?scope=copilot&scope=platform").text
    assert 'name="area" value="staffing"' not in t


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
        # Área faceteada a staffing en ambos caminos.
        assert 'name="area" value="staffing" checked' in t
        assert 'name="area" value="methodology"' not in t


# ── item 21: sin métricas de negocio de Operations en el Activity Dashboard ───

def test_no_operations_business_metrics_invented(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity?scope=operations").text
    assert "staffing_overview_viewed" in t                           # sí: conteo de uso
    # no: KPIs de negocio de Staffing (eso es el dashboard de Staffing, no éste).
    for label in ("Consultores", "Horas activas", "utilization", "capacity", "Disponibilidad de asignación"):
        assert label not in t
