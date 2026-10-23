"""Quitar del proceso a un candidato con Onboarding (Finalizado · Ingreso) elimina ese Onboarding; no deja huérfanos y no toca nada más (Entrevista, Perfil del Banco, otros candidatos)."""
from __future__ import annotations

import logging
import re

import pytest

import config
from webapp.services import busqueda_repository as brepo, entrevista_repository as erepo, human_capital_service as svc, onboarding_repository as orepo, profile_repository
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, env, remote  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import session_client, operations_users  # noqa: F401
from tests.webapp.test_pipeline_interview_shortcut import _card, _manual  # noqa: F401


@pytest.fixture()
def c():
    return session_client(CCHH_EMAIL)


def _candidate(bid, first) -> tuple[str, str]:
    pid = profile_repository.create({"first_name": first, "last_name": "Test", "email": f"{first.lower()}@danwor.com"})["profile_id"]
    return pid, svc.add_candidate_to_busqueda(bid, pid, actor=CCHH_EMAIL)["candidatos"][-1]["application_id"]


def _ingreso(bid, first):
    pid, app_id = _candidate(bid, first)
    svc.move_application_stage(bid, app_id, "Finalizado", "Ingreso", actor=CCHH_EMAIL)
    assert orepo.get(app_id) is not None
    return pid, app_id


def _remove(c, bid, app_id):
    return c.post(f"/cchh/busquedas/{bid}/candidatos/{app_id}/eliminar", headers={"HX-Request": "true"})


def _file(app_id):
    return config.BASE_DIR / "human-capital" / "onboarding" / f"{app_id}.json"


def test_removing_an_ingreso_candidate_deletes_only_that_onboarding(c):
    bid = _manual()
    pid, app_id = _candidate(bid, "Ana")
    svc.move_application_stage(bid, app_id, "Entrevista Líder", None, actor=CCHH_EMAIL)                          # pasó por entrevista (queda su registro administrativo)…
    svc.move_application_stage(bid, app_id, "Finalizado", "Ingreso", actor=CCHH_EMAIL)                          # …y terminó en Ingreso: Onboarding creado
    assert orepo.get(app_id) is not None
    other_pid, other_app = _ingreso(bid, "Elena")
    leader_pid, leader_app = _candidate(bid, "Carla")
    svc.move_application_stage(bid, leader_app, "Entrevista Líder", None, actor=CCHH_EMAIL)
    assert erepo.find_by_profile_and_busqueda(pid, bid) is not None
    r = _remove(c, bid, app_id)
    assert r.status_code == 200 and r.text.count("busqueda-cand__onboarding") == 1 and f"/cchh/onboarding/{app_id}/" not in r.text           # la tarjeta de Ana salió del tablero; solo queda el acceso de Elena
    assert orepo.get(app_id) is None and not _file(app_id).exists()                                              # el Onboarding de la postulación quitada se eliminó
    assert orepo.get(other_app) is not None and _file(other_app).exists()                                       # el de otro ingresante sigue
    assert [a["application_id"] for a in brepo.get(bid)["candidatos"]] == [other_app, leader_app]
    assert profile_repository.get(pid) is not None and not profile_repository.get(pid).get("deleted_at")        # el Perfil del Banco no se toca
    assert erepo.find_by_profile_and_busqueda(pid, bid) is not None                                            # la entrevista administrativa tampoco (contrato existente)
    assert erepo.find_by_profile_and_busqueda(leader_pid, bid) is not None
    assert [o["application_id"] for o in orepo.list()] == [other_app]                                           # sin huérfanos: ningún Onboarding sin postulación


def test_removing_a_candidate_without_onboarding_works_and_touches_no_onboarding(c):
    bid = _manual()
    _, ingreso_app = _ingreso(bid, "Ana")
    _, in_pipeline = _candidate(bid, "Bruno")
    assert _remove(c, bid, in_pipeline).status_code == 200
    assert [a["application_id"] for a in brepo.get(bid)["candidatos"]] == [ingreso_app] and orepo.get(ingreso_app) is not None


def test_removing_twice_is_idempotent_and_never_fails(c):
    bid = _manual()
    _, app_id = _ingreso(bid, "Ana")
    assert _remove(c, bid, app_id).status_code == 200
    again = _remove(c, bid, app_id)                                                                             # la postulación ya no existe: respuesta controlada
    assert again.status_code == 200 and orepo.get(app_id) is None and orepo.list() == []
    assert orepo.delete(app_id) is False                                                                        # y el borrado del repositorio es idempotente


def test_a_rejected_removal_deletes_nothing(c):                                                                 # postulación inexistente en esa búsqueda: ApplicationNotFound
    bid = _manual()
    _, app_id = _ingreso(bid, "Ana")
    other = _manual(nombre_puesto="Otra")
    _remove(c, other, app_id)                                                                                   # postulación que no pertenece a esa búsqueda
    assert orepo.get(app_id) is not None


def test_a_storage_failure_on_the_onboarding_never_breaks_the_confirmed_removal(c, monkeypatch, caplog):
    bid = _manual()
    _, app_id = _ingreso(bid, "Ana")
    monkeypatch.setattr(orepo, "delete", lambda _id: (_ for _ in ()).throw(orepo.OnboardingStorageError("disco")))
    with caplog.at_level(logging.WARNING):
        r = _remove(c, bid, app_id)
    assert r.status_code == 200 and brepo.get(bid)["candidatos"] == []                                          # el quitado ya confirmado se mantiene
    assert "onboarding_huerfano_no_eliminado" in caplog.text and orepo.get(app_id) is not None                  # y el sobrante queda informado (borrado idempotente, reintentable)


def test_repository_delete_rolls_back_the_local_delete_when_the_remote_is_not_confirmed(remote):
    bid = _manual()
    _, app_id = _ingreso(bid, "Ana")
    remote.delete_fails, remote.enabled = True, True
    with pytest.raises(orepo.OnboardingRemoteOutcomeUnknownError):
        orepo.delete(app_id)
    assert _file(app_id).exists() and orepo.get(app_id) is not None                                             # rollback: local y remoto no divergen
    remote.delete_fails = False
    assert orepo.delete(app_id) is True and not _file(app_id).exists()


def test_the_confirmation_dialog_warns_about_the_onboarding_only_when_there_is_one(c):
    bid = _manual()
    _, ing = _ingreso(bid, "Ana")
    _, plain = _candidate(bid, "Bruno")
    page = c.get(f"/cchh/busquedas/{bid}").text
    msg = lambda name: re.search(r'data-dw-confirm-message="([^"]*)"', _card(page, name).split('aria-label="Quitar del proceso"')[1]).group(1)   # noqa: E731
    assert msg("Ana Test") == "¿Quitar a este candidato del proceso? También se eliminará su Onboarding (checklist y datos de ingreso). El CV del Banco no se elimina."
    assert msg("Bruno Test") == "¿Quitar a este candidato del proceso? El CV del Banco no se elimina."
