"""
test_reporting_time_logs_closed_year_integration.py — DURABLE VERSIONED
STATE FOR HISTORICAL TIME LOGS, integration with
`_download_time_logs_rows`/`_download_one_time_logs_file_cached`
(reporting_repository.py) — Integral Cold Start Fase 1, owner ticket, ver
HANDOFF.

Complementa test_reporting_time_logs_durable.py (unit, sin Graph/repository)
y test_reporting_time_logs_historical_cache.py (Fase 2/3B, eTag in-process —
SIN duplicar acá su matriz de bajo nivel). Este archivo prueba el camino
REAL de una request: listar (o NO listar, ver Layer 1) → resolver durable →
descargar/parsear sólo lo que faltaba → persistir → equivalencia de negocio.

Años de prueba deliberadamente atemporales (`"2000"`/`"2001"` = SIEMPRE
cerrados, `"9999"` = SIEMPRE abierto) — nunca dependen de la fecha real de
la corrida, a diferencia de `test_reporting_time_logs_historical_cache.py`
(que sí asume 2026 real, coincidencia con la fecha de esta sesión pero no
algo de lo que este archivo debe depender)."""
from __future__ import annotations

import csv
import io
import threading
import time

import pytest

from core import remote_store
from webapp.services import reporting_repository as repo_mod
from webapp.services import reporting_time_logs_durable as durable
from webapp.services.reporting_repository import ReportingRepository
from webapp.services.reporting_time_logs_cache import TimeLogsHistoricalCache

_SITE, _OPS_SITE = "site-1", "ops-site-1"
_HP, _PROY, _CLI = "hp-list", "proy-list", "cli-list"
_CSV_HEADERS = ["Date", "Client", "Project", "Task", "Email", "Duration Decimal"]


def _csv_text(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_HEADERS)
    writer.writeheader()
    for r in rows:
        writer.writerow({h: r.get(h, "") for h in _CSV_HEADERS})
    return buf.getvalue().encode("utf-8")


def _row(year: str, hours: str = "1") -> dict:
    return {"Date": f"{year}-06-01", "Client": "Danone", "Project": "Soporte Danone",
            "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": hours}


def _closed_item(item_id, year, *, etag="etag-1"):
    return {"id": item_id, "name": f"Time Logs Reports {year}.csv",
             "eTag": etag, "lastModifiedDateTime": f"{year}-12-31T00:00:00Z", "size": 500}


def _current_item():
    return {"id": "current-1", "name": "Time Logs Reports.csv",
             "eTag": "etag-current", "lastModifiedDateTime": "9999-01-01T00:00:00Z", "size": 1000}


class _CallLog:
    def __init__(self):
        self.json = []
        self.paged = []
        self.bytes = []
        self._lock = threading.Lock()

    def add(self, bucket, url):
        with self._lock:
            getattr(self, bucket).append(url)


def _patch(monkeypatch, *, root_files, history_files, file_rows, block_item_id=None, block_started=None, block_release=None):
    log = _CallLog()

    def fake_json(url, access_token=None, **kw):
        log.add("json", url)
        if url == f"{repo_mod.GRAPH_BASE}/sites/{repo_mod.TIME_LOGS_SITE_PATH}":
            return {"id": _OPS_SITE}
        return {}

    def fake_paged(url, access_token=None, **kw):
        log.add("paged", url)
        if f"/sites/{_OPS_SITE}/drive/root:" in url and ":/children" in url:
            return root_files
        if "folder-previos" in url:
            return history_files
        return []

    def fake_bytes(url, access_token=None, **kw):
        log.add("bytes", url)
        if block_item_id and f"/drive/items/{block_item_id}/content" in url:
            block_started.set()
            block_release.wait(timeout=2)
        for item_id, rows in file_rows.items():
            if f"/drive/items/{item_id}/content" in url:
                return _csv_text(rows)
        return _csv_text([])

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)
    return log


def _repo():
    return ReportingRepository(site_id=_SITE, horas_proyectos_list_id=_HP, proyectos_list_id=_PROY, cliente_list_id=_CLI)


