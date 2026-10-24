"""cv_history_import_exec.py — EJECUCIÓN de la importación histórica de CVs (incremento I3.2): crea/vincula perfiles en el Banco de CCHH, sube los CV a la carpeta administrada de CVs y gestiona las excepciones.

Parte del PLAN persistido de I3.1 (`import-plan.json`): no repite el análisis. Reutiliza los servicios actuales respetando sus contratos: `human_capital_service.validate_and_shape_fields`/`find_duplicate`/`create_profile_for_import`
(alta con id preasignado, solo-crea), `profile_document_service` (carpeta por candidato, subida, metadata) y el manifiesto protegido. Escribe SOLO en el Banco y en la carpeta administrada de CVs (archivos y carpetas NUEVOS); el origen
histórico de SharePoint se lee y nunca se modifica ni se borra.

CONFIRMACIÓN: nada se escribe hasta que el owner confirma con la frase `IMPORTAR-<run_id>` Y el alcance que vio (revisión del plan, perfiles y documentos): si el plan cambió desde entonces, no se inicia. El inicio valida además el
permiso EFECTIVO de escritura sobre la carpeta de CVs con el mismo token (sonda con nombre ÚNICO: PUT de un archivo diminuto y borrado; si el borrado falla se informa el archivo residual y se BLOQUEA el inicio hasta que ya no esté).
Desde ese momento el plan queda CONGELADO (no se puede regenerar).

IDENTIDADES DETERMINÍSTICAS (base de la idempotencia): `profile_id = uuid5(lote, grupo)` y `document_id = uuid5(profile_id, content_key)`. El nombre remoto de un archivo es `<document_id>__<nombre>`, así que repetir una subida apunta al MISMO
archivo (nunca crea otro) y repetir un alta de perfil es un reintento idempotente (`create_with_explicit_id` no sobrescribe).

LIBRO DE EJECUCIÓN (`ledger/<content_key>.json`, durable y SIN datos personales): `content_key → profile_id → document_id`, estado, `item_id` del archivo en SharePoint y sha256. Sobrevive al descarte de los datos temporales.
Estados: PLANNED → PROFILE_READY → UPLOADING → DONE; o BLOCKED (el Banco cambió: no se escribe), FAILED, DISMISSED.

REVALIDACIÓN: cada decisión se vuelve a validar contra el Banco AL EJECUTARSE (email ya existente, posible duplicado por nombre o celular, perfil destino cambiado). Si ya no es seguro, el contenido pasa a BLOCKED con el motivo y queda
para el owner: nunca se sobrescribe ni se fusiona un perfil existente.

SUBIDAS INCIERTAS: se concilian contra el estado REAL antes de reintentar, en este orden: (1) metadata del documento ya persistida para ese `document_id`; (2) el archivo remoto en SharePoint, por su ruta determinística, verificado por CONTENIDO
(hash comparable del origen o, si no lo hay, sha256 del archivo descargado) —nunca solo por nombre y tamaño—; recién si no existe se descarga el origen (verificando su sha256 contra el del análisis) y se sube.

EXCEPCIONES (los pendientes del plan y los bloqueados al ejecutar): completar identidad (alta con nombre y apellido reales; nunca un nombre ficticio), vincular a un perfil existente (sin modificarlo), descartar, reintentar. Sin fusiones automáticas."""
from __future__ import annotations

import hashlib
import time
import urllib.error
import uuid
from collections import Counter, defaultdict

import config

from webapp.services import (cv_extract, cv_history_import as planning, cv_history_manifest as manifest, cv_history_pilot as pilot, cv_reconcile, human_capital_service, profile_document_repository,
                             profile_document_service as docsvc, profile_document_validation, profile_repository)

NAMESPACE = uuid.UUID("5d3f2c1e-8a47-4b9e-9c1d-0e6b7a2f4d11")
TAG_HISTORICO, TAG_REVIEW = "Histórico SharePoint", "Revisar identidad"
BATCH_DEFAULT_UNITS, BATCH_MAX_UNITS = 20, 100
MAX_ATTEMPTS = 3
STREAK_LIMIT = 3                                                                  # errores reintentables seguidos: se corta la tanda (probablemente un problema general, no del documento)

PLANNED, PROFILE_READY, UPLOADING, DONE, BLOCKED, FAILED, DISMISSED, REOPENED = "PLANNED", "PROFILE_READY", "UPLOADING", "DONE", "BLOCKED", "FAILED", "DISMISSED", "REOPENED"
FINAL = {DONE, BLOCKED, FAILED, DISMISSED}
MANUAL_CREATE, MANUAL_LINK = "MANUAL_CREATE", "MANUAL_LINK"
DISMISS_REASONS = ("NOT_A_CV", "NOT_A_CANDIDATE", "OTHER")
_ACTION_RANK = {planning.CREATE: 0, MANUAL_CREATE: 0, planning.LINK: 1, MANUAL_LINK: 1, planning.ATTACH: 2}


class ExecutionError(ValueError):
    """Error de uso o de estado, con mensaje seguro para el owner."""


