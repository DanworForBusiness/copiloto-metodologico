"""
Router de RESOLUCIONES DE REVISIÓN (Iteración III): operaciones backend autenticadas para resolver / reconciliar / revocar
revisiones metodológicas del Assessment, y consultar su estado. La UI (badges + diálogo) vive en el informe del Assessment.

Seguridad (MISMO patrón que las decisiones de gobierno CDS): sesión válida (`auth_service.session_user` → 401) → mismo origen
para escrituras (`same_origin_guard.is_same_origin_write` → 403) → APP_OWNER (`access.is_owner_user` → 403). El `actor` (email de
sesión), `event_id` (uuid) y `decided_at` (UTC) los IMPONE el backend; NUNCA se aceptan del navegador (tampoco el digest, que se
recomputa del sidecar ligado al perfil actual). Independiente del Juicio / scoring (D8).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Query, Request

from webapp import access, same_origin_guard
from webapp.services import assessment_service as svc
from webapp.services import auth_service, catalog_reads
from webapp.services import governed_decisions as gd
from webapp.services import governed_decisions_store as store
from webapp.services import review_resolution_ops as ops

router = APIRouter()


def _authorize_write(request: Request) -> str:
    """Escritura: sesión → same-origin → APP_OWNER. Devuelve el actor (email de sesión en minúsculas)."""
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen no permitido")
    if not access.is_owner_user(user):
        raise HTTPException(403, "No autorizado")
    return str(user.get("sub") or "").strip().lower()


def _session_owner_flag(request: Request) -> bool:
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    return bool(access.is_owner_user(user))


def _entry_or_404(flow: str):
    mod, entries = catalog_reads._catalog_entries()
    entry = mod.find_flow_by_id(entries, flow) if (mod and flow) else None
    if entry is None:
        raise HTTPException(404, "Flujo no encontrado en el catálogo")
    return entry


def _status_or_404(flow: str):
    """Assessment vigente (perfil + sidecar) desde la caché gobernada. 409 si no es consultable (no hay perfil que resolver)."""
    status = svc.status_for_entry(_entry_or_404(flow))
    if status.profile is None:
        raise HTTPException(409, "El Assessment no tiene un informe vigente que resolver")
    return status


def _identity_from_tid(technical_id: str) -> dict:
    try:
        ident = json.loads(technical_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "Identidad de revisión inválida")
    if not isinstance(ident, dict) or ident.get("klass") not in gd.SUPPORTED_KLASSES:
        raise HTTPException(400, "Identidad de revisión inválida")
    return ident


def _auth_ctx(actor: str) -> ops.AuthContext:
    # Las guardas HTTP ya verificaron owner + same-origin; el contrato de ops lo re-exige fail-closed.
    return ops.AuthContext(actor=actor, is_owner=True, same_origin=True)


_HTTP = (
    (ops.FootprintUnavailable, 409, "Huella de evidencia no verificable: no se resuelve por defecto"),
    (ops.AuthorizationDenied, 403, "No autorizado"),
    (ops.SubjectStateError, 409, "La operación no coincide con el estado actual de la revisión"),
    (store.DecisionConflict, 409, "Conflicto de almacenamiento (create-only)"),
    (store.StorageUnconfirmed, 503, "No se pudo confirmar la escritura; no se informa éxito"),
    (store.HistoryIntegrityError, 409, "Historial de resoluciones inconsistente"),
    (ops.ReviewResolutionError, 400, "Solicitud inválida"),
    (gd.GovernedDecisionError, 400, "Solicitud inválida"),
)
_MAPPED = tuple(e for e, _c, _m in _HTTP)


def _run(fn):
    try:
        return fn()
    except HTTPException:
        raise
    except _MAPPED as exc:
        for etype, code, msg in _HTTP:
            if isinstance(exc, etype):
                raise HTTPException(code, msg) from exc
        raise HTTPException(500, "Error") from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_id() -> str:
    return "rr-" + uuid.uuid4().hex


@router.post("/review-resolutions/resolve", include_in_schema=False)
def resolve(request: Request, flow: str = Form(...), client: str = Form(...), technical_id: str = Form(...),
            disposition: str = Form(...), rationale: str = Form(...), request_id: str = Form(...),
            base_event_id: str = Form("")):
    actor = _authorize_write(request)
    status = _status_or_404(flow)
    ident = _identity_from_tid(technical_id)
    out = _run(lambda: ops.resolve_review(
        client=client.strip(), identity=ident, disposition=disposition.strip(), rationale=rationale,
        decided_at=_now(), event_id=_event_id(), sidecar=status.review_evidence, profile=status.profile,
        auth=_auth_ctx(actor), base_event_id=(base_event_id.strip() or None), request_id=request_id.strip()))
    return {"outcome": "stored" if out["store"].get("stored") else "idempotent", "event_id": out["event"]["event_id"],
            "disposition": out["event"]["disposition"]}


@router.post("/review-resolutions/reconcile", include_in_schema=False)
def reconcile(request: Request, flow: str = Form(...), client: str = Form(...), technical_id: str = Form(...),
              disposition: str = Form(...), rationale: str = Form(...), request_id: str = Form(...),
              supersedes: str = Form(...)):
    actor = _authorize_write(request)
    status = _status_or_404(flow)
    ident = _identity_from_tid(technical_id)
    supersedes_ids = [s.strip() for s in supersedes.split(",") if s.strip()]
    out = _run(lambda: ops.reconcile_review(
        client=client.strip(), identity=ident, disposition=disposition.strip(), rationale=rationale,
        decided_at=_now(), event_id=_event_id(), sidecar=status.review_evidence, profile=status.profile,
        auth=_auth_ctx(actor), supersedes_event_ids=supersedes_ids, request_id=request_id.strip()))
    return {"outcome": "reconciled", "event_id": out["event"]["event_id"], "disposition": out["event"]["disposition"]}


@router.post("/review-resolutions/revoke", include_in_schema=False)
def revoke(request: Request, flow: str = Form(...), client: str = Form(...), technical_id: str = Form(...),
           revokes_event_id: str = Form(...), rationale: str = Form(...), request_id: str = Form(...)):
    actor = _authorize_write(request)
    _status_or_404(flow)                                    # exige informe vigente (coherencia con la lectura)
    out = _run(lambda: ops.revoke_review(
        client=client.strip(), technical_id=technical_id, revokes_event_id=revokes_event_id.strip(),
        rationale=rationale, decided_at=_now(), event_id=_event_id(), auth=_auth_ctx(actor), request_id=request_id.strip()))
    return {"outcome": "revoked", "event_id": out["event"]["event_id"], "revoked_event_id": revokes_event_id.strip()}


@router.get("/review-resolutions/status", include_in_schema=False)
def status(request: Request, flow: str, client: str, technical_id: str):
    _session_owner_flag(request)                            # consulta: cualquier usuario autenticado del Hub
    st = _status_or_404(flow)
    recon = _run(lambda: ops.review_status(client=client.strip(), technical_id=technical_id, sidecar=st.review_evidence,
                                           profile=st.profile, now=datetime.now(timezone.utc), presence="PRESENT"))
    return {"management": recon["management"], "evidence": recon["evidence"], "review": recon["review"],
            "disposition": recon.get("disposition"), "contested_event_ids": recon.get("contested_event_ids") or [],
            "active_event_id": recon.get("active_event_id")}
