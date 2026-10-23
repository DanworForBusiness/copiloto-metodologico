"""test_human_capital_return_context.py — una acción sobre una fila (Editar / Guardar / Cancelar-Volver / Eliminar) NO expulsa al usuario de su contexto de listado.

Mecanismo ÚNICO y transversal (no hidden fields ad-hoc por módulo): el listado agrega `?return_to=<url del listado con filtros/búsqueda/página/tamaño>` a Editar/Eliminar; las páginas de
edición lo leen con el global Jinja `list_return(request, <path>)` (validado por `pagination.list_return_url` sobre `safe_internal_next`: sólo un path interno cuyo PATH es exactamente el
listado) y lo anexan a TODA acción de la página (guardar, eliminar, documentos); las rutas vuelven con `_return_redirect`. Si la página guardada ya no existe, `paginate` la acota.
"""
from __future__ import annotations

import html as _html
import os
import re
from urllib.parse import parse_qs, quote, urlparse

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import (
    auth_service, busqueda_repository, entrevista_repository, human_capital_service, onboarding_repository, pagination, profile_repository,
)

_AUTHORIZED_EMAIL = "hc-return-test@danwor.com"


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(os, "fsync", lambda fd: None)                                        # datasets de ~130 registros reales: sin fsync por archivo
    for mod in (onboarding_repository, entrevista_repository, busqueda_repository, profile_repository):
        monkeypatch.setattr(mod.remote_store, "push_path", lambda path: True)
        monkeypatch.setattr(mod.remote_store, "delete_path", lambda path: True)
        monkeypatch.setattr(mod.remote_store, "enabled", lambda: False)


def _client() -> TestClient:
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": _AUTHORIZED_EMAIL, "name": "R T", "first_name": "R", "family_name": "T"}))
    return c


# ── validación compartida ────────────────────────────────────────────────────

def test_return_url_accepts_only_an_internal_url_whose_path_is_exactly_the_list():
    ok = "/cchh/entrevistas?page=3&page_size=100"
    assert pagination.list_return_url(ok, "/cchh/entrevistas") == ok
    assert pagination.list_return_url("/cchh/entrevistas", "/cchh/entrevistas") == "/cchh/entrevistas"
    for bad in ("", None, "https://evil.example/cchh/entrevistas", "//evil.example/cchh/entrevistas", "/\\evil.example", "/cchh/onboarding?page=2", "/cchh/entrevistas/x/editar",
                "/cchh/entrevistas/../admin", "cchh/entrevistas", "javascript:alert(1)", "/cchh/entrevistasX?page=2"):
        assert pagination.list_return_url(bad, "/cchh/entrevistas") == "", bad
    assert pagination.list_return_url(ok, "/cchh/candidatos") == ""                          # un return_to de OTRA sección tampoco vale


def test_return_context_builds_back_url_and_a_query_suffix_that_roundtrips():
    url = "/cchh/onboarding?estado=pendientes&area=Consultoria&q=gimena+l%C3%B3pez&page=3&page_size=100"
    ctx = pagination.list_return_context({"return_to": url}, "/cchh/onboarding")
    assert ctx["back_url"] == url and ctx["qs"] == f"?return_to={quote(url, safe='')}"
    assert parse_qs(ctx["qs"][1:])["return_to"] == [url]
    assert pagination.list_return_context({}, "/cchh/onboarding") == {"return_to": "", "back_url": "/cchh/onboarding", "qs": "", "from_pipeline": False, "back_label": None}
    assert pagination.list_return_context({"return_to": "//evil"}, "/cchh/onboarding")["back_url"] == "/cchh/onboarding"


def test_paginate_exposes_the_current_list_url_with_the_whole_query_state():
    pg = pagination.paginate(list(range(250)), page=3, page_size=100, base_url="/cchh/onboarding", params={"estado": "pendientes", "q": "x"})
    q = parse_qs(urlparse(pg["urls"]["current"]).query)
    assert q == {"estado": ["pendientes"], "q": ["x"], "page": ["3"], "page_size": ["100"]}


