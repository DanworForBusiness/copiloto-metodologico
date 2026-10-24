"""Contexto explícito de navegación: Entrevista (registro) y Onboarding abiertos desde las tarjetas del Pipeline de una búsqueda vuelven a ESE Pipeline; desde sus secciones independientes o por enlace directo conservan el
destino independiente. Mismo mecanismo `return_to` que los listados (validado, nunca una URL arbitraria ni `history.back()`); no altera datos de entrevista ni onboarding."""
from __future__ import annotations

import re
from urllib.parse import quote

import pytest

from webapp.services import entrevista_repository as erepo, human_capital_service as svc, onboarding_repository as orepo, pagination, profile_repository
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, env, remote  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import session_client, operations_users  # noqa: F401
from tests.webapp.test_pipeline_interview_shortcut import _card, _manual  # noqa: F401

OTHER_BID = "11111111-2222-4333-8444-555555555555"


@pytest.fixture()
def c():
    return session_client(CCHH_EMAIL)


def _candidate(bid, first="Carla") -> tuple[str, str]:
    pid = profile_repository.create({"first_name": first, "last_name": "Test", "email": f"{first.lower()}@danwor.com"})["profile_id"]
    return pid, svc.add_candidate_to_busqueda(bid, pid, actor=CCHH_EMAIL)["candidatos"][-1]["application_id"]


def _card_link(html: str, name: str, css: str) -> str:
    m = re.search(r'<a href="([^"]+)" class="table-action-btn chart-tooltip-anchor ' + css + '"', _card(html, name))
    assert m, f"sin acceso {css} en la tarjeta de {name}"
    return m.group(1).replace("&amp;", "&").replace("&#38;", "&")


def _back(html: str) -> tuple[str, str]:
    m = re.search(r'<a class="catalog-back" href="([^"]+)">← Volver a ([^<]+)</a>', html)
    assert m
    return m.group(1).replace("&amp;", "&"), m.group(2)


def _leader(bid, first="Carla") -> tuple[str, str]:
    pid, app_id = _candidate(bid, first)
    svc.move_application_stage(bid, app_id, "Entrevista Líder", None, actor=CCHH_EMAIL)
    return pid, app_id


def _ingreso(bid, first="Elena") -> str:
    _, app_id = _candidate(bid, first)
    svc.move_application_stage(bid, app_id, "Finalizado", "Ingreso", actor=CCHH_EMAIL)
    return app_id


# ═══════════════════ origen 1: Pipeline de la búsqueda ═══════════════════
def test_interview_opened_from_the_pipeline_returns_to_that_pipeline_before_and_after_saving(c):
    bid = _manual()
    pid, _ = _leader(bid)
    for auto in erepo.list():                                                                                       # al pasar a Entrevista Líder se crea una automática: se la quita para ejercitar también el alta
        svc.delete_entrevista(auto["entrevista_id"])
    ctx = f"/cchh/busquedas/{bid}"
    link = _card_link(c.get(ctx).text, "Carla Test", "busqueda-cand__interview")
    assert link.startswith("/cchh/entrevistas/nueva?") and f"return_to={quote(ctx, safe='')}" in link          # alta (todavía no existe la entrevista)
    page = c.get(link).text
    assert _back(page) == (ctx, "Pipeline de la búsqueda")
    assert f'action="/cchh/entrevistas/nueva?return_to={quote(ctx, safe="")}"' in page and f'<a href="{ctx}" class="btn-secondary">Cancelar</a>' in page
    r = c.post(f"/cchh/entrevistas/nueva?return_to={quote(ctx, safe='')}", data={"profile_id": pid, "busqueda_id": bid, "fecha_entrevista": "2026-10-20", "puesto_evaluado": "SA", "area": "SAP"})
    assert r.status_code == 303 and r.headers["location"].endswith(f"/editar?return_to={quote(ctx, safe='')}")      # tras crear sigue editando CON el contexto
    edit_url = r.headers["location"]
    assert _back(c.get(edit_url).text) == (ctx, "Pipeline de la búsqueda")
    entrevista = erepo.list()[0]
    save = c.post(edit_url, data={"fecha_entrevista": "2026-10-21", "puesto_evaluado": "SA", "area": "SAP", "expected_revision": str(entrevista["revision"])})
    assert save.status_code == 303 and save.headers["location"] == ctx                                             # redirección tras guardar = Pipeline
    assert erepo.get(entrevista["entrevista_id"])["fecha_entrevista"] == "2026-10-21"                              # el dato sí se guardó


