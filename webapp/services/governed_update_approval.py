"""governed_update_approval.py — aprobación y ejecución asegurada de una actualización gobernada desde el Hub (PR-2).

Tres fases SEPARADAS (contrato PR-2, ajuste 3), con estado PERSISTIDO en la solicitud (registro autoritativo ``governed_update_requests``):

  1. AUTORIZACIÓN ........ ``register``: APP_OWNER/CONTENT_OWNER aprueba con el origen DECLARADO (uno o más Spaces, sin preselección) y los valores que
                           revisó (digest del plan, de la procedencia, de la equivalencia, sha256 del sobre, firma de dependencias). Estado ``REGISTERED``.
                           Una sola aprobación ACTIVA por solicitud; repetir el mismo pedido (``idempotency_key``) devuelve la misma.
  2. VERIFICACIÓN PREVIA . ``VERIFYING``: se RECALCULA todo (dry-run restringido al origen declarado + procedencia + equivalencia + integridad) y se
                           compara con lo aprobado. Cualquier diferencia → ``DENIED`` con causa; la aprobación NO se consumió (no empezó nada).
  3. EJECUCIÓN ........... ``STARTED`` se persiste ANTES de invocar la vía asegurada: es el punto de consumo (un reintento ya no puede ejecutarla). La vía
                           asegurada existente hace respaldo durable, postcheck, confirmación remota por bytes y verificación de que no cambió otro sobre;
                           su autorizador es ``hub-human-approval/v1`` (autenticada y auditable; NO es una firma criptográfica y no toca la vía Ed25519).
                           Resultado: ``APPLIED`` | ``ABSTAINED`` | ``FAILED``. Un proceso que muere en ``STARTED`` queda ``INTERRUPTED`` (sin reintento
                           automático: requiere verificación); uno que muere en ``VERIFYING`` pasa a ``DENIED`` (no había empezado).

Nunca cambia metodología, scoring ni el Juicio: aplica la reconstrucción gobernada que el motor produce con la evidencia del origen autorizado.
Una sola versión viva por Assessment gobernado (la vía asegurada reemplaza el sobre; el anterior queda en el respaldo durable).
"""
from __future__ import annotations

import logging
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import config
from webapp.services import assessment_service as svc
from webapp.services import governed_update_requests as gur
from webapp.services import migration_approval as ma

log = logging.getLogger("danwor.governed_update_approval")

APPROVAL_SCHEMA = "hub-human-approval/v1"
REGISTERED, VERIFYING, DENIED, STARTED, APPLIED, ABSTAINED, FAILED, INTERRUPTED = (
    "REGISTERED", "VERIFYING", "DENIED", "STARTED", "APPLIED", "ABSTAINED", "FAILED", "INTERRUPTED")
ACTIVE = frozenset({REGISTERED, VERIFYING, STARTED})
TERMINAL = frozenset({DENIED, APPLIED, ABSTAINED, FAILED, INTERRUPTED})
EXPECTED_KEYS = ("plan_digest", "provenance_digest", "equivalence_digest", "envelope_sha256", "dependency_signature")

_running: set[str] = set()                     # aprobaciones con hilo vivo EN ESTE PROCESO (lo que sobrevive a un reinicio es solo el estado persistido)


