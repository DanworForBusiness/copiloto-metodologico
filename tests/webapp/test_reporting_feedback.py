"""
test_reporting_feedback.py — Feedback + Activity de Reporting (ticket CLOSE
REPORTING §A). Prueba que Reporting REUSA las capacidades compartidas: el store
de feedback (`feedback_store` → FEEDBACK_LOG_DIR, mismo `type`/formato que
Copilot) y el pipeline de Activity (`log_activity`/`log_verbose`, scope
operations/area reporting fijados server-side). Determinístico, sin Graph.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import reporting as reporting_router

_client = TestClient(app)
_USER = {"email": "rep-fb@danwor.com", "name": "Rep FB"}

_REPORTING_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _force_operations_auth(monkeypatch):
    # Auth por monkeypatch (NO mutar el estado global auth_service.*, que otros
    # archivos de test pisan según orden de import) — sesión Operations válida
    # por defecto; el test anónimo la sobrescribe a None.
    monkeypatch.setattr(reporting_router.auth_service, "session_user", lambda request: dict(_USER))
    monkeypatch.setattr(reporting_router.operations_access, "is_operations_authorized", lambda request: True)


# ── Endpoint de feedback: store compartido + evento de Activity ─────────────

def test_feedback_requires_operations_session(monkeypatch):
    monkeypatch.setattr(reporting_router.auth_service, "session_user", lambda request: None)
    r = _client.post("/operaciones/reporting/integral/feedback",
                     data={"report_tab": "cartera", "rating": "up"}, follow_redirects=False)
    assert r.status_code == 303


def test_feedback_thumbs_up_persists_shared_record_and_activity(monkeypatch):
    saved = {}
    events = []
    monkeypatch.setattr(reporting_router.feedback_store, "write_feedback_record",
                        lambda payload, *, prefix, stamp: saved.update({"payload": payload, "prefix": prefix}))
    monkeypatch.setattr(reporting_router, "log_activity",
                        lambda event, **kw: events.append((event, kw)))
    r = _client.post("/operaciones/reporting/integral/feedback",
                     data={"report_tab": "rentabilidad", "rating": "up"})
    assert r.status_code == 200 and r.json() == {"state": "ok"}
    # Store COMPARTIDO: mismo contrato en disco que Copilot (campo `type`).
    assert saved["payload"]["type"] == "reporting_report"
    assert saved["payload"]["report_tab"] == "rentabilidad"
    assert saved["payload"]["rating"] == "up"
    assert saved["prefix"] == "reporting_feedback"
    # Evento del MISMO pipeline de Activity, scope/area fijados server-side.
    assert events and events[0][0] == "reporting.feedback"
    kw = events[0][1]
    assert kw["scope"] == "operations" and kw["area"] == "reporting"
    assert kw["report_tab"] == "rentabilidad" and kw["feedback_sentiment"] == "positive"


def test_feedback_thumbs_down_is_negative_sentiment(monkeypatch):
    events = []
    monkeypatch.setattr(reporting_router.feedback_store, "write_feedback_record",
                        lambda payload, *, prefix, stamp: None)
    monkeypatch.setattr(reporting_router, "log_activity", lambda event, **kw: events.append((event, kw)))
    r = _client.post("/operaciones/reporting/integral/feedback",
                     data={"report_tab": "cartera", "rating": "down"})
    assert r.json() == {"state": "ok"}
    assert events[0][1]["feedback_sentiment"] == "negative"


def test_feedback_rejects_invalid_rating_or_tab(monkeypatch):
    monkeypatch.setattr(reporting_router.feedback_store, "write_feedback_record",
                        lambda payload, *, prefix, stamp: None)
    monkeypatch.setattr(reporting_router, "log_activity", lambda event, **kw: None)
    assert _client.post("/operaciones/reporting/integral/feedback",
                        data={"report_tab": "cartera", "rating": "meh"}).status_code == 400
    assert _client.post("/operaciones/reporting/integral/feedback",
                        data={"report_tab": "inexistente", "rating": "up"}).status_code == 400


# ── Endpoint de telemetría: whitelist + gate VERBOSE + contexto server-side ─

def test_telemetry_logs_whitelisted_event_with_operations_context(monkeypatch):
    events = []
    monkeypatch.setattr(reporting_router, "is_verbose", lambda: True)
    monkeypatch.setattr(reporting_router, "log_verbose", lambda event, **kw: events.append((event, kw)))
    r = _client.post("/operaciones/reporting/integral/telemetry",
                     json={"event": "reporting.tab_view", "report_tab": "horasvendidas",
                           "scope": "copilot"})  # scope inyectado por el cliente: debe IGNORARSE
    assert r.status_code == 204
    assert events and events[0][0] == "reporting.tab_view"
    kw = events[0][1]
    assert kw["scope"] == "operations" and kw["area"] == "reporting"  # fijado server-side
    assert kw["report_tab"] == "horasvendidas"


def test_telemetry_ignores_non_whitelisted_event(monkeypatch):
    events = []
    monkeypatch.setattr(reporting_router, "is_verbose", lambda: True)
    monkeypatch.setattr(reporting_router, "log_verbose", lambda event, **kw: events.append(event))
    r = _client.post("/operaciones/reporting/integral/telemetry",
                     json={"event": "reporting.evil_click"})
    assert r.status_code == 204 and events == []


def test_telemetry_is_noop_when_not_verbose(monkeypatch):
    events = []
    monkeypatch.setattr(reporting_router, "is_verbose", lambda: False)
    monkeypatch.setattr(reporting_router, "log_verbose", lambda event, **kw: events.append(event))
    r = _client.post("/operaciones/reporting/integral/telemetry",
                     json={"event": "reporting.tab_view"})
    assert r.status_code == 204 and events == []


# ── Seam de cliente (reporting.js) ─────────────────────────────────────────

def test_feedback_is_the_last_element_of_a_ready_tab():
    # UN control por MAIN TAB, SIEMPRE el último elemento (después del renderer,
    # que para Horas Productivas ya incluye la nota Punch).
    # FEEDBACK VISIBILITY corrective (owner decision 2026-08-19): "solo en
    # READY" ya no significa "el tab se hidrató alguna vez"
    # (`reportingTabHydrated`, una sola vía) — significa el status PRESENTE
    # de la perspectiva/contenido activo (`isActiveReportReady`, reusa
    # `reportingTabRawStatus`). Ver test_reporting_feedback_visibility.py
    # para la cobertura de runtime real (bootstrap/switch/revisit/error).
    render = _REPORTING_JS[_REPORTING_JS.index("function render()"):_REPORTING_JS.index("function render()") + 2200]
    assert "RENDERERS[expId]() + feedbackHtml" in render
    assert "isActiveReportReady(expId) ? reportingFeedbackHtml(expId) : \"\"" in render


def test_feedback_widget_copy_and_one_per_tab():
    fn = _REPORTING_JS[_REPORTING_JS.index("function reportingFeedbackHtml"):_REPORTING_JS.index("async function submitReportingFeedback")]
    assert "¿Qué te pareció este reporte?" in fn
    assert "data-report-tab" in fn  # keyeado por MAIN TAB (expId), no por perspectiva
    submit = _REPORTING_JS[_REPORTING_JS.index("async function submitReportingFeedback"):_REPORTING_JS.index("async function submitReportingFeedback") + 700]
    assert '"/operaciones/reporting/integral/feedback"' in submit


def test_semantic_activity_events_are_wired_client_side():
    # Acciones semánticas (no "cada click del DOM") emiten al pipeline via beacon.
    assert 'reportingTelemetry("reporting.tab_view"' in _REPORTING_JS
    assert 'reportingTelemetry("reporting.perspective_change"' in _REPORTING_JS
    assert 'reportingTelemetry("reporting.filter_change"' in _REPORTING_JS
    assert 'reportingTelemetry("reporting.filter_clear"' in _REPORTING_JS
    assert 'reportingTelemetry("reporting.cold_year_load"' in _REPORTING_JS
    # El beacon pega al endpoint scopeado de Reporting (no al de Copilot).
    beacon = _REPORTING_JS[_REPORTING_JS.index("function reportingTelemetry"):_REPORTING_JS.index("function reportingTelemetry") + 600]
    assert '"/operaciones/reporting/integral/telemetry"' in beacon


# ── 👎 abre formulario de comentario (paridad transversal con Copilot) ──────

def test_thumbs_down_opens_optional_comment_form_not_instant_thanks():
    fn = _REPORTING_JS[_REPORTING_JS.index("function reportingFeedbackHtml"):_REPORTING_JS.index("function openReportingFeedbackForm")]
    # El 👎 despliega el formulario (openReportingFeedbackForm), NUNCA persiste
    # de una ni muestra "Gracias" seco. Reusa las clases del form de Copilot.
    assert "openReportingFeedbackForm(" in fn
    assert 'st === "form"' in fn
    assert "section-feedback__form" in fn and "section-feedback__send" in fn and "section-feedback__ghost" in fn
    assert "¿Qué esperabas o qué faltó acá?" in fn


def test_feedback_comment_flows_to_endpoint():
    fn = _REPORTING_JS[_REPORTING_JS.index("async function submitReportingFeedback"):_REPORTING_JS.index("async function submitReportingFeedback") + 900]
    assert 'document.getElementById("rfc-" + expId)' in fn  # lee el textarea
    assert "body.comment" in fn                              # y lo manda al endpoint


def test_reporting_events_classify_as_usage_for_metrics():
    # Sin esto caían en `unknown` y el dashboard de uso (event_class == usage)
    # los descartaba (owner: el 👍 decía "Gracias" pero no aparecía en métricas).
    from webapp.services.activity_context import EVENT_CLASS_USAGE, event_class
    for ev in ("reporting.feedback", "reporting.tab_view", "reporting.perspective_change",
               "reporting.filter_change", "reporting.filter_clear", "reporting.cold_year_load"):
        assert event_class(ev) == EVENT_CLASS_USAGE, ev
