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
    assert tiles == {"Tiempo de cobertura prom.": f"{k['tiempo_cobertura']['valor']} días", "Tiempo de contratación prom.": f"{k['tiempo_contratacion']['valor']} días",
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
        [r["nombre_puesto"], f"{r['dias']} días"] for r in page["busquedas_estancadas"]["filas"]]
    assert [text_of(r.select_one(".activity-bar-label")) for r in section("c1-postulantes").select(".activity-bar-row")] == [r["nombre_puesto"] for r in page["postulantes_por_busqueda"]["filas"]]
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


def test_kpi_notes_are_visible_and_use_candidaturas(tmp_path):
    seed_parity(tmp_path)
    s = soup(client().get(PAGE))
    notes = {text_of(t.select_one(".activity-kpi__label")): text_of(t.select_one(".activity-kpi__note")) for t in s.select(".activity-kpi")}
    assert "Días entre apertura y cierre, promedio solo de búsquedas ya cerradas" in notes["Tiempo de cobertura prom."]
    assert "primera llegada a Finalizado" in notes["Tiempo de contratación prom."]
    assert "candidaturas cargadas" in notes["Tasa de conversión"] and "candidaturas marcadas como Ingreso" in notes["Total contratados"].replace("Candidaturas", "candidaturas")
    assert "rechazadas y desistidas" in notes["Total descartados"] and "concretadas" in notes["Entrevistas realizadas"]
    assert not any("candidatos" in n.lower() for n in notes.values())                              # terminología de candidaturas (D5)
    assert not s.select(".activity-kpi__info, .chart-tooltip")                                    # visibles, no en un tooltip


def test_c3_and_c2_use_segmented_bars_whose_segments_and_legend_come_from_the_engine(tmp_path):
    seed_parity(tmp_path)
    s = soup(client().get(PAGE))
    page = engine_page()
    rows = s.select('[data-indicador="c3-ingresos-por-mes"] .activity-segrow')
    assert len(rows) == len(page["ingresos_por_mes"]["filas"]) > 0
    for row, data in zip(rows, page["ingresos_por_mes"]["filas"]):
        segs = row.select(".activity-bar--segmented .activity-bar__seg")
        assert len(segs) == len(data["segmentos"]) == len(page["ingresos_por_mes"]["series"]) + 1
        assert [seg["style"] for seg in segs] == [f"width:{d['ancho_pct']}%" for d in data["segmentos"]]
        assert text_of(row.select_one(".activity-segrow__total")) == f"· Total: {data['total']}"
        assert all(not k.get_text(strip=True).endswith(": 0") for k in row.select(".activity-seg-key"))        # C3 omite las series en 0
    c2 = s.select('[data-indicador="c2-busquedas-por-mes"] .activity-segrow')
    assert len(c2) == len(page["busquedas_por_mes"]["filas"]) and all(len(r.select(".activity-bar__seg")) == 2 for r in c2)


def test_segmented_tones_cycle_for_a_high_dynamic_cardinality_and_the_unspecified_one_is_muted(tmp_path):
    modalidades = [f"Modalidad {i}" for i in range(8)]
    apps = [make_app(f"a{i}", f"p{i}", etapa="Finalizado", resultado="Ingreso", alta="2026-03-02T10:00:00+00:00", movimiento="2026-03-09T10:00:00+00:00") for i in range(9)]
    entrevistas = [make_entrevista(f"e{i}", f"p{i}", busqueda_id="b", application_id=f"a{i}", modalidad=modalidades[i], fecha="2026-03-05") for i in range(8)]       # el 9.º ingreso queda sin modalidad
    seed(tmp_path, busquedas=[make_busqueda("b", "X", candidatos=apps)], entrevistas=entrevistas, catalogos={"modalidad_contratacion": modalidades})
    row = soup(client().get(PAGE)).select_one('[data-indicador="c3-ingresos-por-mes"] .activity-segrow')
    tones = [seg["data-seg"] for seg in row.select(".activity-bar__seg")]
    assert tones == ["0", "1", "2", "3", "4", "5", "0", "1", "muted"]                              # 8 series cíclicas + «Sin especificar» neutro
    assert [k["data-seg"] for k in row.select(".activity-seg-key")] == tones
    assert "aria-label" in row.select_one(".activity-bar--segmented").attrs


def test_date_warnings_show_only_when_records_are_affected(tmp_path):
    clean = make_busqueda("b", "Limpia", apertura="2026-01-01", cierre="2026-02-01", estado="cerrado", candidatos=[make_app("a1", "p1", alta="2026-01-02T00:00:00+00:00", movimiento="2026-01-02T00:00:00+00:00")])
    seed(tmp_path, busquedas=[clean], entrevistas=[make_entrevista("e1", "p1", busqueda_id="b", application_id="a1", fecha="2026-01-10")])
    s = soup(client().get(PAGE))
    assert not s.select("[data-date-note]")                                                       # sin ingresos ni entrevistas estimadas: cero advertencias


