"""sharepoint_graph_diagnostics.py — diagnóstico SEGURO del upload de CVs de Capital Humano a SharePoint.

POR QUÉ EXISTE: `profile_document_service` aplana TODA falla de Graph a tres mensajes genéricos (sin token, 403 → «sin permiso», cualquier otro código → «no pudimos completar») y descarta
el cuerpo real del error (`error.code`/`message`/`innerError`). Con eso no se puede distinguir «el token nunca se obtuvo», «Graph rechazó con 403 por una concesión por-recurso ausente»,
«el item raíz no existe/no es visible» o «el sitio/drive están mal». Este módulo SÓLO agrega observabilidad; NO cambia ninguna decisión del uploader.

QUÉ NUNCA SE REGISTRA NI SE DEVUELVE: el access token, secretos, cookies, cabeceras `Authorization`, el cuerpo del archivo, ni la query string de las URLs. Del token se resume únicamente un
subconjunto de claims NO sensibles (aud, scp, roles, appid, tid, exp, ver, idtyp) decodificado SIN verificar la firma (sólo diagnóstico; nada se autoriza con esto).

SONDA DE LECTURA (`run_probe`): SÓLO GET, con el MISMO token y las MISMAS formas de URL que el uploader (`/sites/{site}/drives/{drive}/items/{item}`), para poder atribuir el nivel exacto donde
se rompe (site → drive → item → carpeta por path → listado → grants). No escribe nada.
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
from datetime import datetime, timezone

import config
from core import microsoft365

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
LOGGER = logging.getLogger("danwor.cchh.sharepoint")

_MAX_ERROR_BODY = 8192
_PROBE_FOLDER_NAME = "__dw_diagnostic_probe_does_not_exist__"      # lookup por path de una carpeta inexistente: misma forma que el primer GET del uploader; se espera 404
_SAFE_TOKEN_CLAIMS = ("aud", "appid", "azp", "tid", "ver", "idtyp", "iss")


def sanitize_endpoint(url: str) -> str:
    """URL sin query string ni fragmento (nunca viaja un token ni un parámetro sensible); se conservan los ids de site/drive/item, que no son secretos."""
    parts = urllib.parse.urlsplit(url or "")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def graph_error_detail(exc: BaseException) -> dict:
    """Resumen estructurado de una falla de Graph: SIN tocar tokens ni cabeceras de petición. Lee el cuerpo (acotado) de un `HTTPError`, que el uploader descartaba."""
    if isinstance(exc, urllib.error.HTTPError):
        detail: dict = {"kind": "http", "status": exc.code, "reason": str(exc.reason or "")[:120]}
        try:
            raw = exc.read(_MAX_ERROR_BODY)
        except Exception:  # noqa: BLE001 — el cuerpo ya se consumió o el socket cerró: no es motivo para perder el resto del diagnóstico
            raw = b""
        error = {}
        if raw:
            try:
                parsed = json.loads(raw.decode("utf-8", errors="replace"))
                error = parsed.get("error") if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict) else {}
            except ValueError:
                detail["body_not_json"] = True
        inner = error.get("innerError") if isinstance(error.get("innerError"), dict) else {}
        detail.update({
            "code": error.get("code"), "message": (error.get("message") or "")[:400] or None,
            "inner_code": inner.get("code"), "request_id": inner.get("request-id"), "client_request_id": inner.get("client-request-id"),
            "date": inner.get("date"),
        })
        headers = getattr(exc, "headers", None)
        if headers is not None:
            detail["www_authenticate"] = (headers.get("WWW-Authenticate") or "")[:300] or None       # p. ej. `insufficient_claims`/`invalid_token`: SIN token (es una cabecera de RESPUESTA)
            detail["request_id"] = detail.get("request_id") or headers.get("request-id")
            detail["client_request_id"] = detail.get("client_request_id") or headers.get("client-request-id")
        return detail
    if isinstance(exc, urllib.error.URLError):
        return {"kind": "transport", "status": None, "reason": str(getattr(exc, "reason", exc))[:200]}
    return {"kind": type(exc).__name__, "status": None, "reason": str(exc)[:200]}


def log_graph_failure(step: str, method: str, url: str, exc: BaseException, *, level: int = logging.WARNING) -> dict:
    """Registra UNA línea JSON por falla (WARNING por defecto) y devuelve el mismo detalle. Nunca lanza."""
    try:
        detail = graph_error_detail(exc)
        record = {"event": "cchh_sharepoint_graph_failure", "step": step, "method": method, "endpoint": sanitize_endpoint(url), **detail}
        LOGGER.log(level, json.dumps(record, ensure_ascii=False, sort_keys=True))
        return detail
    except Exception:  # noqa: BLE001 — la observabilidad jamás puede romper el flujo del uploader
        return {}


def token_claims_summary(token: str) -> dict:
    """Subconjunto NO sensible de claims del access token (sin verificar firma). Sirve para responder «¿qué scopes/audiencia/app efectivos tiene el token que usa el uploader?»."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {"decodable": False}
    scp = [s for s in str(claims.get("scp") or "").split(" ") if s]
    roles = [str(r) for r in (claims.get("roles") or [])] if isinstance(claims.get("roles"), list) else []
    exp = claims.get("exp")
    summary = {"decodable": True, "scp": sorted(scp), "roles": sorted(roles), "mode": "delegated" if scp else ("app-only" if roles else "unknown")}
    summary.update({k: claims[k] for k in _SAFE_TOKEN_CLAIMS if k in claims})
    if isinstance(exp, (int, float)):
        summary["exp_utc"] = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat(timespec="seconds")
    write_scope = config.HC_DOCUMENT_WRITE_SCOPE
    summary["configured_write_scope"] = write_scope or None
    summary["write_scope_in_token"] = bool(write_scope) and write_scope in scp
    summary["configured_client_id"] = config.AZURE_CLIENT_ID or None
    return summary


