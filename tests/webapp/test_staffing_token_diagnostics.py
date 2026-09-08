"""
test_staffing_token_diagnostics.py — STAFFING MSAL TOKEN FAILURE TELEMETRY
(owner ticket). Antes de este ticket, `if not graph_token:` en las 3 rutas
de Staffing (`.../data`, `.../historial`, `.../ausencias`) no logueaba NADA
— cero visibilidad de cuál de las 5 etapas de `hub_auth.js::dwGraphToken`
falló, para quién, o por qué (ver el forense Mac/MSAL previo). Este archivo
prueba: (A) el whitelist server-side (`_safe_token_diagnostic`) en
aislamiento puro, y (B) que las 3 rutas loguean el evento canónico nuevo
(`graph_token_acquisition_failed`) con SOLO los campos permitidos, sin
tocar el contrato de respuesta HTTP existente (`{"state": "missing_token"}`,
MISMO status/body de siempre).

MISMO patrón de test que test_staffing_absence_routes.py
(`_fake_log_verbose_recorder`, mockea `staffing_router.log_verbose` en el
boundary) — reusado acá, nunca un segundo mecanismo de captura."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import staffing as staffing_router
from webapp.services import activity_context, auth_service

auth_service.ALLOWED_USERS = "ALL"
# MISMO email/CSV que el resto de tests/webapp/test_staffing_*.py (ver
# comentario en test_staffing_routes.py) — evita pisarse entre archivos.
auth_service.OPERATIONS_ALLOWED_USERS = "staffing-test@danwor.com,topbar-full@danwor.com,onedanwor-full@danwor.com"
_SESSION = auth_service.create_session(
    {"email": "staffing-test@danwor.com", "name": "Staffing Test", "first_name": "Staffing", "family_name": "Test"}
)
client = TestClient(app)
client.cookies.set(auth_service.COOKIE_NAME, _SESSION)


# ── A: _safe_token_diagnostic en aislamiento (función pura, sin HTTP) ──────

def test_whitelist_passes_through_every_allowed_field_with_correct_type():
    raw = json.dumps({
        "stage": "acquire_silent_interaction_required",
        "msalErrorCode": "interaction_required",
        "msalErrorName": "InteractionRequiredAuthError",
        "browserFamily": "safari",
        "platform": "macos",
        "cookieEnabled": True,
        "sessionStorageAvailable": True,
        "localStorageAvailable": False,
        "isSecureContext": True,
        "hadAccount": True,
        "route": "/operaciones/staffing/asignaciones",
    })
    safe = staffing_router._safe_token_diagnostic(raw)
    assert safe["stage"] == "acquire_silent_interaction_required"
    assert safe["msalErrorCode"] == "interaction_required"
    assert safe["cookieEnabled"] is True
    assert safe["localStorageAvailable"] is False
    assert safe["route"] == "/operaciones/staffing/asignaciones"


def test_whitelist_drops_unknown_fields():
    raw = json.dumps({"stage": "no_account", "accessToken": "SHOULD-NEVER-SURVIVE", "extra": "junk"})
    safe = staffing_router._safe_token_diagnostic(raw)
    assert safe == {"stage": "no_account"}
    assert "accessToken" not in safe
    assert "extra" not in safe


def test_whitelist_drops_wrong_typed_values():
    raw = json.dumps({"stage": 123, "cookieEnabled": "yes", "hadAccount": 1})
    safe = staffing_router._safe_token_diagnostic(raw)
    # `stage` (int, no str), `cookieEnabled` (str, no bool) descartados. OJO:
    # `hadAccount: 1` -- `bool` es subclase de `int` en Python, pero
    # `isinstance(1, bool)` es False (1 no es instancia de bool), así que
    # también se descarta -- nunca cuela un 1/0 como si fuera True/False real.
    assert safe == {}


def test_whitelist_truncates_oversized_strings():
    raw = json.dumps({"stage": "x" * 500})
    safe = staffing_router._safe_token_diagnostic(raw)
    assert len(safe["stage"]) == 64


def test_whitelist_handles_invalid_json_and_non_dict_fail_soft():
    assert staffing_router._safe_token_diagnostic("") == {}
    assert staffing_router._safe_token_diagnostic("not json") == {}
    assert staffing_router._safe_token_diagnostic("[1, 2, 3]") == {}
    assert staffing_router._safe_token_diagnostic('"just a string"') == {}


def test_whitelist_drops_none_values():
    raw = json.dumps({"stage": "no_account", "msalErrorCode": None})
    safe = staffing_router._safe_token_diagnostic(raw)
    assert safe == {"stage": "no_account"}


# ── B: las 3 rutas loguean el evento canónico, sin cambiar la respuesta ────

def _fake_log_verbose_recorder(monkeypatch):
    calls = []

    def _fake_log_verbose(event_type, user=None, *, scope=None, area=None, module=None, **payload):
        calls.append((event_type, module, payload))

    monkeypatch.setattr(staffing_router, "log_verbose", _fake_log_verbose)
    return calls


_ROUTES = [
    ("/operaciones/staffing/asignaciones/data", "initial"),
    ("/operaciones/staffing/asignaciones/historial", "history"),
    ("/operaciones/staffing/asignaciones/ausencias", "absences"),
]


def test_missing_token_logs_the_canonical_event_with_operation_per_route(monkeypatch):
    for url, operation in _ROUTES:
        calls = _fake_log_verbose_recorder(monkeypatch)
        diagnostic = {"stage": "no_account", "hadAccount": False, "browserFamily": "chrome", "platform": "macos"}
        response = client.post(url, data={"graph_token": "", "token_diagnostic": json.dumps(diagnostic)})
        assert response.status_code == 200

        failures = [c for c in calls if c[0] == activity_context.EVENT_TOKEN_ACQUISITION_FAILED]
        assert len(failures) == 1, f"{url}: expected exactly one token-acquisition-failed event"
        _, module, payload = failures[0]
        assert module == "OP01"
        assert payload["operation"] == operation
        assert payload["stage"] == "no_account"
        assert payload["hadAccount"] is False
        assert payload["browserFamily"] == "chrome"
        assert payload["platform"] == "macos"


def test_missing_token_response_contract_is_unchanged(monkeypatch):
    _fake_log_verbose_recorder(monkeypatch)
    r1 = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": ""})
    assert r1.status_code == 200 and "token" in r1.text.lower()
    r2 = client.post("/operaciones/staffing/asignaciones/historial", data={"graph_token": ""})
    assert r2.status_code == 200 and r2.json() == {"state": "missing_token"}
    r3 = client.post("/operaciones/staffing/asignaciones/ausencias", data={"graph_token": ""})
    assert r3.status_code == 200 and r3.json() == {"state": "missing_token"}


def test_missing_token_without_diagnostic_field_still_logs_bare_event(monkeypatch):
    # Compatibilidad hacia atrás: un cliente viejo (sin el fix del lado
    # navegador) sigue mandando SOLO `graph_token=""`, sin `token_diagnostic`
    # -- el evento se loguea igual, solo sin los campos opcionales.
    calls = _fake_log_verbose_recorder(monkeypatch)
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": ""})
    assert response.status_code == 200
    failures = [c for c in calls if c[0] == activity_context.EVENT_TOKEN_ACQUISITION_FAILED]
    assert len(failures) == 1
    _, _, payload = failures[0]
    assert payload == {"operation": "initial"}


def test_forbidden_fields_never_reach_the_logged_payload(monkeypatch):
    # Un cliente comprometido/con bug manda campos prohibidos -- el whitelist
    # server-side los descarta ANTES de que log_verbose los reciba, nunca
    # confía en lo que hub_auth.js dice que captura.
    calls = _fake_log_verbose_recorder(monkeypatch)
    malicious = json.dumps({
        "stage": "no_account",
        "accessToken": "SHOULD-NEVER-APPEAR",
        "idToken": "SHOULD-NEVER-APPEAR",
        "refreshToken": "SHOULD-NEVER-APPEAR",
        "cookie": "dw_session=SHOULD-NEVER-APPEAR",
        "claims": {"email": "victim@danwor.com"},
    })
    response = client.post(
        "/operaciones/staffing/asignaciones/ausencias",
        data={"graph_token": "", "token_diagnostic": malicious},
    )
    assert response.status_code == 200
    failures = [c for c in calls if c[0] == activity_context.EVENT_TOKEN_ACQUISITION_FAILED]
    assert len(failures) == 1
    _, _, payload = failures[0]
    dumped = json.dumps(payload)
    for forbidden in ("SHOULD-NEVER-APPEAR", "accessToken", "idToken", "refreshToken", "cookie", "claims", "victim@danwor.com"):
        assert forbidden not in dumped
    assert payload == {"operation": "absences", "stage": "no_account"}


def test_successful_token_path_never_emits_the_failure_event(monkeypatch):
    # El camino exitoso (token presente) no debe emitir
    # `graph_token_acquisition_failed` bajo ninguna circunstancia -- solo
    # los eventos de siempre (data_load_diagnostics u otros), nunca uno de
    # más porque `token_diagnostic` viajó igual por error del cliente.
    calls = _fake_log_verbose_recorder(monkeypatch)

    class _EmptyOverlayService:
        def get_absence_overlay(self, *a, **kw):
            return {"absences": [], "unmatched_count": 0, "ambiguous_count": 0,
                     "absence_history_start": "2026-07-01", "absence_lookback_max_days": 58}
    monkeypatch.setattr(staffing_router, "StaffingService", lambda: _EmptyOverlayService())

    response = client.post(
        "/operaciones/staffing/asignaciones/ausencias",
        data={"graph_token": "tok", "token_diagnostic": json.dumps({"stage": "no_account"})},
    )
    assert response.status_code == 200
    failures = [c for c in calls if c[0] == activity_context.EVENT_TOKEN_ACQUISITION_FAILED]
    assert failures == []


def test_the_new_event_is_classified_diagnostic_not_usage():
    # MISMO criterio que EVENT_DATA_LOAD_FAILED/EVENT_CONFIGURATION_INCOMPLETE
    # (ver activity_context.py) -- nunca contamina el dashboard de adopción
    # de Usage Metrics.
    assert activity_context.event_class(activity_context.EVENT_TOKEN_ACQUISITION_FAILED) == \
        activity_context.EVENT_CLASS_DIAGNOSTIC