# ── datasets reales por superficie ───────────────────────────────────────────

def _profiles(n: int) -> list[str]:
    return [profile_repository.create({"first_name": f"Nombre{i:03d}", "last_name": "Retorno", "email": f"r{i}@x.com"})["profile_id"] for i in range(n)]


def _seed_candidatos(n):
    return _profiles(n)


def _seed_entrevistas(n):
    pids = _profiles(n)
    for i, pid in enumerate(pids):
        human_capital_service.create_entrevista_manual(pid, {})
    return pids


def _seed_onboarding(n):
    pids = _profiles(n)
    for i, pid in enumerate(pids):
        onboarding_repository.create_if_absent(application_id=f"app{i:04d}", profile_id=pid, busqueda_id="b1", fecha_ingreso=f"2026-01-{1 + i % 28:02d}", area=None)
    return pids


def _edit_hrefs(html: str, section: str) -> list[str]:
    return [_html.unescape(h) for h in re.findall(rf'href="(/cchh/{section}/[^"/]+/editar\?return_to=[^"]*)"', html)]


def _page_of(html: str) -> tuple[str, str]:
    m = re.search(r'data-pager-page="(\d+)" data-pager-pages="(\d+)"', html)
    return (m.group(1), m.group(2)) if m else ("", "")


CASES = [
    ("candidatos", _seed_candidatos, {"q": "Retorno"}),
    ("entrevistas", _seed_entrevistas, {}),
    ("onboarding", _seed_onboarding, {"estado": "pendientes", "q": "retorno"}),
]


@pytest.mark.parametrize("section,seed,filters", CASES, ids=[c[0] for c in CASES])
def test_edit_from_page_3_carries_the_list_context_and_save_returns_to_page_3(section, seed, filters):
    seed(130)
    c = _client()
    list_url = f"/cchh/{section}"
    page3 = c.get(list_url, params={**filters, "page": "3", "page_size": "50"}).text
    assert _page_of(page3) == ("3", "3")
    hrefs = _edit_hrefs(page3, section)
    assert hrefs and all(parse_qs(urlparse(parse_qs(urlparse(h).query)["return_to"][0]).query)["page"] == ["3"] for h in hrefs)
    edit_url = hrefs[0]
    return_to = parse_qs(urlparse(edit_url).query)["return_to"][0]
    rq = parse_qs(urlparse(return_to).query)
    assert urlparse(return_to).path == list_url and rq["page"] == ["3"] and rq["page_size"] == ["50"] and all(rq[k] == [v] for k, v in filters.items())     # filtros + búsqueda + página + tamaño
    page = c.get(edit_url).text                                                               # entrar a Editar
    assert f'href="{_html.escape(return_to)}"' in page or f'href="{return_to}"' in page        # ← Volver / Cancelar = el listado en su contexto
    action = _html.unescape(re.search(r'<form[^>]*method="post"[^>]*action="([^"]+/editar\?return_to=[^"]+)"', page).group(1))
    assert parse_qs(urlparse(action).query)["return_to"] == [return_to]                        # el form de Guardar lo conserva
    post_data = {
        "candidatos": lambda: {"first_name": "Editado", "last_name": "Retorno", "email": "editado@x.com", "expected_revision": re.search(r'name="expected_revision" value="(\d+)"', page).group(1)},
        "entrevistas": lambda: {"observaciones": "ok", "expected_revision": "1"},
        "onboarding": lambda: {"fecha_ingreso": "2026-02-02", "expected_revision": "1"},
    }[section]()
    r = c.post(action, data=post_data)                                                         # Guardar y volver
    assert r.status_code == 303 and r.headers["location"] == return_to
    back = c.get(r.headers["location"]).text                                                   # el listado sigue en la página 3
    assert _page_of(back) == ("3", "3")