class _Stop(Exception):
    """Detiene la tanda (no es un error del documento): permiso, token vencido, persistencia."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def confirm_phrase(run_id: str) -> str:
    return f"IMPORTAR-{run_id}"


def profile_id_for(run_id: str, cluster: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"cchh-import-profile:{run_id}:{cluster}"))


def manual_profile_id(run_id: str, key: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"cchh-import-manual:{run_id}:{key}"))


def document_id_for(profile_id: str, key: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"cchh-import-document:{profile_id}:{key}"))


# ═══════════════════ inicio (confirmación + permiso de escritura) ═══════════════════
def _import_run(run_id: str) -> dict:
    run = manifest.get_run(run_id)
    if run.get("kind") != pilot.KIND_IMPORT:
        raise ExecutionError("No es un lote de importación.")
    return run


def _executable_decisions(plan: dict) -> dict[str, dict]:
    return {k: d for k, d in (plan or {}).get("decisions", {}).items() if d["action"] in (planning.CREATE, planning.ATTACH, planning.LINK)}


def plan_scope(plan: dict) -> dict:
    """Alcance EFECTIVO de un plan: lo que la confirmación del owner debe mostrar y lo que el inicio vuelve a comprobar."""
    return {"revision": plan["revision"], "profiles": len(plan.get("clusters") or {}), "documents": len(_executable_decisions(plan))}


def _clear_probe_residue(run: dict, token: str) -> None:
    """Un archivo residual de una sonda anterior BLOQUEA el inicio mientras siga en la carpeta de CVs; si el owner ya lo eliminó, se limpia el aviso."""
    names = list(run.get("probe_residue") or [])
    if not names:
        return
    try:
        still = [n for n in names if docsvc.find_remote_document(config.HC_CV_ROOT_ITEM_ID, n, token) is not None]
    except docsvc.ProfileDocumentPermissionDeniedError:
        raise ExecutionError("Sin permiso para comprobar la carpeta de CVs con tu sesión actual: autorizá la escritura y reintentá. No se escribió nada.") from None
    except docsvc.ProfileDocumentGraphSourceError:
        raise ExecutionError("SharePoint no respondió al comprobar los archivos residuales de la sonda. No se escribió nada: reintentá.") from None
    if still:
        raise ExecutionError(f"Quedó en la carpeta de CVs un archivo residual de una sonda anterior: {', '.join(still)}. Eliminalo en SharePoint y reintentá. No se inició la importación.")
    manifest.update_run(run["run_id"], lambda d: d.pop("probe_residue", None))


def _run_write_probe(run_id: str, token: str) -> dict:
    """Sonda de escritura con nombre único por ejecución. Si no se puede borrar, se registra el archivo residual y se bloquea (nunca se reutiliza ese nombre)."""
    try:
        check = docsvc.probe_write_access(token, run_id)
    except docsvc.ProfileDocumentPermissionDeniedError:
        raise ExecutionError("Sin permiso de escritura sobre la carpeta de CVs con tu sesión actual: autorizá la escritura y reintentá. No se escribió nada.") from None
    except docsvc.ProfileDocumentConfigIncompleteError:
        raise ExecutionError("La integración de documentos con SharePoint no está configurada en este entorno. No se escribió nada.") from None
    except docsvc.ProfileDocumentGraphSourceError:
        raise ExecutionError("SharePoint no respondió al validar el permiso de escritura. No se escribió nada: reintentá.") from None
    if not check.get("cleaned"):
        manifest.update_run(run_id, lambda d: d.__setitem__("probe_residue", sorted({*(d.get("probe_residue") or []), check["file"]})))
        raise ExecutionError(f"El permiso de escritura es válido, pero no se pudo borrar el archivo de sonda «{check['file']}» de la carpeta de CVs. Eliminalo en SharePoint y reintentá: no se inició la importación.")
    return check


def start_execution(run_id: str, token: str, *, actor: str, confirm: str, expected: dict | None = None) -> dict:
    """Comienza la ejecución: exige la frase de confirmación, el ALCANCE que el owner vio (`expected`: revisión del plan, perfiles y documentos; si el plan cambió desde entonces no se inicia), el plan y el análisis
    completos y el permiso EFECTIVO de escritura sobre la carpeta de CVs. Idempotente: si ya comenzó devuelve el lote sin repetir nada."""
    run = _import_run(run_id)
    if run.get("execution"):
        return run
    if (confirm or "").strip() != confirm_phrase(run_id):
        raise ExecutionError("Falta la confirmación explícita para comenzar a escribir en el Banco y en la carpeta de CVs.")
    if (run.get("staging") or {}).get("state") != "PRESENT":
        raise ExecutionError("Los datos temporales del lote ya no están disponibles: no se puede ejecutar el plan.")
    plan = manifest.get_plan(run_id)
    if not plan:
        raise ExecutionError("Primero generá el plan.")
    if pilot._pending(run_id):
        raise ExecutionError("El análisis no está completo.")
    if not _executable_decisions(plan):
        raise ExecutionError("El plan no tiene contenidos para importar.")
    actual = plan_scope(plan)
    try:
        confirmed = {k: int((expected or {})[k]) for k in ("revision", "profiles", "documents")}
    except (KeyError, TypeError, ValueError):
        raise ExecutionError("Falta el alcance del plan que confirmaste: recargá la página y revisá los conteos.") from None
    if confirmed != actual:
        raise ExecutionError(f"El plan cambió desde que lo revisaste (confirmaste revisión {confirmed['revision']}: {confirmed['profiles']} perfiles y {confirmed['documents']} documentos; ahora es la revisión {actual['revision']}: "
                             f"{actual['profiles']} perfiles y {actual['documents']} documentos). Recargá la página, revisá los conteos y confirmá de nuevo. No se escribió nada.")
    _clear_probe_residue(run, token)
    check = _run_write_probe(run_id, token)
    execution = {"state": "RUNNING", "started_at": manifest.now_iso(), "started_by": actor, "plan_revision": plan["revision"], "write_check": {**check, "at": manifest.now_iso()}, "last_stop": None}
    return manifest.update_run(run_id, lambda d: d.update({"execution": execution}))


# ═══════════════════ Banco: índices y revalidación ═══════════════════
class _Banco:
    """Índices en memoria del Banco para revalidar cada decisión sin releer los perfiles por documento; se actualizan al crear."""

    def __init__(self, profiles: list[dict]):
        self.by_email: dict[str, list[dict]] = defaultdict(list)
        self.by_tokens: dict[frozenset, list[dict]] = defaultdict(list)
        self.by_phone: dict[str, list[dict]] = defaultdict(list)
        for p in profiles:
            self.add(p)

    def add(self, p: dict) -> None:
        email = cv_reconcile.norm_email(p.get("email"))
        if email:
            self.by_email[email].append(p)
        self.by_tokens[cv_reconcile.token_key(p.get("first_name"), p.get("last_name"))].append(p)
        tail = cv_reconcile.phone_tail(p.get("phone"))
        if tail:
            self.by_phone[tail].append(p)

    def email_matches(self, email: str, exclude: str) -> list[dict]:
        return [p for p in self.by_email.get(cv_reconcile.norm_email(email), []) if p.get("profile_id") != exclude]

    def name_matches(self, first: str, last: str, phone: str | None, exclude: str) -> list[str]:
        near = {id(p): p for p in self.by_tokens.get(cv_reconcile.token_key(first, last), []) + self.by_phone.get(cv_reconcile.phone_tail(phone), []) if p.get("profile_id") != exclude}
        person = {"first_name": first, "last_name": last, "phone": phone}
        return sorted(m["profile_id"] for m in cv_reconcile.reconcile_with_profiles(person, list(near.values())) if m["strength"] in (cv_reconcile.STRONG, cv_reconcile.PROBABLE))


# ═══════════════════ libro de ejecución ═══════════════════
def _new_entry(run_id: str, key: str, decision: dict, plan: dict) -> dict:
    action = decision["action"]
    if action == planning.LINK:
        profile_id = decision["link_profile_id"]
    else:
        profile_id = profile_id_for(run_id, decision["cluster"])
    return {"source": "plan", "action": action, "cluster": decision.get("cluster"), "profile_id": profile_id, "document_id": document_id_for(profile_id, key), "state": PLANNED, "attempts": 0,
            "profile_created": False, "reason": None, "last_error": None, "history": []}


def _save(run_id: str, key: str, entry: dict, event: str | None = None) -> dict:
    if event:
        entry["history"] = [*entry.get("history", []), {"at": manifest.now_iso(), "event": event, "state": entry["state"], **({"reason": entry["reason"]} if entry.get("reason") else {})}][-20:]
    saved = manifest.put_ledger(run_id, key, entry)
    if entry["state"] in FINAL:
        manifest.append_audit(run_id, key, "IMPORT_" + entry["state"], code=entry.get("reason"), action=entry.get("action"))
    return saved


def _terminal(run_id: str, key: str, entry: dict, state: str, reason: str | None) -> str:
    entry.update({"state": state, "reason": reason})
    _save(run_id, key, entry, "FINAL")
    return state


def _effective(entry: dict | None) -> str | None:
    return None if entry is None or entry.get("state") == REOPENED else entry["state"]


def _pending_units(plan: dict, ledgers: dict[str, dict]) -> list[tuple[str, str]]:
    units = []
    for key, d in _executable_decisions(plan).items():
        if _effective(ledgers.get(key)) not in FINAL:
            units.append((key, d["action"]))
    for key, e in ledgers.items():
        if e.get("source") == "manual" and _effective(e) not in FINAL and key not in dict(units):
            units.append((key, e["action"]))
    return sorted(units, key=lambda u: (_ACTION_RANK.get(u[1], 3), u[0]))


# ═══════════════════ perfil ═══════════════════
def _specialties(run_id: str, keys: list[str]) -> str | None:
    seen: list[str] = []
    for k in keys:
        g = manifest.get_group(run_id, k) or {}
        for s in g.get("specialties") or []:
            if s not in seen:
                seen.append(s)
    return ", ".join(seen) or None


def _profile_fields(raw: dict) -> dict:
    return human_capital_service.validate_and_shape_fields(raw)


def _origin(run: dict, **extra) -> dict:
    return {"kind": "sharepoint_historical", "run_id": run["run_id"], "imported_at": manifest.now_iso(), **extra}


def _create_planned_profile(run: dict, key: str, entry: dict, plan: dict, banco: _Banco) -> str | None:
    """Alta (CREATE del plan) con revalidación. Devuelve None si el perfil queda listo, o el motivo de bloqueo."""
    run_id, pid = run["run_id"], entry["profile_id"]
    existing = profile_repository.get(pid)
    if existing is not None:
        return None if (existing.get("import_origin") or {}).get("run_id") == run_id else "PROFILE_ID_CONFLICT"
    group = manifest.get_group(run_id, key)
    extraction = (group or {}).get("extraction") or {}
    proposal = pilot._proposal(extraction)
    if not group or not proposal or not extraction.get("email") or not (proposal.get("first_name") and proposal.get("last_name")):
        return "STAGING_UNAVAILABLE"
    if banco.email_matches(extraction["email"], pid):
        return "EMAIL_NOW_EXISTS"
    if banco.name_matches(proposal["first_name"], proposal["last_name"], extraction.get("phone"), pid):
        return "POSSIBLE_DUPLICATE_NOW"
    cluster = plan["clusters"][entry["cluster"]]
    warnings = cluster.get("warnings") or []
    shaped = _profile_fields({"first_name": proposal["first_name"], "last_name": proposal["last_name"], "email": extraction["email"], "phone": extraction.get("phone"),
                              "profile_skills": _specialties(run_id, cluster["members"]), "tags": ", ".join([TAG_HISTORICO] + ([TAG_REVIEW] if warnings else []))})
    profile = {**shaped, "import_origin": _origin(run, cluster=entry["cluster"], leader_content_key=key, plan_revision=run["execution"]["plan_revision"], warnings=warnings, review_identity=bool(warnings))}
    created, _new = human_capital_service.create_profile_for_import(pid, profile)
    banco.add(created)
    entry["profile_created"] = True
    return None


def _revalidate_link(entry: dict, group: dict) -> str | None:
    target = profile_repository.get(entry["profile_id"])
    if target is None or target.get("deleted_at"):
        return "LINK_TARGET_MISSING"
    if entry["source"] == "plan" and cv_reconcile.norm_email(target.get("email")) != cv_reconcile.norm_email(((group or {}).get("extraction") or {}).get("email")):
        return "LINK_TARGET_CHANGED"
    return None


# ═══════════════════ documento: conciliación real + subida ═══════════════════
def _is_401(exc: BaseException) -> bool:
    seen = 0
    while exc is not None and seen < 5:
        if isinstance(exc, urllib.error.HTTPError) and exc.code == 401:
            return True
        exc, seen = exc.__cause__ or exc.__context__, seen + 1
    return False


def _verify_remote(remote: dict, group: dict, token: str) -> bool:
    """¿El archivo que ya está en SharePoint ES este contenido? Por TAMAÑO y por CONTENIDO: el hash comparable del origen (mismo algoritmo) o, si Graph no informa uno comparable, el sha256 del archivo remoto descargado."""
    if int(remote.get("size") or -1) != int(group["size"]):
        return False
    source_hash = group.get("hash") or {}
    remote_hashes = (remote.get("file") or {}).get("hashes") or {}
    if source_hash.get("algo") and source_hash.get("value") and remote_hashes.get(source_hash["algo"]):
        return remote_hashes[source_hash["algo"]] == source_hash["value"]
    return hashlib.sha256(docsvc.read_remote_document_bytes(remote["id"], token)).hexdigest() == group["sha256"]


def _upload_document(run: dict, key: str, entry: dict, group: dict, token: str, actor: str, sleep) -> str:
    run_id, pid, doc_id = run["run_id"], entry["profile_id"], entry["document_id"]
    existing = profile_document_repository.get(doc_id)
    if existing is not None:                                                        # (1) la metadata de ESTE documento ya está persistida: nada que subir
        if existing.get("profile_id") != pid:
            return _terminal(run_id, key, entry, BLOCKED, "DOC_ID_CONFLICT")
        entry.update({"item_id": existing.get("item_id"), "folder_item_id": existing.get("folder_item_id")})
        return _terminal(run_id, key, entry, DONE, None)
    profile = profile_repository.get(pid) or {}
    entry.update({"state": UPLOADING, "attempts": int(entry.get("attempts", 0)) + 1, "sha256": group.get("sha256")})
    _save(run_id, key, entry)
    rep = group["representative"]
    folder = docsvc.ensure_profile_folder(pid, token, first_name=profile.get("first_name"), last_name=profile.get("last_name"))
    remote_name = docsvc.build_remote_filename(doc_id, rep["name"])
    origin = {"import_origin": {"kind": "sharepoint_historical", "run_id": run_id, "content_key": key, "sha256": group.get("sha256"), "specialties": group.get("specialties") or []}}
    remote = docsvc.find_remote_document(folder, remote_name, token)                # (2) conciliación con el estado REAL de SharePoint
    if remote is not None:
        if not _verify_remote(remote, group, token):
            return _terminal(run_id, key, entry, FAILED, "REMOTE_CONTENT_MISMATCH")   # existe algo distinto en esa ruta: no se pisa
        docsvc.adopt_remote_document(document_id=doc_id, profile_id=pid, drive_item=remote, filename=rep["name"], content_type=_content_type(rep["name"]),
                                     size_bytes=int(group["size"]), uploaded_by=actor, folder_item_id=folder, extra_fields=origin)
        entry.update({"item_id": remote.get("id"), "folder_item_id": folder, "reconciled": True})
        return _terminal(run_id, key, entry, DONE, None)
    data = pilot._download(rep["item_id"], rep["drive_id"], token, sleep)           # (3) no existe: se sube desde el origen (solo lectura), verificando su contenido contra el análisis
    if hashlib.sha256(data).hexdigest() != group.get("sha256") or len(data) != int(group["size"]):
        return _terminal(run_id, key, entry, FAILED, "SOURCE_CHANGED")
    document = docsvc.upload_document(profile_id=pid, filename=rep["name"], data=data, access_token=token, uploaded_by=actor, first_name=profile.get("first_name"), last_name=profile.get("last_name"),
                                      document_id=doc_id, extra_fields=origin)
    entry.update({"item_id": document.get("item_id"), "folder_item_id": document.get("folder_item_id") or folder})
    return _terminal(run_id, key, entry, DONE, None)


def _content_type(filename: str) -> str:
    return profile_document_validation.ACCEPTED_CONTENT_TYPES[filename.rsplit(".", 1)[-1].lower() if "." in filename else ""]


_RETRYABLE = (docsvc.ProfileDocumentGraphSourceError, docsvc.ProfileDocumentFolderOutcomeUnknownError, docsvc.ProfileDocumentUploadRetryRequired, docsvc.ProfileDocumentCommitRetryRequired,
              profile_repository.ProfileStorageError, profile_repository.ProfileRemoteOutcomeUnknownError, profile_document_repository.ProfileDocumentRemoteOutcomeUnknownError,
              urllib.error.URLError, OSError)


def _run_unit(run: dict, key: str, entry: dict | None, plan: dict, banco: _Banco, ledgers: dict[str, dict], token: str, actor: str, sleep) -> str:
    """Un contenido de punta a punta. Devuelve `DONE`, `BLOCKED`, `FAILED` o `RETRY` (error reintentable); levanta `_Stop` ante permiso/token/persistencia."""
    run_id = run["run_id"]
    decision = plan["decisions"].get(key)
    if entry is None or entry.get("state") == REOPENED:
        if decision is None:
            return FAILED
        entry = _new_entry(run_id, key, decision, plan)
    group = manifest.get_group(run_id, key)
    if group is None or not group.get("extraction"):
        return _terminal(run_id, key, entry, FAILED, "STAGING_UNAVAILABLE")
    try:
        action = entry["action"]
        if action == planning.CREATE:
            blocked = _create_planned_profile(run, key, entry, plan, banco)
        elif action == planning.ATTACH:
            leader_key = plan["clusters"][entry["cluster"]]["leader"]
            leader = ledgers.get(leader_key) or {}
            blocked = None if leader.get("state") in (PROFILE_READY, UPLOADING, DONE) and leader.get("profile_id") == entry["profile_id"] else "LEADER_NOT_IMPORTED"
        elif action in (planning.LINK, MANUAL_LINK):
            blocked = _revalidate_link(entry, group)
        else:                                                                       # MANUAL_CREATE: el perfil se creó al completar la identidad
            blocked = None if profile_repository.get(entry["profile_id"]) else "PROFILE_MISSING"
        if blocked:
            return _terminal(run_id, key, entry, BLOCKED, blocked)
        if entry["state"] == PLANNED:
            entry["state"] = PROFILE_READY
            _save(run_id, key, entry, "PROFILE_READY")
        ledgers[key] = entry
        outcome = _upload_document(run, key, entry, group, token, actor, sleep)
        ledgers[key] = entry
        return outcome
    except docsvc.ProfileDocumentPermissionDeniedError:
        raise _Stop("PERMISSION_DENIED") from None
    except (docsvc.ProfileDocumentConfigIncompleteError, profile_repository.ProfileConfigIncompleteError, profile_document_repository.ProfileDocumentConfigIncompleteError):
        raise _Stop("CONFIG_INCOMPLETE") from None
    except manifest.ManifestDurabilityError:
        raise _Stop("PERSISTENCE_ERROR") from None
    except profile_document_validation.ProfileDocumentValidationError:
        return _terminal(run_id, key, entry, FAILED, "INVALID_DOCUMENT")
    except profile_repository.ProfileIdConflictError:
        return _terminal(run_id, key, entry, BLOCKED, "PROFILE_ID_CONFLICT")
    except profile_document_repository.ProfileDocumentIdConflictError:
        return _terminal(run_id, key, entry, BLOCKED, "DOC_ID_CONFLICT")
    except ValueError:
        return _terminal(run_id, key, entry, FAILED, "INVALID_PROFILE_FIELDS")
    except _RETRYABLE as exc:
        if _is_401(exc):
            entry["attempts"] = max(0, int(entry.get("attempts", 0)) - 1)           # un token vencido no es un intento fallido del documento
            _save(run_id, key, entry)
            raise _Stop("TOKEN_EXPIRED") from None
        entry["last_error"] = type(exc).__name__
        if int(entry.get("attempts", 0)) >= MAX_ATTEMPTS:
            return _terminal(run_id, key, entry, FAILED, "RETRIES_EXHAUSTED")
        _save(run_id, key, entry, "RETRY")
        ledgers[key] = entry
        return "RETRY"


def execute_batch(run_id: str, token: str, *, actor: str, max_units: int | None = None, time_budget: float = pilot.PROCESS_TIME_BUDGET_S, clock=time.monotonic, sleep=time.sleep) -> dict:
    """Una tanda de ejecución del plan congelado. Reanudable e idempotente: lo ya hecho no se repite, lo incierto se concilia contra SharePoint antes de reintentar. Cortes: presupuesto de tiempo, tope de contenidos
    por tanda, racha de errores reintentables, permiso de escritura, token vencido o persistencia no confirmada."""
    run = _import_run(run_id)
    execution = run.get("execution")
    if not execution:
        raise ExecutionError("La importación todavía no comenzó.")
    plan = manifest.get_plan(run_id)
    if not plan or plan.get("revision") != execution["plan_revision"]:
        raise ExecutionError("El plan cambió desde que comenzó la ejecución.")
    max_units = max(1, min(int(max_units), BATCH_MAX_UNITS)) if max_units else BATCH_DEFAULT_UNITS
    started = clock()
    stopped: str | None = None
    if execution.get("state") == "PERMISSION_DENIED":
        try:
            _clear_probe_residue(run, token)
            _run_write_probe(run_id, token)
        except ExecutionError as exc:
            if "Sin permiso" in str(exc):
                return {**_progress(run_id, plan), "processed": 0, "stopped_reason": "PERMISSION_DENIED"}
            raise
    try:
        banco = _Banco(profile_repository.list())
    except profile_repository.ProfileStorageError:
        return {**_progress(run_id, plan), "processed": 0, "stopped_reason": "PROFILES_UNAVAILABLE"}
    ledgers = manifest.list_ledgers(run_id)
    outcomes: Counter = Counter()
    streak = 0
    for key, _action in _pending_units(plan, ledgers):
        if clock() - started >= time_budget:
            stopped = "TIME_BUDGET"
            break
        if sum(outcomes.values()) >= max_units:
            stopped = "BATCH_LIMIT"
            break
        try:
            outcome = _run_unit(run, key, ledgers.get(key), plan, banco, ledgers, token, actor, sleep)
        except _Stop as stop:
            stopped = stop.reason
            break
        outcomes[outcome] += 1
        streak = streak + 1 if outcome == "RETRY" else 0
        if streak >= STREAK_LIMIT:
            stopped = "ERRORS_STREAK"
            break
    progress = _progress(run_id, plan)
    state = "DONE" if not progress["remaining"] else ("PERMISSION_DENIED" if stopped == "PERMISSION_DENIED" else "RUNNING")
    try:
        manifest.update_run(run_id, lambda d: d["execution"].update({"state": state, "last_stop": stopped, "last_batch_at": manifest.now_iso()}))
    except manifest.ManifestError:
        stopped = stopped or "PERSISTENCE_ERROR"
    return {**progress, "processed": sum(outcomes.values()), "done": outcomes[DONE], "blocked": outcomes[BLOCKED], "failed": outcomes[FAILED], "retry": outcomes["RETRY"], "stopped_reason": stopped}


# ═══════════════════ reporte (sin datos personales) ═══════════════════
def _progress(run_id: str, plan: dict) -> dict:
    ledgers = manifest.list_ledgers(run_id)
    return {"remaining": len(_pending_units(plan, ledgers))}


def execution_report(run_id: str) -> dict | None:
    """Progreso y resultado de la ejecución, calculado del LIBRO (sin datos personales; sigue disponible tras el descarte de los datos temporales)."""
    run = manifest.get_run(run_id)
    if run.get("kind") != pilot.KIND_IMPORT:
        return None
    plan = manifest.get_plan(run_id)
    ledgers = manifest.list_ledgers(run_id)
    execution = run.get("execution")
    states = Counter(_effective(e) for e in ledgers.values() if _effective(e))
    created = {e["profile_id"] for e in ledgers.values() if e.get("profile_created") and _effective(e)}
    clusters = (plan or {}).get("clusters", {})
    warned = {e["profile_id"] for e in ledgers.values() if e.get("profile_created") and e.get("action") == planning.CREATE and (clusters.get(e.get("cluster")) or {}).get("warnings")}
    blocked = Counter(e.get("reason") for e in ledgers.values() if _effective(e) == BLOCKED)
    failed = Counter(e.get("reason") for e in ledgers.values() if _effective(e) == FAILED)
    done = [e for e in ledgers.values() if _effective(e) == DONE]
    pending_plan = [k for k, d in (plan or {}).get("decisions", {}).items() if d["action"] == planning.PENDING]
    open_pending = sum(1 for k in pending_plan if _effective(ledgers.get(k)) not in (DONE, DISMISSED))
    open_other = sum(1 for k, e in ledgers.items() if k not in set(pending_plan) and _effective(e) in (BLOCKED, FAILED))
    executable = _executable_decisions(plan)
    return {
        "started": bool(execution), "state": (execution or {}).get("state"), "started_at": (execution or {}).get("started_at"), "plan_revision": (execution or {}).get("plan_revision"),
        "write_check": (execution or {}).get("write_check"), "last_stop": (execution or {}).get("last_stop"), "probe_residue": list(run.get("probe_residue") or []),
        "units": len(executable) + sum(1 for k, e in ledgers.items() if e.get("source") == "manual" and k not in executable),
        "remaining": len(_pending_units(plan, ledgers)) if plan else 0,
        "states": {s: states.get(s, 0) for s in (PLANNED, PROFILE_READY, UPLOADING, DONE, BLOCKED, FAILED, DISMISSED)},
        "documents_uploaded": len(done), "documents_reconciled": sum(1 for e in done if e.get("reconciled")),
        "profiles_created": len(created), "profiles_with_warnings": len(warned),
        "linked_documents": sum(1 for e in done if e["action"] in (planning.LINK, MANUAL_LINK)), "manual_completed": sum(1 for e in done if e["source"] == "manual"),
        "blocked_by_reason": dict(sorted(blocked.items())), "failed_by_reason": dict(sorted(failed.items())),
        "exceptions_open": open_pending + open_other, "dismissed": states.get(DISMISSED, 0),
    }


def discard_blockers(run_id: str) -> dict | None:
    """Qué impide el descarte normal de los datos temporales de una importación en curso: contenidos sin terminar y excepciones abiertas (ambos dependen del área temporal para resolverse). None si no hay bloqueo."""
    run = manifest.get_run(run_id)
    if run.get("kind") != pilot.KIND_IMPORT or not run.get("execution"):
        return None
    report = execution_report(run_id)
    blockers = {"remaining": report["remaining"], "exceptions_open": report["exceptions_open"]}
    return blockers if blockers["remaining"] or blockers["exceptions_open"] else None


def assert_can_discard(run_id: str) -> None:
    """No se descartan los datos temporales mientras la importación (ya iniciada) tenga contenidos sin terminar o excepciones abiertas: sin ellos no se puede completar el alta ni la subida. La eliminación sigue siendo
    explícita (frase de confirmación) y nunca automática; las excepciones se resuelven (completar, vincular o descartar) desde la interfaz."""
    blockers = discard_blockers(run_id)
    if blockers:
        raise pilot.PilotInputError(f"No se pueden eliminar los datos temporales: quedan {blockers['remaining']} contenidos sin terminar y {blockers['exceptions_open']} excepciones abiertas que dependen de ellos. "
                                    "Resolvelas desde «Gestionar excepciones» (completar, vincular o descartar) y volvé a intentarlo.")


# ═══════════════════ excepciones ═══════════════════
def _exception_status(plan_decision: dict, ledger: dict | None) -> str | None:
    state = _effective(ledger)
    if state == DONE:
        manual = (ledger or {}).get("source") == "manual"
        return "resolved" if (plan_decision["action"] == planning.PENDING or manual) else None   # una importación automática terminada no es una excepción; una resuelta a mano sí queda a la vista
    if state == DISMISSED:
        return "dismissed"
    if state in (PLANNED, PROFILE_READY, UPLOADING):
        return "in_progress" if (ledger or {}).get("source") == "manual" or plan_decision["action"] == planning.PENDING else None
    if state in (BLOCKED, FAILED):
        return "blocked" if state == BLOCKED else "error"
    return "open" if plan_decision["action"] == planning.PENDING else None


def _exception_index(plan: dict, ledgers: dict[str, dict]) -> list[dict]:
    rows = []
    for key, d in (plan or {}).get("decisions", {}).items():
        ledger = ledgers.get(key)
        status = _exception_status(d, ledger)
        if status is None:
            continue
        reason = (ledger or {}).get("reason") if status in ("blocked", "error") else (d["reasons"][0] if d["reasons"] else None)
        rows.append({"content_key": key, "status": status, "reason": reason, "plan_action": d["action"], "plan_reasons": d["reasons"], "candidates": d.get("candidates") or []})
    return rows


def exceptions_overview(run_id: str, *, status: str = "open", reason: str | None = None) -> dict:
    """Listado de excepciones filtrado (la PAGINACIÓN la hace el paginador canónico del Hub en el router, después de filtrar). `status`: open (por resolver: abiertas, en curso, bloqueadas y con error) | resolved | dismissed | all. Solo códigos y `content_key`: los datos personales se agregan aparte (`exception_details`)."""
    run = _import_run(run_id)
    plan = manifest.get_plan(run_id)
    ledgers = manifest.list_ledgers(run_id)
    rows = _exception_index(plan, ledgers)
    group_of = {"open": {"open", "in_progress", "blocked", "error"}, "resolved": {"resolved"}, "dismissed": {"dismissed"}, "all": {"open", "in_progress", "blocked", "error", "resolved", "dismissed"}}.get(status, {"open"})
    in_scope = [r for r in rows if r["status"] in group_of]
    by_reason = Counter(r["reason"] or "—" for r in in_scope)
    filtered = [r for r in in_scope if reason in (None, "", r["reason"])]
    return {"rows": filtered, "total": len(filtered), "by_reason": dict(sorted(by_reason.items())),
            "counts": dict(Counter(r["status"] for r in rows)), "staging_present": (run.get("staging") or {}).get("state") == "PRESENT"}


def exception_details(run_id: str, rows: list[dict]) -> list[dict]:
    """Datos para el owner (CONTIENE datos personales): extracción, alternativas y perfiles sugeridos del Banco (candidatos del plan y coincidencias por email/celular/nombre)."""
    profiles = profile_repository.list()
    by_id = {p["profile_id"]: p for p in profiles}
    out = []
    for r in rows:
        group = manifest.get_group(run_id, r["content_key"]) or {}
        e = group.get("extraction") or {}
        proposal = pilot._proposal(e)
        person = {"first_name": (proposal or {}).get("first_name"), "last_name": (proposal or {}).get("last_name"), "email": e.get("email"), "phone": e.get("phone"), "phone_digits": e.get("phone_digits")}
        suggested: dict[str, str] = {pid: "plan" for pid in r["candidates"]}
        for m in cv_reconcile.reconcile_with_profiles(person, profiles):
            suggested.setdefault(m["profile_id"], "+".join(m["signals"]))
        ledger = manifest.get_ledger(run_id, r["content_key"]) or {}
        out.append({**r, "ledger": {"state": ledger.get("state"), "reason": ledger.get("reason"), "profile_id": ledger.get("profile_id"), "action": ledger.get("action")},
                    "available": bool(group), "filename": (group.get("representative") or {}).get("name"), "specialties": group.get("specialties") or [], "email": e.get("email"), "emails": (e.get("candidates") or {}).get("emails") or [],
                    "phone": e.get("phone"), "failure": e.get("failure"), "alternatives": [a["tokens"] for a in (e.get("name_decision") or {}).get("alternatives", [])],
                    "proposal": proposal, "suggestions": [{"profile_id": pid, "why": why, "name": f"{by_id[pid].get('first_name')} {by_id[pid].get('last_name')}", "email": by_id[pid].get("email"), "deleted": bool(by_id[pid].get("deleted_at"))}
                                                         for pid, why in suggested.items() if pid in by_id and not by_id[pid].get("deleted_at")][:5]})
    return out


def exception_row(run_id: str, key: str) -> dict | None:
    """Fila de excepción de UN contenido (con sus datos para el owner), o None si ya no es una excepción visible."""
    plan = manifest.get_plan(run_id)
    index = [r for r in _exception_index(plan, manifest.list_ledgers(run_id)) if r["content_key"] == key]
    return exception_details(run_id, index)[0] if index else None


def _manual_unit(run_id: str, key: str) -> tuple[dict, dict, dict, dict]:
    run = _import_run(run_id)
    if not run.get("execution"):
        raise ExecutionError("La importación todavía no comenzó: las excepciones se gestionan una vez iniciada.")
    if (run.get("staging") or {}).get("state") != "PRESENT":
        raise ExecutionError("Los datos temporales del lote ya no están disponibles.")
    plan = manifest.get_plan(run_id)
    decision = (plan or {}).get("decisions", {}).get(key)
    group = manifest.get_group(run_id, key)
    if decision is None or group is None or group.get("state") != pilot.EXTRACTED:
        raise ExecutionError("Este contenido no se puede gestionar (no fue analizado).")
    ledger = manifest.get_ledger(run_id, key)
    state = _effective(ledger)
    if state in (DONE, DISMISSED):
        raise ExecutionError("Este contenido ya está resuelto.")
    if decision["action"] != planning.PENDING and state not in (BLOCKED, FAILED) and not (ledger and ledger.get("source") == "manual"):
        raise ExecutionError("Este contenido está en el plan automático: se importa con la ejecución, no a mano.")
    return run, plan, decision, group


def _finish_manual(run: dict, plan: dict, key: str, entry: dict, token: str, actor: str, sleep) -> str:
    banco = _Banco([])
    try:
        outcome = _run_unit(run, key, entry, plan, banco, manifest.list_ledgers(run["run_id"]), token, actor, sleep)
    except _Stop as stop:
        return "STOP:" + stop.reason
    return outcome


def complete_identity(run_id: str, key: str, token: str, *, actor: str, first_name: str, last_name: str, email: str = "", phone: str = "", duplicate_confirmed: bool = False, sleep=time.sleep) -> dict:
    """Alta MANUAL con nombre y apellido reales que escribe el owner (nunca uno ficticio) y subida del CV. Pasa por la misma validación del Banco y por el mismo aviso de duplicado; no fusiona nada."""
    run, plan, decision, group = _manual_unit(run_id, key)
    try:
        shaped = _profile_fields({"first_name": first_name, "last_name": last_name, "email": email, "phone": phone, "profile_skills": ", ".join(group.get("specialties") or []), "tags": TAG_HISTORICO})
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    pid = manual_profile_id(run_id, key)
    if profile_repository.get(pid) is None:
        duplicate = None if duplicate_confirmed else human_capital_service.find_duplicate(shaped, profile_repository.list())
        if duplicate is not None:
            return {"ok": False, "advisory": {"profile_id": duplicate["profile_id"], "name": f"{duplicate.get('first_name')} {duplicate.get('last_name')}", "email": duplicate.get("email")}}
        try:
            human_capital_service.create_profile_for_import(pid, {**shaped, "import_origin": _origin(run, manual=True, content_key=key, completed_by=actor)})
        except (profile_repository.ProfileStorageError, profile_repository.ProfileRemoteOutcomeUnknownError, profile_repository.ProfileIdConflictError):
            return {"ok": False, "error": "No se pudo guardar el perfil (persistencia no confirmada). No se creó nada: reintentá."}
    entry = _manual_entry(run_id, key, MANUAL_CREATE, pid, actor)
    outcome = _finish_manual(run, plan, key, entry, token, actor, sleep)
    return {"ok": True, "outcome": outcome, "profile_id": pid}


def link_existing(run_id: str, key: str, token: str, *, actor: str, profile_id: str, sleep=time.sleep) -> dict:
    """Vincula el CV a un perfil EXISTENTE elegido por el owner, sin modificar ninguno de sus campos."""
    run, plan, decision, group = _manual_unit(run_id, key)
    target = profile_repository.get((profile_id or "").strip())
    if target is None or target.get("deleted_at"):
        return {"ok": False, "error": "El perfil elegido no existe o está dado de baja."}
    entry = _manual_entry(run_id, key, MANUAL_LINK, target["profile_id"], actor)
    outcome = _finish_manual(run, plan, key, entry, token, actor, sleep)
    return {"ok": True, "outcome": outcome, "profile_id": target["profile_id"]}


def _manual_entry(run_id: str, key: str, action: str, profile_id: str, actor: str) -> dict:
    previous = manifest.get_ledger(run_id, key) or {}
    entry = {"source": "manual", "action": action, "cluster": None, "profile_id": profile_id, "document_id": document_id_for(profile_id, key), "state": PLANNED, "attempts": 0,
             "profile_created": action == MANUAL_CREATE, "reason": None, "last_error": None, "resolved_by": actor, "history": previous.get("history", [])}
    return _save(run_id, key, entry, action)


def dismiss(run_id: str, key: str, *, actor: str, reason: str) -> dict:
    if reason not in DISMISS_REASONS:
        return {"ok": False, "error": "Motivo inválido."}
    run, plan, decision, group = _manual_unit(run_id, key)
    previous = manifest.get_ledger(run_id, key) or {}
    entry = {"source": "manual", "action": "DISMISS", "cluster": None, "profile_id": None, "document_id": None, "state": DISMISSED, "attempts": 0, "profile_created": False, "reason": reason,
             "last_error": None, "resolved_by": actor, "history": previous.get("history", [])}
    _save(run_id, key, entry, "DISMISSED")
    return {"ok": True}


def reopen(run_id: str, key: str, *, actor: str) -> dict:
    """Deshace un descarte (solo DISMISSED): el contenido vuelve a quedar por resolver."""
    ledger = manifest.get_ledger(run_id, key)
    if not ledger or ledger.get("state") != DISMISSED:
        return {"ok": False, "error": "Solo se puede reabrir un contenido descartado."}
    manifest.put_ledger(run_id, key, {**ledger, "state": REOPENED, "history": [*ledger.get("history", []), {"at": manifest.now_iso(), "event": "REOPENED", "by": actor}][-20:]})
    return {"ok": True}


def retry(run_id: str, key: str, *, actor: str) -> dict:
    """Reintenta un contenido BLOQUEADO o con error: vuelve a PLANNED (se revalida contra el Banco al ejecutarse)."""
    ledger = manifest.get_ledger(run_id, key)
    if not ledger or ledger.get("state") not in (BLOCKED, FAILED):
        return {"ok": False, "error": "Solo se reintenta un contenido bloqueado o con error."}
    manifest.put_ledger(run_id, key, {**ledger, "state": PLANNED, "attempts": 0, "reason": None, "last_error": None, "history": [*ledger.get("history", []), {"at": manifest.now_iso(), "event": "RETRY", "by": actor}][-20:]})
    return {"ok": True}
