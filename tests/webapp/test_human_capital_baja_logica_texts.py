"""
test_human_capital_baja_logica_texts.py — CCHH: «Dar de baja» (gobierno documental).

La acción sobre un candidato es una BAJA LÓGICA (`deleted_at`): el registro deja de verse en el Hub y sus documentos (CV y carpeta de SharePoint) PERMANECEN. Este archivo fija (1) los textos de las dos entradas de UI
(ícono del listado y botón de Editar), que ya no dicen «Eliminar» y explicitan la conservación de los documentos, y (2) que la baja no emite NINGUNA operación hacia Graph (ni DELETE ni otra). El backend (`soft_delete_profile`)
no cambia: sus tests siguen en test_human_capital_soft_delete.py.
"""
from __future__ import annotations

import re
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from core import microsoft365
from webapp.main import app
from webapp.services import auth_service, human_capital_service, profile_document_repository, profile_repository

_ROOT = Path(__file__).resolve().parents[2]
_EDIT = (_ROOT / "webapp" / "templates" / "human_capital" / "profile_edit.html").read_text(encoding="utf-8")
_PANEL = (_ROOT / "webapp" / "templates" / "partials" / "human_capital_profiles_panel.html").read_text(encoding="utf-8")
_EMAIL = "hc-baja-texts-test@danwor.com"
_KEEP = "No tiene documentos cargados."          # sin documentos: solo se informa la baja lógica (con documentos: diálogo contextual, ver test_cchh_baja_contextual_documents.py)


def _top_level_block(src: str, header: str) -> str:
    """Texto de una función de módulo: desde su `def` hasta la siguiente sentencia de nivel superior."""
    block = src[src.index(header):]
    end = re.search(r"\n(?=@|def |async def |class )", block[len(header):])
    return block[: len(header) + end.start()] if end else block


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    for repo in (profile_repository, profile_document_repository):
        monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
        monkeypatch.setattr(repo.remote_store, "enabled", lambda: False)


@pytest.fixture
def client() -> TestClient:
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": _EMAIL, "name": "T", "first_name": "T", "family_name": "T"}))
    return c


@pytest.fixture
def graph_calls(monkeypatch):
    """Cualquier acceso a la red (urllib) o a un helper `graph_*` durante la baja hace fallar el test; registra los intentos."""
    calls: list[str] = []

    def _forbid(name):
        def _f(*a, **k):
            calls.append(name)
            raise AssertionError(f"la baja lógica no debe llamar a Graph/red: {name}")
        return _f

    monkeypatch.setattr(urllib.request, "urlopen", _forbid("urllib.request.urlopen"))
    for attr in dir(microsoft365):
        if attr.startswith("graph_") and callable(getattr(microsoft365, attr)):
            monkeypatch.setattr(microsoft365, attr, _forbid(f"microsoft365.{attr}"))
    return calls


# ── textos (plantillas) ───────────────────────────────────────────────────────────────────────────────────────
def test_the_templates_no_longer_say_eliminar_and_state_that_documents_stay_in_sharepoint():
    for tpl in (_EDIT, _PANEL):
        assert 'data-dw-confirm-title="Dar de baja al candidato"' in tpl and 'data-dw-confirm-ok="Dar de baja"' in tpl
        assert "¿Querés dar de baja a " in tpl and _KEEP in tpl
        assert 'data-dw-confirm-title="Eliminar candidato"' not in tpl and "puede afectar la información asociada" not in tpl and "¿Querés eliminar a" not in tpl
    assert 'aria-label="Dar de baja al candidato"' in _PANEL and "<p>Dar de baja</p>" in _PANEL and "Eliminar candidato</p>" not in _PANEL
    assert re.search(r"</svg>\s*Dar de baja\s*</button>", _EDIT) and not re.search(r"</svg>\s*Eliminar\s*</button>", _EDIT)


