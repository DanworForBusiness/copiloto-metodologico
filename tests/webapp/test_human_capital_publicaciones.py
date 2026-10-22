"""test_human_capital_publicaciones.py — Publicaciones / Avisos (core): entidad, snapshot de la posición, canal gobernado, período history-safe, listado/filtros/paginación/return_to y delete.

Dominio verbatim de la app de referencia (app-seleccion: tabla `publicaciones`): registro HISTÓRICO plano (sin estados) de avisos publicados; `posicion` obligatoria y SNAPSHOT (se copia el puesto de la
Búsqueda al elegirla y no se sincroniza después); `proceso_id` opcional (Búsqueda 1 → 0..N Publicaciones); canal = catálogo gobernado `canal_publicacion`; período «YYYY-Qn». MISMO patrón de
aislamiento/durabilidad-en-test que el resto de CCHH; la mecánica del paginador y de `return_to` tiene sus propias suites (acá sólo lo propio de Publicaciones).
"""
from __future__ import annotations

import html as _html
import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import (
    auth_service, busqueda_repository, entrevista_repository, human_capital_service, onboarding_repository, profile_repository, publicacion_repository,
)
from webapp.services import human_capital_catalog_repository as catalog_repo

_AUTHORIZED_EMAIL = "hc-publicaciones-test@danwor.com"
TODAY_PERIODO = human_capital_service.periodo_actual()


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "remote")     # estos tests simulan fallos/confirmaciones de Spaces: ejercitan el contrato REMOTE (el modo es explícito)
    monkeypatch.setattr(os, "fsync", lambda fd: None)
    for mod in (publicacion_repository, onboarding_repository, entrevista_repository, busqueda_repository, profile_repository):
        monkeypatch.setattr(mod.remote_store, "push_path", lambda path: True)
        monkeypatch.setattr(mod.remote_store, "delete_path", lambda path: True)
        monkeypatch.setattr(mod.remote_store, "enabled", lambda: False)
    _seed_canales("LinkedIn Jobs", "Bumeran", "Computrabajo")


def _client(follow=True) -> TestClient:
    c = TestClient(app, follow_redirects=follow)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": _AUTHORIZED_EMAIL, "name": "P T", "first_name": "P", "family_name": "T"}))
    return c


def _seed_canales(*values):
    snap = catalog_repo.build_snapshot({"canal_publicacion": [{"valor": v, "orden": i + 1} for i, v in enumerate(values)]}, refreshed_by="test", source={"drive_id": "d", "item_id": "i"})
    p = catalog_repo.snapshot_path(); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(catalog_repo._serialize(snap))


def _busqueda(nombre="Analista BI", **over) -> dict:
    return human_capital_service.create_busqueda({"nombre_puesto": nombre, "fecha_apertura": "2026-09-01", **over})


def _pub(**over) -> dict:
    base = {"busqueda_id": "", "posicion": "Account Executive", "canal": "LinkedIn Jobs", "cantidad_avisos": 1, "periodo": TODAY_PERIODO, "fecha": "", "observacion": ""}
    base.update(over)
    return human_capital_service.create_publicacion(base)


# ── entidad ──────────────────────────────────────────────────────────────────

def test_a_publication_without_busqueda_keeps_the_free_text_position_and_has_no_lifecycle_fields():
    p = _pub(posicion="  Account Executive  ", observacion="  nota  ")
    assert (p["posicion"], p["busqueda_id"], p["canal"], p["cantidad_avisos"], p["periodo"], p["fecha"], p["observacion"], p["revision"]) == (
        "Account Executive", None, "LinkedIn Jobs", 1, TODAY_PERIODO, None, "nota", 1)
    assert p["publicacion_id"] and p["created_at"] and p["updated_at"]
    assert not {"estado", "status", "publicada", "cerrada", "expira", "vigencia"} & set(p)                      # no se inventan estados ni vigencia
    assert human_capital_service.get_publicacion(p["publicacion_id"]) == p


def test_one_busqueda_has_many_publications_and_a_publication_belongs_to_at_most_one_busqueda():
    b = _busqueda("Analista BI")
    pubs = [_pub(busqueda_id=b["busqueda_id"], canal=c) for c in ("LinkedIn Jobs", "Bumeran", "Computrabajo")]
    free = _pub()
    assert {p["busqueda_id"] for p in pubs} == {b["busqueda_id"]} and free["busqueda_id"] is None
    assert len([p for p in publicacion_repository.list() if p["busqueda_id"] == b["busqueda_id"]]) == 3
    assert not any(k in free for k in ("application_id", "profile_id"))                                         # sin relación con CandidateApplication / Profile


def test_required_fields_and_quantity_rules():
    for bad, msg in (({"posicion": ""}, "posición"), ({"canal": ""}, "canal"), ({"cantidad_avisos": 0}, "al menos 1"), ({"cantidad_avisos": "x"}, "al menos 1"),
                     ({"cantidad_avisos": -2}, "al menos 1"), ({"periodo": ""}, "período"), ({"fecha": "31/12/2026"}, "fecha"), ({"busqueda_id": "no-existe"}, "no existe")):
        with pytest.raises(ValueError, match=msg):
            _pub(**bad)
    assert publicacion_repository.list() == []
    assert _pub(cantidad_avisos="3")["cantidad_avisos"] == 3 and _pub(fecha="2026-10-02")["fecha"] == "2026-10-02"


