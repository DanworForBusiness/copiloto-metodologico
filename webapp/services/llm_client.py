"""
llm_client.py — infraestructura compartida de cliente LLM (OpenAI) para Danwor Hub.

Seam ACOTADO de resiliencia LLM (no un framework de integración):
  1. construye el cliente OpenAI por defecto con timeout + retry acotado;
  2. normaliza fallas de red/proveedor a errores TIPADOS propios de Danwor;
  3. extrae metadata diagnóstica SEGURA (kind / http_status / request_id).

NO contiene prompts ni lógica de negocio; NO es un registry de proveedores ni un
framework de DI. Assistant y Section Analysis siguen siendo servicios de dominio y
solo consumen este seam (vía su `client_factory` inyectable, que sigue existiendo
para los tests con fakes). No importa ``webapp.main``.
"""
from __future__ import annotations

import logging
from typing import Any

import openai
from openai import OpenAI

# Política por defecto para UI interactiva síncrona: esperar de forma ACOTADA (el
# incidente real fue una request colgada que terminó en ERR_CONNECTION_CLOSED sin
# respuesta). El SDK aplica los reintentos internamente con backoff.
LLM_TIMEOUT_SECONDS = 30.0
LLM_MAX_RETRIES = 1

_logger = logging.getLogger("danwor.llm")

# Copys públicos (seguros, en español) por tipo de falla. NUNCA exponen stack,
# nombre de clase, API key ni texto crudo del proveedor.
_PUBLIC_MESSAGES = {
    "timeout": "La consulta tardó más de lo esperado. Podés intentar nuevamente.",
    "rate_limit": (
        "El servicio de IA está recibiendo muchas consultas en este momento. "
        "Intentá nuevamente en unos minutos."
    ),
    "connection": (
        "No pudimos completar la consulta porque el servicio de IA no está "
        "disponible en este momento. Intentá nuevamente en unos minutos."
    ),
    "provider": (
        "No pudimos completar la consulta porque el servicio de IA no está "
        "disponible en este momento. Intentá nuevamente en unos minutos."
    ),
}


class LLMServiceError(RuntimeError):
    """Falla NORMALIZADA de un proveedor LLM (timeout / connection / rate_limit /
    provider). `public_message` es el texto seguro para el browser;
    `http_status`/`request_id` son metadata diagnóstica opcional para los logs del
    server — NUNCA se exponen al browser."""

    def __init__(
        self,
        kind: str,
        *,
        http_status: int | None = None,
        request_id: str | None = None,
    ) -> None:
        self.kind = kind
        self.http_status = http_status
        self.request_id = request_id
        self.public_message = _PUBLIC_MESSAGES.get(kind, _PUBLIC_MESSAGES["provider"])
        super().__init__(f"llm_{kind}")


def default_client(**kwargs: Any) -> OpenAI:
    """Cliente OpenAI por defecto con timeout + retry acotado. Firma compatible con
    el `client_factory` inyectable de los servicios (recibe `api_key=...`); los
    tests siguen inyectando un fake — este factory solo fija la POLÍTICA."""
    kwargs.setdefault("timeout", LLM_TIMEOUT_SECONDS)
    kwargs.setdefault("max_retries", LLM_MAX_RETRIES)
    return OpenAI(**kwargs)


def _safe_request_id(exc: BaseException) -> str | None:
    rid = getattr(exc, "request_id", None)
    return str(rid) if rid else None


def classify_openai_error(exc: BaseException) -> LLMServiceError | None:
    """Mapea una excepción del SDK de OpenAI a un `LLMServiceError` tipado, o
    devuelve None si NO es una falla de proveedor conocida (p. ej. un bug de
    programación) — esas NUNCA deben mislabelarse como 'IA no disponible'.

    Orden por jerarquía del SDK: APITimeoutError ⊂ APIConnectionError ⊂ APIError;
    RateLimitError ⊂ APIStatusError ⊂ APIError."""
    if isinstance(exc, openai.APITimeoutError):
        return LLMServiceError("timeout", request_id=_safe_request_id(exc))
    if isinstance(exc, openai.APIConnectionError):
        return LLMServiceError("connection", request_id=_safe_request_id(exc))
    if isinstance(exc, openai.RateLimitError):
        return LLMServiceError(
            "rate_limit",
            http_status=getattr(exc, "status_code", 429),
            request_id=_safe_request_id(exc),
        )
    if isinstance(exc, openai.APIStatusError):
        return LLMServiceError(
            "provider",
            http_status=getattr(exc, "status_code", None),
            request_id=_safe_request_id(exc),
        )
    if isinstance(exc, openai.APIError):
        return LLMServiceError("provider", request_id=_safe_request_id(exc))
    return None


def log_failure(operation: str, exc: LLMServiceError) -> None:
    """Diagnóstico estructurado y SEGURO de una falla de proveedor (logging de
    aplicación, no una nueva taxonomía de Activity). Campos: provider, operation,
    failure_kind, http_status, request_id. NUNCA loguea API key/Authorization/
    prompt/respuesta/cookies/body."""
    _logger.warning(
        "llm_failure provider=openai operation=%s failure_kind=%s http_status=%s request_id=%s",
        operation,
        exc.kind,
        exc.http_status if exc.http_status is not None else "-",
        exc.request_id or "-",
    )
