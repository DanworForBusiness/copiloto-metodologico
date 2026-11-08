"""
datasphere_assessment.py — herramienta "Assessment de tu modelo" (assessment ad-hoc desde SAP Datasphere).

Contrato de producto: EVALUAR != PUBLICAR. Ejecutar = adquirir evidencia → correr el Modeling Assessment → guardar un resultado
ad-hoc (24 h) → abrir el informe. Nada se publica en el Catálogo ni en Spaces.

Rutas (SOLO el POST ejecuta; los GET son de lectura):

  POST /modules/{stem}/datasphere-assessment/run                       valida + ejecuta (operación del servidor) y transmite el avance en tiempo real
                                                                       (NDJSON, vía fetch); termina con la URL del informe
  GET  /modules/{stem}/datasphere-assessment/operation/{run_id}?since=N  recupera avance y resultado de una ejecución PROPIA si la conexión se cortó
  GET  /modules/{stem}/datasphere-assessment/result/{result_id}        informe ad-hoc (MISMA plantilla/presenter que el del Catálogo)
  GET  /modules/{stem}/datasphere-assessment/result/{result_id}/log          detalle de ejecución (log saneado, vence con el resultado)
  POST /modules/{stem}/datasphere-assessment/result/{result_id}/delete       elimina ESE resultado temporal (y su log); solo su dueño
  GET  /modules/{stem}/datasphere-assessment/result/{result_id}/export.json   perfil canónico guardado
  GET  /modules/{stem}/datasphere-assessment/result/{result_id}/export.md     render_markdown del mismo perfil
  POST /modules/{stem}/datasphere-profiles                             (SOLO dueño) lista SEGURA de perfiles de clientes desde SharePoint, para el
                                                                       selector "Cliente" de Assessment Y Publicar (sin secretos; ver
                                                                       ``datasphere_profiles``); cualquier otro usuario recibe 403 sin tocar SharePoint
  POST /modules/{stem}/datasphere-assessment/refresh-catalog           capacidad existente (futuro "Catálogo → Agregar"); ya no se
                                                                       expone desde esta herramienta

Seguridad: sesión obligatoria (401); el POST exige mismo origen (`same_origin_guard`; las cookies son SameSite=None); ejecutar exige
un consultor con perfil completo del Hub (o el dueño) y ver/exportar/eliminar solo es posible sobre los resultados PROPIOS (el de otro
usuario es indistinguible de uno inexistente: 404). Las capacidades administrativas (perfiles de clientes de SharePoint, regenerar el
Catálogo) siguen siendo del dueño (`access.is_owner`). Un ``result_id`` opaco NO es autorización. El Client Secret llega
solo por el cuerpo del POST, se pasa al servicio y NO se devuelve, no se guarda y no se registra.
"""
from __future__ import annotations

import json
import logging
import threading
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from core.content_registry import DEFAULT_PACK, content_pack_short_label, resolve_pack
from webapp import same_origin_guard, workspaces
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as asvc
from webapp.services import auth_service
from webapp.services import datasphere_profiles as profiles
from webapp.services import datasphere_assessment_service as svc
from webapp.services.status_tone import status_tone
from webapp.services.activity_context import AREA_CATALOG, SCOPE_COPILOT
from webapp.services.activity_service import log_activity
from webapp.services.activity_catalog import outcome_of
from webapp.services.content_service import academy_asset_for, list_academy, list_appendices, list_modules
from webapp.templating import templates

router = APIRouter()
log = logging.getLogger(__name__)


def _authorize_read(request: Request, stem: str) -> dict:
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    if not svc.tool_enabled(stem):
        raise HTTPException(404, "Herramienta no disponible en este módulo")
    if not svc.can_execute(request):
        raise HTTPException(403, "No autorizado")
    return user


def _authorize(request: Request, stem: str) -> dict:
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen no permitido")
    return _authorize_read(request, stem)


def _authorize_owner(request: Request, stem: str) -> dict:
    """Capacidades ADMINISTRATIVAS de la herramienta (perfiles de clientes con secretos compartidos, regenerar el Catálogo): sesión +
    mismo origen + herramienta habilitada + APP_OWNER. Un consultor que ejecuta assessments NO las hereda."""
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen no permitido")
    if not svc.tool_enabled(stem):
        raise HTTPException(404, "Herramienta no disponible en este módulo")
    if not svc.can_administer(request):
        raise HTTPException(403, "No autorizado")
    return user


