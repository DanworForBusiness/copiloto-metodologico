"""Hotfix CCHH — (1) integridad de la entrevista creada desde `nueva` con origen en una postulación del Pipeline (ligada a la Búsqueda y a la postulación, única por candidato/proceso);
(2) retorno contextual VALIDADO EN SERVIDOR (Pipeline / Evaluación CCHH) para Entrevista y Onboarding; (3) señales de los dos mecanismos de guardado de Onboarding."""
from __future__ import annotations

import re
import threading
from urllib.parse import quote

import pytest

from webapp.services import busqueda_repository as brepo, entrevista_repository as erepo, human_capital_service as svc, onboarding_repository as orepo, profile_repository
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, env, remote  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import session_client, operations_users  # noqa: F401
from tests.webapp.test_pipeline_interview_shortcut import _card, _manual  # noqa: F401
from tests.webapp.test_pipeline_return_context import _back, _candidate, _card_link, _ingreso, _leader
from tests.webapp.test_scoring_evaluation import _ready, _url as eval_url

STRANGER_BID = "11111111-2222-4333-8444-555555555555"


@pytest.fixture()
def c():
    return session_client(CCHH_EMAIL)


def _drop_auto_interviews():
    for e in erepo.list():
        svc.delete_entrevista(e["entrevista_id"])


def _nueva_url(pid, bid, ctx=None) -> str:
    q = f"/cchh/entrevistas/nueva?profile_id={pid}&busqueda_id={bid}"
    return q + (f"&return_to={quote(ctx, safe='')}" if ctx else "")


def _post_nueva(c, pid, bid, ctx=None, **over):
    url = "/cchh/entrevistas/nueva" + (f"?return_to={quote(ctx, safe='')}" if ctx else "")
    return c.post(url, data={"profile_id": pid, "busqueda_id": bid, "fecha_entrevista": "2026-10-20", "puesto_evaluado": "SA", "area": "SAP", **over})


# ═══════════════════ 1. integridad de la entrevista ═══════════════════
def test_new_from_a_pipeline_application_is_bound_to_the_busqueda_and_the_application_and_the_pipeline_finds_it(c):
    bid = _manual()
    pid, app_id = _leader(bid)
    _drop_auto_interviews()
    ctx = f"/cchh/busquedas/{bid}"
    page = c.get(_nueva_url(pid, bid, ctx)).text
    assert f'<input type="hidden" name="busqueda_id" value="{bid}">' in page and f'<input type="hidden" name="profile_id" value="{pid}">' in page
    assert re.search(r'<select id="ent-profile" name="profile_id" required disabled>', page)                         # el candidato viene fijo del Pipeline
    assert "Registro administrativo de la entrevista de este candidato en la Búsqueda de origen." in page and "todavía no está en ninguna Búsqueda" not in page
    r = _post_nueva(c, pid, bid, ctx, application_id="forjada-por-el-navegador")
    assert r.status_code == 303
    rows = erepo.list()
    assert len(rows) == 1 and rows[0]["busqueda_id"] == bid and rows[0]["profile_id"] == pid and rows[0]["application_id"] == app_id    # la postulación la resuelve el servidor
    assert erepo.find_by_profile_and_busqueda(pid, bid)["entrevista_id"] == rows[0]["entrevista_id"]
    link = _card_link(c.get(ctx).text, "Carla Test", "busqueda-cand__interview")                                        # el Pipeline ahora la encuentra: va a su edición, no a un alta
    assert link.startswith(f"/cchh/entrevistas/{rows[0]['entrevista_id']}/editar") and "nueva" not in link
    assert r.headers["location"] == link.replace("&amp;", "&").replace("foco=tecnica&", "")                              # y el alta redirige a esa misma edición, con el contexto de regreso (este alta se envió sin foco)


