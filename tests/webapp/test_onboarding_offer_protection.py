"""«Oferta cerrada» de Onboarding es un dato SENSIBLE: MISMA política que «Remuneración pretendida» (`HC_SENSITIVE_DATA_ALLOWED_USERS`, denegación por defecto). Sin permiso no se ve, no se busca, no se edita y no se pisa."""
from __future__ import annotations

import re

import pytest

from webapp.services import auth_service, human_capital_service as svc, onboarding_repository as orepo, profile_repository
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, env, remote  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import session_client, operations_users  # noqa: F401
from tests.webapp.test_pipeline_interview_shortcut import _manual  # noqa: F401

PRIVILEGED = "cchh.sensible@danwor.com"
OFFER = "USD 2.400 netos"


@pytest.fixture(autouse=True)
def two_levels(monkeypatch):
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", f"{CCHH_EMAIL},{PRIVILEGED}")
    monkeypatch.setattr(auth_service, "HC_SENSITIVE_DATA_ALLOWED_USERS", PRIVILEGED)              # CCHH_EMAIL está admitido en Capital Humano pero NO figura en la lista de datos sensibles


@pytest.fixture()
def plain():
    return session_client(CCHH_EMAIL)


@pytest.fixture()
def privileged():
    return session_client(PRIVILEGED)


def _onboarding(offer=OFFER) -> str:
    bid = _manual()
    pid = profile_repository.create({"first_name": "Ana", "last_name": "Ingreso", "email": "ana@danwor.com"})["profile_id"]
    app_id = svc.add_candidate_to_busqueda(bid, pid, actor=CCHH_EMAIL)["candidatos"][-1]["application_id"]
    svc.move_application_stage(bid, app_id, "Finalizado", "Ingreso", actor=CCHH_EMAIL)
    if offer:
        orepo.update(app_id, 1, {"fecha_ingreso": "2026-10-23", "oferta_cerrada": offer, "area": None})
    return app_id


def _edit(c, app_id, **over):
    revision = orepo.get(app_id)["revision"]
    return c.post(f"/cchh/onboarding/{app_id}/editar", data={"fecha_ingreso": "2026-11-02", "area": "", "expected_revision": str(revision), **over})


# ═══════════════════ sin permiso: no se ve ═══════════════════
def test_without_permission_the_offer_is_masked_and_never_reaches_the_html(plain):
    app_id = _onboarding()
    page = plain.get(f"/cchh/onboarding/{app_id}/editar").text
    assert 'id="onb-oferta" type="text" value="****" readonly disabled' in page and "Dato restringido: solo lo ven y editan las personas autorizadas." in page
    assert 'name="oferta_cerrada"' not in page and OFFER not in page and "2.400" not in page                       # ni valor ni campo editable
    frag = plain.post(f"/cchh/onboarding/{app_id}/checklist/dni", data={"checked": "1"}, headers={"HX-Request": "true"}).text
    assert OFFER not in frag and "2.400" not in frag                                                                # tampoco en el fragmento del checklist
    listing = plain.get("/cchh/onboarding", headers={"HX-Request": "true"}).text
    assert OFFER not in listing and "2.400" not in listing


def test_the_mask_does_not_reveal_whether_there_is_a_value_at_all(plain):
    empty = _onboarding(offer=None)
    assert 'value="****" readonly disabled' in plain.get(f"/cchh/onboarding/{empty}/editar").text                    # igual con o sin valor guardado


def test_with_permission_the_offer_is_shown_and_editable(privileged):
    app_id = _onboarding()
    page = privileged.get(f"/cchh/onboarding/{app_id}/editar").text
    assert f'id="onb-oferta" name="oferta_cerrada" type="text" value="{OFFER}"' in page and "Dato restringido" not in page


# ═══════════════════ sin permiso: no se busca ═══════════════════
def test_the_list_search_cannot_be_used_to_find_or_probe_the_offer_without_permission(plain, privileged):
    app_id = _onboarding()
    query = lambda c, q: c.get(f"/cchh/onboarding?q={q}", headers={"HX-Request": "true"}).text
    assert "Ana Ingreso" in query(privileged, "usd 2.400") and "Ana Ingreso" in query(privileged, "NETOS")             # con permiso, la oferta es buscable (como antes)
    assert "Ana Ingreso" not in query(plain, "usd 2.400") and "Ana Ingreso" not in query(plain, "netos")                # sin permiso no es un canal lateral para deducirla
    assert "Ana Ingreso" in query(plain, "ana") and app_id                                                           # el resto de la búsqueda no cambia


