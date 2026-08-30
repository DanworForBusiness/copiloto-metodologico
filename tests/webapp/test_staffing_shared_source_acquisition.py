"""
test_staffing_shared_source_acquisition.py — STAFFING PERFORMANCE / SHARED
SOURCE ACQUISITION CORRECTIVE (owner ticket, ver HANDOFF).

Owner-measured baseline (una sola sesión de servidor): Initial ~7.7s
mediana/8.45s última corrida; Historial 9.87s (~4125ms de eso es puramente
releer Proyectos/Colaboradores/Cliente ya adquiridos en Initial); Ausencias
~2.46s última/~3.3-3.6s típica previa. Causa raíz: Proyectos/Colaboradores/
Cliente se releen COMPLETOS en cada flujo (Initial→Historial→Ausencias), y
el XLSX de Ausencias se descarga en cada activación aunque no cambió.

Este corrective enruta Proyectos/Colaboradores/Cliente por el MISMO
`DeltaCache` ya probado en Reporting (instancia PROPIA de Staffing,
`staffing_repository._STAFFING_DELTA_CACHE` — nunca compartida con
`reporting_repository._DELTA_CACHE`, ver el comentario extenso junto a esa
constante) y el XLSX de Ausencias por `TimeLogsHistoricalCache` (instancia
propia, `_STAFFING_XLSX_CACHE`). Cubre SOLO integración/equivalencia/
wiring específico de Staffing (§12 del ticket, A-K, M) — deliberadamente
NO duplica la matriz de bajo nivel de `DeltaCache`/`TimeLogsHistoricalCache`
(single-flight interno, LRU, atomicidad de `sync_fn`), ya comprobada
exhaustivamente por la suite de Reporting
(test_reporting_horas_delta_sync.py,
test_reporting_proyectos_cliente_client_partner_delta.py,
test_reporting_delta_transient_fallback.py,
test_reporting_time_logs_historical_cache.py). La cobertura de ruta HTTP
real (§12.L) vive en test_staffing_shared_source_acquisition_route_wiring.py
— archivo separado, mismo motivo que la serie de Horas Vendidas: un test a
través del boundary HTTP real es la única prueba que "la optimización no
puede quedar silenciosamente inactiva en producción" (lección explícita del
ticket, ver esa investigación).

§12.N (semántica de vacaciones/ausencias no debe regresionar) se cubre
dejando la suite EXISTENTE de Ausencias corriendo tal cual
(test_staffing_absence_repository.py/test_staffing_absence_service.py/
test_staffing_absence_routes.py, sin un solo cambio de comportamiento) —
nunca una segunda implementación de esas pruebas acá."""
from __future__ import annotations

import io
import threading

import openpyxl
import pytest

from webapp.services import staffing_repository as repo_mod
from webapp.services.reporting_delta_cache import DeltaCache
from webapp.services.reporting_time_logs_cache import TimeLogsHistoricalCache
from webapp.services.staffing_repository import (
    StaffingFieldMapping, StaffingGraphError, StaffingRepository,
)

_SITE = "gestion-site-1"
_ASIG, _PROY, _COLAB, _CLI = "asig-list", "proy-list", "colab-list", "cli-list"
_OPS_SITE_ID = "ops-site-1"

_MAPPING = StaffingFieldMapping(
    colaborador_lookup_id="ColaboradorLookupId",
    proyecto_lookup_id="ProyectoLookupId",
    allocation="Horaspormes",
    status="Status",
    start_date="Fechainicio",
    end_date="FechaFin",
    proyecto_title="Title",
    colaborador_title="Title",
    colaborador_disponibilidad="Disponibilidad",
    proyecto_empresa_lookup="EmpresaLookupId",
    cliente_title="ClienteNombre",
)


@pytest.fixture(autouse=True)
def _isolate_staffing_caches(monkeypatch):
    """Instancias FRESCAS por test — nunca estado compartido entre casos
    (MISMO criterio que `test_reporting_horas_vendidas_delta_reuse.py`)."""
    monkeypatch.setattr(repo_mod, "_STAFFING_DELTA_CACHE", DeltaCache())
    monkeypatch.setattr(repo_mod, "_STAFFING_XLSX_CACHE", TimeLogsHistoricalCache())


