"""
Router de ESCRITURA del registro de conectividad (F2.2c.1, `connectivity-registry/v1`): administración gobernada de tenants y ambientes
desde «Tenants y ambientes» (Catálogo de conectividad, módulo 01). La vista (lectura) es `GET /catalog/connectivity-registry` (main.py).

  POST /connectivity-registry/{action}   action ∈ register-tenant · bind-host · unbind-host · assign-space · revoke-assignment · deactivate-tenant
                                         · capture (F2.2c: captura opt-in del inventario normalizado de una extracción F2.1; ver
                                           connectivity_capture_service — exige tenant activo con el host de la extracción vinculado)

Seguridad: MISMO `_authorize_write` de las decisiones gobernadas (B3): sesión (401) → mismo origen (403) → APP_OWNER (403). El `actor`
(email de sesión), el `decided_at` (UTC) y el `event_id` los IMPONE el backend; el `event_id` se deriva del `request_id` del formulario
(actor + request_id), así un doble envío o reintento del MISMO pedido es idempotente y nunca duplica un evento.
Resultado: éxito SOLO si el evento quedó APLICADO al releer el registro (`connectivity_registry_store`). Una carrera que el replay resuelve en
contra devuelve 409 con `status = CONCURRENT_CONFLICT`: el evento quedó guardado y rechazado en el historial, sin simular consistencia transaccional.
Sin inventarios, sin SAP, sin OAuth: el registro no extrae ni homologa nada.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse

from core import remote_store
from tools.catalog.connectivity_governance import registry as reg
from webapp.routers.governed_decisions import _authorize_write
from webapp.services import connectivity_registry_store as store
from webapp.services import connectivity_registry_view as view

router = APIRouter()

ACTIONS = ("register-tenant", "bind-host", "unbind-host", "assign-space", "revoke-assignment", "deactivate-tenant", "capture")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_HEADERS = {"Cache-Control": "no-store"}


def _err(code: int, detail: str, **extra) -> JSONResponse:
    return JSONResponse({"detail": detail, **extra}, status_code=code, headers=_HEADERS)


def _event(action: str, f: dict, common: dict) -> dict:
    tid = f["tenant_id"]
    if action == "register-tenant":
        return reg.register_tenant(tenant_id=tid, display_name=f["display_name"] or None, **common)
    if action == "bind-host":
        return reg.bind_host(tenant_id=tid, host=f["host"], **common)
    if action == "unbind-host":
        return reg.unbind_host(tenant_id=tid, unbinds_event_id=f["target_event_id"], **common)
    if action == "assign-space":
        return reg.assign_space(tenant_id=tid, space=f["space"], environment=f["environment"],
                                supersedes_event_id=f["target_event_id"] or None, **common)
    if action == "revoke-assignment":
        return reg.revoke_assignment(tenant_id=tid, revokes_event_id=f["target_event_id"], **common)
    return reg.deactivate_tenant(tenant_id=tid, **common)


@router.post("/connectivity-registry/{action}", include_in_schema=False)
def write(request: Request, action: str, client: str = Form(...), tenant_id: str = Form(...), rationale: str = Form(...),
          request_id: str = Form(...), display_name: str = Form(""), host: str = Form(""), space: str = Form(""),
          environment: str = Form(""), target_event_id: str = Form(""), assignment: str = Form(""), provenance_kind: str = Form("owner_declaration"),
          evidence_ref: str = Form(""), operation_id: str = Form("")):
    user = _authorize_write(request)
    if action not in ACTIONS:
        raise HTTPException(404, "Acción inexistente")
    client, request_id = client.strip(), request_id.strip()
    if not _REQUEST_ID.match(request_id):
        return _err(400, "Solicitud inválida: falta el identificador del pedido.")
    actor = str(user.get("sub") or "").strip().lower()
    if action == "capture":
        return _capture(actor, operation_id.strip(), tenant_id.strip(), rationale.strip(), request_id)
    provenance = {"kind": provenance_kind.strip()}
    if evidence_ref.strip():
        provenance["evidence_ref"] = evidence_ref.strip()
    common = {"event_id": "cr-" + hashlib.sha256(f"{actor}|{request_id}".encode("utf-8")).hexdigest()[:32], "client": client,
              "actor": actor, "decided_at": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
              "rationale": rationale.strip(), "provenance": provenance}
    if action == "assign-space" and assignment.strip():                            # reasignar: «<event_id vigente visto>::<space>»
        target_event_id, _sep, space = assignment.strip().partition("::")
    fields = {"tenant_id": tenant_id.strip(), "display_name": display_name.strip(), "host": host.strip(), "space": space.strip(),
              "environment": environment.strip(), "target_event_id": target_event_id.strip()}
    try:
        event = _event(action, fields, common)
    except reg.RegistryError as exc:
        return _err(400, f"Solicitud inválida: {exc}")
    try:
        out = store.append_event(event, authorized_owner=True)                       # la autorización real ya la impuso _authorize_write
    except store.ConcurrentConflict as exc:
        return _err(409, "Otra escritura simultánea ganó el orden del registro: el evento quedó guardado como RECHAZADO y no se aplicó. "
                         + view.reason_text(exc.reason), status="CONCURRENT_CONFLICT", reason=exc.reason, event_id=event["event_id"])
    except store.RegistryConflict as exc:
        reason = next((r for r in view.REASON_TEXT if r in str(exc)), "")
        return _err(409, view.reason_text(reason) if reason else "El pedido ya existe con otro contenido.", status="CONFLICT", reason=reason)
    except store.StorageUnconfirmed:
        return _err(503, "No se pudo confirmar la escritura en el almacén: no se informa éxito. Recargá antes de reintentar.", status="UNCONFIRMED")
    except store.HistoryIntegrityError:
        return _err(503, "El registro tiene objetos inconsistentes: operación rechazada.", status="INTEGRITY")
    except remote_store.RemoteReadError:
        return _err(503, "El almacén gobernado no está disponible en este entorno.", status="UNAVAILABLE")
    return JSONResponse({"status": "APPLIED", "idempotent": out["idempotent"], "event_id": event["event_id"], "kind": event["kind"]},
                        headers=_HEADERS)


def _capture(actor: str, operation_id: str, tenant_id: str, rationale: str, request_id: str) -> JSONResponse:
    from webapp.services import connectivity_capture_service as capsvc
    from webapp.services import connectivity_capture_store as cstore
    if not rationale:
        return _err(400, "Solicitud inválida: el motivo es obligatorio.")
    try:
        out = capsvc.capture(operation_id, actor=actor, tenant_id=tenant_id, rationale=rationale, request_id=request_id,
                             now_iso=datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"))
    except capsvc.CaptureRejected as exc:
        return _err(410 if exc.reason == "EXPIRED" else 409, capsvc.REASON_TEXT[exc.reason], status="REJECTED", reason=exc.reason)
    except cstore.CaptureConflict:
        return _err(409, "El pedido ya existe con otro contenido.", status="CONFLICT")
    except (cstore.StorageUnconfirmed, store.StorageUnconfirmed):
        return _err(503, "No se pudo confirmar la captura en el almacén: no se informa éxito.", status="UNCONFIRMED")
    except (cstore.HistoryIntegrityError, store.HistoryIntegrityError):
        return _err(503, "El registro o las capturas tienen objetos inconsistentes: operación rechazada.", status="INTEGRITY")
    except remote_store.RemoteReadError:
        return _err(503, "El almacén gobernado no está disponible en este entorno.", status="UNAVAILABLE")
    cap = out["capture"]
    envs = cap["space_environments"]
    return JSONResponse({"status": "APPLIED", "idempotent": out["idempotent"], "capture_id": cap["capture_id"], "tenant_id": cap["tenant_id"],
                         "client": cap["client"], "connections": cap["observation"]["coverage"]["discovered_connections"],
                         "governed_spaces": sum(1 for v in envs.values() if v), "ungoverned_spaces": sum(1 for v in envs.values() if not v)},
                        headers=_HEADERS)
