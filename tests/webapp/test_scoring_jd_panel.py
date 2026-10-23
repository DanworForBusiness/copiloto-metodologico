"""Scoring (JD) — incremento 1 (scoring CCHH): repositorio de la grilla (instancia por búsqueda con identidad y versión), pestaña «Scoring (JD)» con JD y grilla funcionales, y el resto de las pestañas sin cambios.

Fixtures de I1 (persistencia aislada; `PERSISTENCE_MODE=remote` con Spaces simulado)."""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import busqueda_repository as brepo, human_capital_service as svc, scoring_engine as se, scoring_grid_repository as grepo
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, OPS_A, SID, env, remote, req  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import session_client, operations_users  # noqa: F401
from tests.webapp.test_pipeline_interview_shortcut import _manual  # noqa: F401


@pytest.fixture()
def c():
    return session_client(CCHH_EMAIL)


def _rows(*items) -> dict:
    """Cuerpo de formulario del editor: una fila = (bloque, nombre, peso, extra)."""
    cols = {k: [] for k in grepo_fields}
    for it in items:
        bloque, nombre, peso, *extra = it
        extra = extra[0] if extra else {}
        row = {"criterio_id": extra.get("criterio_id", ""), "bloque": bloque, "nombre": nombre, "peso": peso, "descripcion": extra.get("descripcion", ""), "ancla_1": "", "ancla_3": "", "ancla_5": "",
               "preguntas_guia": extra.get("preguntas_guia", "")}
        for k in grepo_fields:
            cols[k].append(row[k])
    return cols


grepo_fields = ("criterio_id", "bloque", "nombre", "peso", "descripcion", "ancla_1", "ancla_3", "ancla_5", "preguntas_guia")
VALID = (("excluyente", "¿Usó Datasphere?", "0"), ("tecnico", "SAP Datasphere", "60", {"preguntas_guia": "¿Cuántos proyectos?"}), ("deseable", "Certificación", "15"), ("competencia", "Comunicación", "25"))


def _grid_post(c, bid, revision, rows, **kw):
    return c.post(f"/cchh/busquedas/{bid}/scoring/grilla", data={"expected_revision": str(revision), **rows}, **kw)


def _grid_rev(bid) -> int:
    return (grepo.get(bid) or {}).get("revision", 0)


# ═══════════════════ repositorio: instancia por búsqueda con identidad y versión ═══════════════════
def _crit(nombre, peso, bloque="tecnico", cid=None):
    return {"criterio_id": cid, "fase": "cchh", "bloque": bloque, "nombre": nombre, "peso": peso, "descripcion": None, "ancla_1": None, "ancla_3": None, "ancla_5": None, "preguntas_guia": None}


def test_the_grid_is_an_instance_with_own_identity_and_a_version_that_only_moves_when_the_content_changes():
    g1, changed = grepo.save("b1", 0, [_crit("SQL", 60), _crit("Inglés", 40, "competencia")], actor="Ana@Danwor.com")
    assert changed and (g1["version"], g1["revision"]) == (1, 1) and g1["grid_id"] and g1["busqueda_id"] == "b1"
    assert g1["origen"] == {"tipo": "manual", "template_id": None, "template_version": None}                  # reserva para instanciar plantillas
    assert g1["created_by"] == g1["updated_by"] == "ana@danwor.com" and [c["orden"] for c in g1["criterios"]] == [0, 1] and all(c["criterio_id"] for c in g1["criterios"])
    again, changed2 = grepo.save("b1", 1, [_crit("SQL", 60, cid=g1["criterios"][0]["criterio_id"]), _crit("Inglés", 40, "competencia", cid=g1["criterios"][1]["criterio_id"])], actor="x@danwor.com")
    assert not changed2 and again == g1 and grepo.get("b1") == g1                                              # sin cambios: ni escritura ni versión nueva
    ids = [c["criterio_id"] for c in g1["criterios"]]
    g2, changed3 = grepo.save("b1", 1, [_crit("SQL", 50, cid=ids[0]), _crit("Inglés", 50, "competencia", cid=ids[1]), _crit("Falso", 0, "excluyente", cid="id-inventado")], actor="beto@danwor.com")
    assert changed3 and (g2["version"], g2["revision"], g2["grid_id"]) == (2, 2, g1["grid_id"]) and g2["updated_by"] == "beto@danwor.com" and g2["created_by"] == "ana@danwor.com"
    assert [c["criterio_id"] for c in g2["criterios"]][:2] == ids and g2["criterios"][2]["criterio_id"] != "id-inventado"          # ids estables; un id inventado no se acepta


