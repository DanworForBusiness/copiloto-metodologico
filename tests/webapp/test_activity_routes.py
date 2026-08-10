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

from fastapi.testclient import TestClient

from webapp.main import app
from webapp import access
from webapp.services import auth_service
from webapp.services import activity_analytics

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
    # operations queda faceteado fuera (su única área, staffing, no está en la
    # selección) — no se ofrece como opción.
    assert 'name="scope" value="operations"' not in t


# ── item 18: selección inválida → fallback determinístico (todo) ──────────────

def test_invalid_scope_falls_back_to_all(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    t = _hx(_owner_client(monkeypatch), "/admin/activity?scope=zzz-inexistente").text
    # No se fabrica la opción inexistente…
    assert "zzz-inexistente" not in t
    # …y se cae a todos los ámbitos presentes, tildados.
    for scope in ("platform", "copilot", "operations"):
        assert f'name="scope" value="{scope}" checked' in t


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
