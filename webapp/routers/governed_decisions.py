"""
Router de DECISIONES DE GOBIERNO (v4.B / B3): operaciones backend autenticadas de ratificación/revocación de contratos
CDS/API + consulta técnica. SIN UI (eso es B4).

Seguridad (patrón existente del Hub): sesión válida (`auth_service.session_user` → 401) → mismo origen para escrituras
(`same_origin_guard.is_same_origin_write` → 403) → autorización del APP_OWNER (`access.is_owner_user` → 403). El `actor`
(email de sesión), `event_id` (uuid4) y `decided_at` (UTC) los IMPONE el backend; NUNCA se aceptan del navegador (tampoco el
digest). El `request_id` (idempotencia A) es un token opaco del cliente. Independiente de Judgment / canonical-evidence (D8).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request

from webapp import access, same_origin_guard
from webapp.services import auth_service
from webapp.services import governed_decisions as gd
from webapp.services import governed_decisions_ops as ops
from webapp.services import governed_decisions_store as store

router = APIRouter()


def _owner(request: Request) -> dict:
    """401 sin sesión → 403 si no es el APP_OWNER. Autorización REAL server-side (no un indicador del navegador)."""
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    if not access.is_owner_user(user):
        raise HTTPException(403, "No autorizado")
    return user


def _session(request: Request) -> dict:
    """401 sin sesión. CONSULTA (B4, Política B): cualquier usuario autenticado del Hub puede LEER el estado de un contrato —
    misma visibilidad que ya tienen los informes de Assessment de donde deriva la evidencia (ver B4_ACCESS_MODEL.md). La
    ESCRITURA (ratify/revoke) sigue siendo owner-only vía `_authorize_write`."""
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    return user


def _authorize_write(request: Request) -> dict:
    """Escritura: sesión → same-origin → APP_OWNER."""
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen no permitido")
    if not access.is_owner_user(user):
        raise HTTPException(403, "No autorizado")
    return user


def _identity(remote_connection: str, remote_entity: str) -> dict:
    conn = (remote_connection or "").strip()
    return {"remote_connection": conn or None, "remote_entity": (remote_entity or "").strip()}


# errores de la capa funcional/almacén → códigos HTTP estables (sin filtrar internals).
# Orden = prioridad de coincidencia (subclases antes que bases).
_HTTP = (
    (ops.ContractEvidenceInsufficient, 404, "Evidencia de contrato insuficiente o identidad inexistente"),
    (ops.ContractEvidenceUnverifiable, 409, "Evidencia de contrato no verificable (Assessment ilegible/alterado): operación rechazada"),
    (ops.ContractEvidenceAmbiguous, 409, "Evidencia de contrato contradictoria entre Assessments"),
    (ops.IdempotencyConflict, 409, "request_id reutilizado con contenido distinto"),
    (ops.RevocationNotAllowed, 409, "Revocación no permitida (huérfana/repetida/no vigente)"),
    (store.DecisionConflict, 409, "Conflicto de almacenamiento (create-only)"),
    (store.StorageUnconfirmed, 503, "No se pudo confirmar la escritura; no se informa éxito"),
    (store.HistoryIntegrityError, 409, "Historial de decisiones inconsistente"),
    (ops.GovernedOpsError, 400, "Solicitud inválida"),
    (gd.GovernedDecisionError, 400, "Solicitud inválida"),
)
_MAPPED = tuple(etype for etype, _c, _m in _HTTP)


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


@router.post("/governed-decisions/ratify", include_in_schema=False)
def ratify(request: Request, client: str = Form(...), remote_connection: str = Form(""), remote_entity: str = Form(...),
           rationale: str = Form(...), request_id: str = Form(...)):
    user = _authorize_write(request)
    actor = str(user.get("sub") or "").strip().lower()
    out = _run(lambda: ops.ratify(client=client.strip(), identity=_identity(remote_connection, remote_entity),
                                  rationale=rationale, actor=actor, now=datetime.now(timezone.utc),
                                  event_id="gd-" + uuid.uuid4().hex, request_id=request_id.strip()))
    return {"outcome": out["outcome"], "event_id": out["event"]["event_id"], "subject": out["event"]["subject"],
            "digest": out["event"]["evidence_ref"]["digest"], "consumers": out.get("consumers", [])}


@router.post("/governed-decisions/revoke", include_in_schema=False)
def revoke(request: Request, client: str = Form(...), remote_connection: str = Form(""), remote_entity: str = Form(...),
           rationale: str = Form(...), request_id: str = Form(...)):
    user = _authorize_write(request)
    actor = str(user.get("sub") or "").strip().lower()
    out = _run(lambda: ops.revoke(client=client.strip(), identity=_identity(remote_connection, remote_entity),
                                  rationale=rationale, actor=actor, now=datetime.now(timezone.utc),
                                  event_id="gd-" + uuid.uuid4().hex, request_id=request_id.strip()))
    return {"outcome": out["outcome"], "event_id": out["event"]["event_id"], "subject": out["event"]["subject"],
            "revoked_event_id": out.get("revoked_event_id")}


@router.get("/governed-decisions/status", include_in_schema=False)
def status(request: Request, client: str, remote_entity: str, remote_connection: str = ""):
    _session(request)                                          # consulta técnica: cualquier usuario autenticado (B4, Política B); escritura sigue owner-only
    recon = _run(lambda: ops.status(client.strip(), _identity(remote_connection, remote_entity), datetime.now(timezone.utc)))
    st = recon["status"]
    # dimensiones INDEPENDIENTES; una ratificación NO implica cumplimiento metodológico
    return {"subject": recon["subject"], "decision": st["decision"], "evidence": st["evidence"], "review": st["review"],
            "principal": st["principal"], "effective_review_by": st.get("effective_review_by"),
            "evidence_digest": recon.get("evidence_digest"), "consumers": recon.get("consumers", []),
            "events": [{"event": e["event"], "event_id": e["event_id"], "decided_at": e["decided_at"],
                        "actor": e["actor"], "rationale": e["rationale"]} for e in recon["events"]]}
