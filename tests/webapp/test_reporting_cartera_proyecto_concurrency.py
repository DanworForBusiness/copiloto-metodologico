"""
test_reporting_cartera_proyecto_concurrency.py — REPORTING CARTERA/PROYECTO
PERFORMANCE CORRECTIVE, Fase 1 (owner decision, root cause confirmado por
timing real — ver HANDOFF):

    cartera_proyecto.facts       ~14.9s
    cartera_proyecto.time_logs   ~26.6s (6 descargas CSV SECUENCIALES, ~24s)
    cartera_proyecto.total       ~41.6s

Fase 1A: las descargas CSV de Time Logs (`_download_time_logs_rows`) pasan de
secuencial a un `ThreadPoolExecutor(max_workers=_TIME_LOGS_MAX_WORKERS=3)`
LOCAL a la llamada — cada archivo se descarga+parsea en su propio worker
(`_download_one_time_logs_file`), sin estado mutable compartido.

Fase 1B (RETIRADA — owner A/B isolation, medición real): `get_cartera_proyecto`
corrió brevemente `get_cartera_facts_with_project_dimension` y
`get_time_logs_worked_hours` en dos workers top-level (MISMO patrón que
`get_rentabilidad`, sin tocar) — la medición real combinada con Fase 1A dio
PEOR resultado que la versión 100% secuencial (facts 14.9s→50.2s, time_logs
26.6s→50.6s, CSV wall phase de Fase 1A solo 23.9s→47.4s con el MISMO pool de
3 workers): fuerte indicio de contención real contra Microsoft
365/SharePoint al combinar dos fuentes concurrentes, no un defecto de este
código. Fase 1B fue REMOVIDA permanentemente — Facts y Time Logs vuelven a
correr secuencial, en el thread que llamó a `get_cartera_proyecto` (nunca en
un pool). Fase 1A SÍ se mantiene: midió mejor en aislamiento (~29% Time
Logs, ~33% CSV wall download) — es una capa de concurrencia distinta (I/O
intra-fuente Time Logs, nunca fan-out cross-fuente).

Este archivo prueba (ningún assert de wall-clock — solo sincronización
determinística vía threading.Event/lock):
  A. las 6 descargas CSV de Time Logs se solapan en el tiempo (Fase 1A);
  B. como mucho 3 workers de CSV activos a la vez, y realmente se alcanzan 3;
  C. las ramas Facts/Time Logs corren SECUENCIAL, nunca solapadas (Fase 1B
     retirada) — ambas en el thread que llamó, sin pool;
  E. ningún worker de Fase 1A sobrevive/filtra threads tras una excepción;
  + presupuesto de llamadas remotas SIN CAMBIOS vs. la versión secuencial;
  + orden de merge determinístico (orden original de archivos, no orden de
    finalización);
  + contrato de error preservado (fail-soft por-archivo intacto, una
    excepción inesperada se propaga, prioridad Facts > Time Logs);
  + equivalencia de resultado contra una referencia secuencial (sin threads).
"""
from __future__ import annotations

import csv
import io
import threading
import time as _time_mod

import openpyxl
import pytest
from openpyxl.worksheet.table import Table

from webapp.services import reporting_repository as repo_mod
from webapp.services.reporting_repository import (
    ReportingGraphError,
    ReportingRepository,
    ReportingSourceError,
)
from webapp.services.reporting_service import ReportingService

_SITE, _OPS_SITE = "site-1", "ops-site-1"
_HP, _PROY, _CLI, _CP, _COLAB, _USERINFO = "hp-list", "proy-list", "cli-list", "cp-list", "colab-list", "userinfo-list"
_CSV_HEADERS = ["Date", "Client", "Project", "Task", "Email", "Duration Decimal"]
_CP_COLUMNS = [{"name": "Title"}, {"name": "ClientPartner", "lookup": {"listId": _CP}}]


def _items(rows):
    return [{"id": r.get("id", ""), "fields": r} for r in rows]


