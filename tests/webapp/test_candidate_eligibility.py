"""Regla transversal de elegibilidad para NUEVAS postulaciones: «No reconsiderar» (clave semántica estable). Filtra el selector, se exige en el servidor y preserva postulaciones e historial existentes (sin bajas automáticas)."""
from __future__ import annotations

import re

import pytest

from webapp.services import busqueda_repository as brepo, candidate_eligibility as ce, human_capital_service as svc, profile_repository
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, env, remote  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import session_client, operations_users  # noqa: F401
from tests.webapp.test_pipeline_interview_shortcut import _manual  # noqa: F401


@pytest.fixture()
def c():
    return session_client(CCHH_EMAIL)


def _profile(first, last="Test", mark=None) -> str:
    return profile_repository.create({"first_name": first, "last_name": last, "email": f"{first.lower()}@danwor.com", "potential_mark": mark})["profile_id"]


def _add(c, bid, pid, **kw):
    return c.post(f"/cchh/busquedas/{bid}/candidatos", data={"profile_id": pid}, **kw)


def _apps(bid) -> list[dict]:
    return brepo.get(bid)["candidatos"]


# ═══════════════════ la clave semántica (puro) ═══════════════════
@pytest.mark.parametrize("mark", ["No reconsiderar", "NO RECONSIDERAR", "  no   reconsiderar  ", "No reconsiderar.", "no-reconsiderar", "No Reconsiderár", "No reconsiderar"])
def test_variants_of_the_visible_text_resolve_to_the_same_semantic_key(mark):
    assert ce.potencial_key(mark) == ce.NO_RECONSIDERAR == "no_reconsiderar"
    assert ce.blocked_reason({"potential_mark": mark}) == "no_reconsiderar" and not ce.is_eligible({"potential_mark": mark})


@pytest.mark.parametrize("mark", ["Terna futura", "", None, "Reconsiderar", "No", "Potencial alto", "no reconsiderar nunca más"])
def test_other_marks_do_not_block(mark):
    assert ce.potencial_key(mark) is None and ce.is_eligible({"potential_mark": mark})
    assert ce.is_eligible({}) and ce.is_eligible(None) and ce.message_for(None) == ""


def test_the_catalog_health_check_tells_when_the_blocking_value_is_missing():
    assert ce.catalog_exposes_blocking_key(["Terna futura", "NO RECONSIDERAR"]) and not ce.catalog_exposes_blocking_key(["Terna futura", "Descartado"]) and not ce.catalog_exposes_blocking_key(None)


# ═══════════════════ selector y servidor ═══════════════════
def test_the_picker_does_not_offer_blocked_candidates_but_tells_how_many_are_hidden(c):
    bid = _manual()
    ok1, ok2 = _profile("Ana"), _profile("Beto", mark="Terna futura")
    _profile("Zeta", "Oculto", mark="No reconsiderar")
    _profile("Yara", "Oculta", mark="NO RECONSIDERAR")
    page = c.get(f"/cchh/busquedas/{bid}").text
    rows = re.findall(r'data-picker-row data-search="[^"]*">\s*<td class="left"><div class="dw-idcell"><span class="dw-idcell__name">([^<]+)</span>', page)
    assert sorted(rows) == ["Ana Test", "Beto Test"] and "Oculto" not in page and "Oculta" not in page                    # no se nombra a quienes están bloqueados
    assert "2 candidatos marcados «No reconsiderar» no se ofrecen para nuevos procesos." in page
    assert {p["profile_id"] for p in svc.profiles_available_for_busqueda(brepo.get(bid))} == {ok1, ok2} and svc.profiles_blocked_for_busqueda_count(brepo.get(bid)) == 2


def test_the_singular_hint_and_no_hint_when_nobody_is_blocked(c):
    bid = _manual()
    _profile("Ana")
    assert "data-picker-blocked" not in c.get(f"/cchh/busquedas/{bid}").text
    _profile("Zeta", mark="No reconsiderar")
    assert "1 candidato marcado «No reconsiderar» no se ofrece para nuevos procesos." in c.get(f"/cchh/busquedas/{bid}").text


