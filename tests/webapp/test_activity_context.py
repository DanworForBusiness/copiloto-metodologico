"""
test_activity_context.py — Fundación de contexto de actividad (Batch A1).

Prueba, con directorios de log temporales aislados (nunca los runtime reales):
- el envelope aditivo scope/area en el sink (activity_service);
- que el saneo/redacción y el gate BASIC/VERBOSE NO cambiaron;
- la normalización canónica (activity_context.context_of / classify) para
  registros históricos (sin campos) y nuevos (con campos explícitos);
- que el lector (activity_analytics._read_events) adjunta scope/area a ambos.
"""
from __future__ import annotations

import json

from webapp.services import activity_service
from webapp.services import activity_analytics
from webapp.services import activity_context as ax


def _write_jsonl(tmp_path, day: str, records: list[dict]) -> None:
    (tmp_path / f"activity_{day}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _read_only_file(tmp_path) -> list[dict]:
    files = list(tmp_path.glob("activity_*.jsonl"))
    assert len(files) == 1, files
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines() if line.strip()]


# ── classify / context_of ────────────────────────────────────────────────────

def test_classify_known_families():
    assert ax.classify("login") == (ax.SCOPE_PLATFORM, ax.AREA_AUTH)
    assert ax.classify("module_selected") == (ax.SCOPE_COPILOT, ax.AREA_METHODOLOGY)
    assert ax.classify("assistant_query") == (ax.SCOPE_COPILOT, ax.AREA_ASSISTANT)
    assert ax.classify("academy_selected") == (ax.SCOPE_COPILOT, ax.AREA_ACADEMY)
    assert ax.classify("catalog_view") == (ax.SCOPE_COPILOT, ax.AREA_CATALOG)
    assert ax.classify("tree_outcome") == (ax.SCOPE_COPILOT, ax.AREA_METHODOLOGY)
    assert ax.classify("staffing_overview_error") == (ax.SCOPE_OPERATIONS, ax.AREA_STAFFING)


def test_classify_unknown_is_explicit_fallback_not_copilot():
    # Un evento sin evidencia NO se clasifica en silencio como copiloto.
    assert ax.classify("algo_que_no_existe") == (ax.SCOPE_UNKNOWN, ax.AREA_UNKNOWN)


def test_context_of_prefers_explicit_over_classification():
    # Registro nuevo con campos explícitos: se respetan tal cual.
    assert ax.context_of({"event": "login", "scope": "operations", "area": "staffing"}) == ("operations", "staffing")
    # Registro histórico sin campos: cae a la clasificación por event.
    assert ax.context_of({"event": "login"}) == (ax.SCOPE_PLATFORM, ax.AREA_AUTH)
    # Parcial (solo scope explícito) completa el área por clasificación.
    assert ax.context_of({"event": "login", "scope": "platform"}) == ("platform", ax.AREA_AUTH)


def test_every_emitted_event_name_is_classified():
    # Guardia: todo evento que un productor real emite debe tener clasificación
    # (no caer a unknown). Si se agrega un productor nuevo sin mapear, falla acá.
    emitted = [
        "login", "module_selected", "module_continue", "module_reached_end",
        "nav_jump", "link_external", "disclosure_toggle", "theme_change",
        "academy_selected", "assistant_query", "catalog_view", "catalog_regenerate",
        "tree_started", "tree_step", "tree_outcome",
        "staffing_overview_viewed", "staffing_overview_config_incomplete", "staffing_overview_error",
    ]
    for name in emitted:
        scope, area = ax.classify(name)
        assert scope != ax.SCOPE_UNKNOWN and area != ax.AREA_UNKNOWN, name


# ── envelope del sink ────────────────────────────────────────────────────────

