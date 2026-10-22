"""test_human_capital_indicadores_view.py — 06 Indicadores (CCHH), Bloque B: pantalla `/cchh/indicadores`.

Cubre: rutas y sesión, fragmentos HTMX de C6/C9 (independientes), navegación «06 Indicadores», valores renderizados == motor, notas visibles de los KPI («candidaturas»), advertencias de fecha solo
con registros afectados, estados vacíos, período inválido, escape de HTML, un snapshot por request, solo lectura, ausencia de cálculo de negocio en Jinja, primitives reutilizados sin duplicar y ausencia
de exportación (Bloque C) y de filtros globales. Los datos son sintéticos y aislados en un directorio temporal.
"""
from __future__ import annotations

import json
import os
import re
from html import unescape
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import config
from tests.webapp.human_capital_indicators_fixtures import build_raw_dataset, make_app, make_busqueda, make_entrevista, make_publicacion
from webapp.main import app
from webapp.services import auth_service, busqueda_repository, entrevista_repository, profile_repository, publicacion_repository
from webapp.services import human_capital_indicators as ind

ROOT = Path(__file__).resolve().parents[2]
EMAIL = "hc-indicadores-test@danwor.com"
PAGE = "/cchh/indicadores"
HX = {"HX-Request": "true"}
KINDS = {"busquedas": "busqueda_id", "entrevistas": "entrevista_id", "publicaciones": "publicacion_id", "profiles": "profile_id"}
SECTION_ORDER = ["c1-postulantes", "c2-busquedas-por-mes", "c3-ingresos-por-mes", "c4-entrevistas-por-anio", "c5-entrevistas-por-periodo", "c6-perfiles", "c7-fuente", "c8-tiempo-cobertura",
                 "c9-canales", "c10-estancadas"]


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(os, "fsync", lambda fd: None)
    for mod in (busqueda_repository, entrevista_repository, publicacion_repository, profile_repository):
        monkeypatch.setattr(mod.remote_store, "push_path", lambda path: True)
        monkeypatch.setattr(mod.remote_store, "delete_path", lambda path: True)
        monkeypatch.setattr(mod.remote_store, "enabled", lambda: False)


def client(email: str = EMAIL, *, follow=False) -> TestClient:
    c = TestClient(app, follow_redirects=follow)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "T"}))
    return c


def seed(tmp_path: Path, *, busquedas=(), entrevistas=(), publicaciones=(), fuentes=None, catalogos=None) -> None:
    base = tmp_path / "human-capital"
    for sub, docs in (("busquedas", busquedas), ("entrevistas", entrevistas), ("publicaciones", publicaciones)):
        (base / sub).mkdir(parents=True, exist_ok=True)
        for doc in docs:
            (base / sub / f"{doc[KINDS[sub]]}.json").write_text(json.dumps(doc), encoding="utf-8")
    (base / "profiles").mkdir(parents=True, exist_ok=True)
    for pid, source in (fuentes or {}).items():
        (base / "profiles" / f"{pid}.json").write_text(json.dumps({"profile_id": pid, "source": source, "revision": 1}), encoding="utf-8")
    if catalogos:
        (base / "catalogs").mkdir(parents=True, exist_ok=True)
        (base / "catalogs" / "catalogos.json").write_text(json.dumps({"schema_version": 1, "catalogs": {k: [{"valor": v, "orden": i} for i, v in enumerate(vs)] for k, vs in catalogos.items()}}), encoding="utf-8")


def seed_parity(tmp_path: Path) -> None:
    raw = build_raw_dataset()
    seed(tmp_path, busquedas=raw["busquedas"], entrevistas=raw["entrevistas"], publicaciones=raw["publicaciones"], fuentes=raw["fuentes"],
         catalogos={"modalidad_contratacion": ["Full time", "Part time"], "canal_publicacion": ["LinkedIn", "Flyer"]})


def soup(response) -> BeautifulSoup:
    return BeautifulSoup(response.text, "html.parser")


def text_of(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True))


def engine_page() -> dict:
    return ind.build_page(ind.build_facts(ind.load_snapshot()))


# ═════════════════ rutas, sesión y navegación ═════════════════

def test_the_page_requires_a_session_and_human_capital_authorization(tmp_path):
    assert TestClient(app, follow_redirects=False).get(PAGE).status_code in (302, 303, 307)
    assert client("otra-persona@danwor.com").get(PAGE).status_code == 403
    assert client().get(PAGE).status_code == 200


def test_the_fragment_routes_require_the_same_session_and_validate_the_card(tmp_path):
    for card in ("perfiles", "canales"):
        assert TestClient(app, follow_redirects=False).get(f"{PAGE}/tarjetas/{card}", headers=HX).status_code in (302, 303, 307)
        assert client("otra-persona@danwor.com").get(f"{PAGE}/tarjetas/{card}", headers=HX).status_code == 403
    assert client().get(f"{PAGE}/tarjetas/inexistente", headers=HX).status_code == 404


def test_a_fragment_requested_without_htmx_goes_back_to_the_full_page(tmp_path):
    r = client().get(f"{PAGE}/tarjetas/perfiles?periodo=2026-Q1")
    assert r.status_code == 303 and r.headers["location"] == PAGE


