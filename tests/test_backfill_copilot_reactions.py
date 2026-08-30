"""
test_backfill_copilot_reactions.py — HISTORICAL BACKFILL corrective (owner
ticket "REACCIONES — COMPLETE COPILOT COVERAGE + HISTORY" §5/§6/§8/§9/§12).

Prueba `backfill_copilot_reactions.py` (script standalone, MISMO patrón que
`analyze_feedback.py`) en aislamiento total: `plan()`/`apply()` reciben
`feedback_dir`/`activity_dir` explícitos (nunca tocan FEEDBACK_LOG_DIR/
ACTIVITY_LOG_DIR reales de este entorno).
"""
from __future__ import annotations

import json

import yaml

import backfill_copilot_reactions as bcr


def _write_feedback(feedback_dir, name, payload):
    (feedback_dir / name).write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8",
    )


def _read_activity_day(activity_dir, day):
    path = activity_dir / f"activity_{day}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── plan(): cada tipo históricamente suficiente produce el evento correcto ──

def test_module_content_backfills_to_module_feedback_methodology(tmp_path):
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    _write_feedback(fb, "module_feedback_20260101_000000_aaaaaaaa.yaml", {
        "type": "module_content", "module": "CP01", "module_title": "Arquitectura",
        "rating": "up", "user": "a@d.com", "ts": "2026-01-15T10:30:00+00:00",
    })
    candidates = bcr.plan(feedback_dir=fb, activity_dir=act)
    assert len(candidates) == 1
    c = candidates[0]
    assert c["event"] == "module.feedback"
    assert c["scope"] == "copilot" and c["area"] == "methodology"
    assert c["module"] == "CP01"
    assert c["user"] == "a@d.com"
    assert c["ts_utc"] == "2026-01-15T10:30:00+00:00"  # ORIGINAL, nunca "now"
    assert c["payload"]["feedback_sentiment"] == "positive"


def test_catalog_backfills_without_a_module_field(tmp_path):
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    _write_feedback(fb, "catalog_feedback_20260102_000000_bbbbbbbb.yaml", {
        "type": "catalog", "catalog_kind": "dominio", "catalog_ref": "ventas",
        "rating": "down", "user": "b@d.com", "ts": "2026-01-16T09:00:00+00:00",
    })
    candidates = bcr.plan(feedback_dir=fb, activity_dir=act)
    assert len(candidates) == 1
    c = candidates[0]
    assert c["event"] == "catalog.feedback"
    assert c["area"] == "catalog"
    assert "module" not in c  # catálogo no tiene esa dimensión, MISMO criterio que catalog_view.
    assert c["payload"]["feedback_sentiment"] == "negative"


def test_academy_content_backfills_to_academy_area(tmp_path):
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    _write_feedback(fb, "academy_feedback_20260103_000000_cccccccc.yaml", {
        "type": "academy_content", "academy_page": "intro", "academy_title": "Intro",
        "rating": "up", "user": "c@d.com", "ts": "2026-01-17T08:00:00+00:00",
    })
    candidates = bcr.plan(feedback_dir=fb, activity_dir=act)
    assert len(candidates) == 1
    assert candidates[0]["event"] == "academy.feedback"
    assert candidates[0]["area"] == "academy"
    assert candidates[0]["module"] == "intro"


def test_assistant_response_backfills_using_reply_id_as_marker_source(tmp_path):
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    _write_feedback(fb, "feedback_20260104_000000_dddddddd.yaml", {
        "type": "assistant_response", "module": "CP02", "topic": "x", "action": "chat",
        "rating": "up", "question": "hola", "reply_id": "reply-123", "conversation_id": "conv-1",
        "user": "d@d.com", "name": "D", "ts": "2026-01-18T07:00:00+00:00",
    })
    candidates = bcr.plan(feedback_dir=fb, activity_dir=act)
    assert len(candidates) == 1
    c = candidates[0]
    assert c["event"] == "assistant.feedback"
    assert c["area"] == "assistant"
    assert c["module"] == "CP02"
    assert c["name"] == "D"
    # marker derivado de reply_id (clave más fuerte que el filename), MISMO
    # formato opaco (sha256 truncado) que el resto de los tipos.
    expected_marker = bcr._opaque_marker("reply-123")
    assert c["payload"]["backfill_marker"] == expected_marker


# ── Q: anónimo cuenta a la izquierda, nunca fabrica usuario ─────────────────

def test_anonymous_feedback_backfills_with_empty_user_never_invented(tmp_path):
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    _write_feedback(fb, "section_feedback_20260105_000000_eeeeeeee.yaml", {
        "type": "section_content", "module": "CP01", "section_slug": "s1",
        "rating": "up", "user": "", "ts": "2026-01-19T06:00:00+00:00",
    })
    candidates = bcr.plan(feedback_dir=fb, activity_dir=act)
    assert len(candidates) == 1
    assert candidates[0]["user"] == ""  # nunca inventado


# ── R: Tree/case_library NUNCA se backfillea ─────────────────────────────────

def test_tree_wizard_ratings_are_never_backfilled(tmp_path):
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    # Ni siquiera si alguien escribiera un YAML con un `type` de árbol acá
    # (nunca ocurre en la práctica -- case_library vive en OTRO directorio,
    # CASE_LIBRARY_DIR, nunca FEEDBACK_LOG_DIR) -- el mapa de tipos cubiertos
    # simplemente no lo conoce.
    _write_feedback(fb, "case_20260101_000000.yaml", {
        "type": "tree_case", "rating": "up", "ts": "2026-01-01T00:00:00+00:00",
    })
    candidates = bcr.plan(feedback_dir=fb, activity_dir=act)
    assert candidates == []
    assert "tree_case" not in bcr._BACKFILL_TYPES
    assert "tree.feedback" not in {v[0] for v in bcr._BACKFILL_TYPES.values()}


