"""test_human_capital_indicators_engine.py — motor de 06 Indicadores (CCHH), Bloque A.

Cubre: paridad numérica con la app original (oráculo diferencial sobre `lib/metricas.ts`), redondeo `Math.round`, fechas históricas y fallbacks con su procedencia, cardinalidad N, consistencia
cruzada KPI↔tarjetas, catálogos dinámicos y valores históricos, ausencia de datos, filtros locales independientes (C6/C9), ausencia de N+1, ausencia de efectos secundarios, determinismo y
reutilización de las funciones canónicas del Hub (ingreso, estancamiento, trimestre). Sin HTTP: el motor no conoce FastAPI, Jinja ni openpyxl.
"""
from __future__ import annotations

import ast
import json
import random
from datetime import datetime
from pathlib import Path

import pytest

import config
from tests.webapp.human_capital_indicators_fixtures import (
    make_app, make_busqueda, make_entrevista, make_publicacion, make_snapshot, snapshot_from_raw,
)
from webapp.services import busqueda_repository, entrevista_repository, human_capital_service as hc, profile_repository, publicacion_repository
from webapp.services import human_capital_catalog_repository as catalog_repo
from webapp.services import human_capital_indicators as ind

DATA = Path(__file__).parent / "data" / "cchh_indicators"
MODULE_PATH = Path(ind.__file__)


def facts_of(**kw) -> ind.IndicatorFacts:
    return ind.build_facts(make_snapshot(**kw))


def ingreso_app(app_id, profile, alta, llegada, **kw):
    return make_app(app_id, profile, etapa="Finalizado", resultado="Ingreso", alta=alta, movimiento=llegada, **kw)


@pytest.fixture(scope="module")
def parity():
    raw = json.loads((DATA / "dataset.json").read_text(encoding="utf-8"))
    golden = json.loads((DATA / "golden.json").read_text(encoding="utf-8"))["results"]
    facts = ind.build_facts(snapshot_from_raw(raw))
    return facts, ind.build_page(facts), golden


# ═════════════════ helpers: redondeo Math.round, fechas, etiquetas ═════════════════

@pytest.mark.parametrize("value, expected", [(0.0, 0), (0.4, 0), (0.5, 1), (1.5, 2), (2.5, 3), (4.5, 5), (12.5, 13), (33.333, 33), (99.5, 100)])
def test_round_half_up_is_math_round_for_positive_values_not_the_pythons_bankers_rounding(value, expected):
    assert ind.round_half_up(value) == expected
    if value in (0.5, 2.5, 4.5, 12.5):
        assert round(value) != expected, "el redondeo al par de Python daría otro resultado: por eso no se usa"


def test_parse_utc_accepts_date_only_offsets_and_z_and_treats_garbage_as_missing_never_inventing_a_date():
    assert ind.parse_utc("2026-09-30") == datetime.fromisoformat("2026-09-30T00:00:00+00:00")
    assert ind.parse_utc("2026-09-30T14:03:24.337245+00:00").hour == 14
    assert ind.parse_utc("2026-09-30T14:00:00Z") == ind.parse_utc("2026-09-30T11:00:00-03:00")
    assert ind.parse_utc("2026-09-30T14:00:00").tzinfo is not None
    for bad in (None, "", "   ", "no-es-fecha", "2026-13-45"):
        assert ind.parse_utc(bad) is None


def test_dias_entre_rounds_half_up_with_a_zero_floor():
    d = ind.parse_utc
    assert ind.dias_entre(d("2026-01-01"), d("2026-01-11")) == 10
    assert ind.dias_entre(d("2026-01-01T00:00:00Z"), d("2026-01-01T12:00:00Z")) == 1            # 0,5 día → 1
    assert ind.dias_entre(d("2026-01-01T00:00:00Z"), d("2026-01-01T11:59:00Z")) == 0
    assert ind.dias_entre(d("2026-02-01"), d("2026-01-01")) == 0                                # cierre anterior a la apertura → mínimo 0


def test_month_keys_labels_and_quarters_are_utc():
    moment = ind.parse_utc("2026-03-31T23:30:00-03:00")                                         # ya es 1-abr en UTC
    assert ind.mes_clave(moment) == "2026-04" and ind.trimestre(moment) == "2026-Q2"
    assert ind.etiqueta_mes("2026-01") == "Ene 2026" and ind.etiqueta_mes("2025-12") == "Dic 2025"


# ═════════════════ paridad numérica con la app original (oráculo diferencial) ═════════════════

def test_golden_provenance_is_recorded_and_the_dataset_is_not_trivial(parity):
    doc = json.loads((DATA / "golden.json").read_text(encoding="utf-8"))["provenance"]
    assert set(doc["sha256"]) == {"metricas.ts", "types.ts", "publicaciones.ts"} and doc["tz"] == "UTC"
    _facts, _page, golden = parity
    assert golden["kpis"]["contratados"] >= 5 and golden["kpis"]["entrevistasRealizadas"] >= 20 and len(golden["periodosEntrevistas"]) >= 3


def test_parity_kpis_match_the_original_functions(parity):
    _facts, page, golden = parity
    k, g = page["kpis"], golden["kpis"]
    assert k["tiempo_cobertura"]["valor"] == g["timeToFill"]
    assert k["tiempo_contratacion"]["valor"] == g["timeToHire"]
    assert k["tasa_conversion"]["valor"] == g["conversion"]
    assert k["total_contratados"]["valor"] == g["contratados"]
    assert k["total_descartados"]["valor"] == g["descartados"]
    assert k["entrevistas_realizadas"]["valor"] == g["entrevistasRealizadas"]