# ── snapshot ─────────────────────────────────────────────────────────────────

def test_create_with_a_busqueda_copies_its_current_position_ignoring_the_typed_text():
    b = _busqueda("Analista BI")
    p = _pub(busqueda_id=b["busqueda_id"], posicion="texto que se ignora")
    assert (p["busqueda_id"], p["posicion"]) == (b["busqueda_id"], "Analista BI")


def test_a_later_rename_of_the_busqueda_never_alters_the_publication_even_after_unrelated_edits():
    b = _busqueda("Analista BI")
    p = _pub(busqueda_id=b["busqueda_id"])
    human_capital_service.update_busqueda(b["busqueda_id"], 1, {"nombre_puesto": "Analista BI Senior", "fecha_apertura": "2026-09-01"})
    assert human_capital_service.get_publicacion(p["publicacion_id"])["posicion"] == "Analista BI"            # sin sincronización automática
    view = human_capital_service.publicacion_view(human_capital_service.get_publicacion(p["publicacion_id"]))
    assert view["posicion"] == "Analista BI" and view["busqueda_nombre"] == "Analista BI Senior"               # se muestra el snapshot; la Búsqueda se lee en vivo sólo para identificarla
    edited = human_capital_service.update_publicacion(p["publicacion_id"], 1, {"busqueda_id": b["busqueda_id"], "posicion": "otra cosa", "canal": "Bumeran", "cantidad_avisos": 2,
                                                                                 "periodo": TODAY_PERIODO, "fecha": "", "observacion": "x"})
    assert edited["posicion"] == "Analista BI" and edited["canal"] == "Bumeran"                              # editar con el MISMO vínculo conserva el snapshot histórico


def test_explicitly_changing_the_busqueda_refreshes_the_snapshot_and_clearing_it_requires_free_text():
    b1, b2 = _busqueda("Analista BI"), _busqueda("Desarrollador Full-Stack")
    p = _pub(busqueda_id=b1["busqueda_id"])
    common = {"canal": "LinkedIn Jobs", "cantidad_avisos": 1, "periodo": TODAY_PERIODO, "fecha": "", "observacion": ""}
    moved = human_capital_service.update_publicacion(p["publicacion_id"], 1, {"busqueda_id": b2["busqueda_id"], "posicion": "", **common})
    assert (moved["busqueda_id"], moved["posicion"]) == (b2["busqueda_id"], "Desarrollador Full-Stack")       # cambio explícito => puesto de la nueva Búsqueda
    with pytest.raises(ValueError, match="posición"):
        human_capital_service.update_publicacion(p["publicacion_id"], 2, {"busqueda_id": "", "posicion": "  ", **common})
    freed = human_capital_service.update_publicacion(p["publicacion_id"], 2, {"busqueda_id": "", "posicion": "Aviso genérico", **common})
    assert (freed["busqueda_id"], freed["posicion"]) == (None, "Aviso genérico")


def test_a_publication_whose_busqueda_disappeared_keeps_its_position_and_stays_editable():
    b = _busqueda("Analista BI")
    p = _pub(busqueda_id=b["busqueda_id"])
    busqueda_repository._busqueda_path(b["busqueda_id"]).unlink()
    out = human_capital_service.update_publicacion(p["publicacion_id"], 1, {"busqueda_id": b["busqueda_id"], "posicion": "", "canal": "LinkedIn Jobs", "cantidad_avisos": 1,
                                                                             "periodo": TODAY_PERIODO, "fecha": "", "observacion": "sigue editable"})
    assert out["posicion"] == "Analista BI" and out["observacion"] == "sigue editable"
    page = _client().get(f"/cchh/publicaciones/{p['publicacion_id']}/editar").text
    assert "Búsqueda no disponible" in page


# ── catálogo canal_publicacion ───────────────────────────────────────────────

def test_canal_is_the_governed_catalog_active_values_only_on_create_and_there_is_no_hardcoded_fallback():
    assert _pub(canal="Computrabajo")["canal"] == "Computrabajo"
    with pytest.raises(ValueError, match="Canal inválido"):
        _pub(canal="Flyer")                                                                                    # no está en el catálogo => rechazado aunque sea un valor de la referencia
    _seed_canales()                                                                                            # catálogo productivo vacío => fail-closed
    with pytest.raises(ValueError, match="catálogo de Canales de publicación todavía no está disponible"):
        _pub(canal="LinkedIn Jobs")
    root = Path(__import__("webapp").__file__).parent
    for src in (root / "services" / "human_capital_service.py", root / "services" / "publicacion_repository.py", root / "routers" / "human_capital.py",
                root / "templates" / "human_capital" / "publicacion_form.html", root / "templates" / "partials" / "human_capital_publicaciones_panel.html"):
        text = src.read_text(encoding="utf-8")
        assert not re.search(r"""["'](LinkedIn|Flyer)["']""", re.sub(r"#.*|\{#.*?#\}", "", text, flags=re.S)), src.name   # ningún canal hardcodeado fuera de comentarios