def _csv_text(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_HEADERS)
    writer.writeheader()
    for r in rows:
        writer.writerow({h: r.get(h, "") for h in _CSV_HEADERS})
    return buf.getvalue().encode("utf-8")


def _facturadas_xlsx(pairs):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Facturado"
    ws.append(["Proyecto Base", "Suma de Cantidad"])
    for proyecto_base, cantidad in pairs:
        ws.append([proyecto_base, cantidad])
    ws.add_table(Table(displayName="Tabla24", ref=f"A1:B{1 + len(pairs)}"))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _repo():
    return ReportingRepository(
        site_id=_SITE, horas_proyectos_list_id=_HP, proyectos_list_id=_PROY, cliente_list_id=_CLI,
        colaboradores_list_id=_COLAB, user_information_list_id=_USERINFO,
    )


def _repo_with_token():
    """MISMO repo, pero con `_token` ya fijado — `_download_time_logs_rows`
    es un método INTERNO que asume el token ya seteado por el método
    público (`get_time_logs_worked_hours`/`get_time_logs_facts`), MISMO
    invariante que el código de producción — nunca se llama en frío."""
    repo = _repo()
    repo._token = "tok"
    return repo


class _CallLog:
    def __init__(self):
        self.json = []
        self.paged = []
        self.bytes = []
        self._lock = threading.Lock()

    def add_json(self, url):
        with self._lock:
            self.json.append(url)

    def add_paged(self, url):
        with self._lock:
            self.paged.append(url)

    def add_bytes(self, url):
        with self._lock:
            self.bytes.append(url)


# csv_files: items de la RAÍZ de "Template Dashboard" (puede incluir la
# carpeta "Times previos"). subfolder_csv_files: dict folder_id -> items.
# file_rows: dict item_id -> filas CSV (o Exception para simular una falla
# de transporte por-archivo). on_download(item_id): hook opcional invocado
# DENTRO de fake_bytes (para tests de sincronización/concurrencia — thread-
# safe, cada worker llama con su propio item_id).
def _patch(
    monkeypatch, *, horas_por_proyectos, proyectos, clientes, client_partners, facturadas,
    csv_files, subfolder_csv_files, file_rows, on_download=None,
):
    log = _CallLog()

    def fake_json(url, access_token=None, **kw):
        log.add_json(url)
        if url == f"{repo_mod.GRAPH_BASE}/sites/{repo_mod.TIME_LOGS_SITE_PATH}":
            return {"id": _OPS_SITE}
        return {}

    def fake_paged(url, access_token=None, **kw):
        log.add_paged(url)
        if f"/sites/{_OPS_SITE}/drive/root:" in url and ":/children" in url:
            return csv_files
        for folder_id, items in (subfolder_csv_files or {}).items():
            if f"/sites/{_OPS_SITE}/drive/items/{folder_id}/children" in url:
                return items
        if "/columns" in url:
            return _CP_COLUMNS
        if _CP in url:
            return _items(client_partners or [])
        if _HP in url:
            return _items(horas_por_proyectos)
        if _PROY in url:
            return _items(proyectos)
        if _CLI in url:
            return _items(clientes)
        return []

    def fake_bytes(url, access_token=None, **kw):
        log.add_bytes(url)
        if "Horas Facturadas" in url or "facturadas" in url.lower():
            return facturadas
        for item_id, rows in file_rows.items():
            if f"/drive/items/{item_id}/content" in url:
                if on_download is not None:
                    on_download(item_id)
                if isinstance(rows, Exception):
                    raise rows
                return _csv_text(rows)
        return _csv_text([])

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)
    return log


_HORAS = [{
    "ProyectoLookupId": "p1", "EmpresaLookupId": "c1", "A_x00f1_o": "2026", "Mes": "Agosto",
    "HsTrabajadas": "10", "HsVendidas": "160", "HsFacturadas": "140",
}]
_PROYECTOS = [{
    "id": "p1", "Title": "Soporte Danone", "ID_x0020_Proyecto_x0020_Danwor": "DAN-001",
    "TipodeProyecto": "Proyecto", "FechadeInicio": "2026-01-15", "Status": "En curso",
}]
_CLIENTES = [{"id": "c1", "Title": "Danone", "ClientPartnerLookupId": "pm1"}]
_CLIENT_PARTNERS = [{"id": "pm1", "Title": "María López"}]
_FACTURADAS = _facturadas_xlsx([("Soporte Danone", 290)])

