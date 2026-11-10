"""
datasphere_publication.py — herramienta "Publicar modelo" (publicación de un modelo de SAP Datasphere al Catálogo gobernado).

Contrato de producto: PUBLICAR (no evaluar). Ejecutar = adquirir evidencia en el pool canónico → regenerar el Catálogo → el nuevo
dominio queda disponible en la pestaña "Catálogo". Es el HERMANO del Assessment pero con la acción de negocio OPUESTA, y con su
PROPIA ruta POST: el navegador no puede convertir un assessment en una publicación (ni al revés) manipulando un parámetro.

Ruta (SOLO el POST ejecuta):

  POST /modules/{stem}/datasphere-publication/run    valida + publica y transmite el avance en tiempo real (NDJSON, vía fetch);
                                                     al terminar entrega la URL de la pestaña Catálogo del módulo.

Seguridad: sesión obligatoria (401); el POST exige mismo origen (``same_origin_guard``); publicar exige ``svc.can_execute``: el dueño, o un consultor con perfil completo SOLO si la capacidad para consultores está disponible
(interruptor + garantías de la publicación segura + clientes autorizados; apagada por defecto) y, además, ``svc.client_authorized`` sobre el cliente pedido. Los perfiles de clientes compartidos (``profile_id``) son SOLO del dueño. El modo
``publish`` lo fuerza el SERVIDOR (``datasphere_publication_service.run``): la UI nunca lo elige. El Client Secret llega solo por el cuerpo del POST, se pasa al servicio y NO se devuelve, no se guarda, no se
registra.
"""
from __future__ import annotations

import json
import logging
import threading
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import StreamingResponse

from core.content_registry import DEFAULT_PACK, resolve_pack
from tools.catalog import publication_replacement as replacement
from webapp import same_origin_guard
from webapp.services import auth_service
from webapp.services import datasphere_profiles as profiles
from webapp.services import datasphere_publication_service as svc
from webapp.services import operation_registry
from webapp.services.activity_context import AREA_CATALOG, SCOPE_COPILOT
from webapp.services.activity_service import log_activity
from webapp.services.activity_catalog import outcome_of

log = logging.getLogger("danwor.datasphere_publication")
router = APIRouter()

