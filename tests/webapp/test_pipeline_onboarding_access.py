"""Acceso contextual al Onboarding desde las tarjetas Finalizado · Ingreso del pipeline: solo para ese resultado, hacia el Onboarding EXISTENTE, sin crear nada al navegar ni al renderizar."""
from __future__ import annotations

import re

import pytest

import config
from webapp.services import busqueda_repository as brepo, human_capital_service as svc, onboarding_repository as orepo, profile_repository
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, env, remote  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import session_client, operations_users  # noqa: F401
from tests.webapp.test_pipeline_interview_shortcut import _manual, _card  # noqa: F401


@pytest.fixture()
def c():
    return session_client(CCHH_EMAIL)


def _candidate(bid, first) -> str:
    pid = profile_repository.create({"first_name": first, "last_name": "Test", "email": f"{first.lower()}@danwor.com"})["profile_id"]
    return svc.add_candidate_to_busqueda(bid, pid, actor=CCHH_EMAIL)["candidatos"][-1]["application_id"]


def _finish(bid, app_id, etapa="Finalizado", resultado=None):
    svc.move_application_stage(bid, app_id, etapa, resultado, actor=CCHH_EMAIL)


def _onboarding_files() -> list:
    d = config.BASE_DIR / "human-capital" / "onboarding"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def _link(card: str) -> str | None:
    m = re.search(r'<a href="([^"]+)" class="table-action-btn chart-tooltip-anchor busqueda-cand__onboarding" aria-label="Ir al Onboarding">', card)
    return m.group(1) if m else None


def test_only_finalizado_with_ingreso_shows_the_onboarding_access_pointing_to_the_existing_record(c):
    bid = _manual()
    apps = {n: _candidate(bid, n) for n in ("Ana", "Bruno", "Carla", "Diego", "Elena", "Fede")}
    _finish(bid, apps["Ana"], resultado="Ingreso")
    _finish(bid, apps["Bruno"], resultado="Rechazado")
    _finish(bid, apps["Carla"], resultado="Desistió")
    _finish(bid, apps["Diego"])                                                                              # Finalizado sin resultado todavía
    _finish(bid, apps["Elena"], etapa="Decisión Final")
    page = c.get(f"/cchh/busquedas/{bid}").text
    assert _link(_card(page, "Ana Test")) == f"/cchh/onboarding/{apps['Ana']}/editar"
    for name in ("Bruno", "Carla", "Diego", "Elena", "Fede"):
        assert "busqueda-cand__onboarding" not in _card(page, f"{name} Test"), name                           # ni otros resultados, ni otras etapas
    card = _card(page, "Ana Test")
    assert "<p>Ir al Onboarding</p>" in card and 'd="m9 14 2 2 4-4"' in card                                   # SVG clipboard-check + tooltip del Hub
    assert card.index("<select") < card.index("busqueda-cand__contact") < card.index("busqueda-cand__onboarding") < card.index('aria-label="Quitar del proceso"')   # etapa → contactar → onboarding → quitar
    assert c.get(_link(card)).status_code == 200 and "Ana" in c.get(_link(card)).text


def test_rendering_and_following_the_link_never_creates_records(c):
    bid = _manual()
    app_id = _candidate(bid, "Ana")
    _finish(bid, app_id, resultado="Ingreso")
    files, onboardings = _onboarding_files(), len(orepo.list())
    for _ in range(3):
        page = c.get(f"/cchh/busquedas/{bid}")
        assert page.status_code == 200
        assert c.get(_link(_card(page.text, "Ana Test"))).status_code == 200
    assert _onboarding_files() == files and len(orepo.list()) == onboardings == 1


def test_without_an_existing_onboarding_there_is_no_link_and_nothing_is_created(c, monkeypatch):
    bid = _manual()
    app_id = _candidate(bid, "Ana")
    _finish(bid, app_id, resultado="Ingreso")
    (config.BASE_DIR / "human-capital" / "onboarding" / f"{app_id}.json").unlink()                           # anomalía: el Onboarding no existe
    page = c.get(f"/cchh/busquedas/{bid}").text
    assert "busqueda-cand__onboarding" not in _card(page, "Ana Test") and _onboarding_files() == []           # no se ofrece un enlace a algo que no está, ni se crea al renderizar
    with monkeypatch.context() as m:                                                                          # y un fallo de lectura no rompe el tablero
        m.setattr(orepo, "get", lambda *a: (_ for _ in ()).throw(orepo.OnboardingStorageError("disco")))
        failing = c.get(f"/cchh/busquedas/{bid}")
    assert failing.status_code == 200 and "Ana Test" in failing.text and "busqueda-cand__onboarding" not in failing.text


def test_the_access_survives_htmx_refreshes_and_leaves_the_existing_actions_in_place(c):
    bid = _manual()
    app_id = _candidate(bid, "Ana")
    _finish(bid, app_id, resultado="Ingreso")
    r = c.post(f"/cchh/busquedas/{bid}/candidatos/{app_id}/etapa", data={"etapa": "Finalizado", "resultado_final": "Ingreso"}, headers={"HX-Request": "true"})   # sin cambios: el fragmento HTMX también lo trae
    card = _card(r.text, "Ana Test")
    assert _link(card) == f"/cchh/onboarding/{app_id}/editar" and 'name="resultado_final"' in card and 'aria-label="Contactar al candidato"' in card and 'aria-label="Quitar del proceso"' in card
    moved = c.post(f"/cchh/busquedas/{bid}/candidatos/{app_id}/etapa", data={"etapa": "Decisión Final", "resultado_final": ""}, headers={"HX-Request": "true"})
    assert "busqueda-cand__onboarding" not in moved.text                                                      # si deja de estar Finalizado · Ingreso, el acceso desaparece (el Onboarding no se toca)
    assert len(orepo.list()) == 1


def test_a_closed_process_keeps_only_the_onboarding_access_of_its_entrants(c):
    bid = _manual()
    ana, bruno = _candidate(bid, "Ana"), _candidate(bid, "Bruno")
    _finish(bid, ana, resultado="Ingreso")
    _finish(bid, bruno, resultado="Rechazado")
    svc.close_busqueda(bid, [], actor=CCHH_EMAIL)
    page = c.get(f"/cchh/busquedas/{bid}").text
    ana_card, bruno_card = _card(page, "Ana Test"), _card(page, "Bruno Test")
    assert _link(ana_card) == f"/cchh/onboarding/{ana}/editar" and "<select" not in ana_card and "Quitar del proceso" not in ana_card and "busqueda-cand__contact" not in ana_card
    assert "busqueda-cand__onboarding" not in bruno_card and "busqueda-cand__actions" not in bruno_card
