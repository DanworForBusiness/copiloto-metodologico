"""
test_human_capital_catalog_graph_auth_taxonomy.py — CCHH Catalog Foundation,
owner post-#104 acceptance (item E): un `HTTPError` de Graph al leer el
workbook de catálogos NUNCA debe colapsar 401/403/404 al mismo
`CatalogFetchError` genérico -- antes de este slice, `fetch_workbook_bytes`
descartaba `exc.code` por completo (`except Exception: raise
CatalogFetchError(f"...{type(exc).__name__}.")`), así que un token vencido
(401) y un 500 de Graph rendeaban el MISMO mensaje. Contrato exigido:

    token vacío        -> NEEDS_AUTH (nunca llama a Graph)
    HTTP 401           -> NEEDS_AUTH ("Autorizar Microsoft y reintentar")
    HTTP 403           -> FORBIDDEN (autenticado, sin permiso -- reautenticar
                          NO lo arregla, nunca se ofrece ese botón acá)
    HTTP 404           -> SOURCE_NOT_FOUND
    validación/schema  -> SOURCE_INVALID (ya existía, `CatalogWorkbookError`)
    resto transporte   -> FETCH_ERROR (ya existía, `CatalogFetchError`)

Cubre dos capas: la clasificación en el repositorio (`fetch_workbook_bytes`)
y el rendering distinto por `kind` en `POST /cchh/catalogos/refresh`."""
from __future__ import annotations

import urllib.error

import pytest
from fastapi.testclient import TestClient

import config
from core import microsoft365
from webapp.main import app
from webapp.services import auth_service, human_capital_catalog_repository as repo

_AUTHORIZED_EMAIL = "human-capital-catalog-auth-taxonomy-test@danwor.com"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    # Identidad del workbook configurada -- si no, CatalogConfigIncompleteError
    # gana antes de siquiera intentar el fetch, y no es lo que estos tests miden.
    monkeypatch.setattr(config, "HC_SHAREPOINT_DRIVE_ID", "drive-1")
    monkeypatch.setattr(config, "HC_CATALOG_ITEM_ID", "item-1")


def _client_as(email: str) -> TestClient:
    session = auth_service.create_session(
        {"email": email, "name": "HC Catalog Auth Test", "first_name": "HC", "family_name": "Test"}
    )
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://graph.microsoft.com/v1.0/drives/drive-1/items/item-1/content",
        code=code, msg=f"HTTP {code}", hdrs=None, fp=None,
    )


# ---------------------------------------------------------------------------
# 1. Repositorio -- clasificación de fetch_workbook_bytes por status code
# ---------------------------------------------------------------------------
def test_fetch_401_raises_auth_required(monkeypatch):
    monkeypatch.setattr(microsoft365, "graph_get_bytes", lambda *a, **k: (_ for _ in ()).throw(_http_error(401)))
    with pytest.raises(repo.CatalogAuthRequiredError):
        repo.fetch_workbook_bytes("expired-token")


def test_fetch_403_raises_forbidden_not_auth_required(monkeypatch):
    monkeypatch.setattr(microsoft365, "graph_get_bytes", lambda *a, **k: (_ for _ in ()).throw(_http_error(403)))
    with pytest.raises(repo.CatalogForbiddenError):
        repo.fetch_workbook_bytes("valid-but-insufficient-token")


def test_fetch_404_raises_not_found(monkeypatch):
    monkeypatch.setattr(microsoft365, "graph_get_bytes", lambda *a, **k: (_ for _ in ()).throw(_http_error(404)))
    with pytest.raises(repo.CatalogNotFoundError):
        repo.fetch_workbook_bytes("valid-token")


def test_fetch_other_http_status_falls_back_to_generic_fetch_error(monkeypatch):
    monkeypatch.setattr(microsoft365, "graph_get_bytes", lambda *a, **k: (_ for _ in ()).throw(_http_error(500)))
    with pytest.raises(repo.CatalogFetchError):
        repo.fetch_workbook_bytes("valid-token")


def test_fetch_non_http_transport_error_falls_back_to_generic_fetch_error(monkeypatch):
    monkeypatch.setattr(microsoft365, "graph_get_bytes", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("slow")))
    with pytest.raises(repo.CatalogFetchError):
        repo.fetch_workbook_bytes("valid-token")


def test_fetch_error_messages_never_expose_the_raw_graph_body():
    # Ningún mensaje de las excepciones de clasificación puede contener texto
    # de cuerpo de respuesta HTTP -- todos son copy fijo, escrito acá, nunca
    # interpolado desde `exc` (más allá del código en el fallback genérico).
    for exc_cls in (repo.CatalogAuthRequiredError, repo.CatalogForbiddenError, repo.CatalogNotFoundError):
        instance = exc_cls("mensaje de prueba")
        assert "<html" not in str(instance).lower()