def test_a_legacy_channel_is_preserved_on_unrelated_edits_but_a_change_needs_an_active_value():
    p = _pub()
    legacy = publicacion_repository.update(p["publicacion_id"], 1, {"canal": "Flyer"})                          # histórico fuera de catálogo (como cargado antes)
    base = {"busqueda_id": "", "posicion": "Account Executive", "cantidad_avisos": 1, "periodo": TODAY_PERIODO, "fecha": "", "observacion": ""}
    kept = human_capital_service.update_publicacion(p["publicacion_id"], legacy["revision"], {**base, "canal": "Flyer", "observacion": "edición no relacionada"})
    assert kept["canal"] == "Flyer" and kept["observacion"] == "edición no relacionada"
    with pytest.raises(ValueError, match="Canal inválido"):
        human_capital_service.update_publicacion(p["publicacion_id"], kept["revision"], {**base, "canal": "Otro portal"})
    assert human_capital_service.update_publicacion(p["publicacion_id"], kept["revision"], {**base, "canal": "Bumeran"})["canal"] == "Bumeran"
    page = _client().get(f"/cchh/publicaciones/{p['publicacion_id']}/editar").text
    assert '<option value="Bumeran" selected>Bumeran</option>' in page


def test_the_form_offers_the_active_catalog_and_flags_a_legacy_value():
    p = _pub()
    publicacion_repository.update(p["publicacion_id"], 1, {"canal": "Flyer"})
    html = _client().get(f"/cchh/publicaciones/{p['publicacion_id']}/editar").text
    sel = html.split('id="pub-canal"', 1)[1].split("</select>", 1)[0]
    assert re.findall(r'<option value="([^"]*)"', sel) == ["", "Flyer", "LinkedIn Jobs", "Bumeran", "Computrabajo"]
    assert 'value="Flyer" selected>Flyer (fuera de catálogo)' in sel and "Valor histórico fuera del catálogo vigente" in html
    _seed_canales()
    assert "Catálogo no disponible" in _client().get("/cchh/publicaciones/nueva").text


# ── período ──────────────────────────────────────────────────────────────────

def test_periodo_format_default_window_and_quarter_rule():
    assert human_capital_service.periodo_desde_fecha(date(2026, 3, 31)) == "2026-Q1" and human_capital_service.periodo_desde_fecha(date(2026, 10, 1)) == "2026-Q4"
    assert human_capital_service.periodo_actual(date(2026, 10, 19)) == "2026-Q4"
    assert human_capital_service.periodos_disponibles(date(2026, 10, 19)) == ["2027-Q4", "2027-Q3", "2027-Q2", "2027-Q1", "2026-Q4", "2026-Q3", "2026-Q2", "2026-Q1"]
    for bad in ("2026-Q5", "2026Q3", "26-Q3", "2026-q3", "Q3-2026", "2026-Q0"):
        with pytest.raises(ValueError, match="formato"):
            _pub(periodo=bad)
    with pytest.raises(ValueError, match="de la lista"):
        _pub(periodo="2019-Q1")                                                                                 # un alta sólo acepta la ventana calculada
    page = _client().get("/cchh/publicaciones/nueva").text
    assert f'<option value="{TODAY_PERIODO}" selected>' in page and 'value="2019-Q1"' not in page            # default = trimestre actual


def test_a_historical_period_is_preserved_selected_and_editable_but_cannot_be_chosen_again_on_a_change():
    p = _pub()
    old = publicacion_repository.update(p["publicacion_id"], 1, {"periodo": "2024-Q2"})                       # registro histórico fuera de la ventana
    base = {"busqueda_id": "", "posicion": "Account Executive", "canal": "LinkedIn Jobs", "cantidad_avisos": 1, "fecha": "", "observacion": ""}
    kept = human_capital_service.update_publicacion(p["publicacion_id"], old["revision"], {**base, "periodo": "2024-Q2", "observacion": "histórico"})
    assert kept["periodo"] == "2024-Q2" and kept["observacion"] == "histórico"
    with pytest.raises(ValueError, match="de la lista"):
        human_capital_service.update_publicacion(p["publicacion_id"], kept["revision"], {**base, "periodo": "2023-Q1"})
    html = _client().get(f"/cchh/publicaciones/{p['publicacion_id']}/editar").text
    assert '<option value="2024-Q2" selected>2024-Q2</option>' in html and "Período histórico fuera de la ventana actual" in html
    r = _client(follow=False).post(f"/cchh/publicaciones/{p['publicacion_id']}/editar", data={**base, "busqueda_id": "", "posicion": "Account Executive", "periodo": "2024-Q2", "cantidad_avisos": "1",
                                                                                              "expected_revision": str(kept["revision"])})
    assert r.status_code == 303 and human_capital_service.get_publicacion(p["publicacion_id"])["periodo"] == "2024-Q2"


# ── listado / filtros ────────────────────────────────────────────────────────

def _names(html: str) -> list[str]:
    return re.findall(r'dw-idcell__name">([^<]*)<', html)


def _get(**params):
    return _client().get("/cchh/publicaciones", params=params, headers={"HX-Request": "true"}).text


def _total(html: str) -> str:
    return re.search(r'dashboard-section__count">(\d+) publicaci', html).group(1)


def _options(html: str, field: str) -> list[tuple[str, str]]:
    block = html.split(f'id="hc-publicaciones-{field}"', 1)[1].split("</select>", 1)[0]
    return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)<', block)


