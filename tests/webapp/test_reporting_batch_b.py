"""
test_reporting_batch_b.py — Contrato de datos reales de Batch B (Horas
Productivas, AMBAS perspectivas — Productivas Y No Asignadas, Reporting
Integral real-data). Fuente física NUEVA: CSV de Time Logs Reports (sitio
Operaciones), verificada contra evidencia PRIMARIA del PBIP (Correction 1,
ver HANDOFF/real-data-lineage.md §8bis). Determinístico, sin Graph real:
monkeypatchea `core.microsoft365` (mismo criterio que
test_reporting_batch_a.py).
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from webapp.services import reporting_repository as repo_mod
from webapp.services.reporting_repository import ReportingRepository, ReportingSourceError
from webapp.services.reporting_service import ReportingService

_SITE, _PROY, _CLI, _COLAB = "site-1", "proy-list", "cli-list", "colab-list"
_OPS_SITE = "ops-site-1"

_CSV_HEADERS = ["Date", "From", "To", "Description", "Duration", "Duration Decimal",
                "Client", "Project", "Task", "User", "Email", "Billable Amount", "Billable", "Cost"]


def _items(rows):
    return [{"id": r.get("id", ""), "fields": r} for r in rows]


def _csv_bytes(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_HEADERS)
    writer.writeheader()
    for r in rows:
        writer.writerow({h: r.get(h, "") for h in _CSV_HEADERS})
    return buf.getvalue().encode("utf-8")


def _patch(monkeypatch, *, proyectos, clientes, colaboradores=None, csv_files=None, file_rows=None,
           ops_site_id=_OPS_SITE, cliente_columns=None):
    """`csv_files`: lista de items de `/children` (id/name). `file_rows`:
    dict item_id -> lista de filas CSV crudas para ESE archivo (permite
    simular un archivo con headers faltantes devolviendo bytes malformados
    vía una lista vacía + header custom, ver test de skip). `cliente_columns`:
    metadata cruda de `/lists/{cliente}/columns` (descubrimiento en vivo del
    internal name de `Porcentaje de Part.`, ver `_resolve_porcentaje_part_field`)
    — default `[]` (sin match, fail-soft)."""
    file_rows = file_rows or {}

    def fake_json(url, access_token=None, **kw):
        if url == f"{repo_mod.GRAPH_BASE}/sites/{repo_mod.TIME_LOGS_SITE_PATH}":
            return {"id": ops_site_id}
        return {}

    def fake_paged(url, access_token=None, **kw):
        if f"/sites/{ops_site_id}/drive/root:" in url and ":/children" in url:
            return csv_files if csv_files is not None else [{"id": "f1", "name": "TimeLogs_2026.csv"}]
        if "/columns" in url:
            return cliente_columns if cliente_columns is not None else []
        if _PROY in url:
            return _items(proyectos)
        if _CLI in url:
            return _items(clientes)
        if _COLAB in url:
            return _items(colaboradores or [])
        return []

    def fake_bytes(url, access_token, **kw):
        for item_id, rows in file_rows.items():
            if f"/drive/items/{item_id}/content" in url:
                return _csv_bytes(rows)
        return _csv_bytes([])

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)


def _repo():
    return ReportingRepository(
        site_id=_SITE, proyectos_list_id=_PROY, cliente_list_id=_CLI, colaboradores_list_id=_COLAB,
    )


def _service():
    return ReportingService(_repo())


# ============================================================================
# Repositorio — descubrimiento/combine de CSV + join a Cliente vía Proyectos
# ============================================================================

def test_get_time_logs_facts_joins_project_and_cliente_via_normalized_key(monkeypatch):
    _patch(
        monkeypatch,
        proyectos=[{"id": "p1", "Title": "Implementación SADI v2", "EmpresaLookupId": "c1"}],
        clientes=[{"id": "c1", "Title": "YPF"}],
        csv_files=[{"id": "f1", "name": "TimeLogs.csv"}],
        file_rows={"f1": [
            {"Date": "2026-03-15", "Project": "Implementación SADI v2", "Task": "Dev", "Email": "a@danwor.com",
             "Duration Decimal": "8000", "Client": "csv-raw-client"},
        ]},
    )
    facts = _repo().get_time_logs_facts("tok")
    assert len(facts) == 1
    f = facts[0]
    # Cliente resuelto vía join (YPF), NUNCA el texto crudo del CSV.
    assert f["cliente"] == "YPF"
    assert f["proyecto"] == "Implementación SADI v2"
    assert f["horas"] == 8.0  # Duration Decimal / 1000
    assert f["mes"] == "2026-03"
    assert f["anio"] == "2026"
    assert f["task"] == "Dev"
    assert f["email"] == "a@danwor.com"


def test_get_time_logs_facts_reads_porcentaje_de_part_from_cliente(monkeypatch):
    # "Cliente + Part" (Horas Productivas panel derecho, §8ter item 1):
    # `Porcentaje de Part.` es una columna FÍSICA de Cliente (Percentage.Type
    # en el M, fracción 0-1). Owner corrective ("Do not rely indefinitely on
    # guessed candidate names"): el internal name se DESCUBRE en vivo de la
    # metadata de columnas (`/lists/{cliente}/columns` → displayName), MISMO
    # mecanismo que `_resolve_client_partners` — la fixture simula Graph
    # devolviendo un internal name ARBITRARIO (`AlgunNombreInternoRaro`) para
    # probar que NUNCA se depende de un candidato codificado a mano.
    _patch(
        monkeypatch,
        proyectos=[{"id": "p1", "Title": "SADI v2", "EmpresaLookupId": "c1"}],
        clientes=[{"id": "c1", "Title": "YPF", "AlgunNombreInternoRaro": "0.125"}],
        csv_files=[{"id": "f1", "name": "TimeLogs.csv"}],
        file_rows={"f1": [
            {"Date": "2026-03-15", "Project": "SADI v2", "Task": "Dev", "Email": "a@danwor.com",
             "Duration Decimal": "8000", "Client": "csv-raw-client"},
        ]},
        cliente_columns=[{"name": "AlgunNombreInternoRaro", "displayName": "Porcentaje de Part."}],
    )
    facts = _repo().get_time_logs_facts("tok")
    assert len(facts) == 1
    assert facts[0]["porcentajePart"] == 0.125


def test_get_time_logs_facts_porcentaje_de_part_is_zero_without_column_match(monkeypatch):
    # Fail-soft: sin match en la metadata de columnas (owner todavía no
    # confirmó el nombre real, o Graph no devuelve la columna) ⇒ 0.0, NUNCA
    # un candidato codificado a mano como último recurso.
    _patch(
        monkeypatch,
        proyectos=[{"id": "p1", "Title": "SADI v2", "EmpresaLookupId": "c1"}],
        clientes=[{"id": "c1", "Title": "YPF", "AlgunNombreInternoRaro": "0.125"}],
        csv_files=[{"id": "f1", "name": "TimeLogs.csv"}],
        file_rows={"f1": [
            {"Date": "2026-03-15", "Project": "SADI v2", "Task": "Dev", "Email": "a@danwor.com",
             "Duration Decimal": "8000", "Client": "csv-raw-client"},
        ]},
        cliente_columns=[],  # sin match — displayName distinto/columna no listada
    )
    facts = _repo().get_time_logs_facts("tok")
    assert len(facts) == 1
    assert facts[0]["porcentajePart"] == 0.0


def test_get_time_logs_facts_falls_back_to_raw_csv_text_when_no_project_match(monkeypatch):
    _patch(
        monkeypatch,
        proyectos=[], clientes=[],
        csv_files=[{"id": "f1", "name": "TimeLogs.csv"}],
        file_rows={"f1": [
            {"Date": "2026-01-05", "Project": "Danwor - Horas No Productivas (#1)", "Client": "Danwor",
             "Duration Decimal": "1000", "Email": "b@danwor.com"},
        ]},
    )
    facts = _repo().get_time_logs_facts("tok")
    assert len(facts) == 1
    # Sin match en Proyectos -> conserva el texto crudo (nunca descarta la fila).
    assert facts[0]["proyecto"] == "Danwor - Horas No Productivas (#1)"
    assert facts[0]["cliente"] == "Danwor"


def test_download_time_logs_rows_skips_files_missing_required_headers(monkeypatch):
    def fake_json(url, access_token=None, **kw):
        return {"id": _OPS_SITE}

    def fake_paged(url, access_token=None, **kw):
        if ":/children" in url:
            return [{"id": "good", "name": "a.csv"}, {"id": "bad", "name": "b.csv"}]
        return []

    def fake_bytes(url, access_token, **kw):
        if "/items/good/" in url:
            return _csv_bytes([{"Date": "2026-01-01", "Project": "X", "Duration Decimal": "1000",
                                 "Email": "x@danwor.com", "Client": "X", "Task": "T"}])
        # Archivo "malo": le faltan headers requeridos (solo 2 columnas).
        return "Foo,Bar\n1,2\n".encode("utf-8")

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)

    repo = _repo()
    repo._token = "tok"
    rows = repo._download_time_logs_rows(_OPS_SITE)
    # Solo el archivo válido aporta filas — el malformado se saltea sin
    # romper el combine completo.
    assert len(rows) == 1
    assert rows[0]["Project"] == "X"


def test_download_time_logs_rows_raises_when_no_csv_files_found(monkeypatch):
    def fake_paged(url, access_token=None, **kw):
        return []

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    repo = _repo()
    repo._token = "tok"
    with pytest.raises(ReportingSourceError):
        repo._download_time_logs_rows(_OPS_SITE)


def test_list_time_logs_csv_items_filters_extension_and_excludes_old(monkeypatch):
    def fake_paged(url, access_token=None, **kw):
        return [
            {"id": "1", "name": "TimeLogs_2026.csv"},
            {"id": "2", "name": "TimeLogs_2026_OLD.csv"},
            {"id": "3", "name": "notes.xlsx"},
            {"id": "4", "name": "TimeLogs_2025.CSV"},
        ]

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    repo = _repo()
    repo._token = "tok"
    items = repo._list_time_logs_csv_items(_OPS_SITE)
    names = {it["name"] for it in items}
    assert names == {"TimeLogs_2026.csv", "TimeLogs_2025.CSV"}


# ============================================================================
# Service — Productivas (Cliente×Proyecto) / No Asignadas (Consultor×mes)
# ============================================================================

def test_shape_horas_productivas_sums_horas_and_excludes_danwor_placeholder():
    facts = [
        {"cliente": "YPF", "proyecto": "SADI v2", "task": "Dev", "email": "a@danwor.com", "horas": 5.0, "mes": "2026-01", "anio": "2026", "porcentajePart": 0.125},
        {"cliente": "YPF", "proyecto": "SADI v2", "task": "Dev", "email": "a@danwor.com", "horas": 3.0, "mes": "2026-02", "anio": "2026", "porcentajePart": 0.125},
        # Placeholder interno — excluido de Productivas (page.json real: NOT Cliente CONTAINS "Danwor").
        {"cliente": "Danwor Internal", "proyecto": "Danwor - Horas No Productivas (#1)", "task": "Admin",
         "email": "b@danwor.com", "horas": 8.0, "mes": "2026-01", "anio": "2026", "porcentajePart": 0.0},
    ]
    rows = _service()._shape_horas_productivas(facts)
    assert len(rows) == 1
    r = rows[0]
    assert r["cliente"] == "YPF" and r["proyecto"] == "SADI v2" and r["horas"] == 8.0
    # Grano POR-AÑO (Año/Mes, ticket "CONTINUE REPORTING") — Ene=5, Feb=3.
    assert r["horasByYear"] == {"2026": [5.0, 3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}
    # "Cliente + Part" (§8ter item 1) — fracción *100, redondeada, NUNCA
    # una segunda agregación separada.
    assert r["porcentajePart"] == 12.5


def test_shape_horas_productivas_no_asignadas_groups_by_consultant_monthly():
    facts = [
        {"cliente": "Danwor", "proyecto": "Danwor - Horas No Productivas (#1)", "task": "Admin",
         "email": "A@Danwor.com", "horas": 4.0, "mes": "2026-01", "anio": "2026"},
        {"cliente": "Danwor", "proyecto": "Danwor - Horas No Productivas (#2)", "task": "Admin",
         "email": "a@danwor.com", "horas": 2.0, "mes": "2026-01", "anio": "2026"},
        {"cliente": "Danwor", "proyecto": "Danwor - Horas No Productivas (#1)", "task": "Admin",
         "email": "a@danwor.com", "horas": 6.0, "mes": "2026-03", "anio": "2026"},
        # Proyecto real (Productivas) — NUNCA debe aparecer en No Asignadas.
        {"cliente": "YPF", "proyecto": "SADI v2", "task": "Dev", "email": "a@danwor.com", "horas": 5.0, "mes": "2026-01", "anio": "2026"},
    ]
    colaboradores = {"a@danwor.com": "Federico Bressi"}
    rows = _service()._shape_horas_productivas_no_asignadas(facts, colaboradores)
    assert len(rows) == 1
    r = rows[0]
    assert r["consultor"] == "Federico Bressi"
    assert r["hsAsig"] == 0  # gap documentado — no adivinado
    assert r["monthly"][0] == 6.0  # Enero: 4.0 (case-insensitive email match) + 2.0
    assert r["monthly"][2] == 6.0  # Marzo
    assert sum(r["monthly"]) == 12.0


def test_shape_horas_productivas_no_asignadas_omits_unresolved_email():
    facts = [
        {"cliente": "Danwor", "proyecto": "Danwor - Horas No Productivas (#1)", "task": "Admin",
         "email": "sin-colaborador@danwor.com", "horas": 4.0, "mes": "2026-01", "anio": "2026"},
    ]
    assert _service()._shape_horas_productivas_no_asignadas(facts, {}) == []


def test_get_batch_b_fetches_time_logs_and_colaboradores_once(monkeypatch):
    calls = {"time_logs": 0, "colaboradores": 0}
    real_time_logs = ReportingRepository.get_time_logs_facts
    real_colab = ReportingRepository.get_colaboradores_email_map

    def counted_time_logs(self, access_token):
        calls["time_logs"] += 1
        return real_time_logs(self, access_token)

    def counted_colab(self, access_token):
        calls["colaboradores"] += 1
        return real_colab(self, access_token)

    monkeypatch.setattr(ReportingRepository, "get_time_logs_facts", counted_time_logs)
    monkeypatch.setattr(ReportingRepository, "get_colaboradores_email_map", counted_colab)
    _patch(
        monkeypatch,
        proyectos=[{"id": "p1", "Title": "SADI v2", "EmpresaLookupId": "c1"}],
        clientes=[{"id": "c1", "Title": "YPF"}],
        colaboradores=[{"id": "co1", "Title": "Federico Bressi", "EMail": "a@danwor.com"}],
        csv_files=[{"id": "f1", "name": "TimeLogs.csv"}],
        file_rows={"f1": [
            {"Date": "2026-01-05", "Project": "SADI v2", "Client": "YPF", "Duration Decimal": "1000", "Email": "a@danwor.com", "Task": "Dev"},
        ]},
    )
    result = _service().get_batch_b("tok")
    assert calls == {"time_logs": 1, "colaboradores": 1}
    assert len(result["horasProductivas"]) == 1
    r = result["horasProductivas"][0]
    assert r["cliente"] == "YPF" and r["proyecto"] == "SADI v2" and r["horas"] == 1.0
    assert result["horasProductivasNoAsignadas"] == []


# ============================================================================
# Colaboradores — Email (lowercase) -> Consultor
# ============================================================================

def test_get_colaboradores_email_map_lowercases_email(monkeypatch):
    def fake_paged(url, access_token=None, **kw):
        if _COLAB in url:
            return _items([{"id": "co1", "Title": "Federico Bressi", "EMail": "Federico.Bressi@Danwor.com"}])
        return []

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    mapping = _repo().get_colaboradores_email_map("tok")
    assert mapping == {"federico.bressi@danwor.com": "Federico Bressi"}


# ============================================================================
# Frontend — ciclo de vida real-data propio (Batch B, distinto de Batch A)
# ============================================================================

_REPORTING_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")


def test_batch_b_endpoint_and_lifecycle_exist():
    assert '"/operaciones/reporting/integral/data/batch-b"' in _REPORTING_JS
    assert "function loadBatchBData" in _REPORTING_JS
    assert "function renderBatchBDataState" in _REPORTING_JS
    assert "batchBDataStatus: \"idle\"," in _REPORTING_JS


def test_batch_b_never_mixes_mock_and_real_data():
    load_fn = _REPORTING_JS[_REPORTING_JS.index("async function loadBatchBData"):_REPORTING_JS.index("async function loadBatchBData") + 1000]
    assert "replaceRows(CAPACIDAD_PRODUCTIVAS_ROWS, data.horasProductivas)" in load_fn
    assert "replaceRows(CAPACIDAD_NOASIGNADAS_ROWS, data.horasProductivasNoAsignadas)" in load_fn


def test_horas_productivas_and_no_asignadas_gate_on_batch_b_status_not_batch_a():
    for fn in ("renderCapacidadProductivas", "renderCapacidadNoAsignadas"):
        body = _REPORTING_JS[_REPORTING_JS.index(f"function {fn}("):_REPORTING_JS.index(f"function {fn}(") + 500]
        assert 'state.batchBDataStatus === "idle"' in body
        assert "loadBatchBData()" in body
        assert "batchADataStatus" not in body


# ============================================================================
# "Cliente + Part" two-panel parity (ticket "CONTINUE REPORTING" — evidencia
# PRIMARIA en real-data-lineage.md §8ter item 1: "Cliente + Part" reusa el
# MISMO Rank/Horas/%Incidencia que el panel Cliente, solo con un rótulo
# enriquecido por el % de referencia ESTÁTICO de cada cliente)
# ============================================================================

def _fn_body_batch_b(name: str, window: int = 3600) -> str:
    start = _REPORTING_JS.index(f"function {name}(")
    return _REPORTING_JS[start:start + window]


def test_capacidad_productivas_renders_two_panels_with_independent_state():
    fn = _fn_body_batch_b("renderCapacidadProductivas", 4200)
    assert 'class="activity-grid-2"' in fn
    # DOS stateKeys distintos (árboles de expansión independientes) — nunca
    # el mismo stateKey reusado para ambos paneles.
    assert '"capacidad::Productivas::Cliente"' in fn
    assert '"capacidad::Productivas::ClientePart"' in fn
    # El panel derecho reusa el MISMO motor `rowsFor` (MISMA implementación
    # de selección/orden que el izquierdo) — nunca un fetch/agregación
    # paralela — pero cada uno con su PROPIO argumento de panel.
    assert 'rowsFor("cliente")' in fn
    assert 'rowsFor("clientePart")' in fn
    assert "clientePart:" in fn and '"clientePart"' in fn
    # AMBOS paneles consumen las MISMAS columnas (measure family compartida,
    # ver capacidadProductivasColumns) — cada uno con su propio `panelKey`.
    assert 'capacidadProductivasColumns(panelKey)' in fn
    # Owner corrective: cada panel monta su PROPIO Año/Mes en su propio
    # toolbar (nunca un único Año/Mes compartido en el filter bar).
    assert "capacidadProductivasAnioField(panelKey)" in fn
    assert "capacidadProductivasMesField(panelKey)" in fn


def test_capacidad_productivas_anio_field_offers_todos_unlike_rentabilidad_hv():
    # Sin hot/cold (CSV completo ya en memoria) — a diferencia de
    # Rentabilidad/HV, "Todos" SÍ es una opción válida (MISMO criterio que
    # el Año de Cartera/Mantenimiento). Parametrizado por panel.
    fn = _fn_body_batch_b("capacidadProductivasAnioField", 700)
    assert "function capacidadProductivasAnioField(panel)" in fn
    assert '>Todos</option>' in fn
    assert "capacidadProductivasAnioOptions()" in fn


def test_capacidad_productivas_selected_rows_collapses_all_years_when_anio_is_todos():
    fn = _fn_body_batch_b("capacidadProductivasSelectedRows")
    assert "function capacidadProductivasSelectedRows(panel)" in fn
    assert "_sumMonthlyArrays(Object.values(r.horasByYear || {}))" in fn


def test_capacidad_productivas_total_uses_anio_mes_scoped_population():
    # Gran total FIJO (denominador REMOVEFILTERS de Cliente/Client Partner)
    # recalculado contra la población YA proyectada a Año/Mes DE ESE PANEL —
    # MISMO criterio que Participación (Año/Estado no son dimensiones
    # REMOVEFILTERS, son scope real de los datos). Parametrizado por panel —
    # nunca un total compartido entre ambos.
    fn = _fn_body_batch_b("capacidadProductivasTotal", 200)
    assert "function capacidadProductivasTotal(panel)" in fn
    assert "capacidadProductivasSelectedRows(panel)" in fn


# ============================================================================
# Owner corrective — "PRESERVE PBIP PANEL-INDEPENDENT TEMPORAL STATE": cada
# panel tiene su PROPIA key de Año/Mes en `state.filters.capacidad`, nunca
# la misma key compartida entre ambos. "Shared implementation != shared
# state" — mismas funciones, dos instancias de estado independientes.
# ============================================================================

def test_capacidad_panels_use_distinct_filter_keys_never_shared():
    assert 'function capacidadPanelAnioKey(panel) { return `anio_${panel}`; }' in _REPORTING_JS
    assert 'function capacidadPanelMesKey(panel) { return `mes_${panel}`; }' in _REPORTING_JS
    # CAPACIDAD_PRODUCTIVAS_PANELS enumera los dos paneles reales — nunca un
    # tercer panel fantasma ni uno solo.
    assert 'const CAPACIDAD_PRODUCTIVAS_PANELS = ["cliente", "clientePart"];' in _REPORTING_JS


def test_prune_capacidad_productivas_reconciles_both_panels_independently():
    # reconcileExperienceFacets debe reconciliar AMBOS paneles (cada uno con
    # su propia key) — nunca solo uno, y nunca una función sin el parámetro
    # `panel`.
    body = _fn_body_batch_b("reconcileExperienceFacets", 2600)
    assert "CAPACIDAD_PRODUCTIVAS_PANELS.forEach(panel =>" in body
    assert "pruneCapacidadProductivasAnio(panel)" in body
    assert "pruneCapacidadProductivasMes(panel)" in body


def test_capacidad_productivas_columns_closes_over_a_per_panel_total():
    # `capacidadProductivasColumns` es una FUNCIÓN (no un array `const`
    # compartido) — cada panel necesita su propio total en el cierre del
    # % Incidencia, nunca uno global.
    fn = _fn_body_batch_b("capacidadProductivasColumns", 700)
    assert "capacidadProductivasTotal(panel)" in fn
    assert "const CAPACIDAD_PRODUCTIVAS_COLUMNS =" not in _REPORTING_JS