def test_parity_c1_postulantes_por_busqueda(parity):
    _f, page, golden = parity
    engine = [(r["nombre_puesto"], r["cantidad"]) for r in page["postulantes_por_busqueda"]["filas"]]
    original = [(r["nombrePuesto"], r["cantidad"]) for r in golden["candidatosPorProceso"]]
    assert [c for _n, c in engine] == [c for _n, c in original]                                 # mismas cantidades en el mismo orden (los empates se desempatan distinto)
    counts = {r["nombre_puesto"]: r["cantidad"] for r in ind.postulantes_por_busqueda(parity[0])["filas"]}
    assert all(counts[n] == c for n, c in engine)


def test_parity_c2_busquedas_por_mes(parity):
    _f, page, golden = parity
    assert [(r["mes"], r["abiertas"], r["cerradas"]) for r in page["busquedas_por_mes"]["filas"]] == [(r["mes"], r["abiertas"], r["cerradas"]) for r in golden["busquedasPorMes"]]


def test_parity_c3_ingresos_por_mes_with_the_original_modalities(parity):
    _f, page, golden = parity
    rows = []
    for r in page["ingresos_por_mes"]["filas"]:
        seg = {s["clave"]: s["valor"] for s in r["segmentos"]}
        rows.append((r["mes"], seg["Full time"], seg["Part time"], seg[ind.SIN_ESPECIFICAR], r["total"]))
    assert rows == [(r["mes"], r["fullTime"], r["partTime"], r["sinEspecificar"], r["total"]) for r in golden["ingresosPorMes"]]


def test_parity_c4_c5_entrevistas_por_anio_y_por_mes(parity):
    _f, page, golden = parity
    assert [(r["anio"], r["realizadas"], r["no_realizadas"], r["total"]) for r in page["entrevistas_por_anio"]["filas"]] == [
        (r["anio"], r["realizadas"], r["noRealizadas"], r["total"]) for r in golden["entrevistasPorAnio"]]
    assert [(r["mes"], r["realizadas"], r["no_realizadas"], r["total"]) for r in page["entrevistas_por_mes"]["filas"]] == [
        (r["mes"], r["realizadas"], r["noRealizadas"], r["total"]) for r in golden["entrevistasPorMes"]]


def test_parity_c6_perfiles_for_every_period_option_and_the_period_list(parity):
    facts, _page, golden = parity
    assert ind.build_perfiles(facts, "todos")["periodos"] == golden["periodosEntrevistas"]
    for periodo, original in golden["perfiles"].items():
        got = ind.build_perfiles(facts, periodo)
        assert got["periodo"] == periodo
        assert {r["perfil"]: r["cantidad"] for r in got["filas"]} == {r["perfil"]: r["cantidad"] for r in original}
        counts = [r["cantidad"] for r in got["filas"]]
        assert counts == sorted(counts, reverse=True)
        assert len(got["top"]) == min(10, len(original))


def test_parity_c7_efectividad_por_fuente(parity):
    _f, page, golden = parity
    engine = {r["fuente"]: (r["postulantes"], r["contratados"], r["conversion"]) for r in page["efectividad_por_fuente"]["filas"]}
    assert engine == {r["fuente"]: (r["postulantes"], r["contratados"], r["conversion"]) for r in golden["efectividadPorFuente"]}
    postulantes = [r["postulantes"] for r in page["efectividad_por_fuente"]["filas"]]
    assert postulantes == sorted(postulantes, reverse=True)


def test_parity_c8_tiempo_de_cobertura_por_busqueda(parity):
    _f, page, golden = parity
    engine = sorted((r["nombre_puesto"], r["dias"]) for r in page["tiempo_cobertura_por_busqueda"]["filas"])
    assert engine == sorted((r["nombrePuesto"], r["dias"]) for r in golden["timeToFillPorProceso"])


def test_parity_c9_canales_for_every_period_option_and_the_period_list(parity):
    facts, _page, golden = parity
    assert ind.build_canales(facts, "todos")["periodos"] == golden["periodosCanal"]
    for periodo, original in golden["canales"].items():
        got = ind.build_canales(facts, periodo)
        assert got["periodo"] == periodo
        assert [(r["canal"], r["avisos"], r["candidatos"], r["contratados"], r["conversion"], r["contrataciones_por_aviso"]) for r in got["filas"]] == [
            (r["canal"], r["avisos"], r["candidatos"], r["contratados"], r["conversion"], r["contratacionesPorAviso"]) for r in original]
        assert got["sin_datos"] == all(r["avisos"] == 0 and r["candidatos"] == 0 for r in original)


# ═════════════════ redondeo en indicadores ═════════════════

def test_k1_k2_k3_round_half_up_not_to_even():
    busquedas = [make_busqueda("b1", "A", apertura="2026-01-01", cierre="2026-01-11"), make_busqueda("b2", "B", apertura="2026-02-01", cierre="2026-02-16")]
    assert ind.kpis(facts_of(busquedas=busquedas))["tiempo_cobertura"]["valor"] == 13          # (10 + 15) / 2 = 12,5 → 13 (round() de Python daría 12)
    apps = [ingreso_app("a1", "p1", "2026-01-01T00:00:00+00:00", "2026-01-05T00:00:00+00:00"), ingreso_app("a2", "p2", "2026-02-01T00:00:00+00:00", "2026-02-06T00:00:00+00:00")]
    assert ind.kpis(facts_of(busquedas=[make_busqueda("b", "X", candidatos=apps)]))["tiempo_contratacion"]["valor"] == 5   # (4 + 5) / 2 = 4,5 → 5
    una = [ingreso_app("a0", "p0", "2026-01-01T00:00:00+00:00", "2026-01-03T00:00:00+00:00")] + [make_app(f"a{i}", f"p{i}") for i in range(1, 8)]
    assert ind.kpis(facts_of(busquedas=[make_busqueda("b", "X", candidatos=una)]))["tasa_conversion"]["valor"] == 13   # 1 de 8 = 12,5 % → 13