# Plazo de SEGUIMIENTO de una publicación (no de la publicación): vencido, el aviso deja de mostrarla «en curso» SIN afirmar resultado; la
# publicación sigue y, si termina, su resultado real reemplaza este cierre. Nunca declara un rollback que no se verificó.
PUBLICATION_MONITOR_MAX_S = 3 * 3600
PUBLICATION_MONITOR_TIMEOUT = {
    "t": "done", "state": "UNKNOWN", "error_code": "MONITOR_TIMEOUT", "title": "Seguimiento de la publicación vencido",
    "message": ("El seguimiento superó su tiempo máximo sin recibir el resultado final. No se canceló nada y no puede afirmarse si la publicación "
                "se aplicó: revisá el Catálogo y el detalle técnico antes de volver a publicar."),
    "errors": {}, "result_url": None}


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
    profile_id: str = Form(""), graph_token: str = Form(""), replace_token: str = Form(""),
):
    """Publica y emite el avance EN TIEMPO REAL como NDJSON (una línea JSON por evento):
        {"t":"start"} · {"t":"line","v":"<línea del extractor>"}* · {"t":"phase","phase":"<id>","v":"<texto>"}* ·
        {"t":"done","state","error_code","message","title","errors","result_url"}
    Al terminar con éxito, ``result_url`` apunta a la pestaña Catálogo del módulo (el nuevo dominio ya está disponible)."""
    user = _authorize(request, stem)
    if profile_id.strip() and not svc.can_use_client_profiles(request):      # el secreto de un perfil de cliente es del dueño: jamás se resuelve para otro usuario
        raise HTTPException(403, "No autorizado")
    if not svc.client_authorized(request, client):                            # un consultor solo publica sobre los clientes autorizados explícitamente
        return StreamingResponse(iter([svc.denied_run_events("CLIENT_NOT_AUTHORIZED", svc.CLIENT_NOT_AUTHORIZED_MESSAGE)]), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
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
    # La publicación vive en el registro COMPARTIDO de operaciones (el mismo del diagnóstico): navegar, recargar o un corte de esta conexión no la
    # detiene ni la pierde; el aviso global del Copiloto la recupera por usuario. Solo metadatos no sensibles; el secreto jamás entra.
    run_id = operation_registry.start_operation(
        owner, meta={"kind": "publication", "stem": stem, "pack": selected, "client": values.client, "space": values.space, "root_object": values.root_object},
        max_s=PUBLICATION_MONITOR_MAX_S, timeout_event=PUBLICATION_MONITOR_TIMEOUT, accept_late_done=True)

    def worker() -> None:
        nonlocal secret
        try:
            _work()
        except Exception:                                                  # noqa: BLE001 — una falla inesperada NUNCA deja la operación sin evento terminal (colgaría al cliente)
            log.exception("datasphere_publication: falla inesperada en el hilo de publicación")
            operation_registry.publish_event(run_id, {"t": "done", "state": "FAILED", "error_code": "EXTRACTION_FAILED", "message": svc.asd.ERROR_MESSAGES["EXTRACTION_FAILED"],
                                                      "title": svc.TITLE_PUBLISH, "errors": {}, "result_url": None})
        finally:
            secret = ""                                                    # el secreto no sobrevive a la ejecución

    def _work() -> None:
        outcome = svc.run(values, secret, user_email=owner, replace_token=replacement.valid_token(replace_token),
                          on_line=lambda line: operation_registry.publish_event(run_id, {"t": "line", "v": line}),
                          on_phase=lambda phase: operation_registry.publish_event(run_id, {"t": "phase", "phase": phase, "v": svc.PHASE_COPY[phase]}))
        # Éxito → abrir la pestaña Catálogo del módulo (el nuevo dominio ya figura tras invalidar la caché).
        result_url = (f"/modules/{stem}?" + urlencode({"pack": selected, "tab": "catalogo"})) if outcome.state == "SUCCESS" else None
        operation_registry.publish_event(run_id, {
            "t": "done", "state": outcome.state, "error_code": outcome.error_code, "message": outcome.message,
            "title": outcome.title, "errors": outcome.errors, "operation_id": outcome.operation_id,
            "duration_s": outcome.duration_s, "result_url": result_url,
            "status": getattr(outcome, "status", ""), "durability": getattr(outcome, "durability", ""), "stages": getattr(outcome, "stages", {}),
            "change": getattr(outcome, "change", ""), "assessment": {k: len(v) for k, v in (getattr(outcome, "assessment", None) or {}).items()},
            "replacement": getattr(outcome, "replacement", None)})            # alcance del conflicto / reemplazo (solo nombres de objetos y modelos; sin rutas)
        try:                                                               # telemetría DESPUÉS del `done`: su falla solo se registra, nunca cambia el resultado
            log_activity("datasphere_publication_run", user=user, scope=SCOPE_COPILOT, area=AREA_CATALOG,
                         client=values.client, space=values.space, root_object=values.root_object,
                         state=outcome.state, error_code=outcome.error_code, stage=outcome.stage, duration_s=outcome.duration_s,
                         operation_id=outcome.operation_id, status=getattr(outcome, "status", ""), durability=getattr(outcome, "durability", ""), change=getattr(outcome, "change", ""),
                         outcome=outcome_of(outcome.state))
        except Exception as exc:                                           # noqa: BLE001 — solo el tipo: nunca valores del run ni del usuario
            log.warning("datasphere_publication run=%s activity log failed exc=%s", run_id, type(exc).__name__)

    threading.Thread(target=worker, name="datasphere-publication", daemon=True).start()

    def stream():
        # `run_id` = cómo retomar la publicación (aviso global / «Ver ejecución»); el latido evita el corte por inactividad.
        yield json.dumps({"t": "start", "run_id": run_id}) + chr(10)
        cursor = 0
        while True:
            batch, cursor, finished = operation_registry.wait_events(run_id, cursor, svc.asd.HEARTBEAT_S)
            if not batch and not finished:
                yield json.dumps({"t": "ping"}) + chr(10)
                continue
            for event in batch:
                yield json.dumps(event, ensure_ascii=False) + chr(10)
            if finished:
                return

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff"})