_SIX_CSV_FILES = [
    {"id": "current-1", "name": "Time Logs Reports.csv"},
    {"id": "folder-previos", "name": "Times previos", "folder": {"childCount": 5}},
]
_SIX_SUBFOLDER = {"folder-previos": [
    {"id": f"hist-{y}", "name": f"Time Logs Reports {y}.csv"} for y in (2021, 2022, 2023, 2024, 2025)
]}


def _six_file_rows(per_file_rows=1):
    rows = {}
    for fid in ["current-1", "hist-2021", "hist-2022", "hist-2023", "hist-2024", "hist-2025"]:
        rows[fid] = [
            {"Date": "2026-08-05", "Client": "Danone", "Project": "Soporte Danone",
             "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": str(1 + i)}
            for i in range(per_file_rows)
        ]
    return rows


# --- §7 CALL BUDGET: concurrencia NO agrega llamadas remotas ---------------

def test_call_budget_unchanged_by_bounded_concurrency(monkeypatch):
    log = _patch(
        monkeypatch, horas_por_proyectos=_HORAS, proyectos=_PROYECTOS, clientes=_CLIENTES,
        client_partners=_CLIENT_PARTNERS, facturadas=_FACTURADAS,
        csv_files=_SIX_CSV_FILES, subfolder_csv_files=_SIX_SUBFOLDER, file_rows=_six_file_rows(),
    )
    rows = ReportingService(_repo()).get_cartera_proyecto("tok")
    assert rows  # sanity — el fixture realmente produjo datos

    proyectos_reads = [u for u in log.paged if _PROY in u and "/columns" not in u]
    cliente_reads = [u for u in log.paged if _CLI in u and "/columns" not in u]
    columns_reads = [u for u in log.paged if "/columns" in u]
    horas_reads = [u for u in log.paged if _HP in u]
    cp_reads = [u for u in log.paged if _CP in u]
    assert len(proyectos_reads) == 1, proyectos_reads
    assert len(cliente_reads) == 1, cliente_reads
    assert len(columns_reads) == 1, columns_reads
    assert len(horas_reads) == 1, horas_reads
    assert len(cp_reads) == 1, cp_reads

    site_reads = [u for u in log.json if u == f"{repo_mod.GRAPH_BASE}/sites/{repo_mod.TIME_LOGS_SITE_PATH}"]
    assert len(site_reads) == 1, site_reads
    root_listing = [u for u in log.paged if "/drive/root:" in u and ":/children" in u]
    assert len(root_listing) == 1, root_listing
    previos_listing = [u for u in log.paged if "folder-previos" in u]
    assert len(previos_listing) == 1, previos_listing

    facturadas_downloads = [u for u in log.bytes if "Horas Facturadas" in u or "facturadas" in u.lower()]
    assert len(facturadas_downloads) == 1, facturadas_downloads
    csv_downloads = [u for u in log.bytes if "/content" in u and u not in facturadas_downloads]
    # exactamente 6 descargas — ni una repetida, ni una faltante — con el
    # pool acotado a 3 workers.
    assert len(csv_downloads) == 6, csv_downloads
    assert len({u for u in csv_downloads}) == 6, "descarga duplicada de un mismo archivo"


# --- §8.A/B — descargas CSV se solapan, tope de 3 concurrentes -------------

