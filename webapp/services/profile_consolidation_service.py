"""
profile_consolidation_service.py — «Consolidar perfiles» (Capital Humano): dos perfiles del Banco de CVs que son la MISMA persona pasan a ser UNA identidad activa, conservando
ambos CVs, su procedencia y las referencias existentes. NO es un motor de deduplicación: la operación la inicia una persona con permiso explícito sobre exactamente dos perfiles.

Contrato (owner):
  * Permiso exclusivo APP_OWNER (se exige en el router; el servicio exige un ``actor`` y jamás decide por similitud de nombres).
  * Bloqueo estricto: si el perfil SECUNDARIO tiene candidaturas (búsquedas), entrevistas, onboarding —o no se puede verificar— NO se consolida y se listan las relaciones; nada se migra por supuestos.
  * No se mueve ningún archivo ni carpeta de SharePoint: los documentos conservan ``site_id``/``drive_id``/``item_id``/``web_url``/``folder_item_id``; solo cambia a qué candidato pertenecen
    (``profile_document_repository.reassign_for_consolidation``, excepción acotada y autorizada por la intención auditada).
  * Operación RECUPERABLE con visibilidad controlada: una consolidación parcial nunca aparece como exitosa ni deja a los dos perfiles operables.

Máquina de pasos (cada uno idempotente; el estado se DEDUCE de los datos, no de un flag que pueda mentir):
    0. PLAN (solo lectura) → 1. INTENCIÓN durable (``human-capital/profile-consolidations/<operación>/intent.json``, write-once, fail-closed; incluye las imágenes previas de ambos perfiles y de los
       documentos) → 2. BLOQUEO de ambos perfiles (``consolidation.state = IN_PROGRESS``: dejan de listarse, abrirse, editarse y recibir documentos/candidaturas) → 3. REASIGNACIÓN de los documentos del
       secundario al principal → 4. el secundario pasa a baja lógica (``consolidated_into``) → 5. el principal recibe los campos resueltos y se DESBLOQUEA (único instante en que la consolidación «aparece») →
       6. ``result.json`` COMPLETED.
    Si un paso falla, la operación queda PENDIENTE (ambos perfiles siguen ocultos y bloqueados) y se puede REANUDAR (idempotente, mismo ``operation_id``) o REVERTIR (restaura las imágenes previas).
    Reintentar la misma consolidación ya completada es un no-op (``ALREADY_CONSOLIDATED``).

Persistencia: perfiles y documentos usan sus repositorios (revisión, escritura atómica, push a Spaces con compensación); la auditoría usa ``remote_store.write_through`` fail-closed (como la exportación de CVs).
Lock de proceso (``_EXEC_LOCK``) + bloqueo por perfil: dos consolidaciones concurrentes sobre un mismo perfil no se pisan (la segunda ve el perfil bloqueado). Réplica única, como el resto de CCHH.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import config
from core import remote_store
from webapp.services import (
    busqueda_repository, entrevista_repository, onboarding_repository, profile_document_repository, profile_repository,
)

_logger = logging.getLogger("danwor.human_capital")

SCHEMA = 1
OPERATION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MERGE_FIELDS = profile_repository._BUSINESS_FIELDS
FIELD_LABELS = {
    "first_name": "Nombre", "last_name": "Apellido", "email": "Email", "phone": "Teléfono", "linkedin_url": "LinkedIn", "location": "Ubicación",
    "profile_skills": "Perfil / Skills", "seniority": "Seniority", "source": "Fuente", "tags": "Etiquetas", "notes": "Notas", "potential_rating": "Scoring", "potential_mark": "Marca de potencial",
}
_PROFILE_KEYS_TOUCHED_ON_DUPLICATE = ("deleted_at", "deleted_by", "consolidated_into", "consolidation")

# estados de resultado
READY, BLOCKED, INVALID, ALREADY_CONSOLIDATED = "READY", "BLOCKED", "INVALID", "ALREADY_CONSOLIDATED"
COMPLETED, PENDING, ABORTED, STALE_PLAN, AUDIT_UNAVAILABLE = "COMPLETED", "PENDING", "ABORTED", "STALE_PLAN", "AUDIT_UNAVAILABLE"

_EXEC_LOCK = threading.RLock()


class ConsolidationAuditUnavailable(Exception):
    """La auditoría durable no se pudo confirmar: la operación no empieza (intención) o no se da por cerrada (resultado)."""


class ConsolidationStepError(Exception):
    """Un paso no pudo completarse; ``code`` estable. La operación queda pendiente (recuperable)."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