def _make_repo() -> StaffingRepository:
    return StaffingRepository(
        site_id=_SITE, asignaciones_list_id=_ASIG, proyectos_list_id=_PROY,
        colaboradores_list_id=_COLAB, cliente_list_id=_CLI, field_mapping=_MAPPING,
    )


def _asig_row(item_id, *, colaborador="col1", proyecto="p1", status="Activo"):
    return {"id": item_id, "fields": {
        "ColaboradorLookupId": colaborador, "ProyectoLookupId": proyecto,
        "Horaspormes": 40, "Status": status, "Fechainicio": "2026-01-01", "FechaFin": "2026-12-31",
    }}


def _proy_row(item_id, *, title="Proyecto Alfa", empresa="cli1"):
    return {"id": item_id, "fields": {"Title": title, "EmpresaLookupId": empresa}}


def _colab_row(item_id, *, title="Ana Pérez", disponibilidad="Full Time"):
    return {"id": item_id, "fields": {"Title": title, "Disponibilidad": disponibilidad}}


def _cli_row(item_id, *, title="Cliente Uno"):
    return {"id": item_id, "fields": {"ClienteNombre": title}}


def _build_workbook_bytes(rows: list[tuple]) -> bytes:
    """`rows`: tuplas (Apellido, Nombre, Motivo, Desde, Hasta, Estado, Días)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = repo_mod.ABSENCE_WORKSHEET_NAME
    ws.append(["Apellido", "Nombre", "Motivo", "Desde", "Hasta", "Estado", "Días"])
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_WORKBOOK_APROBADA = _build_workbook_bytes([
    ("Pérez", "Ana", "Vacaciones", "01/03/2026", "05/03/2026", "Aprobada", 5),
])
_WORKBOOK_APROBADA_V2 = _build_workbook_bytes([
    ("Pérez", "Ana", "Vacaciones", "01/03/2026", "07/03/2026", "Aprobada", 7),
])


class _FakeTransport:
    """MISMO patrón self-perpetuante que
    `test_reporting_horas_vendidas_route_wiring.py::_FakeTransport` —
    distingue llamadas delta (`graph_get_json` a `/items/delta`) de
    llamadas legacy (`graph_get_paged_values`), y llamadas de Ausencias
    (`graph_get_json` a `/sites/...`/metadata, `graph_get_bytes` a
    `:content`)."""

    def __init__(self):
        self.json_calls: list[str] = []
        self.paged_calls: list[str] = []
        self.bytes_calls: list[str] = []
        self._delta_responses: dict[str, dict] = {}
        self._legacy_responses: dict[str, list] = {}
        self.xlsx_metadata: dict | None = {"id": "xlsx-1", "eTag": "etag-v1"}
        self.xlsx_bytes: bytes = _WORKBOOK_APROBADA
        self._fail_json_urls: set[str] = set()
        self._transient_fail_once: set[str] = set()

    def seed_delta_source(self, list_id, select_fields, items):
        url = repo_mod._list_items_delta_url(_SITE, list_id, select_fields)
        delta_link = f"{url}&dl=v1"
        self._delta_responses[url] = {"value": items, "@odata.deltaLink": delta_link}
        self._delta_responses[delta_link] = {"value": [], "@odata.deltaLink": delta_link}
        return url, delta_link

    def advance_delta_checkpoint(self, current_link, *, changes=None, next_delta_suffix="v2"):
        """Simula un update/delete: la respuesta del checkpoint ACTUAL trae
        `changes` (items nuevos/`@removed`) y un NUEVO deltaLink
        self-perpetuante."""
        new_link = current_link.rsplit("&dl=", 1)[0] + f"&dl={next_delta_suffix}"
        self._delta_responses[current_link] = {"value": changes or [], "@odata.deltaLink": new_link}
        self._delta_responses[new_link] = {"value": [], "@odata.deltaLink": new_link}
        return new_link

    def seed_legacy(self, url, items):
        self._legacy_responses[url] = items

    def fail_json_once(self, url):
        self._transient_fail_once.add(url)

    def install(self, monkeypatch):
        def fake_json(url, access_token=None, **kw):
            self.json_calls.append(url)
            if url in self._transient_fail_once:
                self._transient_fail_once.discard(url)
                raise TimeoutError("simulated read timeout")
            if url in self._delta_responses:
                return self._delta_responses[url]
            if url == f"{repo_mod.GRAPH_BASE}/sites/{repo_mod.ABSENCE_SITE_PATH}":
                return {"id": _OPS_SITE_ID}
            if "/drive/root:/" in url and ":/content" not in url:
                return self.xlsx_metadata
            raise AssertionError(f"URL JSON no programada: {url}")

        def fake_paged(url, access_token=None, extra_headers=None, on_page=None, **kw):
            self.paged_calls.append(url)
            if url in self._legacy_responses:
                items = self._legacy_responses[url]
            elif _ASIG in url:
                items = self._legacy_responses.get("__asignaciones__", [])
            else:
                raise AssertionError(f"URL paginada no programada: {url}")
            if on_page:
                on_page(len(items))
            return items

        def fake_bytes(url, access_token=None, **kw):
            self.bytes_calls.append(url)
            return self.xlsx_bytes

        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
        monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)


def _seeded_transport():
    t = _FakeTransport()
    t.seed_delta_source(_PROY, [_MAPPING.proyecto_title, _MAPPING.proyecto_empresa_lookup], [_proy_row("p1")])
    t.seed_delta_source(_COLAB, [_MAPPING.colaborador_title, _MAPPING.colaborador_disponibilidad], [_colab_row("col1")])
    t.seed_delta_source(_CLI, [_MAPPING.cliente_title], [_cli_row("cli1")])
    asig_filter_url = repo_mod._list_items_url(
        _SITE, _ASIG,
        filter_expr=f"fields/{_MAPPING.status} ne '{repo_mod.INACTIVE_STATUS_VALUE}'",
    )
    t.seed_legacy(asig_filter_url, [_asig_row("a1")])
    t._legacy_responses["__asignaciones__"] = [_asig_row("a1")]
    # Camino LEGACY (cache_scope=None) de las 3 dimensiones — MISMO select
    # que su contraparte delta (el select es idéntico, solo cambia el
    # endpoint `/items` vs. `/items/delta`), para que
    # `test_business_equivalence_*` pueda ejercitar `cache_scope=None` con
    # el MISMO transporte fixture.
    t.seed_legacy(
        repo_mod._list_items_url(_SITE, _PROY, [_MAPPING.proyecto_title, _MAPPING.proyecto_empresa_lookup]),
        [_proy_row("p1")],
    )
    t.seed_legacy(
        repo_mod._list_items_url(_SITE, _COLAB, [_MAPPING.colaborador_title, _MAPPING.colaborador_disponibilidad]),
        [_colab_row("col1")],
    )
    t.seed_legacy(repo_mod._list_items_url(_SITE, _CLI, [_MAPPING.cliente_title]), [_cli_row("cli1")])
    return t


# ============================================================================
# §12.A — Initial cold: cada dimensión se sincroniza UNA vez
# ============================================================================

def test_a_initial_cold_builds_each_shared_dimension_once(monkeypatch):
    repo = _make_repo()
    transport = _seeded_transport()
    transport.install(monkeypatch)

    records = repo.get_allocations("tok", cache_scope="scope-a")

    assert len(records) == 1
    assert records[0].project_name == "Proyecto Alfa"
    assert records[0].consultant_name == "Ana Pérez"
    assert records[0].client_name == "Cliente Uno"
    diag = repo.last_diagnostics
    assert diag["proyectos"]["acquisition"] == "delta_cold"
    assert diag["colaboradores"]["acquisition"] == "delta_cold"
    assert diag["cliente"]["acquisition"] == "delta_cold"
    # Cold sync de cada fuente ⇒ exactamente 1 llamada delta por fuente
    # (single page, sin @odata.nextLink en el fixture).
    proy_cold = repo_mod._list_items_delta_url(_SITE, _PROY, [_MAPPING.proyecto_title, _MAPPING.proyecto_empresa_lookup])
    assert transport.json_calls.count(proy_cold) == 1


# ============================================================================
# §12.B — Historial tras Initial NO relee Proyectos/Colaboradores/Cliente
# ============================================================================

def test_b_history_after_initial_does_not_full_reread_shared_dimensions(monkeypatch):
    repo = _make_repo()
    transport = _seeded_transport()
    transport.install(monkeypatch)
    inactive_filter_url = repo_mod._list_items_url(
        _SITE, _ASIG, filter_expr=f"fields/{_MAPPING.status} eq '{repo_mod.INACTIVE_STATUS_VALUE}'",
    )
    transport.seed_legacy(inactive_filter_url, [_asig_row("a2", status="Inactivo")])

    repo.get_allocations("tok", cache_scope="scope-a")  # Initial
    transport.json_calls.clear()

    repo.get_allocations("tok", assignment_scope="historical_inactive", cache_scope="scope-a")  # Historial

    diag = repo.last_diagnostics
    assert diag["proyectos"]["acquisition"] == "delta_warm"
    assert diag["colaboradores"]["acquisition"] == "delta_warm"
    assert diag["cliente"]["acquisition"] == "delta_warm"
    # Ningún cold sync (`/items/delta` SIN `&dl=`) se repite — solo el
    # checkpoint (`&dl=v1`) de cada fuente.
    proy_cold = repo_mod._list_items_delta_url(_SITE, _PROY, [_MAPPING.proyecto_title, _MAPPING.proyecto_empresa_lookup])
    assert proy_cold not in transport.json_calls
    colab_cold = repo_mod._list_items_delta_url(
        _SITE, _COLAB, [_MAPPING.colaborador_title, _MAPPING.colaborador_disponibilidad],
    )
    assert colab_cold not in transport.json_calls
    cli_cold = repo_mod._list_items_delta_url(_SITE, _CLI, [_MAPPING.cliente_title])
    assert cli_cold not in transport.json_calls


# ============================================================================
# §12.C — Ausencias tras Initial NO relee Colaboradores completo
# ============================================================================

def test_c_absences_after_initial_reuses_colaboradores_state(monkeypatch):
    repo = _make_repo()
    transport = _seeded_transport()
    transport.install(monkeypatch)

    repo.get_allocations("tok", cache_scope="scope-a")  # Initial calienta Colaboradores
    transport.json_calls.clear()

    consultants = repo.get_consultants("tok", cache_scope="scope-a")

    assert consultants == [("col1", "Ana Pérez")]
    assert repo.last_diagnostics["colaboradores_identity"]["acquisition"] == "delta_warm"
    colab_cold = repo_mod._list_items_delta_url(
        _SITE, _COLAB, [_MAPPING.colaborador_title, _MAPPING.colaborador_disponibilidad],
    )
    assert colab_cold not in transport.json_calls


# ============================================================================
# §12.D/E/F — XLSX de Ausencias: eTag cache
# ============================================================================

def test_d_absence_xlsx_cold_one_metadata_check_and_one_download(monkeypatch):
    repo = _make_repo()
    transport = _seeded_transport()
    transport.install(monkeypatch)

    periods = repo.get_absences("tok", cache_scope="scope-a")

    assert len(periods) == 1 and periods[0].consultant_name == "Ana Pérez"
    assert repo.last_diagnostics["ausencias"]["workbook_hit"] is False
    assert len(transport.bytes_calls) == 1
    metadata_calls = [u for u in transport.json_calls if "/drive/root:/" in u and ":/content" not in u]
    assert len(metadata_calls) == 1


def test_e_absence_xlsx_warm_unchanged_zero_content_downloads(monkeypatch):
    repo = _make_repo()
    transport = _seeded_transport()
    transport.install(monkeypatch)

    repo.get_absences("tok", cache_scope="scope-a")
    transport.bytes_calls.clear()
    transport.json_calls.clear()

    periods = repo.get_absences("tok", cache_scope="scope-a")

    assert len(periods) == 1
    assert repo.last_diagnostics["ausencias"]["workbook_hit"] is True
    assert transport.bytes_calls == []  # CERO descargas de contenido
    metadata_calls = [u for u in transport.json_calls if "/drive/root:/" in u and ":/content" not in u]
    assert len(metadata_calls) == 1  # el chequeo de versión SÍ ocurre siempre


def test_f_absence_xlsx_etag_change_triggers_exactly_one_new_download(monkeypatch):
    repo = _make_repo()
    transport = _seeded_transport()
    transport.install(monkeypatch)

    repo.get_absences("tok", cache_scope="scope-a")
    transport.bytes_calls.clear()

    transport.xlsx_metadata = {"id": "xlsx-1", "eTag": "etag-v2"}
    transport.xlsx_bytes = _WORKBOOK_APROBADA_V2

    periods = repo.get_absences("tok", cache_scope="scope-a")

    assert len(transport.bytes_calls) == 1
    assert repo.last_diagnostics["ausencias"]["workbook_hit"] is False
    assert periods[0].end_date == "2026-03-07"  # el dato cambiado SÍ surge


# ============================================================================
# §12.G — insert/update/delete visibles para todos los consumidores
# ============================================================================

def test_g_delta_update_and_delete_visible_to_every_consumer(monkeypatch):
    repo = _make_repo()
    transport = _seeded_transport()
    transport.install(monkeypatch)

    repo.get_allocations("tok", cache_scope="scope-a")  # Initial: p1 "Proyecto Alfa"

    proy_cold = repo_mod._list_items_delta_url(_SITE, _PROY, [_MAPPING.proyecto_title, _MAPPING.proyecto_empresa_lookup])
    checkpoint = transport._delta_responses[proy_cold]["@odata.deltaLink"]
    transport.advance_delta_checkpoint(
        checkpoint, changes=[_proy_row("p1", title="Proyecto Alfa Renombrado")],
    )

    inactive_filter_url = repo_mod._list_items_url(
        _SITE, _ASIG, filter_expr=f"fields/{_MAPPING.status} eq '{repo_mod.INACTIVE_STATUS_VALUE}'",
    )
    transport.seed_legacy(inactive_filter_url, [_asig_row("a1", status="Activo")])
    records = repo.get_allocations("tok", cache_scope="scope-a")  # segunda llamada: warm + update
    assert records[0].project_name == "Proyecto Alfa Renombrado"

    checkpoint2 = transport._delta_responses[proy_cold]  # nunca reusado, solo referencia
    # Delete: el próximo checkpoint remueve p1 (@removed).
    latest_link = None
    for url, payload in transport._delta_responses.items():
        if payload.get("value") == [] and "dl=v2" in url:
            latest_link = url
    assert latest_link is not None
    transport.advance_delta_checkpoint(latest_link, changes=[{"id": "p1", "@removed": {"reason": "deleted"}}], next_delta_suffix="v3")
    records = repo.get_allocations("tok", cache_scope="scope-a")
    assert records[0].project_name is None  # el proyecto ya no resuelve tras el delete


# ============================================================================
# §12.H — 410 dispara UN resync controlado
# ============================================================================

def test_h_410_triggers_controlled_resync(monkeypatch):
    repo = _make_repo()
    transport = _seeded_transport()
    transport.install(monkeypatch)

    repo.get_allocations("tok", cache_scope="scope-a")
    proy_cold = repo_mod._list_items_delta_url(_SITE, _PROY, [_MAPPING.proyecto_title, _MAPPING.proyecto_empresa_lookup])
    checkpoint = transport._delta_responses[proy_cold]["@odata.deltaLink"]

    import urllib.error

    def fake_json_410(url, access_token=None, **kw):
        transport.json_calls.append(url)
        if url == checkpoint:
            raise urllib.error.HTTPError(url, 410, "Gone", None, None)
        if url in transport._delta_responses:
            return transport._delta_responses[url]
        if url == f"{repo_mod.GRAPH_BASE}/sites/{repo_mod.ABSENCE_SITE_PATH}":
            return {"id": _OPS_SITE_ID}
        raise AssertionError(f"URL no programada: {url}")

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json_410)

    records = repo.get_allocations("tok", cache_scope="scope-a")
    assert records[0].project_name == "Proyecto Alfa"  # resync exitoso, mismo dato
    assert repo.last_diagnostics["proyectos"]["acquisition"] == "delta_cold"  # resync se etiqueta cold


# ============================================================================
# §12.I — falla transitoria cae a legacy SIN perder el checkpoint
# ============================================================================

def test_i_transient_failure_falls_back_to_legacy_preserving_checkpoint(monkeypatch):
    repo = _make_repo()
    transport = _seeded_transport()
    transport.install(monkeypatch)

    proy_cold = repo_mod._list_items_delta_url(_SITE, _PROY, [_MAPPING.proyecto_title, _MAPPING.proyecto_empresa_lookup])
    transport.seed_legacy(
        repo_mod._list_items_url(_SITE, _PROY, [_MAPPING.proyecto_title, _MAPPING.proyecto_empresa_lookup]),
        [_proy_row("p1", title="Proyecto Alfa Legacy Fallback")],
    )

    repo.get_allocations("tok", cache_scope="scope-a")  # Initial: calienta el checkpoint

    # Simula un timeout de lectura en el checkpoint warm.
    checkpoint = transport._delta_responses[proy_cold]["@odata.deltaLink"]
    transport.fail_json_once(checkpoint)

    records = repo.get_allocations("tok", cache_scope="scope-a")
    assert records[0].project_name == "Proyecto Alfa Legacy Fallback"  # fallback corrió
    assert repo.last_diagnostics["proyectos"]["acquisition"] == "delta_fallback"

    # El checkpoint cacheado NO se corrompió: la SIGUIENTE llamada vuelve a
    # warm sync normal (no un segundo cold sync).
    transport.json_calls.clear()
    repo.get_allocations("tok", cache_scope="scope-a")
    assert repo.last_diagnostics["proyectos"]["acquisition"] == "delta_warm"
    assert proy_cold not in transport.json_calls


# ============================================================================
# §12.J — aislamiento de scope
# ============================================================================

def test_j_cache_scope_isolation_between_users(monkeypatch):
    repo = _make_repo()
    transport = _seeded_transport()
    transport.install(monkeypatch)

    repo.get_allocations("tok", cache_scope="scope-a")
    proy_cold = repo_mod._list_items_delta_url(_SITE, _PROY, [_MAPPING.proyecto_title, _MAPPING.proyecto_empresa_lookup])
    assert transport.json_calls.count(proy_cold) == 1

    transport.json_calls.clear()
    repo.get_allocations("tok", cache_scope="scope-b")
    # scope-b paga SU propio cold sync — nunca hereda el de scope-a.
    assert transport.json_calls.count(proy_cold) == 1


# ============================================================================
# §12.K — consumidores concurrentes de la MISMA fuente fría: single-flight
# ============================================================================

def test_k_simultaneous_cold_consumers_single_flight(monkeypatch):
    repo = _make_repo()
    transport = _seeded_transport()

    proy_cold = repo_mod._list_items_delta_url(_SITE, _PROY, [_MAPPING.proyecto_title, _MAPPING.proyecto_empresa_lookup])
    start_gate = threading.Event()
    call_count = {"n": 0}
    lock = threading.Lock()

    def fake_json(url, access_token=None, **kw):
        if url == proy_cold:
            with lock:
                call_count["n"] += 1
            start_gate.wait(timeout=2)  # fuerza superposición real entre threads
        if url in transport._delta_responses:
            return transport._delta_responses[url]
        raise AssertionError(f"URL no programada: {url}")

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(
        repo_mod.microsoft365, "graph_get_paged_values",
        lambda url, access_token=None, extra_headers=None, on_page=None, **kw: (
            [_asig_row("a1")] if _ASIG in url else []
        ),
    )

    results = []

    def worker():
        rows = repo._fetch_proyectos_delta(_SITE, _MAPPING, "tok", cache_scope="scope-a")
        results.append(rows)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    start_gate.set()
    for t in threads:
        t.join(timeout=3)

    assert call_count["n"] == 1  # UN solo cold sync pese a 4 llamadas concurrentes
    assert len(results) == 4
    assert all(r == results[0] for r in results)


# ============================================================================
# §12.M — Asignaciones NUNCA se cachea/reusa entre Initial e Historial
# ============================================================================

def test_m_asignaciones_never_cached_between_initial_and_history(monkeypatch):
    repo = _make_repo()
    transport = _seeded_transport()
    transport.install(monkeypatch)
    inactive_filter_url = repo_mod._list_items_url(
        _SITE, _ASIG, filter_expr=f"fields/{_MAPPING.status} eq '{repo_mod.INACTIVE_STATUS_VALUE}'",
    )
    transport.seed_legacy(inactive_filter_url, [_asig_row("a2", status="Inactivo")])

    repo.get_allocations("tok", cache_scope="scope-a")
    repo.get_allocations("tok", assignment_scope="historical_inactive", cache_scope="scope-a")
    repo.get_allocations("tok", cache_scope="scope-a")

    asig_filter_url = repo_mod._list_items_url(
        _SITE, _ASIG, filter_expr=f"fields/{_MAPPING.status} ne '{repo_mod.INACTIVE_STATUS_VALUE}'",
    )
    # 2 llamadas "operational" + 1 "historical_inactive" ⇒ Asignaciones se
    # leyó 3 VECES, nunca cacheada — la lectura filtrada operational
    # ocurrió 2 veces (nunca 1, que delataría un reuso indebido).
    assert transport.paged_calls.count(asig_filter_url) == 2
    assert transport.paged_calls.count(inactive_filter_url) == 1


# ============================================================================
# §11 — equivalencia de negocio: legacy vs. delta cold vs. delta warm
# ============================================================================

def test_business_equivalence_legacy_vs_delta_cold_vs_delta_warm(monkeypatch):
    repo_legacy = _make_repo()
    repo_delta = _make_repo()
    transport = _seeded_transport()
    transport.install(monkeypatch)

    legacy_records = repo_legacy.get_allocations("tok", cache_scope=None)
    cold_records = repo_delta.get_allocations("tok", cache_scope="scope-a")
    warm_records = repo_delta.get_allocations("tok", cache_scope="scope-a")

    def _shape(records):
        return [
            (r.assignment_id, r.consultant_id, r.consultant_name, r.project_id, r.project_name,
             r.allocation_value, r.status, r.consultant_availability, r.client_id, r.client_name)
            for r in records
        ]

    assert _shape(legacy_records) == _shape(cold_records) == _shape(warm_records)


def test_business_equivalence_absences_cold_parse_vs_warm_cache(monkeypatch):
    repo = _make_repo()
    transport = _seeded_transport()
    transport.install(monkeypatch)

    cold = repo.get_absences("tok", cache_scope="scope-a")
    warm = repo.get_absences("tok", cache_scope="scope-a")
    legacy = repo.get_absences("tok", cache_scope=None)

    def _shape(periods):
        return [(p.source_id, p.consultant_name, p.start_date, p.end_date, p.concept, p.hours) for p in periods]

    assert _shape(cold) == _shape(warm) == _shape(legacy)


# ============================================================================
# StaffingGraphError — clasificación de transporte (incluye TimeoutError)
# ============================================================================

def test_call_staffing_graph_classifies_bare_timeout_as_transient(monkeypatch):
    def _raise_timeout():
        raise TimeoutError("read timed out")

    with pytest.raises(StaffingGraphError) as exc_info:
        repo_mod._call_staffing_graph("proyectos_delta", _raise_timeout)
    assert exc_info.value.status_code is None