def test_c9_contrataciones_por_aviso_uses_three_decimals_half_up():
    def row(avisos, contratados):
        apps = [ingreso_app(f"a{i}", f"p{i}", "2026-01-01T00:00:00+00:00", "2026-01-05T00:00:00+00:00") for i in range(contratados)]
        f = facts_of(busquedas=[make_busqueda("b", "X", candidatos=apps)], publicaciones=[make_publicacion("pub", "Aviso", avisos=avisos)],
                     fuentes={f"p{i}": "Aviso" for i in range(contratados)})
        return ind.build_canales(f)["filas"][0]["contrataciones_por_aviso"]
    assert row(3, 1) == 0.333 and row(3, 2) == 0.667 and row(8, 1) == 0.125 and row(2000, 1) == 0.001
    assert row(0 + 1, 0) == 0.0


# ═════════════════ fechas históricas, fallbacks y procedencia ═════════════════

def test_ingreso_date_is_the_first_arrival_to_finalizado_and_is_always_flagged_as_derived():
    historial = [{"etapa": "Preselección", "fecha": "2026-01-01T10:00:00+00:00"}, {"etapa": "Finalizado", "fecha": "2026-01-09T10:00:00+00:00"}, {"etapa": "Finalizado", "fecha": "2026-01-20T10:00:00+00:00"}]
    app = make_app("a", "p", etapa="Finalizado", resultado="Ingreso", alta="2026-01-01T10:00:00+00:00", movimiento="2026-01-20T10:00:00+00:00", historial=historial)
    c = facts_of(busquedas=[make_busqueda("b", "X", candidatos=[app])]).candidaturas[0]
    assert c.ingreso_fecha == ind.parse_utc("2026-01-09T10:00:00+00:00") and c.ingreso_origen == ind.ORIGEN_INGRESO_HISTORIAL
    assert ind.dias_entre(c.alta, c.ingreso_fecha) == 8                                         # no 19: la segunda llegada (asignación del resultado) no cuenta


def test_ingreso_without_historial_falls_back_to_last_movement_and_a_missing_date_is_excluded_not_invented():
    sin_historial = ingreso_app("a1", "p1", "2026-01-01T00:00:00+00:00", "2026-01-11T00:00:00+00:00", historial=False)
    sin_fecha = make_app("a2", "p2", etapa="Finalizado", resultado="Ingreso", alta="2026-01-01T00:00:00+00:00", movimiento="basura", historial=False)
    facts = facts_of(busquedas=[make_busqueda("b", "X", candidatos=[sin_historial, sin_fecha])])
    by_id = {c.application_id: c for c in facts.candidaturas}
    assert by_id["a1"].ingreso_origen == ind.ORIGEN_INGRESO_ULTIMO_MOVIMIENTO and by_id["a2"].ingreso_fecha is None and by_id["a2"].ingreso_origen is None
    k = ind.kpis(facts)
    assert k["total_contratados"]["valor"] == 2                                                 # ambos son Ingreso
    assert k["tiempo_contratacion"]["n"] == 1 and k["tiempo_contratacion"]["valor"] == 10        # solo el que tiene fecha entra al promedio
    assert k["tiempo_contratacion"]["fechas"] == {"ingresos": 2, "con_fecha": 1, "desde_historial": 0, "desde_ultimo_movimiento": 1, "sin_fecha": 1}
    assert ind.ingresos_por_mes(facts)["fechas"]["sin_fecha"] == 1


def test_interview_date_chain_each_link_and_its_origin():
    apps = [make_app("a-mov", "p1", alta="2026-01-01T00:00:00+00:00", movimiento="2026-02-10T00:00:00+00:00"),
            make_app("a-alta", "p2", alta="2026-03-01T00:00:00+00:00", movimiento="basura", historial=False)]
    entrevistas = [
        make_entrevista("e1", "p1", busqueda_id="b", application_id="a-mov", fecha="2026-05-05", created_at="2026-01-02T00:00:00+00:00"),    # 1. fecha cargada
        make_entrevista("e2", "p1", busqueda_id="b", application_id="a-mov", fecha=None, created_at="2026-01-02T00:00:00+00:00"),              # 2. último movimiento de la candidatura
        make_entrevista("e3", "p2", busqueda_id="b", application_id="a-alta", fecha=None, created_at="2026-01-02T00:00:00+00:00"),             # 3. alta de la candidatura
        make_entrevista("e4", "p3", busqueda_id="b", application_id="ghost", fecha="no-es-fecha", created_at="2026-04-04T00:00:00+00:00"),     # 4. alta de la entrevista (candidatura removida)
        make_entrevista("e5", "p4", fecha=None, created_at="2026-06-06T00:00:00+00:00"),                                                       # manual: alta de la entrevista
        make_entrevista("e6", "p5", fecha=None, created_at="ilegible"),                                                                        # sin ninguna fecha
    ]
    got = {e.entrevista_id: (e.fecha.date().isoformat() if e.fecha else None, e.fecha_origen) for e in facts_of(busquedas=[make_busqueda("b", "X", candidatos=apps)], entrevistas=entrevistas).entrevistas}
    assert got == {"e1": ("2026-05-05", "registrada"), "e2": ("2026-02-10", "ultimo_movimiento_candidatura"), "e3": ("2026-03-01", "alta_candidatura"),
                   "e4": ("2026-04-04", "alta_entrevista"), "e5": ("2026-06-06", "alta_entrevista"), "e6": (None, None)}


def test_interview_charts_report_how_many_dates_are_estimated_and_how_many_are_missing():
    entrevistas = [make_entrevista("e1", "p1", fecha="2026-01-10"), make_entrevista("e2", "p2", fecha=None, created_at="2026-01-12T00:00:00+00:00"),
                   make_entrevista("e3", "p3", fecha=None, created_at="ilegible")]
    facts = facts_of(entrevistas=entrevistas)
    meta = ind.entrevistas_por_anio(facts)["fechas"]
    assert meta == {"total": 3, "registradas": 1, "estimadas": 1, "sin_fecha": 1}
    assert ind.entrevistas_por_anio(facts)["filas"] == [{"anio": "2026", "realizadas": 2, "no_realizadas": 0, "total": 2}]        # la entrevista sin fecha no se ubica en ningún año
    assert ind.kpis(facts)["entrevistas_realizadas"]["valor"] == 3                                                                # pero sí cuenta como realizada (K6 no depende de la fecha)
    assert ind.build_perfiles(facts)["fechas"]["estimadas"] == 1


