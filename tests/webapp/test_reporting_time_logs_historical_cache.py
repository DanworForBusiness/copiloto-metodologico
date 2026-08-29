"""
test_reporting_time_logs_historical_cache.py — CACHE VERSIONED HISTORICAL
TIME LOGS corrective (Reporting Cartera/Proyecto Fase 2, owner ticket, ver
HANDOFF + webapp/services/reporting_time_logs_cache.py).

Cubre:
  - primitivo `TimeLogsHistoricalCache` en aislamiento (hit/miss/single-
    flight/LRU/excepción no envenena intentos futuros/keys distintas nunca
    se serializan entre sí);
  - §12 EQUIVALENCIA DE NEGOCIO: resultado frío (sin cache) == resultado
    tibio (cache HIT), incluyendo invalidación real por cambio de eTag;
  - §13 CALL-BUDGET: 5 históricos + 1 actual — 1ra llamada
    historical_downloads=5/current=1, 2da idéntica
    historical_downloads=0/current=1, un eTag histórico cambiado
    historical_downloads=1/current=1;
  - §14 AISLAMIENTO DE AUTORIZACIÓN: scope A no filtra hacia scope B;
  - el archivo ACTUAL (sin año en el nombre) NUNCA se cachea (política
    conservadora Fase 2, §7);
  - `cache_scope=None` (Mantenimiento/Rentabilidad/Batch B, callers que NO
    optan por esta fase) preserva el comportamiento EXACTO de siempre —
    cero caching.

Cada test que ejercita la cache real usa una instancia PROPIA de
`TimeLogsHistoricalCache` inyectada vía
`monkeypatch.setattr(repo_mod, "_TIME_LOGS_HISTORICAL_CACHE", ...)` —
aislamiento total entre tests, nunca estado compartido con el singleton de
producción ni entre casos de este mismo archivo.
"""
from __future__ import annotations

import csv
import io
import threading

import pytest

from webapp.services import reporting_repository as repo_mod
from webapp.services.reporting_repository import ReportingRepository
from webapp.services.reporting_time_logs_cache import TimeLogsHistoricalCache

_SITE, _OPS_SITE = "site-1", "ops-site-1"
_HP, _PROY, _CLI, _COLAB, _USERINFO = "hp-list", "proy-list", "cli-list", "colab-list", "userinfo-list"
_CSV_HEADERS = ["Date", "Client", "Project", "Task", "Email", "Duration Decimal"]

_HIST_YEARS = (2021, 2022, 2023, 2024, 2025)


def _csv_text(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_HEADERS)
    writer.writeheader()
    for r in rows:
        writer.writerow({h: r.get(h, "") for h in _CSV_HEADERS})
    return buf.getvalue().encode("utf-8")


def _row(year: int, hours: str = "1") -> dict:
    return {"Date": f"{year}-06-01", "Client": "Danone", "Project": "Soporte Danone",
            "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": hours}


def _csv_files(*, etags=None):
    """6 items — 1 actual (raíz, sin año) + 5 históricos (Times previos/),
    cada uno con `eTag`/`lastModifiedDateTime`/`size` (MISMOS campos que
    Graph ya trae en el listado de carpeta hoy, sin `$select` — ver AUDIT
    en HANDOFF §3). `etags`: dict opcional `{year: "etag-value"}` para
    forzar un valor específico (tests de invalidación); default
    determinístico `f"etag-{year}"`/`"etag-current"`."""
    etags = etags or {}
    current = {
        "id": "current-1", "name": "Time Logs Reports.csv",
        "eTag": etags.get("current", "etag-current"), "lastModifiedDateTime": "2026-08-01T00:00:00Z", "size": 1000,
    }
    root_files = [current]
    history_files = [
        {
            "id": f"hist-{y}", "name": f"Time Logs Reports {y}.csv",
            "eTag": etags.get(y, f"etag-{y}"), "lastModifiedDateTime": f"{y}-12-31T00:00:00Z", "size": 500,
        }
        for y in _HIST_YEARS
    ]
    return root_files, history_files


class _CallLog:
    def __init__(self):
        self.json = []
        self.paged = []
        self.bytes = []
        self._lock = threading.Lock()

    def add(self, bucket: str, url: str):
        with self._lock:
            getattr(self, bucket).append(url)