@dataclass
class Relations:
    busquedas: list[dict] = field(default_factory=list)
    entrevistas: list[dict] = field(default_factory=list)
    onboardings: list[dict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.busquedas) + len(self.entrevistas) + len(self.onboardings)

    def counts(self) -> dict:
        return {"busquedas": len(self.busquedas), "entrevistas": len(self.entrevistas), "onboardings": len(self.onboardings)}


@dataclass
class Plan:
    status: str
    primary: dict | None = None
    duplicate: dict | None = None
    conflicts: list[dict] = field(default_factory=list)           # [{field, label, values: {profile_id: valor}}] — requieren elección explícita
    fills: list[dict] = field(default_factory=list)               # [{field, label, value}] — el principal está vacío y el duplicado aporta el dato
    documents: dict = field(default_factory=dict)                 # profile_id -> [documentos]
    relations: dict = field(default_factory=dict)                 # profile_id -> Relations
    blockers: list[dict] = field(default_factory=list)            # [{code, message}]
    digest: str = ""
    operation_id: str = ""                                        # ALREADY_CONSOLIDATED / bloqueo por operación pendiente: la operación implicada


@dataclass
class Outcome:
    status: str
    message: str = ""
    operation_id: str = ""
    code: str = ""
    plan: Plan | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ───────────────────────── auditoría durable (write-once, fail-closed) ─────────────────────────
def audit_root() -> Path:
    return Path(config.BASE_DIR) / "human-capital" / "profile-consolidations"


def _op_dir(operation_id: str) -> Path:
    if not OPERATION_ID_RE.match(operation_id or ""):
        raise ConsolidationStepError("INVALID_OPERATION", "Identificador de operación inválido.")
    return audit_root() / operation_id


def _write_audit(path: Path, payload: dict, *, once: bool = True) -> None:
    """Escribe y CONFIRMA en Spaces (fail-closed salvo modo local). ``once``: jamás se sobrescribe un archivo existente con contenido distinto (la auditoría es un registro, no un estado)."""
    if remote_store.read_only_mode():
        raise ConsolidationAuditUnavailable("modo de solo lectura")
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)
    if once and path.exists():
        try:
            if json.loads(path.read_text(encoding="utf-8")) == json.loads(text):
                return
        except (OSError, ValueError):
            pass
        raise ConsolidationAuditUnavailable("el registro de auditoría ya existe con otro contenido")
    try:
        outcome = remote_store.write_through(path, text)
    except OSError:
        raise ConsolidationAuditUnavailable("no se pudo escribir el registro") from None
    if outcome != remote_store.DURABLE_REMOTE and not remote_store.local_only():
        raise ConsolidationAuditUnavailable("registro no confirmado en Spaces")


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_audit(operation_id: str) -> dict:
    """``{"intent":…, "result":…}`` (None si falta). Para la pantalla de operaciones, la recuperación y las pruebas."""
    try:
        base = _op_dir(operation_id)
    except ConsolidationStepError:
        return {"intent": None, "result": None}
    return {"intent": _read_json(base / "intent.json"), "result": _read_json(base / "result.json")}


def _event(operation_id: str, kind: str, **detail) -> None:
    """Rastro de reanudaciones, fallos y reversiones (un archivo por evento, nunca se sobrescribe). Best-effort: la fuente de verdad del estado son los datos y ``intent``/``result``."""
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        _write_audit(_op_dir(operation_id) / "events" / f"{stamp}-{kind}.json", {"schema": SCHEMA, "operation_id": operation_id, "kind": kind, "at": _now(), **detail}, once=False)
    except Exception:                                           # noqa: BLE001
        _logger.warning("human_capital.profile_consolidation_event_failed kind=%s", kind)