# ═════════════════ cardinalidad N y ventanas exactas ═════════════════

def test_c1_top_10_and_total_with_twelve_searches():
    busquedas = [make_busqueda(f"b{i:02d}", f"Puesto {i:02d}", candidatos=[make_app(f"a{i}-{j}", f"p{j}") for j in range(i + 1)]) for i in range(12)] + [make_busqueda("vacia", "Sin nadie")]
    got = ind.postulantes_por_busqueda(facts_of(busquedas=busquedas))
    assert len(got["filas"]) == 10 and got["total_busquedas"] == 12                              # las de 0 no cuentan
    assert [r["cantidad"] for r in got["filas"]] == list(range(12, 2, -1)) and got["filas"][0]["ancho_pct"] == 100.0


def test_c2_and_c3_keep_the_last_six_months_with_movement_in_chronological_order():
    busquedas = [make_busqueda(f"b{m}", f"P{m}", apertura=f"2026-{m:02d}-05") for m in (1, 2, 3, 5, 6, 8, 9, 11)]            # 8 meses con huecos
    got = ind.busquedas_por_mes(facts_of(busquedas=busquedas))
    assert [r["mes"] for r in got["filas"]] == ["2026-03", "2026-05", "2026-06", "2026-08", "2026-09", "2026-11"]
    assert [r["mes"] for r in ind.busquedas_por_mes(facts_of(busquedas=busquedas), None)["filas"]][0] == "2026-01"            # `None` = todos los meses
    ingresos = [ingreso_app(f"a{m}", f"p{m}", f"2026-{m:02d}-01T00:00:00+00:00", f"2026-{m:02d}-09T00:00:00+00:00") for m in (1, 2, 3, 4, 6, 7, 9)]
    got = ind.ingresos_por_mes(facts_of(busquedas=[make_busqueda("b", "X", candidatos=ingresos)]))
    assert len(got["filas"]) == 6 and got["filas"][0]["mes"] == "2026-02" and got["filas"][-1]["mes"] == "2026-09"


def test_c2_opened_and_closed_in_the_same_month_counts_in_both_columns_and_bars_normalize_against_the_max():
    busquedas = [make_busqueda("b1", "A", apertura="2026-01-03", cierre="2026-01-20", estado="cerrado"), make_busqueda("b2", "B", apertura="2026-01-10"), make_busqueda("b3", "C", apertura="2026-02-02")]
    rows = ind.busquedas_por_mes(facts_of(busquedas=busquedas))["filas"]
    assert [(r["mes"], r["abiertas"], r["cerradas"], r["total"]) for r in rows] == [("2026-01", 2, 1, 3), ("2026-02", 1, 0, 1)]
    assert rows[0]["ancho_abiertas_pct"] == round(2 / 3 * 100, 2) and rows[0]["ancho_cerradas_pct"] == round(1 / 3 * 100, 2) and rows[1]["ancho_abiertas_pct"] == round(1 / 3 * 100, 2)


def test_c5_has_no_window_and_c6_lists_everything_while_charting_the_top_10():
    entrevistas = [make_entrevista(f"e{i}", f"p{i}", fecha=f"2025-{(i % 12) + 1:02d}-10", puesto=f"Perfil {i:02d}") for i in range(14)]
    facts = facts_of(entrevistas=entrevistas)
    assert len(ind.entrevistas_por_mes(facts)["filas"]) == 12                                    # historial completo, sin recorte a 6
    perfiles = ind.build_perfiles(facts)
    assert len(perfiles["filas"]) == 14 and len(perfiles["top"]) == 10


# ═════════════════ consistencia cruzada KPI ↔ tarjetas ═════════════════

def test_cross_consistency_between_kpis_and_cards_on_the_parity_dataset(parity):
    facts, page, _golden = parity
    k = page["kpis"]
    por_fuente = page["efectividad_por_fuente"]["filas"]
    assert k["total_contratados"]["valor"] == sum(r["contratados"] for r in por_fuente)
    assert k["tasa_conversion"]["candidaturas"] == sum(r["postulantes"] for r in por_fuente) == len(facts.candidaturas)
    assert k["total_contratados"]["valor"] + k["total_descartados"]["valor"] <= len(facts.candidaturas)
    assert page["entrevistas_por_anio"]["fechas"]["sin_fecha"] == 0
    anual = {r["anio"]: (r["realizadas"], r["no_realizadas"]) for r in page["entrevistas_por_anio"]["filas"]}
    mensual: dict[str, list[int]] = {}
    for r in page["entrevistas_por_mes"]["filas"]:
        acc = mensual.setdefault(r["mes"][:4], [0, 0])
        acc[0] += r["realizadas"]
        acc[1] += r["no_realizadas"]
    assert anual == {y: tuple(v) for y, v in mensual.items()}                                   # C4 = agregación anual de C5
    assert k["entrevistas_realizadas"]["valor"] == sum(r[0] for r in anual.values())               # K6 = Σ realizadas
    todos = ind.ingresos_por_mes(facts, None)
    assert sum(r["total"] for r in todos["filas"]) == k["tiempo_contratacion"]["fechas"]["con_fecha"]
    assert all(sum(s["valor"] for s in r["segmentos"]) == r["total"] for r in todos["filas"])


# ═════════════════ catálogos dinámicos y valores históricos ═════════════════

