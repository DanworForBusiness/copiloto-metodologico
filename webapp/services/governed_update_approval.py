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
                           Resultado: ``APPLIED`` | ``ABSTAINED`` | ``FAILED`` (estos dos SOLO si la evidencia acredita que el sobre no cambió);
                           si no, ``RECONCILIATION_REQUIRED``.

RECUPERACIÓN (fase B): ningún GET cambia estados. Al ARRANCAR (``recover_on_boot``, después del restore) una aprobación sin hilo vivo en ESTE proceso:
  · ``REGISTERED``/``VERIFYING`` → ``DENIED`` (no se había consumido; si otro proceso siguiera vivo, su próxima transición falla por estado de origen).
  · ``STARTED`` → ``APPLIED`` SOLO si se acredita el resultado EXACTO (firma del artefacto candidato aprobado + durabilidad remota confirmada); en
    cualquier otro caso ``RECONCILIATION_REQUIRED`` (bloqueante: la solicitud no admite otra aprobación). NO se infiere que otro proceso murió porque
    éste arrancó, y «el sobre anterior sigue presente» NO equivale a «es seguro reintentar».
  · ``reconcile`` (mantenimiento EXPLÍCITO por CLI, con confirmación del operador de que no hay ejecución activa): ``APPLIED`` si el resultado es el
    exacto; ``NOT_APPLIED`` (re-aprobable) solo si el sobre local y el remoto siguen siendo el previo y el diario de la vía asegurada acredita que no
    hubo ``COMMITTED``; si falta evidencia, sigue bloqueada (resolver con los procedimientos de respaldo/restauración existentes).

DISPONIBILIDAD (C5): el proceso arranca en ``PENDING`` y solo pasa a ``READY`` cuando el restore terminó completo y ``recover_on_boot`` cerró todo lo que
encontró; si cualquiera falla, queda ``FAILED`` (cerrado hasta el próximo arranque). Fuera de ``READY`` no se registra, no se arranca y no se autoriza
ninguna escritura gobernada; los GET y el dry-run (sin escrituras) siguen disponibles, pero la vista no ofrece aprobar. Sin esperas temporales.

LÍMITES (no sobre-afirmar): los locks (``gur._lock``) y ``_running`` son de ESTE proceso; las transiciones verifican el estado de origen sobre el registro
LOCAL y el PUT remoto es last-writer-wins. Nada de esto es un compare-and-swap distribuido: con más de una instancia o con solape de deploy no hay
exclusión garantizada (requisito operativo: una réplica y deploy sin solape mientras haya aprobaciones activas).

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
RECONCILIATION_REQUIRED, NOT_APPLIED = "RECONCILIATION_REQUIRED", "NOT_APPLIED"
ACTIVE = frozenset({REGISTERED, VERIFYING, STARTED})
TERMINAL = frozenset({DENIED, APPLIED, ABSTAINED, FAILED, INTERRUPTED, RECONCILIATION_REQUIRED, NOT_APPLIED})
BLOCKING = frozenset({RECONCILIATION_REQUIRED, INTERRUPTED})       # INTERRUPTED: estado previo a la fase B (se trata igual: requiere reconciliación)
# C1 — estado de ORIGEN permitido por destino (ninguna transición repetida: STARTED→STARTED no existe).
ALLOWED_FROM = {VERIFYING: frozenset({REGISTERED}), STARTED: frozenset({VERIFYING}), DENIED: frozenset({REGISTERED, VERIFYING}),
                APPLIED: frozenset({STARTED, RECONCILIATION_REQUIRED, INTERRUPTED}), ABSTAINED: frozenset({STARTED}), FAILED: frozenset({STARTED}),
                RECONCILIATION_REQUIRED: frozenset({STARTED}), NOT_APPLIED: frozenset({RECONCILIATION_REQUIRED, INTERRUPTED})}
EV_EXACT, EV_UNCHANGED, EV_DIVERGENT, EV_UNVERIFIABLE = "EXACT_RESULT", "UNCHANGED", "DIVERGENT", "UNVERIFIABLE"
EXPECTED_KEYS = ("plan_digest", "provenance_digest", "equivalence_digest", "envelope_sha256", "dependency_signature", "fingerprint_schema")
_EXPECTED_SINCE_V2 = frozenset({"fingerprint_schema"})          # ausente en aprobaciones previas: se interpretan bajo su versión original (no se exige)

_running: set[str] = set()                     # aprobaciones con hilo vivo EN ESTE PROCESO (lo que sobrevive a un reinicio es solo el estado persistido)

