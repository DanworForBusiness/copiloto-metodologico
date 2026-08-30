"""
test_wizard_feedback_activity.py — USAGE METRICS REACCIONES: Tree/Wizard
outcome rating forward telemetry (owner ticket "REACCIONES — COMPLETE
COPILOT COVERAGE + HISTORY" §4). Forward-only a propósito — NUNCA reclama
cobertura histórica (ver backfill_copilot_reactions.py, que deliberadamente
excluye este productor).

Navega un árbol REAL hasta una hoja (MISMA secuencia que
tests/webapp/test_wizard_service.py::test_completed_tree_accepts_feedback_...)
usando el `store` COMPARTIDO del router (webapp.copilot_runtime.store) —
nunca una instancia propia, para probar el mismo objeto que
webapp.routers.wizard realmente usa."""
from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.copilot_runtime import store
from webapp.main import app

client = TestClient(app)


def _run_to_leaf():
    tree = store.trees_for_module("01_Arquitectura_y_Conectividad")[0]
    run = store.start(tree)
    store.answer(run.id, "Warehouse/Lakehouse")
    store.answer(run.id, "2-Tier")
    store.answer(run.id, "Sí")
    assert run.leaf is not None  # sanity: la secuencia realmente llega a una hoja.
    return run


def test_tree_feedback_up_emits_activity_reaction(monkeypatch):
    run = _run_to_leaf()
    events = []
    monkeypatch.setattr("webapp.routers.wizard.log_activity", lambda event, **kw: events.append((event, kw)))
    r = client.post(f"/wizards/{run.id}/feedback", data={"rating": "up"})
    assert r.status_code == 200
    assert events and events[0][0] == "tree.feedback"
    kw = events[0][1]
    assert kw["scope"] == "copilot" and kw["area"] == "methodology"
    assert kw["run_id"] == run.id
    assert kw["tree_id"]  # MISMO campo que tree_started/tree_step/tree_outcome ya usan.
    assert kw["feedback_sentiment"] == "positive"


def test_tree_feedback_down_requires_lesson_and_is_negative_sentiment(monkeypatch):
    run = _run_to_leaf()
    events = []
    monkeypatch.setattr("webapp.routers.wizard.log_activity", lambda event, **kw: events.append((event, kw)))
    r = client.post(
        f"/wizards/{run.id}/feedback/submit",
        data={"rating": "down", "lesson": "Faltó contemplar el caso X"},
    )
    assert r.status_code == 200
    assert events[0][1]["feedback_sentiment"] == "negative"


def test_tree_feedback_down_without_lesson_never_emits_activity(monkeypatch):
    # Rating rechazado (falta la lección obligatoria) -> re-muestra el form,
    # NUNCA llega a persistir feedback -- tampoco debe emitir Activity.
    run = _run_to_leaf()
    events = []
    monkeypatch.setattr("webapp.routers.wizard.log_activity", lambda event, **kw: events.append((event, kw)))
    r = client.post(f"/wizards/{run.id}/feedback/submit", data={"rating": "down"})
    assert r.status_code == 200
    assert events == []


def test_tree_activity_emission_never_breaks_the_case_library_write_or_response(monkeypatch):
    run = _run_to_leaf()

    def _boom(event, **kw):
        raise RuntimeError("activity sink down")

    monkeypatch.setattr("webapp.routers.wizard.log_activity", _boom)
    r = client.post(f"/wizards/{run.id}/feedback", data={"rating": "up"})
    assert r.status_code == 200
    assert "Gracias" in r.text or "gracias" in r.text.lower()  # el thanks-fragment se sirvió igual.


def test_tree_feedback_is_classified_usage_for_metrics():
    from webapp.services import activity_context
    assert activity_context.event_class("tree.feedback") == activity_context.EVENT_CLASS_USAGE