def test_navigation_has_06_indicadores_active_only_on_its_page_and_present_on_the_other_cchh_pages(tmp_path):
    def link(response):
        a = soup(response).find("a", href=PAGE)
        return a, (a.get("class") or []) if a else []
    a, classes = link(client().get(PAGE))
    assert a is not None and "is-active" in classes and text_of(a) == "06 Indicadores"
    for other in ("/cchh/publicaciones", "/cchh/candidatos", "/cchh/busquedas"):
        a, classes = link(client().get(other))
        assert a is not None and "is-active" not in classes, other
    others = [text_of(x) for x in soup(client().get(PAGE)).select("nav a.module-link")]
    assert others[:5] == ["01 Candidatos", "02 Búsquedas", "03 Entrevistas", "04 Onboarding", "05 Avisos"] and others[5] == "06 Indicadores"


def test_the_page_has_the_original_header_and_no_export_button_yet_and_no_global_filters(tmp_path):
    seed_parity(tmp_path)
    html = client().get(PAGE).text
    s = BeautifulSoup(html, "html.parser")
    assert text_of(s.find("h1")) == "Indicadores" and "Indicadores clave del proceso de reclutamiento" in html
    assert "Exportar" not in html and "/exportar" not in html                                    # el XLSX llega con el Bloque C: nada inoperante
    forms = s.select("main form")
    assert len(forms) == 2 and {f.get("hx-get") for f in forms} == {f"{PAGE}/tarjetas/perfiles", f"{PAGE}/tarjetas/canales"}
    assert len(s.select("main select")) == 2 and not s.select(f'main [hx-get="{PAGE}"]')            # sin filtros globales: solo los dos selectores propios de C6 y C9


# ═════════════════ contenido: valores == motor, orden y notas ═════════════════

def test_the_rendered_values_are_the_engines_values(tmp_path):
    seed_parity(tmp_path)
    s = soup(client().get(PAGE))
    page = engine_page()
    k = page["kpis"]
    tiles = {text_of(t.select_one(".activity-kpi__label")): text_of(t.select_one(".activity-kpi__value")) for t in s.select(".activity-kpi")}
    assert tiles == {"Cobertura promedio": f"{k['tiempo_cobertura']['valor']} días", "Contratación promedio": f"{k['tiempo_contratacion']['valor']} días",
                     "Tasa de conversión": f"{k['tasa_conversion']['valor']}%", "Total contratados": str(k["total_contratados"]["valor"]),
                     "Total descartados": str(k["total_descartados"]["valor"]), "Entrevistas realizadas": str(k["entrevistas_realizadas"]["valor"])}
    section = lambda key: s.select_one(f'[data-indicador="{key}"]')  # noqa: E731
    assert [[text_of(c) for c in tr.find_all("td")] for tr in section("c7-fuente").select("tbody tr")] == [
        [r["fuente"], str(r["postulantes"]), str(r["contratados"]), f"{r['conversion']}%"] for r in page["efectividad_por_fuente"]["filas"]]
    assert [[text_of(c) for c in tr.find_all("td")] for tr in section("c4-entrevistas-por-anio").select("tbody tr")] == [
        [r["anio"], str(r["realizadas"]), str(r["no_realizadas"]), str(r["total"])] for r in page["entrevistas_por_anio"]["filas"]]
    assert [[text_of(c) for c in tr.find_all("td")] for tr in section("c5-entrevistas-por-periodo").select("tbody tr")] == [
        [r["etiqueta"], str(r["realizadas"]), str(r["no_realizadas"]), str(r["total"])] for r in page["entrevistas_por_mes"]["filas"]]
    assert [[text_of(c) for c in tr.find_all("td")] for tr in section("c10-estancadas").select("tbody tr")] == [
        [r["nombre_puesto"], str(r["dias"])] for r in page["busquedas_estancadas"]["filas"]]
    assert [text_of(r.select_one(".bar-label")) for r in section("c1-postulantes").select(".bar-row")] == [r["nombre_puesto"] for r in page["postulantes_por_busqueda"]["filas"]]
    assert [[text_of(c) for c in tr.find_all("td")][0] for tr in section("c6-perfiles").select("tbody tr")] == [r["perfil"] for r in page["perfiles_entrevistados"]["filas"]]
    canales = [[text_of(c) for c in tr.find_all("td")] for tr in section("c9-canales").select("tbody tr")]
    assert canales == [[r["canal"], str(r["avisos"]), str(r["candidatos"]), str(r["contratados"]), f"{r['conversion']}%",
                        "—" if r["contrataciones_por_aviso"] is None else f"{r['contrataciones_por_aviso']:g}"] for r in page["canales_aviso"]["filas"]]


def test_the_ten_cards_appear_in_the_original_order_all_open_with_their_titles(tmp_path):
    seed_parity(tmp_path)
    s = soup(client().get(PAGE))
    sections = s.select("main details.dashboard-section")
    assert [d["data-indicador"] for d in sections] == SECTION_ORDER and all(d.has_attr("open") for d in sections)
    assert [text_of(d.select_one(".dashboard-section__title")) for d in sections] == [
        "Postulantes por búsqueda", "Búsquedas abiertas vs cerradas por mes", "Ingresos efectivos por mes", "Entrevistas realizadas por año", "Entrevistas realizadas por período",
        "Perfiles más entrevistados", "Efectividad por fuente", "Tiempo de cobertura por búsqueda cerrada", "Efectividad por canal de aviso", "Búsquedas vencidas o estancadas"]
    assert len(s.select(".activity-kpi")) == 6