def _result_urls(stem: str, result_id: str, pack: str) -> tuple[str, str, str, str]:
    base = f"/modules/{stem}/datasphere-assessment/result/{result_id}"
    # "Volver" regresa a SU herramienta: el contrato canónico `?tool=assessment` abre solo "Herramientas de diagnóstico" con Assessment seleccionado.
    return (base + "?" + urlencode({"pack": pack}), base + "/export.json", base + "/export.md",
            f"/modules/{stem}?" + urlencode({"pack": pack, "tool": "assessment"}))


@router.post("/modules/{stem}/datasphere-assessment/run", include_in_schema=False)
def run_assessment(
    request: Request, stem: str, pack: str = Form(DEFAULT_PACK),
    host: str = Form(""), token_url: str = Form(""),
    client_id: str = Form("", alias="datasphere_client_id"), client_secret: str = Form("", alias="datasphere_client_secret"),
    space: str = Form(""), root_object: str = Form("", alias="datasphere_root_object"), client: str = Form(""),
    features: list[str] = Form([], alias="datasphere_features"), features_submitted: str = Form("", alias="datasphere_features_submitted"),
    profile_id: str = Form(""), graph_token: str = Form(""),
):
    """Ejecuta y emite el avance EN TIEMPO REAL como NDJSON (una línea JSON por evento):
        {"t":"start","run_id":…}                   apenas arranca (run_id = cómo retomarla si la conexión se corta)
        {"t":"ping"}                               latido cada HEARTBEAT_S sin salida (la conexión nunca queda inactiva)
        {"t":"line","v":"<línea del extractor>"}   cada línea de salida del extractor, en orden y sin reinterpretar
        {"t":"phase","phase":"<id>","v":"<texto>"}  hito semántico del backend (evidence_acquired | assessing | assessment_ready) + su texto;
                                                   cómo se dibuja (spinner) lo decide el frontend
        {"t":"done","state":…,"error_code":…,"message":…,"title":…,"errors":{…},"result_url":…}   resultado final
    Todo texto del extractor viaja saneado (sin secretos/tokens/rutas internas). La UI lo inserta con textContent."""
    user = _authorize(request, stem)
    if profile_id.strip() and not svc.can_use_client_profiles(request):      # el secreto de un perfil de cliente es del dueño: jamás se resuelve para otro usuario
        raise HTTPException(403, "No autorizado")
    selected = resolve_pack(pack)
    values = svc.FormValues.from_mapping({"host": host, "token_url": token_url, "client_id": client_id, "space": space,
                                          "root_object": root_object, "client": client,
                                          "features": list(features) if features_submitted else None})
    svc.save_form_context(svc.session_key(request), values)        # contexto no sensible de la sesión (el secreto jamás entra a FormValues)
    secret = client_secret
    del client_secret
    if profile_id.strip():                                              # perfil del dueño: el secreto se resuelve en el SERVIDOR (jamás viene del navegador)
        profile = profiles.resolve(profile_id, graph_token)
        del graph_token
        if profile is None:
            return StreamingResponse(iter([profiles.failed_run_events()]), media_type="application/x-ndjson",
                                     headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        secret = profile.secret
        del profile
    owner = svc.owner_email(request)
    run_id = svc.start_operation(owner)        # la operación vive en el servidor: el corte de ESTA conexión no la detiene ni la pierde

    failed_done = {"t": "done", "state": "FAILED", "error_code": "EXTRACTION_FAILED", "message": svc.ERROR_MESSAGES["EXTRACTION_FAILED"],
                   "title": svc.TITLE_EXTRACTION, "errors": {}, "operation_id": None, "duration_s": None, "result_url": None}

    def worker() -> None:
        # INVARIANTE: toda operación termina en UN `done`, publicado en el `finally` ANTES de la telemetría. Ni una falla del run ni una del
        # registro de actividad pueden dejar al navegador esperando, y la telemetría jamás cambia el resultado real del Assessment.
        nonlocal secret
        outcome = None
        try:
            outcome = svc.run(values, secret, user_email=owner,
                              on_line=lambda line: svc.publish_event(run_id, {"t": "line", "v": line}),
                              on_phase=lambda phase: svc.publish_event(run_id, {"t": "phase", "phase": phase, "v": svc.PHASE_COPY[phase]}))
        except Exception as exc:                                           # noqa: BLE001 — jamás el texto crudo; el `done` de falla lo emite el finally
            log.warning("datasphere_assessment run=%s worker failed exc=%s", run_id, type(exc).__name__)
        finally:
            secret = ""                                                    # el secreto no sobrevive a la ejecución
            done = failed_done
            if outcome is not None:
                try:
                    result_url = _result_urls(stem, outcome.result_id, selected)[0] if outcome.result_id else None
                    done = {"t": "done", "state": outcome.state, "error_code": outcome.error_code, "message": outcome.message,
                            "title": outcome.title, "errors": outcome.errors, "operation_id": outcome.operation_id,
                            "duration_s": outcome.duration_s, "result_url": result_url}
                except Exception as exc:                                   # noqa: BLE001 — sin URL no hay informe que abrir: falla visible, nunca espera
                    log.warning("datasphere_assessment run=%s done build failed exc=%s", run_id, type(exc).__name__)
            svc.publish_event(run_id, done)
        if outcome is None:
            return
        try:                                                               # telemetría DESPUÉS del `done`: su falla solo se registra
            log_activity("datasphere_assessment_run", user=user, scope=SCOPE_COPILOT, area=AREA_CATALOG,
                         client=values.client, space=values.space, root_object=values.root_object,
                         state=outcome.state, error_code=outcome.error_code, stage=outcome.stage, duration_s=outcome.duration_s,
                         operation_id=outcome.operation_id, outcome=outcome_of(outcome.state))
        except Exception as exc:                                           # noqa: BLE001 — solo el tipo: nunca valores del run ni del usuario
            log.warning("datasphere_assessment run=%s op=%s activity log failed exc=%s", run_id, outcome.operation_id, type(exc).__name__)

    threading.Thread(target=worker, name="datasphere-assessment", daemon=True).start()

    def stream():
        # `run_id` = cómo retomar la operación si la conexión se corta (GET …/operation/{run_id}); el latido evita el corte por inactividad.
        yield json.dumps({"t": "start", "run_id": run_id}) + chr(10)
        cursor = 0
        while True:
            batch, cursor, finished = svc.wait_events(run_id, cursor, svc.HEARTBEAT_S)
            if not batch and not finished:
                yield json.dumps({"t": "ping"}) + chr(10)
                continue
            for event in batch:
                yield json.dumps(event, ensure_ascii=False) + chr(10)
            if finished:
                return

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff"})


@router.get("/modules/{stem}/datasphere-assessment/operation/{run_id}", include_in_schema=False)
def run_operation_events(request: Request, stem: str, run_id: str, since: int = Query(0, ge=0)):
    """Recuperación tras un corte de la conexión: avance y resultado de una ejecución PROPIA en curso o terminada, sin reiniciarla.
    Solo LEE el registro en memoria (mismos eventos saneados del stream). Otra operación o una vencida: 404 indistinguible."""
    _authorize_read(request, stem)
    data = svc.operation_events(run_id, svc.owner_email(request), since)
    if data is None:
        raise HTTPException(404, "La ejecución no existe o venció.")
    return JSONResponse(data, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/modules/{stem}/datasphere-profiles", include_in_schema=False)
def datasphere_client_profiles(request: Request, stem: str, graph_token: str = Form("")):
    """Perfiles de clientes (SOLO metadatos seguros) para el selector del dueño. ``_authorize_owner`` exige sesión + mismo origen +
    herramienta habilitada + ser APP_OWNER ANTES de cualquier lectura de SharePoint. Estado no disponible → ``{"ok": false, "reason",
    "message"}`` (200): el formulario manual sigue funcionando."""
    _authorize_owner(request, stem)
    return JSONResponse(profiles.list_public(graph_token), headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def _record_or_404(request: Request, result_id: str) -> dict:
    """El resultado debe existir, estar vigente y pertenecer al usuario autenticado: el de otro usuario es indistinguible de uno inexistente
    (el ``result_id`` no autoriza ni permite enumerar)."""
    record = svc.load_result(result_id)
    if record is None or str(record.get("owner") or "") != svc.owner_email(request):
        raise HTTPException(404, "El resultado no existe o venció (los assessments ad-hoc duran 24 horas).")
    return record


@router.get("/modules/{stem}/datasphere-assessment/result/{result_id}", response_class=HTMLResponse, include_in_schema=False)
def adhoc_report(request: Request, stem: str, result_id: str, pack: str = Query(DEFAULT_PACK)):
    """Informe ad-hoc: el MISMO informe del Catálogo, con el contexto de un resultado guardado (sin flujo, sin regenerar, sin linaje)."""
    user = _authorize_read(request, stem)
    selected = resolve_pack(pack)
    record = _record_or_404(request, result_id)
    _report, json_url, md_url, back_url = _result_urls(stem, result_id, selected)
    view = presenter.report_view(svc.result_status(record), None, selected, adhoc=presenter.AdhocReport(back_url, json_url, md_url),
                                 space=record.get("space"))
    academy = [a for a in list_academy(selected) if not a.nav_hidden]
    return templates.TemplateResponse(
        request=request, name="datasphere_assessment_result.html",
        context={"v": view, "catalog_review_url": f"/modules/{stem}/datasphere-assessment/result/{result_id}/catalog?" + urlencode({"pack": selected}),
                 "modules": list_modules(selected), "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
                 "academy": academy, "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
                 "active_group": "core", "active_stem": stem, "readonly": False, "pack": selected,
                 "pack_label": content_pack_short_label(selected), "user": user, "current_workspace": workspaces.COPILOT,
                 "available_workspaces": workspaces.available_workspaces(user)})


@router.post("/modules/{stem}/datasphere-assessment/result/{result_id}/delete", include_in_schema=False)
def adhoc_delete(request: Request, stem: str, result_id: str, pack: str = Form(DEFAULT_PACK)):
    """Elimina un assessment ad-hoc TEMPORAL del propio usuario. POST (nunca GET), mismo origen, sesión y dueño; un ``result_id`` ajeno o
    inexistente es indistinguible (404). Con ``Accept: application/json`` (fetch de la herramienta) devuelve JSON; sin JS, redirige al módulo."""
    _authorize(request, stem)
    if not svc.delete_result(result_id, svc.owner_email(request)):
        raise HTTPException(404, "El resultado no existe o venció (los assessments ad-hoc duran 24 horas).")
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"ok": True, "remaining": len(svc.list_results(svc.owner_email(request)))}, headers={"Cache-Control": "no-store"})
    return RedirectResponse(f"/modules/{stem}?" + urlencode({"pack": resolve_pack(pack)}), status_code=303)


@router.get("/modules/{stem}/datasphere-assessment/result/{result_id}/log", response_class=HTMLResponse, include_in_schema=False)
def adhoc_execution_log(request: Request, stem: str, result_id: str, pack: str = Query(DEFAULT_PACK)):
    """Detalle de ejecución: el log YA saneado de la corrida que produjo el resultado. Vive y vence con el resultado; no forma parte del
    JSON/Markdown canónico."""
    user = _authorize_read(request, stem)
    selected = resolve_pack(pack)
    record = _record_or_404(request, result_id)
    report_url, _json, _md, _back = _result_urls(stem, result_id, selected)
    academy = [a for a in list_academy(selected) if not a.nav_hidden]
    return templates.TemplateResponse(
        request=request, name="datasphere_assessment_log.html",
        context={"log_lines": [str(line) for line in record.get("execution_log") or []], "terminal_am": record.get("terminal_am", ""),
                 "client_display": record.get("client_display", ""), "report_url": report_url,
                 "modules": list_modules(selected), "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
                 "academy": academy, "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
                 "active_group": "core", "active_stem": stem, "readonly": False, "pack": selected,
                 "pack_label": content_pack_short_label(selected), "user": user, "current_workspace": workspaces.COPILOT,
                 "available_workspaces": workspaces.available_workspaces(user)})


@router.get("/modules/{stem}/datasphere-assessment/result/{result_id}/catalog", response_class=HTMLResponse, include_in_schema=False)
def adhoc_catalog_review(request: Request, stem: str, result_id: str, pack: str = Query(DEFAULT_PACK)):
    """«Regenerar Catálogo»: REVISIÓN (solo lectura) de cómo llevar ESTE diagnóstico al Catálogo gobernado, como DIÁLOGO de Herramientas de diagnóstico (fragmento HTMX
    dentro del `<dialog class="dw-confirm busqueda-dialog">` del panel). Muestra cliente, modelo, Space de origen, el Assessment del Catálogo si existe y la operación gobernada
    realmente disponible (la publicación desde el Space del resultado). No escribe nada: la publicación la confirma el usuario en su propio formulario, con sus credenciales y bajo
    la guarda de identidad del pool. Una navegación directa (sin HTMX) vuelve a la herramienta: la revisión no tiene página propia."""
    from webapp.services import datasphere_publication_service as pub
    user = _authorize_read(request, stem)
    selected = resolve_pack(pack)
    record = _record_or_404(request, result_id)
    review = svc.catalog_continuity(record)
    report_url, _json, _md, back_url = _result_urls(stem, result_id, selected)
    can_publish = pub.tool_enabled(stem) and pub.can_execute(request) and pub.client_authorized(request, review["client"])
    publish_url = (svc.publication_prefill_url(stem, selected, review["client"], review["model"], review["space"])
                   if can_publish and review["operation"] in svc.PUBLISHABLE_OPERATIONS else None)
    if not request.headers.get("HX-Request"):
        return RedirectResponse(back_url, status_code=303)
    catalog_url = presenter.flow_url(review["catalog"]["entry"], selected) if review.get("catalog") else None
    return templates.TemplateResponse(
        request=request, name="partials/datasphere_assessment_catalog_dialog.html",
        context={"review": review, "publish_url": publish_url, "can_publish": can_publish, "catalog_url": catalog_url, "report_url": report_url,
                 "level_labels": {"BUENA": "Buena", "ADECUADA": "Adecuada", "PARCIAL": "Parcial", "PLENA": "Plena"},
                 "state_labels": presenter.STATE_LABELS, "status_tone": status_tone, "user": user},
        headers={"Cache-Control": "no-store"})


def _export(request: Request, stem: str, result_id: str, ext: str) -> Response:
    _authorize_read(request, stem)
    record = _record_or_404(request, result_id)
    status = svc.result_status(record)
    body, media = (asvc.export_json(status), "application/json; charset=utf-8") if ext == "json" else \
                  (asvc.export_markdown(status), "text/markdown; charset=utf-8")
    return Response(content=body, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{asvc.export_filename(status, ext)}"',
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.get("/modules/{stem}/datasphere-assessment/result/{result_id}/export.json", include_in_schema=False)
def adhoc_export_json(request: Request, stem: str, result_id: str):
    return _export(request, stem, result_id, "json")


@router.get("/modules/{stem}/datasphere-assessment/result/{result_id}/export.md", include_in_schema=False)
def adhoc_export_markdown(request: Request, stem: str, result_id: str):
    return _export(request, stem, result_id, "md")


@router.post("/modules/{stem}/datasphere-assessment/refresh-catalog", response_class=HTMLResponse, include_in_schema=False)
def refresh_catalog(request: Request, stem: str, pack: str = Form(DEFAULT_PACK)):
    """Capacidad existente de regenerar el catálogo desde el pool CSN (futuro "Catálogo → Agregar"). NO se expone desde esta herramienta y
    es ADMINISTRATIVA: solo el dueño, aunque el Assessment ad-hoc esté abierto a los consultores."""
    user = _authorize_owner(request, stem)
    outcome = svc.refresh_catalog(user_email=svc.owner_email(request))
    log_activity("datasphere_assessment_refresh_catalog", user=user, scope=SCOPE_COPILOT, area=AREA_CATALOG,
                 state=outcome.state, error_code=outcome.error_code, duration_s=outcome.duration_s, operation_id=outcome.operation_id,
                 outcome=outcome_of(outcome.state))
    return templates.TemplateResponse(request=request, name="partials/datasphere_assessment_refresh.html", context={"outcome": outcome})
