"""Scoring CCHH — incremento 2: evaluación por candidato/evaluador y ranking sobre la grilla versionada (incremento 1).

Identidad real (el evaluador es la sesión), versión de grilla en cada evaluación, cobertura explícita, excluyentes, tiers y dispersión; navegar no crea registros; el registro administrativo de entrevista queda separado.
Fixtures de I1 (persistencia aislada)."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import (auth_service, busqueda_repository as brepo, entrevista_repository as erepo, human_capital_service as svc, profile_repository,
                             scoring_engine as se, scoring_evaluation_repository as erepo_s, scoring_grid_repository as grepo)
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, env, remote  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import session_client, operations_users  # noqa: F401
from tests.webapp.test_pipeline_interview_shortcut import _manual, _card  # noqa: F401
from tests.webapp.test_scoring_jd_panel import VALID, _grid_post, _rows

OTHER = "cchh.segunda@danwor.com"


@pytest.fixture(autouse=True)
def two_evaluators(monkeypatch):
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", f"{CCHH_EMAIL},{OTHER}")


@pytest.fixture()
def c():
    return session_client(CCHH_EMAIL)


def _candidate(bid, first="Mateo", last="Test") -> str:
    pid = profile_repository.create({"first_name": first, "last_name": last, "email": f"{first.lower()}@danwor.com"})["profile_id"]
    return svc.add_candidate_to_busqueda(bid, pid, actor=CCHH_EMAIL)["candidatos"][-1]["application_id"]


def _ready(c, bid=None):
    bid = bid or _manual()
    assert _grid_post(c, bid, 0, _rows(*VALID)).status_code == 303
    return bid, grepo.get(bid)


def _ids(grid) -> dict:
    return {x["bloque"]: x["criterio_id"] for x in grid["criterios"]}


def _form(grid, notas=(5, 5, 5), cumple="si", revision=0, **extra) -> dict:
    ids = _ids(grid)
    return {"expected_revision": str(revision), f"cumple_{ids['excluyente']}": cumple, f"nota_{ids['tecnico']}": str(notas[0]), f"nota_{ids['deseable']}": str(notas[1]),
            f"nota_{ids['competencia']}": str(notas[2]), **extra}


def _url(bid, app_id) -> str:
    return f"/cchh/busquedas/{bid}/candidatos/{app_id}/evaluacion-cchh"


def _all(bid):
    return erepo_s.list_for_busqueda(bid)


def _ranking(c, bid) -> str:
    return c.get(f"/cchh/busquedas/{bid}?vista=scoring&paso=ranking").text


# ═══════════════════ motor ═══════════════════
def test_ranking_order_positions_and_form_parsing_are_pure_engine_rules():
    items = [{"nombre": "Zoe", "tier": "B", "puntaje": 70.0}, {"nombre": "Ana", "tier": "A", "puntaje": 90.0}, {"nombre": "Eva", "tier": "No aplica", "puntaje": 99.0},
             {"nombre": "Bea", "tier": None, "puntaje": None}, {"nombre": "Lia", "tier": "A", "puntaje": 90.0}, {"nombre": "Cris", "tier": "C", "puntaje": 10.0}]
    out = se.ordenar_ranking(items)
    assert [(i["nombre"], i["posicion"]) for i in out] == [("Ana", 1), ("Lia", 2), ("Zoe", 3), ("Cris", 4), ("Eva", None), ("Bea", None)]       # No aplica y sin evaluar no puntúan en el ranking
    cr = [{"criterio_id": "x", "bloque": "excluyente"}, {"criterio_id": "t", "bloque": "tecnico"}]
    det = se.parsear_formulario_evaluacion(cr, {"cumple_x": "SI", "nota_t": "4", "comentario_t": "  ok  "})
    assert det == {"x": {"nota": None, "cumple": True, "comentario": None}, "t": {"nota": 4, "cumple": None, "comentario": "ok"}}
    assert se.parsear_formulario_evaluacion(cr, {"nota_t": "abc", "cumple_x": "quizás"})["t"]["nota"] is None


# ═══════════════════ repositorio de evaluaciones ═══════════════════
def _record(app="a1", evaluator="ana@danwor.com", bid="b1", nota=5, **kw):
    grid = {"grid_id": "g1", "busqueda_id": bid, "version": 1, "criterios": [{"criterio_id": "t1", "fase": "cchh", "bloque": "tecnico", "nombre": "SQL", "peso": 100}]}
    return se.construir_evaluacion(grid, application_id=app, fase="cchh", recorded_by=evaluator, detalles={"t1": {"nota": nota}}, ahora=kw.pop("ahora", "2026-10-23T10:00:00+00:00"), **kw)


def test_the_evaluation_repository_upserts_idempotently_by_application_phase_and_evaluator():
    e1, changed = erepo_s.save(_record(), 0)
    assert changed and e1["revision"] == 1 and e1["evaluation_id"] == erepo_s.evaluation_id("a1:cchh:ana@danwor.com") and e1["created_at"] == e1["updated_at"]
    same, changed2 = erepo_s.save(_record(ahora="2026-10-23T11:00:00+00:00"), 1)
    assert not changed2 and same == e1 and len(_all("b1")) == 1                                         # el mismo contenido no escribe ni cambia la revisión
    e2, changed3 = erepo_s.save(_record(nota=3, ahora="2026-10-23T12:00:00+00:00"), 1)
    assert changed3 and e2["revision"] == 2 and e2["created_at"] == e1["created_at"] and e2["updated_at"] == "2026-10-23T12:00:00+00:00" and e2["puntaje"] == 50.0
    other, _ = erepo_s.save(_record(evaluator="beto@danwor.com"), 0)                                   # otro evaluador = otro documento
    assert other["evaluation_id"] != e2["evaluation_id"] and len(_all("b1")) == 2
    assert erepo_s.get("b1", "a1:cchh:ana@danwor.com")["revision"] == 2 and erepo_s.get("b1", "a9:cchh:ana@danwor.com") is None
    with pytest.raises(erepo_s.EvaluationConcurrentModificationError):
        erepo_s.save(_record(nota=1), 1)                                                                # revisión vieja
    with pytest.raises(erepo_s.EvaluationConcurrentModificationError):
        erepo_s.save(_record(evaluator="nuevo@danwor.com"), 5)
    for bad in ("../x", "a/b", ""):
        with pytest.raises(ValueError):
            erepo_s.list_for_busqueda(bad)
    assert erepo_s.list_for_busqueda("b-sin-nada") == []


def test_corrupt_evaluation_files_are_skipped_and_remote_failures_compensate(remote):
    erepo_s.save(_record(), 0)
    folder = config.BASE_DIR / "human-capital" / "scoring-evaluations" / "b1"
    (folder / "roto.json").write_text("{no es json", encoding="utf-8")
    (folder / "lista.json").write_text("[]", encoding="utf-8")
    assert len(_all("b1")) == 1
    remote.enabled = True
    remote.push_script = [False, False]
    with pytest.raises(erepo_s.GridRemoteOutcomeUnknownError):
        erepo_s.save(_record(evaluator="nuevo@danwor.com"), 0)
    assert erepo_s.get("b1", "a1:cchh:nuevo@danwor.com") is None and len(_all("b1")) == 1            # el alta no confirmada no deja archivo


# ═══════════════════ navegar no crea nada; pantalla de evaluación ═══════════════════
def test_opening_the_evaluation_creates_nothing_and_offers_the_administrative_record_as_secondary_access(c):
    bid, grid = _ready(c)
    app_id = _candidate(bid, "Mateo", "Biasotto")
    interviews = len(erepo.list())
    page = c.get(_url(bid, app_id))
    assert page.status_code == 200 and "<h1>Evaluación CCHH</h1>" in page.text and "Mateo Biasotto · Solution Architect" in page.text and "Grilla v1" in page.text
    assert not (config.BASE_DIR / "human-capital" / "scoring-evaluations").exists() and len(erepo.list()) == interviews          # leer no crea evaluaciones ni entrevistas
    pid = brepo.get(bid)["candidatos"][0]["profile_id"]
    assert f'<a class="btn-secondary" href="/cchh/entrevistas/nueva?profile_id={pid}&amp;busqueda_id={bid}">Registro de entrevista</a>' in page.text
    for crit in grid["criterios"]:
        assert crit["nombre"] in page.text
    assert "SAP Datasphere" in page.text and "¿Cuántos proyectos?" in page.text and "Sin experiencia" in page.text and "Experiencia sólida" in page.text and "No cumple" in page.text
    assert "no forma parte de este resultado" in page.text and "consolidado" not in page.text.lower().replace("no es un consolidado", "")
    svc.move_application_stage(bid, app_id, "Entrevista CCHH", None, actor=CCHH_EMAIL)                                           # al existir el registro administrativo, el acceso lleva a SU edición
    existing = erepo.find_by_profile_and_busqueda(pid, bid)
    assert f'href="/cchh/entrevistas/{existing["entrevista_id"]}/editar">Registro de entrevista</a>' in c.get(_url(bid, app_id)).text


def test_without_a_ready_grid_the_evaluation_explains_it_and_links_to_the_grid(c):
    bid = _manual()
    app_id = _candidate(bid)
    page = c.get(_url(bid, app_id)).text
    assert "Grilla no disponible" in page and f"/cchh/busquedas/{bid}?vista=scoring&amp;paso=grilla" in page and "<form" not in page.split("</section>")[-1].split("Grilla no disponible")[0]
    r = c.post(_url(bid, app_id), data={"expected_revision": "0"})
    assert r.status_code == 200 and "todavía no hay una grilla" in r.text.lower() and _all(bid) == []


def test_an_unknown_application_or_a_foreign_one_gives_404_and_creates_nothing(c):
    bid, grid = _ready(c)
    other_bid = _manual(nombre_puesto="Otro")
    foreign = _candidate(other_bid)
    for app_id in ("no-existe", foreign):
        assert c.get(_url(bid, app_id)).status_code == 404 and c.post(_url(bid, app_id), data=_form(grid)).status_code == 404
    assert _all(bid) == [] and _all(other_bid) == []
    assert c.get(_url("no-existe", foreign)).status_code == 404


# ═══════════════════ guardar: identidad real, versión de grilla, idempotencia ═══════════════════
def test_saving_records_the_real_identity_the_grid_version_and_ignores_a_forged_evaluator(c):
    bid, grid = _ready(c)
    app_id = _candidate(bid)
    r = c.post(_url(bid, app_id), data=_form(grid, notas=(5, 3, 1), evaluator="otro@danwor.com", recorded_by="falso@danwor.com", comentario_general="  Buen perfil  ", **{f"comentario_{_ids(grid)['tecnico']}": "Sólido"}))
    assert r.status_code == 303 and r.headers["location"] == f"/cchh/busquedas/{bid}?vista=scoring&paso=ranking&ok=evaluacion"
    [e] = _all(bid)
    assert (e["evaluator"], e["recorded_by"], e["grid_id"], e["grid_version"], e["fase"], e["application_id"]) == (CCHH_EMAIL, CCHH_EMAIL, grid["grid_id"], 1, "cchh", app_id)
    assert e["puntaje"] == pytest.approx(60 + 7.5 + 0) and e["cumple_excluyentes"] is True and e["comentario_general"] == "Buen perfil" and "otro@danwor.com" not in str(e) and "falso@" not in str(e)
    assert {d["criterio_id"]: d["comentario"] for d in e["detalles"]}[_ids(grid)["tecnico"]] == "Sólido"
    assert "Evaluación CCHH guardada." in c.get(r.headers["location"]).text


def test_saving_the_same_answers_again_changes_nothing_and_a_change_updates_the_same_record(c):
    bid, grid = _ready(c)
    app_id = _candidate(bid)
    c.post(_url(bid, app_id), data=_form(grid))
    [e1] = _all(bid)
    again = c.post(_url(bid, app_id), data=_form(grid, revision=1))
    assert again.status_code == 303 and again.headers["location"].endswith("ok=evaluacion_sin_cambios") and _all(bid) == [e1]
    changed = c.post(_url(bid, app_id), data=_form(grid, notas=(3, 3, 3), revision=1))
    [e2] = _all(bid)
    assert changed.status_code == 303 and e2["evaluation_id"] == e1["evaluation_id"] and e2["revision"] == 2 and e2["puntaje"] == 50.0
    stale = c.post(_url(bid, app_id), data=_form(grid, notas=(1, 1, 1), revision=1))                                              # formulario abierto con una revisión vieja
    assert stale.status_code == 200 and "cambió desde que abriste el formulario" in stale.text and _all(bid) == [e2]
    page = c.get(_url(bid, app_id)).text
    assert 'name="expected_revision" value="2"' in page and "Actualizar evaluación" in page
    assert re.search(r'name="nota_%s" value="3" checked' % _ids(grid)["tecnico"], page)                                           # precarga la evaluación propia


def test_an_incomplete_evaluation_is_rejected_keeping_the_selections(c):
    bid, grid = _ready(c)
    app_id = _candidate(bid)
    ids = _ids(grid)
    r = c.post(_url(bid, app_id), data={"expected_revision": "0", f"nota_{ids['tecnico']}": "5", f"comentario_{ids['tecnico']}": "mi comentario", "comentario_general": "general"})
    assert r.status_code == 200 and "Marcá si cumple el excluyente «¿Usó Datasphere?»" in r.text and "Falta la nota de «Certificación»" in r.text and "Falta la nota de «Comunicación»" in r.text
    assert re.search(r'name="nota_%s" value="5" checked' % ids["tecnico"], r.text) and 'value="mi comentario"' in r.text and ">general</textarea>" in r.text
    assert _all(bid) == []
    bad = c.post(_url(bid, app_id), data=_form(grid, notas=(6, 5, 5)))
    assert bad.status_code == 200 and "Falta la nota de «SAP Datasphere»" in bad.text and _all(bid) == []


# ═══════════════════ ranking: cobertura, tiers, excluyentes, dispersión ═══════════════════
def test_the_ranking_orders_by_score_with_tiers_coverage_and_explicit_missing_evaluations(c):
    bid, grid = _ready(c)
    a, b, d, z = _candidate(bid, "Ana"), _candidate(bid, "Beto"), _candidate(bid, "Dora"), _candidate(bid, "Zoe")
    c.post(_url(bid, a), data=_form(grid, notas=(5, 5, 5)))                   # 100 -> A
    c.post(_url(bid, b), data=_form(grid, notas=(3, 3, 3)))                   # 50 -> C
    c.post(_url(bid, d), data=_form(grid, notas=(5, 5, 5), cumple="no"))      # falla el excluyente -> No aplica
    page = _ranking(c, bid)
    rows = re.findall(r'<tr data-application-id="([^"]+)" data-estado="([^"]+)">\s*<td class="center">([^<]+)</td>', page)
    assert [(r[0], r[2]) for r in rows] == [(a, "1"), (b, "2"), (d, "–"), (z, "–")]                          # A, C, No aplica, sin evaluar
    assert [r[1] for r in rows] == ["vigente", "vigente", "vigente", "sin_evaluar"]
    assert re.search(r'scoring-tier--a">A<', page) and re.search(r'scoring-tier--c">C<', page) and re.search(r'scoring-tier--na">No aplica<', page) and 'class="scoring-tier">Sin evaluar<' in page
    assert "<strong>100.0</strong>" in page and "<strong>50.0</strong>" in page and "✗ No cumple: ¿Usó Datasphere?" in page
    assert "3 con evaluación vigente" in page and "1 sin evaluar" in page and "0 con evaluación desactualizada" in page
    assert "no es un consolidado entre fases" in page.lower() or "No incluye la entrevista técnica ni es un consolidado entre fases" in page
    assert page.count("Evaluar</a>") == 1 and page.count("Editar mi evaluación</a>") == 3                     # acceso directo desde el ranking a abrir/editar cada evaluación
    assert f'href="/cchh/busquedas/{bid}/candidatos/{z}/evaluacion-cchh">Evaluar</a>' in page and f'href="/cchh/busquedas/{bid}/candidatos/{a}/evaluacion-cchh">Editar mi evaluación</a>' in page


def test_a_second_evaluator_adds_their_own_record_and_a_large_gap_raises_the_dispersion_warning(c):
    bid, grid = _ready(c)
    app_id = _candidate(bid)
    c.post(_url(bid, app_id), data=_form(grid, notas=(5, 5, 5)))
    second = session_client(OTHER)
    assert second.get(_url(bid, app_id)).status_code == 200 and len(_all(bid)) == 1                                  # abrir no crea
    second.post(_url(bid, app_id), data=_form(grid, notas=(3, 3, 3)))
    evs = _all(bid)
    assert sorted(e["evaluator"] for e in evs) == [CCHH_EMAIL, OTHER] and {e["recorded_by"] for e in evs} == {CCHH_EMAIL, OTHER}
    page = _ranking(c, bid)
    assert "<strong>75.0</strong>" in page and "scoring-tier--a" in page            # promedio de las vigentes
    assert "⚠ Dif. 50 pts" in page                                                  # alerta de dispersión
    assert "2 evaluaciones" in page
    assert f"<strong>{CCHH_EMAIL}</strong>" in page and f"<strong>{OTHER}</strong>" in page
    mine = second.get(_url(bid, app_id)).text
    assert re.search(r'name="nota_%s" value="3" checked' % _ids(grid)["tecnico"], mine) and 'name="expected_revision" value="1"' in mine   # cada evaluador ve y edita SOLO lo suyo
    assert len(_all(bid)) == 2 and {e["evaluator"] for e in _all(bid)} == {CCHH_EMAIL, OTHER}


def test_a_grid_change_makes_the_evaluations_outdated_visibly_and_reevaluating_makes_them_current_again(c):
    bid, grid = _ready(c)
    app_id = _candidate(bid)
    c.post(_url(bid, app_id), data=_form(grid))
    assert "Vigente" in _ranking(c, bid)
    edited = _rows(*[(x["bloque"], x["nombre"], ("50" if x["nombre"] == "SAP Datasphere" else ("25" if x["nombre"] == "Certificación" else str(int(x["peso"])))), {"criterio_id": x["criterio_id"]}) for x in grid["criterios"]])
    assert _grid_post(c, bid, grid["revision"], edited).status_code == 303 and grepo.get(bid)["version"] == 2
    page = _ranking(c, bid)
    assert "Desactualizada (1)" in page and "0 con evaluación vigente" in page and "1 con evaluación desactualizada" in page and 'class="scoring-tier">Sin evaluar<' in page
    assert "<strong>–</strong>" in page and "desactualizada</strong>" in page                                          # no cuenta en el resultado; se identifica explícitamente
    form = c.get(_url(bid, app_id)).text
    assert "data-evaluation-outdated" in form and "versión 1 de la grilla; la vigente es la 2" in form and re.search(r'name="nota_%s" value="5" checked' % _ids(grid)["tecnico"], form)
    c.post(_url(bid, app_id), data=_form(grepo.get(bid), revision=1))
    [e] = _all(bid)
    assert e["grid_version"] == 2 and e["revision"] == 2 and "Vigente" in _ranking(c, bid) and "Desactualizada" not in _ranking(c, bid).split("data-scoring-ranking")[1]


# ═══════════════════ pipeline, paso 3 del panel y separación del registro administrativo ═══════════════════
def test_the_pipeline_icon_of_the_cchh_stage_opens_the_evaluation_and_other_stages_keep_their_access(c):
    bid, grid = _ready(c)
    ids = {n: _candidate(bid, n) for n in ("Ana", "Bruno", "Carla", "Diego")}
    svc.move_application_stage(bid, ids["Bruno"], "Entrevista CCHH", None, actor=CCHH_EMAIL)
    svc.move_application_stage(bid, ids["Carla"], "Entrevista Líder", None, actor=CCHH_EMAIL)
    svc.move_application_stage(bid, ids["Diego"], "Decisión Final", None, actor=CCHH_EMAIL)
    page = c.get(f"/cchh/busquedas/{bid}").text
    bruno, carla = _card(page, "Bruno Test"), _card(page, "Carla Test")
    assert f'href="{_url(bid, ids["Bruno"])}" class="table-action-btn chart-tooltip-anchor busqueda-cand__interview" aria-label="Evaluación CCHH"' in bruno
    assert 'aria-label="Ir a entrevista técnica"' in carla and "/cchh/entrevistas/" in carla and "evaluacion-cchh" not in carla      # Líder: registro administrativo, sin cambios
    assert "busqueda-cand__interview" not in _card(page, "Ana Test") and "busqueda-cand__interview" not in _card(page, "Diego Test")


def test_step_three_opens_by_default_with_a_valid_grid_and_the_evaluations_never_touch_the_administrative_interview(c):
    bid = _manual()
    page = c.get(f"/cchh/busquedas/{bid}?vista=scoring").text
    assert 'aria-current="step"' in page and "requiere la grilla" in page and "data-scoring-ranking" not in page
    _, grid = _ready(c, bid)
    app_id = _candidate(bid)
    svc.move_application_stage(bid, app_id, "Entrevista CCHH", None, actor=CCHH_EMAIL)
    entrevista_before = erepo.find_by_profile_and_busqueda(brepo.get(bid)["candidatos"][0]["profile_id"], bid)
    c.post(_url(bid, app_id), data=_form(grid))
    assert erepo.find_by_profile_and_busqueda(brepo.get(bid)["candidatos"][0]["profile_id"], bid) == entrevista_before          # el registro administrativo único no se modifica
    for forbidden in ("puntaje", "grid_version", "detalles", "tier"):
        assert forbidden not in entrevista_before
    default = c.get(f"/cchh/busquedas/{bid}?vista=scoring").text
    assert "data-scoring-ranking" in default and f'href="/cchh/busquedas/{bid}?vista=scoring&paso=ranking"' in default


# ═══════════════════ procesos cerrados, candidatos eliminados, autorización ═══════════════════
def test_a_closed_process_keeps_the_ranking_readable_but_rejects_evaluations(c):
    bid, grid = _ready(c)
    app_id = _candidate(bid)
    c.post(_url(bid, app_id), data=_form(grid))
    svc.close_busqueda(bid, [], actor=CCHH_EMAIL)
    before = _all(bid)
    page = _ranking(c, bid)
    assert "data-scoring-ranking" in page and "Evaluar</a>" not in page and "Editar mi evaluación" not in page and "El proceso está cerrado" in page
    form = c.get(_url(bid, app_id)).text
    assert "El proceso está cerrado: la evaluación queda en solo lectura." in form and "Actualizar evaluación" not in form
    r = c.post(_url(bid, app_id), data=_form(grid, notas=(1, 1, 1), revision=1))
    assert r.status_code == 200 and "El proceso está cerrado" in r.text and _all(bid) == before


def test_a_removed_candidate_cannot_be_evaluated_and_has_no_action_in_the_ranking(c):
    bid, grid = _ready(c)
    app_id = _candidate(bid, "Baja")
    pid = brepo.get(bid)["candidatos"][0]["profile_id"]
    profile_repository.soft_delete(pid, actor=CCHH_EMAIL)
    assert c.post(_url(bid, app_id), data=_form(grid)).status_code == 404 and _all(bid) == []
    page = _ranking(c, bid)
    assert "Candidato eliminado del Banco" in page and "Evaluar</a>" not in page


def test_evaluations_require_the_cchh_session_and_authorization(c):
    bid, grid = _ready(c)
    app_id = _candidate(bid)
    anon = TestClient(app, follow_redirects=False)
    assert anon.get(_url(bid, app_id)).status_code in (302, 303, 307) and anon.post(_url(bid, app_id), data=_form(grid)).status_code in (302, 303, 307)
    outsider = session_client("sin.acceso@danwor.com")
    assert outsider.get(_url(bid, app_id)).status_code == 403 and outsider.post(_url(bid, app_id), data=_form(grid)).status_code == 403
    assert _all(bid) == []


def test_a_storage_failure_shows_a_controlled_error_and_leaves_no_record(c, monkeypatch):
    bid, grid = _ready(c)
    app_id = _candidate(bid)
    def boom(*a, **k):
        raise grepo.GridStorageError("disco lleno")
    with monkeypatch.context() as m:
        m.setattr(erepo_s, "save", boom)
        r = c.post(_url(bid, app_id), data=_form(grid))
    assert r.status_code == 200 and "disco lleno" in r.text and _all(bid) == []
