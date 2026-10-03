"""
datasphere_assessment.py — herramienta "Assessment de tu modelo" (exportación directa desde SAP Datasphere).

Rutas (SOLO POST ejecutan; no existe ninguna ruta GET que dispare nada):

  POST /modules/{stem}/datasphere-assessment/run               valida + ejecuta la exportación (misma función que el CLI) y
                                                               transmite su salida en tiempo real (NDJSON, vía fetch)
  POST /modules/{stem}/datasphere-assessment/refresh-catalog   paso explícito: regenerar el catálogo desde el pool CSN

Seguridad: sesión obligatoria (401); mismo origen (`same_origin_guard`, las cookies son SameSite=None); ejecutar exige ser el
dueño de la app (`access.is_owner`, el predicado que ya gobierna las herramientas operativas). El Client Secret llega solo por
el cuerpo del POST, se pasa al servicio y NO se devuelve, no se guarda en sesión y no se registra.
"""
from __future__ import annotations

import json
import queue
import threading

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from core.content_registry import DEFAULT_PACK
from webapp import same_origin_guard
from webapp.services import auth_service
from webapp.services import datasphere_assessment_service as svc
from webapp.services.activity_context import AREA_CATALOG, SCOPE_COPILOT
from webapp.services.activity_service import log_activity
from webapp.templating import templates

router = APIRouter()


def _authorize(request: Request, stem: str) -> dict:
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen no permitido")
    if not svc.tool_enabled(stem):
        raise HTTPException(404, "Herramienta no disponible en este módulo")
    if not svc.can_execute(request):
        raise HTTPException(403, "No autorizado")
    return user


@router.post("/modules/{stem}/datasphere-assessment/run", include_in_schema=False)
def run_assessment(
    request: Request, stem: str,
    host: str = Form(""), token_url: str = Form(""),
    client_id: str = Form("", alias="datasphere_client_id"), client_secret: str = Form("", alias="datasphere_client_secret"),
    space: str = Form(""), analytic_model: str = Form(""), client: str = Form(""),
):
    """Ejecuta la exportación y emite el avance EN TIEMPO REAL como NDJSON (una línea JSON por evento):
        {"t":"start","operation_id":…}            apenas arranca
        {"t":"line","v":"<línea del extractor>"}  cada línea de salida del extractor, en orden y sin reinterpretar
        {"t":"done","state":…,"error_code":…,"message":…,"errors":{…},"rows":[[etiqueta,valor]…]}   resultado final
    Todo texto del extractor viaja saneado (sin secretos/tokens/rutas internas). La UI lo inserta con textContent."""
    user = _authorize(request, stem)
    values = svc.FormValues.from_mapping({"host": host, "token_url": token_url, "client_id": client_id, "space": space,
                                          "analytic_model": analytic_model, "client": client})
    secret = client_secret
    del client_secret
    events: queue.Queue = queue.Queue()

    def worker() -> None:
        nonlocal secret
        try:
            outcome = svc.run(values, secret, user_email=str(user.get("email") or ""), on_line=lambda line: events.put({"t": "line", "v": line}))
        finally:
            secret = ""                                                    # el secreto no sobrevive a la ejecución
        log_activity("datasphere_assessment_run", user=user, scope=SCOPE_COPILOT, area=AREA_CATALOG,
                     client=values.client, space=values.space, analytic_model=values.analytic_model,
                     state=outcome.state, error_code=outcome.error_code, duration_s=outcome.duration_s, operation_id=outcome.operation_id)
        events.put({"t": "done", "state": outcome.state, "error_code": outcome.error_code, "message": outcome.message,
                    "errors": outcome.errors, "operation_id": outcome.operation_id, "duration_s": outcome.duration_s,
                    "rows": svc.summary_rows(outcome.summary) if outcome.summary else []})
        events.put(None)

    threading.Thread(target=worker, name="datasphere-assessment", daemon=True).start()

    def stream():
        yield json.dumps({"t": "start"}) + chr(10)
        while True:
            event = events.get()
            if event is None:
                return
            yield json.dumps(event, ensure_ascii=False) + chr(10)

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff"})


@router.post("/modules/{stem}/datasphere-assessment/refresh-catalog", response_class=HTMLResponse, include_in_schema=False)
def refresh_catalog(request: Request, stem: str, pack: str = Form(DEFAULT_PACK)):
    user = _authorize(request, stem)
    outcome = svc.refresh_catalog(user_email=str(user.get("email") or ""))
    log_activity("datasphere_assessment_refresh_catalog", user=user, scope=SCOPE_COPILOT, area=AREA_CATALOG,
                 state=outcome.state, error_code=outcome.error_code, duration_s=outcome.duration_s, operation_id=outcome.operation_id)
    return templates.TemplateResponse(request=request, name="partials/datasphere_assessment_refresh.html", context={"outcome": outcome})
