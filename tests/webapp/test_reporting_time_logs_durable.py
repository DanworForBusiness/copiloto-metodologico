"""
test_reporting_time_logs_durable.py — DURABLE VERSIONED STATE FOR HISTORICAL
TIME LOGS, unit coverage of reporting_time_logs_durable.py itself (Integral
Cold Start Fase 1, owner ticket, ver HANDOFF).

Cubre lo que es PROPIO de este módulo — el contrato de año cerrado
(`is_closed_year`, rollover determinístico), la escritura/lectura crash-safe,
el gate de `PERSIST_ENABLED`, y la coordinación de restore vía
`core.remote_store`. La integración con `_download_time_logs_rows`/
`_download_one_time_logs_file_cached` (reporting_repository.py) vive en
test_reporting_time_logs_closed_year_integration.py — SIN duplicar acá."""
from __future__ import annotations

import json
import threading
import time

import pytest

from core import remote_store
from webapp.services import reporting_time_logs_durable as durable


@pytest.fixture(autouse=True)
def _isolated_durable_dir(monkeypatch, tmp_path):
    """CADA test corre contra un `BASE_DIR` temporal propio — nunca toca
    el repo real. `PERSIST_ENABLED` arranca en `True` (el caso que este
    archivo prueba) — los tests de gating lo apagan explícitamente."""
    import config
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    # Aislamiento del estado de restore entre tests (mismo criterio que
    # cualquier otro `_isolate_*` de esta suite).
    monkeypatch.setattr(remote_store, "_restore_state", "not_applicable")
    monkeypatch.setattr(remote_store, "_restore_elapsed_ms", None)
    remote_store._restore_done.clear()


# ============================================================
# is_closed_year — puro, rollover determinístico
# ============================================================

def test_year_before_current_is_closed():
    assert durable.is_closed_year("2025", current_year=2026) is True


def test_current_year_is_not_closed():
    assert durable.is_closed_year("2026", current_year=2026) is False


def test_future_year_is_not_closed():
    assert durable.is_closed_year("2027", current_year=2026) is False


def test_rollover_survives_without_code_change():
    # MISMO año, evaluado en dos calendarios distintos — 2026 pasa de
    # abierto a cerrado apenas cambia `current_year`, sin ninguna lista
    # harcodeada de años de por medio.
    assert durable.is_closed_year("2026", current_year=2026) is False
    assert durable.is_closed_year("2026", current_year=2027) is True
    assert durable.is_closed_year("2025", current_year=2027) is True


def test_non_numeric_year_is_never_closed():
    assert durable.is_closed_year("no-year", current_year=2026) is False
    assert durable.is_closed_year("", current_year=2026) is False


def test_default_current_year_uses_wall_clock_not_hardcoded():
    # Sin `current_year` explícito, usa datetime.now().year — un año MUY
    # en el pasado (año 1) siempre está cerrado sin importar cuándo corra
    # la suite; MISMA fuente de "hoy" que ya usa `_infer_time_logs_csv_year`.
    assert durable.is_closed_year("1") is True


# ============================================================
# save_closed_year_entry / load_closed_year_entry — round-trip
# ============================================================

def test_round_trip_hit():
    rows = [{"Date": "2024-03-01", "Project": "Alfa", "Duration Decimal": "4"}]
    durable.save_closed_year_entry("user-a@danwor.com", "2024", "item-1", "etag-1", rows)
    assert durable.load_closed_year_entry("user-a@danwor.com", "2024") == rows


def test_missing_entry_is_a_clean_miss():
    assert durable.load_closed_year_entry("user-a@danwor.com", "2024") is None


def test_empty_rows_are_never_persisted():
    durable.save_closed_year_entry("user-a@danwor.com", "2024", "item-1", "etag-1", [])
    assert durable.load_closed_year_entry("user-a@danwor.com", "2024") is None


def test_save_overwrites_the_same_deterministic_path():
    durable.save_closed_year_entry("user-a@danwor.com", "2024", "item-1", "etag-1", [{"a": 1}])
    durable.save_closed_year_entry("user-a@danwor.com", "2024", "item-1", "etag-2", [{"a": 2}])
    assert durable.load_closed_year_entry("user-a@danwor.com", "2024") == [{"a": 2}]


def test_corrupt_json_is_a_safe_miss_not_an_exception():
    durable.save_closed_year_entry("user-a@danwor.com", "2024", "item-1", "etag-1", [{"a": 1}])
    path = durable._entry_path("user-a@danwor.com", "2024")
    path.write_text("{not valid json", encoding="utf-8")
    assert durable.load_closed_year_entry("user-a@danwor.com", "2024") is None


