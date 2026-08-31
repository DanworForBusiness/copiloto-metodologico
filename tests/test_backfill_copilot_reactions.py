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


def test_assistant_response_backfills_using_filename_marker_not_reply_id(tmp_path):
    # CORRECTIVE (owner AUDIT — "REACTIONS BACKFILL — FINAL CORRECTIVE"):
    # reply_id identifica la RESPUESTA del asistente, NO un registro de
    # feedback persistido -- AssistantFeedbackRepository.save no tiene
    # ninguna invariante de unicidad por reply_id. El marker de
    # deduplicación es SIEMPRE el nombre de archivo, igual que los otros
    # cinco tipos; reply_id/conversation_id viajan solo como correlación.
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
    # marker derivado del NOMBRE DE ARCHIVO -- NUNCA de reply_id.
    expected_marker = bcr._opaque_marker("feedback_20260104_000000_dddddddd.yaml")
    assert c["payload"]["backfill_marker"] == expected_marker
    assert c["payload"]["backfill_marker"] != bcr._opaque_marker("reply-123")
    # reply_id/conversation_id SÍ viajan -- solo como metadata de correlación.
    assert c["payload"]["reply_id"] == "reply-123"
    assert c["payload"]["conversation_id"] == "conv-1"


# ── §1 del corrective: dos registros legítimos pueden compartir reply_id ────

def test_two_assistant_records_sharing_reply_id_both_become_candidates(tmp_path):
    # A: dos envíos de feedback LEGÍTIMOS para la MISMA respuesta (p. ej. un
    # reenvío real) -- dos archivos YAML distintos, MISMO reply_id.
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    _write_feedback(fb, "feedback_20260104_000000_first111.yaml", {
        "type": "assistant_response", "module": "CP02", "rating": "up",
        "reply_id": "reply-shared", "conversation_id": "conv-1",
        "user": "d@d.com", "ts": "2026-01-18T07:00:00+00:00",
    })
    _write_feedback(fb, "feedback_20260104_000100_second22.yaml", {
        "type": "assistant_response", "module": "CP02", "rating": "down",
        "reply_id": "reply-shared", "conversation_id": "conv-1",
        "user": "d@d.com", "ts": "2026-01-18T07:01:00+00:00",
    })
    candidates = bcr.plan(feedback_dir=fb, activity_dir=act)
    assert len(candidates) == 2  # ambos sobreviven -- ninguno se pierde por compartir reply_id.
    sentiments = {c["payload"]["feedback_sentiment"] for c in candidates}
    assert sentiments == {"positive", "negative"}
    for c in candidates:
        assert c["payload"]["reply_id"] == "reply-shared"

    # B: markers DISTINTOS pese al reply_id compartido.
    markers = {c["payload"]["backfill_marker"] for c in candidates}
    assert len(markers) == 2

    # C: aplicar y re-planear -- la segunda corrida no inserta nada nuevo.
    inserted = bcr.apply(candidates, activity_dir=act)
    assert inserted == 2
    assert bcr.plan(feedback_dir=fb, activity_dir=act) == []
    assert len(_read_activity_day(act, "20260118")) == 2  # ambos eventos, nunca colapsados en uno.


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


# ── §2 del corrective: normalización UTC real, nunca slicing de texto ──────

def test_offset_timestamp_crossing_utc_day_boundary_normalizes_correctly(tmp_path):
    # D + E + F: 23:30 a -05:00 es 04:30 UTC del día SIGUIENTE -- el
    # candidato/evento debe reflejar el instante UTC real, y el archivo
    # activity_YYYYMMDD.jsonl debe ser el del día UTC, no el del texto
    # original.
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    _write_feedback(fb, "module_feedback_boundary.yaml", {
        "type": "module_content", "module": "CP01", "rating": "up",
        "user": "a@d.com", "ts": "2026-01-15T23:30:00-05:00",
    })
    candidates = bcr.plan(feedback_dir=fb, activity_dir=act)
    assert len(candidates) == 1
    c = candidates[0]
    # E: ts_utc es el instante UTC canónico, nunca el offset original.
    assert c["ts_utc"] == "2026-01-16T04:30:00+00:00"
    inserted = bcr.apply(candidates, activity_dir=act)
    assert inserted == 1
    # F: el archivo del día UTC, NUNCA el día del texto original (15, no 16).
    assert list(act.glob("activity_*.jsonl")) == [act / "activity_20260116.jsonl"]
    day_events = _read_activity_day(act, "20260116")
    assert len(day_events) == 1
    assert day_events[0]["ts_utc"] == "2026-01-16T04:30:00+00:00"
    assert not (act / "activity_20260115.jsonl").exists()


def test_naive_timestamp_without_timezone_is_skipped(tmp_path):
    # G: sin offset -- nunca se asume UTC/local, se saltea.
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    _write_feedback(fb, "module_feedback_naive.yaml", {
        "type": "module_content", "module": "CP01", "rating": "up",
        "user": "a@d.com", "ts": "2026-01-15T10:30:00",  # sin offset.
    })
    assert bcr.plan(feedback_dir=fb, activity_dir=act) == []
    assert bcr._parse_utc("2026-01-15T10:30:00") is None


def test_malformed_timestamp_is_skipped(tmp_path):
    # H: texto que ni siquiera parsea como ISO 8601.
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    _write_feedback(fb, "module_feedback_bad.yaml", {
        "type": "module_content", "module": "CP01", "rating": "up",
        "user": "a@d.com", "ts": "no-es-una-fecha",
    })
    assert bcr.plan(feedback_dir=fb, activity_dir=act) == []
    assert bcr._parse_utc("no-es-una-fecha") is None
    assert bcr._parse_utc("") is None


def test_ordinary_utc_offset_record_is_semantically_unchanged(tmp_path):
    # I: un registro +00:00 normal produce el MISMO ts_utc, byte a byte --
    # la normalización es un no-op para lo que ya era UTC.
    fb = tmp_path / "feedback_log"; fb.mkdir()
    act = tmp_path / "activity_log"
    original_ts = "2026-06-10T14:05:30+00:00"
    _write_feedback(fb, "catalog_feedback_plain.yaml", {
        "type": "catalog", "rating": "up", "user": "a@d.com", "ts": original_ts,
    })
    candidates = bcr.plan(feedback_dir=fb, activity_dir=act)
    assert len(candidates) == 1
    assert candidates[0]["ts_utc"] == original_ts
    inserted = bcr.apply(candidates, activity_dir=act)
    assert inserted == 1
    assert list(act.glob("activity_*.jsonl")) == [act / "activity_20260610.jsonl"]


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