@pytest.mark.parametrize("section,seed,filters", CASES, ids=[c[0] for c in CASES])
def test_unsafe_or_foreign_return_to_falls_back_to_the_base_list(section, seed, filters):
    seed(3)
    c = _client()
    list_url = f"/cchh/{section}"
    edit_url = _edit_hrefs(c.get(list_url).text, section)[0].split("?")[0]
    for bad in ("https://evil.example/", "//evil.example", "/cchh/busquedas?page=2", "/cchh/entrevistas/x/editar"):
        if urlparse(bad).path == list_url:
            continue
        page = c.get(edit_url, params={"return_to": bad}).text
        assert f'href="{list_url}"' in page and "evil.example" not in page and "return_to=" not in re.search(r"<form[^>]*method=\"post\"[^>]*>", page).group(0)
        data = {"candidatos": {"first_name": "A", "last_name": "B", "email": "z@z.com", "expected_revision": "1"},
                "entrevistas": {"expected_revision": "1"}, "onboarding": {"fecha_ingreso": "2026-02-02", "expected_revision": "1"}}[section]
        r = c.post(edit_url + "?return_to=" + quote(bad, safe=""), data=data)
        assert r.status_code in (303, 200) and (r.headers.get("location") == list_url or r.status_code == 200)


def test_entrevistas_delete_returns_to_the_same_page_when_it_still_exists():
    _seed_entrevistas(130)
    c = _client()
    page3 = c.get("/cchh/entrevistas", params={"page": "3"}).text
    delete = _html.unescape(re.search(r'action="(/cchh/entrevistas/[^"/]+/eliminar\?return_to=[^"]+)"', page3).group(1))
    return_to = parse_qs(urlparse(delete).query)["return_to"][0]
    r = c.post(delete)
    assert r.status_code == 303 and r.headers["location"] == return_to
    after = c.get(r.headers["location"]).text
    assert _page_of(after) == ("3", "3") and "129 entrevistas" in after                       # sigue en la página 3 (existe todavía)


def test_entrevistas_delete_of_the_last_row_of_the_last_page_clamps_to_the_last_valid_page():
    _seed_entrevistas(101)                                                                     # 3 páginas: 50 + 50 + 1
    c = _client()
    page3 = c.get("/cchh/entrevistas", params={"page": "3"}).text
    assert _page_of(page3) == ("3", "3")
    delete = _html.unescape(re.search(r'action="(/cchh/entrevistas/[^"/]+/eliminar\?return_to=[^"]+)"', page3).group(1))
    r = c.post(delete)
    after = c.get(r.headers["location"]).text                                                  # return_to apunta a page=3, que ya no existe
    assert _page_of(after) == ("2", "2") and "100 entrevistas" in after                         # 100 filas = 2 páginas de 50: se acota a la última válida (nunca tabla vacía)


def test_candidatos_delete_from_the_edit_page_returns_to_the_list_context_and_clamps():
    pids = _seed_candidatos(101)
    c = _client()
    page3 = c.get("/cchh/candidatos", params={"page": "3"}).text
    edit_url = _edit_hrefs(page3, "candidatos")[0]
    page = c.get(edit_url).text
    form = re.search(r'<form method="post" action="(/cchh/candidatos/[^"/]+/eliminar\?return_to=[^"]+)"', page)
    assert form                                                                                # el botón Eliminar de Editar conserva el contexto
    r = c.post(_html.unescape(form.group(1)))
    assert r.status_code == 303 and urlparse(r.headers["location"]).path == "/cchh/candidatos"
    after = c.get(r.headers["location"]).text
    assert _page_of(after) == ("2", "2") and "100 candidatos" in after                         # la página 3 ya no existe: última válida