def _patch(monkeypatch, *, root_files, history_files, file_rows):
    """`file_rows`: dict item_id -> list[row-dict] (contenido ACTUAL del
    CSV en esta llamada — puede variar entre llamadas del mismo test para
    simular "el eTag no cambió pero el archivo sí" vs "el eTag cambió Y el
    archivo cambió", ver tests de invalidación)."""
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
        # Buscar `Times previos/` como carpeta hija de la raíz.
        return []

    def fake_bytes(url, access_token=None, **kw):
        log.add("bytes", url)
        for item_id, rows in file_rows().items():
            if f"/drive/items/{item_id}/content" in url:
                return _csv_text(rows)
        return _csv_text([])

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)
    return log


def _repo():
    return ReportingRepository(
        site_id=_SITE, horas_proyectos_list_id=_HP, proyectos_list_id=_PROY, cliente_list_id=_CLI,
        colaboradores_list_id=_COLAB, user_information_list_id=_USERINFO,
    )


def _default_file_rows():
    rows = {"current-1": [_row(2026)]}
    for y in _HIST_YEARS:
        rows[f"hist-{y}"] = [_row(y)]
    return rows


def _mark_history_folder(root_files):
    # El fixture de arriba necesita una carpeta "Times previos" en la raíz
    # para que `_list_time_logs_csv_items` la descubra por nombre — ver
    # ese método en reporting_repository.py.
    return root_files + [{"id": "folder-previos", "name": "Times previos", "folder": {"childCount": 5}}]


# ============================================================
# Primitivo TimeLogsHistoricalCache — aislado, sin Graph
# ============================================================

def test_cache_hit_after_first_compute():
    cache = TimeLogsHistoricalCache(max_entries=10)
    calls = []

    def compute():
        calls.append(1)
        return "value"

    v1, outcome1 = cache.get_or_compute(("s", "f", "v1"), compute)
    v2, outcome2 = cache.get_or_compute(("s", "f", "v1"), compute)
    assert v1 == v2 == "value"
    assert outcome1 == "computed"
    assert outcome2 == "hit"
    assert len(calls) == 1


def test_different_version_is_a_miss_not_a_hit():
    cache = TimeLogsHistoricalCache(max_entries=10)
    calls = []

    def compute():
        calls.append(1)
        return f"value-{len(calls)}"

    v1, _ = cache.get_or_compute(("s", "f", "v1"), compute)
    v2, outcome2 = cache.get_or_compute(("s", "f", "v2"), compute)
    assert v1 != v2
    assert outcome2 == "computed"
    assert len(calls) == 2


def test_single_flight_concurrent_calls_same_key_compute_once():
    cache = TimeLogsHistoricalCache(max_entries=10)
    started = threading.Event()
    release = threading.Event()
    call_count = {"n": 0}
    lock = threading.Lock()

    def compute():
        with lock:
            call_count["n"] += 1
        started.set()
        release.wait(timeout=2)
        return "value"

    results = []

    def worker():
        results.append(cache.get_or_compute(("s", "f", "v1"), compute))

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    assert started.wait(timeout=2)
    t2.start()  # arranca DESPUÉS de que compute() ya está en vuelo — debe esperar, nunca recomputar
    release.set()
    t1.join(timeout=2)
    t2.join(timeout=2)

    assert call_count["n"] == 1, "single-flight falló — compute() corrió más de una vez para la MISMA key"
    outcomes = sorted(o for _, o in results)
    assert outcomes == ["computed", "waited"]
    assert all(v == "value" for v, _ in results)


def test_different_keys_never_block_each_other():
    cache = TimeLogsHistoricalCache(max_entries=10)
    key_a_started = threading.Event()
    key_a_release = threading.Event()

    def compute_a():
        key_a_started.set()
        key_a_release.wait(timeout=2)
        return "a"

    def compute_b():
        return "b"

    t = threading.Thread(target=lambda: cache.get_or_compute(("s", "a", "v1"), compute_a))
    t.start()
    assert key_a_started.wait(timeout=2)
    # key "b" no debe bloquearse esperando a que "a" libere su lock — si
    # esto colgara, el test fallaría por timeout del propio pytest/thread.
    v_b, outcome_b = cache.get_or_compute(("s", "b", "v1"), compute_b)
    assert v_b == "b"
    assert outcome_b == "computed"
    key_a_release.set()
    t.join(timeout=2)