def test_new_event_persists_explicit_scope_area(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "VERBOSE")
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)
    activity_service.log_activity(
        "staffing_overview_viewed", user={"sub": "a@danwor.com", "name": "A"},
        scope="operations", area="staffing",
    )
    (rec,) = _read_only_file(tmp_path)
    assert list(rec.keys()) == ["ts_utc", "event", "user", "name", "scope", "area", "payload"]
    assert rec["scope"] == "operations" and rec["area"] == "staffing"
    assert rec["event"] == "staffing_overview_viewed"
    assert rec["user"] == "a@danwor.com" and rec["name"] == "A"
    assert rec["payload"] == {}


def test_envelope_omits_context_when_not_passed(monkeypatch, tmp_path):
    # Backward-compat de forma: un caller que no pasa scope/area escribe el
    # envelope histórico de 5 campos (el lector normaliza su ausencia).
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "VERBOSE")
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)
    activity_service.log_activity("legacy_shape", user={"sub": "a@danwor.com"})
    (rec,) = _read_only_file(tmp_path)
    assert "scope" not in rec and "area" not in rec
    assert set(rec.keys()) == {"ts_utc", "event", "user", "name", "payload"}


def test_sanitization_and_redaction_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "VERBOSE")
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)
    long_normal = "x" * 5000
    long_answer = "y" * 5000
    activity_service.log_activity(
        "e", user={"sub": "a@danwor.com"}, scope="copilot", area="assistant",
        token="super-secret", note=long_normal, answer=long_answer,
    )
    (rec,) = _read_only_file(tmp_path)
    p = rec["payload"]
    assert p["token"] == "[redacted]"                       # clave sensible redactada
    assert p["note"].endswith("…[truncated]") and len(p["note"]) <= 4000 + len("…[truncated]")
    assert p["answer"] == long_answer                        # long-text: NO truncado (<20000)


def test_basic_verbose_gate_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)

    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "OFF")
    activity_service.log_activity("x", user={"sub": "a@danwor.com"}, scope="copilot", area="catalog")
    assert list(tmp_path.glob("activity_*.jsonl")) == []     # OFF: nada

    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "BASIC")
    activity_service.log_verbose("verbose_evt", user={"sub": "a@danwor.com"}, scope="copilot", area="assistant")
    assert list(tmp_path.glob("activity_*.jsonl")) == []     # BASIC: log_verbose no-op
    activity_service.log_activity("basic_evt", user={"sub": "a@danwor.com"}, scope="platform", area="auth")
    names = [r["event"] for r in _read_only_file(tmp_path)]
    assert names == ["basic_evt"]                            # BASIC: log_activity sí


# ── lector: normalización de históricos y nuevos ─────────────────────────────