def modalidad_facts(catalogo, modalidades):
    apps = [ingreso_app(f"a{i}", f"p{i}", "2026-01-01T00:00:00+00:00", "2026-01-09T00:00:00+00:00") for i in range(len(modalidades))]
    entrevistas = [make_entrevista(f"e{i}", f"p{i}", busqueda_id="b", application_id=f"a{i}", modalidad=m) for i, m in enumerate(modalidades) if m != "<sin entrevista>"]
    return facts_of(busquedas=[make_busqueda("b", "X", candidatos=apps)], entrevistas=entrevistas, catalogos={"modalidad_contratacion": tuple(catalogo)})


def test_c3_series_follow_the_catalog_order_then_historical_values_then_sin_especificar():
    got = ind.ingresos_por_mes(modalidad_facts(("Zeta", "Alfa"), ["Alfa", "Zeta", "Zeta", "Legado B", "legado a", "<sin entrevista>", None]))
    assert got["series"] == ["Zeta", "Alfa", "legado a", "Legado B"]                              # catálogo en su orden + históricos (casefold) al final
    seg = {s["clave"]: s["valor"] for s in got["filas"][0]["segmentos"]}
    assert seg == {"Zeta": 2, "Alfa": 1, "legado a": 1, "Legado B": 1, ind.SIN_ESPECIFICAR: 2} and got["filas"][0]["total"] == 7
    assert got["filas"][0]["segmentos"][-1]["sin_especificar"] is True


def test_c3_with_an_empty_catalog_still_works_using_only_what_the_data_has():
    got = ind.ingresos_por_mes(modalidad_facts((), ["Remoto", None]))
    assert got["series"] == ["Remoto"] and [s["valor"] for s in got["filas"][0]["segmentos"]] == [1, 1]
    assert ind.ingresos_por_mes(modalidad_facts((), [None]))["series"] == []


def test_c9_rows_are_the_catalog_plus_the_channels_present_in_publications_and_work_with_an_empty_catalog():
    pubs = [make_publicacion("p1", "Bumeran", avisos=2), make_publicacion("p2", "Computrabajo", avisos=1), make_publicacion("p3", "Bumeran", avisos=1)]
    con_catalogo = ind.build_canales(facts_of(publicaciones=pubs, catalogos={"canal_publicacion": ("LinkedIn Jobs", "Bumeran")}))
    assert [(f["canal"], f["avisos"]) for f in con_catalogo["filas"]] == [("LinkedIn Jobs", 0), ("Bumeran", 3), ("Computrabajo", 1)]
    sin_catalogo = ind.build_canales(facts_of(publicaciones=pubs))
    assert [f["canal"] for f in sin_catalogo["filas"]] == ["Bumeran", "Computrabajo"] and sin_catalogo["sin_datos"] is False
    assert ind.build_canales(facts_of())["sin_datos"] is True and ind.build_canales(facts_of())["filas"] == []


def test_c9_matches_channel_and_source_by_exact_name_only_and_says_so():
    app = ingreso_app("a1", "p1", "2026-01-01T00:00:00+00:00", "2026-01-09T00:00:00+00:00")
    other = make_app("a2", "p2")
    facts = facts_of(busquedas=[make_busqueda("b", "X", candidatos=[app, other])], publicaciones=[make_publicacion("pub", "LinkedIn", avisos=2), make_publicacion("pub2", "Flyer")],
                     fuentes={"p1": "LinkedIn", "p2": "linkedin"})                                  # «linkedin» (otra capitalización) NO coincide
    rows = {r["canal"]: r for r in ind.build_canales(facts)["filas"]}
    assert rows["LinkedIn"]["candidatos"] == 1 and rows["LinkedIn"]["contratados"] == 1 and rows["Flyer"]["candidatos"] == 0
    assert ind.build_canales(facts)["coincidencia_por_nombre"] is True


def test_c7_source_comes_from_the_person_including_soft_deleted_and_missing_profiles():
    apps = [make_app("a1", "p-baja"), make_app("a2", "p-activo"), make_app("a3", "p-sin-fuente"), make_app("a4", "p-inexistente")]
    rows = {r["fuente"]: r for r in ind.efectividad_por_fuente(facts_of(
        busquedas=[make_busqueda("b", "X", candidatos=apps)], fuentes={"p-baja": "Referido", "p-activo": "Referido", "p-sin-fuente": None}))["filas"]}
    assert rows["Referido"]["postulantes"] == 2 and rows[ind.SIN_ESPECIFICAR]["postulantes"] == 2


def test_the_engine_hardcodes_no_governed_value():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    forbidden = {"Full time", "Part time", "Full-Time", "Part-Time", "Full Time", "Part Time", "LinkedIn", "Flyer", "Referido", "Hunting"}
    assert not (literals & forbidden), literals & forbidden


# ═════════════════ ausencia de datos ═════════════════

def test_empty_snapshot_yields_empty_states_never_errors():
    page = ind.build_page(facts_of())
    k = page["kpis"]
    assert k["tiempo_cobertura"]["valor"] is None and k["tiempo_contratacion"]["valor"] is None
    assert k["tasa_conversion"]["valor"] == 0 and k["total_contratados"]["valor"] == 0 and k["total_descartados"]["valor"] == 0 and k["entrevistas_realizadas"]["valor"] == 0
    for key in ("postulantes_por_busqueda", "busquedas_por_mes", "ingresos_por_mes", "entrevistas_por_anio", "entrevistas_por_mes", "perfiles_entrevistados",
                "efectividad_por_fuente", "tiempo_cobertura_por_busqueda", "canales_aviso", "busquedas_estancadas"):
        assert page[key]["filas"] == [], key
    assert page["canales_aviso"]["sin_datos"] is True and page["perfiles_entrevistados"]["periodos"] == []
    assert [t["hoja"] for t in ind.export_tables(facts_of())] == EXPECTED_SHEETS


def test_a_search_with_unreadable_dates_is_skipped_by_the_date_based_indicators():
    facts = facts_of(busquedas=[make_busqueda("b", "X", apertura="mañana", cierre="2026-01-01", estado="cerrado")])
    assert ind.kpis(facts)["tiempo_cobertura"]["valor"] is None and ind.tiempo_cobertura_por_busqueda(facts)["filas"] == []
    assert [r["mes"] for r in ind.busquedas_por_mes(facts)["filas"]] == ["2026-01"]                # solo cuenta el cierre legible; la apertura ilegible no inventa un mes