def test_lru_evicts_least_recently_used_beyond_max_entries():
    cache = TimeLogsHistoricalCache(max_entries=2)
    cache.get_or_compute(("s", "1", "v"), lambda: "1")
    cache.get_or_compute(("s", "2", "v"), lambda: "2")
    cache.get_or_compute(("s", "3", "v"), lambda: "3")  # desaloja "1" (el menos usado)
    assert len(cache) == 2

    calls = []
    _, outcome1 = cache.get_or_compute(("s", "1", "v"), lambda: calls.append(1) or "1-recomputed")
    _, outcome3 = cache.get_or_compute(("s", "3", "v"), lambda: calls.append(1) or "3-recomputed")
    assert outcome1 == "computed"  # "1" fue desalojado, tuvo que recomputar
    assert outcome3 == "hit"       # "3" seguía en cache


def test_exception_in_compute_propagates_and_does_not_poison_future_attempts():
    cache = TimeLogsHistoricalCache(max_entries=10)
    attempts = {"n": 0}

    def compute():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("falla transitoria")
        return "value"

    with pytest.raises(RuntimeError):
        cache.get_or_compute(("s", "f", "v1"), compute)
    # Un segundo intento para la MISMA key debe poder reintentar desde
    # cero — nunca quedar bloqueado por el fallo anterior.
    value, outcome = cache.get_or_compute(("s", "f", "v1"), compute)
    assert value == "value"
    assert outcome == "computed"
    assert attempts["n"] == 2


# ============================================================
# §13 CALL-BUDGET — 5 históricos + 1 actual
# ============================================================

def test_call_budget_cold_then_warm_then_one_etag_changed(monkeypatch):
    """1ra llamada (fría): 5 históricos + 1 actual. 2da llamada idéntica
    (tibia): 0 históricos (cache) + 1 actual (siempre fresco). 3ra llamada
    con UN eTag histórico cambiado (2023): 1 histórico + 1 actual — los
    otros 4 históricos siguen viniendo de cache. Cada tramo lee el log del
    `_patch` vigente en ESE tramo (cada `_patch` devuelve un `_CallLog`
    nuevo — no reusar el de un tramo anterior)."""
    fresh_cache = TimeLogsHistoricalCache()
    monkeypatch.setattr(repo_mod, "_TIME_LOGS_HISTORICAL_CACHE", fresh_cache)
    repo = _repo()

    root_files, history_files = _csv_files()
    root_files = _mark_history_folder(root_files)
    log1 = _patch(monkeypatch, root_files=root_files, history_files=history_files, file_rows=_default_file_rows)
    repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")
    d1 = [u for u in log1.bytes if "/content" in u]
    assert len(d1) == 6

    log2 = _patch(monkeypatch, root_files=root_files, history_files=history_files, file_rows=_default_file_rows)
    repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")
    d2 = [u for u in log2.bytes if "/content" in u]
    assert len(d2) == 1
    assert "current-1" in d2[0]

    root_files3, history_files3 = _csv_files(etags={2023: "etag-2023-v2"})
    root_files3 = _mark_history_folder(root_files3)
    log3 = _patch(monkeypatch, root_files=root_files3, history_files=history_files3, file_rows=_default_file_rows)
    repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")
    d3 = [u for u in log3.bytes if "/content" in u]
    assert len(d3) == 2, d3  # current (siempre) + hist-2023 (único eTag distinto)
    assert any("current-1" in u for u in d3)
    assert any("hist-2023" in u for u in d3)
    assert not any(f"hist-{y}" in u for y in (2021, 2022, 2024, 2025) for u in d3)