def test_a_stale_revision_is_rejected_and_unsafe_ids_never_reach_the_filesystem():
    grepo.save("b1", 0, [_crit("SQL", 100)], actor=None)
    with pytest.raises(grepo.GridConcurrentModificationError):
        grepo.save("b1", 0, [_crit("Otro", 100)], actor=None)                                                  # quien abrió el editor antes de que existiera pisaría la grilla
    for bad in ("../x", "a/b", "", "a b"):
        with pytest.raises(ValueError):
            grepo.get(bad)
    assert [p.name for p in (config.BASE_DIR / "human-capital" / "scoring-grids").iterdir()] == ["b1.json"]


def test_remote_failures_compensate_without_losing_the_previous_version(remote):
    remote.enabled = True
    remote.push_script = [False, False]
    with pytest.raises(grepo.GridRemoteOutcomeUnknownError):
        grepo.save("b1", 0, [_crit("SQL", 100)], actor=None)
    assert grepo.get("b1") is None                                                                             # el alta no confirmada no deja archivo
    g1, _ = grepo.save("b1", 0, [_crit("SQL", 100)], actor=None)
    remote.push_script = [False, False]
    with pytest.raises(grepo.GridRemoteOutcomeUnknownError):
        grepo.save("b1", 1, [_crit("SQL", 50), _crit("Otro", 50)], actor=None)
    assert grepo.get("b1") == g1                                                                               # la versión confirmada se conserva


def test_read_only_mode_rejects_writes_before_touching_the_disk(monkeypatch):
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "read-only")
    with pytest.raises(grepo.GridConfigIncompleteError):
        grepo.save("b1", 0, [_crit("SQL", 100)], actor=None)
    assert not (config.BASE_DIR / "human-capital" / "scoring-grids").exists()


# ═══════════════════ pestañas: Scoring (JD) funcional, el resto sin cambios ═══════════════════
def test_only_pipeline_and_scoring_tabs_are_enabled_and_the_others_stay_pending(c):
    bid = _manual()
    page = c.get(f"/cchh/busquedas/{bid}").text
    assert re.search(r'<a href="/cchh/busquedas/%s" class="is-active" aria-current="page">Vista pipeline</a>' % bid, page)
    assert f'<a href="/cchh/busquedas/{bid}?vista=scoring" class="">Scoring (JD)</a>' in page
    for pending in ("Vista evaluación", "Sugeridos del banco", "Reportes"):
        assert f'<button type="button" disabled title="Próximamente">{pending}</button>' in page
    assert 'id="busqueda-detail-body"' in page and "data-scoring-panel" not in page                         # por defecto, el pipeline de siempre


def test_the_scoring_view_shows_the_cchh_panel_instead_of_the_pipeline_and_never_claims_a_consolidated_result(c):
    bid = _manual()
    page = c.get(f"/cchh/busquedas/{bid}?vista=scoring").text
    assert "data-scoring-panel" in page and "<h2>Scoring CCHH</h2>" in page and 'id="busqueda-detail-body"' not in page
    assert "no es un consolidado CCHH / técnico" in page and f"Tier A ≥ {se.TIER_A_MIN}, B ≥ {se.TIER_B_MIN}" in page
    assert re.search(r'<a href="/cchh/busquedas/%s\?vista=scoring" class="is-active" aria-current="page">Scoring \(JD\)</a>' % bid, page)
    assert 'aria-current="step"' in page and "Evaluación CCHH y ranking" in page and 'aria-disabled="true"' in page and "próximamente" in page
    assert "Todavía no cargaste la Job Description" not in page and 'name="jd_texto"' in page                # sin JD: abre en el paso 1


def test_an_unmanaged_solicitud_has_no_scoring_view(c):
    svc.create_solicitud(SID, req(), OPS_A)                                                                  # solicitud enviada: aún no es un proceso de CCHH
    page = c.get(f"/cchh/busquedas/{SID}?vista=scoring").text
    assert "data-scoring-panel" not in page and "Scoring (JD)" not in page
    assert c.post(f"/cchh/busquedas/{SID}/scoring/jd", data={"jd_texto": "x"}).status_code == 200 and not brepo.get(SID).get("jd_texto")


