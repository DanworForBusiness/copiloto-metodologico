"""
test_reporting_cartera_rentabilidad_worked_hours_sources.py — URGENT
REPORTING HOTFIX, INCIDENTE A (owner report: "Cartera / Proyectos deja de
mostrar correctamente Horas Trabajadas desde ~Julio 2026; Rentabilidad
Agosto 2026 muestra ~2.682 hs, Cartera no muestra ese mismo volumen para el
mismo período" — expectativa inicial del owner: ambas deberían reconciliar).

REVIEW GATE (primera vuelta de este incidente): el owner cuestionó
correctamente la primera versión de este test/commit por basarse en un
comentario de código ("Certificadas viene de SharePoint, Trabajadas viene
de Time") sin trazar el binding PBIP real — ese comentario, leído aislado,
en realidad apoya la EXPECTATIVA del owner (Trabajadas → Time Logs), no la
conclusión de fuentes distintas. Se pidió explícitamente re-auditar contra
el TMDL/DAX original, no contra comentarios.

RE-AUDIT CONTRA EVIDENCIA PRIMARIA (TMDL/DAX/page.json citados textual,
NUNCA inferidos por nombre de campo):

- **Cartera** (`artifacts/reporting-integral-v0/real-data-lineage.md`,
  Fase 1 — lineage trazada contra el PBIP ANTES de que existiera
  Rentabilidad/Horas Productivas): "Horas por proyectos" (SharePoint list,
  GUID `6c3a8689-e7ed-4a2c-b358-10c5ca1b37b8`) es "el hecho de Cartera",
  grano proyecto×mes, con `HsTrabajadas`/`HsVendidas`/`HsFacturadas`
  (renombrada "Hs certificadas"). Mapeo confirmado: `trabajadas←hsTrabajadas`.
  **Validado en vivo por el owner con datos reales** (mismo documento,
  encabezado): "CARTERA REAL-DATA = VALIDADA EN VIVO (owner smoke OK)...
  rango 01/06–31/07/2026: Trabajadas=1761... Aluar rollup Trab=800".

- **Rentabilidad** (HANDOFF.md, "RENTABILIDAD — PBIP↔HUB SOURCE-PARITY
  RECONCILIATION", DAX citado TEXTUAL de `Time Logs Reports.tmdl`/
  `Comparativo.tmdl`): la medida real es `'Horas  Trabajadas por Mes'` =
  `SUMMARIZE('Time Logs Reports', 'Time Logs Reports'[Project],
  'Time Logs Reports'[Mes], "Horas", SUM('Time Logs Reports'[HORAS]))`,
  unida a la tabla calculada `Comparativo` por `Key_Proyect_desc`
  (`Upper+sin espacios` del Project crudo del CSV). `Comparativo` es
  explícitamente una UNION de 4 fuentes distintas (Vendidas/Certificadas
  de "Horas por proyectos" + Trabajadas de Time Logs) — una MEZCLA
  DELIBERADA, dentro de la propia página "Analisis Rentabilidad", para
  poder calcular `Diff Cer vs Tr = Certificadas - Trabajadas` (¿cuánto de
  lo certificado/facturado se respalda con horas realmente registradas en
  time-tracking?). El campo `HsTrabajadas` de "Horas por proyectos" NUNCA
  se lee para esta medida — confirmado leyendo `get_rentabilidad` completo
  (solo usa `hsCertificadas`/`hsVendidas` de esa fuente).

CONCLUSIÓN (ahora respaldada por binding primario, no por comentario):
son dos medidas del modelo PBIP ORIGINAL, deliberadamente distintas,
coincidiendo solo en el nombre visible "Horas Trabajadas" — un problema de
ROTULADO/producto (dos páginas usan la misma palabra para cosas distintas),
NO un defecto de código reconciliable. Ninguno de los dos pipelines tiene
corte de mes/fecha hardcodeado (auditado `_default_hot_years`/
`get_cartera_facts`/`_operationalMonthCap` — el scope es siempre por AÑO);
el síntoma "desaparece desde julio" es consistente con que el campo MANUAL
de SharePoint (`HsTrabajadas`) no se actualiza desde esa fecha — un gap de
completitud de dato en el origen, ajeno al código.

Por eso este archivo NO afirma igualdad — bloquea la DIVERGENCIA esperada
por diseño original, para que una futura edición no la "corrija" haciendo
que ambas lean la misma fuente sin una decisión explícita de negocio del
owner (eso sería cambiar la semántica de una de las dos métricas, o
requeriría rotular a una de las dos páginas de forma más honesta).
"""
from __future__ import annotations

from webapp.services import reporting_repository as repo_mod
from webapp.services.reporting_repository import ReportingRepository
from webapp.services.reporting_service import ReportingService

_SITE, _OPS_SITE = "site-1", "ops-site-1"
_HP, _PROY, _CLI, _COLAB, _USERINFO = "hp-list", "proy-list", "cli-list", "colab-list", "userinfo-list"

