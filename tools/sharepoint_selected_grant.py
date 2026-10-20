"""sharepoint_selected_grant.py — concede (CHECKPOINT ACTUAL: sólo PREPARA) un grant Selected `write` a UNA aplicación Entra sobre UNA carpeta de SharePoint.

Herramienta ADMINISTRATIVA, independiente del runtime web y del uploader de CCHH: sólo stdlib, no importa `webapp`/`config`/`core`, no usa el token del Hub y no agrega infraestructura.
Operación de fondo (Graph v1.0, doc oficial `driveitem-post-permissions`):

    POST /drives/{drive-id}/items/{item-id}/permissions
    {"grantedToV2": {"application": {"id": "<client id>"}}, "roles": ["write"]}

Modos (exactamente uno):
  --check    SOLO LECTURA: valida allowlist + token, lee la metadata del destino (debe ser CARPETA) y enumera lo que Graph deja ver. Nunca emite un POST.
  --dry-run  Lo de --check + el preview EXACTO del request y el veredicto de las compuertas (`would_apply`). Nunca emite un POST.
  --apply    ÚNICA operación de escritura (1 POST, sin reintentos). DESHABILITADO en este checkpoint (`APPLY_ENABLED = False`): termina con código 3 antes de autenticar o llamar a Graph.

Invocación (todos los identificadores son OBLIGATORIOS y se validan contra la allowlist; no hay valores por defecto que puedan apuntar a otro destino):

    python tools/sharepoint_selected_grant.py --dry-run \
        --tenant-id f287b953-5428-4a5e-ae17-bd9c4cfaedbe --client-id ad98b5cd-71d0-451f-8d70-e8581a4788fa \
        --site-id a129a1d0-a4ee-4438-8de8-9e39e7007764 \
        --drive-id "b!0KEpoe6kOESN6J455wB3ZLYmlHEbGsZPlL4_LU8lfDroKl3b1Ti0RYJfK4nkJLM3" --item-id 01U7IMSJOGXNT3ZNP5NVAKDFLQWY3L6OMQ \
        --admin-client-id <app registration PÚBLICA del admin, ver doc>

Autenticación (identidad ADMINISTRATIVA independiente; nunca contraseñas, secretos ni tokens en argumentos CLI, código ni logs):
  1. Variable de entorno `DANWOR_GRAPH_ADMIN_TOKEN` (token delegado obtenido por el admin con sus propias herramientas), o
  2. device code flow (RFC 8628, cliente PÚBLICO, sin secreto) con `--admin-client-id`, contra la autoridad del tenant de la allowlist. Sólo se pide `Sites.Read.All` (check/dry-run) o `Sites.ReadWrite.All` (apply).
El token del Hub (apps `ad98b5cd…`/`eeca6df8…`) se rechaza: un grant no puede concederse con la identidad de la app que lo recibe.

Dos niveles de autorización que NO son lo mismo (ver tools/SHAREPOINT_SELECTED_GRANT.md): (a) permiso Graph del CLIENTE administrativo (scope del token) y (b) autorización EFECTIVA del usuario sobre la carpeta
(propietario/Control total). Un token con el scope correcto y un usuario sin ese rol termina en 403 en el POST.

Evidencia de duplicados: sólo la PRESENCIA de un grant equivalente es autoritativa. Ningún endpoint documentado prueba la AUSENCIA (driveItem/permissions lista permisos de COMPARTIR; listItem/permissions no está
documentado como enumeración exhaustiva de grants Selected). Por eso `absence_is_authoritative` devuelve siempre False y `--apply` se detiene en estado UNDETERMINED: no se simula una idempotencia que no existe."""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
GRAPH_RESOURCE = "https://graph.microsoft.com"
GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
LOGIN_ROOT = "https://login.microsoftonline.com"
TOKEN_ENV = "DANWOR_GRAPH_ADMIN_TOKEN"

# Checkpoint actual: el POST existe y está probado con transporte simulado, pero NO se puede disparar.
APPLY_ENABLED = False
CONFIRM_PHRASE = "CONCEDER GRANT WRITE"

