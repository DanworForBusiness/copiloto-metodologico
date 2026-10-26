"""
datasphere_publication.py — herramienta "Publicar modelo" (publicación de un modelo de SAP Datasphere al Catálogo gobernado).

Contrato de producto: PUBLICAR (no evaluar). Ejecutar = adquirir evidencia en el pool canónico → regenerar el Catálogo → el nuevo
dominio queda disponible en la pestaña "Catálogo". Es el HERMANO del Assessment pero con la acción de negocio OPUESTA, y con su
PROPIA ruta POST: el navegador no puede convertir un assessment en una publicación (ni al revés) manipulando un parámetro.

Ruta (SOLO el POST ejecuta):

  POST /modules/{stem}/datasphere-publication/run    valida + publica y transmite el avance en tiempo real (NDJSON, vía fetch);
                                                     al terminar entrega la URL de la pestaña Catálogo del módulo.

Seguridad: sesión obligatoria (401); el POST exige mismo origen (``same_origin_guard``); publicar exige ser el dueño de la app
(``access.is_owner`` vía ``svc.can_execute``). El modo ``publish`` lo fuerza el SERVIDOR (``datasphere_publication_service.run``):
la UI nunca lo elige. El Client Secret llega solo por el cuerpo del POST, se pasa al servicio y NO se devuelve, no se guarda, no se
registra.
"""
from __future__ import annotations

import json
import queue
import threading
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import StreamingResponse

from core.content_registry import DEFAULT_PACK, resolve_pack
from webapp import same_origin_guard
from webapp.services import auth_service
from webapp.services import datasphere_profiles as profiles
from webapp.services import datasphere_publication_service as svc
from webapp.services.activity_context import AREA_CATALOG, SCOPE_COPILOT
from webapp.services.activity_service import log_activity

router = APIRouter()


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


@router.post("/modules/{stem}/datasphere-publication/run", include_in_schema=False)
def run_publication(
    request: Request, stem: str, pack: str = Form(DEFAULT_PACK),
    host: str = Form(""), token_url: str = Form(""),
    client_id: str = Form("", alias="datasphere_client_id"), client_secret: str = Form("", alias="datasphere_client_secret"),
    space: str = Form(""), root_object: str = Form("", alias="datasphere_root_object"), client: str = Form(""),
    features: list[str] = Form([], alias="datasphere_features"), features_submitted: str = Form("", alias="datasphere_features_submitted"),
    profile_id: str = Form(""), graph_token: str = Form(""),
):
    """Publica y emite el avance EN TIEMPO REAL como NDJSON (una línea JSON por evento):
        {"t":"start"} · {"t":"line","v":"<línea del extractor>"}* · {"t":"phase","phase":"<id>","v":"<texto>"}* ·
        {"t":"done","state","error_code","message","title","errors","result_url"}
    Al terminar con éxito, ``result_url`` apunta a la pestaña Catálogo del módulo (el nuevo dominio ya está disponible)."""
    user = _authorize(request, stem)
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
    events: queue.Queue = queue.Queue()

    def worker() -> None:
        nonlocal secret
        try:
            outcome = svc.run(values, secret, user_email=svc.owner_email(request),
                              on_line=lambda line: events.put({"t": "line", "v": line}),
                              on_phase=lambda phase: events.put({"t": "phase", "phase": phase, "v": svc.PHASE_COPY[phase]}))
        finally:
            secret = ""                                                    # el secreto no sobrevive a la ejecución
        log_activity("datasphere_publication_run", user=user, scope=SCOPE_COPILOT, area=AREA_CATALOG,
                     client=values.client, space=values.space, root_object=values.root_object,
                     state=outcome.state, error_code=outcome.error_code, stage=outcome.stage, duration_s=outcome.duration_s,
                     operation_id=outcome.operation_id, status=getattr(outcome, "status", ""), durability=getattr(outcome, "durability", ""))
        # Éxito → abrir la pestaña Catálogo del módulo (el nuevo dominio ya figura tras invalidar la caché).
        result_url = (f"/modules/{stem}?" + urlencode({"pack": selected, "tab": "catalogo"})) if outcome.state == "SUCCESS" else None
        events.put({"t": "done", "state": outcome.state, "error_code": outcome.error_code, "message": outcome.message,
                    "title": outcome.title, "errors": outcome.errors, "operation_id": outcome.operation_id,
                    "duration_s": outcome.duration_s, "result_url": result_url,
                    "status": getattr(outcome, "status", ""), "durability": getattr(outcome, "durability", ""), "stages": getattr(outcome, "stages", {})})
        events.put(None)

    threading.Thread(target=worker, name="datasphere-publication", daemon=True).start()

    def stream():
        yield json.dumps({"t": "start"}) + chr(10)
        while True:
            event = events.get()
            if event is None:
                return
            yield json.dumps(event, ensure_ascii=False) + chr(10)

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff"})