def test_the_server_rejects_a_blocked_candidate_even_if_the_request_is_forged_and_says_why(c):
    bid = _manual()
    blocked = _profile("Zeta", mark="NO RECONSIDERAR")
    r = _add(c, bid, blocked, headers={"HX-Request": "true"})                                                              # POST directo, sin pasar por el selector
    assert r.status_code == 200 and _apps(bid) == []
    assert 'data-error-kind="eligibility"' in r.text and "marcado «No reconsiderar»" in r.text and "No se pudo agregar" in r.text
    plain = _add(c, bid, blocked)
    assert plain.status_code == 303 and plain.headers["location"] == f"/cchh/busquedas/{bid}?aviso=no_elegible" and _apps(bid) == []
    assert "no es elegible para nuevos procesos" in c.get(plain.headers["location"]).text
    with pytest.raises(svc.CandidateNotEligibleError) as exc:
        svc.add_candidate_to_busqueda(bid, blocked, actor=CCHH_EMAIL)
    assert exc.value.reason == "no_reconsiderar" and "No reconsiderar" in str(exc.value) and isinstance(exc.value, ValueError)
    assert brepo.historial_proceso_of(brepo.get(bid)) == []                                                               # un intento bloqueado no deja rastro en el historial


def test_eligible_candidates_are_added_normally_and_only_the_blocked_one_is_refused(c):
    bid = _manual()
    ok = _profile("Ana", mark="Terna futura")
    assert _add(c, bid, ok, headers={"HX-Request": "true"}).status_code == 200 and [a["profile_id"] for a in _apps(bid)] == [ok]
    later = _profile("Beto", mark="No reconsiderar")
    assert _add(c, bid, later, headers={"HX-Request": "true"}).status_code == 200 and len(_apps(bid)) == 1


def test_unmarking_a_candidate_makes_them_eligible_again(c):
    bid = _manual()
    pid = _profile("Beto", mark="No reconsiderar")
    assert not ce.is_eligible(profile_repository.get(pid))
    stored = profile_repository.get(pid)
    stored_fields = {k: stored.get(k) for k in ("first_name", "last_name", "email", "phone", "linkedin_url", "location", "profile_skills", "seniority", "source", "tags", "notes", "potential_rating")}
    profile_repository.update(pid, stored["revision"], {**stored_fields, "potential_mark": None})
    assert ce.is_eligible(profile_repository.get(pid))
    assert _add(c, bid, pid, headers={"HX-Request": "true"}).status_code == 200 and [a["profile_id"] for a in _apps(bid)] == [pid]


# ═══════════════════ lo histórico se preserva ═══════════════════
def test_existing_applications_and_history_are_preserved_when_a_candidate_is_marked_later(c):
    bid = _manual()
    pid = _profile("Ana")
    assert _add(c, bid, pid, headers={"HX-Request": "true"}).status_code == 200
    app_id = _apps(bid)[0]["application_id"]
    svc.move_application_stage(bid, app_id, "Entrevista CCHH", None, actor=CCHH_EMAIL)
    before = brepo.get(bid)
    stored = profile_repository.get(pid)
    profile_repository.update(pid, stored["revision"], {**{k: stored.get(k) for k in ("first_name", "last_name", "email", "phone", "linkedin_url", "location", "profile_skills", "seniority", "source", "tags", "notes", "potential_rating")}, "potential_mark": "No reconsiderar"})
    assert brepo.get(bid) == before                                                                                       # marcar no baja, mueve ni reescribe ninguna postulación
    page = c.get(f"/cchh/busquedas/{bid}").text
    assert "Ana Test" in page and f"/candidatos/{app_id}/etapa" in page                                                    # sigue visible y operable en su proceso
    svc.move_application_stage(bid, app_id, "Entrevista Líder", None, actor=CCHH_EMAIL)                                   # el pipeline existente sigue funcionando
    assert brepo.get(bid)["candidatos"][0]["etapa"] == "Entrevista Líder"
    assert any(p["profile_id"] == pid for p in svc.list_profiles())                                                      # y el candidato sigue en el Banco
    r = _add(c, bid, pid, headers={"HX-Request": "true"})                                                                 # pero no puede volver a incorporarse como postulación nueva
    assert 'data-error-kind="eligibility"' in r.text and len(_apps(bid)) == 1


def test_a_deleted_candidate_keeps_its_own_message_and_unknown_ids_are_rejected(c):
    bid = _manual()
    gone = _profile("Baja", mark="No reconsiderar")
    profile_repository.soft_delete(gone, actor=CCHH_EMAIL)
    with pytest.raises(ValueError) as exc:
        svc.add_candidate_to_busqueda(bid, gone, actor=CCHH_EMAIL)
    assert "eliminado del Banco" in str(exc.value) and not isinstance(exc.value, svc.CandidateNotEligibleError)
    assert _add(c, bid, "no-existe", headers={"HX-Request": "true"}).status_code == 200 and _apps(bid) == []
