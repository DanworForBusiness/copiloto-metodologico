"""
test_commercial_feedback.py — Feedback + Activity de Comercial (Homologación
al Danwor Hub, ver HANDOFF). Prueba que Comercial REUSA las capacidades
compartidas: el store de feedback (`feedback_store` → FEEDBACK_LOG_DIR, mismo
`type`/formato que Copilot/Reporting/Staffing) y el pipeline de Activity
(`log_activity`, scope commercial/area crm fijados server-side). MISMO
patrón de test que test_reporting_feedback.py — determinístico, sin Odoo.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import commercial as commercial_router

_client = TestClient(app)
_USER = {"email": "crm-fb@danwor.com", "name": "CRM FB"}

_COMMERCIAL_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial.js").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _force_commercial_auth(monkeypatch):
    # Auth por monkeypatch (NO mutar el estado global auth_service.*, que otros
    # archivos de test pisan según orden de import) — sesión Comercial válida
    # por defecto; el test anónimo la sobrescribe a None.
    monkeypatch.setattr(commercial_router.auth_service, "session_user", lambda request: dict(_USER))
    monkeypatch.setattr(commercial_router.commercial_access, "is_commercial_authorized", lambda request: True)


# ── Endpoint de feedback: store compartido + evento de Activity ─────────────

def test_feedback_requires_commercial_session(monkeypatch):
    monkeypatch.setattr(commercial_router.auth_service, "session_user", lambda request: None)
    r = _client.post("/comercial/feedback",
                     data={"capability": "clientes", "rating": "up"}, follow_redirects=False)
    assert r.status_code == 303


def test_feedback_thumbs_up_persists_shared_record_and_activity(monkeypatch):
    saved = {}
    events = []
    monkeypatch.setattr(commercial_router.feedback_store, "write_feedback_record",
                        lambda payload, *, prefix, stamp: saved.update({"payload": payload, "prefix": prefix}))
    monkeypatch.setattr(commercial_router, "log_activity",
                        lambda event, **kw: events.append((event, kw)))
    r = _client.post("/comercial/feedback", data={"capability": "leads", "rating": "up"})
    assert r.status_code == 200 and r.json() == {"state": "ok"}
    # Store COMPARTIDO: mismo contrato en disco que Copilot/Reporting.
    assert saved["payload"]["type"] == "commercial_capability"
    assert saved["payload"]["capability"] == "leads"
    assert saved["payload"]["capability_title"] == "Leads"
    assert saved["payload"]["rating"] == "up"
    assert saved["prefix"] == "commercial_feedback"
    # Evento del MISMO pipeline de Activity, scope/area fijados server-side.
    assert events and events[0][0] == "commercial.feedback"
    kw = events[0][1]
    assert kw["scope"] == "commercial" and kw["area"] == "crm"
    assert kw["module"] == "CM01"
    assert kw["capability"] == "leads" and kw["feedback_sentiment"] == "positive"


def test_feedback_thumbs_down_is_negative_sentiment_and_persists_comment(monkeypatch):
    saved = {}
    events = []
    monkeypatch.setattr(commercial_router.feedback_store, "write_feedback_record",
                        lambda payload, *, prefix, stamp: saved.update({"payload": payload}))
    monkeypatch.setattr(commercial_router, "log_activity", lambda event, **kw: events.append((event, kw)))
    r = _client.post("/comercial/feedback",
                     data={"capability": "clientes", "rating": "down", "comment": "Faltó el filtro por Estado"})
    assert r.json() == {"state": "ok"}
    assert events[0][1]["feedback_sentiment"] == "negative"
    assert saved["payload"]["comment"] == "Faltó el filtro por Estado"


def test_feedback_comment_is_optional_on_thumbs_down(monkeypatch):
    saved = {}
    monkeypatch.setattr(commercial_router.feedback_store, "write_feedback_record",
                        lambda payload, *, prefix, stamp: saved.update({"payload": payload}))
    monkeypatch.setattr(commercial_router, "log_activity", lambda event, **kw: None)
    r = _client.post("/comercial/feedback", data={"capability": "dashboard", "rating": "down"})
    assert r.json() == {"state": "ok"}
    assert "comment" not in saved["payload"]


def test_feedback_rejects_invalid_rating_or_capability(monkeypatch):
    monkeypatch.setattr(commercial_router.feedback_store, "write_feedback_record",
                        lambda payload, *, prefix, stamp: None)
    monkeypatch.setattr(commercial_router, "log_activity", lambda event, **kw: None)
    assert _client.post("/comercial/feedback",
                        data={"capability": "clientes", "rating": "meh"}).status_code == 400
    assert _client.post("/comercial/feedback",
                        data={"capability": "prospectos", "rating": "up"}).status_code == 400
    # "prospectos"/"mis-tareas"/"ex-clientes" NUNCA son capabilities de nav
    # válidas para feedback — ver COMMERCIAL_CAPABILITIES en commercial.js.
    assert _client.post("/comercial/feedback",
                        data={"capability": "ex-clientes", "rating": "up"}).status_code == 400


def test_feedback_accepts_all_seven_nav_capabilities(monkeypatch):
    monkeypatch.setattr(commercial_router.feedback_store, "write_feedback_record",
                        lambda payload, *, prefix, stamp: None)
    monkeypatch.setattr(commercial_router, "log_activity", lambda event, **kw: None)
    for cap in ("dashboard", "actividades", "licencias", "historial-perdidas", "detalle", "clientes", "leads"):
        r = _client.post("/comercial/feedback", data={"capability": cap, "rating": "up"})
        assert r.status_code == 200, cap


def test_commercial_events_classify_as_usage_for_metrics():
    # Sin esto caería en `unknown` y el dashboard de uso (event_class == usage)
    # lo descartaría (mismo hallazgo que motivó reporting.feedback/staffing.feedback).
    from webapp.services.activity_context import EVENT_CLASS_USAGE, event_class
    assert event_class("commercial.feedback") == EVENT_CLASS_USAGE


# ── Seam de cliente (commercial.js) — un control por CAPABILITY, no por
#    sub-tab, última posición del panel ─────────────────────────────────────

def test_feedback_is_the_last_element_of_a_ready_capability():
    fn = _COMMERCIAL_JS[_COMMERCIAL_JS.index("function commercialCapabilityHtml"):
                        _COMMERCIAL_JS.index("function commercialCapabilityHtml") + 900]
    assert "commercialFeedbackReady(cap) ? commercialFeedbackHtml(cap) : \"\"" in fn
    assert "return body +" in fn


def test_feedback_widget_copy_and_shared_classes():
    fn = _COMMERCIAL_JS[_COMMERCIAL_JS.index("function commercialFeedbackHtml"):
                        _COMMERCIAL_JS.index("function commercialOpenFeedbackForm")]
    assert "¿Qué te pareció este reporte?" in fn
    assert "section-feedback" in fn and "section-feedback__bar" in fn
    assert "data-commercial-feedback" in fn  # keyeado por CAPABILITY


def test_feedback_posts_to_the_dedicated_commercial_endpoint():
    fn = _COMMERCIAL_JS[_COMMERCIAL_JS.index("async function commercialSubmitFeedback"):
                        _COMMERCIAL_JS.index("async function commercialSubmitFeedback") + 800]
    assert '"/comercial/feedback"' in fn


def test_thumbs_down_opens_optional_comment_form_not_instant_thanks():
    fn = _COMMERCIAL_JS[_COMMERCIAL_JS.index("function commercialFeedbackHtml"):
                        _COMMERCIAL_JS.index("function commercialOpenFeedbackForm")]
    assert "data-commercial-feedback-open" in fn
    assert 'st === "form"' in fn
    assert "section-feedback__form" in fn and "section-feedback__send" in fn and "section-feedback__ghost" in fn
    assert "¿Qué esperabas o qué faltó acá?" in fn


def test_feedback_comment_flows_to_endpoint():
    fn = _COMMERCIAL_JS[_COMMERCIAL_JS.index("async function commercialSubmitFeedback"):
                        _COMMERCIAL_JS.index("async function commercialSubmitFeedback") + 900]
    assert 'document.getElementById("cfc-" + cap)' in fn  # lee el textarea
    assert "body.comment" in fn                             # y lo manda al endpoint


def test_feedback_ready_covers_all_seven_capabilities_and_clientes_reads_active_subtab():
    fn = _COMMERCIAL_JS[_COMMERCIAL_JS.index("function commercialFeedbackReady"):
                        _COMMERCIAL_JS.index("function commercialFeedbackHtml")]
    for cap in ('"dashboard"', '"actividades"', '"licencias"', '"detalle"',
               '"historial-perdidas"', '"clientes"', '"leads"'):
        assert cap in fn, cap
    # Licencias/Detalle comparten el MISMO status subyacente — evidencia de
    # que comparten loader, no de que compartan feedback (siguen siendo dos
    # `case` distintos en el switch, cada uno con su propia identidad).
    assert fn.count("opportunitiesDetailStatus") == 1  # un solo return compartido por los dos case
    # Clientes lee el sub-tab activo (Ex Clientes vs Clientes), MISMO criterio
    # "perspectiva activa" que Reporting/Cartera — nunca una capability nueva.
    assert 'clientesSubTab === "ex-clientes"' in fn
    assert "exClientsStatus" in fn and "clientsStatus" in fn


def test_feedback_state_resets_on_tipo_change():
    # Un cambio de Todos/NN/BI invalida "este reporte" para TODAS las
    # capabilities — MISMO criterio que el resto del estado local por tab
    # que ya se resetea en commercialSetTipo (búsqueda/orden/expansión).
    fn = _COMMERCIAL_JS[_COMMERCIAL_JS.index("function commercialSetTipo"):
                        _COMMERCIAL_JS.index("function commercialSetTipo") + 5500]
    assert "commercialState.feedback = {};" in fn