def _seed_mixed():
    b = _busqueda("Analista de Datos BI")
    _pub(busqueda_id=b["busqueda_id"], canal="LinkedIn Jobs", periodo="2026-Q3", fecha="2026-08-18", cantidad_avisos=2, observacion="Aviso principal")
    _pub(posicion="Account Executive", canal="Bumeran", periodo="2026-Q4", observacion="")
    _pub(posicion="Pasante de RRHH", canal="Bumeran", periodo="2026-Q3", fecha="2026-09-12", observacion="repost semanal")
    legacy = _pub(posicion="Cadete", canal="Computrabajo", periodo="2026-Q3")
    publicacion_repository.update(legacy["publicacion_id"], 1, {"canal": "Flyer", "periodo": "2024-Q2"})      # canal histórico + período fuera de la ventana
    return b


def test_list_page_columns_actions_nav_and_plain_text_channel():
    _seed_mixed()
    c = _client()
    page = c.get("/cchh/publicaciones").text
    assert "<h1>Publicaciones</h1>" in page and "+ Nueva publicación" in page and "<strong>Avisos</strong>" in page and "05" in page
    assert 'href="/cchh/publicaciones" class="module-link is-active"' in page
    for col in ("posicion", "canal", "cantidad", "periodo", "fecha", "observacion"):
        assert f'data-column-key="{col}"' in page
    assert 'data-draggable-columns-key="cchh-publicaciones"' in page and 'data-resizable-columns-key="cchh-publicaciones"' in page and 'data-dashboard-key="cchh-publicaciones"' in page
    assert page.count("/editar?return_to=") == 4 and page.count("/eliminar?return_to=") == 4 and "Ver publicación" not in page        # sólo Editar / Eliminar
    canal_cells = re.findall(r'<td class="left" data-column-key="canal">(.*?)</td>', page, re.S)
    assert sorted(x.strip() for x in canal_cells) == ["Bumeran", "Bumeran", "Flyer", "LinkedIn Jobs"] and not any("status-badge" in x for x in canal_cells)   # texto normal, sin badge ni color por canal
    assert "Búsqueda: Analista de Datos BI" in page and "18-08-2026" in page                                                        # búsqueda vinculada y fecha DD-MM-YYYY


def test_list_header_counter_is_the_filtered_total_with_period_desc_ordering():
    _seed_mixed()
    html = _get()
    assert _total(html) == "4" and _names(html) == ["Account Executive", "Pasante de RRHH", "Analista de Datos BI", "Cadete"]      # período desc (2026-Q4, 2026-Q3 …, 2024-Q2), luego alta desc
    assert "1 publicación<" in _get(canal="LinkedIn Jobs").replace("</span>", "<")


def test_period_options_are_the_periods_present_in_the_dataset_not_the_create_window():
    _seed_mixed()
    assert _options(_get(), "periodo") == [("", "Todos"), ("2026-Q4", "2026-Q4"), ("2026-Q3", "2026-Q3"), ("2024-Q2", "2024-Q2")]
    assert _options(_get(canal="Bumeran", q="account"), "periodo") == _options(_get(), "periodo")       # no desaparecen al aplicar otros filtros


def test_canal_options_are_the_active_catalog_plus_legacy_present_without_sin_canal():
    _seed_mixed()
    assert _options(_get(), "canal") == [("", "Todos"), ("LinkedIn Jobs", "LinkedIn Jobs"), ("Bumeran", "Bumeran"), ("Computrabajo", "Computrabajo"), ("Flyer", "Flyer (fuera de catálogo)")]


def test_period_and_channel_filters_and_search_combine_with_and():
    _seed_mixed()
    assert sorted(_names(_get(periodo="2026-Q3"))) == ["Analista de Datos BI", "Pasante de RRHH"]
    assert sorted(_names(_get(canal="Bumeran"))) == ["Account Executive", "Pasante de RRHH"] and _names(_get(canal="Flyer")) == ["Cadete"]            # el canal histórico no se oculta
    assert _names(_get(periodo="2026-Q3", canal="Bumeran")) == ["Pasante de RRHH"]
    assert _names(_get(periodo="2026-Q3", canal="Bumeran", q="repost")) == ["Pasante de RRHH"] and _names(_get(periodo="2026-Q4", canal="Bumeran", q="repost")) == []
    assert _names(_get(periodo="2026-Q3", canal="LinkedIn Jobs", q="aviso")) == ["Analista de Datos BI"]


def test_search_covers_position_channel_period_dates_observation_and_busqueda_name_but_never_ids():
    b = _seed_mixed()
    n = lambda q: _names(_get(q=q))
    assert n("  PASANTE   de rrhh ") == ["Pasante de RRHH"]                                                  # case + trim + espacios colapsados
    assert n("bumeran") == ["Account Executive", "Pasante de RRHH"] and n("computrabajo") == [] and n("flyer") == ["Cadete"]      # canal
    assert n("2024-q2") == ["Cadete"] and n("2026-Q4") == ["Account Executive"]                           # período
    assert n("12-09-2026") == ["Pasante de RRHH"] and n("2026-08-18") == ["Analista de Datos BI"]          # fecha visible e ISO
    assert n("repost semanal") == ["Pasante de RRHH"]                                                       # observación
    assert n("datos bi") == ["Analista de Datos BI"]                                                        # posición / nombre de la Búsqueda vinculada
    human_capital_service.update_busqueda(b["busqueda_id"], 1, {"nombre_puesto": "Nombre Nuevo De Busqueda", "fecha_apertura": "2026-09-01"})
    assert n("nombre nuevo de busqueda") == ["Analista de Datos BI"] and n("analista de datos bi") == ["Analista de Datos BI"]      # nombre vivo de la Búsqueda + posición snapshot
    html = _get()
    some_id = re.search(r'/cchh/publicaciones/([0-9a-f-]{36})/editar', html).group(1)
    assert n(some_id) == [] and n(some_id[:8]) == []                                                       # no se busca sobre ids técnicos
    assert _get(q="zzz").count('data-empty-kind="no_matches"') == 1