def test_format_version_mismatch_is_a_safe_miss():
    durable.save_closed_year_entry("user-a@danwor.com", "2024", "item-1", "etag-1", [{"a": 1}])
    path = durable._entry_path("user-a@danwor.com", "2024")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["format_version"] = 999
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert durable.load_closed_year_entry("user-a@danwor.com", "2024") is None


def test_parser_version_mismatch_is_a_safe_miss():
    durable.save_closed_year_entry("user-a@danwor.com", "2024", "item-1", "etag-1", [{"a": 1}])
    path = durable._entry_path("user-a@danwor.com", "2024")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["parser_version"] = 999
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert durable.load_closed_year_entry("user-a@danwor.com", "2024") is None


def test_wrong_shape_value_is_a_safe_miss():
    path = durable._entry_path("user-a@danwor.com", "2024")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "format_version": durable._DURABLE_FORMAT_VERSION,
        "parser_version": durable._PARSER_VERSION,
        "value": "not-a-list",
    }), encoding="utf-8")
    assert durable.load_closed_year_entry("user-a@danwor.com", "2024") is None


def test_scope_isolation_never_shares_entries():
    durable.save_closed_year_entry("user-a@danwor.com", "2024", "item-1", "etag-1", [{"a": 1}])
    assert durable.load_closed_year_entry("user-b@danwor.com", "2024") is None


def test_raw_scope_and_item_id_never_appear_on_disk():
    durable.save_closed_year_entry("user-a@danwor.com", "2024", "secret-item-id-xyz", "etag-1", [{"a": 1}])
    path = durable._entry_path("user-a@danwor.com", "2024")
    raw = path.read_text(encoding="utf-8")
    assert "user-a@danwor.com" not in raw
    assert "secret-item-id-xyz" not in raw
    # La ruta en sí tampoco expone el scope crudo.
    assert "user-a@danwor.com" not in str(path)


def test_crash_safe_write_never_leaves_a_visible_tmp_file():
    durable.save_closed_year_entry("user-a@danwor.com", "2024", "item-1", "etag-1", [{"a": 1}])
    scope_dir = durable._entry_path("user-a@danwor.com", "2024").parent
    leftovers = [p for p in scope_dir.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


# ============================================================
# PERSIST_ENABLED gating — dev local / esta misma suite sin Spaces
# ============================================================

def test_persist_disabled_never_touches_disk(monkeypatch):
    import config
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)
    durable.save_closed_year_entry("user-a@danwor.com", "2024", "item-1", "etag-1", [{"a": 1}])
    assert not durable._durable_dir().exists()
    assert durable.load_closed_year_entry("user-a@danwor.com", "2024") is None


def test_ensure_restored_is_a_true_noop_when_disabled(monkeypatch):
    import config
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)
    monkeypatch.setattr(remote_store, "_restore_state", "in_progress")  # nunca debería esperar esto
    started = time.perf_counter()
    durable.ensure_restored()
    assert (time.perf_counter() - started) < 1.0  # no bloqueó


# ============================================================
# Restore-coordination — via core.remote_store, uso real desde un thread
# ============================================================

def test_ensure_restored_returns_immediately_when_already_completed(monkeypatch):
    monkeypatch.setattr(remote_store, "_restore_state", "completed")
    started = time.perf_counter()
    durable.ensure_restored()
    assert (time.perf_counter() - started) < 1.0


def test_ensure_restored_joins_an_in_progress_restore_rather_than_polling(monkeypatch):
    monkeypatch.setattr(remote_store, "_restore_state", "in_progress")
    monkeypatch.setattr(durable, "_RESTORE_WAIT_TIMEOUT_S", 5.0)

    def _finish_after_delay():
        time.sleep(0.15)
        with remote_store._restore_state_lock:
            remote_store._restore_state = "completed"
        remote_store._restore_done.set()

    t = threading.Thread(target=_finish_after_delay)
    t.start()
    started = time.perf_counter()
    durable.ensure_restored()
    elapsed = time.perf_counter() - started
    t.join(timeout=2)
    assert 0.1 <= elapsed < 2.0, "no esperó al restore real (probablemente hizo polling o ignoró el evento)"
    assert remote_store.restore_status() == "completed"


def test_ensure_restored_gives_up_after_timeout_and_degrades(monkeypatch):
    monkeypatch.setattr(remote_store, "_restore_state", "in_progress")
    monkeypatch.setattr(durable, "_RESTORE_WAIT_TIMEOUT_S", 0.1)
    started = time.perf_counter()
    durable.ensure_restored()  # nunca lanza, nunca cuelga más allá del timeout
    assert (time.perf_counter() - started) < 1.0