# ═══════════════════ paso 1: Job Description ═══════════════════
def test_saving_the_jd_uses_the_process_fields_and_leads_to_the_grid_step(c):
    bid = _manual()
    rev = brepo.get(bid)["revision"]
    r = c.post(f"/cchh/busquedas/{bid}/scoring/jd", data={"jd_texto": "  Responsabilidades: modelar en Datasphere.  ", "palabras_clave": "SAP, Datasphere"})
    assert r.status_code == 303 and r.headers["location"] == f"/cchh/busquedas/{bid}?vista=scoring&paso=grilla&ok=jd"
    doc = brepo.get(bid)
    assert doc["jd_texto"] == "Responsabilidades: modelar en Datasphere." and doc["palabras_clave"] == "SAP, Datasphere" and doc["revision"] == rev + 1
    assert doc["nombre_puesto"] == "Solution Architect"                                                      # el resto del proceso no se toca
    assert "Responsabilidades: modelar en Datasphere." in c.get(f"/cchh/busquedas/{bid}/editar").text      # la MISMA JD que muestra el formulario de edición
    page = c.get(r.headers["location"]).text
    assert "Job Description guardada." in page and 'aria-current="step"' in page and 'data-scoring-form' in page and "Todavía no cargaste" not in page
    assert c.post(f"/cchh/busquedas/{bid}/scoring/jd", data={"jd_texto": "", "palabras_clave": ""}).status_code == 303 and brepo.get(bid)["jd_texto"] is None


def test_an_overlong_jd_is_rejected_keeping_what_the_user_typed(c):
    bid = _manual()
    r = c.post(f"/cchh/busquedas/{bid}/scoring/jd", data={"jd_texto": "x" * 30001, "palabras_clave": "kw"})
    assert r.status_code == 200 and "supera los 30000 caracteres" in r.text and brepo.get(bid).get("jd_texto") is None and 'value="kw"' in r.text


# ═══════════════════ paso 2: grilla CCHH ═══════════════════
def test_a_valid_grid_is_saved_as_version_1_and_shown_in_the_editor(c):
    bid = _manual()
    r = _grid_post(c, bid, 0, _rows(*VALID))
    assert r.status_code == 303 and r.headers["location"] == f"/cchh/busquedas/{bid}?vista=scoring&paso=grilla&ok=grilla"
    g = grepo.get(bid)
    assert (g["version"], g["revision"]) == (1, 1) and [(x["bloque"], x["nombre"], x["peso"]) for x in g["criterios"]] == [("excluyente", "¿Usó Datasphere?", 0.0), ("tecnico", "SAP Datasphere", 60.0),
                                                                                                                       ("deseable", "Certificación", 15.0), ("competencia", "Comunicación", 25.0)]
    assert g["criterios"][1]["preguntas_guia"] == "¿Cuántos proyectos?" and all(x["fase"] == "cchh" for x in g["criterios"])
    page = c.get(r.headers["location"]).text
    assert "Grilla guardada: se creó una nueva versión." in page and "· v1" in page and 'value="SAP Datasphere"' in page and 'name="expected_revision" value="1"' in page
    assert '<strong data-scoring-sum>100</strong>% de 100%' in page and "evaluaciones hechas con versiones anteriores quedan <strong>desactualizadas</strong>" in page
    assert page.count("data-scoring-row ") == 4 + 1 and "data-scoring-row-template" in page                      # 4 criterios + la fila plantilla del botón «Agregar criterio»


def test_an_invalid_sum_is_rejected_with_the_reasons_and_what_was_typed(c):
    bid = _manual()
    r = _grid_post(c, bid, 0, _rows(("tecnico", "SQL", "50"), ("deseable", "Cert", "30")))
    assert r.status_code == 200 and "hoy suma 80%" in r.text and 'value="SQL"' in r.text and 'value="Cert"' in r.text and grepo.get(bid) is None
    assert '<strong data-scoring-sum>80</strong>' in r.text
    r2 = _grid_post(c, bid, 0, _rows(("excluyente", "Solo filtro", "0")))
    assert r2.status_code == 200 and "al menos un criterio con ponderación" in r2.text and grepo.get(bid) is None
    r3 = _grid_post(c, bid, 0, _rows(("tecnico", "SQL", "abc"), ("raro", "X", "10")))
    assert r3.status_code == 200 and "entre 0 y 100" in r3.text and "bloque inválido" in r3.text and grepo.get(bid) is None


def test_saving_again_without_changes_keeps_the_version_and_a_change_creates_the_next_one(c):
    bid = _manual()
    _grid_post(c, bid, 0, _rows(*VALID))
    g1 = grepo.get(bid)
    same = _rows(*[(x["bloque"], x["nombre"], str(int(x["peso"])), {"criterio_id": x["criterio_id"], "preguntas_guia": x["preguntas_guia"] or ""}) for x in g1["criterios"]])
    r = _grid_post(c, bid, g1["revision"], same)
    assert r.status_code == 303 and r.headers["location"].endswith("ok=grilla_sin_cambios") and grepo.get(bid) == g1
    assert "no tenía cambios" in c.get(r.headers["location"]).text
    edited = _rows(*[(x["bloque"], x["nombre"], ("50" if x["nombre"] == "SAP Datasphere" else str(int(x["peso"]))), {"criterio_id": x["criterio_id"]}) for x in g1["criterios"]],
                   ("competencia", "Nuevo", "10"))
    r2 = _grid_post(c, bid, g1["revision"], edited)
    g2 = grepo.get(bid)
    assert r2.status_code == 303 and (g2["version"], g2["revision"], g2["grid_id"]) == (2, 2, g1["grid_id"]) and "v2" in c.get(r2.headers["location"]).text
    assert [x["criterio_id"] for x in g2["criterios"]][:4] == [x["criterio_id"] for x in g1["criterios"]]    # criterios existentes conservan su identidad