def test_the_filter_bar_reuses_the_shared_toolbar_in_the_requested_order_and_clear_keeps_the_page_size():
    _seed_mixed()
    html = _get(periodo="2026-Q3", canal="Bumeran", q="x", page_size="100")
    bar = html.split('<form class="hc-profile-filters filter-toolbar-card"', 1)[1].split("</form>", 1)[0]
    order = [bar.index(x) for x in ('id="hc-publicaciones-periodo"', 'id="hc-publicaciones-canal"', 'id="hc-publicaciones-q"', "✕ Limpiar")]
    assert order == sorted(order) and bar.count("feedback-field--auto") == 2 and bar.count("feedback-field--wide") == 1 and "style=" not in bar
    assert bar.count('hx-include="closest form"') == 3 and 'hx-trigger="keyup changed delay:500ms"' in bar and 'name="page_size" value="100"' in bar and 'name="page"' not in bar
    assert re.findall(r'class="whatsnew-filter__clear" hx-get="([^"]+)"', bar) == ["/cchh/publicaciones?page_size=100"]
    css = (Path(__import__("webapp").__file__).parent / "static" / "human_capital.css").read_text(encoding="utf-8")
    own = re.sub(r"/\*.*?\*/", "", css, flags=re.S).replace(".module-hero + #hc-publicaciones-panel", "")            # la ÚNICA mención es el respiro hero → primer componente (24px) compartido con las otras secciones
    assert "publicaciones" not in own                                                                           # cero CSS propio de Publicaciones


# ── estándar de tabla: paginación / return_to ────────────────────────────────

def _seed_n(n: int, monkeypatch):
    rows = [{"publicacion_id": f"p{i:05d}", "busqueda_id": None, "busqueda": None, "busqueda_nombre": None, "posicion": f"Posicion {i:04d}", "canal": "Bumeran" if i % 2 else "LinkedIn Jobs",
             "cantidad_avisos": 1, "periodo": "2026-Q3", "fecha": None, "observacion": None, "created_at": f"2026-01-01T00:{i // 60 % 60:02d}:{i % 60:02d}+00:00"} for i in range(n)]
    monkeypatch.setattr(human_capital_service, "list_publicaciones_view", lambda: list(rows))


@pytest.mark.parametrize("n,pages", [(49, 1), (50, 1), (51, 2)])
def test_the_shared_paginator_applies_default_50_and_filters_run_before_the_slice(monkeypatch, n, pages):
    _seed_n(n, monkeypatch)
    html = _get()
    assert len(_names(html)) == min(50, n) and _total(html) == str(n)
    assert ("data-pager" in html) is (pages > 1) and ('data-pager-pages="2"' in html) is (pages > 1)
    if pages > 1:
        assert 'class="activity-pager"' in html and re.findall(r'<option value="(\d+)"', html.split('class="activity-pager__size"', 1)[1].split("</select>", 1)[0]) == ["50", "100", "200", "300"]


def test_filtered_total_drives_the_counter_and_pagination_and_any_change_returns_to_page_1(monkeypatch):
    _seed_n(120, monkeypatch)                                                                                   # Bumeran = 60 de 120
    p1, p2 = _get(canal="Bumeran"), _get(canal="Bumeran", page="2")
    assert (_total(p1), len(_names(p1)), _total(p2), len(_names(p2))) == ("60", 50, "60", 10)
    stale = _get(canal="Bumeran", q="Posicion 0003", page="9")
    assert _names(stale) == ["Posicion 0003"] and _total(stale) == "1" and "data-pager" not in stale
    assert 'name="page_size" value="100"' in _get(page_size="100", page="2").split("</form>", 1)[0]


def _seed_real(n: int):
    for i in range(n):
        _pub(posicion=f"Posicion {i:04d}", canal="Bumeran" if i % 2 else "LinkedIn Jobs", periodo="2026-Q3" if i % 3 else "2026-Q4", observacion="retorno")