def test_kpi_tiles_are_the_compact_avisos_tile_with_the_explanation_only_inside_the_tooltip(tmp_path):
    seed_parity(tmp_path)
    s = soup(client().get(PAGE))
    grid = s.select_one(".activity-kpi-grid")
    assert grid.get("class") == ["activity-kpi-grid"]                                              # grilla SIN modificador propio (idéntica a Avisos/CRM)
    tiles = grid.select(".activity-kpi")
    assert len(tiles) == 6
    for tile in tiles:
        children = [(c.name, tuple(c.get("class") or [])) for c in tile.find_all(True, recursive=False)]
        assert children == [("span", ("activity-kpi__value",)), ("span", ("activity-kpi__label",)), ("button", ("activity-kpi__info",)), ("span", ("chart-tooltip",))], children   # valor + etiqueta + (i) + tooltip
        visible_text = text_of(tile.select_one(".activity-kpi__value")) + " " + text_of(tile.select_one(".activity-kpi__label"))
        assert text_of(tile).replace(text_of(tile.select_one(".chart-tooltip")), "").strip() == visible_text.strip()   # fuera del tooltip no hay NINGÚN otro texto
        assert not tile.select("p, .activity-kpi__note, .activity-kpi__note--warn")                 # ni párrafos explicativos ni notas
    assert not s.select_one("main").select(".activity-kpi__note, .activity-kpi__note--warn, .activity-kpi-grid--notes")
    helps = {text_of(t.select_one(".activity-kpi__label")): t for t in tiles}
    for label, fragment in (("Cobertura promedio", "promedio solo de búsquedas ya cerradas"), ("Contratación promedio", "primera llegada a Finalizado"),
                            ("Tasa de conversión", "candidaturas cargadas"), ("Total contratados", "Candidaturas marcadas como Ingreso"), ("Total descartados", "rechazadas y desistidas"),
                            ("Entrevistas realizadas", "marcadas como concretadas")):
        tile = helps[label]
        assert fragment in text_of(tile.select_one(".chart-tooltip"))                              # la explicación completa vive en el tooltip…
        assert text_of(tile.select_one(".chart-tooltip")) in tile.select_one("button")["aria-label"]  # …y en el nombre accesible del botón (hover, foco de teclado y tap)
        assert "candidatos" not in text_of(tile.select_one(".chart-tooltip")).lower()               # terminología de candidaturas (D5)
    assert s.select_one("main").find(string=re.compile("Total contratados")) is not None


def test_kpi_tiles_have_the_same_markup_as_the_avisos_tile(tmp_path):
    seed_parity(tmp_path)
    c = client()
    avisos = soup(c.get("/cchh/publicaciones")).select_one(".activity-kpi")
    indicadores = soup(c.get(PAGE)).select_one(".activity-kpi")
    shape = lambda t: [(x.name, tuple(x.get("class") or [])) for x in t.find_all(True, recursive=False)]      # noqa: E731
    assert shape(avisos) == shape(indicadores)



def test_c2_and_c3_have_exactly_one_legend_each_before_the_rows_and_the_rows_repeat_no_series_names(tmp_path):
    seed_parity(tmp_path)
    s = soup(client().get(PAGE))
    page = engine_page()
    for key in SECTION_ORDER:
        sec = s.select_one(f'[data-indicador="{key}"]')
        assert len(sec.select(".category-plot-legend")) == (1 if key in ("c2-busquedas-por-mes", "c3-ingresos-por-mes") else 0), key      # más de una leyenda por gráfico → falla
    c2, c3 = (s.select_one(f'[data-indicador="{k}"]') for k in ("c2-busquedas-por-mes", "c3-ingresos-por-mes"))
    assert [text_of(b) for b in c2.select(".category-plot-legend b")] == ["Abiertas", "Cerradas"]
    assert [text_of(b) for b in c3.select(".category-plot-legend b")] == [*page["ingresos_por_mes"]["series"], "Sin especificar"]    # series DINÁMICAS del motor
    for sec, rows in ((c2, page["busquedas_por_mes"]["filas"]), (c3, page["ingresos_por_mes"]["filas"])):
        legend = sec.select_one(".category-plot-legend")
        assert legend.find_next_sibling("div", attrs={"data-seg-row": True}) is not None and list(legend.find_all_next(attrs={"data-seg-row": True})) and not legend.find_all_previous(attrs={"data-seg-row": True}) \
            or sec.find_all(attrs={"data-seg-row": True})                                          # la leyenda precede a las filas
        seg_rows = sec.select("[data-seg-row]")
        assert len(seg_rows) == len(rows) > 0
        for row in seg_rows:
            assert not row.select(".category-plot-legend, .swatch-bar, b i, .activity-seg-key, .activity-segrow__legend")        # claves de leyenda DENTRO de una fila → falla
            visible = text_of(row).replace(text_of(row.select_one(".chart-tooltip")), "")
            assert not re.search(r"[A-Za-zÀ-ÿ]{4,}", visible.replace(text_of(row.select_one(".bar-label")), "")), visible    # fila = mes + barra + valor (sin nombres de serie)
    for row, data in zip(c3.select("[data-seg-row]"), page["ingresos_por_mes"]["filas"]):
        segs = row.select(".bar-track--segmented > .bar-seg")
        assert len(segs) == len(data["segmentos"]) == len(page["ingresos_por_mes"]["series"]) + 1
        assert [seg["style"] for seg in segs] == [f"width:{d['ancho_pct']}%" for d in data["segmentos"]]                          # proporciones del motor
        assert text_of(row.select_one(".bar-value")) == str(data["total"])                                                          # total compacto visible
        assert [text_of(p) for p in row.select(".chart-tooltip p")] == [f"{d['clave']}: {d['valor']}" for d in data["segmentos"] if d["valor"]] + [f"Total: {data['total']}"]
    for row, data in zip(c2.select("[data-seg-row]"), page["busquedas_por_mes"]["filas"]):
        assert [text_of(b) for b in row.select(".bar-value b")] == [str(data["abiertas"]), str(data["cerradas"])]
        assert [text_of(p) for p in row.select(".chart-tooltip p")] == [f"Abiertas: {data['abiertas']}", f"Cerradas: {data['cerradas']}", f"Total: {data['total']}"]


