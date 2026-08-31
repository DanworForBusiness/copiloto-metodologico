"""
test_hub_metrics_visibility.py — HUB METRICS VERTICAL DATA VISIBILITY (owner
ticket "HUB METRICS — ENFORCE VERTICAL DATA VISIBILITY").

`HUB_METRICS_ALLOWED_USERS` correctly gates WHETHER a user may open Usage
Metrics at all (ver test_hub_metrics_access.py) — pero, una vez adentro, el
usuario veía datos de TODAS las verticales, incluso las que no puede
acceder. Este archivo prueba la corrección: `metrics_visibility_for` (owner
§3, `webapp/hub_metrics_access.py`) reusa los MISMOS predicados de vertical
(`operations_access`/`commercial_access`) para recortar, ANTES de cualquier
agregación, el universo de eventos que `activity_analytics.build_summary`
puede ver — un único punto de aplicación (owner §8) del que Eventos,
Reacciones, Daily Trend, Usuario/Módulo, Historias y Recorridos derivan por
construcción.

Personas del ticket (owner §11):
- both (operations=True, commercial=True): ve las 3 verticales.
- operations_only (operations=True, commercial=False): NUNCA ve Comercial.
- commercial_only (futuro; operations=False, commercial=True): NUNCA ve
  Operations, aunque HUB_METRICS_ENABLED_VERTICALS no incluya "commercial"
  todavía (eso solo gatea la SUPERFICIE de navegación desde Comercial, nunca
  la autorización de datos en sí — ver hub_metrics_access.py).

MISMO patrón de test que test_activity_routes.py (`_hx`, `_panel_html`) y
test_hub_metrics_access.py (`_patch_hub_metrics_allowed`) — reusados acá,
nunca un segundo fixture."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from webapp import hub_metrics_access
from webapp.main import app
from webapp.services import activity_analytics, auth_service
from tests.webapp.test_activity_routes import _hx, _panel_html

# ── Dataset compartido: UNA reacción/evento de USO por vertical, MISMA
# fecha (§12: la prueba de seguridad clave) ──────────────────────────────
_MIXED_VERTICAL_ROWS = [
    # Operations · Staffing.
    {"ts_utc": "2026-08-01T10:00:00+00:00", "event": "module_viewed", "user": "ops-user@d.com", "name": "OpsUser",
     "scope": "operations", "area": "staffing", "module": "assignments", "payload": {}},
    {"ts_utc": "2026-08-01T10:01:00+00:00", "event": "staffing.feedback", "user": "ops-user@d.com", "name": "OpsUser",
     "scope": "operations", "area": "staffing", "module": "assignments",
     "payload": {"feedback_sentiment": "positive"}},
    # Commercial · CRM. Event NAME deliberadamente "module_viewed" en AMBAS
    # filas (nunca "commercial.feedback"): ese nombre de evento SOLO está
    # registrado como EVENT_CLASS_USAGE en el batch Comercial todavía
    # PENDIENTE/sin commitear (ver activity_context.py, dirty de otro
    # ticket) — usarlo acá haría que estas filas cuenten en el working tree
    # local (donde ese registro YA está, sin commitear) pero NO contra HEAD
    # committed (donde no existe todavía), rompiendo la validación
    # pristine-HEAD sin que el fixture de este archivo haya tocado ese
    # batch. `event_class` clasifica por NOMBRE de evento únicamente, nunca
    # por `scope` — "module_viewed" ya es usage HOY, sin depender de nada
    # pendiente, así que sirve para simular la reacción igual (Reacciones
    # solo mira `payload.feedback_sentiment`, nunca el nombre del evento).
    {"ts_utc": "2026-08-01T10:02:00+00:00", "event": "module_viewed", "user": "crm-user@d.com", "name": "CrmUser",
     "scope": "commercial", "area": "crm", "payload": {}},
    {"ts_utc": "2026-08-01T10:03:00+00:00", "event": "module_viewed", "user": "crm-user@d.com", "name": "CrmUser",
     "scope": "commercial", "area": "crm", "payload": {"feedback_sentiment": "negative"}},
    # Copiloto · Metodología.
    {"ts_utc": "2026-08-01T10:04:00+00:00", "event": "module_viewed", "user": "cop-user@d.com", "name": "CopUser",
     "scope": "copilot", "area": "methodology", "module": "CP01", "payload": {}},
]


def _seed(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    empty_feedback_dir = tmp_path / "_no_feedback_yaml"
    empty_feedback_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(activity_analytics, "FEEDBACK_LOG_DIR", empty_feedback_dir)
    (tmp_path / "activity_20260801.jsonl").write_text(
        "\n".join(json.dumps(r) for r in _MIXED_VERTICAL_ROWS) + "\n", encoding="utf-8",
    )


def _client(monkeypatch, email, *, operations=False, commercial=False, metrics=True):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", email if operations else "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", email if commercial else "")
    metrics_list = email if metrics else "someone-else@danwor.com"
    monkeypatch.setattr(auth_service, "HUB_METRICS_ALLOWED_USERS", metrics_list, raising=False)
    monkeypatch.setattr(hub_metrics_access, "HUB_METRICS_ALLOWED_USERS", metrics_list, raising=False)
    monkeypatch.setattr(hub_metrics_access, "HUB_METRICS_ENABLED_VERTICALS", "operations", raising=False)
    session = auth_service.create_session(
        {"email": email, "name": "Test User", "first_name": "Test", "family_name": "User"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


# ── A: operations_only puede abrir Metrics desde Operations ────────────────

def test_a_operations_only_can_open_metrics_from_operations(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    c = _client(monkeypatch, "ops-only@d.com", operations=True, commercial=False, metrics=True)
    r = c.get("/operaciones/metricas")
    assert r.status_code == 200


# ── B: checkboxes de Comercial/CRM destildados + deshabilitados ───────────

def test_b_operations_only_commercial_and_crm_checkboxes_disabled(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    c = _client(monkeypatch, "ops-only@d.com", operations=True, commercial=False, metrics=True)
    t = _hx(c, "/admin/activity").text
    assert 'name="scope" value="commercial"' in t  # sigue OFRECIDO como opción.
    assert 'name="scope" value="commercial"  disabled' in t
    assert 'name="scope" value="commercial" checked' not in t
    assert 'name="area" value="crm"' in t
    assert 'disabled' in t.split('name="area" value="crm"', 1)[1].split('>', 1)[0]
    assert 'name="area" value="crm" checked' not in t


# ── C: default (sin tocar nada) excluye Comercial por completo ────────────

def test_c_operations_only_default_summary_has_zero_commercial_events(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    c = _client(monkeypatch, "ops-only@d.com", operations=True, commercial=False, metrics=True)
    t = _hx(c, "/admin/activity").text
    # 3 eventos de uso visibles (2 Operations + 1 Copiloto) -- NUNCA los 5.
    assert "3 evento(s)" in t
    assert "crm-user@d.com" not in t
    assert "CrmUser" not in t
    # "Comercial" SOLO puede aparecer como la etiqueta del checkbox
    # deshabilitado -- nunca como área/dato real dentro de Reacciones o Eventos.
    assert 'name="scope" value="commercial"  disabled' in t
    assert 'data-breakdown="crm"' not in t
    assert 'data-event="crm"' not in t


# ── D: query forjado pidiendo commercial/crm sigue devolviendo cero ───────

def test_d_operations_only_forged_query_still_excludes_commercial(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    c = _client(monkeypatch, "ops-only@d.com", operations=True, commercial=False, metrics=True)
    t = _hx(c, "/admin/activity?scope=commercial&area=crm").text
    # El fallback "nada intersecta -> todo lo AUTORIZADO" entra en juego --
    # NUNCA se cuela Comercial ni aunque se pida explícito por query param.
    assert "crm-user@d.com" not in t
    assert "CrmUser" not in t
    assert "3 evento(s)" in t  # el universo autorizado completo, no cero total.


# ── E/F: sigue viendo Reporting/Staffing y Copiloto ────────────────────────

def test_e_operations_only_still_sees_staffing(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    c = _client(monkeypatch, "ops-only@d.com", operations=True, commercial=False, metrics=True)
    t = _hx(c, "/admin/activity").text
    assert "ops-user@d.com" in t
    assert 'name="area" value="staffing"' in t
    assert 'name="area" value="staffing" checked' in t


def test_f_operations_only_still_sees_copiloto(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    c = _client(monkeypatch, "ops-only@d.com", operations=True, commercial=False, metrics=True)
    t = _hx(c, "/admin/activity").text
    assert "cop-user@d.com" in t
    assert 'name="scope" value="copilot" checked' in t


# ── G: both ve Comercial + Operations + Copiloto ────────────────────────────

def test_g_both_user_sees_all_three_verticals(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    c = _client(monkeypatch, "both@d.com", operations=True, commercial=True, metrics=True)
    t = _hx(c, "/admin/activity").text
    assert "5 evento(s)" in t
    assert "ops-user@d.com" in t
    assert "crm-user@d.com" in t
    assert "cop-user@d.com" in t
    assert 'name="scope" value="commercial" checked' in t


# ── H: commercial_only (futuro) ve Comercial, NUNCA gana Operations ────────

def test_h_commercial_only_sees_commercial_but_not_operations(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    c = _client(monkeypatch, "crm-only@d.com", operations=False, commercial=True, metrics=True)
    # "operations" no listado en su allowlist -- llegar por /admin/activity
    # (el otro entry point posible cuando algún día exista uno para Comercial).
    t = _hx(c, "/admin/activity").text
    assert "crm-user@d.com" in t
    assert 'name="scope" value="commercial" checked' in t
    assert "ops-user@d.com" not in t
    assert 'name="scope" value="operations"  disabled' in t
    assert 'name="scope" value="operations" checked' not in t
    # 2 eventos de uso visibles (Comercial) + 1 Copiloto = 3, NUNCA los 2 de Operations.
    assert "3 evento(s)" in t


# ── I: la vertical no autorizada no se cuela por el dropdown de Usuario ────

def test_i_unauthorized_vertical_user_does_not_leak_through_dropdown(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    c = _client(monkeypatch, "ops-only@d.com", operations=True, commercial=False, metrics=True)
    t = _hx(c, "/admin/activity").text
    select_block = t.split('name="user"', 1)[1].split("</select>", 1)[0]
    assert "crm-user@d.com" not in select_block
    assert "ops-user@d.com" in select_block
    assert "cop-user@d.com" in select_block


# ── J: módulos/subjects no autorizados no se cuelan por filtros/detalle ────

def test_j_unauthorized_vertical_module_does_not_leak_through_filters(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    c = _client(monkeypatch, "ops-only@d.com", operations=True, commercial=False, metrics=True)
    t = _hx(c, "/admin/activity").text
    select_block = t.split('name="module"', 1)[1].split("</select>", 1)[0]
    assert "assignments" in select_block or "Asignaciones" in select_block
    # Comercial no tiene dimensión de módulo (MISMO criterio que catalog_view),
    # así que no hay módulo propio que pudiera colarse -- confirmamos que el
    # desglose de Reacciones tampoco expone su área.
    assert 'data-breakdown="crm"' not in t


# ── K: Daily Trend respeta la autorización de vertical ─────────────────────

def test_k_daily_trend_respects_vertical_authorization(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    c = _client(monkeypatch, "ops-only@d.com", operations=True, commercial=False, metrics=True)
    t = _hx(c, "/admin/activity").text
    assert "20260801: 3 evento(s)" in t  # NUNCA "5 evento(s)" ese día.


# ── L: Reacciones respeta la autorización de vertical ──────────────────────

def test_l_reacciones_respects_vertical_authorization(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    c = _client(monkeypatch, "ops-only@d.com", operations=True, commercial=False, metrics=True)
    t = _hx(c, "/admin/activity").text
    # La reacción de Staffing sigue contando -- Operations está autorizado.
    assert 'title="Staffing: 1 👍 · 0 👎 de 1"' in t
    # La reacción de Comercial (negative) NUNCA aparece -- ni como total de
    # área, ni como fila/desglose.
    assert 'title="Comercial: 0 👍 · 1 👎 de 1"' not in t
    assert 'data-breakdown="crm"' not in t


# ── M: los 4 endpoints /admin/activity* obedecen el MISMO contrato ────────

def test_m_admin_activity_trend_endpoint_obeys_visibility(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    c = _client(monkeypatch, "ops-only@d.com", operations=True, commercial=False, metrics=True)
    t = _hx(c, "/admin/activity/trend").text
    assert "20260801: 3 evento(s)" in t
    assert "20260801: 5 evento(s)" not in t


def test_m_admin_activity_stories_and_tree_runs_endpoints_obey_visibility(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    c = _client(monkeypatch, "ops-only@d.com", operations=True, commercial=False, metrics=True)
    for url in ("/admin/activity/stories", "/admin/activity/tree-runs"):
        r = _hx(c, url)
        assert r.status_code == 200
        assert "crm-user@d.com" not in r.text
        assert "CrmUser" not in r.text


# ── N: HUB_METRICS_ALLOWED_USERS (acceso a Metrics en sí) sin cambios ─────

def test_n_hub_metrics_allowed_users_authorization_unchanged(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    # Operations+Commercial autorizados, pero NO en HUB_METRICS_ALLOWED_USERS
    # -- sigue sin poder ENTRAR a Metrics, sin importar su universo de datos.
    c = _client(monkeypatch, "not-a-metrics-user@d.com", operations=True, commercial=True, metrics=False)
    r = _hx(c, "/admin/activity")
    assert r.status_code == 403


# ── §12: PRUEBA DE SEGURIDAD CLAVE ──────────────────────────────────────────

def test_security_regression_operations_only_total_excludes_commercial_even_when_forced(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    c = _client(monkeypatch, "ops-only@d.com", operations=True, commercial=False, metrics=True)

    # Default: total == Operations (2) + Copiloto (1) únicamente.
    default_text = _hx(c, "/admin/activity").text
    assert "3 evento(s)" in default_text
    assert "5 evento(s)" not in default_text

    # Pedido EXPLÍCITO de scope=commercial/area=crm -- el evento Comercial
    # sigue excluido; esta es la prueba real de autorización server-side.
    forged_text = _hx(c, "/admin/activity?scope=commercial&area=crm").text
    assert "crm-user@d.com" not in forged_text
    assert "CrmUser" not in forged_text
    assert 'data-breakdown="crm"' not in forged_text
    assert "5 evento(s)" not in forged_text