def test_return_to_preserves_periodo_canal_q_page_and_page_size_through_edit_save_and_delete():
    _seed_real(330)
    c = _client(follow=False)
    filters = {"periodo": "2026-Q3", "canal": "Bumeran", "q": "retorno"}                                      # Bumeran ∧ 2026-Q3 = ~110 => 3 páginas de 50
    page3 = c.get("/cchh/publicaciones", params={**filters, "page": "3", "page_size": "50"}).text
    assert 'data-pager-page="3"' in page3
    hrefs = [_html.unescape(h) for h in re.findall(r'href="(/cchh/publicaciones/[^"/]+/editar\?return_to=[^"]*)"', page3)]
    return_to = parse_qs(urlparse(hrefs[0]).query)["return_to"][0]
    assert parse_qs(urlparse(return_to).query) == {"periodo": ["2026-Q3"], "canal": ["Bumeran"], "q": ["retorno"], "page": ["3"], "page_size": ["50"]}
    page = _html.unescape(c.get(hrefs[0]).text)
    assert page.count(f'href="{return_to}"') >= 1                                                           # ← Volver a Publicaciones
    action = re.search(r'<form method="post" action="(/cchh/publicaciones/[^"]+/editar\?return_to=[^"]+)"', page).group(1)
    rev = re.search(r'name="expected_revision" value="(\d+)"', page).group(1)
    saved = c.post(action, data={"busqueda_id": "", "posicion": "Editada", "canal": "Bumeran", "cantidad_avisos": "2", "periodo": "2026-Q3", "fecha": "", "observacion": "retorno", "expected_revision": rev})
    assert saved.status_code == 303 and saved.headers["location"] == return_to
    back = c.get(saved.headers["location"]).text
    assert 'data-pager-page="3"' in back and 'value="2026-Q3" selected' in back and 'value="Bumeran" selected' in back and 'value="retorno"' in back and 'data-pager-size="50"' in back
    delete = _html.unescape(re.search(r'action="(/cchh/publicaciones/[^"/]+/eliminar\?return_to=[^"]+)"', back).group(1))
    gone = delete.split("/")[3]
    r = c.post(delete)
    assert r.status_code == 303 and r.headers["location"] == return_to and human_capital_service.get_publicacion(gone) is None


def test_deleting_the_last_row_of_the_last_page_clamps_to_the_last_valid_page():
    _seed_real(101)
    c = _client(follow=False)
    page3 = c.get("/cchh/publicaciones", params={"page": "3"}).text
    assert 'data-pager-page="3"' in page3
    delete = _html.unescape(re.search(r'action="(/cchh/publicaciones/[^"/]+/eliminar\?return_to=[^"]+)"', page3).group(1))
    r = c.post(delete)
    after = c.get(r.headers["location"]).text
    assert 'data-pager-page="2" data-pager-pages="2"' in after and _total(after) == "100"


# ── formularios / rutas / delete ─────────────────────────────────────────────

def test_create_form_posts_with_a_busqueda_and_redirects_to_the_list_with_the_snapshot_saved():
    b = _busqueda("Analista BI")
    c = _client(follow=False)
    page = c.get("/cchh/publicaciones/nueva").text
    assert 'name="busqueda_id"' in page and "Analista BI" in page and 'name="posicion"' in page and 'name="canal"' in page and 'name="cantidad_avisos"' in page and 'name="periodo"' in page
    assert "<optgroup label=\"Activas\">" in page and "Cancelar" in page and "Crear publicación" in page
    r = c.post("/cchh/publicaciones/nueva", data={"busqueda_id": b["busqueda_id"], "posicion": "", "canal": "Bumeran", "cantidad_avisos": "2", "periodo": TODAY_PERIODO, "fecha": "2026-10-02", "observacion": "ok"})
    assert r.status_code == 303 and r.headers["location"] == "/cchh/publicaciones"
    (p,) = publicacion_repository.list()
    assert (p["posicion"], p["busqueda_id"], p["canal"], p["cantidad_avisos"], p["fecha"]) == ("Analista BI", b["busqueda_id"], "Bumeran", 2, "2026-10-02")


def test_create_and_edit_validation_errors_rerender_with_the_typed_values_and_concurrency_is_optimistic():
    c = _client(follow=False)
    bad = c.post("/cchh/publicaciones/nueva", data={"busqueda_id": "", "posicion": "", "canal": "Bumeran", "cantidad_avisos": "1", "periodo": TODAY_PERIODO})
    assert bad.status_code == 200 and "No se guardó" in bad.text and "Completá la posición" in bad.text and publicacion_repository.list() == []
    p = _pub()
    data = {"busqueda_id": "", "posicion": "Otra", "canal": "Bumeran", "cantidad_avisos": "1", "periodo": TODAY_PERIODO, "fecha": "", "observacion": "", "expected_revision": "1"}
    ok = c.post(f"/cchh/publicaciones/{p['publicacion_id']}/editar", data=data)
    assert ok.status_code == 303 and human_capital_service.get_publicacion(p["publicacion_id"])["revision"] == 2
    stale = c.post(f"/cchh/publicaciones/{p['publicacion_id']}/editar", data=data)
    assert "cambió desde que abriste el formulario" in stale.text
    invalid = c.post(f"/cchh/publicaciones/{p['publicacion_id']}/editar", data={**data, "cantidad_avisos": "0", "expected_revision": "2"})
    assert invalid.status_code == 200 and "al menos 1" in invalid.text and f'/cchh/publicaciones/{p["publicacion_id"]}/editar' in invalid.text
    assert c.post(f"/cchh/publicaciones/{p['publicacion_id']}/editar", data={**data, "expected_revision": "x"}).status_code == 400
    assert c.post("/cchh/publicaciones/no-existe/editar", data=data).status_code == 404 and c.get("/cchh/publicaciones/no-existe/editar").status_code == 404