class ApprovalRefused(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _now() -> str:
    return gur._now()


def expected_from_preview(rec: dict, preview: dict | None) -> dict:
    """Valores que el aprobador REVISÓ (los del último dry-run restringido) y que la ejecución exige idénticos."""
    p = preview or {}
    return {"plan_digest": p.get("plan_digest"), "provenance_digest": (p.get("provenance") or {}).get("digest"),
            "equivalence_digest": (p.get("fingerprint") or {}).get("equivalence_digest"), "envelope_sha256": p.get("governed_sha256"),
            "dependency_signature": p.get("dependency_signature")}


def approvable(rec: dict, spaces: list[str]) -> list[str]:
    """Por qué NO se puede aprobar ahora (vacío = se puede). Solo lectura sobre la solicitud y su último dry-run."""
    reasons = []
    if gur.current_status(rec) != gur.OPEN:
        reasons.append("REQUEST_OBSOLETE")
    if not ((rec.get("source") or {}).get("fingerprint")):
        reasons.append("REQUEST_WITHOUT_FINGERPRINT")
    p = rec.get("preview") or {}
    if not spaces:
        reasons.append("NO_SPACE_DECLARED")
    elif sorted(p.get("spaces") or []) != sorted(spaces):
        reasons.append("PREVIEW_NOT_FOR_DECLARED_SPACES")
    if p.get("state") != gur.PREVIEW_READY:
        reasons.append("PREVIEW_NOT_READY")
    if (p.get("provenance") or {}).get("state") != "ACCREDITED":
        reasons.append("PROVENANCE_NOT_ACCREDITED")
    if p.get("governed_sha256") != (rec.get("governed") or {}).get("sha256"):
        reasons.append("PREVIEW_OUTDATED")
    current = rec.get("approval") or {}
    if current.get("state") in ACTIVE:
        reasons.append("APPROVAL_IN_PROGRESS")
    return reasons


def register(rec: dict, user: dict, role: str, spaces: list[str], *, idempotency_key: str) -> tuple[dict, bool]:
    """Fase 1. ``(aprobación, creada)``. Idempotente por ``idempotency_key`` y con UNA aprobación activa por solicitud."""
    spaces = sorted({str(s).strip() for s in spaces if str(s).strip()})
    with gur._lock:
        fresh = gur.load(rec["request_id"]) or rec
        current = fresh.get("approval") or {}
        if current and current.get("idempotency_key") == idempotency_key:
            return current, False                                          # reintento del MISMO pedido: misma aprobación, nada nuevo
        if current.get("state") in ACTIVE:
            return current, False                                          # ya hay una en curso: no se crea otra
        reasons = approvable(fresh, spaces)
        if reasons:
            raise ApprovalRefused(reasons[0])
        approval = {"schema": APPROVAL_SCHEMA, "approval_id": uuid.uuid4().hex, "idempotency_key": idempotency_key,
                    "request_id": fresh["request_id"], "client_dir": fresh["client_dir"], "terminal_am": fresh["terminal_am"],
                    "approver": gur.user_email(user), "role": role, "declared_spaces": spaces,
                    "expected": expected_from_preview(fresh, fresh.get("preview")), "state": REGISTERED,
                    "history": [{"state": REGISTERED, "at": _now()}]}
        if fresh.get("approval"):
            fresh.setdefault("approvals", []).append(fresh["approval"])    # las anteriores (terminales) quedan en la bitácora
        fresh["approval"] = approval
        gur._write(fresh)
        rec.update(fresh)
    return approval, True


def _transition(request_id: str, approval_id: str, state: str, **fields) -> dict | None:
    """Cambio de estado PERSISTIDO de la aprobación vigente (solo si sigue siendo la misma y no es terminal)."""
    with gur._lock:
        fresh = gur.load(request_id)
        appr = (fresh or {}).get("approval") or {}
        if not fresh or appr.get("approval_id") != approval_id or appr.get("state") in TERMINAL:
            return None
        appr.update(fields, state=state)
        appr.setdefault("history", []).append({"state": state, "at": _now(), **({"reason": fields["reason"]} if fields.get("reason") else {})})
        if state == APPLIED:
            fresh["status"] = "APPLIED"                                    # la solicitud queda cerrada: el sobre vigente es el nuevo
        gur._write(fresh)
        return appr


def recover(rec: dict) -> dict:
    """Al leer la solicitud: una aprobación activa SIN hilo vivo en este proceso (reinicio) se cierra de forma segura."""
    appr = rec.get("approval") or {}
    if appr.get("state") in ACTIVE and appr.get("approval_id") not in _running:
        if appr["state"] == STARTED:
            _transition(rec["request_id"], appr["approval_id"], INTERRUPTED, reason="PROCESS_RESTARTED_DURING_EXECUTION")
        else:
            _transition(rec["request_id"], appr["approval_id"], DENIED, reason="PROCESS_RESTARTED_BEFORE_START")
        return gur.load(rec["request_id"]) or rec
    return rec


def hub_authorizer(request_id: str, approval_id: str, expected_plan_digest: str) -> Callable[[str, dict], ma.Authorization]:
    """Autorizador ``hub-human-approval/v1`` para la vía asegurada: concede SOLO si la aprobación vigente es ESTA, está ``STARTED`` y el plan recalculado
    por la vía asegurada tiene EXACTAMENTE el digest aprobado. Autenticada (sesión del Hub) y auditable; no es una firma criptográfica."""
    def authorize(migration: str, plan: dict) -> ma.Authorization:
        rec = gur.load(request_id) or {}
        appr = rec.get("approval") or {}
        if appr.get("approval_id") != approval_id or appr.get("state") != STARTED:
            return ma.DENIED("HUB_APPROVAL_NOT_ACTIVE")
        if plan.get("digest") != expected_plan_digest:
            return ma.DENIED("PLAN_DIGEST_MISMATCH")
        doc = {"schema": APPROVAL_SCHEMA, "approval_id": approval_id, "request_id": request_id, "migration": migration, "plan_digest": plan.get("digest"),
               "approver": appr.get("approver"), "role": appr.get("role"), "declared_spaces": appr.get("declared_spaces"),
               "expected": appr.get("expected"), "approved_at": (appr.get("history") or [{}])[0].get("at"),
               "assurance": "AUTHENTICATED_HUB_SESSION"}
        return ma.Authorization(True, "HUB_HUMAN_APPROVAL", approver=appr.get("approver"), operator=appr.get("approver"), document=doc,
                                duties_mode="HUB_HUMAN_APPROVAL", duties_collapsed=True, operator_assurance="AUTHENTICATED_HUB_SESSION")
    return authorize


def _precheck(rec: dict, appr: dict, compute: Callable) -> tuple[list[str], dict]:
    """Fase 2: recalcula y compara con lo aprobado. Devuelve (causas, dry-run recalculado)."""
    fresh = compute(rec, spaces=appr["declared_spaces"])
    now = expected_from_preview(rec, fresh)
    reasons = []
    if fresh.get("state") != gur.PREVIEW_READY:
        reasons.append(f"PREVIEW_{fresh.get('state')}")
    if (fresh.get("provenance") or {}).get("state") != "ACCREDITED":
        reasons.append("PROVENANCE_NOT_ACCREDITED")
    labels = {"plan_digest": "PLAN_CHANGED", "provenance_digest": "PROVENANCE_CHANGED", "equivalence_digest": "EVIDENCE_CHANGED",
              "envelope_sha256": "OBSOLETE", "dependency_signature": "DEPENDENCIES_CHANGED"}
    for k in EXPECTED_KEYS:
        if (appr.get("expected") or {}).get(k) != now.get(k):
            reasons.append(labels[k])
    return reasons, fresh


def _backup_dir(approval_id: str) -> Path:
    base = Path(getattr(config, "BASE_DIR", tempfile.gettempdir())) / "governed-update-backups"
    return base / approval_id


def _execute(rec: dict, appr: dict, path: str) -> dict:
    """Fase 3: UN modelo por la vía asegurada existente, con el contexto de conexiones del origen declarado y el autorizador del Hub."""
    from webapp.services import assessment_backfill as ab
    entries = gur._entries_for(rec["client_dir"], rec["terminal_am"])
    authorizer = hub_authorizer(rec["request_id"], appr["approval_id"], appr["expected"]["plan_digest"])
    local_only = not svc.remote_store.enabled()                            # sin Spaces (dev/tests aislados): respaldo local; en producción es OBLIGATORIO el remoto
    with svc.connection_spaces(appr["declared_spaces"]):
        if path.startswith("MIGRATION:"):
            return ab.secured_migrate(entries[:1], path.split(":", 1)[1], authorizer=authorizer, backup_dir=_backup_dir(appr["approval_id"]),
                                      allow_local_only=local_only, waves=[[rec["client_dir"]]], run_id=appr["approval_id"])
        # el Juicio proyectado ya viaja en ``judgment_action`` DENTRO del digest del plan aprobado: un Juicio distinto cambia el digest y el autorizador niega
        return ab.secured_regenerate_selected(entries[:1], [rec["terminal_am"]], authorizer=authorizer, backup_dir=_backup_dir(appr["approval_id"]),
                                              client=rec["client_dir"], allow_local_only=local_only, run_id=appr["approval_id"])


def _outcome_state(report: dict) -> tuple[str, str]:
    from webapp.services import assessment_backfill as ab
    outcome = str(report.get("outcome") or "")
    if outcome == ab.APPLIED and report.get("applied", True):
        return APPLIED, outcome
    if outcome in (ab.APPLIED_WITH_ABSTENTIONS, ab.STOPPED_AT_WAVE, ab.ABSTENTIONS_CHANGED, ab.DENIED, ab.REMOTE_REQUIRED, ab.BACKUP_NOT_GUARANTEED, "PLAN_CHANGED"):
        return ABSTAINED, outcome
    return FAILED, outcome or "UNKNOWN_OUTCOME"


def start(rec: dict, appr: dict, *, compute: Callable | None = None, execute: Callable | None = None, on_event: Callable | None = None) -> str:
    """Corre las fases 2 y 3 en un hilo (no depende del request HTTP). ``STARTED_THREAD`` | ``ALREADY_RUNNING``."""
    aid = appr["approval_id"]
    with gur._lock:
        if aid in _running:
            return "ALREADY_RUNNING"
        _running.add(aid)
    compute = compute or gur.compute_preview
    execute = execute or _execute
    emit = on_event or (lambda *a, **k: None)

    def worker() -> None:
        t0 = time.time()
        try:
            if _transition(rec["request_id"], aid, VERIFYING) is None:
                return
            try:
                reasons, fresh = _precheck(rec, appr, compute)
            except Exception as exc:                                       # noqa: BLE001 — no empezó nada: se deniega, la aprobación no se consumió
                _transition(rec["request_id"], aid, DENIED, reason=f"PRECHECK_ERROR:{type(exc).__name__}")
                emit("rejected", aid, reason=f"PRECHECK_ERROR:{type(exc).__name__}")
                return
            if reasons:
                _transition(rec["request_id"], aid, DENIED, reason=reasons[0], reasons=reasons)
                emit("rejected", aid, reason=reasons[0])
                return
            if _transition(rec["request_id"], aid, STARTED, started_at=_now(), verified=expected_from_preview(rec, fresh),
                           provenance_route=(fresh.get("provenance") or {}).get("route")) is None:   # PUNTO DE CONSUMO: persistido antes de escribir nada
                return
            emit("started", aid)
            try:
                report = execute(gur.load(rec["request_id"]) or rec, {**appr, "state": STARTED}, fresh.get("path") or "")
                state, outcome = _outcome_state(report)
                _transition(rec["request_id"], aid, state, outcome=outcome, finished_at=_now(), duration_s=round(time.time() - t0, 1),
                            run_id=report.get("run_id") or aid, writes=report.get("writes"))
                emit("executed", aid, state=state, outcome=outcome)
            except Exception as exc:                                       # noqa: BLE001 — ya empezó: FALLIDA (la vía asegurada conserva el respaldo)
                log.exception("governed_update approval %s execution failed", aid)
                _transition(rec["request_id"], aid, FAILED, outcome=f"EXECUTION_ERROR:{type(exc).__name__}", finished_at=_now())
                emit("executed", aid, state=FAILED, outcome=f"EXECUTION_ERROR:{type(exc).__name__}")
        finally:
            with gur._lock:
                _running.discard(aid)

    threading.Thread(target=worker, name=f"governed-update-approval-{aid[:8]}", daemon=True).start()
    return "STARTED_THREAD"