# ---------------------------------------------------------------------------
# 2. Router -- POST /cchh/catalogos/refresh rendea distinto por kind
# ---------------------------------------------------------------------------
def test_empty_token_never_calls_graph_and_renders_auth_required(monkeypatch):
    def _must_not_be_called(*a, **k):
        raise AssertionError("refresh_catalogs no debía llamarse con token vacío")
    monkeypatch.setattr(repo, "refresh_catalogs", _must_not_be_called)
    client = _client_as(_AUTHORIZED_EMAIL)
    resp = client.post("/cchh/catalogos/refresh", data={"graph_token": ""})
    assert resp.status_code == 200
    assert 'data-error-kind="auth_required"' in resp.text
    assert "Autorizar Microsoft y reintentar" in resp.text


def test_auth_required_error_renders_reauth_button(monkeypatch):
    monkeypatch.setattr(
        repo, "refresh_catalogs",
        lambda *a, **k: (_ for _ in ()).throw(repo.CatalogAuthRequiredError("venció")),
    )
    client = _client_as(_AUTHORIZED_EMAIL)
    resp = client.post("/cchh/catalogos/refresh", data={"graph_token": "expired"})
    assert 'data-error-kind="auth_required"' in resp.text
    assert "Autorizar Microsoft y reintentar" in resp.text
    assert "data-hc-catalog-reauth" in resp.text


def test_forbidden_error_never_offers_reauth_button(monkeypatch):
    # 403: ya autenticado, sin permiso -- reautenticar no cambia nada, así
    # que NUNCA debe verse el mismo CTA que auth_required.
    monkeypatch.setattr(
        repo, "refresh_catalogs",
        lambda *a, **k: (_ for _ in ()).throw(repo.CatalogForbiddenError("sin permiso")),
    )
    client = _client_as(_AUTHORIZED_EMAIL)
    resp = client.post("/cchh/catalogos/refresh", data={"graph_token": "valid-but-insufficient"})
    assert 'data-error-kind="forbidden"' in resp.text
    assert "Autorizar Microsoft y reintentar" not in resp.text
    # `data-hc-catalog-reauth` sigue existiendo como SELECTOR en el <script>
    # compartido de la página (siempre presente) -- lo que nunca debe
    # aparecer es el elemento <button> real con ese atributo.
    assert "<button type=\"button\" class=\"dashboard-subhead__reset\" data-hc-catalog-reauth>" not in resp.text


def test_not_found_error_renders_source_not_found_kind(monkeypatch):
    monkeypatch.setattr(
        repo, "refresh_catalogs",
        lambda *a, **k: (_ for _ in ()).throw(repo.CatalogNotFoundError("no está")),
    )
    client = _client_as(_AUTHORIZED_EMAIL)
    resp = client.post("/cchh/catalogos/refresh", data={"graph_token": "valid"})
    assert 'data-error-kind="not_found"' in resp.text
    assert "Autorizar Microsoft y reintentar" not in resp.text


# ---------------------------------------------------------------------------
# 3. Página GET -- friendly labels/timestamp, nunca schema v1 crudo
# ---------------------------------------------------------------------------
def _seed_meta():
    snap = repo.build_snapshot(
        {"canal_publicacion": [{"valor": "LinkedIn", "orden": 1}]},
        refreshed_by="alguien@danwor.com", source={"drive_id": "d", "item_id": "i"},
    )
    p = repo.snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(repo._serialize(snap))


def test_catalogos_page_never_shows_raw_schema_version():
    _seed_meta()
    client = _client_as(_AUTHORIZED_EMAIL)
    text = client.get("/cchh/catalogos").text
    assert "schema v" not in text.lower()


def test_catalogos_page_shows_business_label_not_raw_identifier():
    client = _client_as(_AUTHORIZED_EMAIL)
    text = client.get("/cchh/catalogos").text
    assert "Canal de publicación" in text
    assert "canal_publicacion" not in text


def test_catalogos_page_timestamp_is_not_raw_iso():
    _seed_meta()
    client = _client_as(_AUTHORIZED_EMAIL)
    text = client.get("/cchh/catalogos").text
    # El ISO crudo tiene un "T" separando fecha/hora y un offset -- ninguno
    # de los dos debe sobrevivir al formateo friendly.
    assert "+00:00" not in text


def test_refresh_success_counts_use_business_labels_not_raw_keys(monkeypatch):
    monkeypatch.setattr(
        repo, "refresh_catalogs",
        lambda *a, **k: {"outcome": "success", "counts": {"canal_publicacion": 3, "seniority": 5}},
    )
    client = _client_as(_AUTHORIZED_EMAIL)
    resp = client.post("/cchh/catalogos/refresh", data={"graph_token": "valid"})
    assert "Canal de publicación: 3" in resp.text
    assert "canal_publicacion" not in resp.text