def test_delete_is_a_hard_delete_without_preconditions_and_idempotent():
    b = _busqueda("Analista BI")
    p = _pub(busqueda_id=b["busqueda_id"])
    c = _client(follow=False)
    page = c.get("/cchh/publicaciones").text
    assert 'data-dw-confirm-form' in page and 'data-dw-confirm-title="Eliminar publicación"' in page and "Esta acción no se puede deshacer" in page
    r = c.post(f"/cchh/publicaciones/{p['publicacion_id']}/eliminar")
    assert r.status_code == 303 and r.headers["location"] == "/cchh/publicaciones"
    assert human_capital_service.get_publicacion(p["publicacion_id"]) is None and not publicacion_repository._publicacion_path(p["publicacion_id"]).exists()
    assert busqueda_repository.get(b["busqueda_id"]) is not None                                                  # la Búsqueda NO se toca
    assert c.post(f"/cchh/publicaciones/{p['publicacion_id']}/eliminar").status_code == 303                       # ya no existe: idempotente


def test_a_failed_remote_delete_rolls_back_the_local_file(monkeypatch):
    p = _pub()
    monkeypatch.setattr(publicacion_repository.remote_store, "delete_path", lambda path: False)
    monkeypatch.setattr(publicacion_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(publicacion_repository.remote_store, "await_restore", lambda timeout=None: None)
    with pytest.raises(publicacion_repository.PublicacionRemoteOutcomeUnknownError):
        human_capital_service.delete_publicacion(p["publicacion_id"])
    assert human_capital_service.get_publicacion(p["publicacion_id"]) is not None


def test_routes_require_the_human_capital_session_and_empty_states_differ():
    anon = TestClient(app, follow_redirects=False)
    assert anon.get("/cchh/publicaciones").status_code in (302, 303, 307)
    other = TestClient(app, follow_redirects=False)
    other.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "x@danwor.com", "name": "X", "first_name": "X", "family_name": "Y"}))
    assert other.get("/cchh/publicaciones").status_code == 403 and other.post("/cchh/publicaciones/x/eliminar").status_code == 403
    empty = _client().get("/cchh/publicaciones").text
    assert 'data-empty-kind="dataset_empty"' in empty and "0 publicaciones" in empty
    _pub()
    assert 'data-empty-kind="no_matches"' in _get(q="zzz")


def test_the_shared_topbar_refresh_button_and_back_link_render_on_publicaciones_pages():
    p = _pub()
    c = _client()
    for url in ("/cchh/publicaciones", "/cchh/publicaciones/nueva", f"/cchh/publicaciones/{p['publicacion_id']}/editar"):
        html = c.get(url).text
        assert 'hx-post="/cchh/catalogos/refresh"' in html and "CAPITAL HUMANO" in html, url
    assert 'class="catalog-back" href="/cchh/publicaciones"' in c.get("/cchh/publicaciones/nueva").text


# ── resumen de métricas (formato Hub) ────────────────────────────────────────

def _summary(rows, all_rows=None, today=date(2026, 10, 19)):
    return human_capital_service.publicaciones_summary(rows, rows if all_rows is None else all_rows, today=today)


def _r(posicion="Account Executive", canal="Bumeran", cantidad=1, periodo="2026-Q4") -> dict:
    return {"posicion": posicion, "canal": canal, "cantidad_avisos": cantidad, "periodo": periodo}


def test_avisos_publicados_is_the_sum_of_cantidad_not_the_row_count():
    s = _summary([_r(cantidad=3), _r(cantidad=2), _r(cantidad=1)])
    assert s["avisos_publicados"] == 6 and s["registros"] == 3


def test_busquedas_difundidas_counts_distinct_normalized_positions_like_the_reference_not_busqueda_ids():
    rows = [_r("Account Executive"), _r("  account executive "), _r("ACCOUNT EXECUTIVE"), _r("Cadete"), _r("")]
    assert _summary(rows)["busquedas_difundidas"] == 3                       # «account executive», «cadete», «» (trim + minúsculas)
    b = _busqueda("Analista BI")
    _pub(busqueda_id=b["busqueda_id"], periodo="2026-Q3")
    _pub(busqueda_id=b["busqueda_id"], periodo="2026-Q4")
    _pub(posicion="Analista BI", periodo="2026-Q4")                          # sin vínculo pero MISMA posición → no suma
    assert _summary(human_capital_service.list_publicaciones_view())["busquedas_difundidas"] == 1


def test_registros_cargados_counts_rows():
    assert _summary([_r(cantidad=5), _r(cantidad=5)])["registros"] == 2 and _summary([])["registros"] == 0


def test_este_trimestre_sums_only_the_current_period_over_the_full_dataset_and_ignores_the_filtered_subset():
    allr = [_r(cantidad=2, periodo="2026-Q4"), _r(cantidad=4, periodo="2026-Q4"), _r(cantidad=9, periodo="2026-Q3"), _r(cantidad=7, periodo="2025-Q4")]
    s = _summary([allr[2]], all_rows=allr)                                   # subset filtrado al Q3
    assert s["avisos_trimestre"] == 6 and s["periodo_actual"] == "2026-Q4" and s["avisos_publicados"] == 9
    assert _summary(allr, today=date(2026, 1, 5))["periodo_actual"] == "2026-Q1"