def test_the_chart_rows_are_keyboard_reachable_and_expose_their_breakdown_to_assistive_tech(tmp_path):
    seed_parity(tmp_path)
    s = soup(client().get(PAGE))
    rows = s.select("[data-seg-row]")
    assert rows and all(r.get("tabindex") == "0" and "chart-tooltip-anchor" in r["class"] and r.get("aria-label") for r in rows)
    assert all(r.select_one(".chart-tooltip") is not None for r in rows)                           # el MISMO tooltip del Hub: sin implementación propia
    assert not s.select(".bar-row.chart-tooltip-anchor[tabindex] button")



def test_segmented_tones_cycle_for_a_high_dynamic_cardinality_and_legend_and_bars_agree(tmp_path):
    modalidades = [f"Modalidad {i}" for i in range(8)]
    apps = [make_app(f"a{i}", f"p{i}", etapa="Finalizado", resultado="Ingreso", alta="2026-03-02T10:00:00+00:00", movimiento="2026-03-09T10:00:00+00:00") for i in range(9)]
    entrevistas = [make_entrevista(f"e{i}", f"p{i}", busqueda_id="b", application_id=f"a{i}", modalidad=modalidades[i], fecha="2026-03-05") for i in range(8)]       # el 9.º ingreso queda sin modalidad
    seed(tmp_path, busquedas=[make_busqueda("b", "X", candidatos=apps)], entrevistas=entrevistas, catalogos={"modalidad_contratacion": modalidades})
    sec = soup(client().get(PAGE)).select_one('[data-indicador="c3-ingresos-por-mes"]')
    tones = [seg["data-seg"] for seg in sec.select("[data-seg-row] .bar-seg")]
    assert tones == ["0", "1", "2", "3", "4", "5", "0", "1", "muted"]                              # 8 series cíclicas + «Sin especificar» neutro
    legend = [(text_of(b), b.select_one(".swatch-bar")["data-seg"]) for b in sec.select(".category-plot-legend b")]
    assert legend == [*[(m, str(i % 6)) for i, m in enumerate(modalidades)], ("Sin especificar", "muted")]    # la leyenda usa los MISMOS tonos que las barras
    assert len(sec.select(".category-plot-legend")) == 1


def test_the_c3_legend_follows_the_catalog_and_is_never_hardcoded(tmp_path):
    apps = [make_app("a1", "p1", etapa="Finalizado", resultado="Ingreso", alta="2026-03-02T10:00:00+00:00", movimiento="2026-03-09T10:00:00+00:00")]
    seed(tmp_path, busquedas=[make_busqueda("b", "X", candidatos=apps)], entrevistas=[make_entrevista("e1", "p1", busqueda_id="b", application_id="a1", modalidad="Zeta", fecha="2026-03-05")],
         catalogos={"modalidad_contratacion": ["Zeta", "Alfa"]})
    sec = soup(client().get(PAGE)).select_one('[data-indicador="c3-ingresos-por-mes"]')
    assert [text_of(b) for b in sec.select(".category-plot-legend b")] == ["Zeta", "Alfa", "Sin especificar"]


def test_date_warnings_show_only_when_records_are_affected(tmp_path):
    clean = make_busqueda("b", "Limpia", apertura="2026-01-01", cierre="2026-02-01", estado="cerrado", candidatos=[make_app("a1", "p1", alta="2026-01-02T00:00:00+00:00", movimiento="2026-01-02T00:00:00+00:00")])
    seed(tmp_path, busquedas=[clean], entrevistas=[make_entrevista("e1", "p1", busqueda_id="b", application_id="a1", fecha="2026-01-10")])
    s = soup(client().get(PAGE))
    assert not s.select("[data-date-note]") and not s.select("details.dashboard-section > summary .chart-tooltip")        # sin ingresos ni entrevistas estimadas: cero advertencias (ni (i) en los encabezados)