# ── allowlist ───────────────────────────────────────────────────────────────────────────────────────────────
TENANT_ID = "f287b953-5428-4a5e-ae17-bd9c4cfaedbe"
LOCAL_APP_ID = "ad98b5cd-71d0-451f-8d70-e8581a4788fa"
PRODUCTION_APP_ID = "eeca6df8-2436-422f-a1b8-05ff9e8ff653"  # RESERVADA: NO es destino ejecutable en este checkpoint
HUB_APP_IDS = frozenset({LOCAL_APP_ID, PRODUCTION_APP_ID})
# Agregar una fila (p. ej. la carpeta `CCHH HUB` del sitio Comunicaciones + la app de producción) requiere un cambio de código y un GO explícito del owner.
ALLOWED_GRANTS: tuple[dict[str, str], ...] = (
    {"name": "control-local", "tenant_id": TENANT_ID, "client_id": LOCAL_APP_ID, "site_id": "a129a1d0-a4ee-4438-8de8-9e39e7007764",
     "drive_id": "b!0KEpoe6kOESN6J455wB3ZLYmlHEbGsZPlL4_LU8lfDroKl3b1Ti0RYJfK4nkJLM3", "item_id": "01U7IMSJOGXNT3ZNP5NVAKDFLQWY3L6OMQ"},
)
GRANT_ROLE = "write"
EQUIVALENT_ROLES = frozenset({"write", "owner", "fullcontrol"})

# Permisos Graph DELEGADOS del cliente administrativo (doc: driveitem-post-permissions / listitem-list-permissions / driveitem-list-permissions).
READ_SCOPES = frozenset({"Sites.Read.All", "Sites.ReadWrite.All", "Sites.Manage.All", "Sites.FullControl.All"})
WRITE_SCOPES = frozenset({"Sites.ReadWrite.All", "Sites.Manage.All", "Sites.FullControl.All"})

_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_DRIVE = re.compile(r"^b![A-Za-z0-9_-]+$")
_ITEM = re.compile(r"^[A-Za-z0-9!_-]+$")
_MAX_PAGES = 10
_TIMEOUT = 30

Transport = Callable[[str, str, dict, "bytes | None"], "tuple[int, dict, bytes]"]


class ToolError(Exception):
    """Falla controlada: `stage` identifica la compuerta; `exit_code` 2 (validación/auth) o 1 (Graph/red)."""

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
        raise ToolError("config_rejected", "la aplicación de producción está RESERVADA y no es destino ejecutable en este checkpoint")
    for row in ALLOWED_GRANTS:
        if all(row[k].lower() == wanted[k].lower() if k not in ("drive_id", "item_id") else row[k] == wanted[k] for k in wanted):
            return dict(row)
    mismatched = sorted(k for k in wanted if not any((row[k].lower() == wanted[k].lower()) if k not in ("drive_id", "item_id") else row[k] == wanted[k] for row in ALLOWED_GRANTS))
    raise ToolError("config_rejected", "la combinación tenant/cliente/site/drive/item no está en la allowlist", fields=mismatched or ["combination"])


def build_grant_request(grant: dict[str, str]) -> dict[str, Any]:
    """Preview EXACTO del único POST: body mínimo, sin campos extra, rol fijo `write`."""
    body = {"grantedToV2": {"application": {"id": grant["client_id"]}}, "roles": [GRANT_ROLE]}
    return {"method": "POST", "url": f"{GRAPH_ROOT}/drives/{_q(grant['drive_id'])}/items/{_q(grant['item_id'])}/permissions", "body": body}


def _q(segment: str) -> str:
    return urllib.parse.quote(segment, safe="!")


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


def inspect_env_token(token: str, tenant_id: str, *, now: float) -> dict[str, Any]:
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
        raise ToolError("auth_failed", "el tenant no entregó un device code", exit_code=2, error=_oauth_error(start), status=status)
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
        raise ToolError("auth_failed", "el login administrativo no se completó", error=err, status=status)


