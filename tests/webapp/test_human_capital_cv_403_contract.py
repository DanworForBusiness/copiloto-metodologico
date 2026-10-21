"""
test_human_capital_cv_403_contract.py — CCHH: contrato del alta con CV cuando SharePoint NO autoriza la escritura (HTTP 403 `accessDenied`).

Situación real de producción hasta que la app DO tenga el grant Selected `write` sobre `CVs`: las LECTURAS funcionan (Sites.Read.All), la creación de carpeta devuelve 403. Se fija que:
  1. el alta SIN CV funciona y no toca Graph;
  2. con CV y 403 el candidato queda guardado, la UI informa el permiso (200, nunca el redirect de éxito) y NO se registra ningún documento;
  3. el resultado es recuperable: con el permiso concedido, el reintento desde el panel de Documentos crea la carpeta legible y registra el documento una sola vez;
  4. si la carpeta se crea y el PUT falla con 403, tampoco hay documento falso y el reintento REUTILIZA la carpeta (sin duplicados);
  5. ninguna rama emite DELETE ni reintenta a ciegas, y no se elude el scope `Files.SelectedOperations.Selected`.
Graph está simulado (SharePoint en memoria de `test_profile_document_folder_naming`); ninguna llamada de red real."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import auth_service, profile_document_repository as repo, profile_document_service as svc, profile_repository
from tests.webapp.test_profile_document_folder_naming import PDF, ROOT, FakeDrive, _http_error

_EMAIL = "hc-cv-403-contract-test@danwor.com"
_DENIED_MESSAGE = "No tenés permiso para escribir documentos en SharePoint. Contactá al administrador."


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "HC_SHAREPOINT_SITE_ID", "site-1")
    monkeypatch.setattr(config, "HC_SHAREPOINT_DRIVE_ID", "drive-1")
    monkeypatch.setattr(config, "HC_CV_ROOT_ITEM_ID", ROOT)
    monkeypatch.setattr(config, "HC_DOCUMENT_WRITE_SCOPE", "Files.SelectedOperations.Selected")
    for r in (repo, profile_repository):
        monkeypatch.setattr(r.remote_store, "push_path", lambda path: True)
        monkeypatch.setattr(r.remote_store, "enabled", lambda: False)
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")


@pytest.fixture
def drive(monkeypatch):
    d = FakeDrive()
    monkeypatch.setattr(svc.microsoft365, "graph_get_json", d.get)
    monkeypatch.setattr(svc.microsoft365, "graph_post_json", d.post)
    monkeypatch.setattr(svc.microsoft365, "graph_put_bytes", d.put)
    monkeypatch.setattr(svc.microsoft365, "graph_delete", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nunca se borra nada")))
    return d


@pytest.fixture
def client() -> TestClient:
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": _EMAIL, "name": "T", "first_name": "T", "family_name": "T"}))
    return c


def _alta(client, *, cv=True, **extra):
    files = {"file": ("cv.pdf", PDF, "application/pdf")} if cv else None
    return client.post("/cchh/candidatos/nuevo", data={"first_name": "Ana", "last_name": "López", "graph_token": "tok", **extra}, files=files)


def _the_profile():
    profiles = profile_repository.list()
    assert len(profiles) == 1
    return profiles[0]


def test_the_alta_without_cv_works_and_never_touches_graph_even_if_sharepoint_would_deny_everything(monkeypatch, client):
    def boom(*a, **k):
        raise AssertionError("el alta sin CV no debe llamar a Graph")
    for name in ("graph_get_json", "graph_post_json", "graph_put_bytes", "graph_delete", "graph_get_bytes"):
        monkeypatch.setattr(svc.microsoft365, name, boom)
    r = _alta(client, cv=False)
    assert r.status_code == 303 and r.headers["location"] == "/cchh/candidatos"
    assert repo.list_for_profile(_the_profile()["profile_id"]) == []


def test_a_403_creating_the_folder_keeps_the_candidate_reports_the_permission_and_registers_no_document(drive, client):
    drive.post_errors = [_http_error(403)]
    r = _alta(client)
    profile = _the_profile()
    assert r.status_code == 200 and "location" not in r.headers                                      # nunca el redirect de éxito
    assert 'data-error-kind="graph_permission_denied"' in r.text and _DENIED_MESSAGE in r.text
    assert f'action="/cchh/candidatos/{profile["profile_id"]}/documentos' in r.text                 # el panel de Documentos queda disponible para reintentar
    assert repo.list_for_profile(profile["profile_id"]) == []                                         # ningún documento falso
    assert drive.methods.count("POST") == 1 and "PUT" not in drive.methods and drive.folders == {}   # sin reintento a ciegas, sin archivo, sin carpeta
    assert "DELETE" not in drive.methods


def test_after_a_403_the_upload_is_recoverable_from_the_documents_panel_once_the_permission_exists(drive, client):
    drive.post_errors = [_http_error(403)]
    assert _alta(client).status_code == 200
    profile = _the_profile()
    drive.post_errors = []                                                                            # el permiso ya fue concedido
    r = client.post(f"/cchh/candidatos/{profile['profile_id']}/documentos", data={"graph_token": "tok"}, files={"file": ("cv.pdf", PDF, "application/pdf")})
    docs = repo.list_for_profile(profile["profile_id"])
    assert r.status_code == 303 and len(docs) == 1 and len(drive.folders) == 1 and len(profile_repository.list()) == 1
    assert docs[0]["folder_item_id"] in drive.folders and docs[0]["filename"] == "cv.pdf"
    assert next(iter(drive.folders.values()))["name"].startswith("López, Ana [")


def test_a_403_on_the_file_write_after_the_folder_exists_registers_no_document_and_the_retry_reuses_the_folder(drive, client, monkeypatch):
    real_put = drive.put
    monkeypatch.setattr(svc.microsoft365, "graph_put_bytes", lambda *a, **k: (_ for _ in ()).throw(_http_error(403)))
    r = _alta(client)
    profile = _the_profile()
    assert r.status_code == 200 and 'data-error-kind="graph_permission_denied"' in r.text and _DENIED_MESSAGE in r.text
    assert repo.list_for_profile(profile["profile_id"]) == []                                         # el documento NO se registra si el archivo no se escribió
    assert len(drive.folders) == 1                                                                    # limitación conocida: puede quedar la carpeta vacía (nunca se borra automáticamente)
    monkeypatch.setattr(svc.microsoft365, "graph_put_bytes", real_put)
    r2 = client.post(f"/cchh/candidatos/{profile['profile_id']}/documentos", data={"graph_token": "tok"}, files={"file": ("cv.pdf", PDF, "application/pdf")})
    assert r2.status_code == 303 and len(drive.folders) == 1 and len(repo.list_for_profile(profile["profile_id"])) == 1      # misma carpeta: sin duplicados
    assert drive.methods.count("POST") == 1 and "DELETE" not in drive.methods


def test_without_a_graph_token_the_candidate_is_saved_and_no_sharepoint_write_is_attempted(drive, client):
    r = client.post("/cchh/candidatos/nuevo", data={"first_name": "Ana", "last_name": "López"}, files={"file": ("cv.pdf", PDF, "application/pdf")})
    profile = _the_profile()
    assert r.status_code == 200 and "autorización de Microsoft 365" in r.text and repo.list_for_profile(profile["profile_id"]) == [] and drive.calls == []


def test_the_write_scope_is_still_the_selected_operations_scope_and_nothing_bypasses_it():
    assert config.HC_DOCUMENT_WRITE_SCOPE == "Files.SelectedOperations.Selected"
    import inspect
    src = inspect.getsource(svc)
    assert "Files.ReadWrite" not in src and "Sites.ReadWrite" not in src and "Sites.FullControl" not in src