def test_reader_normalizes_historical_and_new(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    _write_jsonl(tmp_path, "20260101", [
        # Históricos SIN scope/area (forma de 5 campos) — deben clasificarse.
        {"ts_utc": "2026-01-01T00:00:00+00:00", "event": "login", "user": "u@d.com", "name": "U", "payload": {}},
        {"ts_utc": "2026-01-01T00:01:00+00:00", "event": "module_selected", "user": "u@d.com", "name": "U", "payload": {"module": "01"}},
        {"ts_utc": "2026-01-01T00:02:00+00:00", "event": "academy_selected", "user": "u@d.com", "name": "U", "payload": {}},
        {"ts_utc": "2026-01-01T00:03:00+00:00", "event": "catalog_view", "user": "u@d.com", "name": "U", "payload": {"kind": "cds"}},
        {"ts_utc": "2026-01-01T00:04:00+00:00", "event": "staffing_overview_error", "user": "u@d.com", "name": "U", "payload": {}},
        {"ts_utc": "2026-01-01T00:05:00+00:00", "event": "quien_sabe", "user": "u@d.com", "name": "U", "payload": {}},
        # Nuevo CON scope/area explícitos — deben preservarse.
        {"ts_utc": "2026-01-01T00:06:00+00:00", "event": "staffing_overview_viewed", "user": "u@d.com", "name": "U", "scope": "operations", "area": "staffing", "payload": {}},
    ])
    events = activity_analytics._read_events("", "")
    ctx = {e["event"]: (e["scope"], e["area"]) for e in events}
    assert len(events) == 7                                   # 5-field históricos legibles
    assert ctx["login"] == ("platform", "auth")
    assert ctx["module_selected"] == ("copilot", "methodology")
    assert ctx["academy_selected"] == ("copilot", "academy")
    assert ctx["catalog_view"] == ("copilot", "catalog")
    assert ctx["staffing_overview_error"] == ("operations", "staffing")
    assert ctx["quien_sabe"] == ("unknown", "unknown")        # fallback explícito
    assert ctx["staffing_overview_viewed"] == ("operations", "staffing")  # explícito preservado


# ── Batch A2: dashboard Danwor Hub-wide (scope/area) ──────────────────────────

def _seed_hub(monkeypatch, tmp_path):
    """Dataset mixto Hub-wide para los tests A2. Incluye plataforma, copiloto y
    operaciones; un evento HISTÓRICO de 5 campos (sin scope/area) y uno de scope
    desconocido, para probar normalización y fallback explícito."""
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    _write_jsonl(tmp_path, "20260301", [
        {"ts_utc": "2026-03-01T10:00:00+00:00", "event": "login", "user": "copi@d.com", "name": "Copi", "scope": "platform", "area": "auth", "payload": {}},
        {"ts_utc": "2026-03-01T10:01:00+00:00", "event": "module_selected", "user": "copi@d.com", "name": "Copi", "scope": "copilot", "area": "methodology", "payload": {"module": "01"}},
        {"ts_utc": "2026-03-01T10:02:00+00:00", "event": "assistant_query", "user": "copi@d.com", "name": "Copi", "scope": "copilot", "area": "assistant", "payload": {"question": "q", "conversation_id": "c1", "reply_id": "r1", "answer": "a", "api_cost": True}},
        {"ts_utc": "2026-03-01T10:03:00+00:00", "event": "tree_started", "user": "copi@d.com", "name": "Copi", "scope": "copilot", "area": "methodology", "payload": {"stem": "01", "tree_id": "t1", "tree_title": "Árbol 1"}},
        {"ts_utc": "2026-03-01T10:04:00+00:00", "event": "staffing_overview_viewed", "user": "ops@d.com", "name": "Ops", "scope": "operations", "area": "staffing", "payload": {}},
        # Histórico de 5 campos (sin scope/area): debe normalizar por clasificación.
        {"ts_utc": "2026-03-01T10:05:00+00:00", "event": "academy_selected", "user": "copi@d.com", "name": "Copi", "payload": {"page": "ACAD01"}},
        # Evento de scope desconocido (sin campos, nombre sin evidencia).
        {"ts_utc": "2026-03-01T10:06:00+00:00", "event": "mystery_event", "user": "x@d.com", "name": "X", "payload": {}},
    ])


def test_default_summary_is_hub_wide_including_operations(monkeypatch, tmp_path):
    # item 1/2: default incluye platform + copilot + operations (exclusión A1 retirada).
    _seed_hub(monkeypatch, tmp_path)
    s = activity_analytics.build_summary("", "")
    scopes = {f["key"] for f in s["scope_facets"]}
    assert {"platform", "copilot", "operations"} <= scopes
    assert all(f["checked"] for f in s["scope_facets"])       # default: todo tildado
    assert all(f["checked"] for f in s["area_facets"])
    events = {r["event"] for r in s["event_counts"]}
    assert "staffing_overview_viewed" in events               # Operations incluido por default
    assert "login" in events and "module_selected" in events


def test_filter_scope_operations_returns_operations_only(monkeypatch, tmp_path):
    # item 3
    _seed_hub(monkeypatch, tmp_path)
    s = activity_analytics.build_summary("", "", scopes=["operations"])
    assert {r["event"] for r in s["event_counts"]} == {"staffing_overview_viewed"}
    assert s["copilot_present"] is False
    assert [f["key"] for f in s["area_facets"]] == ["staffing"]   # faceting: área se restringe


def test_filter_scope_copilot_excludes_operations_preserves_copilot(monkeypatch, tmp_path):
    # item 4 + item 13 (Operations no contamina lentes Copilot)
    _seed_hub(monkeypatch, tmp_path)
    s = activity_analytics.build_summary("", "", scopes=["copilot"])
    events = {r["event"] for r in s["event_counts"]}
    assert "staffing_overview_viewed" not in events
    assert {"module_selected", "assistant_query", "tree_started"} <= events
    assert s["copilot_present"] is True
    assert s["assistant_query_count"] == 1                    # lente Copilot correcta
    assert s["tree_started"]                                  # árboles presentes


def test_filter_scope_platform_preserves_auth(monkeypatch, tmp_path):
    # item 5
    _seed_hub(monkeypatch, tmp_path)
    s = activity_analytics.build_summary("", "", scopes=["platform"])
    assert {r["event"] for r in s["event_counts"]} == {"login"}
    assert [f["key"] for f in s["area_facets"]] == ["auth"]


def test_filter_area_staffing_returns_staffing_and_facets_scope_to_operations(monkeypatch, tmp_path):
    # item 6 + item 14 (facets derivados del universo)
    _seed_hub(monkeypatch, tmp_path)
    s = activity_analytics.build_summary("", "", areas=["staffing"])
    assert {r["event"] for r in s["event_counts"]} == {"staffing_overview_viewed"}
    # Área=staffing ⇒ Ámbito compatible resuelve a Operaciones.
    assert [f["key"] for f in s["scope_facets"]] == ["operations"]


def test_scope_and_area_combine(monkeypatch, tmp_path):
    # item 7
    _seed_hub(monkeypatch, tmp_path)
    s = activity_analytics.build_summary("", "", scopes=["copilot"], areas=["methodology"])
    assert {r["event"] for r in s["event_counts"]} == {"module_selected", "tree_started"}


def test_historical_five_field_and_unknown(monkeypatch, tmp_path):
    # item 8 (histórico 5 campos normaliza) + item 9 (unknown explícito, no copilot)
    _seed_hub(monkeypatch, tmp_path)
    read = {e["event"]: (e["scope"], e["area"]) for e in activity_analytics._read_events("", "")}
    assert read["academy_selected"] == ("copilot", "academy")   # histórico clasificado
    assert read["mystery_event"] == ("unknown", "unknown")      # fallback explícito
    # unknown filtrable como su propio ámbito (NO cae en copilot).
    s = activity_analytics.build_summary("", "", scopes=["unknown"])
    assert {r["event"] for r in s["event_counts"]} == {"mystery_event"}
    s_cop = activity_analytics.build_summary("", "", scopes=["copilot"])
    assert "mystery_event" not in {r["event"] for r in s_cop["event_counts"]}


def test_date_user_module_filters_still_behave(monkeypatch, tmp_path):
    # item 10 + item 11 (module no absorbe Operations)
    _seed_hub(monkeypatch, tmp_path)
    # user filter
    s_user = activity_analytics.build_summary("", "", user="ops@d.com")
    assert {r["event"] for r in s_user["event_counts"]} == {"staffing_overview_viewed"}
    # module filter → solo eventos con module (Copilot); Operations nunca tiene module
    s_mod = activity_analytics.build_summary("", "", module="01")
    ev_mod = {r["event"] for r in s_mod["event_counts"]}
    assert "staffing_overview_viewed" not in ev_mod            # module no absorbe Operations
    assert "module_selected" in ev_mod
    assert "01" in s_mod["available_modules"]


def test_areas_active_kpi_counts_distinct_areas(monkeypatch, tmp_path):
    _seed_hub(monkeypatch, tmp_path)
    s = activity_analytics.build_summary("", "")
    # auth, methodology, assistant, staffing, academy, unknown = 6 áreas distintas
    assert s["areas_active"] == len({e["area"] for e in activity_analytics._read_events("", "")})
