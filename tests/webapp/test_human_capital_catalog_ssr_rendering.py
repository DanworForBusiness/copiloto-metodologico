"""
test_human_capital_catalog_ssr_rendering.py — CCHH Catalog Foundation, follow-up:
las páginas SSR de Capital Humano (alta, edición, listado/filtro) tienen que
poblar sus `<select>` de campos gobernados (Seniority, Fuente) EXCLUSIVAMENTE
desde `human_capital_catalog_repository` (tokenless, sin Graph en el GET) --
nunca desde una lista hardcodeada en el template ni en el router. Cubre
también el marcador visual de "fuera de catálogo" en edición (migration-safe,
§6), que hasta este slice sólo existía para Seniority."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import auth_service, profile_repository
from webapp.services import human_capital_catalog_repository as catalog_repo

_AUTHORIZED_EMAIL = "human-capital-catalog-ssr-test@danwor.com"


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)


def _client_as(email: str) -> TestClient:
    session = auth_service.create_session(
        {"email": email, "name": "HC Catalog SSR Test", "first_name": "HC", "family_name": "Test"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


def _seed(**catalogs):
    snap = catalog_repo.build_snapshot(
        {name: [{"valor": v, "orden": i + 1} for i, v in enumerate(values)] for name, values in catalogs.items()},
        refreshed_by="test", source={"drive_id": "d", "item_id": "i"},
    )
    p = catalog_repo.snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(catalog_repo._serialize(snap))


# ============================================================================
# Alta — GET /cchh/candidatos/nuevo
# ============================================================================

def test_new_page_source_options_come_from_catalog_not_hardcode():
    _seed(fuente=["LinkedIn", "Referido"])
    client = _client_as(_AUTHORIZED_EMAIL)
    text = client.get("/cchh/candidatos/nuevo").text
    assert '<option value="LinkedIn"' in text
    assert '<option value="Referido"' in text
    # Ningún valor del viejo VALID_SOURCES que no fue sembrado en ESTE catálogo.
    assert '<option value="Hunting"' not in text
    assert '<option value="Flyer"' not in text


def test_new_page_seniority_options_still_come_from_catalog():
    _seed(seniority=["Junior", "Senior"])
    client = _client_as(_AUTHORIZED_EMAIL)
    text = client.get("/cchh/candidatos/nuevo").text
    assert '<option value="Junior"' in text
    assert '<option value="Senior"' in text


def test_new_page_source_select_empty_without_catalog():
    client = _client_as(_AUTHORIZED_EMAIL)  # sin seed
    text = client.get("/cchh/candidatos/nuevo").text
    assert 'id="hc-source"' in text
    assert '<option value="LinkedIn"' not in text


# ============================================================================
# Listado/filtro — GET /cchh/candidatos
# ============================================================================

def test_filter_source_options_come_from_catalog_not_hardcode():
    _seed(fuente=["LinkedIn", "Referido"])
    client = _client_as(_AUTHORIZED_EMAIL)
    text = client.get("/cchh/candidatos").text
    assert 'id="hc-filter-source"' in text
    assert '<option value="LinkedIn"' in text
    assert '<option value="Hunting"' not in text


# ============================================================================
# Edición — GET /cchh/candidatos/{id}/editar: marcador de legacy
# ============================================================================

def _create(client: TestClient, **overrides) -> dict:
    data = {"first_name": "Juan", "last_name": "Gómez", **overrides}
    client.post("/cchh/candidatos/nuevo", data=data)
    matches = [p for p in profile_repository.list() if p["first_name"] == data["first_name"]]
    return matches[0]


def test_edit_page_marks_legacy_source_out_of_catalog():
    # El alta se hace SIN fuente (para no chocar con la gobernanza de
    # create), y el valor legacy se siembra directo vía repository -- MISMO
    # criterio que test_human_capital_source_governance.py.
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create(client)
    profile_repository.update(profile["profile_id"], profile["revision"],
                               {**profile, "source": "Portal de empleo legacy"})
    _seed(fuente=["LinkedIn", "Referido"])  # el catálogo NO incluye el legacy
    text = client.get(f"/cchh/candidatos/{profile['profile_id']}/editar").text
    assert 'value="Portal de empleo legacy"' in text
    assert "(fuera de catálogo)" in text
    assert "Valor legacy fuera del catálogo vigente" in text


def test_edit_page_active_source_shows_no_legacy_marker():
    _seed(fuente=["LinkedIn", "Referido"])
    client = _client_as(_AUTHORIZED_EMAIL)
    profile = _create(client, source="LinkedIn")
    text = client.get(f"/cchh/candidatos/{profile['profile_id']}/editar").text
    assert "(fuera de catálogo)" not in text
    assert "Valor legacy fuera del catálogo vigente" not in text