def _json(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _oauth_error(body: dict) -> str:
    return str(body.get("error", "unknown"))[:80]


# ── Graph (GET libre; un único POST permitido) ───────────────────────────────────────────────────────────────
class Graph:
    """Cliente mínimo. Sin reintentos. Sólo GET y el POST de permisos: NO existe PUT/DELETE/PATCH. El token vive acá y no sale en ningún resultado."""

    def __init__(self, token: str, transport: "Transport | None" = None):
        self._token = token
        self._transport = transport or _http_transport
        self.calls: list[tuple[str, str]] = []  # (método, ruta sin query) — para auditoría/tests; sin headers ni token

    def _call(self, method: str, url: str, body: "dict | None" = None) -> dict[str, Any]:
        if method not in ("GET", "POST"):
            raise ValueError(f"método no permitido: {method}")
        if not url.startswith(GRAPH_ROOT + "/"):
            raise ToolError("url_rejected", "URL fuera de Graph v1.0 (el token no se envía a otros hosts)")
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.calls.append((method, url.split("?")[0].replace(GRAPH_ROOT, "")))
        status, resp_headers, raw = self._transport(method, url, headers, data)
        payload = _json(raw)
        err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        lower = {k.lower(): v for k, v in resp_headers.items()}
        return {"ok": 200 <= status < 300, "status": status, "body": payload, "code": err.get("code"), "message": str(err.get("message", ""))[:300] or None,
                "request_id": lower.get("request-id") or (err.get("innerError") or {}).get("request-id"), "client_request_id": lower.get("client-request-id")}

    def get(self, url: str) -> dict[str, Any]:
        return self._call("GET", url)

    def post_permission(self, preview: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", preview["url"], preview["body"])


def _graph_error(res: dict[str, Any]) -> dict[str, Any]:
    return {k: res.get(k) for k in ("status", "code", "message", "request_id", "client_request_id")}


# ── destino y enumeración ───────────────────────────────────────────────────────────────────────────────────
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
    if problems:
        raise ToolError("target_mismatch", "la metadata real del destino no coincide con la allowlist", fields=problems)
    if not isinstance(body.get("folder"), dict) or "file" in body:
        raise ToolError("target_not_a_folder", "el destino no es una carpeta: no se prepara ningún grant")
    return {"name": body.get("name"), "site_id": ids.get("siteId"), "list_id": ids.get("listId"), "list_item_id": ids.get("listItemId")}


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
                    entry["matches"].append({"roles": roles})
        pages += 1
        url = res["body"].get("@odata.nextLink")
        if url and (not url.startswith(GRAPH_ROOT + "/") or pages >= _MAX_PAGES):
            return {**entry, "note": "paginación no completada (enlace fuera de Graph o límite de páginas)"}
    entry["complete"] = True
    return entry


def enumerate_grants(graph: Graph, grant: dict[str, str], meta: dict[str, Any]) -> list[dict[str, Any]]:
    drive_url = f"{GRAPH_ROOT}/drives/{_q(grant['drive_id'])}/items/{_q(grant['item_id'])}/permissions"
    ids = (meta.get("site_id"), meta.get("list_id"), meta.get("list_item_id"))
    item_url = f"{GRAPH_ROOT}/sites/{_q(str(ids[0]))}/lists/{_q(str(ids[1]))}/items/{_q(str(ids[2]))}/permissions" if all(ids) else None
    return [_enumerate(graph, "driveItem.permissions", drive_url, grant["client_id"]), _enumerate(graph, "listItem.permissions", item_url, grant["client_id"])]


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


def gate(state: dict[str, Any], token: dict[str, Any]) -> list[str]:
    blockers = []
    if not APPLY_ENABLED:
        blockers.append("apply_disabled_in_this_checkpoint")
    if state["state"] == "ALREADY_GRANTED":
        blockers.append("already_granted_nothing_to_do")
    elif state["state"] == "ROLE_INSUFFICIENT_PRESENT":
        blockers.append("existing_grant_with_insufficient_role")
    elif state["state"] != "ABSENT_CONFIRMED":
        blockers.append("duplicate_evidence_not_authoritative")
    if not token.get("can_write"):
        blockers.append("token_scope_insufficient_for_apply")
    return blockers


# ── orquestación ────────────────────────────────────────────────────────────────────────────────────────────
def acquire_token(args: argparse.Namespace, grant: dict[str, str], env: dict, *, transport, device_transport, sleep, clock, out, now: float) -> "tuple[str, dict]":
    need = ["Sites.ReadWrite.All"] if args.apply else ["Sites.Read.All"]
    env_token = (env.get(TOKEN_ENV) or "").strip()
    if env_token:
        return env_token, _scope_gate(inspect_env_token(env_token, grant["tenant_id"], now=now))
    if args.admin_client_id:
        admin = _norm("guid", args.admin_client_id)
        if admin.lower() in {a.lower() for a in HUB_APP_IDS}:
            raise ToolError("token_rejected", "--admin-client-id no puede ser una app del Hub")
        got = device_code_token(grant["tenant_id"], admin, need, transport=device_transport, sleep=sleep, clock=clock, out=out)
        return got["access_token"], _scope_gate(got["info"])
    raise ToolError("auth_missing", f"sin credencial administrativa: definir {TOKEN_ENV} o pasar --admin-client-id (device code)")


def run(args: argparse.Namespace, *, env: dict, transport=None, device_transport=None, sleep=time.sleep, clock=time.monotonic, out=None, now: "float | None" = None) -> "tuple[int, dict]":
    mode = "apply" if args.apply else "dry-run" if args.dry_run else "check"
    report: dict[str, Any] = {"mode": mode, "ok": False}
    try:
        grant = resolve_grant(args.tenant_id, args.client_id, args.site_id, args.drive_id, args.item_id)
        report["allowlist"] = grant["name"]
        if args.apply and not APPLY_ENABLED:
            raise ToolError("apply_disabled", "--apply está DESHABILITADO en este checkpoint; no se autenticó ni se llamó a Graph", exit_code=3)
        if args.apply and args.confirm_apply != CONFIRM_PHRASE:
            raise ToolError("apply_not_confirmed", "falta la confirmación explícita (--confirm-apply)", exit_code=3)
        token, info = acquire_token(args, grant, env, transport=transport, device_transport=device_transport, sleep=sleep, clock=clock, out=out, now=time.time() if now is None else now)
        report["token"] = {k: info[k] for k in ("source", "client_id", "tenant_id", "scopes", "can_write")}
        graph = Graph(token, transport)
        meta = check_target(graph, grant)
        evidence = enumerate_grants(graph, grant, meta)
        state = classify(evidence)
        preview = build_grant_request(grant)
        blockers = gate(state, info)
        report.update({"target": meta, "evidence": evidence, "duplicate_state": state, "preview": preview, "blockers": blockers, "would_apply": not blockers})
        if mode != "apply":
            report["ok"] = True
            return 0, report
        if blockers:
            report["stage"] = "apply_blocked"
            return 3, report
        res = graph.post_permission(preview)  # ÚNICO POST; sin reintentos
        report["post"] = _graph_error(res)
        if not res["ok"]:
            report["stage"] = "post"
            return 1, report
        granted = (res["body"].get("grantedToV2") or {}).get("application") or {}
        report["post"].update({"permission_id": res["body"].get("id"), "roles": res["body"].get("roles"), "app_id": granted.get("id"),
                               "response_matches_request": str(granted.get("id", "")).lower() == grant["client_id"].lower() and res["body"].get("roles") == [GRANT_ROLE]})
        report["evidence_after"] = enumerate_grants(graph, grant, meta)
        report["ok"] = bool(report["post"]["response_matches_request"])
        return (0 if report["ok"] else 1), report
    except ToolError as exc:
        report.update({"stage": exc.stage, "error": exc.message, **exc.detail})
        return exc.exit_code, report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Prepara un grant Selected `write` (Graph v1.0) para UNA app sobre UNA carpeta de SharePoint. --apply está deshabilitado en este checkpoint.")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="solo lectura: valida y enumera")
    mode.add_argument("--dry-run", action="store_true", help="solo lectura: --check + preview exacto y compuertas")
    mode.add_argument("--apply", action="store_true", help="DESHABILITADO en este checkpoint")
    for name in ("tenant-id", "client-id", "site-id", "drive-id", "item-id"):
        p.add_argument(f"--{name}", required=True)
    p.add_argument("--admin-client-id", help="app registration PÚBLICA del admin para device code (sin secreto); alternativa: variable " + TOKEN_ENV)
    p.add_argument("--confirm-apply", default="", help="frase exacta requerida por --apply")
    return p


def main(argv: "list[str] | None" = None, *, env: "dict | None" = None, stdout=None, stderr=None, **kw: Any) -> int:
    args = build_parser().parse_args(argv)
    code, report = run(args, env=dict(os.environ if env is None else env), out=stderr or sys.stderr, **kw)
    print(json.dumps(report, indent=2, ensure_ascii=False), file=stdout or sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