def test_avisos_por_canal_groups_sums_orders_desc_with_stable_ties_and_keeps_legacy_channels_without_the_catalog():
    s = _summary([_r(canal="Bumeran", cantidad=2), _r(canal="Flyer", cantidad=5), _r(canal="Bumeran", cantidad=1), _r(canal="Computrabajo", cantidad=3), _r(canal="Alpha", cantidad=3)])
    assert [(x["label"], x["count"]) for x in s["por_canal"]] == [("Flyer", 5), ("Alpha", 3), ("Bumeran", 3), ("Computrabajo", 3)]
    assert [x["pct"] for x in s["por_canal"]] == [100, 60, 60, 60]
    catalog_repo.snapshot_path().unlink()                                    # sin catálogo activo: la distribución no depende de él
    assert [x["label"] for x in _summary([_r(canal="Flyer", cantidad=1)])["por_canal"]] == ["Flyer"]


def test_avisos_por_periodo_groups_sums_and_orders_chronologically_with_only_present_periods():
    s = _summary([_r(periodo="2026-Q3", cantidad=2), _r(periodo="2025-Q4", cantidad=1), _r(periodo="2026-Q4", cantidad=4), _r(periodo="2026-Q3", cantidad=1), _r(periodo="2024-Q2", cantidad=3)])
    assert [(x["label"], x["count"]) for x in s["por_periodo"]] == [("2026-Q4", 4), ("2026-Q3", 3), ("2025-Q4", 1), ("2024-Q2", 3)]
    assert "2026-Q1" not in [x["label"] for x in s["por_periodo"]]


def _kpis(html: str) -> dict[str, str]:
    return {label: value for value, label in re.findall(r'activity-kpi__value">([^<]*)</span><span class="activity-kpi__label">([^<]*)</span>', html)}


def _bars(html: str, title: str) -> list[tuple[str, str]]:
    block = html.split(f'dashboard-subhead">{title}</p>', 1)[1].split("</div>\n      </div>", 1)[0] if title == "Avisos por período" else html.split(f'dashboard-subhead">{title}</p>', 1)[1].split('<div>\n          <p class="dashboard-subhead">', 1)[0]
    return re.findall(r'activity-bar-label" title="[^"]*">([^<]*)</span>.*?activity-bar-count">(\d+)<', block, flags=re.S)


def test_the_page_renders_the_four_kpis_and_both_distributions_following_the_filters_except_este_trimestre():
    _seed_mixed()
    html = _get()
    k = _kpis(html)
    assert k["Avisos publicados"] == "5" and k["Registros cargados"] == "4" and k["Búsquedas difundidas"] == "4"
    assert f"Este trimestre · {TODAY_PERIODO}" in k
    assert _bars(html, "Avisos por período") == [("2026-Q4", "1"), ("2026-Q3", "3"), ("2024-Q2", "1")]
    por_canal = _bars(html, "Avisos por canal")
    assert por_canal == [("Bumeran", "2"), ("LinkedIn Jobs", "2"), ("Flyer", "1")]            # desc por avisos, empate alfabético, canal histórico incluido
    filtered = _get(canal="Bumeran")
    kf = _kpis(filtered)
    assert kf["Avisos publicados"] == "2" and kf["Registros cargados"] == "2"
    assert kf[f"Este trimestre · {TODAY_PERIODO}"] == k[f"Este trimestre · {TODAY_PERIODO}"]  # única métrica global
    assert _bars(filtered, "Avisos por canal") == [("Bumeran", "2")]
    assert [p for p, _ in _bars(filtered, "Avisos por período")] == ["2026-Q4", "2026-Q3"]


def test_the_summary_is_computed_over_the_whole_filtered_set_not_the_current_page(monkeypatch):
    _seed_n(60, monkeypatch)
    html = _get(page_size="50")
    assert _kpis(html)["Registros cargados"] == "60" and len(_names(html)) == 50


def test_the_summary_reuses_the_hub_primitives_and_adds_no_publicaciones_specific_css():
    _pub(cantidad_avisos=3)
    html = _get()
    for cls in ("activity-kpi-grid", "activity-kpi", "activity-kpi__value", "activity-kpi__label", "activity-kpi__info", "activity-grid-2", "dashboard-subhead",
                "activity-bar-row", "activity-bar", "activity-bar__fill", "activity-bar-label", "activity-bar-count", "chart-tooltip"):
        assert f'"{cls}"' in html or f'"{cls} ' in html or f' {cls}"' in html, cls
    assert "publicaciones-kpi" not in html
    root = Path(__file__).resolve().parents[2] / "webapp" / "static"
    css = "\n".join(re.sub(r"\.module-hero \+ #hc-entrevistas-panel[^{]*\{[^}]*\}", "", (root / n).read_text(encoding="utf-8")) for n in ("human_capital.css", "tools.css", "layout.css"))
    assert not re.search(r"publicaciones-(kpi|resumen|bar)|hc-publicaciones[^{]*activity-", css)


def test_the_filter_bar_comes_first_then_the_summary_then_the_table_because_the_tiles_depend_on_the_filters():
    _pub()
    html = _get()
    assert html.index('class="hc-profile-filters filter-toolbar-card"') < html.index('data-dashboard-key="cchh-publicaciones-resumen"') < html.index('data-dashboard-key="cchh-publicaciones"')
    assert html.index('id="hc-publicaciones-panel"') < html.index('class="hc-profile-filters filter-toolbar-card"') < html.index("activity-kpi-grid")
