"""Hotfix CCHH (punto 11) — Publicaciones: el período trimestral ya NO se elige, se DERIVA de la fecha (`human_capital_service.periodo_desde_fecha`, la ÚNICA derivación) y la dimensión trimestral de Indicadores
(tarjeta «Efectividad por canal de aviso», selector Período/Quarter) sigue funcionando sobre esa derivación. Compatibilidad histórica: sin fecha válida se usa el período guardado (sin inventar fechas ni reescribir).
Extremo a extremo con datos sintéticos: servicio, rutas, plantillas y fragmentos HTMX de Indicadores."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from webapp.services import busqueda_repository, human_capital_indicators as ind, human_capital_service as svc, publicacion_repository as prepo
from tests.webapp.test_human_capital_publicaciones import _authorized_and_isolated, _client, _get, _names, _options, _pub, _total  # noqa: F401  (fixture autouse + helpers)

HX = {"HX-Request": "true"}


def _create(c, fecha="2026-03-31", canal="Bumeran", avisos="1", posicion="Puesto", **extra):
    return c.post("/cchh/publicaciones/nueva", data={"busqueda_id": "", "posicion": posicion, "canal": canal, "cantidad_avisos": avisos, "fecha": fecha, "observacion": "", **extra})


def _canales(periodo="todos") -> dict:
    return ind.build_canales(ind.build_facts(ind.load_snapshot()), periodo)


def _avisos(periodo="todos") -> dict[str, int]:
    return {f["canal"]: f["avisos"] for f in _canales(periodo)["filas"]}


def _fragment(c, periodo: str) -> tuple[list[str], dict[str, int], str]:
    """(opciones del selector, {canal: avisos}, período efectivo marcado) del fragmento HTMX REAL de la tarjeta C9."""
    html = c.get("/cchh/indicadores/tarjetas/canales", params={"periodo": periodo}, headers=HX).text
    options = [o for o in re.findall(r'<option value="([^"]+)"', html.split('id="hc-ind-canales-periodo"', 1)[1].split("</select>", 1)[0]) if o != "todos"]       # «Histórico completo» siempre está
    rows = {m[0]: int(m[1]) for m in re.findall(r'<tr><td class="left">([^<]+)</td><td>(\d+)</td>', html)}
    selected = re.search(r'<option value="([^"]+)" selected', html.split('id="hc-ind-canales-periodo"', 1)[1]).group(1)
    return options, rows, selected


# ═══════════════════ derivación única y límites trimestrales ═══════════════════
@pytest.mark.parametrize("fecha,periodo", [("2026-03-31", "2026-Q1"), ("2026-04-01", "2026-Q2"), ("2026-06-30", "2026-Q2"), ("2026-07-01", "2026-Q3"), ("2026-09-30", "2026-Q3"),
                                           ("2026-10-01", "2026-Q4"), ("2026-12-31", "2026-Q4"), ("2027-01-01", "2027-Q1"), ("2026-01-01", "2026-Q1"), ("2024-02-29", "2024-Q1")])
def test_the_quarter_comes_from_the_fecha_with_exact_boundaries_and_year_change(fecha, periodo):
    assert svc.periodo_desde_fecha(date.fromisoformat(fecha)) == periodo
    p = _pub(fecha=fecha)
    assert (p["fecha"], p["periodo"]) == (fecha, periodo) and svc.periodo_efectivo(p) == periodo


def test_there_is_a_single_quarter_derivation_in_the_code_base():
    source = Path(__file__).resolve().parents[2] / "webapp"
    hits = [path.relative_to(source).as_posix() for path in source.rglob("*.py") if "// 3 + 1" in path.read_text(encoding="utf-8") and "human_capital" in path.name + str(path.parent)]
    assert hits == ["services/human_capital_service.py"], hits                                      # Publicaciones e Indicadores reutilizan `periodo_desde_fecha`; nadie la reimplementa


# ═══════════════════ formulario: sin Período, Fecha obligatoria y precargada ═══════════════════
def test_the_forms_have_no_period_field_and_fecha_is_required_and_prefilled_with_today_in_argentina(monkeypatch):
    c = _client()
    new = c.get("/cchh/publicaciones/nueva").text
    assert 'name="periodo"' not in new and 'id="pub-periodo"' not in new and ">Período</label>" not in new
    assert f'<input id="pub-fecha" name="fecha" type="date" required value="{svc.hoy_negocio().isoformat()}"' in new and "<span>opcional</span></label>\n            <input id=\"pub-fecha\"" not in new
    assert "El trimestre se calcula solo a partir de esta fecha" in new
    p = _pub(fecha="2026-05-10")
    edit = c.get(f"/cchh/publicaciones/{p['publicacion_id']}/editar").text
    assert 'name="periodo"' not in edit and 'type="date" required value="2026-05-10"' in edit                  # la edición muestra la fecha guardada, no la de hoy


def test_today_is_computed_in_the_business_timezone_not_utc(monkeypatch):
    real = svc.datetime

    class Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 4, 1, 1, 30, tzinfo=timezone.utc).astimezone(tz)                                # 01:30 UTC del 1/4 = 22:30 del 31/3 en Argentina
    monkeypatch.setattr(svc, "datetime", Frozen)
    assert svc.hoy_negocio() == date(2026, 3, 31) and svc.periodo_actual() == "2026-Q1"
    assert 'type="date" required value="2026-03-31"' in _client().get("/cchh/publicaciones/nueva").text         # el precargado respeta la zona de negocio (y por ende el trimestre)


def test_fecha_is_mandatory_and_the_period_sent_by_the_browser_is_ignored():
    c = _client(follow=False)
    for bad in ("", "   ", "31/03/2026", "2026-02-30", "no-es-fecha"):
        r = _create(c, fecha=bad)
        assert r.status_code == 200 and ("Ingresá la fecha de publicación" in r.text or "La fecha no es válida" in r.text), bad
    assert prepo.list() == []                                                                                   # nada se guardó
    ok = _create(c, fecha="2026-03-31", periodo="1999-Q4")                                                      # período arbitrario enviado por el navegador
    assert ok.status_code == 303
    (stored,) = prepo.list()
    assert (stored["fecha"], stored["periodo"]) == ("2026-03-31", "2026-Q1")                                    # se ignora: manda la fecha
    edited = c.post(f"/cchh/publicaciones/{stored['publicacion_id']}/editar", data={"busqueda_id": "", "posicion": "Puesto", "canal": "Bumeran", "cantidad_avisos": "1", "fecha": "2026-04-02",
                                                                                      "periodo": "2000-Q1", "observacion": "", "expected_revision": str(stored["revision"])})
    assert edited.status_code == 303 and prepo.get(stored["publicacion_id"])["periodo"] == "2026-Q2"


# ═══════════════════ Indicadores: la dimensión trimestral sigue viva sobre la fecha ═══════════════════
def test_a_publication_of_31_03_counts_in_q1_and_one_of_01_04_in_q2_and_the_year_change_goes_to_q4_and_q1():
    c = _client(follow=False)
    for fecha, avisos in (("2026-03-31", "1"), ("2026-04-01", "2"), ("2025-12-31", "4"), ("2026-01-01", "8")):
        assert _create(c, fecha=fecha, avisos=avisos).status_code == 303
    assert _avisos("2026-Q1")["Bumeran"] == 1 + 8                                                                   # 31/03 y 01/01/2026 -> Q1
    assert _avisos("2026-Q2")["Bumeran"] == 2 and _avisos("2025-Q4")["Bumeran"] == 4                               # 01/04 -> Q2 ; 31/12/2025 -> Q4 del año anterior
    assert _avisos("2026-Q1")["Bumeran"] + _avisos("2026-Q2")["Bumeran"] + _avisos("2025-Q4")["Bumeran"] == _avisos("todos")["Bumeran"] == 15       # sin doble conteo ni pérdidas


def test_quarter_filters_of_indicadores_include_and_exclude_exactly_the_right_publications():
    c = _client(follow=False)
    for fecha, canal, avisos in (("2026-03-31", "Bumeran", "1"), ("2026-04-01", "Bumeran", "2"), ("2025-12-31", "LinkedIn Jobs", "4"), ("2026-01-01", "LinkedIn Jobs", "8")):
        _create(c, fecha=fecha, canal=canal, avisos=avisos)
    assert (_avisos("2026-Q1")["Bumeran"], _avisos("2026-Q1")["LinkedIn Jobs"]) == (1, 8)
    assert (_avisos("2026-Q2")["Bumeran"], _avisos("2026-Q2")["LinkedIn Jobs"]) == (2, 0)
    assert (_avisos("2025-Q4")["Bumeran"], _avisos("2025-Q4")["LinkedIn Jobs"]) == (0, 4)
    options, rows, selected = _fragment(c, "2026-Q2")                                                            # el fragmento HTMX REAL del selector de Quarter
    assert options == ["2026-Q2", "2026-Q1", "2025-Q4"] and selected == "2026-Q2" and rows["Bumeran"] == 2 and rows["LinkedIn Jobs"] == 0
    options, rows, selected = _fragment(c, "todos")
    assert selected == "todos" and rows["Bumeran"] == 3 and rows["LinkedIn Jobs"] == 12


def test_changing_the_fecha_moves_the_publication_between_quarters_in_the_tile_and_its_metrics():
    c = _client(follow=False)
    _create(c, fecha="2026-03-31", avisos="2")
    (p,) = prepo.list()
    assert _fragment(c, "2026-Q1")[1]["Bumeran"] == 2 and _fragment(c, "2026-Q1")[0] == ["2026-Q1"]
    edit = c.post(f"/cchh/publicaciones/{p['publicacion_id']}/editar", data={"busqueda_id": "", "posicion": "Puesto", "canal": "Bumeran", "cantidad_avisos": "2", "fecha": "2026-04-01", "observacion": "",
                                                                              "expected_revision": str(p["revision"])})
    assert edit.status_code == 303 and prepo.get(p["publicacion_id"])["periodo"] == "2026-Q2"
    options, rows, selected = _fragment(c, "2026-Q1")                                                            # el trimestre viejo ya no existe y se degrada a histórico completo
    assert options == ["2026-Q2"] and selected == "todos"
    assert _fragment(c, "2026-Q2")[1]["Bumeran"] == 2 and _canales("2026-Q1")["periodo_valido"] is False and _avisos("todos")["Bumeran"] == 2      # pasó al nuevo trimestre, sin duplicarse ni quedar en el viejo
    page = _get(periodo="2026-Q2")                                                                                # el listado de Publicaciones y su resumen siguen la misma derivación
    assert _total(page) == "1" and _total(_get(periodo="2026-Q1")) == "0"


def test_historical_publications_without_fecha_keep_counting_through_their_legacy_period_and_are_never_rewritten():
    c = _client(follow=False)
    legacy = prepo.create({"busqueda_id": None, "posicion": "Histórica", "canal": "Bumeran", "cantidad_avisos": 5, "periodo": "2025-Q2", "fecha": None, "observacion": None})
    stale = prepo.create({"busqueda_id": None, "posicion": "Con fecha", "canal": "Bumeran", "cantidad_avisos": 3, "periodo": "2020-Q1", "fecha": "2026-08-10", "observacion": None})   # período guardado desfasado
    before = {p["publicacion_id"]: dict(p) for p in prepo.list()}
    assert svc.periodo_efectivo(legacy) == "2025-Q2" and svc.periodo_efectivo(stale) == "2026-Q3"                    # sin fecha: legado; con fecha: manda la fecha aunque el guardado difiera
    assert _avisos("2025-Q2")["Bumeran"] == 5 and _avisos("2026-Q3")["Bumeran"] == 3 and _avisos("2020-Q1")["Bumeran"] == 8      # '2020-Q1' no existe como opción: degrada a todos (5 + 3)
    options, rows, _ = _fragment(c, "todos")
    assert options == ["2026-Q3", "2025-Q2"] and rows["Bumeran"] == 8 and _avisos("2025-Q2")["Bumeran"] + _avisos("2026-Q3")["Bumeran"] == _avisos("todos")["Bumeran"]
    page = _get()
    assert _total(page) == "2" and _options(page, "periodo") == [("", "Todos"), ("2026-Q3", "2026-Q3"), ("2025-Q2", "2025-Q2")] and [n for n in _names(page)] == ["Con fecha", "Histórica"]
    assert {p["publicacion_id"]: dict(p) for p in prepo.list()} == before                                          # leer no reescribe nada (ni se le asignó una fecha)


def test_a_historical_publication_without_fecha_is_edited_only_after_choosing_a_date_never_with_a_silent_today():
    c = _client(follow=False)
    legacy = prepo.create({"busqueda_id": None, "posicion": "Histórica", "canal": "Bumeran", "cantidad_avisos": 1, "periodo": "2025-Q2", "fecha": None, "observacion": None})
    page = c.get(f"/cchh/publicaciones/{legacy['publicacion_id']}/editar").text
    assert 'name="fecha" type="date" required value=""' in page and "Publicación histórica sin fecha (período guardado: 2025-Q2)" in page and 'name="periodo"' not in page
    data = {"busqueda_id": "", "posicion": "Histórica", "canal": "Bumeran", "cantidad_avisos": "1", "fecha": "", "observacion": "toque", "expected_revision": str(legacy["revision"])}
    refused = c.post(f"/cchh/publicaciones/{legacy['publicacion_id']}/editar", data=data)
    assert refused.status_code == 200 and "Ingresá la fecha de publicación" in refused.text and prepo.get(legacy["publicacion_id"]) == legacy
    ok = c.post(f"/cchh/publicaciones/{legacy['publicacion_id']}/editar", data={**data, "fecha": "2025-05-20"})
    assert ok.status_code == 303 and (prepo.get(legacy["publicacion_id"])["fecha"], prepo.get(legacy["publicacion_id"])["periodo"]) == ("2025-05-20", "2025-Q2")


def test_the_form_hides_the_period_but_indicadores_keeps_both_quarter_selectors_fully_functional():
    c = _client(follow=False)
    _create(c, fecha="2026-03-31", avisos="2")
    page = c.get("/cchh/indicadores").text
    assert 'id="hc-ind-canales-periodo"' in page and 'id="hc-ind-perfiles-periodo"' in page and ">Período</span>" in page             # ambos selectores de Quarter siguen en Indicadores
    assert 'hx-get="/cchh/indicadores/tarjetas/canales"' in page and '<option value="2026-Q1"' in page.split('id="hc-ind-canales-periodo"', 1)[1]
    assert 'name="periodo"' not in c.get("/cchh/publicaciones/nueva").text
    options, rows, selected = _fragment(c, "2026-Q1")
    assert options == ["2026-Q1"] and selected == "2026-Q1" and rows["Bumeran"] == 2
    assert c.get("/cchh/indicadores/tarjetas/perfiles", params={"periodo": "todos"}, headers=HX).status_code == 200      # la otra tarjeta trimestral (entrevistas) no se tocó


def test_the_publicaciones_listing_summary_and_filter_use_the_effective_quarter():
    c = _client(follow=False)
    _create(c, fecha="2026-03-31", avisos="2")
    _create(c, fecha="2026-04-01", avisos="3")
    assert _total(_get(periodo="2026-Q1")) == "1" and _total(_get(periodo="2026-Q2")) == "1" and _total(_get()) == "2"
    assert _options(_get(), "periodo") == [("", "Todos"), ("2026-Q2", "2026-Q2"), ("2026-Q1", "2026-Q1")]
    summary = svc.publicaciones_summary(svc.list_publicaciones_view(), svc.list_publicaciones_view(), today=date(2026, 4, 15))
    assert summary["avisos_trimestre"] == 3 and summary["periodo_actual"] == "2026-Q2" and summary["avisos_publicados"] == 5
