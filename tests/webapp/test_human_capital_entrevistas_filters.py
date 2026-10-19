"""test_human_capital_entrevistas_filters.py — barra de filtros de Entrevistas: Origen · Puesto · Área · Buscar · ✕ Limpiar.

Mismo primitivo que Candidatos/Onboarding (`.hc-profile-filters.filter-toolbar-card`, `--auto` / `--wide`), server-side, AND entre los cuatro criterios y ANTES de paginar; las opciones salen del
dataset COMPLETO (no desaparecen al filtrar); el contador es el total filtrado; `return_to` conserva todo el estado. La mecánica del paginador y de `return_to` ya tiene sus propias suites:
acá sólo lo que es propio de Entrevistas.
"""
from __future__ import annotations

import html as _html
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import (
    auth_service, busqueda_repository, entrevista_repository, human_capital_service, onboarding_repository, profile_repository,
)
from webapp.services import human_capital_catalog_repository as catalog_repo

_AUTHORIZED_EMAIL = "hc-ent-filters-test@danwor.com"


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(os, "fsync", lambda fd: None)
    for mod in (onboarding_repository, entrevista_repository, busqueda_repository, profile_repository):
        monkeypatch.setattr(mod.remote_store, "push_path", lambda path: True)
        monkeypatch.setattr(mod.remote_store, "delete_path", lambda path: True)
        monkeypatch.setattr(mod.remote_store, "enabled", lambda: False)


def _client(follow=True) -> TestClient:
    c = TestClient(app, follow_redirects=follow)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": _AUTHORIZED_EMAIL, "name": "F T", "first_name": "F", "family_name": "T"}))
    return c


def _seed_catalogs(**catalogs):
    snap = catalog_repo.build_snapshot({k: [{"valor": v, "orden": i + 1} for i, v in enumerate(vals)] for k, vals in catalogs.items()}, refreshed_by="test", source={"drive_id": "d", "item_id": "i"})
    p = catalog_repo.snapshot_path(); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(catalog_repo._serialize(snap))


def _row(i, *, origen="LinkedIn", puesto="Analista BI", area="Consultoria", first="Nombre", last="Apellido", fecha="2026-09-01", modalidad=None, remuneracion=None, concretada=True) -> dict:
    return {"entrevista_id": f"e{i:05d}", "profile": {"first_name": f"{first}{i:03d}" if first == "Nombre" else first, "last_name": last}, "concretada": concretada, "fecha_entrevista": fecha,
            "origen": origen, "puesto_mostrado": puesto, "area": area, "modalidad_contratacion": modalidad, "remuneracion_pretendida": remuneracion,
            "created_at": f"2026-01-01T00:{i // 60 % 60:02d}:{i % 60:02d}+00:00"}


def _patch(monkeypatch, rows):
    monkeypatch.setattr(human_capital_service, "list_entrevistas_view", lambda: list(rows))


def _get(**params):
    return _client().get("/cchh/entrevistas", params=params, headers={"HX-Request": "true"}).text


def _names(html: str) -> list[str]:
    return re.findall(r'dw-idcell__name">([^<]*)<', html)


def _total(html: str) -> str:
    return re.search(r'dashboard-section__count">(\d+) entrevista', html).group(1)


def _options(html: str, field: str) -> list[tuple[str, str]]:
    block = html.split(f'id="hc-entrevistas-{field}"', 1)[1].split("</select>", 1)[0]
    return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)<', block)


MIXED = lambda: [
    _row(1, origen="LinkedIn", puesto="Analista BI", area="Consultoria", first="Ana", last="Li", fecha="2026-08-22", modalidad="Full time", remuneracion="1.800.000"),
    _row(2, origen="Referido", puesto="Desarrollador Full-Stack", area="RRHH", first="Beto", last="Gomez", fecha="2026-09-14", modalidad="Part time"),
    _row(3, origen="Base legacy 2019", puesto="analista de datos", area="OPERACIONES", first="Carla", last="Diaz", fecha="2026-10-05", concretada=False),   # origen y área legacy
    _row(4, origen=None, puesto=None, area=None, first="Diego", last="Fernandez", fecha="2026-10-20"),                                                # sin origen / puesto / área
]


# ── barra: estructura ────────────────────────────────────────────────────────