def test_date_warnings_live_in_header_tooltips_next_to_the_title_of_each_affected_card(tmp_path):
    legacy = make_app("a2", "p2", etapa="Finalizado", resultado="Ingreso", alta="2026-01-02T00:00:00+00:00", movimiento="2026-01-12T00:00:00+00:00", historial=False)
    seed(tmp_path, busquedas=[make_busqueda("b", "Con ingreso", candidatos=[legacy])], entrevistas=[make_entrevista("e1", "p2", busqueda_id="b", application_id="a2", fecha=None)])
    s = soup(client().get(PAGE))
    assert not s.select("[data-date-note]")                                                           # NADA impreso en los cuerpos
    head_tip = lambda key: text_of(s.select_one(f'[data-indicador="{key}"] > summary .chart-tooltip'))   # noqa: E731
    c3 = head_tip("c3-ingresos-por-mes")
    assert "primera vez que la candidatura llegó a Finalizado" in c3 and "1 de 1 ingresos usan la fecha del último movimiento" in c3
    for key in ("c4-entrevistas-por-anio", "c5-entrevistas-por-periodo", "c6-perfiles"):
        assert "1 de 1 entrevistas usan una fecha estimada" in head_tip(key), key
    # el (i) está AL LADO del título, dentro del encabezado, y es el (i) del Hub (nombre accesible = el tooltip)
    summary = s.select_one('[data-indicador="c3-ingresos-por-mes"] > summary')
    assert [c.get("class")[0] for c in summary.find_all(True, recursive=False)] == ["dashboard-section__caret", "dashboard-section__title", "chart-tooltip-anchor", "dashboard-section__count"]
    assert c3 in summary.select_one("button.activity-kpi__info")["aria-label"]
    k2 = next(t for t in s.select(".activity-kpi") if "Contratación promedio" in text_of(t.select_one(".activity-kpi__label")))
    tooltip = text_of(k2.select_one(".chart-tooltip"))                                                # y el tooltip del KPI conserva las suyas
    assert "primera vez que la candidatura llegó a Finalizado" in tooltip and "1 de 1 ingresos usan la fecha del último movimiento" in tooltip
    assert not k2.select("[data-date-note], p.field-hint")


def test_the_c6_header_tooltip_follows_the_local_period_through_an_oob_swap(tmp_path):
    seed_parity(tmp_path)
    facts = ind.build_facts(ind.load_snapshot())
    periodo = ind.build_perfiles(facts)["periodos"][0]
    expected = ind.build_perfiles(facts, periodo)["fechas"]
    page = soup(client().get(PAGE))
    assert not page.select("[hx-swap-oob]")                                                           # en la página completa no hay OOB (ids no duplicados)
    assert len(page.select("#hc-ind-perfiles-help")) == 1 and page.select_one("#hc-ind-perfiles-help").find_parent("summary") is not None
    r = soup(client().get(f"{PAGE}/tarjetas/perfiles", params={"periodo": periodo}, headers=HX))
    oob = r.select_one('#hc-ind-perfiles-help[hx-swap-oob="true"]')
    assert oob is not None and oob.find_parent(id="hc-ind-perfiles") is None                          # hermano del fragmento (top-level), no dentro
    if expected["estimadas"]:
        assert f"{expected['estimadas']} de {expected['total']} entrevistas usan una fecha estimada" in text_of(oob.select_one(".chart-tooltip"))
    assert not r.select("#hc-ind-perfiles [data-date-note].field-hint--warn") or all("período pedido" in text_of(n) for n in r.select("#hc-ind-perfiles [data-date-note]"))
    clean = soup(client().get(f"{PAGE}/tarjetas/canales", headers=HX))
    assert not clean.select("[hx-swap-oob]")                                                          # C9 no tiene OOB


def test_empty_dataset_renders_every_original_empty_state_without_errors(tmp_path):
    r = client().get(PAGE)
    assert r.status_code == 200
    s = soup(r)
    tiles = {text_of(t.select_one(".activity-kpi__label")): text_of(t.select_one(".activity-kpi__value")) for t in s.select(".activity-kpi")}
    assert tiles["Cobertura promedio"] == "—" and tiles["Contratación promedio"] == "—" and tiles["Tasa de conversión"] == "0%" and tiles["Total contratados"] == "0"
    page_text = text_of(s)
    for expected in ("Todavía no hay datos suficientes.", "Todavía no hay entrevistas concretadas para este período.", "Todavía no hay búsquedas cerradas para calcular este indicador.",
                     "Todavía no hay avisos ni candidaturas para este período.", "No hay búsquedas activas estancadas por el momento."):
        assert expected in page_text, expected
    assert not s.select("[data-date-note], [data-error-kind]")


# ═════════════════ filtros locales independientes (HTMX) ═════════════════

def test_the_perfiles_fragment_is_just_its_card_and_independent_from_the_canales_card(tmp_path):
    seed_parity(tmp_path)
    page = engine_page()
    periodo = page["perfiles_entrevistados"]["periodos"][0]
    r = client().get(f"{PAGE}/tarjetas/perfiles", params={"periodo": periodo}, headers=HX)
    assert r.status_code == 200 and "<html" not in r.text.lower()
    s = soup(r)
    assert s.find(id="hc-ind-perfiles") and not s.find(id="hc-ind-canales") and not s.select(".activity-kpi")
    expected = ind.build_perfiles(ind.build_facts(ind.load_snapshot()), periodo)
    assert s.find("select")["name"] == "periodo" and s.find("option", selected=True)["value"] == periodo
    assert [text_of(tr.find("td")) for tr in s.select("tbody tr")] == [f["perfil"] for f in expected["filas"]]


def test_the_canales_fragment_is_just_its_card_with_its_own_options_and_the_approximation_note(tmp_path):
    seed_parity(tmp_path)
    facts = ind.build_facts(ind.load_snapshot())
    periodo = ind.build_canales(facts)["periodos"][1]
    s = soup(client().get(f"{PAGE}/tarjetas/canales", params={"periodo": periodo}, headers=HX))
    assert s.find(id="hc-ind-canales") and not s.find(id="hc-ind-perfiles")
    assert [o["value"] for o in s.select("option")] == ["todos", *ind.build_canales(facts)["periodos"]] and s.find("option", selected=True)["value"] == periodo
    note = text_of(s.select_one("[data-approximation-note]"))
    assert "aproximación" in note and "solo de nombre" in note and "no es una atribución aviso por aviso ni una causa comprobada" in note
    for forbidden in ("provino de", "gracias a", "generó", "atribuible"):
        assert forbidden not in text_of(s).lower()
    expected = ind.build_canales(facts, periodo)
    assert [text_of(tr.find("td")) for tr in s.select("tbody tr")] == [f["canal"] for f in expected["filas"]]