_CSV_HEADERS = ["Date", "Client", "Project", "Task", "Email", "Duration Decimal"]


def _items(rows):
    return [{"id": r.get("id", ""), "fields": r} for r in rows]


def _csv_text(rows):
    import csv
    import io
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_HEADERS)
    writer.writeheader()
    for r in rows:
        writer.writerow({h: r.get(h, "") for h in _CSV_HEADERS})
    return buf.getvalue().encode("utf-8")


def _patch(monkeypatch, *, horas_por_proyectos, proyectos, clientes, time_logs_rows):
    def fake_json(url, access_token=None, **kw):
        if url == f"{repo_mod.GRAPH_BASE}/sites/{repo_mod.TIME_LOGS_SITE_PATH}":
            return {"id": _OPS_SITE}
        return {}

    def fake_paged(url, access_token=None, **kw):
        if f"/sites/{_OPS_SITE}/drive/root:" in url and ":/children" in url:
            return [{"id": "csv-1", "name": "Time Logs Reports.csv"}]
        if "/columns" in url:
            return []
        if _HP in url:
            return _items(horas_por_proyectos)
        if _PROY in url:
            return _items(proyectos)
        if _CLI in url:
            return _items(clientes)
        if _COLAB in url or _USERINFO in url:
            return []
        return []

    def fake_bytes(url, access_token=None, **kw):
        if "/drive/items/csv-1/content" in url:
            return _csv_text(time_logs_rows)
        return b""

    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_json", fake_json)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_paged_values", fake_paged)
    monkeypatch.setattr(repo_mod.microsoft365, "graph_get_bytes", fake_bytes)


def _repo():
    return ReportingRepository(
        site_id=_SITE, horas_proyectos_list_id=_HP, proyectos_list_id=_PROY, cliente_list_id=_CLI,
        colaboradores_list_id=_COLAB, user_information_list_id=_USERINFO,
    )


def test_cartera_and_rentabilidad_worked_hours_are_deliberately_different_pbip_measures(monkeypatch):
    # MISMO proyecto/cliente/mes en las dos fuentes, con volúmenes bien
    # distintos a propósito (150 vs 2682) — reproduce, a escala de fixture,
    # la queja real del owner (Rentabilidad mostraba un volumen que Cartera
    # no mostraba para "el mismo" período). Si algún día alguien iguala
    # estos dos números para que "reconcilien", este test lo marca como una
    # regresión de semántica de negocio contra el binding PBIP confirmado
    # arriba, no como un fix legítimo.
    _patch(
        monkeypatch,
        horas_por_proyectos=[{
            "ProyectoLookupId": "p1", "EmpresaLookupId": "c1",
            "A_x00f1_o": "2026", "Mes": "Agosto", "HsTrabajadas": "150", "HsVendidas": "150", "HsFacturadas": "150",
        }],
        proyectos=[{"id": "p1", "Title": "Soporte Danone", "TipodeProyecto": "Mantenimiento", "Status": "En curso"}],
        clientes=[{"id": "c1", "Title": "Danone"}],
        time_logs_rows=[
            {"Date": "2026-08-05", "Client": "Danone", "Project": "Soporte Danone",
             "Task": "Soporte", "Email": "a@danwor.com", "Duration Decimal": "2682"},
        ],
    )
    repo = _repo()
    service = ReportingService(repo)

    cartera_rows = service.get_cartera_mantenimiento("tok", anios=["2026"])
    agosto = next(r for r in cartera_rows if r["mes"] == "8")
    assert agosto["trabajadas"] == 150.0, (
        "Cartera lee 'Horas por proyectos'[HsTrabajadas] — binding PBIP confirmado "
        "en real-data-lineage.md (lineage Fase 1, validada en vivo por el owner)"
    )

    rentabilidad = service.get_rentabilidad("tok", anios=["2026"])
    proyecto_row = next(p for p in rentabilidad["proyectos"] if p["proyecto"] == "Soporte Danone")
    agosto_trab_rentabilidad = proyecto_row["trabByYear"]["2026"][7]  # índice 7 = Agosto
    assert agosto_trab_rentabilidad == 2682.0, (
        "Rentabilidad lee SUM('Time Logs Reports'[HORAS]) vía la medida "
        "'Horas  Trabajadas por Mes' — DAX citado textual en HANDOFF.md "
        "(RENTABILIDAD — PBIP↔HUB SOURCE-PARITY RECONCILIATION)"
    )

    # La divergencia es EL CONTRATO (confirmado por binding PBIP primario,
    # no supuesto), no un bug — nunca deben coincidir por casualidad en
    # este fixture (si coincidieran, el fixture estaría mal armado, no
    # probando nada).
    assert agosto["trabajadas"] != agosto_trab_rentabilidad
