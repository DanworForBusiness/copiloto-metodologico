"""sharepoint_selected_grant.py — concede un grant Selected `write` a UNA aplicación Entra sobre UNA carpeta de SharePoint, con un único POST controlado (y un rollback acotado, nunca automático).

Herramienta ADMINISTRATIVA, independiente del runtime web y del uploader de CCHH: sólo stdlib, no importa `webapp`/`config`/`core`, no usa el token del Hub y no agrega infraestructura.
Operación de fondo (Graph v1.0, doc oficial `driveitem-post-permissions`):

    POST /drives/{drive-id}/items/{item-id}/permissions
    {"grantedToV2": {"application": {"id": "<client id>"}}, "roles": ["write"]}

Modos (exactamente uno):
  --check     SOLO LECTURA: valida allowlist + token, lee la metadata del destino (debe ser CARPETA) y enumera lo que Graph deja ver. Nunca emite POST/DELETE ni escribe archivos.
  --dry-run   Lo de --check + identidad + preview EXACTO del request y veredicto de las compuertas (`would_apply`). Nunca emite POST/DELETE ni escribe archivos.
  --apply     ÚNICA operación de creación: 1 POST, sin reintentos, tras (en este orden) allowlist, banderas de reconocimiento, TTY interactivo, token, identidad, destino por IDs, enumeración, confirmación
              interactiva y RELECTURA de permisos inmediatamente antes del POST. Si hay cualquier coincidencia observable o la relectura es incompleta, aborta sin POST.
  --rollback  ÚNICA operación de borrado: 1 DELETE del `permission_id` registrado en el archivo de auditoría de un --apply previo, sobre el ítem de la allowlist, con confirmación interactiva. Jamás se invoca solo.

Invocación (todos los identificadores son OBLIGATORIOS y se validan contra la allowlist; no hay valores por defecto que puedan apuntar a otro destino). Ver tools/SHAREPOINT_SELECTED_GRANT.md.

Autenticación (identidad ADMINISTRATIVA independiente; nunca contraseñas, secretos ni tokens en argumentos CLI, código ni logs):
  1. Variable de entorno `DANWOR_GRAPH_ADMIN_TOKEN` (token delegado obtenido por el admin con sus propias herramientas), o
  2. device code flow (RFC 8628, cliente PÚBLICO, sin secreto) con `--admin-client-id`, contra la autoridad del tenant de la allowlist. Se pide `Sites.Read.All` (check/dry-run) o `Sites.ReadWrite.All` (apply/rollback o
     `--request-write-scope`) más `User.Read` (para verificar la identidad). Pedir `Sites.ReadWrite.All` NO prueba que alcance para administrar grants Selected: la doc de Selected exige además Owner/Control total
     del usuario sobre la carpeta (y habla de `Sites.FullControl.All`); la prueba real es la respuesta del servicio, que se reporta con ambas hipótesis.
El token del Hub (apps `ad98b5cd…`/`eeca6df8…`) se rechaza: un grant no puede concederse con la identidad de la app que lo recibe.

Evidencia de duplicados: sólo la PRESENCIA de un grant equivalente es autoritativa. Ningún endpoint documentado prueba la AUSENCIA (driveItem/permissions lista permisos de COMPARTIR; listItem/permissions no está
documentado como enumeración exhaustiva de grants Selected). Por eso `absence_is_authoritative` devuelve siempre False y el estado es UNDETERMINED: `--apply` sólo avanza si el owner lo reconoce de forma explícita
(`--acknowledge-undetermined`), y aun así relee y aborta ante cualquier coincidencia. No se simula una idempotencia que no existe.

Códigos de salida: 0 ok · 1 error de Graph/red · 2 validación, allowlist, autenticación o identidad · 3 no confirmado/bloqueado por compuertas · 4 el POST/DELETE pudo haberse ejecutado (resultado DESCONOCIDO: no se reintenta)."""
from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

TOOL_ID = "sharepoint_selected_grant/2"
GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
GRAPH_RESOURCE = "https://graph.microsoft.com"
GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
LOGIN_ROOT = "https://login.microsoftonline.com"
TOKEN_ENV = "DANWOR_GRAPH_ADMIN_TOKEN"

# Frases exactas: aparecen tanto como bandera (queda en el historial del shell) como tipeadas en el prompt interactivo (prueba de presencia humana).
CONFIRM_PHRASE = "CONCEDER GRANT WRITE"
ROLLBACK_PHRASE = "REVOCAR GRANT WRITE"
AUDIT_DEFAULT_DIR = os.path.join("reports", "sharepoint_selected_grant")  # `reports/*` está ignorado por git: es runtime local
EXIT_OUTCOME_UNKNOWN = 4

# ── allowlist ───────────────────────────────────────────────────────────────────────────────────────────────
TENANT_ID = "f287b953-5428-4a5e-ae17-bd9c4cfaedbe"
LOCAL_APP_ID = "ad98b5cd-71d0-451f-8d70-e8581a4788fa"
PRODUCTION_APP_ID = "eeca6df8-2436-422f-a1b8-05ff9e8ff653"  # RESERVADA: NO es destino ejecutable
HUB_APP_IDS = frozenset({LOCAL_APP_ID, PRODUCTION_APP_ID})
# Agregar una fila (p. ej. la carpeta `CCHH HUB` del sitio Comunicaciones + la app de producción) requiere un cambio de código y un GO explícito del owner.
ALLOWED_GRANTS: tuple[dict[str, str], ...] = (
    {"name": "control-local", "tenant_id": TENANT_ID, "client_id": LOCAL_APP_ID, "site_id": "a129a1d0-a4ee-4438-8de8-9e39e7007764",
     "drive_id": "b!0KEpoe6kOESN6J455wB3ZLYmlHEbGsZPlL4_LU8lfDroKl3b1Ti0RYJfK4nkJLM3", "item_id": "01U7IMSJOGXNT3ZNP5NVAKDFLQWY3L6OMQ", "folder_name": "Hub"},
)
GRANT_ROLE = "write"
EQUIVALENT_ROLES = frozenset({"write", "owner", "fullcontrol"})

