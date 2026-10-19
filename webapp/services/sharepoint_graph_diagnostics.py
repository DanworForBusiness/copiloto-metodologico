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

import config
from core import microsoft365

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
LOGGER = logging.getLogger("danwor.cchh.sharepoint")

_MAX_ERROR_BODY = 8192
_PROBE_FOLDER_NAME = "__dw_diagnostic_probe_does_not_exist__"      # lookup por path de una carpeta inexistente: misma forma que el primer GET del uploader; se espera 404
_WRITE_ROLES = {"write", "owner", "sp.full control"}                 # roles de un grant de aplicación que permiten escribir
_SELECTED_SCOPE = "Files.SelectedOperations.Selected"


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
    """ÚNICOS claims del access token que se exponen (decodificado SIN verificar la firma; sólo diagnóstico): aud, tid, appid/azp y scp. NUNCA usuario/correo/nombre/oid ni el token."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {"decodable": False}
    scp = sorted(s for s in str(claims.get("scp") or "").split(" ") if s)
    app_id = claims.get("appid") or claims.get("azp")                      # appid = tokens v1, azp = tokens v2: el que corresponda
    write_scope = config.HC_DOCUMENT_WRITE_SCOPE
    client_id = (config.AZURE_CLIENT_ID or "").lower()
    return {
        "decodable": True,
        "aud": claims.get("aud"), "tid": claims.get("tid"), "client_application_id": app_id, "scp": scp,
        "files_selectedoperations_selected_present": _SELECTED_SCOPE in scp,
        "sites_read_all_present": "Sites.Read.All" in scp,
        "configured_write_scope": write_scope or None, "configured_write_scope_in_token": bool(write_scope) and write_scope in scp,
        "token_app_matches_configured_client_id": (str(app_id or "").lower() == client_id) if client_id else None,
    }


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


def _application_grants(items: list[dict]) -> list[dict]:
    """De los `permissions` de un driveItem se extraen SÓLO los grants a APLICACIONES (id, displayName, roles, id del permission). Usuarios y grupos no se devuelven ni se cuentan."""
    grants = []
    for perm in items:
        identities = []
        if isinstance(perm.get("grantedToV2"), dict):
            identities.append(perm["grantedToV2"])
        if isinstance(perm.get("grantedTo"), dict):
            identities.append(perm["grantedTo"])
        for key in ("grantedToIdentitiesV2", "grantedToIdentities"):
            identities.extend(i for i in (perm.get(key) or []) if isinstance(i, dict))
        for ident in identities:
            app = ident.get("application")
            if isinstance(app, dict) and app.get("id"):
                grants.append({"application_id": app.get("id"), "display_name": app.get("displayName"), "roles": sorted(perm.get("roles") or []), "permission_id": perm.get("id")})
    return grants


def app_grant_for(client_application_id: str | None, permissions: list[dict] | None) -> dict:
    """Busca el grant cuyo `application.id` == el id de aplicación del TOKEN. `found: None` si los permissions no se pudieron leer (no es lo mismo que «no hay grant»)."""
    if permissions is None:
        return {"found": None, "reason": "permissions no legibles con este token/usuario"}
    wanted = str(client_application_id or "").lower()
    grants = _application_grants(permissions)
    mine = [g for g in grants if str(g["application_id"]).lower() == wanted] if wanted else []
    return {
        "found": bool(mine), "application_id": mine[0]["application_id"] if mine else None,
        "roles": sorted({r for g in mine for r in g["roles"]}), "permission_id": mine[0]["permission_id"] if mine else None,
        "has_write_role": any(set(g["roles"]) & _WRITE_ROLES for g in mine),
        "other_application_grants": [g for g in grants if g not in mine],          # grants a OTRAS aplicaciones (p. ej. un app registration distinto): sólo id/nombre/roles
    }


def _read_permissions(drive_id: str, item_id: str, token: str) -> tuple[dict, list[dict] | None]:
    """`GET /drives/{drive}/items/{item}/permissions` (con paginado por `@odata.nextLink`, tope de 5 páginas). Sólo lectura."""
    url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/permissions"
    first_record, items, page = None, [], 0
    while url and page < 5:
        record, body = _step("permissions", url, token)
        first_record = first_record or record
        if body is None:
            return record, None
        items.extend(body.get("value", []) or [])
        url, page = body.get("@odata.nextLink"), page + 1
    return first_record, items


def probe_target(label: str, site_id: str, drive_id: str, item_id: str, token: str, client_application_id: str | None) -> dict:
    """Sonda de LECTURA sobre un destino. Site/drive/carpeta con las MISMAS formas de URL que `profile_document_service._drive_items_base` (`/sites/{site}/drives/{drive}/items/{item}`); los grants
    con `GET /drives/{drive}/items/{item}/permissions`. Un 200 en site/drive/carpeta NO prueba el grant de `Files.SelectedOperations.Selected` (el token también trae `Sites.Read.All`)."""
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
    perm_record, permissions = _read_permissions(drive_id, item_id, token)
    perm_record = {**perm_record, "step": f"{label}:item_permissions"}
    steps.append(perm_record)
    read_steps = [s for s in steps if not s["step"].endswith("item_permissions") and "nonexistent" not in s["step"]]
    return {"label": label, "site_id": site_id, "drive_id": drive_id, "item_id": item_id,
            "folder": {"name": (item or {}).get("name"), "web_url": (item or {}).get("webUrl")},
            "read_status": {s["step"].split(":", 1)[1]: s.get("status") for s in read_steps},
            "app_grant": app_grant_for(client_application_id, permissions), "steps": steps}


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


def decision_case(token: dict, current: dict | None, control: dict | None) -> dict:
    """Caso A–E de la matriz de decisión (calculado; evidencia, no autoridad). Sin escribir nada."""
    if not token.get("decodable"):
        return {"case": "?", "why": "El token no se pudo decodificar."}
    if not token.get("files_selectedoperations_selected_present"):
        return {"case": "C", "why": "El token NO trae Files.SelectedOperations.Selected: primero resolver consentimiento/adquisición del token; no tocar grants todavía."}
    cur = (current or {}).get("app_grant") or {}
    if cur.get("found") is None:
        return {"case": "?", "why": "No se pudieron leer los permissions de la carpeta actual (ver el paso item_permissions): no se puede afirmar presencia ni ausencia del grant."}
    if not cur.get("has_write_role"):
        return {"case": "A", "why": "El token trae el scope Selected pero la carpeta actual NO tiene grant de aplicación con rol write para este application id."}
    if control is None:
        return {"case": "B", "why": "Scope presente y grant write en la carpeta actual: el problema está más abajo (reproducir el upload con instrumentación). Control no evaluado."}
    ctl = control.get("app_grant") or {}
    if ctl.get("found") is None:
        return {"case": "?", "why": "Carpeta actual con grant write, pero no se pudieron leer los permissions de la carpeta de control."}
    if not ctl.get("has_write_role"):
        return {"case": "D", "why": "La carpeta actual tiene grant write y la de control no: NO comparar uploads; primero darle a control un grant equivalente."}
    return {"case": "E", "why": "Ambas carpetas tienen grant write de la aplicación: el A/B de upload es válido."}


def run_probe(token: str, *, control_sharing_url: str = "") -> dict:
    """Sonda completa de LECTURA (sólo GET): claims mínimos del token + destino ACTUAL (config) + (opcional) destino de CONTROL descubierto desde un sharing link + grants de la app."""
    claims = token_claims_summary(token)
    app_id = claims.get("client_application_id")
    result: dict = {"token": claims, "current_target": None, "control_target": None}
    cur = current_target()
    if all(cur.values()):
        result["current_target"] = probe_target("current", cur["site_id"], cur["drive_id"], cur["item_id"], token, app_id)
    else:
        result["current_target"] = {"label": "current", "error": "CONFIG_INCOMPLETE: falta HC_SHAREPOINT_SITE_ID / HC_SHAREPOINT_DRIVE_ID / HC_CV_ROOT_ITEM_ID", "steps": []}
    url = (control_sharing_url or "").strip()
    if url:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc.lower().endswith(".sharepoint.com"):
            result["control_target"] = {"label": "control", "error": "El link de control tiene que ser https://*.sharepoint.com/…", "steps": []}
        else:
            record, found = discover_sharing_link(url, token)
            if found and found["site_id"] and found["drive_id"]:
                control = probe_target("control", found["site_id"], found["drive_id"], found["item_id"], token, app_id)
                control.update({"discovery": record, "hostname": parsed.netloc, "is_folder": found["is_folder"],
                                "site_path": parsed.path.split("/s/", 1)[1].split("/", 1)[0] if "/s/" in parsed.path else None})
                result["control_target"] = control
            else:
                result["control_target"] = {"label": "control", "discovery": record, "error": "No se pudo resolver el sharing link a site/drive/item.", "steps": []}
    result["app_grants"] = {"current": (result["current_target"] or {}).get("app_grant"), "control": (result["control_target"] or {}).get("app_grant")}
    control_with_grant = result["control_target"] if (result["control_target"] or {}).get("app_grant") else None
    result["next_step"] = decision_case(claims, result["current_target"], control_with_grant)
    return result
