"""
odoo_client.py — Transporte JSON-RPC hacia el CRM Odoo (Commercial Hub —
Batch 1, read-only). Clasificación: INTEGRATION. Responsable EXCLUSIVAMENTE
del transporte — no conoce semántica de negocio (stages/NN·BI/horas/
pipeline/conversiones, eso vive en `webapp.services.commercial_service`),
no conoce sesión/entitlement HTTP (eso vive en `webapp.routers.commercial`).

MIGRAR COMPORTAMIENTO, NO ESTRUCTURA (ver HANDOFF — Commercial Hub Odoo
Batch 1): preserva del `OdooClient` legacy (`crm-comercial`/`backend/
odoo_client.py`) únicamente lo que es comportamiento REAL de transporte —
JSON-RPC (`authenticate`/`execute_kw` vía `/jsonrpc`), `search_read`,
`search_count`, y el fix de encoding de `_fix_encoding` (Odoo devuelve
strings UTF-8 mal decodificados como CP1252 — bug real del lado de Odoo,
no un detalle de implementación del legacy). NO se porta: el singleton
mutable reconfigurable, `fields_get` (sin consumidor todavía en este
batch), ni ningún acoplamiento a FastAPI/auth/DB del proyecto legacy.

Credenciales EXCLUSIVAMENTE server-side vía `config.py`
(`ODOO_URL`/`ODOO_DB`/`ODOO_USERNAME`/`ODOO_API_KEY`) — este módulo nunca
las recibe desde el browser, nunca las loguea, nunca las incluye en un
mensaje de excepción. `is_configured()` es el único chequeo de "¿están
las 4 presentes?" — el caller (`commercial_service`) lo usa para fallar
fail-soft/contextual ANTES de intentar una llamada real.

`OdooTransportError` es la ÚNICA excepción que puede escapar de `_call`/
`connect`/`execute` — nunca deja pasar `httpx.HTTPError`/`RuntimeError`
crudo (que podría incluir la URL/DB en su mensaje). `stage` distingue
"authenticate" (falla de conexión/credenciales) de "execute" (falla de
una consulta ya autenticada) para que el router pueda loguear sin
adivinar.
"""
from __future__ import annotations

import itertools

import httpx

import config

_ID_COUNTER = itertools.count(1)


def _fix_encoding(obj):
    """Odoo envía strings con bytes UTF-8 decodificados como CP1252.
    Reconstruye los bytes y los decodifica correctamente como UTF-8 —
    comportamiento REAL de la fuente (bug conocido de esta integración
    Odoo), portado 1:1 desde el legacy."""
    if isinstance(obj, str):
        try:
            byte_list = []
            for char in obj:
                cp = ord(char)
                if cp < 0x100:
                    byte_list.append(cp)
                else:
                    byte_list.extend(char.encode("cp1252", errors="ignore"))
            return bytes(byte_list).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return obj
    if isinstance(obj, list):
        return [_fix_encoding(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _fix_encoding(v) for k, v in obj.items()}
    return obj


class OdooTransportError(Exception):
    """Falla real de transporte/autenticación/RPC de Odoo — el mensaje NUNCA
    incluye URL/DB/usuario/API key. `stage` es `"authenticate"` o `"execute"`."""

    def __init__(self, message: str, *, stage: str):
        super().__init__(message)
        self.stage = stage


class OdooClient:
    """Cliente JSON-RPC — instancia liviana, sin estado mutable compartido
    entre requests salvo el `uid` de sesión (se re-autentica sola si falta).
    Lee credenciales de `config.py` al construirse, nunca de `os.getenv`
    directo (única fuente de configuración del Hub)."""

    def __init__(self):
        self.url = (config.ODOO_URL or "").rstrip("/")
        self.db = config.ODOO_DB or ""
        self.username = config.ODOO_USERNAME or ""
        self.api_key = config.ODOO_API_KEY or ""
        self._uid: int | None = None

    def is_configured(self) -> bool:
        return bool(self.url and self.db and self.username and self.api_key)

    def _call(self, service: str, method: str, *args):
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "id": next(_ID_COUNTER),
            "params": {"service": service, "method": method, "args": list(args)},
        }
        try:
            resp = httpx.post(f"{self.url}/jsonrpc", json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            stage = "authenticate" if service == "common" else "execute"
            raise OdooTransportError(f"Odoo transport failed: {exc.__class__.__name__}", stage=stage) from exc
        if "error" in data:
            stage = "authenticate" if service == "common" else "execute"
            message = str((data["error"] or {}).get("data", {}).get("message") or "Odoo RPC error")
            raise OdooTransportError(message, stage=stage)
        return _fix_encoding(data["result"])

    def connect(self) -> int:
        uid = self._call("common", "authenticate", self.db, self.username, self.api_key, {})
        if not uid:
            raise OdooTransportError("Odoo authentication failed", stage="authenticate")
        self._uid = uid
        return uid

    @property
    def uid(self) -> int:
        if not self._uid:
            self.connect()
        return self._uid

    def execute(self, model: str, method: str, args: list, kwargs: dict | None = None):
        return self._call(
            "object", "execute_kw",
            self.db, self.uid, self.api_key,
            model, method, args, kwargs or {},
        )

    def search_read(self, model: str, domain: list | None = None, fields: list | None = None,
                     limit: int | None = None, order: str | None = None) -> list:
        kwargs: dict = {}
        if fields:
            kwargs["fields"] = fields
        if limit:
            kwargs["limit"] = limit
        if order:
            kwargs["order"] = order
        return self.execute(model, "search_read", [domain or []], kwargs)

    def search_count(self, model: str, domain: list | None = None) -> int:
        return self.execute(model, "search_count", [domain or []])