def test_a_concurrent_edit_is_detected_and_nothing_is_overwritten(c):
    bid = _manual()
    _grid_post(c, bid, 0, _rows(*VALID))
    g1 = grepo.get(bid)
    _grid_post(c, bid, g1["revision"], _rows(("tecnico", "Cambio de otra persona", "100")))
    g2 = grepo.get(bid)
    r = _grid_post(c, bid, g1["revision"], _rows(("tecnico", "Mi cambio viejo", "100")))                    # el editor quedó abierto con la revisión anterior
    assert r.status_code == 200 and "La grilla cambió desde que abriste el editor" in r.text and grepo.get(bid) == g2
    assert _grid_post(c, bid, "no-numero", _rows(*VALID)).status_code == 200 and grepo.get(bid) == g2


def test_the_excluding_blocks_have_no_weight_and_the_editor_reads_the_engine_constants(c):
    bid = _manual()
    _grid_post(c, bid, 0, _rows(("excluyente", "Filtro", "40"), ("tecnico", "SQL", "100")))
    assert [x["peso"] for x in grepo.get(bid)["criterios"]] == [0.0, 100.0]
    page = c.get(f"/cchh/busquedas/{bid}?vista=scoring&paso=grilla").text
    for b in se.BLOQUES:
        assert b["label"] in page
    tpl = __import__("pathlib").Path(__file__).resolve().parents[2] / "webapp/templates/partials/human_capital_scoring_panel.html"
    src = tpl.read_text(encoding="utf-8")
    assert "TIER_A_MIN" not in src and "75" not in src and "60" not in src.replace("scoring-row__", "")            # umbrales/pesos nunca hardcodeados en la vista ni en su JavaScript (vienen del motor)


def test_a_closed_process_is_read_only(c):
    bid = _manual()
    _grid_post(c, bid, 0, _rows(*VALID))
    svc.close_busqueda(bid, [], actor=CCHH_EMAIL)
    page = c.get(f"/cchh/busquedas/{bid}?vista=scoring&paso=grilla").text
    assert "El proceso está cerrado: el scoring queda en solo lectura." in page and "Guardar grilla" not in page and "+ Agregar criterio" not in page and "<fieldset class=\"scoring-fieldset\" disabled>" in page
    g = grepo.get(bid)
    r = _grid_post(c, bid, g["revision"], _rows(("tecnico", "X", "100")))
    assert r.status_code == 200 and "El proceso está cerrado" in r.text and grepo.get(bid) == g
    j = c.post(f"/cchh/busquedas/{bid}/scoring/jd", data={"jd_texto": "nueva"})
    assert j.status_code == 200 and "El proceso está cerrado" in j.text and not brepo.get(bid).get("jd_texto")


def test_storage_failures_show_a_controlled_error_not_a_500(c, monkeypatch):
    bid = _manual()
    def boom(*a, **k):
        raise grepo.GridStorageError("disco lleno")
    monkeypatch.setattr(grepo, "get", boom)
    page = c.get(f"/cchh/busquedas/{bid}?vista=scoring")
    assert page.status_code == 200 and "disco lleno" in page.text
    monkeypatch.undo()


# ═══════════════════ autorización ═══════════════════
def test_scoring_requires_the_cchh_session_and_authorization():
    bid = _manual()
    anon = TestClient(app, follow_redirects=False)
    assert anon.get(f"/cchh/busquedas/{bid}?vista=scoring").status_code in (302, 303, 307)
    for url, data in ((f"/cchh/busquedas/{bid}/scoring/jd", {"jd_texto": "x"}), (f"/cchh/busquedas/{bid}/scoring/grilla", {"expected_revision": "0"})):
        assert anon.post(url, data=data).status_code in (302, 303, 307)
    assert brepo.get(bid).get("jd_texto") is None and grepo.get(bid) is None
    outsider = session_client("ops.sin.cchh@danwor.com")
    assert outsider.post(f"/cchh/busquedas/{bid}/scoring/jd", data={"jd_texto": "x"}).status_code == 403 and brepo.get(bid).get("jd_texto") is None