def test_candidatos_document_forms_and_cancel_links_keep_the_return_context():
    _seed_candidatos(60)
    c = _client()
    edit_url = _edit_hrefs(c.get("/cchh/candidatos", params={"page": "2"}).text, "candidatos")[0]
    return_to = parse_qs(urlparse(edit_url).query)["return_to"][0]
    page = c.get(edit_url).text
    text = _html.unescape(page)
    m = re.search(r'<form class="hc-document-upload-form"[^>]*action="(/cchh/candidatos/[^"/]+/documentos\?return_to=[^"]+)"', text)
    assert m and parse_qs(urlparse(m.group(1)).query)["return_to"] == [return_to]              # el form de subir documento conserva el contexto
    back = re.search(r'<a class="catalog-back" href="([^"]+)"', text).group(1)
    assert back == return_to                                                                   # ← Volver al listado
    assert text.count(f'href="{return_to}"') >= 1
    action = _html.unescape(re.search(r'<form class="hc-profile-form hc-profile-form--plain" method="post" action="([^"]+)"', page).group(1))
    stale = _html.unescape(c.post(action, data={"first_name": "A", "last_name": "B", "email": "stale@x.com", "expected_revision": "999"}).text)
    assert 'data-advisory-kind="concurrent_modification"' in stale and f'href="{return_to}" class="btn-secondary">Volver al listado' in stale     # el estado de conflicto también vuelve al contexto
    from webapp.routers import human_capital as router_mod
    class _Req:                                                                               # tras una operación de documentos se vuelve a Editar CONSERVANDO el contexto
        query_params = {"return_to": return_to}
    loc = router_mod._edit_redirect(_Req(), "pid1").headers["location"]
    assert loc.startswith("/cchh/candidatos/pid1/editar?return_to=") and parse_qs(urlparse(loc).query)["return_to"] == [return_to]


def test_onboarding_edit_keeps_filters_search_page_and_size_through_save_and_cancel():
    _seed_onboarding(130)
    c = _client()
    list_params = {"estado": "pendientes", "q": "retorno", "page": "2", "page_size": "50"}
    page2 = c.get("/cchh/onboarding", params=list_params).text
    edit_url = _edit_hrefs(page2, "onboarding")[0]
    return_to = parse_qs(urlparse(edit_url).query)["return_to"][0]
    assert parse_qs(urlparse(return_to).query) == {"estado": ["pendientes"], "q": ["retorno"], "page": ["2"], "page_size": ["50"]}
    page = c.get(edit_url).text
    assert _html.unescape(page).count(f'href="{return_to}"') >= 1                              # ← Volver a Onboarding
    r = c.post(_html.unescape(re.search(r'action="(/cchh/onboarding/[^"]+/editar\?return_to=[^"]+)"', page).group(1)), data={"fecha_ingreso": "2026-03-03", "expected_revision": "1"})
    assert r.headers["location"] == return_to
    back = c.get(r.headers["location"]).text
    assert _page_of(back) == ("2", "3") and 'value="pendientes" selected' in back and 'value="retorno"' in back and 'data-pager-size="50"' in back
    err = c.post(_html.unescape(re.search(r'action="(/cchh/onboarding/[^"]+/editar\?return_to=[^"]+)"', page).group(1)), data={"fecha_ingreso": "", "expected_revision": "2"})
    assert err.status_code == 200 and "La fecha de ingreso es obligatoria" in err.text and "return_to=" in err.text      # un error de validación conserva el contexto para el reintento


def test_the_shared_global_is_the_only_mechanism_no_per_module_hidden_return_fields():
    from pathlib import Path
    root = Path(__import__("webapp").__file__).parent / "templates"
    for t in [*(root / "human_capital").glob("*.html"), *(root / "partials").glob("human_capital_*.html")]:
        assert 'name="return_to"' not in t.read_text(encoding="utf-8"), t.name
    router = (Path(__import__("webapp").__file__).parent / "routers" / "human_capital.py").read_text(encoding="utf-8")
    assert router.count('templates.env.globals["list_return"]') == 1