# C5 — disponibilidad de ESTE proceso para escrituras gobernadas. Arranca CERRADA: solo ``boot_recovery`` la abre.
READINESS_PENDING, READINESS_READY, READINESS_FAILED = "PENDING", "READY", "FAILED"
_readiness: dict = {"state": READINESS_PENDING, "reason": "BOOT_RECOVERY_NOT_RUN"}


def readiness() -> dict:
    return dict(_readiness)


def _set_readiness(state: str, reason: str | None = None) -> None:
    _readiness.update(state=state, reason=reason)


def mark_pending(reason: str = "BOOT_RECOVERY_PENDING") -> None:
    """Antes de servir (lifespan): cierra la compuerta hasta que la recuperación de arranque termine bien."""
    _set_readiness(READINESS_PENDING, reason)


def _not_ready_code() -> str | None:
    state = _readiness["state"]
    return None if state == READINESS_READY else ("RECOVERY_FAILED" if state == READINESS_FAILED else "RECOVERY_PENDING")


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
            "dependency_signature": p.get("dependency_signature"), "fingerprint_schema": p.get("fingerprint_schema")}


def approvable(rec: dict, spaces: list[str]) -> list[str]:
    """Por qué NO se puede aprobar ahora (vacío = se puede). Solo lectura sobre la solicitud y su último dry-run."""
    reasons = []
    not_ready = _not_ready_code()
    if not_ready:
        reasons.append(not_ready)                                          # la vista no ofrece aprobar mientras el proceso no esté READY
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
    if current.get("state") in BLOCKING:
        reasons.append("RECONCILIATION_REQUIRED")
    return reasons


def register(rec: dict, user: dict, role: str, spaces: list[str], *, idempotency_key: str) -> tuple[dict, bool]:
    """Fase 1. ``(aprobación, creada)``. Idempotente por ``idempotency_key`` y con UNA aprobación activa por solicitud."""
    spaces = sorted({str(s).strip() for s in spaces if str(s).strip()})
    not_ready = _not_ready_code()
    if not_ready:
        raise ApprovalRefused(not_ready)                                   # C5: antes de leer o escribir el registro
    with gur._lock:
        fresh = gur.load(rec["request_id"]) or rec
        current = fresh.get("approval") or {}
        if current and current.get("idempotency_key") == idempotency_key:
            return current, False                                          # reintento del MISMO pedido: misma aprobación, nada nuevo
        if current.get("state") in ACTIVE:
            return current, False                                          # ya hay una en curso: no se crea otra
        if current.get("state") in BLOCKING:
            raise ApprovalRefused("RECONCILIATION_REQUIRED")               # un intento consumido sin resultado acreditado bloquea nuevas aprobaciones
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
    """Cambio de estado PERSISTIDO de la aprobación vigente, SOLO desde un estado de origen permitido (``ALLOWED_FROM``) leído bajo el lock: un estado
    distinto del esperado (otra transición ganó, transición repetida, aprobación reemplazada) no escribe nada y devuelve ``None``. Verificación sobre el
    registro LOCAL de este proceso: NO es un compare-and-swap distribuido."""
    with gur._lock:
        fresh = gur.load(request_id)
        appr = (fresh or {}).get("approval") or {}
        if not fresh or appr.get("approval_id") != approval_id or appr.get("state") not in ALLOWED_FROM.get(state, frozenset()):
            return None
        appr.update(fields, state=state)
        appr.setdefault("history", []).append({"state": state, "at": _now(), **({"reason": fields["reason"]} if fields.get("reason") else {})})
        if state == APPLIED:
            fresh["status"] = "APPLIED"                                    # la solicitud queda cerrada: el sobre vigente es el nuevo
        gur._write(fresh)
        return appr


def evidence(rec: dict, appr: dict) -> str:
    """Qué acredita el sobre gobernado ACTUAL sobre una ejecución: ``EXACT_RESULT`` (lleva la firma del artefacto candidato aprobado, cambió respecto del
    previo y, con Spaces, el remoto confirma esos bytes), ``UNCHANGED`` (sigue siendo exactamente el previo), ``DIVERGENT`` (otra cosa) o
    ``UNVERIFIABLE`` (faltan los datos registrados al consumir o no se pudo verificar). Solo lectura."""
    pre, expected = appr.get("pre_sha256"), appr.get("expected_candidate_signature")
    if not pre or not expected:
        return EV_UNVERIFIABLE
    client, am = rec["client_dir"], rec["terminal_am"]
    try:
        sha = gur.envelope_sha(client, am)
        if sha == pre:
            return EV_UNCHANGED
        env = svc._load_envelope(client, am) or {}
        if env.get("assessment_artifact_signature") != expected:
            return EV_DIVERGENT
        if svc.remote_store.enabled() and svc.verify_remote_durability(client, am) != svc.REMOTE_CONFIRMED:
            return EV_UNVERIFIABLE                                         # resultado local exacto pero sin durabilidad acreditada
        return EV_EXACT
    except Exception:                                                      # noqa: BLE001 — sin evidencia legible no se afirma nada
        return EV_UNVERIFIABLE


