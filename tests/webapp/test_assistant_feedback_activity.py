"""
test_assistant_feedback_activity.py — USAGE METRICS REACCIONES: Assistant
response forward telemetry (owner ticket "REACCIONES — COMPLETE COPILOT
COVERAGE + HISTORY" §3). MISMO patrón de test que
test_feedback_activity_reactions.py — determinístico, sin LLM: la conversación/
reply se siembra directo en el `assistant_store` compartido del router (MISMA
instancia que `webapp.routers.assistant` usa, ver webapp/copilot_runtime.py),
nunca vía el flujo de chat real.

Preserva "Historia de preguntas" sin cambios: `AssistantFeedbackRepository.
save` (FEEDBACK_LOG_DIR, type=assistant_response) sigue siendo la ÚNICA
escritura de esa capacidad — esto solo prueba la señal NUEVA y adicional
hacia Activity Log."""
from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.copilot_runtime import assistant_store
from webapp.main import app
from webapp.routers import assistant as assistant_router

client = TestClient(app)


def _seed_reply(module="01_Arquitectura_y_Conectividad", pack="datasphere_core"):
    conversation = assistant_store.start(module, pack)
    reply = assistant_store.append(
        conversation.id, module, pack, "¿Cómo creo una vista CDS?", "Con CREATE VIEW...", "<p>Con CREATE VIEW...</p>",
        topic="assistant_chat", action="chat",
    )
    return reply


def test_assistant_feedback_up_emits_activity_reaction(monkeypatch, tmp_path):
    monkeypatch.setattr(assistant_router.assistant_feedback_repository, "root", tmp_path)
    reply = _seed_reply()
    events = []
    monkeypatch.setattr(assistant_router, "log_activity", lambda event, **kw: events.append((event, kw)))
    r = client.post(f"/assistant/responses/{reply.id}/feedback", data={"rating": "up"})
    assert r.status_code == 200
    assert events and events[0][0] == "assistant.feedback"
    kw = events[0][1]
    assert kw["scope"] == "copilot" and kw["area"] == "assistant"
    assert kw["module"] == "01_Arquitectura_y_Conectividad"
    assert kw["reply_id"] == reply.id
    assert kw["conversation_id"] == reply.conversation_id
    assert kw["feedback_sentiment"] == "positive"


def test_assistant_feedback_down_is_negative_sentiment_and_requires_comment(monkeypatch, tmp_path):
    monkeypatch.setattr(assistant_router.assistant_feedback_repository, "root", tmp_path)
    reply = _seed_reply()
    events = []
    monkeypatch.setattr(assistant_router, "log_activity", lambda event, **kw: events.append((event, kw)))
    r = client.post(
        f"/assistant/responses/{reply.id}/feedback/submit", data={"comment": "Faltó un ejemplo"},
    )
    assert r.status_code == 200
    assert events[0][1]["feedback_sentiment"] == "negative"


def test_assistant_activity_emission_never_breaks_the_feedback_write_or_response(monkeypatch, tmp_path):
    monkeypatch.setattr(assistant_router.assistant_feedback_repository, "root", tmp_path)
    reply = _seed_reply()

    def _boom(event, **kw):
        raise RuntimeError("activity sink down")

    monkeypatch.setattr(assistant_router, "log_activity", _boom)
    r = client.post(f"/assistant/responses/{reply.id}/feedback", data={"rating": "up"})
    assert r.status_code == 200
    saved = list(tmp_path.glob("feedback_*.yaml"))
    assert len(saved) == 1  # el YAML de Historia de preguntas se guardó igual.


def test_assistant_feedback_is_classified_usage_for_metrics():
    from webapp.services import activity_context
    assert activity_context.event_class("assistant.feedback") == activity_context.EVENT_CLASS_USAGE