# ═════════════════ filtros locales independientes (C6 / C9) ═════════════════

def filter_facts():
    apps = [ingreso_app("a1", "p1", "2026-02-01T00:00:00+00:00", "2026-02-09T00:00:00+00:00"), make_app("a2", "p2", alta="2026-05-01T00:00:00+00:00")]
    return facts_of(busquedas=[make_busqueda("b", "Contador", candidatos=apps)],
                    entrevistas=[make_entrevista("e1", "p1", busqueda_id="b", application_id="a1", fecha="2026-02-10"), make_entrevista("e2", "p2", busqueda_id="b", application_id="a2", fecha="2026-05-10")],
                    publicaciones=[make_publicacion("pub", "LinkedIn", avisos=2, periodo="2026-Q3")], fuentes={"p1": "LinkedIn", "p2": "LinkedIn"},
                    catalogos={"canal_publicacion": ("LinkedIn",)})


def test_c6_and_c9_are_independent_each_with_its_own_options_and_none_changes_the_other():
    facts = filter_facts()
    perfiles_q1, canales_todos = ind.build_perfiles(facts, "2026-Q1"), ind.build_canales(facts)
    assert perfiles_q1["periodo"] == "2026-Q1" and [f["cantidad"] for f in perfiles_q1["filas"]] == [1] and perfiles_q1["periodos"] == ["2026-Q2", "2026-Q1"]
    canales_q3 = ind.build_canales(facts, "2026-Q3")
    assert canales_q3["periodo"] == "2026-Q3" and canales_q3["filas"][0]["avisos"] == 2 and canales_q3["filas"][0]["candidatos"] == 0
    assert canales_todos["filas"][0]["candidatos"] == 2 and canales_todos["periodos"] == ["2026-Q3", "2026-Q2", "2026-Q1"]
    # elegir un período en una tarjeta no altera la otra ni los KPI: las funciones no comparten estado
    assert ind.build_canales(facts) == canales_todos and ind.build_perfiles(facts, "2026-Q1") == perfiles_q1
    page = ind.build_page(facts)
    assert page["perfiles_entrevistados"]["periodo"] == "todos" and page["canales_aviso"]["periodo"] == "todos"       # la página arranca con ambos en «todos»
    assert page["kpis"] == ind.kpis(facts)


@pytest.mark.parametrize("requested", ["2099-Q1", "basura", "", "  ", None, "2026-q1", "TODOS"])
def test_an_unknown_period_degrades_to_the_full_history_for_both_filters(requested):
    facts = filter_facts()
    for builder in (ind.build_perfiles, ind.build_canales):
        got = builder(facts, requested)
        assert got["periodo"] == "todos"
        assert got["periodo_valido"] is (requested in ("", "  ", None))                           # vacío = no pidió nada: válido


def test_a_period_option_exists_only_when_there_is_data_for_it():
    facts = filter_facts()
    assert ind.build_perfiles(facts)["periodos"] == ["2026-Q2", "2026-Q1"]
    assert "2026-Q3" not in ind.build_perfiles(facts)["periodos"] and "2026-Q3" in ind.build_canales(facts)["periodos"]


# ═════════════════ ausencia de N+1 y de efectos secundarios ═════════════════

def test_load_snapshot_reads_each_collection_once_and_never_per_candidacy(monkeypatch):
    calls: dict[str, int] = {}

    def counted(name, value):
        def fn(*a, **kw):
            calls[name] = calls.get(name, 0) + 1
            return value
        return fn

    def forbidden(name):
        def fn(*a, **kw):
            raise AssertionError(f"consulta prohibida: {name}")
        return fn
    apps = [make_app(f"a{i}", f"p{i}") for i in range(120)]
    monkeypatch.setattr(busqueda_repository, "list", counted("busquedas", [make_busqueda("b", "X", candidatos=apps)]))
    monkeypatch.setattr(entrevista_repository, "list", counted("entrevistas", []))
    monkeypatch.setattr(publicacion_repository, "list", counted("publicaciones", []))
    monkeypatch.setattr(profile_repository, "list", counted("perfiles", [{"profile_id": f"p{i}", "source": "LinkedIn"} for i in range(120)]))
    monkeypatch.setattr(catalog_repo, "catalog_values", counted("catalogos", []))
    for repo, names in ((busqueda_repository, ("get",)), (entrevista_repository, ("get", "find_by_profile_and_busqueda")), (publicacion_repository, ("get",)), (profile_repository, ("get",))):
        for name in names:
            monkeypatch.setattr(repo, name, forbidden(f"{repo.__name__}.{name}"))
    snapshot = ind.load_snapshot()
    assert calls == {"busquedas": 1, "entrevistas": 1, "publicaciones": 1, "perfiles": 1, "catalogos": 2}
    # tras la lectura, el cálculo no vuelve a tocar ningún repositorio (todos pasan a prohibidos)
    for repo in (busqueda_repository, entrevista_repository, publicacion_repository, profile_repository):
        monkeypatch.setattr(repo, "list", forbidden(f"{repo.__name__}.list"))
    monkeypatch.setattr(catalog_repo, "catalog_values", forbidden("catalog_values"))
    facts = ind.build_facts(snapshot)
    ind.build_page(facts), ind.build_perfiles(facts, "2026-Q1"), ind.build_canales(facts, "2026-Q1"), ind.export_tables(facts)
    assert len(facts.candidaturas) == 120 and facts.candidaturas[0].fuente == "LinkedIn"


def tree_state(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(root.rglob("*")) if p.is_file()}