# ───────────────────────── plan (solo lectura) ─────────────────────────
def _empty(value) -> bool:
    return value is None or value == "" or value == [] or (isinstance(value, str) and not value.strip())


def _norm(value):
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    if isinstance(value, list):
        return tuple(sorted(_norm(v) for v in value))
    return value


def _relations_of(profile_id: str) -> Relations:
    """Candidaturas, entrevistas y onboarding que referencian al perfil. Cualquier fuente ilegible levanta (el plan lo trata como bloqueo: no se verifica ⇒ no se consolida)."""
    rel = Relations()
    for busqueda in busqueda_repository.list():
        for app in busqueda.get("candidatos", []) or []:
            if app.get("profile_id") == profile_id:
                rel.busquedas.append({"busqueda_id": busqueda.get("busqueda_id"), "puesto": busqueda.get("nombre_puesto"), "application_id": app.get("application_id"), "etapa": app.get("etapa")})
    for entrevista in entrevista_repository.list():
        if entrevista.get("profile_id") == profile_id:
            rel.entrevistas.append({"entrevista_id": entrevista.get("entrevista_id"), "busqueda_id": entrevista.get("busqueda_id"), "fecha": entrevista.get("fecha_entrevista")})
    for onboarding in onboarding_repository.list():
        if onboarding.get("profile_id") == profile_id:
            rel.onboardings.append({"application_id": onboarding.get("application_id"), "busqueda_id": onboarding.get("busqueda_id")})
    return rel