def _settle_after_start(rec: dict, aid: str, *, outcome: str, if_unchanged: str = "FAILED", **fields) -> str:
    """Cierra un intento YA CONSUMIDO que no informó APPLIED: ``if_unchanged`` (FAILED/ABSTAINED) solo si el sobre sigue siendo el previo; si cambió,
    APPLIED solo con el resultado exacto; si no, RECONCILIATION_REQUIRED. Una escritura parcial nunca queda con un estado que habilite otra aprobación."""
    appr = (gur.load(rec["request_id"]) or {}).get("approval") or {}
    ev = evidence(rec, appr)
    state = APPLIED if ev == EV_EXACT else if_unchanged if ev == EV_UNCHANGED else RECONCILIATION_REQUIRED
    _transition(rec["request_id"], aid, state, outcome=outcome, evidence=ev, **fields)
    return state


def boot_recovery() -> list[dict]:
    """C5 — ÚNICA vía a ``READY``: exige el restore COMPLETO (con persistencia remota) y una ``recover_on_boot`` sin errores ni transiciones perdidas;
    cualquier otra cosa deja ``FAILED`` (la compuerta queda cerrada hasta el próximo arranque)."""
    if svc.remote_store.enabled():
        reason = svc.remote_store.sync_blocked_reason()
        if reason is not None:
            _set_readiness(READINESS_FAILED, f"RESTORE_NOT_COMPLETE:{reason}")
            log.warning("governed_update: compuerta FAILED — restore no completo (%s)", reason)
            return []
    try:
        done = recover_on_boot()
    except Exception as exc:                                               # noqa: BLE001 — recuperación incompleta: cerrado
        _set_readiness(READINESS_FAILED, f"RECOVERY_ERROR:{type(exc).__name__}")
        log.exception("governed_update: compuerta FAILED — la recuperación de arranque falló")
        return []
    if any(d.get("to") is None for d in done):
        _set_readiness(READINESS_FAILED, "RECOVERY_INCOMPLETE")
        log.warning("governed_update: compuerta FAILED — transiciones de recuperación no aplicadas %s", done)
        return done
    _set_readiness(READINESS_READY)
    return done


def recover_on_boot() -> list[dict]:
    """C2/C3 — mecanismo EXPLÍCITO de arranque (después del restore), nunca desde un GET. Ver docstring del módulo. Devuelve lo que hizo (auditable)."""
    done: list[dict] = []
    root = gur._dir()
    for path in (sorted(root.glob("*/*/*.json")) if root.is_dir() else ()):
        rec = gur._read(path)
        appr = (rec or {}).get("approval") or {}
        aid, state = appr.get("approval_id"), appr.get("state")
        if not rec or state not in ACTIVE or aid in _running:
            continue
        if state in (REGISTERED, VERIFYING):
            new = DENIED if _transition(rec["request_id"], aid, DENIED, reason="PROCESS_RESTARTED_BEFORE_START") else None
        else:
            ev = evidence(rec, appr)
            if ev == EV_EXACT:
                new = APPLIED if _transition(rec["request_id"], aid, APPLIED, reason="RECONCILED_EXACT_RESULT", evidence=ev, reconciled=True) else None
            else:
                new = (RECONCILIATION_REQUIRED if _transition(rec["request_id"], aid, RECONCILIATION_REQUIRED, reason="PROCESS_RESTARTED_DURING_EXECUTION",
                                                              evidence=ev) else None)
        done.append({"request_id": rec["request_id"], "approval_id": aid, "from": state, "to": new})
    if done:
        log.warning("governed_update: recuperación de arranque %s", done)
    return done


def _journal_committed(approval_id: str) -> bool | None:
    """¿El diario LOCAL de la vía asegurada registra algún ``COMMITTED``? ``None`` si no hay diario legible (p. ej. FS efímero tras un redeploy)."""
    from webapp.services import governed_backup as gb
    d = _backup_dir(approval_id)
    if not d.is_dir():
        return None if svc.remote_store.enabled() else False               # sin Spaces el disco local persiste: sin directorio no empezó el respaldo
    try:
        return any(e.get("event") == gb.EV_COMMITTED for e in gb.read_journal(d))
    except Exception:                                                      # noqa: BLE001
        return None