def test_interview_not_concretada_and_validation_error_keep_the_pipeline_context(c):
    bid = _manual()
    _leader(bid)
    ctx = f"/cchh/busquedas/{bid}"
    pid = erepo and svc.get_busqueda(bid)["candidatos"][0]["profile_id"]
    e = erepo.find_by_profile_and_busqueda(pid, bid)                                                                  # la entrevista automática de Entrevista Líder (ligada a la búsqueda)
    url = f"/cchh/entrevistas/{e['entrevista_id']}/editar?return_to={quote(ctx, safe='')}"
    bad = c.post(url, data={"fecha_entrevista": "2026-10-20", "puesto_evaluado": "SA", "area": "Inexistente", "expected_revision": str(e["revision"])})
    assert bad.status_code == 200 and _back(bad.text) == (ctx, "Pipeline de la búsqueda") and f"/editar?return_to={quote(ctx, safe='')}" in bad.text        # el re-render conserva el contexto
    nc = c.post(f"/cchh/entrevistas/{e['entrevista_id']}/no-concretada?return_to={quote(ctx, safe='')}",
                data={"fecha_entrevista": "2026-10-20", "puesto_evaluado": "SA", "area": "SAP", "expected_revision": str(e["revision"])})
    assert nc.status_code == 303 and nc.headers["location"] == ctx


def test_onboarding_opened_from_the_pipeline_returns_to_that_pipeline_before_and_after_saving(c):
    bid = _manual()
    app_id = _ingreso(bid)
    ctx = f"/cchh/busquedas/{bid}"
    link = _card_link(c.get(ctx).text, "Elena Test", "busqueda-cand__onboarding")
    assert link == f"/cchh/onboarding/{app_id}/editar?return_to={quote(ctx, safe='')}"
    page = c.get(link).text
    assert _back(page) == (ctx, "Pipeline de la búsqueda") and f'action="/cchh/onboarding/{app_id}/editar?return_to={quote(ctx, safe="")}"' in page
    before = orepo.get(app_id)
    bad = c.post(link, data={"fecha_ingreso": "no-es-fecha", "area": "", "expected_revision": str(before["revision"])})
    assert bad.status_code == 200 and _back(bad.text) == (ctx, "Pipeline de la búsqueda")                           # error de validación: mismo contexto
    ok = c.post(link, data={"fecha_ingreso": "2026-11-02", "area": "", "expected_revision": str(before["revision"])})
    assert ok.status_code == 303 and ok.headers["location"] == ctx and orepo.get(app_id)["fecha_ingreso"] == "2026-11-02"


# ═══════════════════ origen 2: secciones independientes (comportamiento actual) ═══════════════════
def test_standalone_sections_keep_their_current_back_destination(c):
    bid = _manual()
    pid, _ = _leader(bid)
    app_id = _ingreso(bid)
    e = erepo.find_by_profile_and_busqueda(pid, bid)                                                                  # la entrevista automática de Entrevista Líder (ligada a la búsqueda)
    listed = "/cchh/entrevistas?page=2&page_size=25"
    ctx_qs = f"?return_to={quote(listed, safe='')}"
    assert _back(c.get(f"/cchh/entrevistas/{e['entrevista_id']}/editar{ctx_qs}").text) == (listed, "Entrevistas")      # desde el listado (con su estado)
    assert _back(c.get(f"/cchh/onboarding/{app_id}/editar").text) == ("/cchh/onboarding", "Onboarding")
    r = c.post(f"/cchh/entrevistas/{e['entrevista_id']}/editar{ctx_qs}", data={"fecha_entrevista": "2026-10-22", "puesto_evaluado": "SA", "area": "SAP", "expected_revision": str(e["revision"])})
    assert r.status_code == 303 and r.headers["location"] == listed
    o = orepo.get(app_id)
    r2 = c.post(f"/cchh/onboarding/{app_id}/editar", data={"fecha_ingreso": "2026-11-03", "area": "", "expected_revision": str(o["revision"])})
    assert r2.status_code == 303 and r2.headers["location"] == "/cchh/onboarding"


