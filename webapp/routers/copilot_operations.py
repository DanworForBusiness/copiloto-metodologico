"""
copilot_operations.py — operaciones PROPIAS en curso o recientes del Copiloto (diagnóstico ad hoc y publicación del Catálogo).

Solo LEE el registro en memoria (``operation_registry``), que es la única fuente de estados: no ejecuta, no reintenta ni cancela nada.

  GET /copilot/operations                   lista JSON de las operaciones del usuario (aviso global del Copiloto)
  GET /copilot/operations/{run_id}          avance desde ``since`` (mismos eventos saneados del stream)
  GET /copilot/operations/{run_id}/log      «Ver ejecución»: la MISMA página «Detalle de ejecución» de los assessments ad hoc

Seguridad: sesión obligatoria (401). Cada consulta se acota al usuario autenticado: la operación de otro usuario, una inexistente o una
vencida son indistinguibles (404). Nada de esto contiene credenciales: el registro solo guarda eventos saneados y metadatos no sensibles.
"""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from core.content_registry import DEFAULT_PACK, content_pack_short_label, resolve_pack
from webapp import workspaces
from webapp.services import auth_service
from webapp.services import datasphere_assessment_service as asd
from webapp.services import operation_registry
from webapp.services.content_service import academy_asset_for, list_academy, list_appendices, list_modules
from webapp.templating import templates

router = APIRouter()
_NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
KIND_LABELS = {"assessment": "Assessment", "publication": "Publicación"}
STATE_LABELS = {"RUNNING": "En curso", "SUCCESS": "Completada", "FAILED": "Falló", "INVALID": "Datos inválidos", "UNKNOWN": "Resultado sin confirmar"}


def _user(request: Request) -> tuple[dict, str]:
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    return user, asd.owner_email(request)


def _log_url(run_id: str, pack: str) -> str:
    return f"/copilot/operations/{run_id}/log?" + urlencode({"pack": pack})


def _public(op: dict) -> dict:
    meta = op["meta"]
    return {"run_id": op["run_id"], "kind": meta.get("kind", ""), "kind_label": KIND_LABELS.get(meta.get("kind", ""), "Operación"),
            "client": meta.get("client", ""), "root_object": meta.get("root_object", ""), "space": meta.get("space", ""),
            "state": op["state"], "state_label": STATE_LABELS.get(op["state"], op["state"]), "finished": op["finished"],
            "title": op["title"], "message": op["message"], "result_url": op["result_url"],
            "log_url": _log_url(op["run_id"], meta.get("pack") or DEFAULT_PACK), "created_at": op["created_at"]}


@router.get("/copilot/operations", include_in_schema=False)
def my_operations(request: Request):
    _user_, owner = _user(request)
    return JSONResponse({"operations": [_public(op) for op in operation_registry.list_operations(owner)]}, headers=_NO_STORE)


@router.get("/copilot/operations/{run_id}", include_in_schema=False)
def operation_progress(request: Request, run_id: str, since: int = Query(0, ge=0)):
    _user_, owner = _user(request)
    data = operation_registry.operation_events(run_id, owner, since)
    if data is None:
        raise HTTPException(404, "La ejecución no existe o venció.")
    return JSONResponse(data, headers=_NO_STORE)


@router.get("/copilot/operations/{run_id}/log", response_class=HTMLResponse, include_in_schema=False)
def operation_log(request: Request, run_id: str, pack: str = Query(DEFAULT_PACK)):
    """«Ver ejecución» de una operación propia (en curso o terminada): reusa la página «Detalle de ejecución»; mientras siga en curso, la
    página sigue el avance con ``/copilot/operations/{run_id}``. Un diagnóstico terminado con éxito ofrece además su informe."""
    user, owner = _user(request)
    detail = operation_registry.operation_detail(run_id, owner)
    if detail is None:
        raise HTTPException(404, "La ejecución no existe o venció (el registro de ejecuciones dura 24 horas).")
    selected = resolve_pack(pack)
    op = _public(detail)
    back_url = op["result_url"] or ("/modules/" + detail["meta"].get("stem", "") + "?" + urlencode({"pack": selected}) if detail["meta"].get("stem") else "/?" + urlencode({"pack": selected}))
    back_label = ("← Abrir el Catálogo" if op["kind"] == "publication" else "← Volver al assessment") if op["result_url"] else "← Volver al módulo"
    academy = [a for a in list_academy(selected) if not a.nav_hidden]
    return templates.TemplateResponse(
        request=request, name="datasphere_assessment_log.html",
        context={"log_lines": detail["log_lines"], "terminal_am": op["root_object"] or op["kind_label"], "client_display": op["client"],
                 "report_url": back_url, "back_label": back_label, "operation": op,
                 "live_url": (f"/copilot/operations/{run_id}" if not op["finished"] else ""), "live_next": detail["next"],
                 "modules": list_modules(selected), "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
                 "academy": academy, "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
                 "active_group": "core", "active_stem": detail["meta"].get("stem", ""), "readonly": False, "pack": selected,
                 "pack_label": content_pack_short_label(selected), "user": user, "current_workspace": workspaces.COPILOT,
                 "available_workspaces": workspaces.available_workspaces(user)}, headers={"Cache-Control": "no-store"})
