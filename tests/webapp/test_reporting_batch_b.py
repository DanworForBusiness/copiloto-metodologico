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
             "Duration Decimal": "8.5", "Client": "csv-raw-client"},
        ]},
    )
    facts = _repo().get_time_logs_facts("tok")
    assert len(facts) == 1
    f = facts[0]
    # Cliente resuelto vía join (YPF), NUNCA el texto crudo del CSV.
    assert f["cliente"] == "YPF"
    assert f["proyecto"] == "Implementación SADI v2"
    # FINAL RECONCILIATION CORRECTIVE: `Duration Decimal` YA es horas decimales
    # (8,5 = 8h30m) — se toma tal cual, SIN /1000 (que daba 1000× menos, owner
    # golden case). Ver reporting_repository get_time_logs_facts.
    assert f["horas"] == 8.5
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


def test_shape_horas_productivas_denominador_reintroduces_danwor_placeholder():
    # SOURCE-PARITY corrective (owner live comparison contra el PBI —
    # evidencia PRIMARIA en Time Logs Reports.tmdl: medidas '% Hs
    # ejecutadas'/'% Hs ejecutadas2' hacen REMOVEFILTERS(Cliente[Cliente])
    # + REMOVEFILTERS('Time Logs Reports'[Client/Cliente + Part]) — el
    # MISMO campo donde viven los 2 filtros de página que excluyen el
    # cliente placeholder interno de "No Asignadas". El denominador REAL
    # los deshace y reintroduce esas horas — a diferencia de
    # `_shape_horas_productivas` (filas MOSTRADAS), que SIGUE excluyendo
    # el placeholder tal cual. MISMA fixture que
    # test_shape_horas_productivas_sums_horas_and_excludes_danwor_placeholder
    # — prueba EXACTAMENTE que el denominador (16, incluye las 8 del
    # placeholder) es MAYOR que la suma de las filas mostradas (8).
    facts = [
        {"cliente": "YPF", "proyecto": "SADI v2", "task": "Dev", "email": "a@danwor.com", "horas": 5.0, "mes": "2026-01", "anio": "2026", "porcentajePart": 0.125},
        {"cliente": "YPF", "proyecto": "SADI v2", "task": "Dev", "email": "a@danwor.com", "horas": 3.0, "mes": "2026-02", "anio": "2026", "porcentajePart": 0.125},
        {"cliente": "Danwor Internal", "proyecto": "Danwor - Horas No Productivas (#1)", "task": "Admin",
         "email": "b@danwor.com", "horas": 8.0, "mes": "2026-01", "anio": "2026", "porcentajePart": 0.0},
    ]
    svc = _service()
    rows = svc._shape_horas_productivas(facts)
    denominador = svc._shape_horas_productivas_denominador(facts)
    filas_total = sum(r["horas"] for r in rows)
    denominador_total = sum(denominador["2026"])
    assert filas_total == 8.0
    assert denominador_total == 16.0  # 5 + 3 (YPF) + 8 (Danwor placeholder reintroducido)
    assert denominador_total != filas_total  # LA prueba central del corrective
    # Grano mensual real (no solo el total) — Ene=5(YPF)+8(Danwor)=13, Feb=3.
    assert denominador["2026"] == [13.0, 3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def test_shape_horas_productivas_denominador_ignores_month_gaps_and_invalid_anio():
    facts = [
        {"cliente": "X", "proyecto": "Y", "task": "", "email": "", "horas": 4.0, "mes": "2025-06", "anio": "2025", "porcentajePart": 0.0},
        # Fila sin mes reconocible (fail-soft de _time_logs_mes) — no aporta.
        {"cliente": "X", "proyecto": "Y", "task": "", "email": "", "horas": 99.0, "mes": "", "anio": "", "porcentajePart": 0.0},
    ]
    denominador = _service()._shape_horas_productivas_denominador(facts)
    assert denominador == {"2025": [0.0, 0.0, 0.0, 0.0, 0.0, 4.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}


def test_shape_horas_productivas_preserves_3rd_decimal_owner_live_screenshot():
    # Owner live screenshot (regresión — "pedí 3 decimales, sigo viendo 2"):
    # root cause fue que `_shape_horas_productivas`/`_shape_horas_
    # productivas_denominador` redondeaban a 2 decimales (`_round2`) ANTES
    # de que el dato llegara al frontend — el formatter `hours3` (reporting.js,
    # ya correcto) nunca podía mostrar un 3er decimal que el backend ya
    # había destruido. Fixture reproduce el caso real (Danone ≈5.422h,
    # visible en pantalla como "5.42" en vez de "5.422"): la suma cruda
    # NUNCA debe perder el 3er decimal en el shaping.
    facts = [
        {"cliente": "Danone", "proyecto": "Soporte Analítica", "task": "Dev", "email": "a@danwor.com",
         "horas": 5.4219, "mes": "2026-01", "anio": "2026", "porcentajePart": 0.064},
    ]
    svc = _service()
    rows = svc._shape_horas_productivas(facts)
    assert rows[0]["horas"] == 5.422  # 3 decimales preservados, NUNCA 5.42
    assert rows[0]["horasByYear"]["2026"][0] == 5.422
    denominador = svc._shape_horas_productivas_denominador(facts)
    assert denominador["2026"][0] == 5.422  # el denominador (Total Horas tile) TAMBIÉN a 3 decimales


_NA_TASK = "01. No Asignación a Servicio en Cliente"  # slicer del PBI (con prefijo "01. ")


def test_shape_horas_productivas_no_asignadas_groups_by_consultant_monthly():
    # FINAL PBIP SOURCE-PARITY CORRECTIVE: se filtra por TASK (slicer del PBI),
    # NO por proyecto placeholder. El proyecto es irrelevante para el match.
    facts = [
        {"cliente": "YPF", "proyecto": "SADI v2", "task": _NA_TASK,
         "email": "A@Danwor.com", "horas": 4.0, "mes": "2026-01", "anio": "2026"},
        {"cliente": "Molinos", "proyecto": "Otro", "task": _NA_TASK,
         "email": "a@danwor.com", "horas": 2.0, "mes": "2026-01", "anio": "2026"},
        {"cliente": "YPF", "proyecto": "SADI v2", "task": _NA_TASK,
         "email": "a@danwor.com", "horas": 6.0, "mes": "2026-03", "anio": "2026"},
        # Otro Task (Productivas) — NUNCA debe aparecer en No Asignadas.
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
        {"cliente": "Danwor", "proyecto": "X", "task": _NA_TASK,
         "email": "sin-colaborador@danwor.com", "horas": 4.0, "mes": "2026-01", "anio": "2026"},
    ]
    assert _service()._shape_horas_productivas_no_asignadas(facts, {}) == []


def test_no_asignadas_matches_task_normalized_and_ignores_other_tasks():
    # SOURCE PARITY + NORMALIZATION: el Task fuente lleva prefijo "01. " y no debe
    # excluirse; el match tolera case/whitespace/acentos (NFC) pero NO strippea
    # el prefijo (source-faithful). Otros Tasks se excluyen.
    facts = [
        {"cliente": "C", "proyecto": "P", "task": "01. No Asignación a Servicio en Cliente",
         "email": "a@danwor.com", "horas": 3.0, "mes": "2026-05", "anio": "2026"},
        # Variación de representación (doble espacio + mayúsculas) → mismo match.
        {"cliente": "C", "proyecto": "P", "task": "01.  NO ASIGNACIÓN A SERVICIO EN CLIENTE",
         "email": "a@danwor.com", "horas": 2.0, "mes": "2026-05", "anio": "2026"},
        # Task distinto (sin prefijo / otra tarea) → excluido.
        {"cliente": "C", "proyecto": "P", "task": "Desarrollo",
         "email": "a@danwor.com", "horas": 99.0, "mes": "2026-05", "anio": "2026"},
    ]
    rows = _service()._shape_horas_productivas_no_asignadas(facts, {"a@danwor.com": "Ana"})
    assert len(rows) == 1
    assert rows[0]["monthly"][4] == 5.0  # Mayo: 3 + 2 (el Task "Desarrollo" NO suma)


def test_no_asignadas_golden_2026_monthly_and_total_reconcile():
    # Owner golden case (2026, Task No Asignación): Ene-Ago = 130/90/113/152/
    # 216/172/208/59, Total = 1.139. Fixture determinística que reproduce esos
    # totales mensuales (repartidos en 2 consultores) — prueba mapeo mes→índice,
    # agregación y grano consultor-céntrico. Los valores NO se hardcodean en
    # producción (son evidencia de validación del owner).
    monthly_expected = [130, 90, 113, 152, 216, 172, 208, 59, 0, 0, 0, 0]
    facts = []
    for i, total in enumerate(monthly_expected):
        if total == 0:
            continue
        mes = f"2026-{i + 1:02d}"
        # Repartido en 2 consultores para probar el grano consultor-céntrico.
        facts.append({"cliente": "C", "proyecto": "P", "task": _NA_TASK,
                      "email": "u1@danwor.com", "horas": float(total - 10), "mes": mes, "anio": "2026"})
        facts.append({"cliente": "C", "proyecto": "P", "task": _NA_TASK,
                      "email": "u2@danwor.com", "horas": 10.0, "mes": mes, "anio": "2026"})
    rows = _service()._shape_horas_productivas_no_asignadas(
        facts, {"u1@danwor.com": "Consultor Uno", "u2@danwor.com": "Consultor Dos"})
    assert len(rows) == 2  # grano consultor-céntrico
    # Total por mes (suma de ambos consultores) reproduce el mapeo mes→índice.
    total_por_mes = [round(sum(r["monthly"][m] for r in rows), 2) for m in range(12)]
    assert total_por_mes == [float(v) for v in monthly_expected]
    # El total reconcilia con la suma de los meses del fixture. Nota: el
    # screenshot del owner lista Ene-Ago = 130..59 (suma 1140) pero muestra
    # TOTAL 1.139 — diferencia de REDONDEO (los valores reales son decimales); la
    # validación exacta del par es owner-live, no un oráculo de unit test. Acá se
    # prueba la MECÁNICA (mes/grano/agregación) con un fixture auto-consistente.
    assert round(sum(sum(r["monthly"]) for r in rows), 2) == float(sum(monthly_expected))


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
            {"Date": "2026-01-05", "Project": "SADI v2", "Client": "YPF", "Duration Decimal": "1", "Email": "a@danwor.com", "Task": "Dev"},
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
    # SHARED REPORTING INITIAL-LOAD CONTRACT: el trigger se centralizó en el
    # shell (`TAB_HYDRATION_TRIGGERS.capacidad`, ver `render()`) — ambos
    # sub-renderers solo gatean por status, nunca disparan su propio load.
    assert 'capacidad: () => { if (state.batchBDataStatus === "idle") loadBatchBData(); }' in _REPORTING_JS
    for fn in ("renderCapacidadProductivas", "renderCapacidadNoAsignadas"):
        body = _REPORTING_JS[_REPORTING_JS.index(f"function {fn}("):_REPORTING_JS.index(f"function {fn}(") + 500]
        assert 'state.batchBDataStatus === "idle"' not in body
        assert "loadBatchBData()" not in body
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


def _fn_body_exact(name: str) -> str:
    """Cuerpo EXACTO de una función vía brace-matching — a diferencia de
    `_fn_body_batch_b` (ventana fija en caracteres), nunca queda corto
    cuando la función crece (MISMO criterio que `_function_body` en
    test_reporting_main_tab_hydration.py)."""
    start = _REPORTING_JS.index(f"function {name}(")
    brace = _REPORTING_JS.index("{", start)
    depth = 0
    for i in range(brace, len(_REPORTING_JS)):
        if _REPORTING_JS[i] == "{":
            depth += 1
        elif _REPORTING_JS[i] == "}":
            depth -= 1
            if depth == 0:
                return _REPORTING_JS[brace:i + 1]
    raise AssertionError(f"no se encontró el cierre de function {name}(")


def test_capacidad_productivas_renders_two_panels_with_independent_state():
    fn = _fn_body_exact("renderCapacidadProductivas")
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
    # toolbar (nunca un único Año/Mes compartido en el filter bar). Mes es
    # multi-select (botones, owner live QA — ver capacidadProductivasMesButtons),
    # ya NO un `<select>` de valor único.
    assert "capacidadProductivasAnioField(panelKey)" in fn
    assert "capacidadProductivasMesButtons(panelKey)" in fn


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
    # SOURCE-PARITY corrective (owner live comparison: LEFT PBI ≈50.5 vs
    # KPI visible 42.182) — el denominador de "% Incidencia" YA NO suma
    # `capacidadProductivasSelectedRows(panel)` (esas son las filas
    # MOSTRADAS, Cliente-placeholder interno ya excluido, Estado ya
    # filtrado — reproduce el "Total Horas" VISIBLE, no la medida DAX
    # real). Ahora lee `CAPACIDAD_PRODUCTIVAS_DENOMINADOR_BY_YEAR` (grand
    # total poblado por el backend, ver `_shape_horas_productivas_
    # denominador`) proyectado por el MISMO motor Año/Mes/mes-cap que
    # `capacidadProductivasSelectedRows` (nunca una copia) — Año/Mes SIGUEN
    # siendo scope real (como antes), pero Estado/Cliente/Proyecto ya NO
    # participan (REMOVEFILTERS real, evidencia PRIMARIA TMDL). Parametrizado
    # por panel — nunca un total compartido entre ambos.
    assert "function capacidadProductivasTotal(panel)" in _REPORTING_JS
    fn = _fn_body_exact("capacidadProductivasTotal")
    assert "capacidadProductivasSelectedRows(panel)" not in fn
    assert "CAPACIDAD_PRODUCTIVAS_DENOMINADOR_BY_YEAR" in fn
    assert "_maskMeses(arr, meses)" in fn
    assert "_operationalMonthCap(anio)" in fn


# ============================================================================
# Owner corrective — "PRESERVE PBIP PANEL-INDEPENDENT TEMPORAL STATE": cada
# panel tiene su PROPIA key de Año/Mes en `state.filters.capacidad`, nunca
# la misma key compartida entre ambos. "Shared implementation != shared
# state" — mismas funciones, dos instancias de estado independientes.
# ============================================================================

def test_capacidad_panels_use_distinct_filter_keys_never_shared():
    assert 'function capacidadPanelAnioKey(panel) { return `anio_${panel}`; }' in _REPORTING_JS
    # Mes es multi-select (owner live QA corrective) — vive FUERA de
    # `state.filters.capacidad`, un Set POR PANEL en
    # `state.capacidadMesesSeleccionados`, nunca la misma key/Set para ambos.
    assert "capacidadMesesSeleccionados: { cliente: new Set(), clientePart: new Set() }" in _REPORTING_JS
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
    assert "pruneCapacidadProductivasMeses(panel)" in body


def test_capacidad_productivas_columns_closes_over_a_per_panel_total():
    # `capacidadProductivasColumns` es una FUNCIÓN (no un array `const`
    # compartido) — cada panel necesita su propio total en el cierre del
    # % Incidencia, nunca uno global.
    fn = _fn_body_batch_b("capacidadProductivasColumns", 700)
    assert "capacidadProductivasTotal(panel)" in fn
    assert "const CAPACIDAD_PRODUCTIVAS_COLUMNS =" not in _REPORTING_JS


def test_time_logs_horas_is_decimal_hours_not_milli_hours(monkeypatch):
    # FINAL RECONCILIATION CORRECTIVE (owner golden case 2026/Julio/Mantenimiento/
    # En curso: PBI Trabajadas = 4.568, el Hub daba 4,6 = 4568/1000 EXACTO). El
    # `/1000` era una división ESPURIA: `Duration Decimal` YA es horas decimales
    # (estándar de export de time-tracking) — `_num` la parseaba bien y su SUMA
    # ya coincidía con el PBI. Este test fija el CONTRATO de parseo/transformación
    # (raw → parsed → agregación), NUNCA el síntoma: la suma del Hub == suma
    # cruda de Duration Decimal = SUM(HORAS) del PBIP, sin escala.
    _patch(
        monkeypatch,
        proyectos=[{"id": "p1", "Title": "Mantenimiento X", "EmpresaLookupId": "c1"}],
        clientes=[{"id": "c1", "Title": "YPF"}],
        csv_files=[{"id": "f1", "name": "TimeLogs.csv"}],
        file_rows={"f1": [
            {"Date": "2026-07-01", "Project": "Mantenimiento X", "Duration Decimal": "8.5", "Email": "a@danwor.com", "Client": "YPF", "Task": "Dev"},
            {"Date": "2026-07-02", "Project": "Mantenimiento X", "Duration Decimal": "160", "Email": "b@danwor.com", "Client": "YPF", "Task": "Dev"},
            {"Date": "2026-07-03", "Project": "Mantenimiento X", "Duration Decimal": "1.25", "Email": "c@danwor.com", "Client": "YPF", "Task": "Dev"},
        ]},
    )
    facts = _repo().get_time_logs_facts("tok")
    horas = sorted(f["horas"] for f in facts)
    # Identidad: cada valor se toma tal cual (horas decimales), NUNCA ÷1000.
    assert horas == [1.25, 8.5, 160.0]
    total = sum(horas)
    assert total == 169.75          # = SUM(HORAS) del PBIP, sin escala
    assert total != 0.16975         # el defecto ÷1000 habría dado esto (1000× menos)


# ============================================================================
# PUNCH migration — No Asignadas usa el primitivo compartido, fuera del section
# (FINAL PBIP SOURCE-PARITY CORRECTIVE §8)
# ============================================================================

def test_no_asignadas_nota_metodologica_uses_shared_punch_outside_section():
    fn = _fn_body_exact("renderCapacidadNoAsignadas")
    # La nota YA NO es un <p class="empty-note"> INLINE dentro de la tabla.
    assert 'empty-note" style="text-align:left">Horas No Productivas' not in fn
    # Reusa el MISMO primitivo compartido `renderHubCallout` con tono `punch`
    # (NUNCA un estilo No-Asignadas nuevo) — mismo que Productivas.
    assert "renderHubCallout({" in fn and 'tone: "punch"' in fn
    assert "indicador distinto de la disponibilidad de Asignaciones" in fn
    # El punch va AFUERA y DESPUÉS del grupo analítico (dashboardSection detalle).
    assert "return detalle + notaMetodologica;" in fn
    # Wording business-friendly: el bodyHtml del punch NO muestra el prefijo
    # "01." del Task fuente (la presentación no maneja el predicado de filtrado).
    body_html = fn[fn.index("bodyHtml:"):fn.index("bodyHtml:") + 260]
    assert "01." not in body_html


def test_no_asignadas_and_productivas_share_the_same_punch_primitive():
    # Un solo primitivo (renderHubCallout) — nunca un renderer/CSS duplicado
    # por perspectiva.
    for fn_name in ("renderCapacidadProductivas", "renderCapacidadNoAsignadas"):
        assert 'tone: "punch"' in _fn_body_exact(fn_name)
    # renderHubCallout está definido UNA sola vez (primitivo compartido).
    assert _REPORTING_JS.count("function renderHubCallout(") == 1