def test_csv_downloads_overlap_and_are_capped_at_three_workers(monkeypatch):
    active = {"count": 0, "max": 0}
    lock = threading.Lock()
    release = threading.Event()
    reached_three = threading.Event()

    def on_download(item_id):
        with lock:
            active["count"] += 1
            active["max"] = max(active["max"], active["count"])
            if active["count"] == 3:
                reached_three.set()
        # Bloquea hasta que el test confirme que 3 workers están en vuelo a
        # la vez — si el pool NO estuviera acotado a 3, un 4to archivo
        # podría arrancar antes de este release y violar el tope.
        released = reached_three.wait(timeout=2)
        assert released, "nunca se alcanzaron 3 descargas concurrentes — ¿el pool sigue secuencial?"
        with lock:
            active["count"] -= 1

    log = _patch(
        monkeypatch, horas_por_proyectos=_HORAS, proyectos=_PROYECTOS, clientes=_CLIENTES,
        client_partners=_CLIENT_PARTNERS, facturadas=_FACTURADAS,
        csv_files=_SIX_CSV_FILES, subfolder_csv_files=_SIX_SUBFOLDER, file_rows=_six_file_rows(),
        on_download=on_download,
    )
    rows = _repo_with_token()._download_time_logs_rows(_OPS_SITE)
    assert rows
    assert active["max"] == 3, f"se esperaban exactamente 3 workers concurrentes, se vieron {active['max']}"
    assert active["count"] == 0  # todos los workers liberaron correctamente


# --- PHASE 1B REJECTED (owner A/B measurement — ver HANDOFF): la
# concurrencia top-level Facts||TimeLogs midió PEOR en Graph/SharePoint
# real (contención: facts 14.9s→50.2s, time_logs 26.6s→50.6s, CSV wall
# phase 23.9s→47.4s con el MISMO pool de 3 workers de Fase 1A) — REMOVIDA
# permanentemente. Fase 1A (pool acotado de 3 workers para las descargas
# CSV DENTRO de Time Logs) se mantiene — es una capa de concurrencia
# distinta (I/O intra-fuente, no fan-out cross-fuente) y SÍ midió mejor en
# aislamiento (~29% Time Logs, ~33% CSV wall download). El test de abajo
# reemplaza al que exigía overlap Facts/TimeLogs (retirado) — ahora prueba
# lo contrario: que ambas ramas corren SECUENCIAL, Facts completo ANTES de
# que Time Logs arranque, nunca solapadas.

def test_facts_and_time_logs_branches_run_sequentially_never_overlap(monkeypatch):
    facts_started = threading.Event()
    facts_finished = threading.Event()
    time_logs_started = threading.Event()

    def fake_facts(self, access_token, *, include_facturadas, cache_scope=None):
        facts_started.set()
        result = [{
            "cliente": "Danone", "clientPartner": "María López", "proyecto": "Soporte Danone",
            "idProyecto": "DAN-001", "tipoProyecto": "Proyecto", "estado": "En curso",
            "anio": "2026", "mes": "Agosto", "anioInicio": "2026",
            "hsTrabajadas": 10.0, "hsVendidas": 160.0, "hsCertificadas": 140.0, "facturadasProyecto": 290.0,
        }], {}
        # Marca "facts terminó" en el ÚLTIMO instante que este fake controla
        # antes de devolver el control al caller secuencial — si Time Logs
        # llegara a arrancar en un thread separado ANTES de este punto
        # (Fase 1B), el evento seguiría sin setearse cuando lo observe.
        facts_finished.set()
        return result

    def fake_time_logs(self, access_token, *, hot_years, cache_scope=None):
        time_logs_started.set()
        # Si las ramas corrieran concurrentes (Fase 1B), `facts_finished`
        # podría no estar seteado todavía acá — con la topología secuencial
        # correcta, Facts YA retornó (y `fake_facts` ya volvió al caller)
        # antes de que Time Logs arranque siquiera.
        assert facts_finished.is_set(), "time_logs arrancó ANTES de que facts terminara — las ramas se solaparon"
        return [{"proyecto": "Soporte Danone", "anio": "2026", "mes": "2026-08", "horas": 132.4}]

    monkeypatch.setattr(ReportingRepository, "get_cartera_facts_with_project_dimension", fake_facts)
    monkeypatch.setattr(ReportingRepository, "get_time_logs_worked_hours", fake_time_logs)

    rows = ReportingService(_repo()).get_cartera_proyecto("tok")
    assert facts_started.is_set()
    assert time_logs_started.is_set()
    assert rows
    assert rows[0]["trabajadas"] == 132.4


