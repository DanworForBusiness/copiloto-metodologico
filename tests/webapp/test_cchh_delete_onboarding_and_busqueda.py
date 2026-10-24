"""Hotfix CCHH — eliminación MANUAL de un Onboarding (retira la postulación Ingreso de su búsqueda) y de una Búsqueda COMPLETA (cascada auditada y reanudable). Datos sintéticos; persistencia aislada (fixtures de I1, con
Spaces simulado). Nunca se tocan perfiles, CVs ni SharePoint, ni lo que pertenece a otras búsquedas."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import (busqueda_deletion_repository as jrepo, busqueda_repository as brepo, entrevista_repository as erepo, human_capital_service as svc, onboarding_repository as orepo,
                             profile_document_service, profile_repository, publicacion_repository as prepo, scoring_evaluation_repository as evrepo, scoring_grid_repository as grepo)
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, env, remote, send as send_solicitud  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import operations_users, session_client  # noqa: F401
from tests.webapp.test_pipeline_interview_shortcut import _manual

LIST = "/cchh/onboarding"


@pytest.fixture()
def c():
    return session_client(CCHH_EMAIL)


@pytest.fixture(autouse=True)
def _no_sharepoint(monkeypatch):
    """La eliminación NO usa SharePoint/Graph: si algún camino intentara borrar documentos, el test falla."""
    def boom(*a, **k):
        raise AssertionError("la eliminación no debe borrar documentos de perfil ni tocar SharePoint")
    monkeypatch.setattr(profile_document_service, "delete_profile_documents", boom)


def _candidate(bid, first, last="Test") -> tuple[str, str]:
    pid = profile_repository.create({"first_name": first, "last_name": last, "email": f"{first.lower()}@danwor.com"})["profile_id"]
    return pid, svc.add_candidate_to_busqueda(bid, pid, actor=CCHH_EMAIL)["candidatos"][-1]["application_id"]


def _add(bid, pid) -> str:
    return svc.add_candidate_to_busqueda(bid, pid, actor=CCHH_EMAIL)["candidatos"][-1]["application_id"]


def _ingreso(bid, pid=None, first="Ana") -> tuple[str, str]:
    if pid is None:
        pid, app_id = _candidate(bid, first)
    else:
        app_id = _add(bid, pid)
    svc.move_application_stage(bid, app_id, "Entrevista Líder", None, actor=CCHH_EMAIL)
    svc.move_application_stage(bid, app_id, "Finalizado", "Ingreso", actor=CCHH_EMAIL)
    assert orepo.get(app_id) is not None
    return pid, app_id


def _del_onb(c, app_id, return_to=""):
    return c.post(f"/cchh/onboarding/{app_id}/eliminar" + (f"?return_to={return_to}" if return_to else ""))


def _del_bus(c, bid):
    return c.post(f"/cchh/busquedas/{bid}/eliminar-completa")


def _hc_dir() -> Path:
    return config.BASE_DIR / "human-capital"


def _digest(path: Path) -> dict:
    return {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(path.rglob("*")) if p.is_file()} if path.exists() else {}


# ═══════════════════ 7. Eliminar onboarding ═══════════════════
def test_the_onboarding_row_offers_the_shared_delete_button_with_the_corporate_confirmation(c):
    bid = _manual()
    _, app_id = _ingreso(bid)
    html = c.get(LIST).text
    form = re.search(rf'<form method="post" action="/cchh/onboarding/{app_id}/eliminar\?return_to=[^"]*"[^>]*>', html)
    assert form and "data-dw-confirm-form" in form.group(0) and 'data-dw-confirm-title="Eliminar onboarding"' in form.group(0) and 'data-dw-confirm-ok="Eliminar"' in form.group(0)
    assert "Si figura como Ingreso en una búsqueda, también se lo retira de esa búsqueda" in form.group(0) and "El perfil del Banco y su CV no se modifican" in form.group(0)
    assert 'aria-label="Eliminar onboarding"' in html and "table-action-btn--danger" in html                                 # el mismo botón de acción destructiva de Candidatos/Entrevistas
    assert 'aria-label="Editar onboarding"' in html                                                                         # la edición sigue


def test_deleting_an_ingreso_onboarding_retires_that_application_and_nothing_else(c):
    a = _manual()
    b = _manual(nombre_puesto="Otro puesto")
    pid, app_a = _ingreso(a, first="Ana")
    app_b = _add(b, pid)                                                                                                     # la misma persona participa en OTRA búsqueda
    svc.move_application_stage(b, app_b, "Entrevista CCHH", None, actor=CCHH_EMAIL)
    other_pid, other_app = _ingreso(a, first="Elena")
    entrevista_a = erepo.find_by_profile_and_busqueda(pid, a)
    assert entrevista_a is not None and erepo.find_by_profile_and_busqueda(pid, b) is not None
    profile_before = profile_repository.get(pid)
    r = _del_onb(c, app_a, return_to="%2Fcchh%2Fonboarding%3Fpage_size%3D25")
    assert r.status_code == 303 and r.headers["location"] == "/cchh/onboarding?page_size=25"                                  # vuelve al listado en su mismo contexto
    assert orepo.get(app_a) is None and not (_hc_dir() / "onboarding" / f"{app_a}.json").exists()
    assert [x["application_id"] for x in brepo.get(a)["candidatos"]] == [other_app]                                           # la postulación Ingreso se retiró de ESA búsqueda…
    assert any(h.get("transicion") == "candidato_desvinculado" and h.get("application_id") == app_a and h.get("actor") == CCHH_EMAIL for h in brepo.historial_proceso_of(brepo.get(a)))   # …con el evento y el ejecutor
    assert [x["application_id"] for x in brepo.get(b)["candidatos"]] == [app_b]                                               # su participación en la OTRA búsqueda queda igual
    assert erepo.find_by_profile_and_busqueda(pid, b) is not None and erepo.get(entrevista_a["entrevista_id"]) is not None   # ninguna entrevista se borra (contrato existente de «quitar del proceso»)
    assert profile_repository.get(pid) == profile_before                                                                      # el perfil del Banco no se modificó
    assert orepo.get(other_app) is not None and [o["application_id"] for o in orepo.list()] == [other_app]                   # el onboarding de otra persona sigue


def test_repeating_the_onboarding_delete_is_idempotent(c):
    bid = _manual()
    _, app_id = _ingreso(bid)
    assert _del_onb(c, app_id).status_code == 303 and _del_onb(c, app_id).status_code == 303
    assert orepo.get(app_id) is None and brepo.get(bid)["candidatos"] == []
    assert _del_onb(c, "00000000-0000-4000-8000-000000000000").status_code == 303                                          # inexistente: sin error


def test_an_orphan_onboarding_is_deleted_without_touching_any_search(c):
    bid = _manual()
    pid, app_id = _candidate(bid, "Ana")                                                                                     # en Preselección: NO es Ingreso
    orepo.create_if_absent(application_id="aaaaaaaa-0000-4000-8000-000000000001", profile_id=pid, busqueda_id=bid)         # onboarding sin postulación en el pipeline
    before = brepo.get(bid)
    assert _del_onb(c, "aaaaaaaa-0000-4000-8000-000000000001").status_code == 303
    assert orepo.get("aaaaaaaa-0000-4000-8000-000000000001") is None and brepo.get(bid) == before


def test_the_application_is_matched_by_application_and_profile_never_by_name(c):
    bid = _manual()
    _, app_id = _ingreso(bid, first="Ana")
    other_pid, _ = _candidate(_manual(), "Zoe")
    data = orepo.get(app_id)
    path = _hc_dir() / "onboarding" / f"{app_id}.json"
    path.write_text(json.dumps({**data, "profile_id": other_pid}), encoding="utf-8")                                         # vínculo incoherente: otro perfil
    assert _del_onb(c, app_id).status_code == 303
    assert orepo.get(app_id) is None
    assert [x["application_id"] for x in brepo.get(bid)["candidatos"]] == [app_id]                                            # la postulación NO se retiró (no coincide el perfil)


def test_if_the_application_is_no_longer_ingreso_only_the_onboarding_is_deleted(c):
    bid = _manual()
    _, app_id = _ingreso(bid)
    brepo.move_stage(bid, app_id, "Finalizado", "Rechazado", actor=CCHH_EMAIL)                                               # el resultado cambió después (el onboarding no se borra solo)
    assert orepo.get(app_id) is not None
    assert _del_onb(c, app_id).status_code == 303
    assert orepo.get(app_id) is None and [x["application_id"] for x in brepo.get(bid)["candidatos"]] == [app_id]


def test_onboarding_delete_requires_a_capital_humano_session(c):
    bid = _manual()
    _, app_id = _ingreso(bid)
    anonymous = TestClient(app, follow_redirects=False)
    assert anonymous.post(f"/cchh/onboarding/{app_id}/eliminar").status_code in (302, 303, 401, 403)
    stranger = session_client("stranger@danwor.com")
    assert stranger.post(f"/cchh/onboarding/{app_id}/eliminar").status_code in (302, 303, 401, 403)
    assert orepo.get(app_id) is not None and len(brepo.get(bid)["candidatos"]) == 1


def test_a_spaces_failure_leaves_a_recoverable_state_and_the_retry_completes(c, remote):
    remote.enabled = True
    bid = _manual()
    _, app_id = _ingreso(bid)
    remote.delete_fails = True
    assert _del_onb(c, app_id).status_code == 303                                                                            # Spaces no confirma el borrado
    assert orepo.get(app_id) is not None and (_hc_dir() / "onboarding" / f"{app_id}.json").exists()                          # rollback local: el onboarding sigue visible (nunca divergen local y remoto)
    assert app_id in c.get(LIST).text
    remote.delete_fails = False
    assert _del_onb(c, app_id).status_code == 303                                                                            # reintento: lo que faltaba
    assert orepo.get(app_id) is None and f"{app_id}.json" not in remote.objects
    assert brepo.get(bid)["candidatos"] == []


# ═══════════════════ 8. Eliminar búsqueda completa ═══════════════════
def _seed_scoring(bid, app_id, pid):
    criterio = {"criterio_id": None, "fase": "cchh", "bloque": "Técnico", "nombre": "SAP FI", "descripcion": "d", "peso": 3, "ancla_1": "a", "ancla_3": "b", "ancla_5": "c", "preguntas_guia": []}
    grepo.save(bid, 0, [criterio], actor=CCHH_EMAIL)
    key = f"{app_id}:cchh:{CCHH_EMAIL}"
    evrepo.save({"evaluation_key": key, "busqueda_id": bid, "application_id": app_id, "profile_id": pid, "evaluator": CCHH_EMAIL, "recorded_by": CCHH_EMAIL, "grid_id": "g", "grid_version": 1,
                 "fase": "cchh", "puntaje": 80, "cumple_excluyentes": True, "comentario_general": "", "detalles": [], "recorded_at": "2026-10-20T10:00:00+00:00"}, 0)


def _world():
    """Búsqueda A (a eliminar) y B (debe quedar intacta), con una persona en AMBAS, más datos de A en cada dependiente y una entrevista manual sin búsqueda."""
    a, b = _manual(nombre_puesto="Puesto A"), _manual(nombre_puesto="Puesto B")
    shared_pid, shared_app_a = _ingreso(a, first="Ana")
    shared_app_b = _add(b, shared_pid)
    svc.move_application_stage(b, shared_app_b, "Entrevista CCHH", None, actor=CCHH_EMAIL)
    shared_app_b_ingreso = _ingreso_move(b, shared_app_b)
    solo_pid, solo_app = _candidate(a, "Bruno")
    svc.move_application_stage(a, solo_app, "Entrevista CCHH", None, actor=CCHH_EMAIL)
    _seed_scoring(a, solo_app, solo_pid)
    pub_a = prepo.create({"busqueda_id": a, "posicion": "Puesto A", "canal": "LinkedIn", "cantidad_avisos": 2, "periodo": "2026-Q4", "fecha": None, "observacion": "hist"})
    pub_free = prepo.create({"busqueda_id": None, "posicion": "Sin vínculo", "canal": "LinkedIn", "cantidad_avisos": 1, "periodo": "2026-Q4", "fecha": None, "observacion": None})
    free_pid = profile_repository.create({"first_name": "Libre", "last_name": "Test", "email": "libre@danwor.com"})["profile_id"]
    manual = svc.create_entrevista_manual(free_pid, {"fecha_entrevista": "2026-10-01", "puesto_evaluado": "Libre", "area": "SAP"})                         # entrevista SIN búsqueda: no depende de ninguna
    return {"a": a, "b": b, "shared_pid": shared_pid, "shared_app_a": shared_app_a, "shared_app_b": shared_app_b_ingreso, "solo_pid": solo_pid, "solo_app": solo_app, "pub_a": pub_a, "pub_free": pub_free, "manual": manual}


def _ingreso_move(bid, app_id):
    svc.move_application_stage(bid, app_id, "Finalizado", "Ingreso", actor=CCHH_EMAIL)
    assert orepo.get(app_id) is not None
    return app_id


def test_the_detail_header_has_the_intense_red_delete_right_after_close_with_an_explicit_confirmation(c):
    bid = _manual()
    html = c.get(f"/cchh/busquedas/{bid}").text
    close = html.index('data-dialog-open="busqueda-close-dialog">Cerrar proceso</button>')
    form = re.search(rf'<form method="post" action="/cchh/busquedas/{bid}/eliminar-completa"[^>]*>', html)
    assert form and form.start() > close and html[close:form.start()].count("<button") == 0 and html[close:form.start()].count("<a ") == 0     # inmediatamente a la derecha de «Cerrar proceso»
    tag = form.group(0)
    assert "data-dw-confirm-form" in tag and 'data-dw-confirm-title="Eliminar búsqueda"' in tag and 'data-dw-confirm-ok="Eliminar"' in tag
    assert "TODOS los registros asociados" in tag and "postulaciones, entrevistas, onboardings, grilla y evaluaciones" in tag
    assert "no se modifican" in tag and "Esta acción no se puede deshacer" in tag
    assert 'class="btn-danger btn-danger--strong"' in html[form.end():form.end() + 200]


def test_deleting_a_busqueda_cascades_to_its_dependents_and_preserves_everything_else(c):
    w = _world()
    a, b = w["a"], w["b"]
    docs_dir = _hc_dir() / "profile-documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "doc-sintetico.json").write_text(json.dumps({"document_id": "doc-sintetico", "profile_id": w["shared_pid"]}), encoding="utf-8")        # metadatos de un CV (sintético)
    docs_before, b_before = _digest(docs_dir), brepo.get(b)
    profiles_before = {p["profile_id"]: p for p in profile_repository.list()}
    ent_b_before = erepo.find_by_profile_and_busqueda(w["shared_pid"], b)
    onb_b_before = orepo.get(w["shared_app_b"])
    assert erepo.find_by_profile_and_busqueda(w["solo_pid"], a) is not None and orepo.get(w["shared_app_a"]) is not None and evrepo.list_for_busqueda(a) and grepo.get(a)
    r = _del_bus(c, a)
    assert r.status_code == 303 and r.headers["location"] == "/cchh/busquedas"
    assert brepo.get(a) is None and not (_hc_dir() / "busquedas" / f"{a}.json").exists()                                  # la búsqueda y sus postulaciones (embebidas)
    assert [e for e in erepo.list() if e.get("busqueda_id") == a] == []                                                    # entrevistas de A
    assert orepo.get(w["shared_app_a"]) is None                                                                            # onboarding de A
    assert grepo.get(a) is None and evrepo.list_for_busqueda(a) == [] and not (_hc_dir() / "scoring-evaluations" / a).exists()   # grilla y evaluaciones de A
    pub = prepo.get(w["pub_a"]["publicacion_id"])
    assert pub is not None and pub["busqueda_id"] is None and pub["posicion"] == "Puesto A" and pub["canal"] == "LinkedIn"  # la publicación se conserva, desvinculada (FK on delete set null)
    assert prepo.get(w["pub_free"]["publicacion_id"])["posicion"] == "Sin vínculo"
    assert brepo.get(b) == b_before                                                                                        # la OTRA búsqueda, intacta (incluida la misma persona)
    assert erepo.get(w["manual"]["entrevista_id"]) == w["manual"]                                                          # la entrevista manual (sin búsqueda) sigue
    assert erepo.find_by_profile_and_busqueda(w["shared_pid"], b) == ent_b_before and orepo.get(w["shared_app_b"]) == onb_b_before
    assert {p["profile_id"]: p for p in profile_repository.list()} == profiles_before                                      # perfiles intactos
    assert _digest(docs_dir) == docs_before                                                                                # metadatos de documentos/CVs intactos
    journal = jrepo.get(a)
    assert journal["estado"] == "completada" and journal["iniciada_por"] == CCHH_EMAIL and journal["puesto"] == "Puesto A" and journal["finalizada_en"]
    assert journal["plan"] == {"postulaciones": 2, "entrevistas": 2, "onboardings": 1, "evaluaciones": 1, "grilla": 1, "publicaciones_desvinculadas": 1}
    assert journal["hechos"] == {"onboardings": 1, "entrevistas": 2, "scoring": 2, "publicaciones": 1}
    assert "Ana" not in json.dumps(journal) and "Bruno" not in json.dumps(journal)                                         # sin datos personales de candidatos


def test_repeating_the_busqueda_delete_is_idempotent_and_an_unknown_one_is_404(c):
    w = _world()
    assert _del_bus(c, w["a"]).status_code == 303
    again = _del_bus(c, w["a"])
    assert again.status_code == 303 and again.headers["location"] == "/cchh/busquedas" and jrepo.get(w["a"])["intentos"] == 1
    assert _del_bus(c, "00000000-0000-4000-8000-000000000000").status_code == 404


def test_busqueda_delete_requires_a_capital_humano_session(c):
    w = _world()
    assert TestClient(app, follow_redirects=False).post(f"/cchh/busquedas/{w['a']}/eliminar-completa").status_code in (302, 303, 401, 403)
    assert session_client("stranger@danwor.com").post(f"/cchh/busquedas/{w['a']}/eliminar-completa").status_code in (302, 303, 401, 403)
    assert brepo.get(w["a"]) is not None and jrepo.get(w["a"]) is None


def test_a_solicitud_from_operations_is_not_deleted_by_this_route(c, operations_users):
    sid = "55555555-5555-4555-8555-555555555555"
    bid = send_solicitud(sid=sid)["busqueda_id"]
    page = c.get(f"/cchh/busquedas/{bid}").text
    assert "/eliminar-completa" not in page                                                                                # sin botón
    r = _del_bus(c, bid)
    assert r.status_code == 303 and r.headers["location"] == f"/cchh/busquedas/{bid}?aviso=eliminacion_no_permitida"
    assert brepo.get(bid) is not None and jrepo.get(bid) is None


def test_a_partial_failure_keeps_the_busqueda_visible_and_the_retry_resumes_it(c, monkeypatch):
    w = _world()
    a = w["a"]
    real_delete, calls = erepo.delete, {"n": 0}

    def flaky(entrevista_id):
        calls["n"] += 1
        if calls["n"] == 2:
            raise erepo.EntrevistaStorageError("falla simulada")
        return real_delete(entrevista_id)
    monkeypatch.setattr(erepo, "delete", flaky)
    r = _del_bus(c, a)
    assert r.status_code == 303 and r.headers["location"] == f"/cchh/busquedas/{a}?aviso=eliminacion_incompleta"
    assert brepo.get(a) is not None                                                                                        # la búsqueda NO se borra a medias: sigue visible y reanudable
    assert orepo.get(w["shared_app_a"]) is None                                                                            # los pasos previos sí quedaron hechos
    journal = jrepo.get(a)
    assert journal["estado"] == "fallida" and journal["falla"] == {"paso": "entrevistas", "error": "EntrevistaStorageError"}
    assert "No se pudo completar la eliminación de la búsqueda" in c.get(f"/cchh/busquedas/{a}?aviso=eliminacion_incompleta").text
    monkeypatch.setattr(erepo, "delete", real_delete)
    r = _del_bus(c, a)                                                                                                     # reintento: recalcula lo que falta y termina
    assert r.status_code == 303 and r.headers["location"] == "/cchh/busquedas"
    assert brepo.get(a) is None and [e for e in erepo.list() if e.get("busqueda_id") == a] == [] and grepo.get(a) is None
    journal = jrepo.get(a)
    assert journal["estado"] == "completada" and journal["intentos"] == 2 and journal["iniciada_por"] == CCHH_EMAIL and journal["falla"] is None
    assert brepo.get(w["b"]) is not None and erepo.find_by_profile_and_busqueda(w["shared_pid"], w["b"]) is not None


def test_if_the_audit_record_cannot_be_written_nothing_is_deleted(c, monkeypatch):
    w = _world()

    def refuse(*a, **k):
        raise jrepo.DeletionJournalError("sin respaldo")
    monkeypatch.setattr(jrepo, "begin", refuse)
    r = _del_bus(c, w["a"])
    assert r.status_code == 303 and r.headers["location"].endswith("aviso=eliminacion_incompleta")
    assert brepo.get(w["a"]) is not None and orepo.get(w["shared_app_a"]) is not None and grepo.get(w["a"]) is not None and jrepo.get(w["a"]) is None


def test_spaces_deletion_failure_rolls_back_locally_and_the_retry_completes(c, remote):
    remote.enabled = True
    w = _world()
    a = w["a"]
    remote.delete_fails = True
    assert _del_bus(c, a).status_code == 303
    assert brepo.get(a) is not None and orepo.get(w["shared_app_a"]) is not None                                           # el borrado de Spaces no se confirmó: nada quedó a medias entre local y remoto
    assert jrepo.get(a)["estado"] == "fallida" and jrepo.get(a)["falla"]["paso"] == "onboardings"
    remote.delete_fails = False
    assert _del_bus(c, a).status_code == 303
    assert brepo.get(a) is None and f"{w['shared_app_a']}.json" not in remote.objects                                      # y el reintento llega al final también en Spaces
    assert jrepo.get(a)["estado"] == "completada" and jrepo.get(a)["intentos"] == 2
    assert any(name == f"{a}.json" for name in remote.pushes)                                                              # el registro de auditoría también se respaldó


def test_local_persistence_mode_cascades_without_touching_spaces(c, remote, monkeypatch):
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    w = _world()
    remote.pushes.clear()
    assert _del_bus(c, w["a"]).status_code == 303
    assert brepo.get(w["a"]) is None and orepo.get(w["shared_app_a"]) is None and jrepo.get(w["a"])["estado"] == "completada"
    assert remote.pushes == []


def test_deletion_plan_counts_only_what_belongs_to_the_busqueda():
    w = _world()
    assert svc.busqueda_deletion_plan(brepo.get(w["a"])) == {"postulaciones": 2, "entrevistas": 2, "onboardings": 1, "evaluaciones": 1, "grilla": 1, "publicaciones_desvinculadas": 1}
    assert svc.busqueda_deletion_plan(brepo.get(w["b"]))["onboardings"] == 1 and svc.busqueda_deletion_plan(brepo.get(w["b"]))["publicaciones_desvinculadas"] == 0


# ═══════════════════ 8 (corrección): también los procesos nacidos de una solicitud de Operaciones ya tomada ═══════════════════
SOLICITUD_SID = "66666666-6666-4666-8666-666666666666"


def _process(origin: str, closed: bool) -> str:
    """Proceso `directo` (alta de CCHH) o `solicitud` (enviada por Operaciones y TOMADA por CCHH), activo o cerrado, con datos en cada dependiente."""
    if origin == "directo":
        bid = _manual(nombre_puesto=f"Proceso {origin}")
    else:
        bid = send_solicitud(sid=SOLICITUD_SID)["busqueda_id"]
        svc.take_solicitud(bid, brepo.get(bid)["revision"], CCHH_EMAIL)
    pid, app_id = _ingreso(bid, first="Ana")
    solo_pid, solo_app = _candidate(bid, "Bruno")
    svc.move_application_stage(bid, solo_app, "Entrevista CCHH", None, actor=CCHH_EMAIL)
    _seed_scoring(bid, solo_app, solo_pid)
    if closed:
        svc.close_busqueda(bid, [], actor=CCHH_EMAIL)
        assert brepo.get(bid)["estado"] == "cerrado"
    return bid


@pytest.mark.parametrize("closed", [False, True], ids=["activo", "cerrado"])
@pytest.mark.parametrize("origin", ["directo", "solicitud"])
def test_every_process_can_be_deleted_whatever_its_origin_or_state(c, operations_users, origin, closed):
    bid = _process(origin, closed)
    other = _manual(nombre_puesto="Otro")
    other_pid, other_app = _ingreso(other, first="Zoe")
    page = c.get(f"/cchh/busquedas/{bid}").text
    form = re.search(rf'<form method="post" action="/cchh/busquedas/{bid}/eliminar-completa"[^>]*>', page)
    assert form and 'class="btn-danger btn-danger--strong"' in page[form.end():form.end() + 200] and "TODOS los registros asociados" in form.group(0)      # botón visible y conectado a la acción real
    assert ("La solicitud original de Operaciones no se borra" in form.group(0)) == (origin == "solicitud")                                            # la advertencia extra solo donde aplica
    other_before, profiles_before = brepo.get(other), {p["profile_id"]: p for p in profile_repository.list()}
    r = _del_bus(c, bid)
    assert r.status_code == 303 and r.headers["location"] == "/cchh/busquedas"
    assert [e for e in erepo.list() if e.get("busqueda_id") == bid] == [] and [o for o in orepo.list() if o.get("busqueda_id") == bid] == [] and grepo.get(bid) is None and evrepo.list_for_busqueda(bid) == []
    assert brepo.get(other) == other_before and orepo.get(other_app) is not None and {p["profile_id"]: p for p in profile_repository.list()} == profiles_before   # nada ajeno, perfiles intactos
    doc = brepo.get(bid)
    if origin == "directo":
        assert doc is None
    else:
        sol = doc["solicitud"]                                                                                              # la solicitud original SE CONSERVA y se actualiza según su contrato
        assert doc["candidatos"] == [] and doc["estado"] == "cerrado" and doc["requerimiento"]["nombre_puesto"]
        assert sol["estado"] == "cancelada" and sol["cancelada_por"] == CCHH_EMAIL and sol["motivo_cancelacion"] == brepo.MOTIVO_PROCESO_ELIMINADO and sol["proceso_eliminado"]["por"] == CCHH_EMAIL
        assert sol["historial"][-1]["transicion"] == "proceso_eliminado" and sol["historial"][-1]["actor_tipo"] == "cchh"
        assert not any(str(h["transicion"]).startswith("candidato_") for h in sol["historial"]) and any(h["transicion"] == "tomar" for h in sol["historial"])    # sin datos de candidatos; el ciclo de recepción queda
        assert [b["busqueda_id"] for b in svc.list_busquedas_gestionadas()] == [other]                                      # ya no es un proceso de CCHH
        view = svc.solicitud_view(doc)                                                                                      # lo que ve Operaciones
        assert view["estado_solicitud"] == "cancelada" and view["solo_lectura"] and view["motivo_cancelacion"] == brepo.MOTIVO_PROCESO_ELIMINADO
        assert "proceso_eliminado" not in view and "candidatos" not in view and not any(h["transicion"].startswith("candidato_") for h in view["historial"])      # la proyección conserva su lista blanca
        page = c.get(f"/cchh/busquedas/{bid}").text
        assert "Proceso eliminado por Capital Humano" in page and "/eliminar-completa" not in page                         # el aviso de CCHH y sin botón (ya no hay proceso)
    again = _del_bus(c, bid)                                                                                                # repetición idempotente
    assert again.status_code in (303, 404) and (origin == "directo" or again.status_code == 303)
    assert jrepo.get(bid)["estado"] == "completada"


def test_a_solicitud_that_cchh_never_took_or_already_cancelled_is_not_a_process(c, operations_users):
    pending = send_solicitud(sid="77777777-7777-4777-8777-777777777777")["busqueda_id"]
    before = brepo.get(pending)
    r = _del_bus(c, pending)
    assert r.status_code == 303 and r.headers["location"] == f"/cchh/busquedas/{pending}?aviso=eliminacion_no_permitida" and brepo.get(pending) == before and jrepo.get(pending) is None
    assert "/eliminar-completa" not in c.get(f"/cchh/busquedas/{pending}").text


def test_the_retired_solicitud_cannot_be_reached_by_operations_actions(c, operations_users):
    bid = _process("solicitud", False)
    assert _del_bus(c, bid).status_code == 303
    sol_before = brepo.get(bid)
    with pytest.raises(brepo.BusquedaInvalidTransitionError):
        svc.cancel_solicitud(bid, sol_before["revision"], "ops@danwor.com")                                                  # Operaciones no puede (re)cancelarla: CCHH la tomó
    assert brepo.get(bid) == sol_before


def test_the_authorization_of_the_endpoint_is_the_same_for_every_origin(c, operations_users):
    bid = _process("solicitud", False)
    assert session_client("stranger@danwor.com").post(f"/cchh/busquedas/{bid}/eliminar-completa").status_code in (302, 303, 401, 403)
    assert TestClient(app, follow_redirects=False).post(f"/cchh/busquedas/{bid}/eliminar-completa").status_code in (302, 303, 401, 403)
    assert brepo.get(bid)["solicitud"]["estado"] == "en_gestion" and jrepo.get(bid) is None


def test_a_partial_failure_on_a_solicitud_process_keeps_it_visible_and_resumable(c, operations_users, monkeypatch):
    bid = _process("solicitud", False)
    real = brepo.retire_solicitud_process
    monkeypatch.setattr(brepo, "retire_solicitud_process", lambda *a, **k: (_ for _ in ()).throw(brepo.BusquedaStorageError("falla simulada")))
    r = _del_bus(c, bid)
    assert r.status_code == 303 and r.headers["location"].endswith("aviso=eliminacion_incompleta")
    assert brepo.get(bid)["solicitud"]["estado"] == "en_gestion" and jrepo.get(bid)["estado"] == "fallida" and jrepo.get(bid)["falla"]["paso"] == "busqueda"
    monkeypatch.setattr(brepo, "retire_solicitud_process", real)
    assert _del_bus(c, bid).status_code == 303
    assert brepo.get(bid)["solicitud"]["estado"] == "cancelada" and jrepo.get(bid)["estado"] == "completada" and jrepo.get(bid)["intentos"] == 2