def _digest(primary: dict, duplicate: dict, documents: dict, relations: dict, blockers: list) -> str:
    material = {
        "primary": [primary.get("profile_id"), primary.get("revision", 1)], "duplicate": [duplicate.get("profile_id"), duplicate.get("revision", 1)],
        "documents": {pid: sorted((d.get("document_id"), d.get("item_id")) for d in docs) for pid, docs in documents.items()},
        "relations": {pid: rel.counts() for pid, rel in relations.items()}, "blockers": sorted(b["code"] for b in blockers),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def build_plan(primary_id: str, duplicate_id: str) -> Plan:
    """Todo lo que la persona debe ver antes de confirmar: comparación de atributos (conflictos y datos que se completan), documentos de AMBOS perfiles, relaciones y bloqueos. Solo lectura."""
    primary_id, duplicate_id = (primary_id or "").strip(), (duplicate_id or "").strip()
    if not primary_id or not duplicate_id or primary_id == duplicate_id:
        return Plan(INVALID, blockers=[{"code": "NEED_TWO_PROFILES", "message": "Hay que elegir exactamente dos perfiles distintos."}])
    try:
        primary, duplicate = profile_repository.get(primary_id), profile_repository.get(duplicate_id)
    except profile_repository.ProfileStorageError:
        return Plan(BLOCKED, blockers=[{"code": "SOURCE_ERROR", "message": "No se pudieron leer los perfiles. Reintentá en unos minutos."}])
    for pid, profile in ((primary_id, primary), (duplicate_id, duplicate)):
        if profile is None:
            return Plan(INVALID, blockers=[{"code": "PROFILE_NOT_FOUND", "message": "Uno de los perfiles ya no existe."}])
    if duplicate.get("consolidated_into") == primary_id and (duplicate.get("consolidation") or {}).get("state") == COMPLETED:
        return Plan(ALREADY_CONSOLIDATED, primary=primary, duplicate=duplicate, operation_id=str((duplicate.get("consolidation") or {}).get("operation_id") or ""))
    plan = Plan(READY, primary=primary, duplicate=duplicate)
    for role, profile in (("principal", primary), ("duplicado", duplicate)):
        if profile.get("consolidated_into"):
            plan.status = INVALID
            plan.blockers.append({"code": "ALREADY_CONSOLIDATED_ELSEWHERE", "message": f"El perfil {role} ya fue consolidado en otro perfil."})
        elif profile.get("deleted_at"):
            plan.status = INVALID
            plan.blockers.append({"code": "PROFILE_DELETED", "message": f"El perfil {role} está dado de baja."})
        elif profile_repository.is_locked(profile):
            plan.status = BLOCKED
            plan.operation_id = str((profile.get("consolidation") or {}).get("operation_id") or "")
            plan.blockers.append({"code": "PROFILE_LOCKED", "message": f"El perfil {role} está en una consolidación pendiente: reanudala o revertila antes de iniciar otra."})
    if plan.status != READY:
        return plan
    try:
        plan.documents = {pid: sorted(profile_document_repository.list_for_profile(pid), key=lambda d: (str(d.get("uploaded_at") or ""), str(d.get("document_id") or "")))
                          for pid in (primary_id, duplicate_id)}
        plan.relations = {pid: _relations_of(pid) for pid in (primary_id, duplicate_id)}
    except (profile_document_repository.ProfileDocumentStorageError, busqueda_repository.BusquedaStorageError, OSError):
        plan.status = BLOCKED
        plan.blockers.append({"code": "RELATIONS_UNVERIFIABLE", "message": "No se pudieron verificar los documentos y relaciones del perfil duplicado: no se consolida."})
        return plan
    except Exception:                                           # noqa: BLE001 — cualquier otro repositorio de relaciones ilegible: nunca se asume «sin relaciones»
        plan.status = BLOCKED
        plan.blockers.append({"code": "RELATIONS_UNVERIFIABLE", "message": "No se pudieron verificar las relaciones del perfil duplicado: no se consolida."})
        return plan
    dup_rel = plan.relations[duplicate_id]
    if dup_rel.total:
        plan.status = BLOCKED
        parts = [f"{n} {label}" for n, label in ((len(dup_rel.busquedas), "candidatura(s)"), (len(dup_rel.entrevistas), "entrevista(s)"), (len(dup_rel.onboardings), "onboarding(s)")) if n]
        plan.blockers.append({"code": "DUPLICATE_HAS_RELATIONS", "message": "El perfil duplicado tiene " + ", ".join(parts) + ". Esas relaciones no se migran por supuestos: elegí el otro perfil como duplicado o resolvelas antes."})
    for name in MERGE_FIELDS:
        a, b = primary.get(name), duplicate.get(name)
        if _empty(a) and not _empty(b):
            plan.fills.append({"field": name, "label": FIELD_LABELS.get(name, name), "value": b})
        elif not _empty(a) and not _empty(b) and _norm(a) != _norm(b):
            plan.conflicts.append({"field": name, "label": FIELD_LABELS.get(name, name), "values": {primary_id: a, duplicate_id: b}})
    plan.digest = _digest(primary, duplicate, plan.documents, plan.relations, plan.blockers)
    return plan


def _merged_fields(plan: Plan, resolutions: dict) -> dict:
    duplicate_id = plan.duplicate["profile_id"]
    merged: dict = {}                                             # solo lo que CAMBIA en el principal: el resto de sus campos no se toca
    for fill in plan.fills:
        merged[fill["field"]] = fill["value"]
    for conflict in plan.conflicts:
        if resolutions.get(conflict["field"]) == duplicate_id:
            merged[conflict["field"]] = plan.duplicate.get(conflict["field"])
    return merged


def validate_resolutions(plan: Plan, resolutions: dict) -> str | None:
    """Cada conflicto exige una elección explícita (uno de los dos perfiles); no se admiten claves ajenas."""
    allowed = {plan.primary["profile_id"], plan.duplicate["profile_id"]}
    fields = {c["field"] for c in plan.conflicts}
    for name in fields:
        if (resolutions or {}).get(name) not in allowed:
            return f"Falta resolver el campo «{FIELD_LABELS.get(name, name)}»."
    if set(resolutions or {}) - fields:
        return "Hay resoluciones para campos que no están en conflicto."
    return None


# ───────────────────────── ejecución ─────────────────────────
def _consolidation_mark(profile: dict | None) -> dict:
    mark = (profile or {}).get("consolidation")
    return mark if isinstance(mark, dict) else {}


def _is_mine(profile: dict | None, op: str) -> bool:
    return _consolidation_mark(profile).get("operation_id") == op


def _get(profile_id: str) -> dict:
    profile = profile_repository.get(profile_id)
    if profile is None:
        raise ConsolidationStepError("PROFILE_MISSING", "Un perfil de la consolidación ya no existe.")
    return profile


def _update(profile_id: str, fields: dict, op: str) -> dict:
    current = _get(profile_id)
    return profile_repository.update(profile_id, current.get("revision", 1), fields, consolidation_op=op)


def _lock(profile_id: str, role: str, op: str, pre_revision: int) -> None:
    current = _get(profile_id)
    if _is_mine(current, op):
        return
    if profile_repository.is_locked(current):
        raise ConsolidationStepError("LOCKED_BY_OTHER", "El perfil está en otra consolidación.")
    if current.get("revision", 1) != pre_revision:
        raise ConsolidationStepError("PROFILE_CHANGED", "El perfil cambió desde que se armó el plan.")
    profile_repository.update(profile_id, pre_revision, {"consolidation": {"state": profile_repository.CONSOLIDATION_IN_PROGRESS, "operation_id": op, "role": role, "started_at": _now()}}, consolidation_op=op)


def _documents_done(intent: dict) -> bool:
    for document_id, grant in intent["documents"].items():
        doc = profile_document_repository.get(document_id)
        if doc is None or doc.get("profile_id") != grant["to"] or (doc.get("consolidation") or {}).get("operation_id") != intent["operation_id"]:
            return False
    return True


def _run(intent: dict) -> Outcome:
    """Ejecuta los pasos que falten (cada uno comprueba su propio estado). Cualquier fallo deja la operación PENDIENTE y recuperable; nunca se informa éxito parcial."""
    op, primary_id, duplicate_id = intent["operation_id"], intent["primary_id"], intent["duplicate_id"]
    step = "start"
    try:
        step = "lock_duplicate"
        _lock(duplicate_id, "duplicate", op, intent["pre"]["duplicate"].get("revision", 1))
        step = "lock_primary"
        _lock(primary_id, "primary", op, intent["pre"]["primary"].get("revision", 1))
        step = "documents"
        authorized = {did: {"from": g["from"], "to": g["to"]} for did, g in intent["documents"].items()}
        for document_id in intent["documents"]:
            profile_document_repository.reassign_for_consolidation(document_id, operation_id=op, authorized=authorized)
        step = "finalize_duplicate"
        duplicate = _get(duplicate_id)
        if not (_is_mine(duplicate, op) and _consolidation_mark(duplicate).get("state") == COMPLETED):
            _update(duplicate_id, {"deleted_at": intent["started_at"], "deleted_by": intent["actor"], "consolidated_into": primary_id,
                                   "consolidation": {"state": COMPLETED, "operation_id": op, "role": "duplicate", "finished_at": _now()}}, op)
        step = "finalize_primary"
        primary = _get(primary_id)
        if not (_is_mine(primary, op) and _consolidation_mark(primary).get("state") == COMPLETED):
            history = list(intent["pre"]["primary"].get("consolidated_from") or []) + [{"profile_id": duplicate_id, "operation_id": op, "at": intent["started_at"]}]
            _update(primary_id, {**intent["merged_fields"], "consolidated_from": history,
                                 "consolidation": {"state": COMPLETED, "operation_id": op, "role": "primary", "finished_at": _now()}}, op)
        step = "result"
        _write_audit(_op_dir(op) / "result.json", {"schema": SCHEMA, "operation_id": op, "outcome": COMPLETED, "finished_at": _now(), "primary_id": primary_id, "duplicate_id": duplicate_id,
                                                  "documents_moved": sorted(intent["documents"])})
    except Exception as exc:                                    # noqa: BLE001 — recuperable: la operación queda PENDIENTE con ambos perfiles bloqueados
        code = getattr(exc, "code", type(exc).__name__)
        _event(op, "step_failed", step=step, code=code)
        _logger.warning("human_capital.profile_consolidation_pending op=%s step=%s code=%s", op, step, code)
        return Outcome(PENDING, f"La consolidación quedó PENDIENTE en el paso «{step}» ({code}). Los dos perfiles permanecen ocultos y bloqueados hasta que se reanude o se revierta.", op, code)
    return Outcome(COMPLETED, "Los dos perfiles ahora son una única identidad activa; los CVs de ambos están en la ficha del perfil principal.", op)


def execute(primary_id: str, duplicate_id: str, *, resolutions: dict, plan_digest: str, actor: str, confirmed: bool) -> Outcome:
    """Consolida ``duplicate_id`` en ``primary_id``. Requiere confirmación explícita, el digest del plan que la persona vio y una elección para cada conflicto."""
    actor = (actor or "").strip()
    if not actor:
        return Outcome(INVALID, "Falta el usuario que confirma la operación.", code="ACTOR_REQUIRED")
    if not confirmed:
        return Outcome(INVALID, "Hay que confirmar explícitamente la consolidación.", code="CONFIRMATION_REQUIRED")
    with _EXEC_LOCK:
        plan = build_plan(primary_id, duplicate_id)
        if plan.status == ALREADY_CONSOLIDATED:
            return Outcome(ALREADY_CONSOLIDATED, "Estos perfiles ya estaban consolidados: no se hizo nada.", plan.operation_id, plan=plan)
        if plan.status != READY:
            return Outcome(plan.status, " ".join(b["message"] for b in plan.blockers), plan.operation_id, plan.blockers[0]["code"] if plan.blockers else plan.status, plan)
        if plan.digest != (plan_digest or ""):
            return Outcome(STALE_PLAN, "Los perfiles, documentos o relaciones cambiaron desde que se armó el plan. Revisá la comparación y confirmá de nuevo.", code=STALE_PLAN, plan=plan)
        problem = validate_resolutions(plan, resolutions or {})
        if problem:
            return Outcome(INVALID, problem, code="UNRESOLVED_CONFLICT", plan=plan)
        op = uuid.uuid4().hex
        primary, duplicate = plan.primary, plan.duplicate
        merged = _merged_fields(plan, resolutions or {})
        intent = {
            "schema": SCHEMA, "operation_id": op, "kind": "profile_consolidation", "started_at": _now(), "actor": actor, "primary_id": primary_id, "duplicate_id": duplicate_id,
            "plan_digest": plan.digest, "resolutions": dict(resolutions or {}), "fills": [f["field"] for f in plan.fills], "merged_fields": merged,
            "pre": {"primary": primary, "duplicate": duplicate},
            "documents": {d["document_id"]: {"from": duplicate_id, "to": primary_id, "pre_image": d} for d in plan.documents.get(duplicate_id, [])},
            "counts": {"primary_documents": len(plan.documents.get(primary_id, [])), "duplicate_documents": len(plan.documents.get(duplicate_id, []))},
        }
        try:
            _write_audit(_op_dir(op) / "intent.json", intent)
        except ConsolidationAuditUnavailable:
            return Outcome(AUDIT_UNAVAILABLE, "No se pudo registrar la auditoría de la operación en forma durable: no se modificó nada.", code=AUDIT_UNAVAILABLE, plan=plan)
        outcome = _run(intent)
        outcome.plan = plan
        return outcome


def resume(operation_id: str, *, actor: str) -> Outcome:
    """Reanuda una consolidación pendiente (idempotente: solo ejecuta lo que falta)."""
    with _EXEC_LOCK:
        audit = read_audit(operation_id)
        intent, result = audit["intent"], audit["result"]
        if intent is None:
            return Outcome(INVALID, "No existe esa operación.", operation_id, "UNKNOWN_OPERATION")
        if result is not None:
            return Outcome(result.get("outcome", COMPLETED), "La operación ya estaba cerrada.", operation_id, code="ALREADY_CLOSED")
        _event(operation_id, "resume_requested", actor=actor)
        outcome = _run(intent)
        return outcome


def abort(operation_id: str, *, actor: str) -> Outcome:
    """Revierte una consolidación PENDIENTE: devuelve los documentos y ambos perfiles a su imagen previa registrada en la intención. Una consolidación COMPLETADA no se revierte desde acá."""
    with _EXEC_LOCK:
        audit = read_audit(operation_id)
        intent, result = audit["intent"], audit["result"]
        if intent is None:
            return Outcome(INVALID, "No existe esa operación.", operation_id, "UNKNOWN_OPERATION")
        if result is not None:
            if result.get("outcome") == ABORTED:
                return Outcome(ABORTED, "La operación ya estaba revertida.", operation_id, code="ALREADY_CLOSED")
            return Outcome(INVALID, "La consolidación ya se completó: no se revierte desde acá.", operation_id, "ALREADY_COMPLETED")
        _event(operation_id, "abort_requested", actor=actor)
        op, primary_id, duplicate_id = intent["operation_id"], intent["primary_id"], intent["duplicate_id"]
        step = "documents"
        try:
            authorized = {did: {"from": g["from"], "to": g["to"]} for did, g in intent["documents"].items()}
            for document_id, grant in intent["documents"].items():
                profile_document_repository.reassign_for_consolidation(document_id, operation_id=op, authorized=authorized, restore=grant["pre_image"])
            step = "restore_duplicate"
            duplicate = _get(duplicate_id)
            if _is_mine(duplicate, op):
                pre = intent["pre"]["duplicate"]
                _update(duplicate_id, {k: pre.get(k) for k in _PROFILE_KEYS_TOUCHED_ON_DUPLICATE}, op)
            step = "restore_primary"
            primary = _get(primary_id)
            if _is_mine(primary, op):
                pre = intent["pre"]["primary"]
                _update(primary_id, {**{k: pre.get(k) for k in MERGE_FIELDS}, "consolidated_from": pre.get("consolidated_from"), "consolidation": pre.get("consolidation")}, op)
            step = "result"
            _write_audit(_op_dir(op) / "result.json", {"schema": SCHEMA, "operation_id": op, "outcome": ABORTED, "finished_at": _now(), "primary_id": primary_id, "duplicate_id": duplicate_id, "by": actor})
        except Exception as exc:                                # noqa: BLE001
            code = getattr(exc, "code", type(exc).__name__)
            _event(op, "abort_failed", step=step, code=code)
            return Outcome(PENDING, f"No se pudo completar la reversión (paso «{step}», {code}). Los perfiles siguen bloqueados: reintentá.", op, code)
        return Outcome(ABORTED, "La consolidación se revirtió: ambos perfiles y sus documentos volvieron a su estado anterior.", op)


# ───────────────────────── operaciones (pantalla de recuperación) ─────────────────────────
def list_operations() -> list[dict]:
    """Operaciones registradas, las pendientes primero. Lectura fail-soft del directorio de auditoría."""
    root = audit_root()
    out: list[dict] = []
    try:
        entries = sorted(root.iterdir()) if root.is_dir() else []
    except OSError:
        return out
    for entry in entries:
        if not entry.is_dir() or not OPERATION_ID_RE.match(entry.name):
            continue
        intent, result = _read_json(entry / "intent.json"), _read_json(entry / "result.json")
        if intent is None:
            continue
        out.append({"operation_id": entry.name, "started_at": intent.get("started_at"), "actor": intent.get("actor"), "primary_id": intent.get("primary_id"), "duplicate_id": intent.get("duplicate_id"),
                    "status": (result or {}).get("outcome") or PENDING, "documents": len(intent.get("documents") or {}),
                    "primary_name": _display_name(((intent.get("pre") or {}).get("primary"))), "duplicate_name": _display_name(((intent.get("pre") or {}).get("duplicate")))})
    out.sort(key=lambda o: (o["status"] != PENDING, str(o["started_at"] or "")), reverse=False)
    return out


def pending_count() -> int:
    return sum(1 for o in list_operations() if o["status"] == PENDING)


def _display_name(profile: dict | None) -> str:
    profile = profile or {}
    return " ".join(str(profile.get(k) or "").strip() for k in ("first_name", "last_name")).strip()