def test_reading_and_computing_has_no_side_effects_on_disk_or_remote(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    for mod in (busqueda_repository, entrevista_repository, publicacion_repository, profile_repository):
        monkeypatch.setattr(mod.remote_store, "enabled", lambda: False)

        def boom(*a, **kw):
            raise AssertionError("escritura prohibida en una operación de lectura")
        monkeypatch.setattr(mod.remote_store, "push_path", boom)
        monkeypatch.setattr(mod.remote_store, "delete_path", boom)
    base = tmp_path / "human-capital"
    docs = {"busquedas": make_busqueda("b1", "X", candidatos=[ingreso_app("a1", "p1", "2026-01-01T00:00:00+00:00", "2026-01-09T00:00:00+00:00")]),
            "entrevistas": make_entrevista("e1", "p1", busqueda_id="b1", application_id="a1", fecha="2026-01-05", modalidad="Full time"),
            "publicaciones": make_publicacion("pub1", "LinkedIn"), "profiles": {"profile_id": "p1", "source": "LinkedIn", "deleted_at": "2026-02-01T00:00:00+00:00"}}
    for sub, doc in docs.items():
        (base / sub).mkdir(parents=True)
        key = {"busquedas": "busqueda_id", "entrevistas": "entrevista_id", "publicaciones": "publicacion_id", "profiles": "profile_id"}[sub]
        (base / sub / f"{doc[key]}.json").write_text(json.dumps(doc), encoding="utf-8")
    (base / "catalogs").mkdir()
    (base / "catalogs" / "catalogos.json").write_text(json.dumps({"schema_version": 1, "catalogs": {"modalidad_contratacion": [{"valor": "Full time", "orden": 1}]}}), encoding="utf-8")
    before = tree_state(tmp_path)
    snapshot = ind.load_snapshot()
    facts = ind.build_facts(snapshot)
    page = ind.build_page(facts)
    ind.export_tables(facts), ind.build_perfiles(facts, "2026-Q1"), ind.build_canales(facts, "2026-Q1")
    assert tree_state(tmp_path) == before
    assert snapshot.fuentes == {"p1": "LinkedIn"} and snapshot.catalogos["modalidad_contratacion"] == ("Full time",)         # el perfil dado de baja sigue contando (D9)
    assert page["efectividad_por_fuente"]["filas"][0]["fuente"] == "LinkedIn" and page["kpis"]["total_contratados"]["valor"] == 1


def test_the_engine_module_has_no_http_template_or_xlsx_dependencies_and_only_reads_from_repositories():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not imported & {"fastapi", "starlette", "jinja2", "openpyxl", "xlsxwriter", "requests", "urllib", "httpx"}
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(m and ("routers" in m or "templates" in m) for m in modules)
    used = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in {"busqueda_repository", "entrevista_repository", "publicacion_repository", "profile_repository", "catalog_repo"}:
            used.setdefault(node.value.id, set()).add(node.attr)
    allowed_reads = {"list", "catalog_values", "ETAPA_FINAL"}
    assert all(attrs <= allowed_reads for attrs in used.values()), used


# ═════════════════ determinismo (empates, orden de entrada) ═════════════════

def test_results_do_not_depend_on_the_input_order_and_ties_break_by_name_then_id():
    raw = json.loads((DATA / "dataset.json").read_text(encoding="utf-8"))
    base = ind.build_page(ind.build_facts(snapshot_from_raw(raw)))
    rnd = random.Random(7)
    shuffled = {k: (list(v) if isinstance(v, list) else v) for k, v in raw.items()}
    for key in ("busquedas", "entrevistas", "publicaciones"):
        rnd.shuffle(shuffled[key])
    for b in shuffled["busquedas"]:
        b["candidatos"] = list(b["candidatos"])
        rnd.shuffle(b["candidatos"])
    assert ind.build_page(ind.build_facts(snapshot_from_raw(shuffled))) == base
    empate = [make_busqueda("b2", "beta", candidatos=[make_app("a1", "p1")]), make_busqueda("b1", "Alfa", candidatos=[make_app("a2", "p2")]), make_busqueda("b3", "alfa", candidatos=[make_app("a3", "p3")])]
    assert [r["busqueda_id"] for r in ind.postulantes_por_busqueda(facts_of(busquedas=empate))["filas"]] == ["b1", "b3", "b2"]


def test_the_page_and_the_export_are_json_serializable():
    raw = json.loads((DATA / "dataset.json").read_text(encoding="utf-8"))
    facts = ind.build_facts(snapshot_from_raw(raw))
    json.dumps(ind.build_page(facts)), json.dumps(ind.export_tables(facts))


# ═════════════════ C10 estancadas + reutilización de las funciones canónicas ═════════════════

class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 20, 12, 0, 0)


def test_c10_uses_the_canonical_stagnation_rule_with_the_threshold_of_30_days_and_only_active_searches(monkeypatch):
    monkeypatch.setattr(hc, "datetime", _FrozenDatetime)
    mov = lambda dias_atras: f"2026-{(datetime(2026, 10, 20) - __import__('datetime').timedelta(days=dias_atras)).strftime('%m-%d')}T12:00:00+00:00"  # noqa: E731
    busquedas = [
        make_busqueda("b30", "Treinta", candidatos=[make_app("a", "p", alta=mov(60), movimiento=mov(30))]),
        make_busqueda("b29", "Veintinueve", candidatos=[make_app("a", "p", alta=mov(60), movimiento=mov(29))]),
        make_busqueda("b45", "Zeta sin candidatos", apertura="2026-09-05"),
        make_busqueda("b45b", "Alfa sin candidatos", apertura="2026-09-05"),
        make_busqueda("bcer", "Cerrada", estado="cerrado", apertura="2026-01-01", cierre="2026-02-01", candidatos=[make_app("a", "p", alta=mov(90), movimiento=mov(90))]),
        make_busqueda("bmal", "Fecha ilegible", apertura="ayer", candidatos=[make_app("a", "p", alta="mal", movimiento="mal")]),
    ]
    got = ind.busquedas_estancadas(facts_of(busquedas=busquedas))
    assert got["umbral_dias"] == hc.BUSQUEDA_UMBRAL_ESTANCADA_DIAS == 30
    assert [(f["busqueda_id"], f["dias"]) for f in got["filas"]] == [("b45b", 45), ("b45", 45), ("b30", 30)]       # ≥ 30; desempate por nombre; sin cerradas ni ilegibles
    for f in got["filas"]:                                                                                            # coincide con el badge de Búsquedas (misma función)
        doc = next(b for b in busquedas if b["busqueda_id"] == f["busqueda_id"])
        assert hc.busqueda_attention(doc)["estancada"] is True and hc.busqueda_attention(doc)["dias_sin_movimiento"] == f["dias"]


