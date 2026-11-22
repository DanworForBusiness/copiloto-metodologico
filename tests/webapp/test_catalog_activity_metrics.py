"""Métricas de Uso → «Catálogos y Assessments» y la telemetría `hub-activity/v2` de modelos: consultas deduplicadas, Assessments ad hoc
reconstruidos por `run_id`, estados del circuito de actualización gobernada SIN mezclar (mostrada ≠ solicitada ≠ notificada ≠ revisada)."""
from __future__ import annotations

from types import SimpleNamespace

from webapp.services import catalog_activity
from webapp.services import catalog_model_activity as cma

RID = "f" * 32


# ── telemetría ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_model_view_is_deduplicated_per_user_model_and_view(monkeypatch):
    logged = []
    monkeypatch.setattr(cma, "log_activity", lambda event, **kw: logged.append(kw))
    monkeypatch.setattr(cma, "_seen", {})
    entry = SimpleNamespace(cliente="Contreras", dominio="HCM", entry_id="flow-1", artefactos=["2VD_X", "AM_Workforce"])
    user = {"sub": "a@danwor.com"}
    assert cma.log_model_viewed(user, entry, "model", now=10.0)
    assert not cma.log_model_viewed(user, entry, "model", now=20.0)                 # parcial / refresh
    assert cma.log_model_viewed(user, entry, "lineage", now=20.0)                   # otra vista
    assert cma.log_model_viewed(user, entry, "model", now=10.0 + cma.DEDUP_S + 1)
    assert logged[0]["model"] == "AM_Workforce" and logged[0]["flow"] == "flow-1" and logged[1]["view"] == "lineage"


def _ev(event, ts, **attrs):
    return {"event": event, "ts_utc": ts, "user": "a@danwor.com", "payload": attrs}


def test_catalog_container_keeps_states_apart_and_rebuilds_runs():
    events = [
        _ev("catalog_model_viewed", "2026-11-22T10:00:00", client="Contreras", model="AM_Workforce", view="model"),
        _ev("datasphere_assessment_run_started", "2026-11-22T10:01:00", client="Contreras", root_object="AM_Workforce", run_id="r1"),
        _ev("datasphere_assessment_run", "2026-11-22T10:07:00", client="Contreras", root_object="AM_Workforce", run_id="r1", outcome="success", level="BUENA", state="SUCCESS"),
        _ev("datasphere_assessment_run_started", "2026-11-22T11:00:00", client="Contreras", root_object="AM_Workforce", run_id="r2"),   # sin fin: interrumpida
        _ev("governed_update.opportunity_shown", "2026-11-22T10:08:00", client="Contreras", model="AM_Workforce", vigente_level="ND", candidato_level="BUENA"),
        _ev("governed_update.requested", "2026-11-22T10:09:00", client="Contreras", model="AM_Workforce", request_id=RID, created=True),
        _ev("governed_update.notified", "2026-11-22T10:09:01", client="Contreras", model="AM_Workforce", request_id=RID, dispatch="pending"),
        _ev("governed_update.reviewed", "2026-11-22T10:30:00", client="Contreras", model="AM_Workforce", request_id=RID, role="APP_OWNER", status="OPEN"),
        _ev("login", "2026-11-22T09:00:00"),
    ]
    view = catalog_activity.build(events, {})
    k = {x["key"]: x for x in view["kpis"]}
    assert (k["consultas"]["value"], k["assessments"]["value"], k["oportunidades"]["value"], k["solicitudes"]["value"],
            k["notificaciones"]["value"], k["revisiones"]["value"]) == (1, 1, 1, 1, 1, 1)
    assert "interrumpidos 1" in k["assessments"]["hint"]
    off = catalog_activity.build(events + [_ev("governed_update.notified", "2026-11-22T12:00:00", client="Contreras", model="AM_Workforce",
                                               request_id="e" * 32, dispatch="disabled")], {})
    assert {x["key"]: x for x in off["kpis"]}["notificaciones"]["value"] == 1                    # apagada ≠ emitida
    assert view["total"] == 8 and view["rows"][0]["ts"] > view["rows"][-1]["ts"]                  # sin historia: más reciente primero
    hist = catalog_activity.build(events, {"cat_client": "Contreras", "cat_model": "AM_Workforce"})
    assert hist["history"] and [r["ts"] for r in hist["rows"]] == sorted(r["ts"] for r in hist["rows"])
    assert any(r["request_url"] == f"/copilot/governed-updates/{RID}" for r in hist["rows"])
    gov = catalog_activity.build(events, {"cat_tool": catalog_activity.TOOL_GOVERNED})
    assert {r["tool"] for r in gov["rows"]} == {catalog_activity.TOOL_GOVERNED}