def test_double_submit_and_concurrent_submits_create_exactly_one_interview_and_never_overwrite_it(c):
    bid = _manual()
    pid, _ = _leader(bid)
    _drop_auto_interviews()
    first = _post_nueva(c, pid, bid, observaciones="primera")
    first_id = erepo.list()[0]["entrevista_id"]
    again = _post_nueva(c, pid, bid, observaciones="segunda (no debe pisar)")
    assert again.status_code == 303 and again.headers["location"].split("?")[0] == first.headers["location"].split("?")[0]
    assert len(erepo.list()) == 1 and erepo.get(first_id)["observaciones"] == "primera" and erepo.get(first_id)["revision"] == 1
    _drop_auto_interviews()
    barrier, results = threading.Barrier(4), []

    def _go():
        cl = session_client(CCHH_EMAIL)
        barrier.wait()
        results.append(_post_nueva(cl, pid, bid).status_code)
    threads = [threading.Thread(target=_go) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results == [303] * 4 and len(erepo.list()) == 1                                                                # misma regla bajo concurrencia: un solo registro


def test_opening_new_when_the_interview_already_exists_goes_to_it_instead_of_offering_a_duplicate(c):
    bid = _manual()
    pid, _ = _leader(bid)                                                                                                # la automática ya existe
    existing = erepo.find_by_profile_and_busqueda(pid, bid)
    ctx = f"/cchh/busquedas/{bid}"
    r = c.get(_nueva_url(pid, bid, ctx))
    assert r.status_code == 303 and r.headers["location"] == f"/cchh/entrevistas/{existing['entrevista_id']}/editar?return_to={quote(ctx, safe='')}"
    assert len(erepo.list()) == 1


def test_the_binding_is_verified_in_the_server_and_a_forged_one_creates_nothing(c):
    bid = _manual()
    pid, _ = _leader(bid)
    _drop_auto_interviews()
    other = _manual(nombre_puesto="Otra búsqueda")
    for forged in (other, STRANGER_BID, "../../etc"):                                                                   # búsqueda donde NO participa / inexistente / id malformado
        r = _post_nueva(c, pid, forged)
        assert r.status_code == 200 and "no participa de la Búsqueda de origen" in r.text and erepo.list() == []
        assert f'name="busqueda_id" value="{forged}"' in r.text or forged == "../../etc"
    ghost = _post_nueva(c, "no-existe", bid)
    assert ghost.status_code == 200 and erepo.list() == []


def test_new_without_pipeline_origin_still_creates_a_manual_interview(c):
    pid = profile_repository.create({"first_name": "Solo", "last_name": "Banco", "email": "solo@danwor.com"})["profile_id"]
    assert 'name="busqueda_id"' not in c.get(f"/cchh/entrevistas/nueva?profile_id={pid}").text
    r = c.post("/cchh/entrevistas/nueva", data={"profile_id": pid, "fecha_entrevista": "2026-10-20", "puesto_evaluado": "SA", "area": "SAP"})
    assert r.status_code == 303 and "return_to" not in r.headers["location"]
    e = erepo.list()[0]
    assert e["busqueda_id"] is None and e["application_id"] is None


# ═══════════════════ 2. retorno contextual: Evaluación CCHH → Registro → Volver ═══════════════════
def test_evaluation_to_record_to_back_returns_to_that_evaluation_before_and_after_saving(c):
    bid, _grid = _ready(c)
    pid, app_id = _candidate(bid, "Mateo")
    svc.move_application_stage(bid, app_id, "Entrevista CCHH", None, actor=CCHH_EMAIL)
    ev = eval_url(bid, app_id)
    href = re.search(r'<a class="btn-secondary" href="([^"]+)">Registro de entrevista</a>', c.get(ev).text).group(1).replace("&amp;", "&")
    assert href.endswith(f"return_to={quote(ev, safe='')}")
    page = c.get(href).text
    assert _back(page) == (ev, "Evaluación CCHH")
    entrevista = erepo.find_by_profile_and_busqueda(pid, bid)
    save = c.post(href, data={"fecha_entrevista": "2026-10-21", "puesto_evaluado": "SA", "area": "SAP", "expected_revision": str(entrevista["revision"])})
    assert save.status_code == 303 and save.headers["location"] == ev                                                     # tras guardar vuelve a la Evaluación CCHH
    assert c.get(ev).status_code == 200                                                                                  # y esa pantalla conserva su propio «Volver al ranking»
    assert "Volver al ranking de Scoring" in c.get(ev).text


def test_evaluation_to_new_record_keeps_the_evaluation_context_and_binds_the_record(c):
    bid, _grid = _ready(c)
    pid, app_id = _candidate(bid, "Mateo")
    svc.move_application_stage(bid, app_id, "Entrevista CCHH", None, actor=CCHH_EMAIL)
    _drop_auto_interviews()
    ev = eval_url(bid, app_id)
    href = re.search(r'<a class="btn-secondary" href="([^"]+)">Registro de entrevista</a>', c.get(ev).text).group(1).replace("&amp;", "&")
    assert href.startswith("/cchh/entrevistas/nueva?") and _back(c.get(href).text) == (ev, "Evaluación CCHH")
    r = _post_nueva(c, pid, bid, ev)
    assert r.status_code == 303 and r.headers["location"].endswith(f"/editar?return_to={quote(ev, safe='')}")
    assert erepo.list()[0]["application_id"] == app_id


# ═══════════════════ 3. el contexto se valida en servidor contra el registro ═══════════════════
def _edit_page(c, entrevista_id, ctx):
    return c.get(f"/cchh/entrevistas/{entrevista_id}/editar?return_to={quote(ctx, safe='')}").text


def test_interview_context_must_belong_to_the_same_busqueda_and_application(c):
    bid = _manual()
    pid, app_id = _leader(bid)
    e = erepo.find_by_profile_and_busqueda(pid, bid)
    other = _manual(nombre_puesto="Otra búsqueda")
    _, other_app = _candidate(other, "Ajena")
    default = ("/cchh/entrevistas", "Entrevistas")
    assert _back(_edit_page(c, e["entrevista_id"], f"/cchh/busquedas/{bid}")) == (f"/cchh/busquedas/{bid}", "Pipeline de la búsqueda")        # válido
    assert _back(_edit_page(c, e["entrevista_id"], f"/cchh/busquedas/{other}")) == default                              # búsqueda existente pero OTRA
    assert _back(_edit_page(c, e["entrevista_id"], f"/cchh/busquedas/{STRANGER_BID}")) == default                       # búsqueda inexistente
    assert _back(_edit_page(c, e["entrevista_id"], eval_url(bid, other_app))) == default                                # evaluación de otro candidato/búsqueda
    assert _back(_edit_page(c, e["entrevista_id"], eval_url(bid, STRANGER_BID))) == default                             # postulación inexistente
    assert _back(_edit_page(c, e["entrevista_id"], eval_url(bid, app_id)))[1] == "Evaluación CCHH"                      # la suya sí
    for bad_ctx in (f"/cchh/busquedas/{other}", f"/cchh/busquedas/{STRANGER_BID}", eval_url(bid, other_app)):
        r = c.post(f"/cchh/entrevistas/{e['entrevista_id']}/editar?return_to={quote(bad_ctx, safe='')}",
                   data={"fecha_entrevista": "2026-10-22", "puesto_evaluado": "SA", "area": "SAP", "expected_revision": str(erepo.get(e["entrevista_id"])["revision"])})
        assert r.status_code == 303 and r.headers["location"] == "/cchh/entrevistas"                                    # la redirección tras guardar también cae al destino independiente


def test_context_is_dropped_when_the_application_left_the_pipeline_or_the_record_has_no_busqueda(c):
    bid = _manual()
    pid, app_id = _leader(bid)
    e = erepo.find_by_profile_and_busqueda(pid, bid)
    svc.remove_application(bid, app_id, actor=CCHH_EMAIL)                                                                # la entrevista queda (no se borra) pero la postulación ya no está
    assert erepo.get(e["entrevista_id"]) is not None
    assert _back(_edit_page(c, e["entrevista_id"], f"/cchh/busquedas/{bid}")) == ("/cchh/entrevistas", "Entrevistas")
    manual = svc.create_entrevista_manual(profile_repository.create({"first_name": "M", "last_name": "Anual", "email": "m@danwor.com"})["profile_id"], {})
    assert _back(_edit_page(c, manual["entrevista_id"], f"/cchh/busquedas/{bid}")) == ("/cchh/entrevistas", "Entrevistas")      # sin búsqueda: no hay Pipeline de origen


def test_onboarding_context_is_validated_against_its_busqueda_and_application(c):
    bid = _manual()
    app_id = _ingreso(bid)
    other = _manual(nombre_puesto="Otra")
    _, other_app = _candidate(other, "Ajena")
    page = lambda ctx: c.get(f"/cchh/onboarding/{app_id}/editar?return_to={quote(ctx, safe='')}").text      # noqa: E731
    assert _back(page(f"/cchh/busquedas/{bid}")) == (f"/cchh/busquedas/{bid}", "Pipeline de la búsqueda")
    for bad in (f"/cchh/busquedas/{other}", f"/cchh/busquedas/{STRANGER_BID}", eval_url(bid, app_id), eval_url(other, other_app)):
        assert _back(page(bad)) == ("/cchh/onboarding", "Onboarding"), bad                                              # otra búsqueda / inexistente / la Evaluación no es un origen del Onboarding de otra búsqueda
    o = orepo.get(app_id)
    r = c.post(f"/cchh/onboarding/{app_id}/editar?return_to={quote(f'/cchh/busquedas/{other}', safe='')}", data={"fecha_ingreso": "2026-11-02", "area": "", "expected_revision": str(o["revision"])})
    assert r.status_code == 303 and r.headers["location"] == "/cchh/onboarding"


def test_a_context_is_never_accepted_by_screens_that_do_not_open_from_the_pipeline(c):
    pid = profile_repository.create({"first_name": "A", "last_name": "B", "email": "ab@danwor.com"})["profile_id"]
    bid = _manual()
    r = c.get(f"/cchh/candidatos/{pid}/editar?return_to={quote(f'/cchh/busquedas/{bid}', safe='')}")
    assert r.status_code == 200 and f'href="/cchh/busquedas/{bid}"' not in r.text.split('class="catalog-back"')[1].split(">")[0]


# ═══════════════════ 4. Onboarding: dos mecanismos de guardado, dos señales ═══════════════════
def test_onboarding_marks_the_two_save_mechanisms_without_changing_either(c):
    bid = _manual()
    app_id = _ingreso(bid)
    page = c.get(f"/cchh/onboarding/{app_id}/editar").text
    assert 'data-onb-dirty hidden>Cambios sin guardar en «Datos del ingreso»</span>' in page                            # indicador oculto hasta que haya cambios en el formulario de datos
    assert "Guarda los datos del ingreso (fecha, oferta y área). El checklist no lo necesita: se guarda al tildar cada ítem." in page
    assert 'data-onb-autosave-hint>Se guarda al instante al tildar cada ítem.</p>' in page
    assert "Tildar un ítem no guarda los cambios pendientes de «Datos del ingreso»" in page
    assert 'form="onb-form" class="btn-primary">Guardar cambios' in page and 'id="onb-form"' in page and 'action="/cchh/onboarding/' in page    # el botón y el endpoint de datos, intactos
    before = orepo.get(app_id)
    frag = c.post(f"/cchh/onboarding/{app_id}/checklist/dni", data={"checked": "1"}, headers={"HX-Request": "true"}).text
    after = orepo.get(app_id)
    assert "data-onb-autosave-hint" in frag and after["checklist"]["dni"] is True                                       # el ítem se guarda al instante…
    assert after["fecha_ingreso"] == before["fecha_ingreso"] and after["oferta_cerrada"] == before["oferta_cerrada"] and after["area"] == before["area"]   # …y NO toca los datos generales

@pytest.fixture(autouse=True)
def _evaluation_is_app_owner_only(monkeypatch):
    """La pestaña Evaluación está en desarrollo y solo la usa el dueño de la app (APP_OWNER): estos tests ejercitan SU funcionamiento, así que el usuario CCHH de prueba es el dueño."""
    from webapp import access
    monkeypatch.setattr(access, "APP_OWNER", CCHH_EMAIL)

