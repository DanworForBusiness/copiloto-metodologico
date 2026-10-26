"""test_human_capital_candidates_selection.py — CCHH → Candidatos: ficha de consulta de solo lectura (Fase 1A; la Fase 1B suma la selección múltiple y «Compartir perfiles»).

Cubre: la FICHA (permisos, contenido, CV original, contexto de retorno, 404, sin escrituras, rutas fijas no ensombrecidas)."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from webapp import access
from webapp.main import app
from webapp.services import auth_service, human_capital_service, profile_document_repository, profile_repository

_ROOT = Path(__file__).resolve().parents[2]
_HC = "hc-selection-test@danwor.com"
_ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture(autouse=True)
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _HC)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(access, "APP_OWNER", "owner-selection@danwor.com")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")


def client(email: str | None = _HC) -> TestClient:
    c = TestClient(app)
    if email:
        c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def make_profile(first="Ana", last="Sintetica", **extra) -> dict:
    data = {"first_name": first, "last_name": last, "email": f"{first}.{last}@sintetico.test".lower(), "phone": "+54 11 5555-0000", "linkedin_url": "https://linkedin.example/in/x",
            "location": "CABA", "profile_skills": "SAP, Datasphere", "seniority": "Senior", "source": "LinkedIn", "tags": "tag-uno, tag-dos", "notes": "nota privada del candidato",
            "potential_rating": 4, "potential_mark": "Alto", **extra}
    return profile_repository.create(data)


def add_doc(profile_id: str, document_id="doc-1", filename="cv.pdf", web_url="https://tenant.sharepoint.example/cv.pdf", **extra) -> dict:
    doc = {"document_id": document_id, "profile_id": profile_id, "provider": "sharepoint", "site_id": "s", "drive_id": "d", "item_id": f"i-{document_id}", "filename": filename,
           "content_type": "application/pdf", "size_bytes": 2048, "uploaded_at": "2026-10-01T00:00:00+00:00", "uploaded_by": "rrhh@test", "web_url": web_url, **extra}
    return profile_document_repository.create_with_explicit_id(document_id, doc)[0]


def main_of(html: str) -> str:
    """Solo el contenido de la ficha (`<main>`): el shell del Hub (topbar, diálogo de confirmación compartido) tiene sus propios formularios."""
    return re.search(r"<main id=\"content\".*?</main>", html, re.S).group(0)


def data_digest() -> dict:
    out = {}
    for sub in ("profiles", "profile-documents"):
        d = Path(config.BASE_DIR) / "human-capital" / sub
        out[sub] = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(d.glob("*.json"))} if d.exists() else {}
    return out


# ═══════════════ 1A · ficha de consulta ═══════════════
def test_detail_requires_cchh_authorization_like_every_other_screen():
    pid = make_profile()["profile_id"]
    anon = TestClient(app).get(f"/cchh/candidatos/{pid}", follow_redirects=False)
    assert anon.status_code == 303 and anon.headers["location"].startswith("/platform?next=")
    assert client("fuera-de-cchh@danwor.com").get(f"/cchh/candidatos/{pid}").status_code == 403
    assert client().get(f"/cchh/candidatos/{pid}").status_code == 200
    assert client("owner-selection@danwor.com").get(f"/cchh/candidatos/{pid}").status_code == 200              # APP_OWNER: el esquema de siempre


def test_detail_shows_the_candidate_read_only_with_the_same_blocks_as_the_edit_screen():
    p = make_profile(first="Ana", last="Pérez")
    html = client().get(f"/cchh/candidatos/{p['profile_id']}").text
    for fragment in ("Ana", "Pérez", f'href="mailto:{p["email"]}"', "+54 11 5555-0000", "CABA", "SAP, Datasphere", "Senior", "LinkedIn", "tag-uno, tag-dos", "nota privada del candidato", "Alto"):
        assert fragment in html, fragment
    assert 'href="https://linkedin.example/in/x"' in html and 'rel="noopener noreferrer"' in html
    for title in ("Datos del candidato", "Clasificación", "Notas", "Potencial a futuro", "Documentos"):
        assert f'<span class="dashboard-section__title">{title}</span>' in html, title
    assert "Ficha del candidato" in html and "← Volver al listado" in html


def test_detail_is_strictly_read_only_no_form_no_inputs_no_write_controls():
    p = make_profile()
    add_doc(p["profile_id"])
    html = main_of(client().get(f"/cchh/candidatos/{p['profile_id']}").text)
    for forbidden in ("<form", "<input", "<textarea", "<select", "hx-post", "hx-delete", "data-dw-graph-token", "dwHumanCapitalDocumentWriteToken", "Dar de baja", "Eliminar", "Subir"):
        assert forbidden not in html, forbidden
    assert f'href="/cchh/candidatos/{p["profile_id"]}/editar"' in html                                                # «Editar» es solo un enlace


def test_detail_is_the_stable_shareable_url_and_never_the_edit_screen():
    p = make_profile()
    r = client().get(f"/cchh/candidatos/{p['profile_id']}")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.url.path == f"/cchh/candidatos/{p['profile_id']}" and "/editar" not in r.url.path        # la URL que se comparte es la de la ficha, jamás la de edición


def test_detail_documents_open_the_original_cv_in_sharepoint_with_a_plain_link():
    p = make_profile()
    add_doc(p["profile_id"], "d1", "cv-ana.pdf", "https://tenant.sharepoint.example/ana.pdf")
    add_doc(p["profile_id"], "d2", "sin-enlace.docx", "")
    html = client().get(f"/cchh/candidatos/{p['profile_id']}").text
    assert "cv-ana.pdf" in html and "sin-enlace.docx" in html and "2.0 kB" in html
    assert 'href="https://tenant.sharepoint.example/ana.pdf"' in html and 'target="_blank"' in html and "Abrir cv-ana.pdf en SharePoint" in html
    assert "el Hub no descarga ni copia el archivo" in html                                                           # no hay ruta de descarga del Hub


def test_detail_without_documents_and_with_a_failing_document_store_degrades_cleanly(monkeypatch):
    p = make_profile()
    assert 'data-empty-kind="no_documents"' in client().get(f"/cchh/candidatos/{p['profile_id']}").text
    from webapp.services import profile_document_service

    def boom(_pid):
        raise profile_document_repository.ProfileDocumentStorageError("sin acceso")
    monkeypatch.setattr(profile_document_service, "list_documents_for_profile", boom)
    r = client().get(f"/cchh/candidatos/{p['profile_id']}")
    assert r.status_code == 200 and 'data-error-kind="source_error"' in r.text and p["first_name"] in r.text         # la ficha igual se muestra


def test_detail_escapes_values_and_only_links_http_linkedin():
    p = make_profile(first="<b>Ana</b>", linkedin_url="javascript:alert(1)", notes="<script>x</script>")
    html = client().get(f"/cchh/candidatos/{p['profile_id']}").text
    assert "<b>Ana</b>" not in html and "&lt;b&gt;Ana&lt;/b&gt;" in html and "<script>x</script>" not in html
    assert 'href="javascript:' not in html and "javascript:alert(1)" in html                                         # se muestra como texto, nunca como enlace


def test_detail_returns_404_for_unknown_deleted_or_odd_ids():
    p = make_profile()
    assert client().get("/cchh/candidatos/no-existe").status_code == 404
    assert client().get("/cchh/candidatos/..%2Fsecreto").status_code in (404, 422)
    human_capital_service.soft_delete_profile(p["profile_id"]) if hasattr(human_capital_service, "soft_delete_profile") else profile_repository.soft_delete(p["profile_id"])
    assert client().get(f"/cchh/candidatos/{p['profile_id']}").status_code == 404                                     # dado de baja = inexistente


def test_detail_back_link_keeps_the_list_context_and_rejects_foreign_return_targets():
    p = make_profile()
    ctx = "/cchh/candidatos?q=ana&page=2&page_size=50"
    ok = client().get(f"/cchh/candidatos/{p['profile_id']}", params={"return_to": ctx}).text
    assert 'class="catalog-back" href="/cchh/candidatos?q=ana&amp;page=2&amp;page_size=50"' in ok
    assert "/editar?return_to=" in ok
    for evil in ("https://evil.example/x", "//evil.example", "javascript:alert(1)"):
        bad = client().get(f"/cchh/candidatos/{p['profile_id']}", params={"return_to": evil}).text
        assert 'class="catalog-back" href="/cchh/candidatos"' in bad and "evil.example" not in bad, evil


def test_fixed_routes_of_the_same_level_are_not_shadowed_by_the_detail_route():
    for path in ("/cchh/candidatos/nuevo", "/cchh/candidatos/importar"):
        assert client().get(path).status_code == 200, path
    assert client().get("/cchh/candidatos/compartir").status_code == 404                                              # GET no existe (el diálogo es POST)


def test_detail_never_writes_profiles_or_documents():
    p = make_profile()
    add_doc(p["profile_id"])
    before = data_digest()
    for _ in range(3):
        client().get(f"/cchh/candidatos/{p['profile_id']}")
    assert data_digest() == before