# ---------------------------------------------------------------------------
# Sonda de lectura
# ---------------------------------------------------------------------------

def _step(label: str, url: str, token: str) -> tuple[dict, dict | None]:
    """UN GET. Devuelve (registro del paso, JSON o None). Nunca lanza; jamás incluye el token."""
    record: dict = {"step": label, "method": "GET", "endpoint": sanitize_endpoint(url)}
    try:
        body = microsoft365.graph_get_json(url, token)
        record.update({"status": 200, "ok": True})
        return record, body
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        detail = graph_error_detail(exc)
        record.update({"ok": False, **detail})
        return record, None
    except Exception as exc:  # noqa: BLE001 — respuesta no-JSON u otro: se reporta, no se propaga
        record.update({"ok": False, "status": None, "kind": type(exc).__name__, "reason": str(exc)[:200]})
        return record, None


def _grants_summary(body: dict | None) -> dict:
    """De `GET .../permissions`: sólo roles y la identidad de APLICACIÓN (id + displayName) de cada grant. De usuarios/grupos únicamente se cuenta (no se vuelcan correos)."""
    apps, others = [], 0
    for perm in (body or {}).get("value", []) or []:
        roles = perm.get("roles") or []
        identities = []
        v2 = perm.get("grantedToV2") or {}
        if v2:
            identities.append(v2)
        identities.extend(perm.get("grantedToIdentitiesV2") or [])
        found_app = False
        for ident in identities:
            app = ident.get("application") if isinstance(ident, dict) else None
            if app:
                apps.append({"app_id": app.get("id"), "display_name": app.get("displayName"), "roles": sorted(roles)})
                found_app = True
        if not found_app:
            others += 1
    client_id = (config.AZURE_CLIENT_ID or "").lower()
    return {"application_grants": apps, "other_grants_count": others, "hub_app_has_grant": any((a.get("app_id") or "").lower() == client_id for a in apps) if client_id else None}