def _root_and_history():
    root = [_current_item(), {"id": "folder-previos", "name": "Times previos", "folder": {"childCount": 2}}]
    history = [_closed_item("hist-2000", "2000"), _closed_item("hist-2001", "2001")]
    return root, history


def _default_rows():
    return {"current-1": [_row("9999")], "hist-2000": [_row("2000")], "hist-2001": [_row("2001")]}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(repo_mod, "_TIME_LOGS_HISTORICAL_CACHE", TimeLogsHistoricalCache())
    monkeypatch.setattr(remote_store, "_restore_state", "completed")  # "restore ya hecho" por default
    monkeypatch.setattr(remote_store, "_restore_elapsed_ms", 12.5)
    remote_store._restore_done.set()


# ============================================================
# A — primera vez: sin entrada durable, camino normal + persiste
# ============================================================

def test_a_first_run_downloads_parses_and_persists(monkeypatch):
    root, history = _root_and_history()
    log = _patch(monkeypatch, root_files=root, history_files=history, file_rows=_default_rows())
    repo = _repo()

    rows = repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")

    assert {r["anio"] for r in rows} == {"2000", "2001", "9999"}
    assert len([u for u in log.bytes if "/content" in u]) == 3  # los 3 archivos, ninguno durable todavía
    assert durable.load_closed_year_entry("user-a@danwor.com", "2000") is not None
    assert durable.load_closed_year_entry("user-a@danwor.com", "2001") is not None
    # El año en curso NUNCA se persiste durable (§1 del ticket original).
    assert durable._durable_dir().exists()
    persisted_years = {p.stem for p in durable._durable_dir().rglob("*.json")}
    assert "9999" not in persisted_years


# ============================================================
# C — nueva instancia de memoria, entrada durable presente: cero descargas
# ============================================================

def test_c_fresh_memory_instance_with_durable_entry_zero_content_downloads(monkeypatch):
    durable.save_closed_year_entry("user-a@danwor.com", "2000", "hist-2000", "etag-old", [_row("2000", hours="7")])
    durable.save_closed_year_entry("user-a@danwor.com", "2001", "hist-2001", "etag-old", [_row("2001", hours="9")])
    root, history = _root_and_history()
    log = _patch(monkeypatch, root_files=root, history_files=history, file_rows=_default_rows())
    repo = _repo()

    rows = repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")

    hist_downloads = [u for u in log.bytes if "hist-2000" in u or "hist-2001" in u]
    assert hist_downloads == []  # cero descargas de contenido para los años cerrados
    assert next(r["horas"] for r in rows if r["anio"] == "2000") == 7.0  # vino del durable, no del fixture "vivo"


# ============================================================
# D — durable NUNCA se revalida por eTag para un año cerrado
# ============================================================

def test_d_closed_year_durable_entry_is_never_revalidated_against_live_etag(monkeypatch):
    durable.save_closed_year_entry("user-a@danwor.com", "2000", "hist-2000", "etag-STALE", [_row("2000", hours="42")])
    root, history = _root_and_history()
    # El eTag "vivo" que la fuente reportaría es DISTINTO del guardado —
    # para un año cerrado esto NUNCA importa (contrato de inmutabilidad de
    # negocio, no eTag).
    history[0] = _closed_item("hist-2000", "2000", etag="etag-DIFFERENT-NOW")
    log = _patch(monkeypatch, root_files=root, history_files=history, file_rows=_default_rows())
    repo = _repo()

    rows = repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")

    assert next(r["horas"] for r in rows if r["anio"] == "2000") == 42.0
    assert not any("hist-2000" in u for u in log.bytes)


# ============================================================
# E — entrada durable corrupta: ignorada, cae al camino frío, éxito
# ============================================================

def test_e_corrupt_durable_entry_falls_back_to_cold_path_safely(monkeypatch):
    durable.save_closed_year_entry("user-a@danwor.com", "2000", "hist-2000", "etag-1", [_row("2000")])
    durable._entry_path("user-a@danwor.com", "2000").write_text("{not json", encoding="utf-8")
    root, history = _root_and_history()
    log = _patch(monkeypatch, root_files=root, history_files=history, file_rows=_default_rows())
    repo = _repo()

    rows = repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")

    assert any("hist-2000" in u for u in log.bytes)  # tuvo que descargar de nuevo
    assert {r["anio"] for r in rows} == {"2000", "2001", "9999"}  # la request igual tuvo éxito