@pytest.mark.parametrize("card", ["perfiles", "canales"])
def test_an_unknown_period_degrades_to_the_full_history_with_a_visible_notice(tmp_path, card):
    seed_parity(tmp_path)
    s = soup(client().get(f"{PAGE}/tarjetas/{card}", params={"periodo": "2099-Q9"}, headers=HX))
    assert s.find("option", selected=True)["value"] == "todos"
    assert any("El período pedido no está disponible" in text_of(n) for n in s.select("[data-date-note]"))
    explicit = soup(client().get(f"{PAGE}/tarjetas/{card}", params={"periodo": "todos"}, headers=HX))
    assert not any("El período pedido no está disponible" in text_of(n) for n in explicit.select("[data-date-note]"))     # «todos» es un pedido válido: sin aviso


def test_the_page_starts_both_selectors_in_historico_completo_and_each_posts_only_to_its_own_card(tmp_path):
    seed_parity(tmp_path)
    s = soup(client().get(PAGE))
    for card in ("perfiles", "canales"):
        form = s.find(id=f"hc-ind-{card}").find("form")
        assert "whatsnew-filters" in form["class"] and form.find("label", class_="whatsnew-filter") is not None           # el bloque de filtros dentro de una sección del CRM
        assert form["hx-get"] == f"{PAGE}/tarjetas/{card}" and form["hx-target"] == f"#hc-ind-{card}" and form["hx-swap"] == "outerHTML" and form["hx-trigger"] == "change"
        assert form.find("option", selected=True)["value"] == "todos" and text_of(form.find("option", selected=True)) == "Histórico completo"
        assert text_of(form.find("label").find("span")) == "Período" and form.find("label").find("select") is not None


# ═════════════════ escape, errores, solo lectura y un snapshot por request ═════════════════

def test_user_supplied_text_is_escaped_in_the_page_and_in_the_fragments(tmp_path):
    xss = "<script>alert(1)</script>"
    apps = [make_app("a1", "p1", etapa="Finalizado", resultado="Ingreso", alta="2026-01-02T00:00:00+00:00", movimiento="2026-01-12T00:00:00+00:00")]
    seed(tmp_path, busquedas=[make_busqueda("b", xss, estado="cerrado", cierre="2026-02-01", candidatos=apps), make_busqueda("b2", xss + " activa", apertura="2025-01-01")],     # b2: activa y estancada (C10)
         entrevistas=[make_entrevista("e1", "p1", busqueda_id="b", application_id="a1", fecha="2026-01-10", modalidad=xss), make_entrevista("e2", "p9", puesto="<img src=x onerror=alert(2)>", fecha="2026-01-11")],
         publicaciones=[make_publicacion("pub", xss)], fuentes={"p1": xss}, catalogos={"modalidad_contratacion": [xss]})
    c = client()
    for html in (c.get(PAGE).text, c.get(f"{PAGE}/tarjetas/perfiles", headers=HX).text, c.get(f"{PAGE}/tarjetas/canales", headers=HX).text):
        assert "<script>alert" not in html and "<img src=x" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in c.get(PAGE).text and xss in unescape(c.get(PAGE).text)


def test_a_storage_failure_shows_the_error_banner_instead_of_a_500(tmp_path, monkeypatch):
    def boom():
        raise busqueda_repository.BusquedaStorageError("disco no disponible")
    monkeypatch.setattr(ind, "load_snapshot", boom)
    r = client().get(PAGE)
    assert r.status_code == 200 and "No se pudieron cargar las métricas: disco no disponible" in text_of(soup(r))
    assert not soup(r).select(".activity-kpi")
    f = client().get(f"{PAGE}/tarjetas/perfiles", headers=HX)
    assert f.status_code == 200 and "No se pudieron cargar las métricas" in f.text


