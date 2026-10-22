"""
test_human_capital_duplicate_advisory.py — CCHH: advertencia de candidato duplicado en el ALTA (UX).

Es una advertencia INLINE y compacta (no modal) con el componente existente `.callout--punch` del Hub; las acciones («Cancelar» secundario, «Cargar de todos modos» primario de ancho natural) forman un grupo
alineado que se apila en anchos chicos. NO cambia el algoritmo de detección, el mecanismo `duplicate_confirmed` ni la persistencia/subida: el contrato funcional se verifica contra el backend sin tocar.
(La detección es de CANDIDATO duplicado; no hay ninguna acción de «duplicado de CV» ni de «reemplazar CV».)"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import auth_service, profile_repository

_ROOT = Path(__file__).resolve().parents[2]
_FORM = (_ROOT / "webapp" / "templates" / "partials" / "human_capital_profile_new_form.html").read_text(encoding="utf-8")
_EDIT = (_ROOT / "webapp" / "templates" / "partials" / "human_capital_profile_edit_form.html").read_text(encoding="utf-8")
_HC_CSS = (_ROOT / "webapp" / "static" / "human_capital.css").read_text(encoding="utf-8")
_TOOLS_CSS = (_ROOT / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
_EMAIL = "hc-duplicate-advisory-test@danwor.com"
_PDF = b"%PDF-1.4\n%x\n"
_SCOPE = '[data-hc-profile-new-form] .callout[data-advisory-kind="possible_duplicate"]'


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)


@pytest.fixture
def client() -> TestClient:
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": _EMAIL, "name": "T", "first_name": "T", "family_name": "T"}))
    return c


def _form(**over):
    return {"first_name": "Ana", "last_name": "Lopez", "email": "ana.lopez@example.invalid", "notes": "nota que no debe perderse", **over}


def _advisory(client, **over):
    assert client.post("/cchh/candidatos/nuevo", data=_form(), follow_redirects=False).status_code == 303
    r = client.post("/cchh/candidatos/nuevo", data=_form(**over), follow_redirects=False)
    assert r.status_code == 200
    return r.text


# ── markup, copy y accesibilidad ─────────────────────────────────────────────────────────────────────────────
def test_the_advisory_is_inline_with_the_recommended_copy_and_accessible_markup(client):
    html = _advisory(client)
    m = re.search(r'<div class="callout callout--punch" data-advisory-kind="possible_duplicate" role="group" aria-labelledby="hc-duplicate-title">(.*?)</form>', html, re.S)
    assert m, "debe ser el `.callout--punch` del Hub, inline"
    block = m.group(1)
    assert '<span class="callout__icon" aria-hidden="true">⚠️</span>' in block and '<span class="callout__title" id="hc-duplicate-title">Posible candidato duplicado</span>' in block
    assert "Ya existe un candidato con datos coincidentes: <strong>Ana Lopez</strong> — ana.lopez@example.invalid. Podés cancelar o continuar igualmente." in block
    assert "posiblemente" not in block and "dialog" not in block.lower() and "modal" not in block.lower()            # advertencia inline, sin modal


def test_dynamic_content_is_escaped(client):
    assert client.post("/cchh/candidatos/nuevo", data=_form(first_name="Ana <b>X</b>", email="a+b@example.invalid"), follow_redirects=False).status_code == 303
    html = client.post("/cchh/candidatos/nuevo", data=_form(first_name="Ana <b>X</b>", email="a+b@example.invalid"), follow_redirects=False).text
    block = html[html.index('data-advisory-kind="possible_duplicate"'):]
    assert "&lt;b&gt;X&lt;/b&gt;" in block.split("</p>")[0] and "<b>X</b>" not in block.split("</p>")[0]


def test_the_actions_are_one_group_cancel_secondary_and_confirm_primary_with_the_existing_mechanism(client):
    html = _advisory(client)
    block = html[html.index('data-advisory-kind="possible_duplicate"'):]
    group = re.search(r'<div class="feedback-form-actions">(.*?)</div>', block, re.S).group(1)
    assert '<a href="/cchh/candidatos" class="btn-secondary">Cancelar</a>' in group
    assert 'type="submit" form="hc-profile-form" name="duplicate_confirmed" value="true" class="btn-primary asr-generate"' in group and "Cargar de todos modos" in group
    assert html.count('name="duplicate_confirmed"') == 1 and "Reemplazar" not in html and "reemplazar" not in html          # sin acciones inexistentes


def test_the_typed_data_is_preserved_when_the_advisory_is_shown(client):
    html = _advisory(client, notes="otra nota", phone="+54 11 5555-5555", tags="a, b")
    for needle in ('value="Ana"', 'value="Lopez"', 'value="ana.lopez@example.invalid"', "otra nota", 'value="+54 11 5555-5555"'):
        assert needle in html


# ── contrato funcional (backend sin cambios) ─────────────────────────────────────────────────────────────────
def test_without_confirmation_nothing_is_created_and_with_it_the_candidate_is_created(client):
    before = len(profile_repository.list())
    _advisory(client)
    assert len(profile_repository.list()) == before + 1                                                            # sólo el primero
    r = client.post("/cchh/candidatos/nuevo", data=_form(duplicate_confirmed="true"), follow_redirects=False)
    assert r.status_code == 303 and len(profile_repository.list()) == before + 2


def test_confirming_with_cv_and_an_msal_token_uploads_once_with_that_token(client, monkeypatch):
    from webapp.services import profile_document_service
    calls = []
    monkeypatch.setattr(profile_document_service, "upload_document", lambda **kw: calls.append(kw) or {})
    _advisory(client)
    r = client.post("/cchh/candidatos/nuevo", data=_form(graph_token="TOKEN-MSAL", duplicate_confirmed="true"), files={"file": ("cv.pdf", _PDF, "application/pdf")}, follow_redirects=False)
    assert r.status_code == 303 and len(calls) == 1 and calls[0]["access_token"] == "TOKEN-MSAL" and calls[0]["filename"] == "cv.pdf"
    assert calls[0]["first_name"] == "Ana" and calls[0]["last_name"] == "Lopez"


def test_the_duplicate_detection_still_matches_the_same_data_and_is_about_candidates_not_cvs(client):
    html = _advisory(client)
    assert 'data-advisory-kind="possible_duplicate"' in html
    other = client.post("/cchh/candidatos/nuevo", data=_form(first_name="Otra", last_name="Persona", email="otra@example.invalid"), follow_redirects=False)
    assert other.status_code == 303                                                                                # datos distintos: sin advertencia (el algoritmo no se tocó)


# ── CSS: compacto, sin ancho completo, responsive, ambos temas ───────────────────────────────────────────────
def test_the_css_is_scoped_compact_and_never_full_width():
    block = re.search(r"/\* Advertencia de candidato duplicado.*?\.feedback-form-actions \{[^}]*\}", _HC_CSS, re.S).group(0)
    assert f"{_SCOPE} .feedback-form-actions {{ display: flex; flex-wrap: wrap;" in block and "grid-template-columns" not in block and "width: 100%" not in block and "flex: 1" not in block
    assert "padding: 13px 16px" in block and ".callout__title { font-size: .85rem; }" in block and "font-size: .8rem" in block
    # botones: mismo tamaño/borde que las acciones del hero (una sola definición compartida), con borde visible en ambos temas
    assert f"{_SCOPE} .feedback-form-actions .btn-primary" in _HC_CSS and f"{_SCOPE} .feedback-form-actions .btn-secondary" in _HC_CSS
    assert re.search(r"height: 38px; min-height: 38px;", _HC_CSS) and f"{_SCOPE} .btn-secondary {{ border-color: rgba(128,128,128,.35); }}" in _HC_CSS
    assert f".content-theme-dark {_SCOPE} .btn-secondary {{ border-color: rgba(128,128,128,.4); }}" in _HC_CSS


def test_other_consumers_of_the_shared_primitives_are_untouched():
    assert ".feedback-form-actions { display: grid; grid-template-columns: 1fr 1.4fr;" in _TOOLS_CSS                  # los formularios de feedback conservan su grilla
    assert ".callout {" in _TOOLS_CSS and ".callout--punch { --callout-accent: #d3a000; }" in _TOOLS_CSS
    assert "feedback-form-actions" in _EDIT and 'role="group"' not in _EDIT and "guardarlo de todos modos" in _EDIT       # la edición no cambia
