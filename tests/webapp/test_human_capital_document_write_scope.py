"""
test_human_capital_document_write_scope.py — Capital Humano Phase 4.1.1
(SharePoint Document Write Corrective, §2-§7): contrato de configuración +
plumbing del scope de escritura ADICIONAL que Capital Humano pide para
subir documentos — MISMA arquitectura que el resto del Hub (`_env`,
`/api/auth/microsoft/config`), nunca un backend env var que el JS estático
no puede leer.

`hub_auth.js`'s runtime behavior (silent/popup/scope composition) está
probado exhaustivamente en `hub_auth_authority.node.js` (sección O) vía
`test_hub_auth_authority.py`; este archivo cubre lo que ESE harness no
puede: config server-side y el wiring del template."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.routers import auth as auth_router

_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = _ROOT / "webapp" / "templates" / "partials" / "human_capital_profile_documents_panel.html"
_HUB_AUTH = _ROOT / "webapp" / "static" / "hub_auth.js"


# ============================================================================
# config.py — MISMO helper `_env` que ya usa el resto de la config de este
# Hub (nunca un `importlib.reload` del módulo completo: sin precedente en
# esta suite, y reload-ar `config` tiene efectos globales sobre CUALQUIER
# otro test que dependa de un `monkeypatch.setattr(config, ...)` vigente).
# ============================================================================

def test_write_scope_config_defaults_to_empty_fail_closed(monkeypatch):
    monkeypatch.delenv("HC_DOCUMENT_WRITE_SCOPE", raising=False)
    assert config._env("HC_DOCUMENT_WRITE_SCOPE", "") == ""


def test_write_scope_config_reads_from_env(monkeypatch):
    monkeypatch.setenv("HC_DOCUMENT_WRITE_SCOPE", "https://danwor.sharepoint.com/Sites.Selected")
    assert config._env("HC_DOCUMENT_WRITE_SCOPE", "") == "https://danwor.sharepoint.com/Sites.Selected"


def test_write_scope_config_attribute_exists_on_the_live_module():
    # El propio módulo (ya importado, sin reload) expone el atributo --
    # prueba que la línea de declaración existe y no solo que `_env`
    # funcionaría si se la llamara.
    assert hasattr(config, "HC_DOCUMENT_WRITE_SCOPE")


# ============================================================================
# /api/auth/microsoft/config — expone el scope, nunca un secreto
# ============================================================================

def test_microsoft_config_endpoint_exposes_hc_write_scope(monkeypatch):
    # `auth.py` importa `HC_DOCUMENT_WRITE_SCOPE` por NOMBRE (`from config
    # import HC_DOCUMENT_WRITE_SCOPE`, MISMO patrón que AZURE_CLIENT_ID/
    # AZURE_TENANT_ID ahí mismo) -- el monkeypatch tiene que apuntar al
    # nombre LOCAL del router, no a `config.HC_DOCUMENT_WRITE_SCOPE`
    # (cambiar ese no afectaría el binding ya resuelto en auth.py).
    monkeypatch.setattr(auth_router, "HC_DOCUMENT_WRITE_SCOPE", "https://danwor.sharepoint.com/Sites.Selected")
    client = TestClient(app)
    response = client.get("/api/auth/microsoft/config")
    assert response.status_code == 200
    assert response.json()["hc_document_write_scope"] == "https://danwor.sharepoint.com/Sites.Selected"


def test_microsoft_config_endpoint_reflects_empty_write_scope_when_unconfigured(monkeypatch):
    monkeypatch.setattr(auth_router, "HC_DOCUMENT_WRITE_SCOPE", "")
    client = TestClient(app)
    response = client.get("/api/auth/microsoft/config")
    assert response.json()["hc_document_write_scope"] == ""


def test_microsoft_config_endpoint_still_exposes_base_fields():
    # Regresión mínima: agregar el campo nuevo no debe pisar los existentes.
    client = TestClient(app)
    response = client.get("/api/auth/microsoft/config")
    body = response.json()
    assert "enabled" in body and "tenant_id" in body and "client_id" in body


# ============================================================================
# Template — el form de documentos usa el token CAPABILITY-ESPECÍFICO,
# nunca dwGraphToken (que solo pide los scopes de lectura del Hub)
# ============================================================================

def test_documents_panel_uses_the_write_capable_token_function():
    text = _TEMPLATE.read_text(encoding="utf-8")
    script = text.split("<script>", 1)[1]  # solo el <script> real, nunca el comentario Jinja que lo explica
    assert "dwHumanCapitalDocumentWriteToken" in script
    # La ÚNICA función que puebla el campo graph_token del form de subida
    # tiene que ser la capability-específica -- nunca dwGraphToken (que
    # solo pide User.Read/Sites.Read.All, insuficiente para el PUT).
    assert "window.dwGraphToken()" not in script
    assert "window.dwGraphToken " not in script


def test_documents_panel_still_posts_graph_token_field_name():
    # El backend sigue leyendo `graph_token` como nombre de campo (router,
    # sin cambios) -- solo cambia QUIÉN lo llena, nunca el contrato HTTP.
    text = _TEMPLATE.read_text(encoding="utf-8")
    assert 'name="graph_token"' in text


# ============================================================================
# hub_auth.js — dwGraphToken (el resto del Hub) no cambió de superficie
# ============================================================================

def test_hub_auth_js_still_declares_exactly_two_base_graph_scopes():
    text = _HUB_AUTH.read_text(encoding="utf-8")
    assert 'const GRAPH_SCOPES = ["User.Read", "Sites.Read.All"];' in text


def test_hub_auth_js_declares_the_new_capability_specific_function():
    text = _HUB_AUTH.read_text(encoding="utf-8")
    assert "window.dwHumanCapitalDocumentWriteToken = async function" in text


def test_hub_auth_js_acquire_token_popup_appears_exactly_once():
    # MISMO invariante que GUARD 5 de test_hub_auth_gesture.py, reafirmado
    # acá porque es la propiedad que directamente motivó el rediseño de
    # dwHumanCapitalDocumentWriteToken (delega en dwGraphReauthenticate en
    # vez de llamar acquireTokenPopup ella misma).
    import re

    raw = _HUB_AUTH.read_text(encoding="utf-8")
    without_block_comments = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    code = "\n".join(l for l in without_block_comments.splitlines() if not l.strip().startswith("//"))
    assert code.count("acquireTokenPopup") == 1