def test_filter_bar_reuses_the_shared_toolbar_in_the_requested_order_with_natural_selects_and_a_flexible_search(monkeypatch):
    _seed_catalogs(fuente=["LinkedIn", "Referido"], area_consultora=["Consultoria", "RRHH"])
    _patch(monkeypatch, MIXED())
    html = _get()
    bar = html.split('<form class="hc-profile-filters filter-toolbar-card"', 1)[1].split("</form>", 1)[0]
    order = [bar.index(x) for x in ('id="hc-entrevistas-origen"', 'id="hc-entrevistas-puesto"', 'id="hc-entrevistas-area"', 'id="hc-entrevistas-q"', "✕ Limpiar")]
    assert order == sorted(order)                                                          # Origen · Puesto · Área · Buscar · ✕ Limpiar
    assert bar.count("feedback-field--auto") == 3 and bar.count("feedback-field--wide") == 1 and "style=" not in bar and "feedback-field--medium" not in bar
    assert bar.count('hx-include="closest form"') == 4 and 'hx-trigger="keyup changed delay:500ms"' in bar and 'name="page"' not in bar
    assert 'name="page_size" value="50"' in bar and 'class="whatsnew-filter__clear"' in bar
    css = (Path(__import__("webapp").__file__).parent / "static" / "human_capital.css").read_text(encoding="utf-8")
    assert "entrevistas-origen" not in css and "hc-entrevistas-q" not in css              # cero CSS propio de Entrevistas para la barra


# ── opciones ─────────────────────────────────────────────────────────────────

def test_origen_options_come_from_the_governed_fuente_taxonomy_plus_legacy_and_sin_origen(monkeypatch):
    _seed_catalogs(fuente=["LinkedIn", "Referido", "Hunting"], area_consultora=["Consultoria", "RRHH"])
    _patch(monkeypatch, MIXED())
    assert _options(_get(), "origen") == [("", "Todos"), ("LinkedIn", "LinkedIn"), ("Referido", "Referido"), ("Hunting", "Hunting"),
                                          ("Base legacy 2019", "Base legacy 2019 (fuera de catálogo)"), ("__sin_origen__", "Sin origen")]
    _seed_catalogs(fuente=["LinkedIn"], area_consultora=["Consultoria", "RRHH"])           # el catálogo cambia => el filtro lo refleja, sin tocar código; un valor que sale pasa a legacy
    opts = _options(_get(), "origen")
    assert ("Referido", "Referido (fuera de catálogo)") in opts and ("Hunting", "Hunting") not in opts
    _patch(monkeypatch, [_row(1), _row(2, origen="Referido")])                             # sin filas vacías => sin «Sin origen»
    assert "__sin_origen__" not in dict(_options(_get(), "origen"))


def test_puesto_options_are_the_distinct_values_present_stable_and_never_a_catalog(monkeypatch):
    _seed_catalogs(fuente=["LinkedIn"], area_consultora=["Consultoria"])
    _patch(monkeypatch, MIXED() + [_row(5, puesto="Analista BI"), _row(6, puesto="  Analista BI ")])
    opts = _options(_get(), "puesto")
    assert opts == [("", "Todos"), ("Analista BI", "Analista BI"), ("analista de datos", "analista de datos"), ("Desarrollador Full-Stack", "Desarrollador Full-Stack"), ("__sin_puesto__", "Sin puesto")]
    assert all("fuera de catálogo" not in label for _v, label in opts)                      # no es una taxonomía gobernada
    assert _options(_get(origen="Referido", area="RRHH", q="beto"), "puesto") == opts      # las opciones no desaparecen al aplicar otros filtros
    _patch(monkeypatch, [_row(1), _row(2, puesto="Otro")])
    assert "__sin_puesto__" not in dict(_options(_get(), "puesto"))                         # «Sin puesto» sólo si hay filas sin valor


def test_area_options_follow_the_area_consultora_contract_governed_legacy_and_sin_area(monkeypatch):
    _seed_catalogs(fuente=["LinkedIn"], area_consultora=["Consultoria", "RRHH", "Finanzas"])
    _patch(monkeypatch, MIXED())
    assert _options(_get(), "area") == [("", "Todas"), ("Consultoria", "Consultoria"), ("RRHH", "RRHH"), ("Finanzas", "Finanzas"),
                                        ("OPERACIONES", "OPERACIONES (fuera de catálogo)"), ("__sin_area__", "Sin área")]


# ── filtrado ─────────────────────────────────────────────────────────────────

def test_each_dropdown_matches_the_effective_value_including_legacy_and_empty_ones(monkeypatch):
    _seed_catalogs(fuente=["LinkedIn", "Referido"], area_consultora=["Consultoria", "RRHH"])
    _patch(monkeypatch, MIXED())
    by_name = lambda **p: sorted(_names(_get(**p)))
    assert by_name(origen="LinkedIn") == ["Ana Li"] and by_name(origen="Referido") == ["Beto Gomez"]
    assert by_name(origen="Base legacy 2019") == ["Carla Diaz"] and by_name(origen="__sin_origen__") == ["Diego Fernandez"]        # un legacy NO se oculta
    assert by_name(puesto="Analista BI") == ["Ana Li"] and by_name(puesto="__sin_puesto__") == ["Diego Fernandez"]
    assert by_name(area="RRHH") == ["Beto Gomez"] and by_name(area="OPERACIONES") == ["Carla Diaz"] and by_name(area="__sin_area__") == ["Diego Fernandez"]
    assert len(by_name()) == 4                                                              # sin filtros: ninguno oculto