# ============================================================
# G — aislamiento de scope: A nunca ve la entrada durable de B
# ============================================================

def test_g_scope_isolation_across_repository_calls(monkeypatch):
    durable.save_closed_year_entry("user-a@danwor.com", "2000", "hist-2000", "etag-1", [_row("2000", hours="7")])
    root, history = _root_and_history()
    log = _patch(monkeypatch, root_files=root, history_files=history, file_rows=_default_rows())
    repo = _repo()

    rows = repo.get_time_logs_worked_hours("tok", cache_scope="user-b@danwor.com")

    assert any("hist-2000" in u for u in log.bytes)  # B paga su propia descarga
    assert next(r["horas"] for r in rows if r["anio"] == "2000") == 1.0  # dato "vivo", no el 7 de A


# ============================================================
# H — primera request simultánea: single-flight (vía TimeLogsHistoricalCache)
# ============================================================

def test_h_simultaneous_first_requests_single_flight_no_duplicate_download(monkeypatch):
    root, history = _root_and_history()
    started, release = threading.Event(), threading.Event()
    log = _patch(
        monkeypatch, root_files=root, history_files=history, file_rows=_default_rows(),
        block_item_id="hist-2000", block_started=started, block_release=release,
    )
    repo = _repo()
    results = {}

    def call_a():
        results["a"] = repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")

    def call_b():
        assert started.wait(timeout=2)
        results["b"] = repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")
        release.set()

    t1, t2 = threading.Thread(target=call_a), threading.Thread(target=call_b)
    t1.start()
    t2.start()
    t1.join(timeout=3)
    t2.join(timeout=3)

    downloads_2000 = [u for u in log.bytes if "hist-2000" in u]
    assert len(downloads_2000) == 1, "single-flight falló — el mismo archivo se descargó dos veces"
    assert results["a"] and results["b"]


# ============================================================
# I — restore en curso: la request se une, nunca reconstruye por su cuenta
# ============================================================

def test_i_request_joins_in_progress_restore_before_reading_durable(monkeypatch):
    durable.save_closed_year_entry("user-a@danwor.com", "2000", "hist-2000", "etag-1", [_row("2000", hours="7")])
    monkeypatch.setattr(remote_store, "_restore_state", "in_progress")
    remote_store._restore_done.clear()
    root, history = _root_and_history()
    log = _patch(monkeypatch, root_files=root, history_files=history, file_rows=_default_rows())
    repo = _repo()

    def _finish_restore_after_delay():
        time.sleep(0.15)
        with remote_store._restore_state_lock:
            remote_store._restore_state = "completed"
        remote_store._restore_done.set()

    t = threading.Thread(target=_finish_restore_after_delay)
    t.start()
    started = time.perf_counter()
    rows = repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")
    elapsed = time.perf_counter() - started
    t.join(timeout=2)

    assert elapsed >= 0.1, "no esperó al restore en curso"
    assert not any("hist-2000" in u for u in log.bytes)  # se unió al restore y encontró el durable
    assert next(r["horas"] for r in rows if r["anio"] == "2000") == 7.0


# ============================================================
# J — restore falló: camino frío normal, sin colgar la request
# ============================================================

def test_j_restore_failed_falls_back_to_normal_cold_path(monkeypatch):
    monkeypatch.setattr(remote_store, "_restore_state", "failed")
    root, history = _root_and_history()
    log = _patch(monkeypatch, root_files=root, history_files=history, file_rows=_default_rows())
    repo = _repo()

    rows = repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")
    assert {r["anio"] for r in rows} == {"2000", "2001", "9999"}
    assert any("hist-2000" in u for u in log.bytes)


# ============================================================
# K — PERSIST_ENABLED apagado: comportamiento local de siempre
# ============================================================

