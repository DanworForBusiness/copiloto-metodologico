"""
test_commercial_licensing_priority_routes.py — Commercial Licensing,
Director Requests §5-§8: contrato HTTP de
`GET /comercial/data/licensing-priority` +
`POST /comercial/licensing/priority/defer` +
`POST /comercial/licensing/priority/restore`. Repository REAL (no stub) —
aislado vía `config.BASE_DIR`/`tmp_path` (MISMO patrón que
test_commercial_licensing_priority_repository.py) — estas rutas no tocan
Odoo/`CommercialService` en absoluto, así que no hace falta mockear ese
boundary acá."""
from __future__ import annotations

import json

import config
import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.services import auth_service
from webapp.services import commercial_licensing_priority_repository as repo

auth_service.ALLOWED_USERS = "ALL"
# MISMO CSV compartido que test_commercial_hub_foundation.py/
# test_commercial_odoo_routes.py (ver sus propios comentarios): "asignación
# de módulo, la última que se ejecute gana" — reusar un email YA incluido
# en ese CSV compartido (en vez de inventar uno propio que otro archivo
# podría pisar) evita el riesgo de raza entre módulos de test.
auth_service.COMMERCIAL_ALLOWED_USERS = "commercial-test@danwor.com,commercial-odoo-test@danwor.com"

_SESSION = auth_service.create_session({
    "email": "commercial-test@danwor.com", "name": "Priority Test",
    "first_name": "Priority", "family_name": "Test",
})


@pytest.fixture(autouse=True)
def _isolated_base_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: False)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(repo.remote_store, "delete_path", lambda path: True)


def _client() -> TestClient:
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, _SESSION)
    return c


# ============================================================================
# Auth — MISMO gate que el resto de Comercial
# ============================================================================

def test_anonymous_is_redirected_on_all_three_routes():
    anon = TestClient(app)
    assert anon.get("/comercial/data/licensing-priority", follow_redirects=False).status_code == 303
    assert anon.post("/comercial/licensing/priority/defer", data={"account_id": 1},
                      follow_redirects=False).status_code == 303
    assert anon.post("/comercial/licensing/priority/restore", data={"account_id": 1},
                      follow_redirects=False).status_code == 303


def test_unlisted_user_is_denied_with_403():
    unlisted = TestClient(app)
    unlisted.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session(
        {"email": "unlisted@danwor.com", "name": "U", "first_name": "U", "family_name": "U"}))
    assert unlisted.get("/comercial/data/licensing-priority").status_code == 403
    assert unlisted.post("/comercial/licensing/priority/defer", data={"account_id": 1}).status_code == 403


# ============================================================================
# W — round-trip HTTP completo: defer -> list refleja -> restore -> list ya
# no lo refleja. "shared/server-side" probado a nivel HTTP, no sólo repo.
# ============================================================================

def test_w_defer_then_list_then_restore_round_trip():
    client = _client()
    empty = client.get("/comercial/data/licensing-priority").json()
    assert empty == {"state": "ok", "deferred_account_ids": []}

    defer_resp = client.post("/comercial/licensing/priority/defer", data={"account_id": 42})
    assert defer_resp.json() == {"state": "ok"}

    after_defer = client.get("/comercial/data/licensing-priority").json()
    assert after_defer == {"state": "ok", "deferred_account_ids": [42]}

    restore_resp = client.post("/comercial/licensing/priority/restore", data={"account_id": 42})
    assert restore_resp.json() == {"state": "ok"}

    after_restore = client.get("/comercial/data/licensing-priority").json()
    assert after_restore == {"state": "ok", "deferred_account_ids": []}


def test_s_state_survives_a_fresh_client_ie_a_new_session():
    # "Refresh/relogin" a nivel HTTP: un TestClient nuevo (cookies nuevas,
    # sesión nueva) sigue viendo el estado — server-side de verdad, nunca
    # algo atado a una sesión de browser/cookie puntual.
    first_client = _client()
    first_client.post("/comercial/licensing/priority/defer", data={"account_id": 7})

    second_client = _client()
    resp = second_client.get("/comercial/data/licensing-priority").json()
    assert resp["deferred_account_ids"] == [7]


def test_restore_of_never_deferred_account_is_a_no_op_success():
    client = _client()
    resp = client.post("/comercial/licensing/priority/restore", data={"account_id": 999})
    assert resp.json() == {"state": "ok"}


def test_defer_persists_the_identity_of_the_acting_user():
    client = _client()
    client.post("/comercial/licensing/priority/defer", data={"account_id": 42})
    saved = json.loads(repo._record_path(42).read_text(encoding="utf-8"))
    assert saved["deferred_by"] == "commercial-test@danwor.com"


def test_multiple_accounts_are_independent_over_http():
    client = _client()
    client.post("/comercial/licensing/priority/defer", data={"account_id": 1})
    client.post("/comercial/licensing/priority/defer", data={"account_id": 2})
    resp = client.get("/comercial/data/licensing-priority").json()
    assert sorted(resp["deferred_account_ids"]) == [1, 2]
    client.post("/comercial/licensing/priority/restore", data={"account_id": 1})
    resp2 = client.get("/comercial/data/licensing-priority").json()
    assert resp2["deferred_account_ids"] == [2]
