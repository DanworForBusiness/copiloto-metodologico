"""Punch de oportunidad ADAPTATIVO: el tipo sale SOLO de las señales del backend (`level_changed` / `contracts_changed`).
LEVEL → «Oportunidad de actualización» (vigente → candidato); EVIDENCE (misma calificación, cambian contratos) → «Actualización de evidencia
disponible» con CTA «Revisar cambios» y conteos verificables por clase; sin diferencias → sin punch. Todos los caminos van a la revisión gobernada."""
from __future__ import annotations

import pytest

from webapp.services import assessment_presenter as pres
from webapp.services import assessment_service as svc
from tests.webapp.test_governed_update_opportunity import _cand_profile, _env, _patch_env, _render_callout

GOV, EXT = "GOVERNED_SAP_APPLICATION_API", "EXTERNAL_CONTRACT_UNDETERMINED"
REQUEST_FORM = 'action="/copilot/governed-updates/request"'


def _view(monkeypatch, gov_level, gov_status, gov_class, cand_level, cand_class):
    _patch_env(monkeypatch, _env(gov_level, gov_status, gov_class))
    opp = svc.governed_update_opportunity("Contreras", "AM_Workforce", {"status": "DETERMINED", "level": cand_level}, _cand_profile(cand_class))
    return opp, pres._update_opportunity_view(opp)


def _html(up, approver):
    return _render_callout(up, adhoc_result_id="a" * 32, adhoc_stem="04_Modelado_y_Desarrollo", adhoc_approver=approver)


@pytest.mark.parametrize("case,args", [
    ("solo_nivel", ("ADECUADA", "DETERMINED", GOV, "BUENA", GOV)),
    ("nivel_y_contratos", (None, "NOT_DETERMINABLE", EXT, "BUENA", GOV)),
])
def test_a_level_change_keeps_the_update_opportunity_with_vigente_to_candidate(monkeypatch, case, args):
    opp, up = _view(monkeypatch, *args)
    assert opp["level_changed"] and up["kind"] == "LEVEL"
    html = _html(up, approver=True)
    assert 'data-asr-opp-kind="LEVEL"' in html and "Oportunidad de actualización del Assessment gobernado" in html
    assert "data-asr-opp-candidato" in html and "→" in html and "data-asr-opp-cta>Revisar y aprobar<" in html
    assert REQUEST_FORM in html


def test_same_level_with_contract_changes_presents_evidence_update_and_review_changes(monkeypatch):
    opp, up = _view(monkeypatch, "ADECUADA", "DETERMINED", EXT, "ADECUADA", GOV)
    assert not opp["level_changed"] and opp["contracts_changed"] and up["kind"] == "EVIDENCE"
    html = _html(up, approver=True)
    assert 'data-asr-opp-kind="EVIDENCE"' in html and "Actualización de evidencia disponible" in html
    assert "Oportunidad de actualización del Assessment gobernado" not in html and "Revisar y aprobar" not in html   # no anticipa aprobabilidad
    assert "data-asr-opp-cta>Revisar cambios<" in html and "La calificación se mantiene" in html and "data-asr-opp-candidato" not in html
    assert "data-asr-opp-evidence-msg" in html and "lo determina el dry-run" in html
    # explicación VERIFICABLE: conteos de hojas por clase (vigente → evaluación), sin inventar fuentes
    assert {(c["class"], c["vigente"], c["candidato"]) for c in up["contracts_diff"]} == {(EXT, 15, 0), (GOV, 0, 15)}
    assert "API de aplicación SAP gobernada: 0 → 15" in html
    assert REQUEST_FORM in html
    requester = _html(up, approver=False)
    assert "data-asr-opp-cta>Solicitar actualización<" in requester and REQUEST_FORM in requester


def test_no_relevant_difference_shows_no_punch(monkeypatch):
    opp, up = _view(monkeypatch, "ADECUADA", "DETERMINED", GOV, "ADECUADA", GOV)
    assert opp is None and up is None
    assert "data-asr-update-opportunity" not in _html(up, approver=True)


def test_an_open_request_keeps_pointing_to_its_governed_review(monkeypatch):
    opp, up = _view(monkeypatch, "ADECUADA", "DETERMINED", EXT, "ADECUADA", GOV)
    up["open_request_id"] = "b" * 32
    html = _html(up, approver=True)
    assert f'href="/copilot/governed-updates/{"b" * 32}?pack=datasphere_core" data-asr-open-request>Ver solicitud<' in html