def reconcile(request_id: str, *, operator: str, confirm_no_active_execution: bool) -> dict:
    """C3 — reconciliación de MANTENIMIENTO (explícita, por CLI). Solo para ``RECONCILIATION_REQUIRED``/``INTERRUPTED``. El operador CONFIRMA que no hay
    ejecución activa (este proceso no puede acreditarlo sobre otro). Resultado: ``APPLIED`` (resultado exacto), ``NOT_APPLIED`` (sobre local y remoto =
    previo y diario sin ``COMMITTED``: re-aprobable) o ``STILL_BLOCKED`` con la evidencia."""
    rec = gur.load(request_id) or {}
    appr = rec.get("approval") or {}
    if appr.get("state") not in BLOCKING:
        return {"request_id": request_id, "result": "NOT_APPLICABLE", "state": appr.get("state")}
    if appr.get("approval_id") in _running:
        return {"request_id": request_id, "result": "ACTIVE_IN_THIS_PROCESS", "state": appr.get("state")}
    if not confirm_no_active_execution:
        return {"request_id": request_id, "result": "CONFIRMATION_REQUIRED", "state": appr.get("state")}
    aid = appr["approval_id"]
    ev = evidence(rec, appr)
    committed = _journal_committed(aid)
    remote_ok = (not svc.remote_store.enabled()) or svc.verify_remote_durability(rec["client_dir"], rec["terminal_am"]) == svc.REMOTE_CONFIRMED
    if ev == EV_EXACT:
        _transition(request_id, aid, APPLIED, reason="RECONCILED_EXACT_RESULT", evidence=ev, reconciled=True, reconciled_by=operator)
    elif ev == EV_UNCHANGED and committed is False and remote_ok:
        _transition(request_id, aid, NOT_APPLIED, reason="RECONCILED_NO_EFFECT", evidence=ev, reconciled=True, reconciled_by=operator)
    else:
        return {"request_id": request_id, "result": "STILL_BLOCKED", "evidence": ev, "journal_committed": committed, "remote_confirmed": remote_ok}
    return {"request_id": request_id, "result": ((gur.load(request_id) or {}).get("approval") or {}).get("state"), "evidence": ev}


def hub_authorizer(request_id: str, approval_id: str, expected_plan_digest: str) -> Callable[[str, dict], ma.Authorization]:
    """Autorizador ``hub-human-approval/v1`` para la vía asegurada: concede SOLO si la aprobación vigente es ESTA, está ``STARTED`` y el plan recalculado
    por la vía asegurada tiene EXACTAMENTE el digest aprobado. Autenticada (sesión del Hub) y auditable; no es una firma criptográfica."""
    def authorize(migration: str, plan: dict) -> ma.Authorization:
        # C4 — se evalúa JUSTO ANTES del respaldo y de las escrituras (la vía asegurada lo llama tras recalcular el plan). Es una verificación sobre el
        # registro LOCAL y el hilo de ESTE proceso: NO equivale a un compare-and-swap distribuido.
        rec = gur.load(request_id) or {}
        appr = rec.get("approval") or {}
        if _not_ready_code():
            return ma.DENIED("PROCESS_NOT_READY")
        if appr.get("approval_id") != approval_id or appr.get("state") != STARTED:
            return ma.DENIED("HUB_APPROVAL_NOT_ACTIVE")
        if approval_id not in _running:
            return ma.DENIED("NOT_EXECUTING_IN_THIS_PROCESS")
        if rec.get("status") == "APPLIED" or any(a.get("state") == APPLIED for a in rec.get("approvals") or ()):
            return ma.DENIED("ALREADY_APPLIED")
        if appr.get("pre_sha256") and gur.envelope_sha(rec["client_dir"], rec["terminal_am"]) != appr["pre_sha256"]:
            return ma.DENIED("ENVELOPE_CHANGED")
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
              "envelope_sha256": "OBSOLETE", "dependency_signature": "DEPENDENCIES_CHANGED", "fingerprint_schema": "FINGERPRINT_SCHEMA_CHANGED"}
    approved = appr.get("expected") or {}
    for k in EXPECTED_KEYS:
        if k in _EXPECTED_SINCE_V2 and k not in approved:
            continue                                                       # aprobación anterior a v2: se verifica bajo su contrato original
        if approved.get(k) != now.get(k):
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
                                      allow_local_only=local_only, waves=[[rec["client_dir"]]], run_id=appr["approval_id"],
                                      regenerate=svc.regenerate_stale_evidence_replay)          # P0.1: replay verificado, recalculado antes de escribir
        # el Juicio proyectado ya viaja en ``judgment_action`` DENTRO del digest del plan aprobado: un Juicio distinto cambia el digest y el autorizador niega
        return ab.secured_regenerate_selected(entries[:1], [rec["terminal_am"]], authorizer=authorizer, backup_dir=_backup_dir(appr["approval_id"]),
                                              client=rec["client_dir"], allow_local_only=local_only, run_id=appr["approval_id"],
                                              regenerate=svc.regenerate_current_evidence_replay)


