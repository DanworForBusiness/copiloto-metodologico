"""
connectivity_inventory.py — extracción AD-HOC de conexiones (F2.1) desde el Catálogo del módulo 01.

Ruta (SOLO el POST ejecuta):

  POST /modules/{stem}/connectivity-inventory/run    valida + extrae (solo lectura) y transmite NDJSON:
      {"t":"start"} · {"t":"phase","phase":"<hito>","v":"<texto>"}* · {"t":"line","v":"<actividad saneada>"}* ·
      {"t":"done","state","error_code","message","title","errors","operation_id","duration_s","summary"}
  Mismo contrato que el módulo 04: `phase` = hito realmente observado (preflight | listing | reading | summarizing → completed |
  partial | failed); `line` = actividad SANEADA (ordinales S1…, conteos y códigos del vocabulario cerrado), nunca líneas crudas.

Seguridad (MISMO patrón que las herramientas del módulo 04): sesión obligatoria (401); mismo origen (403); herramienta
solo en el módulo 01 (404); ``can_execute`` (403); un ``profile_id``
(perfil de cliente de SharePoint) es SOLO del dueño (403) y su secreto se resuelve en el SERVIDOR. El Client Secret llega
solo por el cuerpo del POST, no se devuelve, no se guarda, no se registra. A diferencia del módulo 04, NO se reenvían líneas
del extractor (las `line` las arma el servicio, saneadas): el único dato de resultado es el resumen saneado (B1). Nada se persiste.
"""
from __future__ import annotations

import json
import queue
import threading

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import StreamingResponse

from webapp import access, same_origin_guard
from webapp.services import auth_service
from webapp.services import connectivity_inventory_service as svc
from webapp.services import datasphere_assessment_service as asd
from webapp.services import datasphere_profiles as profiles
from webapp.services.activity_context import AREA_CATALOG, SCOPE_COPILOT
from webapp.services.activity_service import log_activity

router = APIRouter()

_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


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


@router.post("/modules/{stem}/connectivity-inventory/run", include_in_schema=False)
def run_connectivity_inventory(
    request: Request, stem: str,
    host: str = Form(""), token_url: str = Form(""),
    client_id: str = Form("", alias="datasphere_client_id"), client_secret: str = Form("", alias="datasphere_client_secret"),
    client: str = Form(""), space_id: list[str] = Form([]),
    probe_connection: str = Form(""), profile_id: str = Form(""), graph_token: str = Form(""),
):
    user = _authorize(request, stem)
    if profile_id.strip() and not svc.can_use_client_profiles(request):      # el secreto de un perfil de cliente es del dueño
        raise HTTPException(403, "No autorizado")
    values = svc.ExtractionValues.from_form(host=host, token_url=token_url, client_id=client_id, client=client,
                                            space_ids=list(space_id),
                                            probe_connection=probe_connection)
    secret = client_secret
    del client_secret
    if profile_id.strip():                                                    # perfil del dueño: secreto resuelto en el SERVIDOR
        profile = profiles.resolve(profile_id, graph_token)
        del graph_token
        if profile is None:
            return StreamingResponse(iter([profiles.failed_run_events()]), media_type="application/x-ndjson", headers=_HEADERS)
        secret = profile.secret
        del profile
    events: queue.Queue = queue.Queue()
    owner = asd.owner_email(request)
    # F2.2c: solo el DUEÑO puede capturar; para él la observación normalizada queda pendiente (opt-in posterior, en memoria).
    capture_for = str(user.get("sub") or "").strip().lower() if access.is_owner_user(user) else ""

    def worker() -> None:
        nonlocal secret
        try:
            outcome = svc.run(values, secret, user_email=owner, capture_for=capture_for,
                              on_phase=lambda phase: events.put({"t": "phase", "phase": phase, "v": svc.PHASE_COPY[phase]}),
                              on_line=lambda line: events.put({"t": "line", "v": line}))
        finally:
            secret = ""                                                       # el secreto no sobrevive a la ejecución
        inv = (outcome.summary or {}).get("inventory") or {}
        cov = inv.get("coverage") or {}
        # Actividad: sin host, tenant, Spaces ni nombres (B1). Solo cliente, conteos y estado.
        log_activity("connectivity_inventory_run", user=user, scope=SCOPE_COPILOT, area=AREA_CATALOG,
                     client=values.client, spaces=len(values.spaces), state=outcome.state, error_code=outcome.error_code,
                     inventory_status=inv.get("status", ""), exhaustiveness=inv.get("exhaustiveness", ""),
                     extracted=cov.get("extracted_connections", 0), duration_s=outcome.duration_s,
                     operation_id=outcome.operation_id)
        done = {"t": "done", "state": outcome.state, "error_code": outcome.error_code, "message": outcome.message,
                "title": outcome.title, "errors": outcome.errors, "operation_id": outcome.operation_id,
                "duration_s": outcome.duration_s, "summary": outcome.summary}
        if outcome.capture:                                                   # sin host ni nombres: solo tenants del cliente y conteos
            from webapp.services import connectivity_capture_service as capsvc
            opts = capsvc.options(values.client, outcome.capture["host"])
            done["capture"] = {"client": values.client, "expires_in_s": capsvc.PENDING_TTL_S,   # la operación viaja en done.operation_id
                               "spaces": len(outcome.capture["spaces"]), "connections": outcome.capture["connections"], **opts}
        events.put(done)
        events.put(None)

    threading.Thread(target=worker, name="connectivity-inventory", daemon=True).start()

    def stream():
        yield json.dumps({"t": "start"}) + chr(10)
        while True:
            event = events.get()
            if event is None:
                return
            yield json.dumps(event, ensure_ascii=False) + chr(10)

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={**_HEADERS, "X-Accel-Buffering": "no"})