# Permisos Graph DELEGADOS del cliente administrativo (doc: driveitem-post-permissions / listitem-list-permissions / driveitem-list-permissions).
READ_SCOPES = frozenset({"Sites.Read.All", "Sites.ReadWrite.All", "Sites.Manage.All", "Sites.FullControl.All"})
WRITE_SCOPES = frozenset({"Sites.ReadWrite.All", "Sites.Manage.All", "Sites.FullControl.All"})

_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_DRIVE = re.compile(r"^b![A-Za-z0-9_-]+$")
_ITEM = re.compile(r"^[A-Za-z0-9!_-]+$")
_PERM_ID = re.compile(r"^[A-Za-z0-9_=.-]{1,200}$")
_AADSTS = re.compile(r"AADSTS\d{4,7}")
_MAX_PAGES = 10
_TIMEOUT = 30

Transport = Callable[[str, str, dict, "bytes | None"], "tuple[int, dict, bytes]"]
Prompt = Callable[[str], str]


class ToolError(Exception):
    """Falla controlada: `stage` identifica la compuerta; `exit_code` 2 (validación/auth), 3 (no confirmado/bloqueado), 1 (Graph/red) o 4 (resultado desconocido)."""

    def __init__(self, stage: str, message: str, *, exit_code: int = 2, **detail: Any):
        super().__init__(message)
        self.stage, self.message, self.exit_code, self.detail = stage, message, exit_code, detail


# ── allowlist ───────────────────────────────────────────────────────────────────────────────────────────────
def _norm(kind: str, value: str) -> str:
    value = (value or "").strip()
    pattern = {"guid": _GUID, "drive": _DRIVE, "item": _ITEM}[kind]
    if not pattern.match(value):
        raise ToolError("config_invalid", f"formato inválido para {kind}")
    return value.lower() if kind == "guid" else value


def resolve_grant(tenant_id: str, client_id: str, site_id: str, drive_id: str, item_id: str) -> dict[str, str]:
    """Devuelve la fila de la allowlist que coincide EXACTAMENTE con los 5 identificadores, o falla. Sin red."""
    wanted = {"tenant_id": _norm("guid", tenant_id), "client_id": _norm("guid", client_id), "site_id": _norm("guid", site_id),
              "drive_id": _norm("drive", drive_id), "item_id": _norm("item", item_id)}
    if wanted["client_id"] == PRODUCTION_APP_ID.lower():
        raise ToolError("config_rejected", "la aplicación de producción está RESERVADA y no es destino ejecutable")
    for row in ALLOWED_GRANTS:
        if all(row[k].lower() == wanted[k].lower() if k not in ("drive_id", "item_id") else row[k] == wanted[k] for k in wanted):
            return dict(row)
    mismatched = sorted(k for k in wanted if not any((row[k].lower() == wanted[k].lower()) if k not in ("drive_id", "item_id") else row[k] == wanted[k] for row in ALLOWED_GRANTS))
    raise ToolError("config_rejected", "la combinación tenant/cliente/site/drive/item no está en la allowlist", fields=mismatched or ["combination"])


def _q(segment: str) -> str:
    return urllib.parse.quote(segment, safe="!")


def _item_permissions_url(grant: dict[str, str]) -> str:
    return f"{GRAPH_ROOT}/drives/{_q(grant['drive_id'])}/items/{_q(grant['item_id'])}/permissions"


def build_grant_request(grant: dict[str, str]) -> dict[str, Any]:
    """Preview EXACTO del único POST: body mínimo, sin campos extra, rol fijo `write`."""
    body = {"grantedToV2": {"application": {"id": grant["client_id"]}}, "roles": [GRANT_ROLE]}
    return {"method": "POST", "url": _item_permissions_url(grant), "body": body}


def build_rollback_request(grant: dict[str, str], permission_id: str) -> dict[str, Any]:
    if not _PERM_ID.match(permission_id or ""):
        raise ToolError("rollback_invalid", "permission_id con formato inválido")
    return {"method": "DELETE", "url": f"{_item_permissions_url(grant)}/{_q(permission_id)}"}


# ── token ───────────────────────────────────────────────────────────────────────────────────────────────────
def _decode_claims(token: str) -> "dict | None":
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _scope_names(raw: str) -> list[str]:
    return sorted({s.rsplit("/", 1)[-1] for s in (raw or "").split() if s})


def inspect_env_token(token: str, tenant_id: str, *, now: float, admin_client_id: "str | None" = None) -> dict[str, Any]:
    """Pre-chequeo CONSERVADOR de un token entregado por el admin. Leer claims de un token de Graph no es un contrato soportado: es sólo una compuerta de seguridad
    adicional (si no se puede leer, se rechaza). Graph sigue siendo quien valida el token."""
    claims = _decode_claims(token)
    if claims is None:
        raise ToolError("token_rejected", "el token no es legible como JWT: no se puede verificar tenant/cliente; usar device code (--admin-client-id)")
    problems = []
    if str(claims.get("tid", "")).lower() != tenant_id.lower():
        problems.append("tenant_mismatch")
    client_id = str(claims.get("azp") or claims.get("appid") or "").lower()
    if client_id in {a.lower() for a in HUB_APP_IDS}:
        problems.append("token_is_a_hub_app")
    if not client_id:
        problems.append("client_unknown")
    elif admin_client_id and client_id != admin_client_id.lower():
        problems.append("admin_client_mismatch")
    if str(claims.get("aud", "")).rstrip("/") not in (GRAPH_RESOURCE, GRAPH_APP_ID):
        problems.append("audience_not_graph")
    if claims.get("idtyp") == "app" or not claims.get("scp"):
        problems.append("not_a_delegated_token")
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp <= now:
        problems.append("token_expired_or_without_exp")
    if problems:
        raise ToolError("token_rejected", "token administrativo no aceptable", problems=problems)
    return {"source": "env", "client_id": client_id, "tenant_id": tenant_id.lower(), "scopes": _scope_names(str(claims.get("scp")))}


