"""
test_academy_feedback_activity.py — USAGE METRICS REACCIONES: Academy forward
telemetry (owner ticket "REACCIONES — COMPLETE COPILOT COVERAGE + HISTORY"
§2). MISMO patrón de test que test_feedback_activity_reactions.py
(`monkeypatch.setattr(router, "log_activity", ...)`) — determinístico, sin
depender de que la agregación de activity_analytics vuelva a recorrer el
evento (esa parte ya está cubierta por test_activity_routes.py).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import academy as academy_router

client = TestClient(app)

_STEM = "ACAD01_Paradigmas"  # academy real del content pack default, ver test_routes.py.


def test_academy_feedback_up_emits_activity_reaction(monkeypatch, tmp_path):
    monkeypatch.setattr(academy_router, "FEEDBACK_LOG_DIR", tmp_path)
    events = []
    monkeypatch.setattr(academy_router, "log_activity", lambda event, **kw: events.append((event, kw)))
    r = client.post(f"/academy/{_STEM}/feedback", data={"rating": "up", "pack": "datasphere_core"})
    assert r.status_code == 200
    assert events and events[0][0] == "academy.feedback"
    kw = events[0][1]
    assert kw["scope"] == "copilot" and kw["area"] == "academy"
    assert kw["module"] == _STEM
    assert kw["feedback_sentiment"] == "positive"
    # D (owner ticket "REACCIONES — PER-USER REACTED-OBJECT BREAKDOWN"): el
    # título humano de la página viaja también al evento de Activity.
    assert kw["academy_title"]


def test_academy_feedback_down_is_negative_sentiment(monkeypatch, tmp_path):
    monkeypatch.setattr(academy_router, "FEEDBACK_LOG_DIR", tmp_path)
    events = []
    monkeypatch.setattr(academy_router, "log_activity", lambda event, **kw: events.append((event, kw)))
    r = client.post(
        f"/academy/{_STEM}/feedback/submit",
        data={"rating": "down", "comment": "Faltó un ejemplo", "pack": "datasphere_core"},
    )
    assert r.status_code == 200
    assert events[0][1]["feedback_sentiment"] == "negative"


def test_academy_activity_emission_never_breaks_the_feedback_write_or_response(monkeypatch, tmp_path):
    # Best-effort, fuera del try/except que protege la escritura del YAML —
    # MISMO criterio que webapp/routers/feedback.py.
    monkeypatch.setattr(academy_router, "FEEDBACK_LOG_DIR", tmp_path)

    def _boom(event, **kw):
        raise RuntimeError("activity sink down")

    monkeypatch.setattr(academy_router, "log_activity", _boom)
    r = client.post(f"/academy/{_STEM}/feedback", data={"rating": "up", "pack": "datasphere_core"})
    assert r.status_code == 200
    saved = list(tmp_path.glob("academy_feedback_*.yaml"))
    assert len(saved) == 1  # el YAML se guardó igual, pese a que log_activity reventó.


def test_academy_feedback_is_classified_usage_for_metrics():
    from webapp.services import activity_context
    assert activity_context.event_class("academy.feedback") == activity_context.EVENT_CLASS_USAGE