def test_facts_and_time_logs_run_on_the_calling_thread_no_pool(monkeypatch):
    """Guard más fuerte todavía: sin `ThreadPoolExecutor` top-level, AMBAS
    ramas deben ejecutar en el MISMO thread que llamó a
    `get_cartera_proyecto` — nunca en un worker thread separado (lo que
    Fase 1B sí hacía)."""
    caller_thread = threading.current_thread().ident
    seen_threads = []

    def fake_facts(self, access_token, *, include_facturadas, cache_scope=None):
        seen_threads.append(threading.current_thread().ident)
        return [], {}

    def fake_time_logs(self, access_token, *, hot_years, cache_scope=None):
        seen_threads.append(threading.current_thread().ident)
        return []

    monkeypatch.setattr(ReportingRepository, "get_cartera_facts_with_project_dimension", fake_facts)
    monkeypatch.setattr(ReportingRepository, "get_time_logs_worked_hours", fake_time_logs)

    ReportingService(_repo()).get_cartera_proyecto("tok")
    assert seen_threads == [caller_thread, caller_thread]


# --- §8.E — ningún worker sobrevive/filtra threads tras una excepción -----

def test_no_thread_leak_after_worker_exception(monkeypatch):
    file_rows = _six_file_rows()
    file_rows["hist-2023"] = RuntimeError("fallo inesperado — no transporte, no fail-soft")

    _patch(
        monkeypatch, horas_por_proyectos=_HORAS, proyectos=_PROYECTOS, clientes=_CLIENTES,
        client_partners=_CLIENT_PARTNERS, facturadas=_FACTURADAS,
        csv_files=_SIX_CSV_FILES, subfolder_csv_files=_SIX_SUBFOLDER, file_rows=file_rows,
    )
    baseline = threading.active_count()
    with pytest.raises(RuntimeError):
        _repo_with_token()._download_time_logs_rows(_OPS_SITE)

    # El pool cierra sus workers al salir del `with` (incluso en excepción)
    # — se tolera un breve drenaje del SO, nunca un umbral de milisegundos:
    # polling generoso, no una aserción de wall-clock.
    for _ in range(50):
        if threading.active_count() <= baseline:
            break
        _time_mod.sleep(0.05)
    assert threading.active_count() <= baseline, "quedaron threads del pool vivos tras la excepción"


# --- §3 — orden de merge determinístico (orden original, no de llegada) ---