def test_current_file_is_never_cached_even_when_etag_unchanged(monkeypatch):
    fresh_cache = TimeLogsHistoricalCache()
    monkeypatch.setattr(repo_mod, "_TIME_LOGS_HISTORICAL_CACHE", fresh_cache)
    root_files, history_files = _csv_files()
    root_files = _mark_history_folder(root_files)
    repo = _repo()

    for _ in range(3):
        log = _patch(monkeypatch, root_files=root_files, history_files=history_files, file_rows=_default_file_rows)
        repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")
        current_downloads = [u for u in log.bytes if "/content" in u and "current-1" in u]
        assert len(current_downloads) == 1, "el archivo actual debe descargarse SIEMPRE, nunca cachearse"


# ============================================================
# §12 EQUIVALENCIA DE NEGOCIO — frío == tibio, invalidación real
# ============================================================

def test_cold_and_warm_results_are_business_identical(monkeypatch):
    fresh_cache = TimeLogsHistoricalCache()
    monkeypatch.setattr(repo_mod, "_TIME_LOGS_HISTORICAL_CACHE", fresh_cache)
    root_files, history_files = _csv_files()
    root_files = _mark_history_folder(root_files)
    _patch(monkeypatch, root_files=root_files, history_files=history_files, file_rows=_default_file_rows)
    repo = _repo()

    cold_result = repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")
    warm_result = repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")

    # Orden estable (mismo criterio que el merge de Fase 1A — orden
    # original de archivos), valores de negocio idénticos: proyecto
    # normalizado, año/mes, horas.
    assert cold_result == warm_result
    assert len(cold_result) == 6  # 1 fila x 6 archivos en este fixture
    assert {r["anio"] for r in cold_result} == {"2021", "2022", "2023", "2024", "2025", "2026"}
    assert all(r["proyecto"] == "Soporte Danone" for r in cold_result)
    assert all(r["horas"] == 1.0 for r in cold_result)


def test_etag_change_invalidates_and_new_data_appears(monkeypatch):
    fresh_cache = TimeLogsHistoricalCache()
    monkeypatch.setattr(repo_mod, "_TIME_LOGS_HISTORICAL_CACHE", fresh_cache)
    root_files, history_files = _csv_files()
    root_files = _mark_history_folder(root_files)
    _patch(monkeypatch, root_files=root_files, history_files=history_files, file_rows=_default_file_rows)
    repo = _repo()

    first = repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")
    hist_2023_hours_before = next(r["horas"] for r in first if r["anio"] == "2023")
    assert hist_2023_hours_before == 1.0

    # El archivo 2023 "cambia" en la fuente (nuevo dato real) Y su eTag
    # cambia — SIN esto último, la cache NO debería enterarse (ver test de
    # abajo). Acá SÍ cambia, así que el nuevo valor debe aparecer.
    def changed_rows():
        rows = _default_file_rows()
        rows["hist-2023"] = [_row(2023, hours="999")]
        return rows

    root_files2, history_files2 = _csv_files(etags={2023: "etag-2023-v2"})
    root_files2 = _mark_history_folder(root_files2)
    _patch(monkeypatch, root_files=root_files2, history_files=history_files2, file_rows=changed_rows)

    second = repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")
    hist_2023_hours_after = next(r["horas"] for r in second if r["anio"] == "2023")
    assert hist_2023_hours_after == 999.0, "eTag cambió pero la cache siguió sirviendo el valor viejo"
    # Los demás años, sin cambio de eTag, siguen viniendo de cache (mismo
    # valor exacto que la 1ra llamada).
    for y in (2021, 2022, 2024, 2025, 2026):
        before = next(r["horas"] for r in first if r["anio"] == str(y))
        after = next(r["horas"] for r in second if r["anio"] == str(y))
        assert before == after == 1.0