def _outcome_state(report: dict) -> tuple[str, str]:
    from webapp.services import assessment_backfill as ab
    outcome = str(report.get("outcome") or "")
    if outcome == ab.APPLIED and report.get("applied", True):
        return APPLIED, outcome
    if outcome in (ab.APPLIED_WITH_ABSTENTIONS, ab.STOPPED_AT_WAVE, ab.ABSTENTIONS_CHANGED, ab.DENIED, ab.REMOTE_REQUIRED, ab.BACKUP_NOT_GUARANTEED, "PLAN_CHANGED",
                   "RECOVERY_ONLY", "NOTHING_TO_DO"):                     # el plan recalculado ya no tiene qué aplicar (p. ej. decisión revocada tras el dry-run)
        return ABSTAINED, outcome
    return FAILED, outcome or "UNKNOWN_OUTCOME"


def start(rec: dict, appr: dict, *, compute: Callable | None = None, execute: Callable | None = None, on_event: Callable | None = None) -> str:
    """Corre las fases 2 y 3 en un hilo (no depende del request HTTP). ``STARTED_THREAD`` | ``ALREADY_RUNNING``."""
    aid = appr["approval_id"]
    if _not_ready_code():
        return "NOT_READY"                                                 # C5: nada arranca fuera de READY
    with gur._lock:
        if aid in _running:
            return "ALREADY_RUNNING"
        persisted = (gur.load(rec["request_id"]) or {}).get("approval") or {}
        if persisted.get("approval_id") != aid or persisted.get("state") != REGISTERED:
            return "NOT_STARTABLE"                                         # ya procesada o reemplazada: nunca se re-ejecuta
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
                           provenance_route=(fresh.get("provenance") or {}).get("route"),
                           pre_sha256=gur.envelope_sha(rec["client_dir"], rec["terminal_am"]),                # evidencia para reconciliar (C3)
                           expected_candidate_signature=fresh.get("candidate_artifact_signature")) is None:   # PUNTO DE CONSUMO: persistido antes de escribir nada
                return
            emit("started", aid)
            try:
                report = execute(gur.load(rec["request_id"]) or rec, {**appr, "state": STARTED}, fresh.get("path") or "")
                state, outcome = _outcome_state(report)
                extra = {"finished_at": _now(), "duration_s": round(time.time() - t0, 1), "run_id": report.get("run_id") or aid, "writes": report.get("writes")}
                if state == APPLIED:
                    _transition(rec["request_id"], aid, APPLIED, outcome=outcome, **extra)
                else:                                                      # no aplicada: el estado lo decide la EVIDENCIA (una escritura parcial no se disfraza)
                    state = _settle_after_start(rec, aid, outcome=outcome, if_unchanged=state, **extra)
                emit("executed", aid, state=state, outcome=outcome)
            except Exception as exc:                                       # noqa: BLE001 — ya empezó: el estado lo decide la evidencia
                log.exception("governed_update approval %s execution failed", aid)
                state = _settle_after_start(rec, aid, outcome=f"EXECUTION_ERROR:{type(exc).__name__}", finished_at=_now())
                emit("executed", aid, state=state, outcome=f"EXECUTION_ERROR:{type(exc).__name__}")
        finally:
            with gur._lock:
                _running.discard(aid)

    threading.Thread(target=worker, name=f"governed-update-approval-{aid[:8]}", daemon=True).start()
    return "STARTED_THREAD"


def _main(argv: list[str] | None = None) -> int:
    """Mantenimiento: ``python -m webapp.services.governed_update_approval --reconcile <request_id> --operator <email> --confirm-no-active-execution``."""
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Reconciliación explícita de una actualización gobernada bloqueada (RECONCILIATION_REQUIRED).")
    ap.add_argument("--reconcile", required=True, metavar="REQUEST_ID")
    ap.add_argument("--operator", required=True)
    ap.add_argument("--confirm-no-active-execution", action="store_true")
    args = ap.parse_args(argv)
    out = reconcile(args.reconcile, operator=args.operator, confirm_no_active_execution=args.confirm_no_active_execution)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("result") in (APPLIED, NOT_APPLIED) else 2


if __name__ == "__main__":
    raise SystemExit(_main())