def test_the_rendered_list_and_edit_pages_show_the_baja_texts(client):
    p = profile_repository.create({"first_name": "Ana", "last_name": "Baja"})
    listing = client.get("/cchh/candidatos")
    edit = client.get(f"/cchh/candidatos/{p['profile_id']}/editar")
    assert listing.status_code == 200 and edit.status_code == 200
    for page in (listing.text, edit.text):
        assert "Dar de baja al candidato" in page and "¿Querés dar de baja a Ana Baja?" in page and _KEEP in page
        assert "Eliminar candidato" not in page and "puede afectar la información asociada" not in page


def test_the_shared_confirm_dialog_keeps_eliminar_as_its_default_for_other_consumers():
    base = (_ROOT / "webapp" / "templates" / "base.html").read_text(encoding="utf-8")
    appjs = (_ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'data-dw-confirm-ok>Eliminar</button>' in base and 'getAttribute("data-dw-confirm-ok") || "Eliminar"' in appjs      # el diálogo compartido no se tocó


# ── comportamiento: la baja no toca SharePoint ───────────────────────────────────────────────────────────────
def test_the_baja_from_the_list_never_calls_graph_and_keeps_the_documents(client, graph_calls):
    p = profile_repository.create({"first_name": "Zoe", "last_name": "Lista"})
    profile_document_repository.create_with_explicit_id(
        "doc-baja-1", {"profile_id": p["profile_id"], "provider": "sharepoint", "site_id": "s", "drive_id": "d", "item_id": "i", "folder_item_id": "f",
                       "filename": "cv.pdf", "content_type": "application/pdf", "size_bytes": 10, "uploaded_at": "2026-10-21T00:00:00Z", "uploaded_by": "a@danwor.com"})
    r = client.post(f"/cchh/candidatos/{p['profile_id']}/eliminar", headers={"HX-Request": "true"})
    assert r.status_code == 200 and graph_calls == []
    assert profile_repository.get(p["profile_id"])["deleted_at"]                                                     # `deleted_at` intacto
    assert [d["document_id"] for d in profile_document_repository.list_for_profile(p["profile_id"])] == ["doc-baja-1"]


def test_the_baja_from_the_edit_page_never_calls_graph(client, graph_calls):
    p = profile_repository.create({"first_name": "Eva", "last_name": "Edit"})
    r = client.post(f"/cchh/candidatos/{p['profile_id']}/eliminar")
    assert r.status_code == 303 and r.headers["location"] == "/cchh/candidatos" and graph_calls == []
    assert profile_repository.get(p["profile_id"])["deleted_at"]


def test_the_service_baja_never_calls_graph(graph_calls):
    p = profile_repository.create({"first_name": "Sol", "last_name": "Servicio"})
    human_capital_service.soft_delete_profile(p["profile_id"], "actor@danwor.com")
    assert graph_calls == [] and profile_repository.get(p["profile_id"])["deleted_by"] == "actor@danwor.com"


def test_the_baja_code_path_does_not_reference_graph_delete():
    """Defensa estática: ni el servicio de baja ni el repositorio de perfiles importan/usan helpers de Graph; la ruta sólo llama a `soft_delete_profile`."""
    svc = (_ROOT / "webapp" / "services" / "profile_repository.py").read_text(encoding="utf-8")
    soft = _top_level_block(svc, "def soft_delete(")
    assert "graph_" not in soft and "microsoft365" not in soft
    router = (_ROOT / "webapp" / "routers" / "human_capital.py").read_text(encoding="utf-8")
    route = _top_level_block(router, "def human_capital_profile_delete(")
    # (la rama de error re-renderiza Editar y LEE los documentos locales; no escribe ni borra nada)
    # (la eliminación física es OPT-IN con la casilla «Eliminar también los documentos de SharePoint»: va por el servicio, nunca por helpers de Graph en la ruta)
    assert "microsoft365" not in route and "upload_document" not in route and "delete_profile_documents" in route
    assert "soft_delete_profile(profile_id, actor)" in route