def _scope_gate(info: dict[str, Any]) -> dict[str, Any]:
    scopes = set(info["scopes"])
    if not scopes & READ_SCOPES:
        raise ToolError("token_rejected", "el token no trae un scope de lectura de sitios suficiente", have=sorted(scopes), need_any=sorted(READ_SCOPES))
    info["can_write"] = bool(scopes & WRITE_SCOPES)
    return info


def _http_transport(method: str, url: str, headers: dict, data: "bytes | None") -> "tuple[int, dict, bytes]":
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310  (https fijo, validado antes)
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, exc.read() if exc.fp else b""
    except urllib.error.URLError as exc:
        raise ToolError("network_error", f"sin respuesta de {url.split('/')[2]}: {type(exc.reason).__name__}", exit_code=1) from exc
    except (TimeoutError, OSError, http.client.HTTPException) as exc:  # corte/timeout durante la lectura: el servidor pudo haber procesado el pedido
        raise ToolError("network_error", f"sin respuesta completa de {url.split('/')[2]}: {type(exc).__name__}", exit_code=1) from exc


def _json(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _oauth_error(body: dict) -> str:
    return str(body.get("error", "unknown"))[:80]


def _oauth_extra(body: dict) -> dict[str, Any]:
    """Conserva lo que sirve para diagnosticar un login/consentimiento fallido (código AADSTS, códigos numéricos, 1ª línea de la descripción) sin guardar secretos ni la descripción completa."""
    desc = str(body.get("error_description") or "")
    first = desc.splitlines()[0][:300] if desc else ""
    aadsts = sorted(set(_AADSTS.findall(desc)))
    error = _oauth_error(body)
    extra: dict[str, Any] = {}
    if first:
        extra["error_description"] = first
    if aadsts:
        extra["aadsts"] = aadsts
    codes = [c for c in body.get("error_codes") or [] if isinstance(c, int)]
    if codes:
        extra["error_codes"] = codes
    if error == "authorization_declined" or "AADSTS65004" in aadsts:
        extra["hint"] = "consent_rejected_by_user"
    elif error in ("consent_required", "interaction_required") or {"AADSTS65001", "AADSTS90094"} & set(aadsts):
        extra["hint"] = "consent_not_granted"
    elif error == "invalid_scope" or "AADSTS70011" in aadsts:
        extra["hint"] = "scope_not_requestable_by_this_client"
    return extra


def device_code_token(tenant_id: str, client_id: str, scope_names: list[str], *, transport: "Transport | None" = None,
                      sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic, out=None) -> dict[str, Any]:
    """Device code flow (RFC 8628) de un cliente PÚBLICO: sin secreto, sin refresh token, autoridad = tenant de la allowlist. El access token queda sólo en memoria."""
    transport = transport or _http_transport
    out = out or sys.stderr
    authority = f"{LOGIN_ROOT}/{tenant_id}/oauth2/v2.0"
    form = lambda fields: urllib.parse.urlencode(fields).encode("ascii")  # noqa: E731
    hdr = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    status, _h, raw = transport("POST", f"{authority}/devicecode", hdr, form({"client_id": client_id, "scope": " ".join(f"{GRAPH_RESOURCE}/{s}" for s in scope_names)}))
    start = _json(raw)
    if status != 200 or "device_code" not in start:
        raise ToolError("auth_failed", "el tenant no entregó un device code", exit_code=2, error=_oauth_error(start), status=status, **_oauth_extra(start))
    print(f"[auth] {start.get('message') or 'Abrí ' + str(start.get('verification_uri')) + ' e ingresá el código ' + str(start.get('user_code'))}", file=out)
    deadline, interval = clock() + float(start.get("expires_in", 900)), max(int(start.get("interval", 5)), 1)
    while True:
        sleep(interval)
        if clock() > deadline:
            raise ToolError("auth_failed", "el device code expiró sin que el admin completara el login", error="expired_token")
        status, _h, raw = transport("POST", f"{authority}/token", hdr, form({"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "client_id": client_id, "device_code": start["device_code"]}))
        body = _json(raw)
        if status == 200 and body.get("access_token"):
            return {"access_token": body["access_token"], "info": {"source": "device_code", "client_id": client_id.lower(), "tenant_id": tenant_id.lower(), "scopes": _scope_names(str(body.get("scope", "")))}}
        err = _oauth_error(body)
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        raise ToolError("auth_failed", "el login administrativo no se completó", error=err, status=status, **_oauth_extra(body))


# ── Graph (GET libre; un único POST y un único DELETE acotados) ──────────────────────────────────────────────
class Graph:
    """Cliente mínimo. Sin reintentos. Sólo GET, el POST de permisos y el DELETE de UN permiso del ítem de la allowlist: NO existe PUT/PATCH. El token vive acá y no sale en ningún resultado."""

    def __init__(self, token: str, transport: "Transport | None" = None):
        self._token = token
        self._transport = transport or _http_transport
        self.calls: list[tuple[str, str]] = []  # (método, ruta sin query) — para auditoría/tests; sin headers ni token

    def _call(self, method: str, url: str, body: "dict | None" = None, *, _delete_ok: bool = False) -> dict[str, Any]:
        if method not in ("GET", "POST") and not (method == "DELETE" and _delete_ok):
            raise ValueError(f"método no permitido: {method}")
        if not url.startswith(GRAPH_ROOT + "/"):
            raise ToolError("url_rejected", "URL fuera de Graph v1.0 (el token no se envía a otros hosts)")
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.calls.append((method, url.split("?")[0].replace(GRAPH_ROOT, "")))
        try:
            status, resp_headers, raw = self._transport(method, url, headers, data)
        except Exception as exc:  # noqa: BLE001 — cualquier corte/timeout
            if method == "GET":
                if isinstance(exc, ToolError):
                    raise
                raise ToolError("network_error", f"fallo de transporte: {type(exc).__name__}", exit_code=1) from exc
            # POST/DELETE: el pedido pudo haber llegado y ejecutarse. NO se reintenta; se informa resultado DESCONOCIDO.
            return {"ok": False, "status": None, "body": {}, "code": None, "message": None, "request_id": None, "client_request_id": None,
                    "outcome_unknown": True, "error_type": type(exc).__name__}
        payload = _json(raw)
        err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        lower = {k.lower(): v for k, v in resp_headers.items()}
        return {"ok": 200 <= status < 300, "status": status, "body": payload, "code": err.get("code"), "message": str(err.get("message", ""))[:300] or None,
                "request_id": lower.get("request-id") or (err.get("innerError") or {}).get("request-id"), "client_request_id": lower.get("client-request-id")}

    def get(self, url: str) -> dict[str, Any]:
        return self._call("GET", url)

    def post_permission(self, preview: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", preview["url"], preview["body"])

    def delete_permission(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._call("DELETE", request["url"], _delete_ok=True)


def _graph_error(res: dict[str, Any]) -> dict[str, Any]:
    out = {k: res.get(k) for k in ("status", "code", "message", "request_id", "client_request_id")}
    if res.get("outcome_unknown"):
        out.update({"outcome": "UNKNOWN", "error_type": res.get("error_type")})
    return out


# ── destino, identidad y enumeración ────────────────────────────────────────────────────────────────────────
def check_target(graph: Graph, grant: dict[str, str]) -> dict[str, Any]:
    res = graph.get(f"{GRAPH_ROOT}/drives/{_q(grant['drive_id'])}/items/{_q(grant['item_id'])}?$select=id,name,folder,file,parentReference,sharepointIds")
    if not res["ok"]:
        raise ToolError("target_unreadable", "no se pudo leer la metadata del destino", exit_code=1, graph=_graph_error(res))
    body = res["body"]
    ids = body.get("sharepointIds") if isinstance(body.get("sharepointIds"), dict) else {}
    problems = []
    if body.get("id") != grant["item_id"]:
        problems.append("item_id")
    if (body.get("parentReference") or {}).get("driveId") != grant["drive_id"]:
        problems.append("drive_id")
    if str(ids.get("siteId", "")).lower() != grant["site_id"].lower():
        problems.append("site_id")
    if body.get("name") != grant["folder_name"]:  # defensa en profundidad: la identidad es por IDs; el nombre sólo se suma, nunca la reemplaza
        problems.append("name")
    if problems:
        raise ToolError("target_mismatch", "la metadata real del destino no coincide con la allowlist", fields=problems)
    if not isinstance(body.get("folder"), dict) or "file" in body:
        raise ToolError("target_not_a_folder", "el destino no es una carpeta: no se prepara ningún grant")
    return {"name": body.get("name"), "site_id": ids.get("siteId"), "list_id": ids.get("listId"), "list_item_id": ids.get("listItemId")}


def verify_identity(graph: Graph, expect_user: "str | None", *, required: bool) -> dict[str, Any]:
    """Identidad del usuario autenticado (GET /me, scope `User.Read`). Si se declaró `--expect-user`, debe coincidir; en apply/rollback es obligatoria."""
    res = graph.get(f"{GRAPH_ROOT}/me?$select=id,userPrincipalName,displayName")
    upn = str(res["body"].get("userPrincipalName") or "") if res["ok"] else ""
    if not upn:
        if required or expect_user:
            raise ToolError("identity_unverified", "no se pudo verificar la identidad del usuario autenticado (GET /me)", exit_code=2, graph=_graph_error(res))
        return {"verified": False, "graph": _graph_error(res)}
    if expect_user and upn.lower() != expect_user.strip().lower():
        raise ToolError("identity_mismatch", "el usuario autenticado no es el esperado", expected=expect_user.strip(), actual=upn)
    return {"verified": True, "id": res["body"].get("id"), "upn": upn, "display_name": res["body"].get("displayName"), "matches_expected": bool(expect_user)}


def _app_grants(perm: dict) -> list[tuple[str, list[str]]]:
    """(app id, roles) de los grants a APLICACIONES. `link.application` (app que creó un link) NO es un grant."""
    roles = [str(r).lower() for r in perm.get("roles") or []]
    out = []
    singles = [perm.get("grantedToV2"), perm.get("grantedTo")]
    multi = (perm.get("grantedToIdentitiesV2") or []) + (perm.get("grantedToIdentities") or [])
    for ident in singles + multi:
        app = ident.get("application") if isinstance(ident, dict) else None
        if isinstance(app, dict) and app.get("id"):
            out.append((str(app["id"]).lower(), roles))
    return out


def _enumerate(graph: Graph, source: str, url: "str | None", client_id: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"source": source, "complete": False, "count": 0, "matches": []}
    if url is None:
        return {**entry, "status": None, "note": "sin identificadores de lista/ítem: fuente no disponible"}
    pages = 0
    while url:
        res = graph.get(url)
        if not res["ok"]:
            return {**entry, "status": res["status"], "code": res["code"], "request_id": res["request_id"]}
        entry["status"] = res["status"]
        for perm in res["body"].get("value") or []:
            entry["count"] += 1
            for app_id, roles in _app_grants(perm):
                if app_id == client_id.lower():
                    entry["matches"].append({"roles": roles, "permission_id": perm.get("id")})
        pages += 1
        url = res["body"].get("@odata.nextLink")
        if url and (not url.startswith(GRAPH_ROOT + "/") or pages >= _MAX_PAGES):
            return {**entry, "note": "paginación no completada (enlace fuera de Graph o límite de páginas)"}
    entry["complete"] = True
    return entry


def enumerate_grants(graph: Graph, grant: dict[str, str], meta: dict[str, Any]) -> list[dict[str, Any]]:
    ids = (meta.get("site_id"), meta.get("list_id"), meta.get("list_item_id"))
    item_url = f"{GRAPH_ROOT}/sites/{_q(str(ids[0]))}/lists/{_q(str(ids[1]))}/items/{_q(str(ids[2]))}/permissions" if all(ids) else None
    return [_enumerate(graph, "driveItem.permissions", _item_permissions_url(grant), grant["client_id"]), _enumerate(graph, "listItem.permissions", item_url, grant["client_id"])]


def _matches(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"source": e["source"], **m} for e in evidence for m in e["matches"]]


def _incomplete(evidence: list[dict[str, Any]]) -> list[str]:
    return [e["source"] for e in evidence if not e["complete"]]


def absence_is_authoritative(evidence: list[dict[str, Any]]) -> "tuple[bool, list[str]]":
    """¿Hay evidencia AUTORITATIVA de que NO existe un grant equivalente? HOY: no, nunca. Sólo cambia con documentación oficial que lo respalde + GO del owner.
    - driveItem/permissions: 'effective SHARING permissions' (doc driveitem-list-permissions); un owner ve todos, un no-owner sólo los que le aplican: no es una fuente de grants Selected.
    - listItem/permissions: la doc no declara que sea una enumeración exhaustiva de grants Selected, ni que liste los creados vía driveItem; no hay control positivo validado.
    - los grants heredados de ancestros (herencia jerárquica) no se enumeran."""
    reasons = ["driveItem.permissions expone permisos de compartir, no grants Selected: no prueba ausencia",
               "listItem.permissions no está documentado como enumeración exhaustiva de grants Selected (sin control positivo validado)",
               "los grants heredados de ancestros no se enumeran"]
    reasons += [f"fuente incompleta o con error: {e['source']}" for e in evidence if not e["complete"]]
    return False, reasons


def classify(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    roles = {r for e in evidence for m in e["matches"] for r in m["roles"]}
    if roles & EQUIVALENT_ROLES:
        return {"state": "ALREADY_GRANTED", "reasons": ["evidencia autoritativa (presencia) de un grant equivalente"]}
    if roles:
        return {"state": "ROLE_INSUFFICIENT_PRESENT", "reasons": [f"la app ya tiene un grant con rol(es) {sorted(roles)}; cambiarlo es otra operación (no soportada acá)"]}
    absent, reasons = absence_is_authoritative(evidence)
    return {"state": "ABSENT_CONFIRMED", "reasons": []} if absent else {"state": "UNDETERMINED", "reasons": reasons}


def gate(state: dict[str, Any], token: dict[str, Any], evidence: list[dict[str, Any]], acks: dict[str, bool]) -> list[str]:
    """Compuertas evaluables ANTES de la confirmación interactiva. Vacío = el apply puede avanzar a la confirmación y a la relectura previa al POST."""
    blockers = []
    if state["state"] == "ALREADY_GRANTED":
        blockers.append("already_granted_nothing_to_do")
    elif state["state"] == "ROLE_INSUFFICIENT_PRESENT":
        blockers.append("existing_grant_with_insufficient_role")
    elif state["state"] != "ABSENT_CONFIRMED" and not acks.get("undetermined"):
        blockers.append("undetermined_not_acknowledged")
    if _incomplete(evidence):
        blockers.append("evidence_incomplete")
    if not token.get("can_write"):
        blockers.append("token_scope_insufficient_for_apply")
    if not acks.get("inheritance"):
        blockers.append("inheritance_break_not_acknowledged")
    return blockers


# ── interacción y auditoría ─────────────────────────────────────────────────────────────────────────────────
def _tty_prompt(text: str) -> str:
    if not sys.stdin.isatty():
        raise ToolError("interactive_required", "esta operación exige una terminal interactiva (TTY): no se puede ejecutar desde un proceso sin consola", exit_code=3)
    print(text, file=sys.stderr)
    return input("> ")


def _require_interactive(prompt: "Prompt | None") -> None:
    if prompt is None and not sys.stdin.isatty():
        raise ToolError("interactive_required", "esta operación exige una terminal interactiva (TTY): no se puede ejecutar desde un proceso sin consola", exit_code=3)


def _utc(now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))


def _write_audit(directory: str, grant_name: str, kind: str, payload: dict[str, Any], *, now: float) -> str:
    """Archivo NUEVO (modo `x`, nunca se pisa) con el reporte del paso; jamás contiene tokens. Si no se puede escribir, la operación aborta ANTES de tocar el recurso."""
    path = os.path.join(directory, f"{grant_name}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))}-{kind}.json")
    try:
        os.makedirs(directory, exist_ok=True)
        with open(path, "x", encoding="utf-8") as fh:
            json.dump({"tool": TOOL_ID, "kind": kind, "utc": _utc(now), **payload}, fh, indent=2, ensure_ascii=False)
    except OSError as exc:
        raise ToolError("audit_unwritable", f"no se pudo escribir el registro de auditoría ({type(exc).__name__}): no se ejecuta la operación", exit_code=2) from exc
    return path


def _grant_ids(grant: dict[str, str]) -> dict[str, str]:
    return {k: grant[k] for k in ("name", "tenant_id", "client_id", "site_id", "drive_id", "item_id", "folder_name")}


def _apply_prompt_text(grant: dict[str, str], meta: dict[str, Any], user: dict[str, Any], admin_client: str, preview: dict[str, Any], state: dict[str, Any]) -> str:
    return "\n".join([
        "=== CONFIRMACIÓN: crear UN grant Selected ===",
        f"Usuario autenticado  : {user.get('upn')}",
        f"App administrativa   : {admin_client}",
        f"Carpeta destino      : {meta.get('name')}  (item {grant['item_id']}, site {grant['site_id']})",
        f"App receptora (LOCAL): {grant['client_id']}   rol: {GRANT_ROLE}",
        f"Operación            : POST {preview['url']}",
        f"Cuerpo               : {json.dumps(preview['body'], separators=(',', ':'))}",
        f"Duplicados           : {state['state']} (reconocido): la ausencia no se puede probar; se relee antes del POST y se aborta ante cualquier coincidencia.",
        "Herencia             : la carpeta pasará a tener permisos propios (se rompe la herencia). Borrar el grant NO garantiza restaurarla.",
        "Un solo POST, sin reintentos.",
        f"Para continuar escribí exactamente: {CONFIRM_PHRASE}"])


def _rollback_prompt_text(grant: dict[str, str], meta: dict[str, Any], user: dict[str, Any], request: dict[str, Any], permission_id: str) -> str:
    return "\n".join([
        "=== CONFIRMACIÓN: revocar UN grant (rollback) ===",
        f"Usuario autenticado  : {user.get('upn')}",
        f"Carpeta              : {meta.get('name')}  (item {grant['item_id']})",
        f"Permiso a borrar     : {permission_id}  (app {grant['client_id']}, rol {GRANT_ROLE}, no heredado)",
        f"Operación            : DELETE {request['url']}",
        "Aviso                : borrar el grant NO garantiza restaurar la herencia de permisos de la carpeta (acción aparte en SharePoint).",
        f"Para continuar escribí exactamente: {ROLLBACK_PHRASE}"])


# ── orquestación ────────────────────────────────────────────────────────────────────────────────────────────
def acquire_token(args: argparse.Namespace, grant: dict[str, str], env: dict, *, transport, device_transport, sleep, clock, out, now: float, write: bool) -> "tuple[str, dict]":
    need = (["Sites.ReadWrite.All"] if write else ["Sites.Read.All"]) + ["User.Read"]
    env_token = (env.get(TOKEN_ENV) or "").strip()
    admin = _norm("guid", args.admin_client_id) if args.admin_client_id else None
    if admin and admin.lower() in {a.lower() for a in HUB_APP_IDS}:
        raise ToolError("token_rejected", "--admin-client-id no puede ser una app del Hub")
    if env_token:
        return env_token, _scope_gate(inspect_env_token(env_token, grant["tenant_id"], now=now, admin_client_id=admin))
    if admin:
        got = device_code_token(grant["tenant_id"], admin, need, transport=device_transport, sleep=sleep, clock=clock, out=out)
        claims = _decode_claims(got["access_token"])  # best-effort: si es legible, tenant y app deben coincidir con lo esperado
        if claims is not None:
            problems = []
            if str(claims.get("tid", "")).lower() != grant["tenant_id"].lower():
                problems.append("tenant_mismatch")
            if str(claims.get("azp") or claims.get("appid") or "").lower() != admin.lower():
                problems.append("admin_client_mismatch")
            if problems:
                raise ToolError("token_rejected", "el token obtenido no corresponde al tenant/app administrativa esperados", problems=problems)
        return got["access_token"], _scope_gate(got["info"])
    raise ToolError("auth_missing", f"sin credencial administrativa: definir {TOKEN_ENV} o pasar --admin-client-id (device code)")


def _require_apply_inputs(args: argparse.Namespace, prompt: "Prompt | None") -> None:
    """Compuertas que se evalúan ANTES de autenticar o llamar a Graph."""
    missing = [flag for flag, ok in (("--confirm-apply", args.confirm_apply == CONFIRM_PHRASE), ("--acknowledge-undetermined", args.acknowledge_undetermined),
                                     ("--acknowledge-inheritance-break", args.acknowledge_inheritance_break)) if not ok]
    if missing:
        raise ToolError("apply_not_confirmed", "faltan confirmaciones explícitas del owner", exit_code=3, missing=missing)
    if not args.admin_client_id:
        raise ToolError("admin_client_required", "--apply exige --admin-client-id (se valida la app administrativa del token)")
    if not args.expect_user:
        raise ToolError("expect_user_required", "--apply exige --expect-user (UPN del usuario que debe estar autenticado)")
    _require_interactive(prompt)


def _tail_reread(report: dict[str, Any], graph: Graph, grant: dict[str, str], meta: dict[str, Any]) -> None:
    try:
        report["evidence_after"] = enumerate_grants(graph, grant, meta)
    except ToolError as exc:
        report["evidence_after_error"] = exc.stage


def _record_result(report: dict[str, Any], args: argparse.Namespace, grant: dict[str, str], kind: str, now: float) -> None:
    try:
        report.setdefault("audit", {})["result"] = _write_audit(args.audit_dir, grant["name"], kind, {"ids": _grant_ids(grant), **{k: v for k, v in report.items() if k != "audit"}}, now=now)
    except ToolError as exc:  # el recurso ya fue tocado: no se enmascara el resultado; el reporte por stdout es el respaldo
        report["audit_error"] = exc.message


def _run_apply_tail(args, report, graph, grant, meta, evidence, state, user, preview, prompt, now) -> int:
    answer = (prompt or _tty_prompt)(_apply_prompt_text(grant, meta, user, args.admin_client_id, preview, state))
    if (answer or "").strip() != CONFIRM_PHRASE:
        raise ToolError("apply_not_confirmed", "la confirmación interactiva no coincide con la frase exacta", exit_code=3)
    fresh = enumerate_grants(graph, grant, meta)  # RELECTURA inmediatamente antes del POST
    report["evidence_before_post"] = fresh
    seen = _matches(fresh)
    if seen:
        report["observed_before_post"] = seen
        raise ToolError("duplicate_observed_before_post", "la relectura previa al POST muestra un grant de la app: no se envía nada", exit_code=3)
    if _incomplete(fresh):
        raise ToolError("evidence_incomplete_before_post", "la relectura previa al POST no pudo completarse: no se envía nada", exit_code=3, incomplete=_incomplete(fresh))
    report["audit"] = {"intent": _write_audit(args.audit_dir, grant["name"], "intent", {"ids": _grant_ids(grant), "user": user, "token": report["token"], "preview": preview,
                                                                                         "evidence_before": evidence, "evidence_before_post": fresh}, now=now)}
    res = graph.post_permission(preview)  # ÚNICO POST; sin reintentos
    report["post"] = _graph_error(res)
    if res.get("outcome_unknown"):
        report["stage"] = "post_outcome_unknown"
        report["error"] = "el POST pudo haberse ejecutado (corte/timeout sin respuesta): NO se reintenta; verificar manualmente antes de cualquier otra acción"
        _tail_reread(report, graph, grant, meta)
        report["next_step"] = "revisar evidence_after y el portal de SharePoint; si aparece el grant, usar su permission_id con --rollback si hace falta revertir"
        _record_result(report, args, grant, "result", now)
        return EXIT_OUTCOME_UNKNOWN
    if not res["ok"]:
        report["stage"] = "post"
        if res["status"] in (401, 403):
            report["diagnosis"] = ["scope del token insuficiente para administrar grants Selected (la doc de Selected habla de Sites.FullControl.All o Sites.Selected + Owner/FullControl)",
                                   "el usuario no es Propietario/Control total de la carpeta Hub (autorización efectiva)"]
        _record_result(report, args, grant, "result", now)
        return 1
    granted = (res["body"].get("grantedToV2") or {}).get("application") or {}
    report["post"].update({"permission_id": res["body"].get("id"), "roles": res["body"].get("roles"), "app_id": granted.get("id"),
                           "response_matches_request": str(granted.get("id", "")).lower() == grant["client_id"].lower() and res["body"].get("roles") == [GRANT_ROLE]})
    _tail_reread(report, graph, grant, meta)
    report["ok"] = bool(report["post"]["response_matches_request"])
    if not report["ok"]:
        report["stage"] = "post_response_mismatch"
    if report["post"].get("permission_id"):
        report["rollback_hint"] = f"--rollback --permission-id {report['post']['permission_id']} --audit-file <archivo result de auditoría> (nunca automático)"
    _record_result(report, args, grant, "result", now)
    return 0 if report["ok"] else 1


def _recorded_permission_ids(record: dict[str, Any]) -> set[str]:
    ids = {str(((record.get("post") or {}).get("permission_id")) or "")}
    for key in ("evidence_after", "evidence_before_post"):
        for e in record.get(key) or []:
            ids |= {str(m.get("permission_id") or "") for m in e.get("matches") or []}
    return {i for i in ids if i}


def _load_audit(path: str, grant: dict[str, str]) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ToolError("rollback_audit_unreadable", f"no se pudo leer el archivo de auditoría ({type(exc).__name__})") from exc
    if not isinstance(record, dict) or record.get("tool") != TOOL_ID or record.get("ids") != _grant_ids(grant):
        raise ToolError("rollback_audit_mismatch", "el archivo de auditoría no corresponde a esta herramienta o a este destino de la allowlist", exit_code=3)
    return record


def _run_rollback(args, report, grant, env, kw, prompt, now) -> int:
    if args.confirm_rollback != ROLLBACK_PHRASE:
        raise ToolError("rollback_not_confirmed", "falta la confirmación explícita (--confirm-rollback)", exit_code=3)
    for name, value in (("--permission-id", args.permission_id), ("--audit-file", args.audit_file), ("--admin-client-id", args.admin_client_id), ("--expect-user", args.expect_user)):
        if not value:
            raise ToolError("rollback_input_missing", f"--rollback exige {name}")
    _require_interactive(prompt)
    request = build_rollback_request(grant, args.permission_id)  # valida el formato
    record = _load_audit(args.audit_file, grant)
    if args.permission_id not in _recorded_permission_ids(record):
        raise ToolError("rollback_permission_not_recorded", "el permission_id no figura en el registro de auditoría de un --apply previo: no se borra nada", exit_code=3)
    token, info = acquire_token(args, grant, env, write=True, now=now, **kw)
    report["token"] = {k: info[k] for k in ("source", "client_id", "tenant_id", "scopes", "can_write")}
    if not info["can_write"]:
        raise ToolError("token_rejected", "el token no trae un scope de escritura de sitios (Sites.ReadWrite.All) necesario para borrar el permiso", exit_code=3, have=info["scopes"])
    graph = Graph(token, kw["transport"])
    meta = check_target(graph, grant)
    user = report["user"] = verify_identity(graph, args.expect_user, required=True)
    live = graph.get(request["url"])
    if not live["ok"]:
        raise ToolError("rollback_target_unreadable", "no se pudo leer el permiso a borrar (¿ya no existe?): no se borra nada", exit_code=1, graph=_graph_error(live))
    perm = live["body"]
    apps = _app_grants(perm)
    if not apps or any(a != grant["client_id"].lower() for a, _r in apps) or [r for _a, r in apps][0] != [GRANT_ROLE] or perm.get("inheritedFrom"):
        raise ToolError("rollback_target_mismatch", "el permiso vivo no es un grant `write` NO heredado de la app LOCAL: no se borra nada", exit_code=3)
    report["request"] = request
    answer = (prompt or _tty_prompt)(_rollback_prompt_text(grant, meta, user, request, args.permission_id))
    if (answer or "").strip() != ROLLBACK_PHRASE:
        raise ToolError("rollback_not_confirmed", "la confirmación interactiva no coincide con la frase exacta", exit_code=3)
    report["audit"] = {"intent": _write_audit(args.audit_dir, grant["name"], "rollback-intent", {"ids": _grant_ids(grant), "user": user, "token": report["token"], "request": request,
                                                                                                 "permission_id": args.permission_id, "source_audit": os.path.basename(args.audit_file)}, now=now)}
    res = graph.delete_permission(request)  # ÚNICO DELETE; sin reintentos
    report["delete"] = _graph_error(res)
    report["note"] = "borrar el grant NO garantiza restaurar la herencia de permisos de la carpeta: verificarlo en SharePoint"
    try:  # lectura de verificación (GET): ¿el permiso ya no existe?
        report["permission_gone"] = graph.get(request["url"])["status"] == 404
    except ToolError:
        report["permission_gone"] = None
    if res.get("outcome_unknown"):
        report.update({"stage": "delete_outcome_unknown", "error": "el DELETE pudo haberse ejecutado: NO se reintenta; ver permission_gone y verificar en SharePoint"})
        _record_result(report, args, grant, "rollback-result", now)
        return EXIT_OUTCOME_UNKNOWN
    if not res["ok"]:
        report["stage"] = "delete"
        _record_result(report, args, grant, "rollback-result", now)
        return 1
    report["ok"] = True
    _record_result(report, args, grant, "rollback-result", now)
    return 0


def run(args: argparse.Namespace, *, env: dict, transport=None, device_transport=None, sleep=time.sleep, clock=time.monotonic, out=None, now: "float | None" = None,
        prompt: "Prompt | None" = None) -> "tuple[int, dict]":
    mode = "apply" if args.apply else "rollback" if args.rollback else "dry-run" if args.dry_run else "check"
    report: dict[str, Any] = {"mode": mode, "ok": False}
    now = time.time() if now is None else now
    kw = {"transport": transport, "device_transport": device_transport, "sleep": sleep, "clock": clock, "out": out}
    try:
        grant = resolve_grant(args.tenant_id, args.client_id, args.site_id, args.drive_id, args.item_id)
        report["allowlist"] = grant["name"]
        if mode == "rollback":
            return _run_rollback(args, report, grant, env, kw, prompt, now), report
        if mode == "apply":
            _require_apply_inputs(args, prompt)
        acks = {"undetermined": args.acknowledge_undetermined, "inheritance": args.acknowledge_inheritance_break}
        token, info = acquire_token(args, grant, env, write=(mode == "apply" or args.request_write_scope), now=now, **kw)
        report["token"] = {k: info[k] for k in ("source", "client_id", "tenant_id", "scopes", "can_write")}
        graph = Graph(token, transport)
        meta = check_target(graph, grant)
        evidence = enumerate_grants(graph, grant, meta)
        state = classify(evidence)
        user = verify_identity(graph, args.expect_user, required=(mode == "apply"))
        preview = build_grant_request(grant)
        blockers = gate(state, info, evidence, acks)
        report.update({"target": meta, "user": user, "evidence": evidence, "duplicate_state": state, "preview": preview, "blockers": blockers, "would_apply": not blockers,
                       "notes": ["Sites.ReadWrite.All es condición necesaria según esta herramienta, pero NO está probado suficiente para administrar grants Selected: la prueba real es la respuesta del POST"]})
        if mode in ("check", "dry-run"):
            if mode == "dry-run":
                report["pending_at_apply"] = ["--confirm-apply", "--admin-client-id", "--expect-user", "confirmación interactiva (TTY)", "relectura de permisos inmediatamente antes del POST"]
            report["ok"] = True
            return 0, report
        if blockers:
            report["stage"] = "apply_blocked"
            return 3, report
        return _run_apply_tail(args, report, graph, grant, meta, evidence, state, user, preview, prompt, now), report
    except ToolError as exc:
        report.update({"stage": exc.stage, "error": exc.message, **exc.detail})
        return exc.exit_code, report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Concede un grant Selected `write` (Graph v1.0) a UNA app sobre UNA carpeta de SharePoint, con un único POST controlado y un rollback acotado.")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="solo lectura: valida y enumera")
    mode.add_argument("--dry-run", action="store_true", help="solo lectura: --check + identidad + preview exacto y compuertas")
    mode.add_argument("--apply", action="store_true", help="ÚNICO POST (exige confirmaciones explícitas e interactivas)")
    mode.add_argument("--rollback", action="store_true", help="ÚNICO DELETE del permission_id registrado (nunca automático)")
    for name in ("tenant-id", "client-id", "site-id", "drive-id", "item-id"):
        p.add_argument(f"--{name}", required=True)
    p.add_argument("--admin-client-id", help="app registration PÚBLICA del admin para device code (sin secreto); también se valida contra el token. Alternativa de credencial: variable " + TOKEN_ENV)
    p.add_argument("--request-write-scope", action="store_true", help="en check/dry-run pide Sites.ReadWrite.All (no escribe nada) para ensayar el consentimiento antes del apply")
    p.add_argument("--expect-user", help="UPN que debe estar autenticado (obligatorio en --apply/--rollback; opcional en check/dry-run)")
    p.add_argument("--acknowledge-undetermined", action="store_true", help="el owner reconoce que la ausencia de un grant previo NO se puede probar (estado UNDETERMINED)")
    p.add_argument("--acknowledge-inheritance-break", action="store_true", help="el owner reconoce que el grant rompe la herencia de permisos de la carpeta y que borrarlo no la restaura")
    p.add_argument("--confirm-apply", default="", help="frase exacta requerida por --apply")
    p.add_argument("--confirm-rollback", default="", help="frase exacta requerida por --rollback")
    p.add_argument("--permission-id", help="--rollback: permission_id registrado por un --apply previo")
    p.add_argument("--audit-file", help="--rollback: archivo de auditoría (result) del --apply que creó el permiso")
    p.add_argument("--audit-dir", default=AUDIT_DEFAULT_DIR, help="carpeta local donde --apply/--rollback registran su auditoría (archivos nuevos; check/dry-run no escriben)")
    return p


def main(argv: "list[str] | None" = None, *, env: "dict | None" = None, stdout=None, stderr=None, **kw: Any) -> int:
    args = build_parser().parse_args(argv)
    code, report = run(args, env=dict(os.environ if env is None else env), out=stderr or sys.stderr, **kw)
    print(json.dumps(report, indent=2, ensure_ascii=False), file=stdout or sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
