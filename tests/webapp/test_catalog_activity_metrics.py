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


# ── Mantenimiento del Catálogo: generación/regeneración completa (multi-modelo, sin cliente único) + regeneración de Assessments ──────────────
def test_catalog_generation_and_regeneration_kpis_and_filters():
    events = [
        _ev("catalog_regenerate", "2026-11-22T08:00:00", outcome="success"),            # generación/regeneración COMPLETA (sin cliente único)
        _ev("catalog_regenerate", "2026-11-22T09:00:00", outcome="rejected"),           # lock ocupado → NO ejecutó (rechazada, no un éxito por clic)
        _ev("catalog_apis_sync", "2026-11-22T09:30:00", step="apply", outcome="success"),
        _ev("catalog_client_assessment_regenerate", "2026-11-22T10:00:00", client="Contreras", model="AM_Workforce", outcome="success"),
        _ev("assessment_regenerated", "2026-11-22T10:05:00", client="Contreras", model="AM_Workforce", outcome="success"),
    ]
    k = {x["key"]: x for x in catalog_activity.build(events, {})["kpis"]}
    assert k["catalogo"]["value"] == 3                                                   # 2 catalog_regenerate + 1 catalog_apis_sync
    assert all(s in k["catalogo"]["hint"] for s in ("exitosas 1", "rechazadas 1", "APIs/CDS 1"))
    assert k["regeneraciones_assessment"]["value"] == 2
    assert "por cliente 1" in k["regeneraciones_assessment"]["hint"] and "por modelo 1" in k["regeneraciones_assessment"]["hint"]
    maint = catalog_activity.build(events, {"cat_tool": catalog_activity.TOOL_MAINTENANCE})   # la herramienta agrupa las 4 operaciones
    assert maint["total"] == 5 and {r["tool"] for r in maint["rows"]} == {catalog_activity.TOOL_MAINTENANCE}
    gen = catalog_activity.build(events, {"cat_op": "generacion_catalogo"})
    assert gen["total"] == 2 and {r["op"] for r in gen["rows"]} == {"generacion_catalogo"}
    byclient = catalog_activity.build(events, {"cat_client": "Contreras"})                # la generación completa no tiene cliente → se excluye;
    kc = {x["key"]: x for x in byclient["kpis"]}                                          # las regeneraciones por cliente/modelo SÍ conservan el cliente
    assert kc["catalogo"]["value"] == 0 and kc["regeneraciones_assessment"]["value"] == 2


# ── ADDENDUM: el formulario de filtros reutiliza los primitivos transversales y «✕ Limpiar» limpia SOLO los cat_* ───────────────────────────
def _render_catalog_partial(ctx):
    from pathlib import Path
    from jinja2 import Environment, FileSystemLoader
    tdir = Path(__file__).resolve().parents[2] / "webapp" / "templates"
    env = Environment(loader=FileSystemLoader(str(tdir)), autoescape=True)
    return env.get_template("partials/admin_activity_catalog.html").render(catalog=ctx)


def test_catalog_filter_form_reuses_transversal_primitives_and_clear_preserves_globals():
    import re
    events = [_ev("catalog_regenerate", "2026-11-22T08:00:00", outcome="success"),
              _ev("catalog_client_assessment_regenerate", "2026-11-22T10:00:00", client="Contreras", model="AM_Workforce", outcome="success")]
    # `global_pairs` = estado global YA normalizado por el tablero (no request.query_params crudos).
    gp = [("period", "last30"), ("user", "a@danwor.com")]
    html = _render_catalog_partial(catalog_activity.build(events, {"cat_client": "Contreras"}, global_pairs=gp))
    # 1) mismos primitivos que el tablero (admin_activity_content.html): clases + HTMX al #activity-dashboard
    assert 'class="whatsnew-filters activity-filters catalog-activity__filters"' in html
    assert html.count('<label class="whatsnew-filter">') == 4
    assert 'hx-get="/admin/activity"' in html and 'hx-target="#activity-dashboard"' in html and 'hx-swap="outerHTML"' in html
    # 2) sólo se preservan los globales normalizados como hidden (ni cat_*); Aplicar + «✕ Limpiar» transversales
    assert '<input type="hidden" name="period" value="last30">' in html and '<input type="hidden" name="user" value="a@danwor.com">' in html
    assert 'type="hidden" name="cat_' not in html
    assert '<button type="submit" class="btn-primary">Aplicar</button>' in html
    assert "whatsnew-filter__clear" in html and "✕ Limpiar" in html
    # 3) Limpiar preserva los globales y NO arrastra ningún cat_*
    href = re.search(r'whatsnew-filter__clear" hx-get="([^"]+)"', html).group(1)
    assert "period=last30" in href and "user=a%40danwor.com" in href and "cat_" not in href
    # 4) sin ningún cat_* activo, no se ofrece Limpiar
    assert "whatsnew-filter__clear" not in _render_catalog_partial(catalog_activity.build(events, {}, global_pairs=gp))


