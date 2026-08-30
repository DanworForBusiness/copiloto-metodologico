"""
test_feedback_activity_reactions.py — USAGE METRICS REACCIONES: Copiloto
(owner: "el copiloto tiene un sinnúmero de reacciones que debe ser tenido en
cuenta si el checkbox de copiloto esta encendido o sus derivados donde se
ofrezcan reacciones").

Copiloto's own feedback family (`webapp/routers/feedback.py` — módulo/
sección/catálogo/sección de catálogo) persistía a FEEDBACK_LOG_DIR pero
NUNCA emitía un evento de Activity, así que "Reacciones" (activity_analytics
.build_summary, señal `payload.feedback_sentiment`) estructuralmente no
podía verlo — a diferencia de reporting.feedback/staffing.feedback/
commercial.feedback, que sí lo hacían desde su origen. MISMO patrón de test
que test_commercial_feedback.py (`monkeypatch.setattr(feedback_router,
"log_activity", ...)`) — determinístico, sin depender de que la aggregación
de activity_analytics vuelva a recorrer el evento (esa parte ya está
cubierta por test_activity_routes.py, "USAGE METRICS REACTIONS DRILLDOWN").
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import feedback as feedback_router

client = TestClient(app)


def test_module_feedback_up_emits_activity_reaction(monkeypatch, tmp_path):
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)
    events = []
    monkeypatch.setattr(feedback_router, "log_activity", lambda event, **kw: events.append((event, kw)))
    r = client.post(
        "/modules/01_Arquitectura_y_Conectividad/feedback",
        data={"rating": "up", "pack": "datasphere_core"},
    )
    assert r.status_code == 200
    assert events and events[0][0] == "module.feedback"
    kw = events[0][1]
    assert kw["scope"] == "copilot" and kw["area"] == "methodology"
    assert kw["module"] == "CP01"  # module_id canónico, MISMO criterio que el YAML persistido.
    assert kw["feedback_sentiment"] == "positive"


def test_module_feedback_down_is_negative_sentiment(monkeypatch, tmp_path):
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)
    events = []
    monkeypatch.setattr(feedback_router, "log_activity", lambda event, **kw: events.append((event, kw)))
    r = client.post(
        "/modules/01_Arquitectura_y_Conectividad/feedback/submit",
        data={"rating": "down", "comment": "Faltó un ejemplo", "pack": "datasphere_core"},
    )
    assert r.status_code == 200
    assert events[0][1]["feedback_sentiment"] == "negative"


def test_section_feedback_emits_activity_reaction_same_area_as_module(monkeypatch, tmp_path):
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)
    events = []
    monkeypatch.setattr(feedback_router, "log_activity", lambda event, **kw: events.append((event, kw)))
    r = client.post(
        "/modules/01_Arquitectura_y_Conectividad/sections/espacios-transportes/feedback",
        data={"rating": "up", "pack": "datasphere_core"},
    )
    assert r.status_code == 200
    assert events and events[0][0] == "section.feedback"
    kw = events[0][1]
    # MISMA área que module.feedback -- el feedback de una sección vive en la
    # MISMA área que la navegación de módulos, no una área "sección" aparte.
    assert kw["scope"] == "copilot" and kw["area"] == "methodology"
    assert kw["module"] == "CP01"


def test_catalog_feedback_emits_activity_reaction_in_catalog_area(monkeypatch, tmp_path):
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)
    events = []
    monkeypatch.setattr(feedback_router, "log_activity", lambda event, **kw: events.append((event, kw)))
    r = client.post("/catalog/feedback", data={"rating": "up", "catalog_kind": "dominio", "catalog_ref": "ventas"})
    assert r.status_code == 200
    assert events and events[0][0] == "catalog.feedback"
    kw = events[0][1]
    assert kw["scope"] == "copilot" and kw["area"] == "catalog"
    assert "module" not in kw  # catálogo no tiene dimensión de módulo, MISMO criterio que catalog_view.
    assert kw["feedback_sentiment"] == "positive"


def test_catalog_section_feedback_emits_activity_reaction_in_catalog_area(monkeypatch, tmp_path):
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)
    events = []
    monkeypatch.setattr(feedback_router, "log_activity", lambda event, **kw: events.append((event, kw)))
    r = client.post(
        "/catalog/section-feedback/submit",
        data={"cliente": "AySA", "dominio": "Ventas", "slug": "resumen", "comment": "Falta un diagrama"},
    )
    assert r.status_code == 200
    assert events and events[0][0] == "catalog_section.feedback"
    kw = events[0][1]
    assert kw["scope"] == "copilot" and kw["area"] == "catalog"
    assert kw["feedback_sentiment"] == "negative"


def test_activity_emission_never_breaks_the_feedback_write_or_response(monkeypatch, tmp_path):
    # Best-effort, fuera del try/except que protege la escritura del YAML: si
    # log_activity explota, el feedback YA persistido (o su respuesta) nunca
    # se pierde/rompe -- MISMO criterio fail-soft que el resto de este router.
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)

    def _boom(event, **kw):
        raise RuntimeError("activity sink down")

    monkeypatch.setattr(feedback_router, "log_activity", _boom)
    r = client.post(
        "/modules/01_Arquitectura_y_Conectividad/feedback",
        data={"rating": "up", "pack": "datasphere_core"},
    )
    assert r.status_code == 200
    saved = list(tmp_path.glob("module_feedback_*.yaml"))
    assert len(saved) == 1  # el YAML se guardó igual, pese a que log_activity reventó.


def test_module_and_section_feedback_are_classified_usage_for_metrics():
    from webapp.services import activity_context
    assert activity_context.event_class("module.feedback") == activity_context.EVENT_CLASS_USAGE
    assert activity_context.event_class("section.feedback") == activity_context.EVENT_CLASS_USAGE
    assert activity_context.event_class("catalog.feedback") == activity_context.EVENT_CLASS_USAGE
    assert activity_context.event_class("catalog_section.feedback") == activity_context.EVENT_CLASS_USAGE
