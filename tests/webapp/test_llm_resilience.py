"""
test_llm_resilience.py — resiliencia compartida de fallas LLM/HTTP.

Cubre: el seam compartido `webapp.services.llm_client` (política timeout/retry +
mapeo tipado de errores del SDK de OpenAI), la UX de falla server-side de Assistant
y Section Analysis (fragmento HTMX seguro, sin exponer detalles), la integridad de
estado del assistant_store ante una falla de proveedor, y la higiene del fallback
de red opt-in en app.js / templates. Sin red real: fakes + excepciones construidas.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import openai
import pytest

from fastapi.testclient import TestClient

from webapp.main import app
from webapp import access
from webapp.services import auth_service
from webapp.services import llm_client
from webapp.services.llm_client import LLMServiceError, default_client
from webapp.services.assistant_service import (
    AssistantChatService,
    AssistantConversationStore,
)
from webapp.services.section_analysis import SectionAnalysisService, SectionAnalysisError
from webapp.services.content_service import load_module
from webapp.routers import assistant as assistant_router
from webapp.copilot_runtime import assistant_store as shared_store

# Substring del copy seguro esperado por tipo de falla (ver llm_client._PUBLIC_MESSAGES).
_EXPECTED_MSG = {
    "timeout": "tardó más de lo esperado",
    "connection": "servicio de IA no está disponible",
    "rate_limit": "muchas consultas",
    "provider": "servicio de IA no está disponible",
}

auth_service.ALLOWED_USERS = "ALL"
_SESSION = auth_service.create_session(
    {"email": "llm-test@danwor.com", "name": "LLM Test", "first_name": "LLM", "family_name": "Test"}
)
client = TestClient(app)
client.cookies.set(auth_service.COOKIE_NAME, _SESSION)

_ROOT = Path(__file__).resolve().parents[2]
_APP_JS = (_ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
_MODULE_HTML = (_ROOT / "webapp" / "templates" / "module.html").read_text(encoding="utf-8")
_ROUTE_ACTIONS_HTML = (
    _ROOT / "webapp" / "templates" / "partials" / "assistant_route_actions.html"
).read_text(encoding="utf-8")

_REQ = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _resp(status: int) -> httpx.Response:
    return httpx.Response(status, request=_REQ)


def _openai_exc(kind: str) -> Exception:
    return {
        "timeout": openai.APITimeoutError(_REQ),
        "connection": openai.APIConnectionError(message="la red se cayó", request=_REQ),
        "rate_limit": openai.RateLimitError("rate", response=_resp(429), body=None),
        "provider": openai.InternalServerError("server on fire", response=_resp(500), body=None),
    }[kind]


def _raising_factory(exc: Exception):
    def factory(**kwargs):
        class _Completions:
            def create(self, **kw):
                raise exc

        class _Chat:
            completions = _Completions()

        class _Client:
            chat = _Chat()

        return _Client()

    return factory


# ── seam compartido: política + mapeo ─────────────────────────────────────────

def test_default_client_applies_bounded_timeout_and_retry():
    c = default_client(api_key="sk-unit-test")
    assert c.timeout == llm_client.LLM_TIMEOUT_SECONDS
    assert c.max_retries == llm_client.LLM_MAX_RETRIES


@pytest.mark.parametrize("kind,http", [
    ("timeout", None), ("connection", None), ("rate_limit", 429), ("provider", 500),
])
def test_classify_maps_known_provider_failures(kind, http):
    err = llm_client.classify_openai_error(_openai_exc(kind))
    assert isinstance(err, LLMServiceError)
    assert err.kind == kind
    assert err.http_status == http


def _status_exc(status: int) -> openai.APIStatusError:
    # APIStatusError base con status arbitrario (message = texto crudo del proveedor
    # que NUNCA debe llegar al HTML).
    return openai.APIStatusError("RAW-provider-text", response=_resp(status), body=None)


@pytest.mark.parametrize("status,kind", [
    (500, "provider"), (502, "provider"), (503, "provider"),
    (401, "configuration"), (403, "configuration"),
    (400, "request"), (404, "request"), (409, "request"), (422, "request"),
])
def test_classify_apistatuserror_by_status_semantics(status, kind):
    err = llm_client.classify_openai_error(_status_exc(status))
    assert isinstance(err, LLMServiceError)
    assert err.kind == kind
    assert err.http_status == status
    # el texto crudo del proveedor nunca queda en el mensaje público.
    assert "RAW-provider-text" not in err.public_message


def test_classify_does_not_swallow_programming_errors():
    assert llm_client.classify_openai_error(ValueError("bug real")) is None
    assert llm_client.classify_openai_error(KeyError("k")) is None


def test_public_failure_object_leaks_no_provider_internals():
    err = llm_client.classify_openai_error(_openai_exc("provider"))
    assert "server on fire" not in err.public_message
    assert "openai" not in err.public_message.lower()
    assert "500" not in err.public_message


# ── Assistant: falla de proveedor → fragmento seguro ──────────────────────────

@pytest.mark.parametrize("kind", ["timeout", "connection", "rate_limit", "provider"])
def test_assistant_chat_provider_failure_returns_safe_fragment(monkeypatch, kind):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-should-not-leak")
    monkeypatch.setattr(
        assistant_router, "assistant_chat_service",
        AssistantChatService(client_factory=_raising_factory(_openai_exc(kind)), execution_mode="produccion"),
    )
    # conversation_id VÁLIDO: el chat exige una conversación existente (no la
    # auto-crea) — con "" lanzaría KeyError antes de llegar al proveedor.
    conv = shared_store.start("01_Arquitectura_y_Conectividad", "datasphere_core")
    r = client.post(
        "/modules/01_Arquitectura_y_Conectividad/assistant/chat",
        data={"pack": "datasphere_core", "question": "explicame el módulo",
              "routing_bypass": "true", "conversation_id": conv.id},
    )
    assert r.status_code == 200                       # fragmento renderable, no 5xx crudo
    t = r.text
    assert "section-analysis-error" in t              # bloque de error visible
    assert _EXPECTED_MSG[kind] in t                   # el copy seguro ESPECÍFICO aparece
    assert conv.latest_reply_id == ""                 # la falla no creó reply
    # sin fugas: ni API key, ni texto crudo del proveedor, ni clase de excepción.
    assert "sk-secret-should-not-leak" not in t
    assert "server on fire" not in t and "la red se cayó" not in t
    assert "APITimeoutError" not in t and "RateLimitError" not in t and "Traceback" not in t


@pytest.mark.parametrize("kind", ["timeout", "connection", "rate_limit", "provider"])
def test_section_analysis_provider_failure_returns_safe_fragment(monkeypatch, kind):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-should-not-leak")
    monkeypatch.setattr(
        assistant_router, "section_analysis_service",
        SectionAnalysisService(client_factory=_raising_factory(_openai_exc(kind)), execution_mode="produccion"),
    )
    document = load_module("01_Arquitectura_y_Conectividad")
    r = client.post(
        "/modules/01_Arquitectura_y_Conectividad/sections/analyze",
        data={"pack": "datasphere_core", "section_slug": document.sections[0].slug,
              "action": "Checklist ejecutable"},
    )
    assert r.status_code == 200
    t = r.text
    assert "section-analysis-error" in t
    assert _EXPECTED_MSG[kind] in t                   # el copy seguro ESPECÍFICO aparece
    assert "sk-secret-should-not-leak" not in t and "server on fire" not in t
    assert "Traceback" not in t


def test_successful_section_analysis_unchanged(monkeypatch):
    # El camino exitoso (modo simulación) sigue igual: sin regresión.
    monkeypatch.setattr(
        assistant_router, "section_analysis_service",
        SectionAnalysisService(execution_mode="Simulación"),
    )
    document = load_module("01_Arquitectura_y_Conectividad")
    r = client.post(
        "/modules/01_Arquitectura_y_Conectividad/sections/analyze",
        data={"pack": "datasphere_core", "section_slug": document.sections[0].slug,
              "action": "Checklist ejecutable"},
    )
    assert r.status_code == 200
    assert "Simulación activa" in r.text
    assert "section-analysis-error" not in r.text


# ── integridad de estado ante falla ───────────────────────────────────────────

def test_provider_failure_creates_no_reply_and_feedback_works_after_retry():
    """Replica el orden del router: pregunta → (provider call) → append. Una falla
    de proveedor NO crea reply ni mueve latest_reply_id; un reintento exitoso
    posterior sí produce un reply calificable."""
    store = AssistantConversationStore()
    conv = store.start("01_Arquitectura_y_Conectividad", "datasphere_core")
    document = load_module("01_Arquitectura_y_Conectividad")

    failing = AssistantChatService(
        client_factory=_raising_factory(_openai_exc("provider")), execution_mode="produccion",
    )
    import os
    os.environ["OPENAI_API_KEY"] = "sk-x"
    try:
        with pytest.raises(LLMServiceError):
            failing.ask(document, "pregunta", "datasphere_core", history=())
    finally:
        os.environ.pop("OPENAI_API_KEY", None)

    # El store nunca fue mutado por la falla.
    assert conv.latest_reply_id == ""
    assert store._replies == {}

    # Reintento exitoso (simulación) → append → feedback válido sobre ESE reply.
    ok = AssistantChatService(execution_mode="Simulación")
    answer, html = ok.ask(document, "pregunta", "datasphere_core", history=())
    reply = store.append(conv.id, conv.module, conv.pack, "pregunta", answer, html,
                         topic="Pregunta libre", action="Asistente")
    assert conv.latest_reply_id == reply.id
    assert store.reply_for_feedback(reply.id) == reply


# ── higiene del fallback de red opt-in (app.js + templates) ───────────────────

def test_app_js_registers_network_fallback_events_optin_and_no_catch_all():
    assert 'addEventListener("htmx:sendError", dwRenderRequestErrorFallback)' in _APP_JS
    assert 'addEventListener("htmx:timeout", dwRenderRequestErrorFallback)' in _APP_JS
    # OPT-IN: solo actúa sobre elementos que declaran el contrato (no catch-all).
    assert 'closest("[data-dw-request-error-target]")' in _APP_JS
    assert "if (!trigger) return;" in _APP_JS
    # textContent para el mensaje (sin inyección) y sin auto-reintento.
    assert ".textContent = message;" in _APP_JS
    # no registra listener de htmx:responseError (no duplica el path server-rendered
    # ni interfiere con los 403/409/410 del feedback).
    assert 'addEventListener("htmx:responseError"' not in _APP_JS


def test_assistant_and_section_triggers_declare_network_fallback():
    # Los 2 triggers de module.html (chat + análisis) opt-in al contrato.
    assert _MODULE_HTML.count('data-dw-request-error-target="#assistant-conversation"') == 2
    assert 'data-dw-request-error-message="No pudimos completar la solicitud por un problema de conectividad' in _MODULE_HTML
    # "Explicame vos" (mismo endpoint LLM) también opt-in.
    assert 'data-dw-request-error-target="#assistant-conversation"' in _ROUTE_ACTIONS_HTML