# ═══════════════════ sin permiso: no se edita ni se pisa ═══════════════════
def test_without_permission_a_forged_offer_is_discarded_and_the_stored_one_is_preserved(plain):
    app_id = _onboarding()
    r = _edit(plain, app_id, oferta_cerrada="USD 99.999 (forzado)")
    assert r.status_code == 303
    stored = orepo.get(app_id)
    assert stored["oferta_cerrada"] == OFFER and stored["fecha_ingreso"] == "2026-11-02"                              # lo demás sí se guarda; la oferta no se toca
    r2 = _edit(plain, app_id, oferta_cerrada="")                                                                    # tampoco se puede borrar por omisión
    assert r2.status_code == 303 and orepo.get(app_id)["oferta_cerrada"] == OFFER


def test_without_permission_a_forged_offer_is_not_stored_when_there_was_none(plain):
    app_id = _onboarding(offer=None)
    _edit(plain, app_id, oferta_cerrada="USD 1 forzado")
    assert orepo.get(app_id)["oferta_cerrada"] is None


def test_a_validation_error_re_render_never_echoes_the_offer(plain):
    app_id = _onboarding()
    r = _edit(plain, app_id, fecha_ingreso="no-es-fecha", oferta_cerrada="USD 77 reenviado")
    assert r.status_code == 200 and "fecha de ingreso es obligatoria" in r.text and "USD 77" not in r.text and OFFER not in r.text and 'name="oferta_cerrada"' not in r.text
    assert orepo.get(app_id)["oferta_cerrada"] == OFFER


def test_with_permission_the_offer_can_be_changed_and_cleared(privileged):
    app_id = _onboarding()
    assert _edit(privileged, app_id, oferta_cerrada="  USD 3.000  ").status_code == 303 and orepo.get(app_id)["oferta_cerrada"] == "USD 3.000"
    assert _edit(privileged, app_id, oferta_cerrada="").status_code == 303 and orepo.get(app_id)["oferta_cerrada"] is None


def test_ticking_the_checklist_never_touches_the_offer(plain):
    app_id = _onboarding()
    plain.post(f"/cchh/onboarding/{app_id}/checklist/dni", data={"checked": "1"}, headers={"HX-Request": "true"})
    assert orepo.get(app_id)["oferta_cerrada"] == OFFER and orepo.get(app_id)["checklist"]["dni"] is True


# ═══════════════════ contrato del servicio: denegación por defecto ═══════════════════
def test_the_service_contract_is_fail_closed_by_default():
    app_id = _onboarding()
    doc = orepo.get(app_id)
    assert svc.onboarding_view(doc)["oferta_cerrada"] == "****" and svc.onboarding_view(doc, True)["oferta_cerrada"] == OFFER                 # sin indicar permiso: enmascarada
    assert [o["oferta_cerrada"] for o in svc.list_onboardings_view()] == ["****"] and [o["oferta_cerrada"] for o in svc.list_onboardings_view(True)] == [OFFER]
    assert svc.mask_onboarding_offer(doc, False)["oferta_cerrada"] == "****" and doc["oferta_cerrada"] == OFFER                               # la máscara es una copia: no altera lo persistido
    shaped = svc._shape_onboarding_fields({"fecha_ingreso": "2026-11-02", "oferta_cerrada": "otro"}, existing=doc)
    assert shaped["oferta_cerrada"] == OFFER
    assert svc._shape_onboarding_fields({"fecha_ingreso": "2026-11-02", "oferta_cerrada": "otro"}, existing=doc, allow_sensitive=True)["oferta_cerrada"] == "otro"
    hay = svc._onboarding_search_haystack({"oferta_cerrada": OFFER, "profile": {}, "progress": {}})
    assert "2.400" not in hay and "2.400" in svc._onboarding_search_haystack({"oferta_cerrada": OFFER, "profile": {}, "progress": {}}, include_offer=True)