def test_the_three_dropdowns_and_search_combine_with_and(monkeypatch):
    _seed_catalogs(fuente=["LinkedIn", "Referido"], area_consultora=["Consultoria", "RRHH"])
    rows = [_row(i, origen="LinkedIn" if i % 2 else "Referido", puesto="Analista BI" if i % 3 else "Desarrollador", area="Consultoria" if i % 5 else "RRHH") for i in range(1, 61)]
    _patch(monkeypatch, rows)
    expect = lambda f: sorted(f"Nombre{r['entrevista_id'][1:][-3:]} Apellido" for r in rows if f(r))
    got = _names(_get(origen="LinkedIn", puesto="Analista BI", area="Consultoria", page_size="300"))
    assert sorted(got) == expect(lambda r: r["origen"] == "LinkedIn" and r["puesto_mostrado"] == "Analista BI" and r["area"] == "Consultoria") and got
    only_q = sorted(n for r in rows for n in [f"Nombre{r['entrevista_id'][-3:]} Apellido"] if r["origen"] == "Referido" and r["area"] == "RRHH" and "nombre0" in n.lower())
    assert sorted(_names(_get(origen="Referido", area="RRHH", q="nombre0", page_size="300"))) == only_q and only_q               # dropdowns + búsqueda
    assert _names(_get(origen="LinkedIn", puesto="Desarrollador", area="RRHH", q="Nombre005")) == []                                  # un criterio excluye => nada


def test_search_covers_the_visible_columns_case_insensitive_and_never_technical_ids(monkeypatch):
    _seed_catalogs(fuente=["LinkedIn", "Referido"], area_consultora=["Consultoria", "RRHH"])
    _patch(monkeypatch, MIXED())
    n = lambda q: _names(_get(q=q))
    assert n("ana li") == ["Ana Li"] and n("  BETO   gomez ") == ["Beto Gomez"]               # candidato: case + trim + espacios colapsados
    assert n("referido") == ["Beto Gomez"] and n("full-stack") == ["Beto Gomez"] and n("consultoria") == ["Ana Li"] and n("operaciones") == ["Carla Diaz"]   # origen, puesto, área
    assert n("14-09-2026") == ["Beto Gomez"] and n("2026-10-05") == ["Carla Diaz"]             # fecha visible DMY e ISO
    assert n("part time") == ["Beto Gomez"] and n("1.800.000") == ["Ana Li"]                  # modalidad, remuneración
    assert n("no concretada") == ["Carla Diaz"]                                                # marca visible
    assert n("e00001") == [] and n("e0000") == []                                              # ids técnicos: no se busca sobre ellos
    assert _get(q="zzz").count('data-empty-kind="no_matches"') == 1 and "Sin resultados" in _get(q="zzz")


def test_clear_resets_the_four_criteria_to_page_1_and_keeps_the_page_size(monkeypatch):
    _seed_catalogs(fuente=["LinkedIn"], area_consultora=["Consultoria"])
    _patch(monkeypatch, [_row(i) for i in range(260)])
    html = _get(origen="LinkedIn", puesto="Analista BI", area="Consultoria", q="Nombre", page="2", page_size="100")
    form = html.split('class="hc-profile-filters filter-toolbar-card"', 1)[1].split("</form>", 1)[0]
    assert 'name="page_size" value="100"' in form and 'name="page"' not in form             # los filtros arrastran el tamaño y nunca la página
    assert re.findall(r'class="whatsnew-filter__clear" hx-get="([^"]+)"', form) == ["/cchh/entrevistas?page_size=100"]
    cleared = _client().get("/cchh/entrevistas?page_size=100", headers={"HX-Request": "true"}).text
    assert 'data-pager-page="1"' in cleared and 'data-pager-size="100"' in cleared and 'name="q" type="text" value=""' in cleared and 'value="LinkedIn" selected' not in cleared


# ── filtros antes de paginar / contador / estado ─────────────────────────────

def test_filters_apply_before_the_slice_and_the_counter_is_the_filtered_total(monkeypatch):
    _seed_catalogs(fuente=["LinkedIn", "Referido"], area_consultora=["Consultoria"])
    rows = [_row(i, origen="LinkedIn" if i < 68 else "Referido") for i in range(120)]
    _patch(monkeypatch, rows)
    p1, p2 = _get(origen="LinkedIn"), _get(origen="LinkedIn", page="2")
    assert (_total(p1), len(_names(p1)), _total(p2), len(_names(p2))) == ("68", 50, "68", 18)       # 68 de 120 = 50 + 18; el contador NO es la página
    assert set(_names(p1)).isdisjoint(_names(p2))
    assert _total(_get()) == "120"


