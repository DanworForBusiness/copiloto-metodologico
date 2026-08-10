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