def test_date_warnings_appear_with_estimated_interview_dates_and_ingresos_derived_dates(tmp_path):
    legacy = make_app("a2", "p2", etapa="Finalizado", resultado="Ingreso", alta="2026-01-02T00:00:00+00:00", movimiento="2026-01-12T00:00:00+00:00", historial=False)
    seed(tmp_path, busquedas=[make_busqueda("b", "Con ingreso", candidatos=[legacy])], entrevistas=[make_entrevista("e1", "p2", busqueda_id="b", application_id="a2", fecha=None)])
    s = soup(client().get(PAGE))
    notes = [text_of(n) for n in s.select("[data-date-note]")]
    assert any("primera vez que la candidatura llegó a Finalizado" in n for n in notes)
    assert any("1 de 1 ingresos usan la fecha del último movimiento" in n for n in notes)
    assert any("1 de 1 entrevistas usan una fecha estimada" in n for n in notes)
    assert not any("sin fecha utilizable" in n for n in notes)


def test_empty_dataset_renders_every_original_empty_state_without_errors(tmp_path):
    r = client().get(PAGE)
    assert r.status_code == 200
    s = soup(r)
    tiles = {text_of(t.select_one(".activity-kpi__label")): text_of(t.select_one(".activity-kpi__value")) for t in s.select(".activity-kpi")}
    assert tiles["Tiempo de cobertura prom."] == "—" and tiles["Tiempo de contratación prom."] == "—" and tiles["Tasa de conversión"] == "0%" and tiles["Total contratados"] == "0"
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
        assert form["hx-get"] == f"{PAGE}/tarjetas/{card}" and form["hx-target"] == f"#hc-ind-{card}" and form["hx-swap"] == "outerHTML" and form["hx-trigger"] == "change"
        assert form.find("option", selected=True)["value"] == "todos" and text_of(form.find("option", selected=True)) == "Histórico completo"
        assert text_of(form.find("label")) == "Período" and form.find("label")["for"] == form.find("select")["id"]


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
        assert not re.search(r"\bcount\b\s*[<>=]|\bset\s+\w+\s*=\s*\w+\s*[-/*]\s*\w+", code), path                     # sin aritmética de negocio (el `%6` del tono visual es la única cuenta, en el macro)
    assert "% 6" in jinja_code("partials/human_capital_indicadores_macros.html")


def test_the_view_reuses_hub_primitives_and_adds_only_generic_extensions_defined_once():
    tools = (ROOT / "webapp/static/tools.css").read_text(encoding="utf-8")
    hc = (ROOT / "webapp/static/human_capital.css").read_text(encoding="utf-8")
    for selector in (".activity-bar--segmented { display: flex", ".activity-bar__seg { display: block", ".activity-seg-key::before { content", ".activity-segrow {", ".activity-kpi__note {", ".matrix-scroll--contained {", ".activity-bar-list--fixed .activity-bar-label {"):
        assert len(re.findall("^" + re.escape(selector), tools, re.M)) == 1, selector                 # definida UNA sola vez (la variante oscura va con otro prefijo)
    assert not re.search(r"segmented|activity-segrow|activity-kpi__note|matrix-scroll--contained|indicad", hc, re.I)    # nada específico de Indicadores en el CSS de CCHH
    assert ".activity-kpi-grid.activity-kpi-grid--notes { grid-template-columns: 1fr; }" in tools.replace("\r\n", "\n")
    assert tools.count("--notes") >= 3 and "min-width: 640px" in tools and "min-width: 1000px" in tools                        # 1/2/3 columnas por viewport
    panel = (ROOT / "webapp/templates/partials/human_capital_indicadores_panel.html").read_text(encoding="utf-8")
    macros = (ROOT / "webapp/templates/partials/human_capital_indicadores_macros.html").read_text(encoding="utf-8")
    for primitive in ("activity-kpi-grid", "dashboard-section", "activity-grid-2", "dashboard-table", "matrix-scroll"):
        assert primitive in panel, primitive
    for primitive in ("activity-kpi", "activity-bar-row", "activity-bar", "dashboard-empty", "field-hint", "hc-profile-filters", "feedback-field"):
        assert primitive in macros, primitive
    assert not re.search(r"<style|style=\"[^\"]*(color|background)", panel + macros)                                         # sin estilos de color en línea: el color vive en las clases del Hub
    assert "<script" not in panel + macros + (ROOT / "webapp/templates/human_capital/indicadores.html").read_text(encoding="utf-8")   # sin JavaScript nuevo: HTMX resuelve las interacciones