def test_every_pager_link_keeps_origen_puesto_area_q_and_page_size_and_any_change_returns_to_page_1(monkeypatch):
    _seed_catalogs(fuente=["LinkedIn"], area_consultora=["Consultoria"])
    _patch(monkeypatch, [_row(i) for i in range(260)])
    filters = {"origen": "LinkedIn", "puesto": "Analista BI", "area": "Consultoria", "q": "nombre"}
    html = _get(**filters, page="2", page_size="100")
    nav = html.split('<nav class="activity-pager"', 1)[1].split("</nav>", 1)[0]
    links = [_html.unescape(u) for u in re.findall(r'hx-get="([^"]+)"', nav)]
    assert links
    for url in links:
        q = parse_qs(urlparse(url).query)
        assert all(q[k] == [v] for k, v in filters.items()), url
        if "page" in q:
            assert q["page_size"] == ["100"]
    assert len([u for u in links if "page" not in parse_qs(urlparse(u).query)]) == 1       # el selector de tamaño no lleva página => vuelve a la 1
    stale = _get(**filters, page="9", page_size="100")                                      # índice viejo tras angostar el filtro: se acota, nunca vacío
    assert _names(stale) and 'data-pager-page="3"' in stale
    narrow = _get(**{**filters, "q": "nombre007"}, page="3")
    assert _names(narrow) == ["Nombre007 Apellido"] and _total(narrow) == "1" and "data-pager" not in narrow


# ── return_to conserva los filtros nuevos ────────────────────────────────────

def _seed_real(n):
    _seed_catalogs(fuente=["LinkedIn", "Referido"], area_consultora=["Consultoria", "RRHH"])
    for i in range(n):
        pid = profile_repository.create({"first_name": f"Jose{i:03d}", "last_name": "Retorno", "email": f"j{i}@x.com", "source": "LinkedIn" if i % 2 == 0 else "Referido"})["profile_id"]
        human_capital_service.create_entrevista_manual(pid, {"area": "Consultoria" if i % 3 else "RRHH", "puesto_evaluado": "Analista BI"})


def test_return_to_preserves_origen_puesto_area_q_page_and_page_size_through_save_no_concretada_and_volver():
    _seed_real(420)                                                                         # LinkedIn(210) ∧ Consultoria(~140) ∧ "jose" => 3 páginas de 50
    c = _client(follow=False)
    filters = {"origen": "LinkedIn", "puesto": "Analista BI", "area": "Consultoria", "q": "jose"}
    page3 = c.get("/cchh/entrevistas", params={**filters, "page": "3", "page_size": "50"}).text
    assert 'data-pager-page="3"' in page3
    hrefs = [_html.unescape(h) for h in re.findall(r'href="(/cchh/entrevistas/[^"/]+/editar\?return_to=[^"]*)"', page3)]
    assert hrefs
    return_to = parse_qs(urlparse(hrefs[0]).query)["return_to"][0]
    rq = parse_qs(urlparse(return_to).query)
    assert urlparse(return_to).path == "/cchh/entrevistas" and rq == {"origen": ["LinkedIn"], "puesto": ["Analista BI"], "area": ["Consultoria"], "q": ["jose"], "page": ["3"], "page_size": ["50"]}
    page = _html.unescape(c.get(hrefs[0]).text)
    assert page.count(f'href="{return_to}"') >= 1                                           # ← Volver a Entrevistas
    action = re.search(r'<form[^>]*method="post"[^>]*action="(/cchh/entrevistas/[^"]+/editar\?return_to=[^"]+)"', page).group(1)
    no_conc = re.search(r'formaction="(/cchh/entrevistas/[^"]+/no-concretada\?return_to=[^"]+)"', page).group(1)
    for url in (action, no_conc):
        assert parse_qs(urlparse(url).query)["return_to"] == [return_to]
    saved = c.post(action, data={"observaciones": "ok", "expected_revision": "1"})
    assert saved.status_code == 303 and saved.headers["location"] == return_to              # Guardar -> mismo contexto exacto
    nc = c.post(no_conc, data={"observaciones": "x", "expected_revision": "2"})
    assert nc.status_code == 303 and nc.headers["location"] == return_to                    # No se concretó -> mismo contexto exacto
    back = c.get(saved.headers["location"]).text
    assert 'data-pager-page="3"' in back and 'value="LinkedIn" selected' in back and 'value="jose"' in back and 'value="Consultoria" selected' in back and 'data-pager-size="50"' in back
    delete = _html.unescape(re.search(r'action="(/cchh/entrevistas/[^"/]+/eliminar\?return_to=[^"]+)"', back).group(1))
    r = c.post(delete)
    assert r.status_code == 303 and r.headers["location"] == return_to                      # Eliminar también