# ═══════════════════ origen 3: enlace directo sin contexto ═══════════════════
def test_direct_links_without_context_use_the_default_standalone_destination(c):
    bid = _manual()
    pid, _ = _leader(bid)
    app_id = _ingreso(bid)
    assert _back(c.get(f"/cchh/entrevistas/nueva?profile_id={pid}").text) == ("/cchh/entrevistas", "Entrevistas")
    assert _back(c.get(f"/cchh/onboarding/{app_id}/editar").text)[1] == "Onboarding"
    r = c.post("/cchh/entrevistas/nueva", data={"profile_id": pid, "fecha_entrevista": "2026-10-20", "puesto_evaluado": "SA", "area": "SAP"})
    assert r.status_code == 303 and r.headers["location"].endswith("/editar") and "return_to" not in r.headers["location"]


# ═══════════════════ contextos inválidos ═══════════════════
INVALID = [
    "https://evil.example/cchh/busquedas/" + OTHER_BID,        # absoluta
    "//evil.example/cchh/busquedas/" + OTHER_BID,              # protocol-relative
    "javascript:alert(1)",
    "/cchh/busquedas/" + OTHER_BID + "/editar",                # otra pantalla de búsquedas
    "/cchh/busquedas/nueva",                                   # ruta reservada, no un uuid
    "/cchh/busquedas/" + OTHER_BID + "?vista=evaluacion",      # el contexto no admite query
    "/cchh/busquedas/" + OTHER_BID + "#x",
    "/cchh/busquedas/../../admin",
    "/cchh/candidatos",                                        # otro listado
    "busqueda_pipeline",                                       # no es una URL
    "\\\\evil.example",
    "",
]


@pytest.mark.parametrize("bad", INVALID)
def test_invalid_contexts_fall_back_to_the_default_destination_and_never_redirect_out(c, bad):
    bid = _manual()
    _leader(bid)
    app_id = _ingreso(bid)
    pid = svc.get_busqueda(bid)["candidatos"][0]["profile_id"]
    e = erepo.find_by_profile_and_busqueda(pid, bid)                                                                  # la entrevista automática de Entrevista Líder (ligada a la búsqueda)
    q = f"?return_to={quote(bad, safe='')}"
    assert _back(c.get(f"/cchh/entrevistas/{e['entrevista_id']}/editar{q}").text) == ("/cchh/entrevistas", "Entrevistas")
    assert _back(c.get(f"/cchh/onboarding/{app_id}/editar{q}").text) == ("/cchh/onboarding", "Onboarding")
    r = c.post(f"/cchh/entrevistas/{e['entrevista_id']}/editar{q}", data={"fecha_entrevista": "2026-10-22", "puesto_evaluado": "SA", "area": "SAP", "expected_revision": str(e["revision"])})
    assert r.status_code == 303 and r.headers["location"] == "/cchh/entrevistas"
    o = orepo.get(app_id)
    r2 = c.post(f"/cchh/onboarding/{app_id}/editar{q}", data={"fecha_ingreso": "2026-11-03", "area": "", "expected_revision": str(o["revision"])})
    assert r2.status_code == 303 and r2.headers["location"] == "/cchh/onboarding"


def test_the_pipeline_context_is_opt_in_so_other_lists_do_not_accept_it():
    ctx = f"/cchh/busquedas/{OTHER_BID}"
    assert pagination.list_return_url(ctx, "/cchh/candidatos") == "" and pagination.list_return_url(ctx, "/cchh/entrevistas") == ""
    assert pagination.list_return_url(ctx, "/cchh/entrevistas", allow_process=True) == ctx
    assert pagination.pipeline_return_url("no-es-uuid") == "" and pagination.pipeline_return_url(OTHER_BID) == ctx
    assert pagination.with_return_to("/x?a=1", ctx) == f"/x?a=1&return_to={quote(ctx, safe='')}" and pagination.with_return_to("/x", "") == "/x"

@pytest.fixture(autouse=True)
def _evaluation_is_app_owner_only(monkeypatch):
    """La pestaña Evaluación está en desarrollo y solo la usa el dueño de la app (APP_OWNER): estos tests ejercitan SU funcionamiento, así que el usuario CCHH de prueba es el dueño."""
    from webapp import access
    monkeypatch.setattr(access, "APP_OWNER", CCHH_EMAIL)

