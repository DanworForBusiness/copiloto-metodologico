"""
test_hub_metrics_access.py — HUB METRICS ACCESS CONTROL (owner ticket "HUB
METRICS — CENTRALIZE ACCESS AND ENABLE BY VERTICAL").

Usage Metrics pasa de ser APP_OWNER-only a ser una capacidad de HUB,
expuesta selectivamente por vertical vía `HUB_METRICS_ENABLED_VERTICALS`, y
autorizada de forma independiente vía `HUB_METRICS_ALLOWED_USERS` — poder
entrar a un vertical NUNCA implica poder ver sus métricas.

Prueba en dos capas: (1) el predicado puro `hub_metrics_access.
can_access_hub_metrics` en aislamiento total (sin HTTP), y (2) el contrato
HTTP end-to-end (`/admin/activity` desde Copiloto, `/operaciones/metricas`
+ el link de nav desde Operations) — MISMO patrón que
`test_operations_access.py` (capa pura) + `test_activity_routes.py`
(`_owner_client`/`_hx`, reusados acá tal cual, nunca un segundo fixture)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from webapp import access, hub_metrics_access
from webapp.main import app
from webapp.services import auth_service
from tests.webapp.test_activity_routes import _owner_client, _hx


def _patch_hub_metrics_allowed(monkeypatch, value):
    # can_access_hub_metrics cruza DOS módulos (auth_service.is_hub_metrics_user
    # para la pertenencia, hub_metrics_access._hub_metrics_configured para el
    # fallback) -- cada uno importó HUB_METRICS_ALLOWED_USERS como su propio
    # global de módulo (MISMO patrón que OPERATIONS_ALLOWED_USERS), así que
    # ambos deben parchearse para que el cambio surta efecto de punta a punta.
    monkeypatch.setattr(auth_service, "HUB_METRICS_ALLOWED_USERS", value, raising=False)
    monkeypatch.setattr(hub_metrics_access, "HUB_METRICS_ALLOWED_USERS", value, raising=False)


def _patch_enabled_verticals(monkeypatch, value):
    monkeypatch.setattr(hub_metrics_access, "HUB_METRICS_ENABLED_VERTICALS", value, raising=False)


# ── Predicado puro (sin HTTP) ───────────────────────────────────────────────

def test_a_pure_unset_allowlist_owner_is_allowed(monkeypatch):
    _patch_hub_metrics_allowed(monkeypatch, "")
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    assert hub_metrics_access.can_access_hub_metrics({"sub": "owner@danwor.com"}) is True


def test_b_pure_unset_allowlist_non_owner_is_denied(monkeypatch):
    _patch_hub_metrics_allowed(monkeypatch, "")
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    assert hub_metrics_access.can_access_hub_metrics({"sub": "someone-else@danwor.com"}) is False


def test_c_pure_configured_listed_user_is_allowed(monkeypatch):
    _patch_hub_metrics_allowed(monkeypatch, "metrics-user@danwor.com")
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    assert hub_metrics_access.can_access_hub_metrics({"sub": "metrics-user@danwor.com"}) is True


def test_d_pure_configured_owner_not_listed_is_denied(monkeypatch):
    # El corazón del contrato de migración (§3): una vez configurada la
    # lista explícita, NUNCA hay bypass oculto de APP_OWNER.
    _patch_hub_metrics_allowed(monkeypatch, "metrics-user@danwor.com")
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    assert hub_metrics_access.can_access_hub_metrics({"sub": "owner@danwor.com"}) is False


def test_pure_no_session_is_denied(monkeypatch):
    _patch_hub_metrics_allowed(monkeypatch, "")
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    assert hub_metrics_access.can_access_hub_metrics(None) is False


# ── A/B/C/D a nivel HTTP — /admin/activity (entrada Copiloto/admin) ────────

def test_a_http_unset_allowlist_owner_accesses_metrics(monkeypatch):
    _patch_hub_metrics_allowed(monkeypatch, "")
    r = _hx(_owner_client(monkeypatch), "/admin/activity")
    assert r.status_code == 200


def test_b_http_unset_allowlist_non_owner_denied(monkeypatch):
    _patch_hub_metrics_allowed(monkeypatch, "")
    r = _hx(_owner_client(monkeypatch, owner=False), "/admin/activity")
    assert r.status_code == 403


def test_c_http_configured_listed_user_allowed(monkeypatch):
    _patch_hub_metrics_allowed(monkeypatch, "listed@danwor.com")
    c = _owner_client(monkeypatch, email="listed@danwor.com", owner=False)
    r = _hx(c, "/admin/activity")
    assert r.status_code == 200


def test_d_http_configured_owner_not_listed_denied(monkeypatch):
    _patch_hub_metrics_allowed(monkeypatch, "someone-else@danwor.com")
    r = _hx(_owner_client(monkeypatch), "/admin/activity")  # owner=True default
    assert r.status_code == 403


# ── E-H — superficie de Operations ──────────────────────────────────────────

def _operations_client(monkeypatch, email, *, operations_allowed, metrics_allowed, enabled_verticals):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", operations_allowed)
    _patch_hub_metrics_allowed(monkeypatch, metrics_allowed)
    _patch_enabled_verticals(monkeypatch, enabled_verticals)
    session = auth_service.create_session(
        {"email": email, "name": "Ops User", "first_name": "Ops", "family_name": "User"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


def test_e_operations_user_metrics_listed_sees_entry_and_can_access(monkeypatch):
    c = _operations_client(
        monkeypatch, "ops-e@danwor.com",
        operations_allowed="ops-e@danwor.com", metrics_allowed="ops-e@danwor.com",
        enabled_verticals="operations",
    )
    staffing_page = c.get("/operaciones/staffing/asignaciones")
    assert staffing_page.status_code == 200
    assert 'href="/operaciones/metricas"' in staffing_page.text

    metrics_page = c.get("/operaciones/metricas")
    assert metrics_page.status_code == 200
    assert 'href="/operaciones/metricas"' in metrics_page.text  # nav sigue mostrando el link, activo


def test_f_operations_user_not_metrics_listed(monkeypatch):
    c = _operations_client(
        monkeypatch, "ops-f@danwor.com",
        operations_allowed="ops-f@danwor.com", metrics_allowed="someone-else@danwor.com",
        enabled_verticals="operations",
    )
    # Sigue usando Operations con total normalidad.
    staffing_page = c.get("/operaciones/staffing/asignaciones")
    assert staffing_page.status_code == 200
    assert 'href="/operaciones/metricas"' not in staffing_page.text  # sin el link

    # URL directa igual denegada, server-side, más allá de la navegación.
    metrics_page = c.get("/operaciones/metricas")
    assert metrics_page.status_code == 403


def test_g_metrics_listed_without_operations_access_does_not_gain_operations(monkeypatch):
    c = _operations_client(
        monkeypatch, "metrics-only-g@danwor.com",
        operations_allowed="",  # nadie autorizado a Operations
        metrics_allowed="metrics-only-g@danwor.com", enabled_verticals="operations",
    )
    staffing_page = c.get("/operaciones/staffing/asignaciones")
    assert staffing_page.status_code == 403  # Metrics NUNCA otorga Operations

    metrics_page = c.get("/operaciones/metricas")
    assert metrics_page.status_code == 403  # el gate de Operations corre primero


def test_h_operations_vertical_disabled_hides_entry_even_for_metrics_listed_user(monkeypatch):
    c = _operations_client(
        monkeypatch, "ops-h@danwor.com",
        operations_allowed="ops-h@danwor.com", metrics_allowed="ops-h@danwor.com",
        enabled_verticals="",  # "operations" AUSENTE de HUB_METRICS_ENABLED_VERTICALS
    )
    staffing_page = c.get("/operaciones/staffing/asignaciones")
    assert staffing_page.status_code == 200
    assert 'href="/operaciones/metricas"' not in staffing_page.text

    metrics_page = c.get("/operaciones/metricas")
    assert metrics_page.status_code == 403


# ── J — Comercial no se habilita ────────────────────────────────────────────

def test_j_commercial_not_enabled_by_default():
    assert hub_metrics_access.is_vertical_metrics_enabled("commercial") is False


def test_j_commercial_default_config_value_excludes_commercial():
    # HUB_METRICS_ENABLED_VERTICALS default real (config.py), sin monkeypatch
    # -- prueba el VALOR configurado en el repo, no solo la función pura.
    import config
    verticals = {v.strip().lower() for v in (config.HUB_METRICS_ENABLED_VERTICALS or "").split(",") if v.strip()}
    assert "operations" in verticals
    assert "commercial" not in verticals


def test_j_commercial_allowlist_membership_does_not_grant_metrics(monkeypatch):
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "commercial-user@danwor.com")
    _patch_hub_metrics_allowed(monkeypatch, "someone-else@danwor.com")
    assert auth_service.is_commercial_user("commercial-user@danwor.com") is True
    assert hub_metrics_access.can_access_hub_metrics({"sub": "commercial-user@danwor.com"}) is False