# ── campos estructurales faltantes -> se saltea, nunca se inventa ───────────

def test_record_without_ts_is_skipped_never_defaulted_to_now(tmp_path):
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    _write_feedback(fb, "module_feedback_x.yaml", {
        "type": "module_content", "module": "CP01", "rating": "up", "user": "a@d.com",
    })
    assert bcr.plan(feedback_dir=fb, activity_dir=act) == []


def test_record_without_valid_rating_is_skipped(tmp_path):
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    _write_feedback(fb, "module_feedback_y.yaml", {
        "type": "module_content", "module": "CP01", "rating": "sideways",
        "user": "a@d.com", "ts": "2026-01-01T00:00:00+00:00",
    })
    assert bcr.plan(feedback_dir=fb, activity_dir=act) == []


def test_unrecognized_type_is_skipped(tmp_path):
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    _write_feedback(fb, "catalog_x.yaml", {
        "type": "some_future_type", "rating": "up", "ts": "2026-01-01T00:00:00+00:00",
    })
    assert bcr.plan(feedback_dir=fb, activity_dir=act) == []


# ── apply(): preserva timestamp original, escribe al día correcto (N/O) ────

def test_apply_writes_to_the_original_reaction_day_not_today(tmp_path):
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    _write_feedback(fb, "module_feedback_z.yaml", {
        "type": "module_content", "module": "CP01", "rating": "up",
        "user": "a@d.com", "ts": "2026-02-14T12:00:00+00:00",
    })
    candidates = bcr.plan(feedback_dir=fb, activity_dir=act)
    inserted = bcr.apply(candidates, activity_dir=act)
    assert inserted == 1
    day_events = _read_activity_day(act, "20260214")
    assert len(day_events) == 1
    assert day_events[0]["ts_utc"] == "2026-02-14T12:00:00+00:00"
    assert day_events[0]["event"] == "module.feedback"
    # NUNCA escribe a otro día (p. ej. "hoy") -- ningún otro archivo se creó.
    assert list(act.glob("activity_*.jsonl")) == [act / "activity_20260214.jsonl"]


# ── M: idempotencia -- segunda corrida nunca duplica ────────────────────────

def test_second_backfill_run_inserts_zero_duplicates(tmp_path):
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    _write_feedback(fb, "module_feedback_dup.yaml", {
        "type": "module_content", "module": "CP01", "rating": "up",
        "user": "a@d.com", "ts": "2026-03-01T00:00:00+00:00",
    })
    first_candidates = bcr.plan(feedback_dir=fb, activity_dir=act)
    assert len(first_candidates) == 1
    bcr.apply(first_candidates, activity_dir=act)

    second_candidates = bcr.plan(feedback_dir=fb, activity_dir=act)
    assert second_candidates == []  # el marker ya existe -> nada elegible.
    inserted_second = bcr.apply(second_candidates, activity_dir=act)
    assert inserted_second == 0
    assert len(_read_activity_day(act, "20260301")) == 1  # sigue habiendo UN solo evento.


def test_marker_is_not_reused_across_different_source_records(tmp_path):
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    _write_feedback(fb, "module_feedback_one.yaml", {
        "type": "module_content", "module": "CP01", "rating": "up",
        "user": "a@d.com", "ts": "2026-03-01T00:00:00+00:00",
    })
    _write_feedback(fb, "module_feedback_two.yaml", {
        "type": "module_content", "module": "CP01", "rating": "up",
        "user": "a@d.com", "ts": "2026-03-01T00:00:00+00:00",  # mismo user/día/rating, ARCHIVO distinto.
    })
    candidates = bcr.plan(feedback_dir=fb, activity_dir=act)
    assert len(candidates) == 2  # dos reacciones REALES distintas -- nunca se pisan por timestamp igual.
    markers = {c["payload"]["backfill_marker"] for c in candidates}
    assert len(markers) == 2


# ── dry-run vs --apply (CLI) ─────────────────────────────────────────────────

def test_cli_dry_run_never_writes(tmp_path, monkeypatch, capsys):
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    _write_feedback(fb, "module_feedback_cli.yaml", {
        "type": "module_content", "module": "CP01", "rating": "up",
        "user": "a@d.com", "ts": "2026-04-01T00:00:00+00:00",
    })
    monkeypatch.setattr(bcr.config, "FEEDBACK_LOG_DIR", fb)
    monkeypatch.setattr(bcr.config, "ACTIVITY_LOG_DIR", act)
    rc = bcr.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "module_content: 1" in out
    assert "a@d.com" not in out  # nunca user crudo en la salida.
    assert not act.exists()


def test_cli_apply_writes_and_reports_inserted_count(tmp_path, monkeypatch, capsys):
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    _write_feedback(fb, "module_feedback_cli2.yaml", {
        "type": "module_content", "module": "CP01", "rating": "up",
        "user": "a@d.com", "ts": "2026-04-02T00:00:00+00:00",
    })
    monkeypatch.setattr(bcr.config, "FEEDBACK_LOG_DIR", fb)
    monkeypatch.setattr(bcr.config, "ACTIVITY_LOG_DIR", act)
    rc = bcr.main(["--apply"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 evento(s) insertado(s)" in out
    assert len(_read_activity_day(act, "20260402")) == 1