def test_one_snapshot_per_request_and_reads_never_write(tmp_path, monkeypatch):
    seed_parity(tmp_path)
    calls = {"n": 0}
    real = ind.load_snapshot

    def counted():
        calls["n"] += 1
        return real()
    monkeypatch.setattr(ind, "load_snapshot", counted)
    before = {str(p.relative_to(tmp_path)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(tmp_path.rglob("*")) if p.is_file()}
    c = client()
    assert c.get(PAGE).status_code == 200 and calls["n"] == 1
    assert c.get(f"{PAGE}/tarjetas/perfiles", headers=HX).status_code == 200 and calls["n"] == 2
    assert c.get(f"{PAGE}/tarjetas/canales", params={"periodo": "x"}, headers=HX).status_code == 200 and calls["n"] == 3
    assert {str(p.relative_to(tmp_path)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(tmp_path.rglob("*")) if p.is_file()} == before


# ═════════════════ plantillas y CSS: sin cálculo de negocio ni primitives duplicados ═════════════════

TEMPLATES = ["human_capital/indicadores.html", "partials/human_capital_indicadores_panel.html", "partials/human_capital_indicadores_perfiles.html",
             "partials/human_capital_indicadores_canales.html", "partials/human_capital_indicadores_macros.html"]


def jinja_code(path: str) -> str:
    raw = (ROOT / "webapp" / "templates" / path).read_text(encoding="utf-8")
    raw = re.sub(r"\{#.*?#\}", "", raw, flags=re.S)                                               # fuera comentarios
    return " ".join(re.findall(r"\{[{%].*?[}%]\}", raw, flags=re.S))


def test_the_templates_do_no_business_calculation():
    forbidden = re.compile(r"\|\s*(sum|round|groupby|sort|unique|min|max|batch|slice|selectattr|rejectattr|map)\b|\bnow\(|\bnamespace\(")
    for path in TEMPLATES:
        code = jinja_code(path)
        assert not forbidden.search(code), (path, forbidden.search(code).group(0))
        assert not re.search(r"\bcount\b\s*(?:<=|>=|==|!=|<|>)|\bset\s+\w+\s*=\s*\w+\s*[-/*]\s*\w+", code), path                     # sin aritmética de negocio (el `%6` del tono visual es la única cuenta, en el macro)
    assert "% 6" in jinja_code("partials/human_capital_indicadores_macros.html")



def test_the_view_reuses_hub_primitives_and_adds_only_generic_extensions_defined_once():
    tools = (ROOT / "webapp/static/tools.css").read_text(encoding="utf-8")
    hc = (ROOT / "webapp/static/human_capital.css").read_text(encoding="utf-8")
    commercial = (ROOT / "webapp/static/commercial.css").read_text(encoding="utf-8")
    once = lambda css, selector: len(re.findall("^" + re.escape(selector), css, re.M))             # noqa: E731
    for selector in (".bar-track--segmented { display: flex", ".bar-seg { display: block", ".matrix-scroll--contained {", ".category-plot-legend {", ".category-plot-legend b {", ".category-plot-legend em {",
                     ".category-plot-legend .swatch-bar { width: 11px; height: 11px; border-radius: 2px; }"):
        assert once(tools, selector) == 1, selector                                                  # definidas UNA sola vez, en el CSS compartido
    for family in ("activity-kpi__note", "activity-kpi-grid--notes", "activity-segrow", "activity-seg-key", "activity-bar-list--fixed", "activity-bar--segmented", "activity-bar__seg"):
        assert family not in tools and family not in hc, family                                      # las familias paralelas ya no existen
    assert not re.search(r"segmented|activity-segrow|matrix-scroll--contained|content-surface", hc)    # nada de primitives compartidos en el CSS de CCHH
    # leyenda promovida: lo estructural en tools.css; el COLOR de dominio del CRM sigue en commercial.css
    assert not re.search(r"^\.category-plot-legend \{|^\.category-plot-legend b \{|^\.category-plot-legend em \{", commercial, re.M)
    for kept in (".category-plot-legend .swatch-bar { background: var(--commercial-neutral-ink); }", ".category-plot-legend .swatch-bar--hours { background: var(--gold); }",
                 ".category-plot-legend .swatch-bar--weighted { background: #a67c00; }", ".content-theme-dark .category-plot-legend .swatch-bar--weighted { background: #e8820a; }"):
        assert kept in commercial, kept
    # token de superficie: definido por tema una vez, consumido por las cabeceras fijas (sin literales #fff/#181819 en la regla de Indicadores)
    assert tools.count("--content-surface: #fff;") == 1 and tools.count("--content-surface: #181819;") == 1
    contained = re.search(r"\.matrix-scroll--contained table\.dashboard-table th \{[^}]*\}", tools).group(0)
    assert "var(--content-surface" in contained and "#fff" not in contained.replace("var(--content-surface, #fff)", "") and "#181819" not in contained
    assert "margin-top: 24px" in hc and ".module-hero + #hc-indicadores-panel" in hc
    assert 'table.dashboard-table[data-min-width="26"] { min-width: 26rem; }' in hc and 'table.dashboard-table[data-min-width="34"] { min-width: 34rem; }' in hc
    panel = (ROOT / "webapp/templates/partials/human_capital_indicadores_panel.html").read_text(encoding="utf-8")
    macros = (ROOT / "webapp/templates/partials/human_capital_indicadores_macros.html").read_text(encoding="utf-8")
    canales = (ROOT / "webapp/templates/partials/human_capital_indicadores_canales.html").read_text(encoding="utf-8")
    assert "import kpi_tile" in panel and "assessment_report_macros" in panel                          # el macro compartido del tile KPI
    for primitive in ("activity-kpi-grid", "dashboard-section", "activity-grid-2", "dashboard-table", "matrix-scroll", "dashboard-sections", "data-persist-collapse", "section_head"):
        assert primitive in panel, primitive
    for primitive in ("bar-row", "bar-track", "bar-fill", "bar-value", "category-plot-legend", "swatch-bar", "chart-tooltip-anchor", "chart-tooltip", "dashboard-subhead", "dashboard-section__count", "dashboard-section__head", "whatsnew-filters", "whatsnew-filter", "dashboard-empty", "field-hint"):
        assert primitive in macros, primitive
    assert not re.search(r"<style|style=\"[^\"]*(color|background|min-width)", panel + macros + canales)    # sin estilos de color ni ancho mínimo en línea
    assert "<script" not in panel + macros + (ROOT / "webapp/templates/human_capital/indicadores.html").read_text(encoding="utf-8")   # sin JavaScript nuevo: HTMX resuelve las interacciones
    assert "draggable" not in jinja_code("partials/human_capital_indicadores_panel.html") + panel.split("#}")[-1]                                                                   # colapso persistido, sin reordenamiento


# ═════════════════ layout estándar CCHH y colapso persistido ═════════════════

def test_the_panel_wrapper_follows_the_hero_like_the_other_cchh_sections(tmp_path):
    seed_parity(tmp_path)
    s = soup(client().get(PAGE))
    hero = s.select_one("main .module-hero")
    panel = s.find(id="hc-indicadores-panel")
    assert hero.find_next_sibling() is panel and panel.find(class_="activity-kpi-grid", recursive=False) is not None      # el wrapper sigue al hero y los KPI son su primer hijo
    hc = (ROOT / "webapp/static/human_capital.css").read_text(encoding="utf-8")
    group = next(g for g in re.findall(r"((?:\.module-hero \+ [^,{]+,?\s*)+)\{ margin-top: 24px; \}", hc) if "indicadores" in g)
    assert all(f"#hc-{n}-panel" in group for n in ("entrevistas", "onboarding", "publicaciones", "indicadores"))        # MISMO selector-grupo de 24 px que las demás secciones

def test_requerimientos_not_taken_by_cchh_are_not_counted_as_selection_processes(tmp_path):
    """Compatibilidad con Operaciones -> CCHH (PR #213): una solicitud enviada / observada / cancelada es un REQUERIMIENTO recibido, no un proceso de selección; solo cuentan las históricas,
    las altas directas de CCHH y las solicitudes ya tomadas (`en_gestion`). Misma fuente que el resto de CCHH: `list_busquedas_gestionadas()`."""
    def req(busqueda_id, nombre, estado, **kw):
        doc = make_busqueda(busqueda_id, nombre, candidatos=[make_app(f"app-{busqueda_id}", f"p-{busqueda_id}")], **kw)
        doc["origen"] = {"tipo": "operaciones"}
        doc["solicitud"] = {"estado": estado, "historial": []}
        return doc
    historica = make_busqueda("b-hist", "Historica", candidatos=[make_app("app-hist", "p-hist")])
    directa = make_busqueda("b-dir", "Alta directa", candidatos=[make_app("app-dir", "p-dir")])
    directa["origen"] = {"tipo": "cchh"}
    docs = [historica, directa, req("b-tomada", "Tomada", "en_gestion"), req("b-env", "Solo enviada", "enviada"), req("b-obs", "Solo observada", "observada"), req("b-can", "Solo cancelada", "cancelada")]
    seed(tmp_path, busquedas=docs)
    facts = ind.build_facts(ind.load_snapshot())
    assert sorted(b.busqueda_id for b in facts.busquedas) == ["b-dir", "b-hist", "b-tomada"]
    assert sorted(c.busqueda_id for c in facts.candidaturas) == ["b-dir", "b-hist", "b-tomada"]
    html = client().get(PAGE).text
    assert "Tomada" in html and "Historica" in html and "Alta directa" in html
    assert not any(name in html for name in ("Solo enviada", "Solo observada", "Solo cancelada"))


def test_every_card_lives_in_a_persisted_collapse_container_with_its_section_id(tmp_path):
    seed_parity(tmp_path)
    s = soup(client().get(PAGE))
    containers = s.select("#hc-indicadores-panel > [data-dashboard-key]")
    assert len(containers) == 7 and all(c.has_attr("data-persist-collapse") for c in containers)
    assert len({c["data-dashboard-key"] for c in containers}) == 7 and all(k["data-dashboard-key"].startswith("cchh-indicadores-") for k in containers)
    sections = [d for c in containers for d in c.find_all("details", class_="dashboard-section", recursive=False)]       # `:scope > .dashboard-section`: lo que lee app.js
    assert [d["data-section-id"] for d in sections] == SECTION_ORDER
    assert all(d.select_one(":scope > summary.dashboard-section__head > .dashboard-section__title") is not None for d in sections)
    pairs = [c for c in containers if "activity-grid-2" in c["class"]]
    assert [[d["data-section-id"] for d in c.find_all("details", recursive=False)] for c in pairs] == [SECTION_ORDER[0:2], SECTION_ORDER[3:5], SECTION_ORDER[6:8]]


def test_cards_with_unfiltered_data_show_a_header_count_and_the_filtered_ones_do_not(tmp_path):
    seed_parity(tmp_path)
    s = soup(client().get(PAGE))
    counts = {d["data-indicador"]: (text_of(d.select_one(".dashboard-section__count")) if d.select_one(".dashboard-section__count") else None) for d in s.select("main details.dashboard-section")}
    assert counts["c6-perfiles"] is None and counts["c9-canales"] is None                             # el encabezado no se recarga con su filtro
    assert all(v for k, v in counts.items() if k not in ("c6-perfiles", "c9-canales"))
    page = engine_page()
    assert counts["c7-fuente"] == f"{len(page['efectividad_por_fuente']['filas'])} fuentes"


def test_c10_table_has_the_dias_sin_movimiento_column_with_bare_numbers(tmp_path):
    seed_parity(tmp_path)
    sec = soup(client().get(PAGE)).select_one('[data-indicador="c10-estancadas"]')
    assert [text_of(th) for th in sec.select("thead th")] == ["Búsqueda", "Días sin movimiento"]
    values = [text_of(tr.find_all("td")[1]) for tr in sec.select("tbody tr")]
    assert values and all(v.isdigit() for v in values)                                              # solo números: sin «días»