def test_k_persist_disabled_behaves_exactly_like_before(monkeypatch):
    import config
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)
    root, history = _root_and_history()
    log1 = _patch(monkeypatch, root_files=root, history_files=history, file_rows=_default_rows())
    repo = _repo()
    repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")
    assert len([u for u in log1.bytes if "hist-2000" in u]) == 1

    # Segunda llamada, MISMO proceso: sigue sirviendo de memoria (Fase 2/3B,
    # sin cambios) — pero NUNCA hubo persistencia durable de por medio.
    log2 = _patch(monkeypatch, root_files=root, history_files=history, file_rows=_default_rows())
    repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")
    assert [u for u in log2.bytes if "hist-2000" in u] == []  # cache en memoria, no durable
    assert not durable._durable_dir().exists()


# ============================================================
# Layer 1 — hot_years explícito: años cerrados ya durables NUNCA se listan
# ============================================================

def test_layer1_explicit_hot_years_skips_listing_entirely_when_all_durable(monkeypatch):
    durable.save_closed_year_entry("user-a@danwor.com", "2000", "hist-2000", "etag-1", [_row("2000", hours="7")])
    durable.save_closed_year_entry("user-a@danwor.com", "2001", "hist-2001", "etag-1", [_row("2001", hours="9")])
    root, history = _root_and_history()
    log = _patch(monkeypatch, root_files=root, history_files=history, file_rows=_default_rows())
    repo = _repo()

    rows = repo.get_time_logs_worked_hours("tok", hot_years=["2000", "2001"], cache_scope="user-a@danwor.com")

    assert log.paged == [], "no debía listar la carpeta en absoluto — ambos años ya eran durable"
    assert log.bytes == []
    assert {r["anio"] for r in rows} == {"2000", "2001"}


def test_layer1_partial_durable_only_lists_and_downloads_what_is_missing(monkeypatch):
    durable.save_closed_year_entry("user-a@danwor.com", "2000", "hist-2000", "etag-1", [_row("2000", hours="7")])
    root, history = _root_and_history()
    log = _patch(monkeypatch, root_files=root, history_files=history, file_rows=_default_rows())
    repo = _repo()

    rows = repo.get_time_logs_worked_hours("tok", hot_years=["2000", "2001"], cache_scope="user-a@danwor.com")

    assert not any("hist-2000" in u for u in log.bytes)  # 2000 vino durable
    assert any("hist-2001" in u for u in log.bytes)  # 2001 se descargó
    assert {r["anio"] for r in rows} == {"2000", "2001"}


# ============================================================
# L — equivalencia de negocio: legacy (sin cache_scope) == durable-tibio
# ============================================================

def test_l_business_output_equivalence_legacy_vs_durable(monkeypatch):
    root, history = _root_and_history()

    _patch(monkeypatch, root_files=root, history_files=history, file_rows=_default_rows())
    legacy = _repo().get_time_logs_worked_hours("tok")  # cache_scope=None — SIEMPRE el camino de siempre

    durable.save_closed_year_entry("user-a@danwor.com", "2000", "hist-2000", "etag-1", [_row("2000")])
    durable.save_closed_year_entry("user-a@danwor.com", "2001", "hist-2001", "etag-1", [_row("2001")])
    _patch(monkeypatch, root_files=root, history_files=history, file_rows=_default_rows())
    warm_durable = _repo().get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")

    key = lambda rows: sorted((r["proyecto"], r["anio"], r["mes"], r["horas"]) for r in rows)
    assert key(legacy) == key(warm_durable)


# ============================================================
# Alcance — el año en curso jamás pasa por el camino durable
# ============================================================

def test_current_year_item_never_consults_or_writes_durable_state(monkeypatch):
    root, history = _root_and_history()
    log = _patch(monkeypatch, root_files=root, history_files=history, file_rows=_default_rows())
    repo = _repo()
    repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")

    assert durable.load_closed_year_entry("user-a@danwor.com", "9999") is None
    # Repite la llamada: el archivo actual debe descargarse de nuevo cada
    # vez que su eTag "cambia" — acá no cambia, así que Fase 3B (memoria)
    # sigue sirviéndolo, pero nunca vía el camino durable de años cerrados.
    log2 = _patch(monkeypatch, root_files=root, history_files=history, file_rows=_default_rows())
    repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")
    assert not any("current-1" in u for u in log2.bytes)  # sirvió de memoria (Fase 3B), no durable