def probe_target(label: str, site_id: str, drive_id: str, item_id: str, token: str) -> dict:
    """Sonda de LECTURA sobre un destino. MISMAS formas de URL que `profile_document_service._drive_items_base`: `/sites/{site}/drives/{drive}/items/{item}`."""
    base = f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/items/{item_id}"
    steps: list[dict] = []

    def run(name: str, url: str):
        record, body = _step(f"{label}:{name}", url, token)
        steps.append(record)
        return body

    run("site", f"{GRAPH_BASE}/sites/{site_id}?$select=id,displayName,webUrl")
    run("drive", f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}?$select=id,name,driveType,webUrl")
    item = run("folder_metadata", f"{base}?$select=id,name,webUrl,folder,parentReference")
    run("children_top1", f"{base}/children?$top=1&$select=id,name,folder")
    run("path_lookup_nonexistent (esperado 404)", f"{base}:/{_PROBE_FOLDER_NAME}")
    perms = run("item_permissions", f"{base}/permissions?$select=id,roles,grantedToV2,grantedToIdentitiesV2")
    return {"label": label, "site_id": site_id, "drive_id": drive_id, "item_id": item_id,
            "folder": {"name": (item or {}).get("name"), "web_url": (item or {}).get("webUrl")},
            "grants": _grants_summary(perms) if perms is not None else None, "steps": steps}


def sharing_id(sharing_url: str) -> str:
    """Codificación documentada de Graph para `/shares/{id}`: `u!` + base64url(url) sin padding."""
    return "u!" + base64.urlsafe_b64encode(sharing_url.encode("utf-8")).decode("ascii").rstrip("=")


def discover_sharing_link(sharing_url: str, token: str) -> tuple[dict, dict | None]:
    """DISCOVERY únicamente (no es el contrato del uploader): resuelve un sharing link a site_id/drive_id/item_id vía `/shares/{id}/driveItem`."""
    url = f"{GRAPH_BASE}/shares/{sharing_id(sharing_url)}/driveItem?$select=id,name,webUrl,folder,parentReference"
    record, body = _step("control:discover_sharing_link", url, token)
    record["endpoint"] = f"{GRAPH_BASE}/shares/{{u!base64url(sharing_url)}}/driveItem"        # el id codifica la URL: no se devuelve tal cual
    found = None
    if body:
        parent = body.get("parentReference") or {}
        found = {"site_id": parent.get("siteId"), "drive_id": parent.get("driveId"), "item_id": body.get("id"), "name": body.get("name"), "web_url": body.get("webUrl"),
                 "is_folder": "folder" in body}
    return record, found


def current_target() -> dict:
    return {"site_id": config.HC_SHAREPOINT_SITE_ID, "drive_id": config.HC_SHAREPOINT_DRIVE_ID, "item_id": config.HC_CV_ROOT_ITEM_ID}


def run_probe(token: str, *, control_sharing_url: str = "") -> dict:
    """Sonda completa de LECTURA: claims del token + destino ACTUAL (config) + (opcional) destino de CONTROL descubierto desde un sharing link. Sólo GET."""
    result: dict = {"token": token_claims_summary(token), "current": None, "control": None}
    cur = current_target()
    if all(cur.values()):
        result["current"] = probe_target("current", cur["site_id"], cur["drive_id"], cur["item_id"], token)
    else:
        result["current"] = {"label": "current", "error": "CONFIG_INCOMPLETE: falta HC_SHAREPOINT_SITE_ID / HC_SHAREPOINT_DRIVE_ID / HC_CV_ROOT_ITEM_ID", "steps": []}
    url = (control_sharing_url or "").strip()
    if url:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc.lower().endswith(".sharepoint.com"):
            result["control"] = {"label": "control", "error": "El link de control tiene que ser https://*.sharepoint.com/…", "steps": []}
        else:
            record, found = discover_sharing_link(url, token)
            if found and found["site_id"] and found["drive_id"]:
                control = probe_target("control", found["site_id"], found["drive_id"], found["item_id"], token)
                control["discovery"] = record
                control["site_path"] = parsed.path.split("/s/", 1)[1].split("/", 1)[0] if "/s/" in parsed.path else None
                control["hostname"] = parsed.netloc
                control["is_folder"] = found["is_folder"]
                result["control"] = control
            else:
                result["control"] = {"label": "control", "discovery": record, "error": "No se pudo resolver el sharing link a site/drive/item.", "steps": []}
    return result
