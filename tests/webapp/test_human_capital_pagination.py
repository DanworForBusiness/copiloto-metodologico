"""test_human_capital_pagination.py — ESTÁNDAR DE PAGINACIÓN de tablas de Capital Humano (Candidatos, Entrevistas, Onboarding).

Reusa el paginador canónico del Hub (`.activity-pager`, «Uso de métricas») vía el macro compartido `partials/pager.html` + el helper genérico `webapp.services.pagination`.
Contrato: default 50; tamaños 50/100/200/300; filtros/búsqueda/orden ANTES de paginar; el contador es el TOTAL filtrado; cualquier cambio de filtro/búsqueda/tamaño/Limpiar vuelve a
la página 1; el estado de la consulta (filtros + page_size) se conserva en cada navegación. Datasets SINTÉTICOS (las listas de servicio se parchean): el harness no se infla.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import auth_service, human_capital_service, pagination

_AUTHORIZED_EMAIL = "hc-pagination-test@danwor.com"
SIZES = (50, 100, 200, 300)


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)


def _client() -> TestClient:
    session = auth_service.create_session({"email": _AUTHORIZED_EMAIL, "name": "P T", "first_name": "P", "family_name": "T"})
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


# ── helper genérico ──────────────────────────────────────────────────────────

def test_page_window_is_the_canonical_function_shared_with_uso_de_metricas():
    from webapp.services import activity_analytics
    assert activity_analytics._page_window is pagination.page_window
    assert pagination.page_window(4, 10) == [1, 2, 3, 4, 5, 6, None, 10] and pagination.page_window(8, 20) == [1, None, 6, 7, 8, 9, 10, None, 20] and pagination.page_window(1, 1) == [1]


@pytest.mark.parametrize("total,pages", [(0, 1), (1, 1), (49, 1), (50, 1), (51, 2), (100, 2), (101, 3), (300, 6)])
def test_paginate_default_size_is_50_and_exact_boundaries(total, pages):
    pg = pagination.paginate(list(range(total)), base_url="/x")
    assert (pg["page_size"], pg["total"], pg["total_pages"], pg["page"]) == (50, total, pages, 1) and len(pg["items"]) == min(50, total)
    assert pg["show"] is (total > 50)                                                       # 50 exactos NO crean segunda página ni muestran el paginador


def test_paginate_allowed_sizes_are_exactly_50_100_200_300_and_anything_else_falls_back_to_50():
    assert pagination.CCHH_PAGE_SIZES == SIZES and pagination.CCHH_DEFAULT_PAGE_SIZE == 50
    for ok in SIZES:
        assert pagination.paginate(list(range(1000)), page_size=ok)["page_size"] == ok
    for bad in (10, 25, 500, "all", "All", "", None, "abc", -5, 0, "50.5"):
        assert pagination.paginate(list(range(1000)), page_size=bad)["page_size"] == 50, bad


def test_paginate_clamps_the_page_and_201_rows_with_size_200_are_two_pages():
    pg = pagination.paginate(list(range(201)), page=2, page_size=200)
    assert (pg["total_pages"], len(pg["items"]), pg["items"]) == (2, 1, [200])
    assert pagination.paginate(list(range(10)), page=99)["page"] == 1                        # nunca una página vacía por un índice viejo
    assert pagination.paginate(list(range(120)), page=99)["page"] == 3 and pagination.paginate(list(range(120)), page="x")["page"] == 1
    assert pagination.paginate(list(range(120)), page=-3)["page"] == 1


def test_paginate_urls_keep_active_criteria_and_page_size_but_drop_empty_ones():
    pg = pagination.paginate(list(range(500)), page=3, page_size=100, base_url="/cchh/onboarding", params={"estado": "pendientes", "area": "", "q": "gimena lópez"})
    q = {k: v[0] for k, v in parse_qs(urlparse(pg["urls"][4]).query).items()}
    assert q == {"estado": "pendientes", "q": "gimena lópez", "page": "4", "page_size": "100"}
    assert "page" not in parse_qs(urlparse(pg["urls"]["size"]).query) and parse_qs(urlparse(pg["urls"]["size"]).query)["estado"] == ["pendientes"]     # el select agrega page_size => página 1
    assert parse_qs(urlparse(pg["urls"]["reset"]).query) == {"page_size": ["100"]}                                                                    # ✕ Limpiar: sin filtros, conserva el tamaño
    assert (pg["urls"]["first"].endswith("page=1&page_size=100") or "page=1" in pg["urls"]["first"]) and "page=5" in pg["urls"]["last"] and "page=2" in pg["urls"]["prev"] and "page=4" in pg["urls"]["next"]


# ── datasets sintéticos por superficie ───────────────────────────────────────

def _profile(i: int, **over) -> dict:
    return {"profile_id": f"p{i:04d}", "first_name": f"Nombre{i:04d}", "last_name": "Apellido", "profile_skills": f"skill{i}", "seniority": "Senior" if i % 2 else "Junior",
            "location": "CABA", "source": "LinkedIn", "potential_mark": None, "potential_rating": None, "email": f"p{i}@x.com", **over}


def _entrevista(i: int) -> dict:
    return {"entrevista_id": f"e{i:04d}", "profile": _profile(i), "concretada": True, "fecha_entrevista": "2026-09-01", "origen": "LinkedIn", "puesto_mostrado": "Analista",
            "area": "Consultoria", "modalidad_contratacion": "Full time", "remuneracion_pretendida": "1", "created_at": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00"}


def _onboarding(i: int) -> dict:
    return {"application_id": f"a{i:04d}", "profile": _profile(i), "puesto_mostrado": "Analista", "area": "Consultoria" if i % 3 else "RRHH", "fecha_ingreso": f"2026-{1 + (i // 28) % 12:02d}-{1 + i % 28:02d}",
            "completado": i % 5 == 0, "progress": {"hechos": 3, "total": 17, "porcentaje": 18, "completo": False}, "oferta_cerrada": None, "created_at": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00",
            "checklist": {}, "revision": 1, "busqueda_id": "b", "profile_id": f"p{i:04d}"}


class Surface:
    def __init__(self, name, path, panel_id, name_re, count_word, seed, filters=None):
        self.name, self.path, self.panel_id, self.name_re, self.count_word, self._seed, self.filters = name, path, panel_id, name_re, count_word, seed, filters or {}

    def seed(self, monkeypatch, n):
        self._seed(monkeypatch, n)

    def get(self, **params):
        return _client().get(self.path, params=params, headers={"HX-Request": "true"}).text

    def names(self, html):
        return re.findall(self.name_re, html)

    def total_text(self, html):
        return re.search(r'dashboard-section__count">(\d+) ' + self.count_word, html).group(1)


def _seed_candidatos(monkeypatch, n):
    rows = [_profile(i) for i in range(n)]
    monkeypatch.setattr(human_capital_service, "list_profiles", lambda: list(rows))


def _seed_entrevistas(monkeypatch, n):
    rows = [_entrevista(i) for i in range(n)]
    monkeypatch.setattr(human_capital_service, "list_entrevistas_view", lambda: list(rows))


def _seed_onboarding(monkeypatch, n):
    rows = [_onboarding(i) for i in range(n)]
    monkeypatch.setattr(human_capital_service, "list_onboardings_view", lambda: list(rows))


SURFACES = [
    Surface("candidatos", "/cchh/candidatos", "hc-profiles-panel", r'hc-candidate-name dw-idcell__name">([^<]*)<', "candidato", _seed_candidatos, {"q": "Nombre00"}),
    Surface("entrevistas", "/cchh/entrevistas", "hc-entrevistas-panel", r'dw-idcell__name">([^<]*)<', "entrevista", _seed_entrevistas),
    Surface("onboarding", "/cchh/onboarding", "hc-onboarding-panel", r'dw-idcell__name">([^<]*)<', "onboarding", _seed_onboarding, {"estado": "pendientes", "area": "Consultoria", "q": "nombre"}),
]
IDS = [s.name for s in SURFACES]


@pytest.fixture(params=SURFACES, ids=IDS)
def surface(request, monkeypatch):
    return request.param


@pytest.mark.parametrize("n,pages,first_page_rows", [(0, 1, 0), (10, 1, 10), (49, 1, 49), (50, 1, 50), (51, 2, 50)])
def test_boundaries_49_50_51_rows_default_size(surface, monkeypatch, n, pages, first_page_rows):
    surface.seed(monkeypatch, n)
    html = surface.get()
    assert len(surface.names(html)) == first_page_rows
    if n <= 50:
        assert "data-pager" not in html                                                        # una sola página: sin paginador (49 y 50 no rompen el layout)
        assert (surface.total_text(html) == str(n)) if n else True
    else:
        assert 'data-pager-pages="2"' in html and 'data-pager-page="1"' in html and surface.total_text(html) == "51"
        assert len(surface.names(surface.get(page="2"))) == 1


def test_201_rows_with_size_200_are_two_pages_and_over_300_navigates_with_max_size(surface, monkeypatch):
    surface.seed(monkeypatch, 201)
    assert len(surface.names(surface.get(page_size="200"))) == 200 and len(surface.names(surface.get(page_size="200", page="2"))) == 1
    surface.seed(monkeypatch, 650)
    sizes = [len(surface.names(surface.get(page_size="300", page=p))) for p in (1, 2, 3)]
    assert sizes == [300, 300, 50] and surface.total_text(surface.get(page_size="300", page="3")) == "650"
    assert len(surface.names(surface.get(page_size="300", page="99"))) == 50                  # fuera de rango = última página, nunca vacía


def test_default_size_is_50_and_the_selector_offers_exactly_50_100_200_300(surface, monkeypatch):
    surface.seed(monkeypatch, 400)
    html = surface.get()
    select = html.split('class="activity-pager__size"', 1)[1].split("</select>", 1)[0]
    assert [int(x) for x in re.findall(r'<option value="(\d+)"', select)] == list(SIZES) and 'value="50" selected' in select
    for bad in ("10", "25", "500", "all", "All"):
        assert 'value="50" selected' in surface.get(page_size=bad).split('class="activity-pager__size"', 1)[1]


def test_first_and_previous_are_disabled_on_page_1_and_next_and_last_on_the_last_page(surface, monkeypatch):
    surface.seed(monkeypatch, 120)
    def nav(html):
        return {t: ("disabled" in tag) for t, tag in re.findall(r'<button type="button" class="activity-pager__btn" title="([^"]+)"([^>]*)>', html)}
    first, last = nav(surface.get()), nav(surface.get(page="3"))
    assert first == {"Primera página": True, "Anterior": True, "Siguiente": False, "Última página": False}
    assert last == {"Primera página": False, "Anterior": False, "Siguiente": True, "Última página": True}
    mid = surface.get(page="2")
    assert 'class="activity-pager__page is-active" aria-current="page">2<' in mid and not any(nav(mid).values())


def test_uses_the_canonical_hub_paginator_not_a_cchh_specific_one(surface, monkeypatch):
    surface.seed(monkeypatch, 80)
    html = surface.get()
    assert 'class="activity-pager"' in html and "activity-pager__btn" in html and "activity-pager__page" in html and "activity-pager__size" in html
    assert "hc-pagination" not in html and "cchh-paginator" not in html
    root = Path(__import__("webapp").__file__).parent
    for css in (root / "static" / "human_capital.css",):
        text = css.read_text(encoding="utf-8")
        assert "pagination" not in text.lower() and "pager" not in text.lower()                  # cero CSS de paginación propio de CCHH
    tools = (root / "static" / "tools.css").read_text(encoding="utf-8")
    assert ".activity-pager__size { margin-left: auto;" in tools                                 # selector a la derecha: lo resuelve el primitivo


def test_the_size_selector_is_rendered_after_the_navigation_at_the_right_end(surface, monkeypatch):
    surface.seed(monkeypatch, 80)
    nav = surface.get().split('<nav class="activity-pager"', 1)[1].split("</nav>", 1)[0]
    assert nav.rindex("activity-pager__btn") < nav.index("activity-pager__size")


def test_every_pager_link_keeps_the_query_state_and_the_size_select_resets_to_page_1(surface, monkeypatch):
    surface.seed(monkeypatch, 260)
    params = {**surface.filters, "page_size": "100", "page": "2"}
    html = surface.get(**params)
    nav = html.split('<nav class="activity-pager"', 1)[1].split("</nav>", 1)[0]
    import html as _h
    links = [_h.unescape(u) for u in re.findall(r'hx-get="([^"]+)"', nav)]
    assert links
    for url in links:
        q = parse_qs(urlparse(url).query)
        assert urlparse(url).path == surface.path
        for k, v in surface.filters.items():
            assert q[k] == [v], (url, k)                                                       # filtros + búsqueda conservados
        if "page" in q:
            assert q["page_size"] == ["100"]                                                   # tamaño conservado al navegar
    size_url = [u for u in links if "page" not in parse_qs(urlparse(u).query)]
    assert len(size_url) == 1                                                                  # el selector NO lleva página => cambiar el tamaño vuelve a la 1


def test_filter_forms_carry_page_size_but_never_page_so_any_filter_change_returns_to_page_1(monkeypatch):
    for s in (SURFACES[0], SURFACES[2]):
        s.seed(monkeypatch, 260)
        html = s.get(page_size="200", page="2")
        form = html.split('class="hc-profile-filters filter-toolbar-card"', 1)[1].split("</form>", 1)[0]
        assert 'name="page_size" value="200"' in form and 'name="page"' not in form
        reset = [x for x in re.findall(r'class="whatsnew-filter__clear" hx-get="([^"]+)"', form)]
        assert reset == [f"{s.path}?page_size=200"]                                           # ✕ Limpiar: sin filtros, página 1, tamaño conservado


def test_a_stale_page_after_narrowing_the_filter_shows_page_1_not_an_empty_table(monkeypatch):
    s = SURFACES[2]
    s.seed(monkeypatch, 400)
    html = s.get(estado="todos", q="Nombre0003", page="6")                                      # el usuario estaba en la página 6; el filtro deja 1 fila
    assert s.names(html) and s.total_text(html) == "1" and "data-pager" not in html
    c = SURFACES[0]
    c.seed(monkeypatch, 400)
    html = c.get(q="Nombre0003", page="6")
    assert c.names(html) == ["Nombre0003 Apellido"] and c.total_text(html) == "1"


def test_filters_apply_before_pagination_and_the_counter_is_the_filtered_total_onboarding(monkeypatch):
    s = SURFACES[2]
    s.seed(monkeypatch, 120)                                                                    # i % 3 != 0 => Consultoria (80), i % 3 == 0 => RRHH (40)
    p1 = s.get(area="Consultoria")
    assert s.total_text(p1) == "80" and len(s.names(p1)) == 50
    p2 = s.get(area="Consultoria", page="2")
    assert s.total_text(p2) == "80" and len(s.names(p2)) == 30                                  # 80 filtrados = 50 + 30 (no se pagina el dataset de 120 y recién después se filtra)
    assert set(s.names(p1)).isdisjoint(s.names(p2))
    rrhh = s.get(area="RRHH")
    assert s.total_text(rrhh) == "40" and len(s.names(rrhh)) == 40 and "data-pager" not in rrhh


def test_the_issue_example_68_filtered_of_120_gives_50_and_18_rows(monkeypatch):
    s = SURFACES[2]
    rows = [{**_onboarding(i), "area": "Consultoria" if i < 68 else "RRHH"} for i in range(120)]
    monkeypatch.setattr(human_capital_service, "list_onboardings_view", lambda: list(rows))
    p1, p2 = s.get(area="Consultoria"), s.get(area="Consultoria", page="2")
    assert (s.total_text(p1), len(s.names(p1)), s.total_text(p2), len(s.names(p2))) == ("68", 50, "68", 18)


def test_search_and_status_filters_are_applied_before_the_slice_for_onboarding(monkeypatch):
    s = SURFACES[2]
    s.seed(monkeypatch, 300)
    pend = s.get(estado="pendientes")                                                           # completado = i % 5 == 0 => 240 pendientes
    assert s.total_text(pend) == "240" and len(s.names(pend)) == 50
    both = s.get(estado="pendientes", area="Consultoria", q="nombre", page="2")
    assert s.total_text(both) == str(sum(1 for i in range(300) if i % 5 and i % 3)) and 'data-pager-page="2"' in both


def test_sorting_happens_before_the_slice(monkeypatch):
    """Onboarding ordena por fecha de ingreso desc y Entrevistas por created_at desc: la página 2 continúa el orden GLOBAL, no ordena sólo la página visible."""
    s = SURFACES[2]
    rows = [_onboarding(i) for i in range(120)]
    monkeypatch.setattr(human_capital_service, "list_onboardings_view", lambda: list(rows))
    expected = [f"Nombre{o['profile']['first_name'][6:]} Apellido" for o in sorted(rows, key=lambda o: (o["fecha_ingreso"], o["created_at"]), reverse=True)]
    got = s.names(s.get(page_size="50")) + s.names(s.get(page_size="50", page="2")) + s.names(s.get(page_size="50", page="3"))
    assert got == expected
    e = SURFACES[1]
    _seed_entrevistas(monkeypatch, 120)
    ids_sorted = [f"Nombre{i:04d} Apellido" for i in range(119, -1, -1)]                       # created_at crece con i => desc = i descendente
    assert e.names(e.get()) + e.names(e.get(page="2")) + e.names(e.get(page="3")) == ids_sorted


def test_entrevistas_counter_is_the_total_not_the_page_size_and_the_fragment_is_the_panel_only(monkeypatch):
    s = SURFACES[1]
    s.seed(monkeypatch, 130)
    frag = s.get(page="3")
    assert "<html" not in frag and f'id="{s.panel_id}"' in frag and s.total_text(frag) == "130" and len(s.names(frag)) == 30
    full = _client().get("/cchh/entrevistas").text
    assert "<html" in full and full.count('id="hc-entrevistas-panel"') == 1 and "Nueva entrevista" in full


def test_table_ergonomics_survive_pagination_columns_namespace_and_adapter_travel_in_the_fragment(surface, monkeypatch):
    surface.seed(monkeypatch, 130)
    frag = surface.get(page="2")
    key = {"candidatos": "cchh-candidatos", "entrevistas": "cchh-entrevistas", "onboarding": "cchh-onboarding"}[surface.name]
    assert f'data-draggable-columns-key="{key}"' in frag and f'data-resizable-columns-key="{key}"' in frag     # el estado de columnas es por superficie, independiente de la página
    assert "applyStoredColumnOrder" in frag and "initDraggableColumns" in frag                                 # el adapter reaplica orden/anchos en cada swap


def test_candidatos_delete_keeps_the_current_page_and_filters(monkeypatch):
    s = SURFACES[0]
    s.seed(monkeypatch, 130)
    html = s.get(page="2", q="Nombre")
    assert "hx-vals='{\"page\": 2}'" in html and 'hx-include=".hc-profile-filters"' in html


def test_the_shared_macro_is_the_only_cchh_pager_markup():
    root = Path(__import__("webapp").__file__).parent / "templates"
    macro = (root / "partials" / "pager.html").read_text(encoding="utf-8")
    assert macro.count("activity-pager__btn") == 4 and "activity-pager__size" in macro
    for name in ("human_capital_profiles_panel.html", "human_capital_entrevistas_panel.html", "human_capital_onboarding_panel.html"):
        text = (root / "partials" / name).read_text(encoding="utf-8")
        assert 'from "partials/pager.html" import render_pager' in text and "render_pager(pager," in text and 'class="activity-pager' not in text