def test_source_change_without_etag_change_is_not_observed_stale_by_design(monkeypatch):
    """Documenta el contrato EXACTO (no un bug): si la fuente cambiara sus
    bytes SIN que Graph actualice `eTag`/`lastModifiedDateTime`/`size`
    (violación de la garantía que este corrective asume de Graph), la
    cache legítimamente sigue sirviendo el valor viejo — el mismo
    comportamiento que CUALQUIER cache versionada por eTag. Este test fija
    esa expectativa explícitamente, no la oculta."""
    fresh_cache = TimeLogsHistoricalCache()
    monkeypatch.setattr(repo_mod, "_TIME_LOGS_HISTORICAL_CACHE", fresh_cache)
    root_files, history_files = _csv_files()
    root_files = _mark_history_folder(root_files)
    _patch(monkeypatch, root_files=root_files, history_files=history_files, file_rows=_default_file_rows)
    repo = _repo()
    repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")

    def changed_rows_same_etag():
        rows = _default_file_rows()
        rows["hist-2023"] = [_row(2023, hours="999")]
        return rows

    # MISMOS root_files/history_files (mismo eTag) — solo el contenido
    # servido por `fake_bytes` cambia.
    _patch(monkeypatch, root_files=root_files, history_files=history_files, file_rows=changed_rows_same_etag)
    second = repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")
    hist_2023_hours = next(r["horas"] for r in second if r["anio"] == "2023")
    assert hist_2023_hours == 1.0  # sigue sirviendo el valor cacheado — eTag no cambió


# ============================================================
# §14 AISLAMIENTO DE AUTORIZACIÓN
# ============================================================

def test_scope_b_never_inherits_scope_a_cache_entries(monkeypatch):
    fresh_cache = TimeLogsHistoricalCache()
    monkeypatch.setattr(repo_mod, "_TIME_LOGS_HISTORICAL_CACHE", fresh_cache)
    root_files, history_files = _csv_files()
    root_files = _mark_history_folder(root_files)
    repo = _repo()

    log_a = _patch(monkeypatch, root_files=root_files, history_files=history_files, file_rows=_default_file_rows)
    repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")
    downloads_a = [u for u in log_a.bytes if "/content" in u]
    assert len(downloads_a) == 6  # cold para A

    # A de nuevo — warm, confirma que el cache SÍ funciona para A antes de
    # probar que B no lo hereda.
    log_a2 = _patch(monkeypatch, root_files=root_files, history_files=history_files, file_rows=_default_file_rows)
    repo.get_time_logs_worked_hours("tok", cache_scope="user-a@danwor.com")
    downloads_a2 = [u for u in log_a2.bytes if "/content" in u]
    assert len(downloads_a2) == 1  # solo el actual — históricos de A vinieron de cache

    # B, primera vez — MISMOS archivos/eTags que A, pero scope DISTINTO:
    # debe pagar el costo COMPLETO, cero filtración desde A.
    log_b = _patch(monkeypatch, root_files=root_files, history_files=history_files, file_rows=_default_file_rows)
    repo.get_time_logs_worked_hours("tok", cache_scope="user-b@danwor.com")
    downloads_b = [u for u in log_b.bytes if "/content" in u]
    assert len(downloads_b) == 6, "scope B heredó entradas de scope A — fuga de datos cross-user"


# ============================================================
# `cache_scope=None` (Mantenimiento/Rentabilidad/Batch B) — comportamiento
# EXACTO de siempre, cero caching, incluso con `_TIME_LOGS_HISTORICAL_
# CACHE` poblada por otro caller previo en el MISMO proceso.
# ============================================================

def test_cache_scope_none_never_reads_or_writes_the_cache(monkeypatch):
    fresh_cache = TimeLogsHistoricalCache()
    monkeypatch.setattr(repo_mod, "_TIME_LOGS_HISTORICAL_CACHE", fresh_cache)
    root_files, history_files = _csv_files()
    root_files = _mark_history_folder(root_files)
    repo = _repo()

    # Sin cache_scope: 2 llamadas idénticas SEGUIDAS deben pagar 6
    # descargas CADA VEZ — MISMO contrato que antes de Fase 2.
    log1 = _patch(monkeypatch, root_files=root_files, history_files=history_files, file_rows=_default_file_rows)
    repo.get_time_logs_worked_hours("tok")
    assert len([u for u in log1.bytes if "/content" in u]) == 6

    log2 = _patch(monkeypatch, root_files=root_files, history_files=history_files, file_rows=_default_file_rows)
    repo.get_time_logs_worked_hours("tok")
    assert len([u for u in log2.bytes if "/content" in u]) == 6
    assert len(fresh_cache) == 0, "cache_scope=None no debería haber escrito NADA en la cache compartida"
