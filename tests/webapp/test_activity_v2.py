"""
Métricas de Uso v2 — fase 1: catálogo central, sink v2, page_view con view_id,
compatibilidad de lectura v1 y agregados diarios.

Todo el estado de disco va a `tmp_path` vía monkeypatch de los atributos de
módulo (`ACTIVITY_LOG_DIR` en sink/analytics/agregados) — mismo patrón que el
resto de los tests de actividad; nunca se toca os.environ.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.services import (
    activity_aggregates, activity_analytics, activity_catalog, activity_context, activity_service, auth_service,
)

_SESSION = auth_service.create_session(
    {"email": "v2-test@danwor.com", "name": "V2 Test", "first_name": "V2", "family_name": "Test"}
)


@pytest.fixture
def logdir(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "VERBOSE")
    for mod in (activity_service, activity_analytics, activity_aggregates):
        monkeypatch.setattr(mod, "ACTIVITY_LOG_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def client():
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, _SESSION)
    return c


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _all_records(logdir):
    out = []
    for p in sorted(logdir.glob("activity_*.jsonl")):
        out += _lines(p)
    return out


def _write_day(logdir, day, records):
    path = logdir / f"activity_{day}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def _pv(ts, user, view_id, nav_type="navigate", scope="copilot", area="methodology", module=None):
    rec = {"v": 2, "id": f"id-{view_id}-{ts}", "ts_utc": ts, "kind": "navigation", "event": "page_view",
           "user": user, "vertical": scope, "scope": scope, "area": area, "surface": "page",
           "attrs": {"view_id": view_id, "nav_type": nav_type, "path": "/x"}}
    if module:
        rec["module"] = module
    return rec


def _act(ts, user, event="catalog_entry_retired", scope="copilot", area="catalog", attrs=None):
    spec = activity_catalog.spec_of(event)
    return {"v": 2, "id": f"id-{event}-{ts}", "ts_utc": ts, "kind": spec.kind if spec else "unknown",
            "event": event, "user": user, "vertical": scope, "scope": scope, "area": area,
            "surface": "server", "attrs": attrs or {}}


# ── catálogo + sink ──────────────────────────────────────────────────────────

def test_catalog_covers_every_server_call_site_and_the_11_formerly_invisible_events():
    for name in (
        "catalog_entry_retired", "catalog_connection_retired", "catalog_apis_sync", "catalog_assessment_generate",
        "connectivity_inventory_run", "connectivity_catalog_publish", "datasphere_assessment_run",
        "datasphere_assessment_refresh_catalog", "datasphere_publication_run",
        "governed_contracts_list", "governed_contracts_detail",
    ):
        assert activity_context.event_class(name) == activity_context.EVENT_CLASS_USAGE, name
    assert activity_catalog.SPECS["page_view"].kind == "navigation"
    assert activity_catalog.SPECS["section_view"].kind == "navigation"
    # Los 5 de reporting que ningún JS emite ya no existen en el catálogo.
    for gone in ("reporting.hierarchy_toggle", "reporting.expand_all", "reporting.collapse_all",
                 "reporting.sort_change", "reporting.refresh"):
        assert activity_catalog.spec_of(gone) is None
    # Compatibilidad v1: el rename vive en el catálogo.
    assert activity_catalog.canonical("staffing_overview_viewed") == "module_viewed"
    assert activity_catalog.canonical("module_selected") == "module_viewed"


def test_whitelist_drops_non_allowed_attrs_and_credential_like_values(logdir):
    activity_service.log_activity(
        "link_external", user={"sub": "a@danwor.com", "name": "A"}, scope="copilot", area="methodology",
        href="https://example.com/doc?sig=abc123&token=zzz#parte", cls="ext",
        password="hunter2", userName="x", extra="no permitido", stem="Bearer abcdefgh",
    )
    (rec,) = _all_records(logdir)
    attrs = rec["attrs"]
    assert set(attrs) == {"href", "cls", "stem"}            # extra/password/userName fuera
    assert attrs["href"] == "https://example.com/doc#parte"   # query string fuera
    assert attrs["stem"] == "[redacted]"                      # marcador de credencial en el valor


def test_envelope_v2_shape_and_never_raises(logdir, monkeypatch):
    activity_service.log_activity(
        "section.feedback", user={"sub": "a@danwor.com", "name": "A"}, scope="commercial", area="strategy",
        module="CM02", section="s-01", section_title="Título", feedback_sentiment="positive",
    )
    (rec,) = _all_records(logdir)
    assert rec["v"] == 2 and len(rec["id"]) == 32
    assert list(rec) == ["v", "id", "ts_utc", "kind", "event", "user", "vertical", "scope", "area",
                         "module", "section", "surface", "attrs"]
    assert rec["kind"] == "action" and rec["vertical"] == "commercial" and rec["section"] == "s-01"
    assert "name" not in rec and "payload" not in rec
    assert rec["attrs"] == {"section_title": "Título", "feedback_sentiment": "positive"}
    # Falla de escritura: no levanta hacia el caller.
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", logdir / "no-dir" / "\0invalid")
    activity_service.log_activity("login", user={"sub": "a@danwor.com"})


def test_unknown_event_written_with_empty_attrs(logdir):
    activity_service.log_activity("evento_inventado", user={"sub": "a@danwor.com"}, foo="bar")
    (rec,) = _all_records(logdir)
    assert rec["event"] == "evento_inventado" and rec["attrs"] == {} and rec["kind"] == "unknown"


def test_assistant_query_v2_has_no_question_or_answer_text(logdir):
    activity_service.log_verbose(
        "assistant_query", user={"sub": "a@danwor.com"}, scope="copilot", area="assistant",
        stem="01", section="sec", question="¿pregunta secreta?", answer="respuesta larga",
        reply_id="r1", conversation_id="c1", llm=True, api_cost=False,
    )
    (rec,) = _all_records(logdir)
    blob = json.dumps(rec, ensure_ascii=False)
    assert "pregunta secreta" not in blob and "respuesta larga" not in blob
    assert rec["attrs"]["chars_in"] == len("¿pregunta secreta?") and rec["attrs"]["conversation_id"] == "c1"


# ── telemetry: page_view + bug de colisión de kwargs ─────────────────────────

def test_page_view_beacon_writes_navigation_with_view_id_without_verbose(logdir, client, monkeypatch):
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "BASIC")  # page_view es métrica básica
    r = client.post("/telemetry/event", json={
        "event": "page_view", "view_id": "abc123", "nav_type": "navigate",
        "path": "/operaciones/reporting/integral?x=1", "module_id": "",
    })
    assert r.status_code == 204
    (rec,) = _all_records(logdir)
    assert rec["event"] == "page_view" and rec["kind"] == "navigation" and rec["surface"] == "page"
    assert (rec["scope"], rec["area"]) == ("operations", "reporting")
    assert rec["attrs"] == {"view_id": "abc123", "nav_type": "navigate", "path": "/operaciones/reporting/integral"}


def test_page_view_resolves_module_from_module_id(logdir, client):
    client.post("/telemetry/event", json={"event": "page_view", "view_id": "v1", "nav_type": "reload",
                                          "path": "/comercial/estrategia", "module_id": "CM02"})
    (rec,) = _all_records(logdir)
    assert (rec["scope"], rec["area"], rec["module"]) == ("commercial", "strategy", "CM02")
    assert rec["attrs"]["nav_type"] == "reload"


def test_beacon_with_reserved_keys_no_longer_500s(logdir, client):
    # Antes: `module`/`user` en el body chocaban con los keyword-only del sink → TypeError → 500.
    r = client.post("/telemetry/event", json={"event": "nav_jump", "href": "#a", "module": "X", "user": "evil",
                                              "section": "s", "scope": "operations"})
    assert r.status_code == 204
    (rec,) = _all_records(logdir)
    assert rec["user"] == "v2-test@danwor.com" and rec["scope"] == "copilot" and "module" not in rec


def test_reporting_beacon_with_user_key_no_longer_500s(logdir, monkeypatch):
    from webapp import operations_access
    monkeypatch.setattr(operations_access, "is_operations_authorized", lambda request: True)
    c = TestClient(app, raise_server_exceptions=True)
    c.cookies.set(auth_service.COOKIE_NAME, _SESSION)
    r = c.post("/operaciones/reporting/integral/telemetry",
               json={"event": "reporting.tab_view", "report_tab": "cartera", "user": "evil", "module": "x"})
    assert r.status_code == 204
    (rec,) = _all_records(logdir)
    assert rec["user"] == "v2-test@danwor.com" and rec["module"] == "reporting"
    assert rec["attrs"] == {"report_tab": "cartera"}
    # Eventos retirados de la whitelist: no se escriben.
    c.post("/operaciones/reporting/integral/telemetry", json={"event": "reporting.refresh"})
    assert len(_all_records(logdir)) == 1


def test_base_html_exposes_view_id_only_with_session(monkeypatch, client):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    page = client.get("/modules/01_Arquitectura_y_Conectividad")
    assert page.status_code == 200
    snippet = page.text.split("window.DW_VIEW_ID=", 1)[1].split(";</script>", 1)[0]
    assert len(json.loads(snippet)) == 32
    anon = TestClient(app).get("/login")
    if "window.DW_VIEW_ID=" in anon.text:
        assert anon.text.split("window.DW_VIEW_ID=", 1)[1].split(";</script>", 1)[0] == '""'


# ── lectura v1 + visitas ─────────────────────────────────────────────────────

def test_v1_records_normalized_in_memory_without_rewriting_files(logdir):
    path = _write_day(logdir, "20261020", [
        {"ts_utc": "2026-10-20T10:00:00+00:00", "event": "module_selected", "user": "u@d.com", "name": "U",
         "payload": {"module": "01_Arquitectura_y_Conectividad"}},
        {"ts_utc": "2026-10-20T10:01:00+00:00", "event": "assistant_query", "user": "u@d.com", "name": "U",
         "scope": "copilot", "area": "assistant",
         "payload": {"question": "q v1", "answer": "a v1", "conversation_id": "c1", "reply_id": "r1"}},
    ])
    before = path.read_bytes()
    records = activity_aggregates._read_records("20261020")
    assert path.read_bytes() == before
    view, query = records
    assert view["v"] == 1 and view["event"] == "module_viewed" and view["module"] == "CP01"
    assert activity_context.is_visit(view)
    assert "question" not in query["attrs"] and "answer" not in query["attrs"]
    # El agregado tampoco ve el texto libre.
    agg = activity_aggregates.compute_day("20261020")
    assert "q v1" not in json.dumps(agg) and "a v1" not in json.dumps(agg)
    assert path.read_bytes() == before


def test_reload_not_visit_and_duplicated_view_id_counted_once(logdir):
    _write_day(logdir, "20261020", [
        _pv("2026-10-20T10:00:00+00:00", "u@d.com", "view1"),
        _pv("2026-10-20T10:00:01+00:00", "u@d.com", "view1"),            # beacon duplicado
        _pv("2026-10-20T10:05:00+00:00", "u@d.com", "view2", "reload"),  # recarga
        _pv("2026-10-20T10:06:00+00:00", "u@d.com", "view3", "back_forward"),
        # v2 module_viewed NO es visita (lo es page_view)
        _act("2026-10-20T10:06:30+00:00", "u@d.com", "module_viewed", area="methodology"),
    ])
    out = activity_aggregates.query("20261020", "20261020", visible_verticals=None, today="20261101")
    assert out["kpis"]["visits"] == 2  # view1 + view3


def test_unique_users_is_union_and_midnight_session_counted_once(logdir):
    _write_day(logdir, "20261020", [
        _act("2026-10-20T23:50:00+00:00", "a@d.com"),
        _act("2026-10-20T09:00:00+00:00", "b@d.com"),
    ])
    _write_day(logdir, "20261021", [
        _act("2026-10-21T00:10:00+00:00", "a@d.com"),   # 20 min después: MISMA sesión
        _act("2026-10-21T09:00:00+00:00", "b@d.com"),   # día siguiente: otra sesión
    ])
    out = activity_aggregates.query("2026-10-20", "2026-10-21", visible_verticals=None, today="20261101")
    assert out["kpis"]["unique_users"] == 2               # unión, no suma de días (4)
    assert out["kpis"]["sessions"] == 3                    # a: 1 (cruza medianoche), b: 2
    assert out["kpis"]["actions"] == 4
    assert [t["sessions"] for t in out["trend"]] == [2, 1]
    # El resumen por día guarda los tramos con inicio/fin ISO.
    day = activity_aggregates.load_day("20261020", today="20261101")
    assert day["sessions"]["a@d.com"] == [["2026-10-20T23:50:00+00:00", "2026-10-20T23:50:00+00:00"]]


def test_visibility_filter_excludes_other_verticals_from_aggregates_and_detail(logdir):
    _write_day(logdir, "20261020", [
        _pv("2026-10-20T10:00:00+00:00", "c@d.com", "v-cop"),
        _pv("2026-10-20T10:10:00+00:00", "o@d.com", "v-ops", scope="operations", area="staffing", module="OP01"),
        _act("2026-10-20T10:20:00+00:00", "o@d.com", "staffing.feedback", scope="operations", area="staffing",
             attrs={"feedback_sentiment": "positive"}),
    ])
    out = activity_aggregates.query("20261020", "20261020", visible_verticals={"copilot", "platform"},
                                    today="20261101")
    assert out["kpis"]["visits"] == 1 and out["kpis"]["unique_users"] == 1 and out["kpis"]["sessions"] == 1
    assert {b["vertical"] for b in out["breakdown"]} == {"copilot"}
    detail = activity_aggregates.events("20261020", "20261020", visible_verticals={"copilot"})
    assert detail["total"] == 1 and all(i["vertical"] == "copilot" for i in detail["items"])
    full = activity_aggregates.events("20261020", "20261020", visible_verticals=None)
    assert full["total"] == 3


def test_aggregate_written_once_reused_and_recomputed_when_source_changes(logdir, monkeypatch):
    src = _write_day(logdir, "20261020", [_act("2026-10-20T10:00:00+00:00", "a@d.com")])
    calls = []
    real = activity_aggregates.compute_day
    monkeypatch.setattr(activity_aggregates, "compute_day", lambda day, records=None: calls.append(day) or real(day, records))
    first = activity_aggregates.load_day("20261020", today="20261101")
    agg_path = logdir / "agg" / "20261020.json"
    assert agg_path.is_file() and len(calls) == 1
    stamp = agg_path.stat().st_mtime_ns
    again = activity_aggregates.load_day("20261020", today="20261101")
    assert len(calls) == 1 and again == first and agg_path.stat().st_mtime_ns == stamp   # reusado
    _write_day(logdir, "20261020", [_act("2026-10-20T11:00:00+00:00", "b@d.com")])
    assert src.stat().st_size > 0
    third = activity_aggregates.load_day("20261020", today="20261101")
    assert len(calls) == 2 and sum(r["count"] for r in third["rows"]) == 2           # recalculado
    # Hoy: al vuelo, nunca persistido.
    _write_day(logdir, "20261101", [_act("2026-11-01T08:00:00+00:00", "a@d.com")])
    activity_aggregates.load_day("20261101", today="20261101")
    assert not (logdir / "agg" / "20261101.json").exists()


# ── no regresión: historias del asistente ────────────────────────────────────

def test_v1_stories_still_render_and_v2_reads_text_from_assistant_store(logdir, monkeypatch):
    _write_day(logdir, "20261020", [
        {"ts_utc": "2026-10-20T10:00:00+00:00", "event": "assistant_query", "user": "u@d.com", "name": "U",
         "scope": "copilot", "area": "assistant",
         "payload": {"question": "pregunta v1", "answer": "respuesta v1", "conversation_id": "c1", "reply_id": "r1"}},
        {"v": 2, "id": "x", "ts_utc": "2026-10-20T11:00:00+00:00", "kind": "action", "event": "assistant_query",
         "user": "u@d.com", "vertical": "copilot", "scope": "copilot", "area": "assistant",
         "attrs": {"conversation_id": "c2", "reply_id": "r2", "chars_in": 10, "chars_out": 12}},
    ])
    store = {"r2": SimpleNamespace(question="pregunta v2", answer="respuesta v2")}
    monkeypatch.setattr(activity_analytics, "_assistant_reply", lambda rid: store.get(rid))
    s = activity_analytics.build_summary("2026-10-20", "2026-10-20")
    questions = {st["question"] for st in s["assistant_stories"]}
    assert questions == {"pregunta v1", "pregunta v2"}
    answers = {t["answer"] for st in s["assistant_stories"] for t in st["turns"]}
    assert answers == {"respuesta v1", "respuesta v2"}
    assert s["assistant_query_count"] == 2