def test_csv_rows_merge_in_original_file_order_not_completion_order(monkeypatch):
    """Fuerza que el ÚLTIMO archivo (`hist-2025`) termine PRIMERO y el
    PRIMERO (`current-1`) termine ÚLTIMO — el merge final debe seguir
    reflejando el orden de `items` (root primero, luego Times previos en
    el orden que Graph los listó), nunca el orden de finalización real.

    Con `_TIME_LOGS_MAX_WORKERS=3`, solo los primeros 3 items del batch
    (`current-1`, `hist-2021`, `hist-2022`) arrancan de entrada — forzar
    que el ÚLTIMO item (`hist-2025`) termine antes que el PRIMERO
    (`current-1`) sería un deadlock (`hist-2025` no puede ni arrancar
    hasta que un worker del batch inicial libere su slot). En cambio, se
    fuerza el caso representativo y NUNCA deadlockeable: `current-1`
    (arranca primero) espera a que `hist-2022` (su PEER en el mismo batch
    inicial de 3) termine primero — completion order ≠ submission order —
    y se verifica que el merge final igual preserva el orden original."""
    finish_order = []
    lock = threading.Lock()
    hist_2022_done = threading.Event()

    def on_download(item_id):
        if item_id == "current-1":
            done = hist_2022_done.wait(timeout=2)
            assert done, "hist-2022 nunca terminó — no se pudo forzar el orden fuera de secuencia"
        with lock:
            finish_order.append(item_id)
        if item_id == "hist-2022":
            hist_2022_done.set()

    # Duration Decimal DISTINTO por archivo — permite reconstruir el orden
    # de MERGE final directamente desde `rows`, sin depender del orden de
    # `on_download` (que es el de FINALIZACIÓN, justamente lo que este test
    # prueba que NO determina el merge).
    file_rows = {
        "current-1": [{"Date": "2026-08-05", "Client": "Danone", "Project": "Soporte Danone",
                        "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "100"}],
        "hist-2021": [{"Date": "2021-08-05", "Client": "Danone", "Project": "Soporte Danone",
                        "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "210"}],
        "hist-2022": [{"Date": "2022-08-05", "Client": "Danone", "Project": "Soporte Danone",
                        "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "220"}],
        "hist-2023": [{"Date": "2023-08-05", "Client": "Danone", "Project": "Soporte Danone",
                        "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "230"}],
        "hist-2024": [{"Date": "2024-08-05", "Client": "Danone", "Project": "Soporte Danone",
                        "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "240"}],
        "hist-2025": [{"Date": "2025-08-05", "Client": "Danone", "Project": "Soporte Danone",
                        "Task": "Dev", "Email": "a@danwor.com", "Duration Decimal": "250"}],
    }
    _repo_with_token_holder = _repo_with_token()
    _patch(
        monkeypatch, horas_por_proyectos=_HORAS, proyectos=_PROYECTOS, clientes=_CLIENTES,
        client_partners=_CLIENT_PARTNERS, facturadas=_FACTURADAS,
        csv_files=_SIX_CSV_FILES, subfolder_csv_files=_SIX_SUBFOLDER,
        file_rows=file_rows, on_download=on_download,
    )
    rows = _repo_with_token_holder._download_time_logs_rows(_OPS_SITE)
    # Confirma que efectivamente se forzó un orden de finalización distinto
    # al de submission (hist-2022 termina ANTES que current-1, pese a que
    # current-1 arrancó primero).
    assert finish_order.index("hist-2022") < finish_order.index("current-1")
    # El orden de MERGE de las filas sigue siendo el orden original de
    # `items` (current-1, hist-2021..hist-2025), nunca el de finalización.
    assert [r["Duration Decimal"] for r in rows] == ["100", "210", "220", "230", "240", "250"]


# --- §4 ERROR CONTRACT — fail-soft por-archivo intacto bajo concurrencia --

def test_graph_error_on_one_file_is_skipped_fail_soft_others_still_combine(monkeypatch):
    file_rows = _six_file_rows()
    file_rows["hist-2024"] = ReportingGraphError(stage="time_logs_download", status_code=500)

    _patch(
        monkeypatch, horas_por_proyectos=_HORAS, proyectos=_PROYECTOS, clientes=_CLIENTES,
        client_partners=_CLIENT_PARTNERS, facturadas=_FACTURADAS,
        csv_files=_SIX_CSV_FILES, subfolder_csv_files=_SIX_SUBFOLDER, file_rows=file_rows,
    )
    rows = _repo_with_token()._download_time_logs_rows(_OPS_SITE)
    # 5 archivos válidos x 1 fila — el 6to (hist-2024) se saltea sin romper
    # el combine, MISMO criterio que la versión secuencial.
    assert len(rows) == 5


def test_unicode_decode_error_on_one_file_is_skipped_fail_soft(monkeypatch):
    file_rows = _six_file_rows()
    file_rows["hist-2021"] = UnicodeDecodeError("utf-8-sig", b"\xff\xfe", 0, 1, "bad bytes")

    _patch(
        monkeypatch, horas_por_proyectos=_HORAS, proyectos=_PROYECTOS, clientes=_CLIENTES,
        client_partners=_CLIENT_PARTNERS, facturadas=_FACTURADAS,
        csv_files=_SIX_CSV_FILES, subfolder_csv_files=_SIX_SUBFOLDER, file_rows=file_rows,
    )
    rows = _repo_with_token()._download_time_logs_rows(_OPS_SITE)
    assert len(rows) == 5


def test_unexpected_exception_propagates_not_swallowed(monkeypatch):
    file_rows = _six_file_rows()
    file_rows["current-1"] = KeyError("bug inesperado — nunca fail-soft")

    _patch(
        monkeypatch, horas_por_proyectos=_HORAS, proyectos=_PROYECTOS, clientes=_CLIENTES,
        client_partners=_CLIENT_PARTNERS, facturadas=_FACTURADAS,
        csv_files=_SIX_CSV_FILES, subfolder_csv_files=_SIX_SUBFOLDER, file_rows=file_rows,
    )
    with pytest.raises(KeyError):
        _repo_with_token()._download_time_logs_rows(_OPS_SITE)


def test_all_files_failing_still_raises_reporting_source_error(monkeypatch):
    file_rows = {
        fid: ReportingGraphError(stage="time_logs_download", status_code=500)
        for fid in ["current-1", "hist-2021", "hist-2022", "hist-2023", "hist-2024", "hist-2025"]
    }
    _patch(
        monkeypatch, horas_por_proyectos=_HORAS, proyectos=_PROYECTOS, clientes=_CLIENTES,
        client_partners=_CLIENT_PARTNERS, facturadas=_FACTURADAS,
        csv_files=_SIX_CSV_FILES, subfolder_csv_files=_SIX_SUBFOLDER, file_rows=file_rows,
    )
    with pytest.raises(ReportingSourceError):
        _repo_with_token()._download_time_logs_rows(_OPS_SITE)


# --- §5/ERROR PRIORITY — Facts observado siempre primero -------------------

def test_facts_failure_takes_priority_over_time_logs(monkeypatch):
    def fake_facts(self, access_token, *, include_facturadas, cache_scope=None):
        raise ReportingGraphError(stage="proyectos", status_code=500)

    def fake_time_logs(self, access_token, *, hot_years, cache_scope=None):
        raise ReportingGraphError(stage="time_logs_site", status_code=503)

    monkeypatch.setattr(ReportingRepository, "get_cartera_facts_with_project_dimension", fake_facts)
    monkeypatch.setattr(ReportingRepository, "get_time_logs_worked_hours", fake_time_logs)

    with pytest.raises(ReportingGraphError) as exc_info:
        ReportingService(_repo()).get_cartera_proyecto("tok")
    assert exc_info.value.stage == "proyectos"
    assert exc_info.value.status_code == 500


def test_facts_ok_time_logs_failure_propagates_time_logs_exception(monkeypatch):
    def fake_facts(self, access_token, *, include_facturadas, cache_scope=None):
        return [], {}

    def fake_time_logs(self, access_token, *, hot_years, cache_scope=None):
        raise ReportingGraphError(stage="time_logs_site", status_code=503)

    monkeypatch.setattr(ReportingRepository, "get_cartera_facts_with_project_dimension", fake_facts)
    monkeypatch.setattr(ReportingRepository, "get_time_logs_worked_hours", fake_time_logs)

    with pytest.raises(ReportingGraphError) as exc_info:
        ReportingService(_repo()).get_cartera_proyecto("tok")
    assert exc_info.value.stage == "time_logs_site"
    assert exc_info.value.status_code == 503


# --- §6 EQUIVALENCIA — resultado idéntico contra referencia secuencial ----

def _sequential_reference():
    """MISMO shaping de `get_cartera_proyecto`, invocado con mocks triviales
    sin threads — el payload concurrente de arriba debe coincidir EXACTO."""
    import unittest.mock as mock

    def fake_facts(self, access_token, *, include_facturadas, cache_scope=None):
        return [{
            "cliente": "Danone", "clientPartner": "María López", "proyecto": "Soporte Danone",
            "idProyecto": "DAN-001", "tipoProyecto": "Proyecto", "estado": "En curso",
            "anio": "2026", "mes": "Agosto", "anioInicio": "2026",
            "hsTrabajadas": 10.0, "hsVendidas": 160.0, "hsCertificadas": 140.0, "facturadasProyecto": 290.0,
        }], {}

    def fake_time_logs(self, access_token, *, hot_years, cache_scope=None):
        return [
            {"proyecto": "Soporte Danone", "anio": "2026", "mes": "2026-08", "horas": 100.0},
            {"proyecto": "Soporte Danone", "anio": "2025", "mes": "2025-12", "horas": 32.4},
        ]

    with mock.patch.object(ReportingRepository, "get_cartera_facts_with_project_dimension", fake_facts), \
         mock.patch.object(ReportingRepository, "get_time_logs_worked_hours", fake_time_logs):
        return ReportingService(_repo()).get_cartera_proyecto("tok")


def test_result_payload_identical_to_sequential_reference(monkeypatch):
    def fake_facts(self, access_token, *, include_facturadas, cache_scope=None):
        return [{
            "cliente": "Danone", "clientPartner": "María López", "proyecto": "Soporte Danone",
            "idProyecto": "DAN-001", "tipoProyecto": "Proyecto", "estado": "En curso",
            "anio": "2026", "mes": "Agosto", "anioInicio": "2026",
            "hsTrabajadas": 10.0, "hsVendidas": 160.0, "hsCertificadas": 140.0, "facturadasProyecto": 290.0,
        }], {}

    def fake_time_logs(self, access_token, *, hot_years, cache_scope=None):
        return [
            {"proyecto": "Soporte Danone", "anio": "2026", "mes": "2026-08", "horas": 100.0},
            {"proyecto": "Soporte Danone", "anio": "2025", "mes": "2025-12", "horas": 32.4},
        ]

    monkeypatch.setattr(ReportingRepository, "get_cartera_facts_with_project_dimension", fake_facts)
    monkeypatch.setattr(ReportingRepository, "get_time_logs_worked_hours", fake_time_logs)

    concurrent_result = ReportingService(_repo()).get_cartera_proyecto("tok")
    sequential_result = _sequential_reference()
    assert concurrent_result == sequential_result
    # sanity — campos de negocio reales presentes y coincidentes.
    row = concurrent_result[0]
    assert row["proyecto"] == "Soporte Danone"
    assert row["cliente"] == "Danone"
    assert row["idProyecto"] == "DAN-001"
    assert row["status"] == "En curso"
    assert row["anioInicio"] == "2026"
    assert row["certificadas"] == 140.0
    assert row["vendidas"] == 160.0
    assert row["facturadas"] == 290.0
    assert row["trabajadas"] == 132.4
    assert row["diff"] == 7.6  # certificadas(140.0) - trabajadas(132.4), redondeado a 2 decimales


def test_end_to_end_result_from_real_repository_call_graph_matches_manual_expectation(monkeypatch):
    """MISMO test que el call-budget de arriba, pero validando el payload de
    negocio de punta a punta (repository real + Graph mockeado, no los
    métodos del repository parcheados) — confirma que la concurrencia no
    alteró ningún valor a lo largo de todo el call graph real."""
    _patch(
        monkeypatch, horas_por_proyectos=_HORAS, proyectos=_PROYECTOS, clientes=_CLIENTES,
        client_partners=_CLIENT_PARTNERS, facturadas=_FACTURADAS,
        csv_files=_SIX_CSV_FILES, subfolder_csv_files=_SIX_SUBFOLDER, file_rows=_six_file_rows(per_file_rows=2),
    )
    rows = ReportingService(_repo()).get_cartera_proyecto("tok")
    assert len(rows) == 1
    row = rows[0]
    assert row["proyecto"] == "Soporte Danone"
    assert row["cliente"] == "Danone"
    assert row["certificadas"] == 140.0
    assert row["vendidas"] == 160.0
    assert row["facturadas"] == 290.0
    # 6 archivos x 2 filas (Duration Decimal "1"+"2") = 6 x (1+2) = 18.0hs
    assert row["trabajadas"] == 18.0