def test_the_engine_delegates_to_the_canonical_hub_functions(monkeypatch):
    facts = filter_facts()
    assert ind.kpis(facts)["total_contratados"]["valor"] == 1
    monkeypatch.setattr(hc, "is_ingreso_application", lambda app: False)                      # ingreso: función canónica
    assert ind.kpis(ind.build_facts(make_snapshot(busquedas=[make_busqueda("b", "X", candidatos=[ingreso_app("a", "p", "2026-01-01T00:00:00+00:00", "2026-01-09T00:00:00+00:00")])])))["total_contratados"]["valor"] == 0
    monkeypatch.setattr(hc, "periodo_desde_fecha", lambda d: "TRIM-X")                         # trimestre: función canónica
    assert ind.build_perfiles(facts)["periodos"] == ["TRIM-X"]
    monkeypatch.setattr(hc, "busqueda_attention", lambda doc, pendiente=0: {"dias_sin_movimiento": 99, "estancada": True})   # estancamiento: función canónica
    assert ind.busquedas_estancadas(facts_of(busquedas=[make_busqueda("b", "Activa")]))["filas"][0]["dias"] == 99


# ═════════════════ exportación: las 11 hojas salen del mismo motor ═════════════════

EXPECTED_SHEETS = ["Resumen", "Postulantes por búsqueda", "Búsquedas por mes", "Ingresos por mes", "Efectividad por fuente", "Tiempo de cobertura", "Búsquedas estancadas",
                   "Entrevistas por año", "Entrevistas por período", "Perfiles más entrevistados", "Efectividad por canal"]


def test_export_tables_are_the_eleven_original_sheets_in_order_with_original_headers(parity):
    facts, page, _g = parity
    tables = ind.export_tables(facts)
    assert [t["hoja"] for t in tables] == EXPECTED_SHEETS
    headers = {t["hoja"]: t["encabezados"] for t in tables}
    assert headers["Resumen"] == ["Indicador", "Valor"] and headers["Búsquedas por mes"] == ["Mes", "Abiertas", "Cerradas"]
    assert headers["Efectividad por canal"] == ["Canal", "Avisos", "Candidatos", "Contratados", "Conversión %", "Contrataciones por aviso"]
    assert headers["Ingresos por mes"] == ["Mes", "Full time", "Part time", ind.SIN_ESPECIFICAR, "Total"]       # series dinámicas: aquí las del catálogo del test
    assert all(len(f) == len(t["encabezados"]) for t in tables for f in t["filas"])
    resumen = dict(map(tuple, tables[0]["filas"]))
    assert resumen["Total contratados"] == page["kpis"]["total_contratados"]["valor"] and resumen["Tasa de conversión"] == f"{page['kpis']['tasa_conversion']['valor']}%"
    assert resumen["Tiempo de cobertura prom."] == f"{page['kpis']['tiempo_cobertura']['valor']} días"


def test_export_is_always_the_full_history_for_the_two_local_filters_and_the_months_keep_the_original_window(parity):
    facts, page, _g = parity
    tables = {t["hoja"]: t for t in ind.export_tables(facts)}
    assert len(tables["Perfiles más entrevistados"]["filas"]) == len(page["perfiles_entrevistados"]["filas"]) > len(page["perfiles_entrevistados"]["top"])
    assert len(tables["Efectividad por canal"]["filas"]) == len(page["canales_aviso"]["filas"])
    assert page["canales_aviso"]["periodo"] == "todos" and page["perfiles_entrevistados"]["periodo"] == "todos"
    assert len(tables["Búsquedas por mes"]["filas"]) <= 6
    assert len(next(t for t in ind.export_tables(facts, ventana_meses=None) if t["hoja"] == "Búsquedas por mes")["filas"]) >= len(tables["Búsquedas por mes"]["filas"])


def test_export_keeps_numbers_numeric_months_raw_and_the_dash_only_where_the_original_used_it():
    facts = facts_of(busquedas=[make_busqueda("b", "=SUMA(A1)", apertura="2026-01-01", cierre="2026-01-11", estado="cerrado", candidatos=[ingreso_app("a", "p", "2026-01-01T00:00:00+00:00", "2026-01-05T00:00:00+00:00")])],
                     publicaciones=[make_publicacion("pub", "Canal", avisos=0 + 1)], fuentes={"p": "Canal"})
    tables = {t["hoja"]: t for t in ind.export_tables(facts)}
    assert tables["Postulantes por búsqueda"]["filas"] == [["=SUMA(A1)", 1]]                       # el motor NO neutraliza: lo hace la capa de exportación (Bloque C)
    assert tables["Búsquedas por mes"]["filas"][0][0] == "2026-01" and isinstance(tables["Búsquedas por mes"]["filas"][0][1], int)
    canal = tables["Efectividad por canal"]["filas"][0]
    assert canal == ["Canal", 1, 1, 1, 100, 1.0] and isinstance(canal[5], float)
    sin_avisos = ind.export_tables(facts_of(catalogos={"canal_publicacion": ("Vacío",)}))
    assert next(t for t in sin_avisos if t["hoja"] == "Efectividad por canal")["filas"] == [["Vacío", 0, 0, 0, 0, "—"]]
