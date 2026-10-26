"""Descarga múltiple de CVs (CCHH → Candidatos → «Descargar CVs»), Fase 2.

Una ÚNICA solicitud HTTP: plan (solo metadata local, sin Graph) → confirmación del usuario → auditoría durable → lectura acotada de cada CV por Microsoft Graph con el token DELEGADO del
usuario (sus permisos reales) → ZIP temporal → entrega. No hay procesamiento por tandas ni infraestructura nueva.

Reutiliza lo existente: el modelo documental (`profile_document_service.list_documents_for_profile`: `drive_id`/`item_id`/`size_bytes`/`filename` ya guardados), el transporte Graph compartido
(`core.microsoft365`, ahora con `graph_download_capped`: lectura ACOTADA —tamaño antes y DURANTE la transferencia, plazo total, cancelación—, en lugar de `graph_get_bytes`, que lee todo
en memoria sin tope) y la persistencia durable de CCHH (`remote_store.write_through`, mismo patrón fail-closed que los lotes de importación histórica).

LÍMITES (acordados con el owner; la medición real en DigitalOcean, 3 CVs, dio 452–832 ms por documento y 177 ms de ZIP): 25 candidatos, 60 archivos, 100 MB totales, 25 MB por archivo, presupuesto
de 60 s por solicitud (no se inicia ningún archivo nuevo pasados 45 s, y cada archivo tiene su propio plazo) —el proxy de App Platform corta hacia los ~100 s—. Los topes se validan en el
SERVIDOR (autoritativo), con el tamaño declarado al planificar y con lectura acotada al descargar.

AUDITORÍA DURABLE (`human-capital/cv-exports/<operation_id>/intent.json` y `result.json`, un archivo por evento, nunca se sobrescribe): la INTENCIÓN se confirma en Spaces ANTES de la
primera llamada a Graph (si no se puede confirmar, la exportación no empieza: fail-closed) y el RESULTADO se confirma ANTES de entregar el ZIP (si no se puede, no se entrega). Solo identificadores
internos, cantidades y códigos: sin nombres de candidatos ni de archivos, sin URLs ni tokens. NO usa `LOG_ACTIVITY` (best-effort, no sirve como evidencia).

NUNCA UN ZIP PARCIAL EN SILENCIO: si algún documento planificado falla (403/404/429/timeout/corte/presupuesto), no se entrega nada y se informa cuáles fallaron; recién con una SEGUNDA confirmación
explícita (`allow_partial` + los ids reconocidos) se entrega el ZIP parcial, y solo si los fallos son un subconjunto de los reconocidos. La segunda confirmación vuelve a leer todo (las lecturas
son idempotentes: el único efecto es la auditoría, una operación nueva enlazada con `parent_operation_id`).

TEMPORAL: un directorio `dwcvx-*` fuera de las rutas persistidas; cada CV baja a un archivo parcial que se borra apenas entra al ZIP (pico de disco ≈ ZIP + un archivo); en Linux el ZIP se abre y se
desvincula antes de entregarlo (no queda en disco aunque el cliente se desconecte o el proceso muera); en cualquier caso, `try/finally` y un barrido de restos viejos. Nombres dentro del ZIP sanitizados
(sin rutas, sin `..`, sin caracteres de control ni nombres reservados de Windows). No se persiste ningún ZIP ni copia de CV en Spaces, SharePoint ni directorios del Hub; los originales no se tocan.

CONCURRENCIA (en proceso: réplica única como precondición, igual que el resto de CCHH): una exportación en vuelo por usuario y como máximo `MAX_CONCURRENT` en total."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import config
from core import microsoft365, remote_store
from webapp.services import human_capital_service, human_capital_share, profile_document_service

_logger = logging.getLogger("danwor.human_capital")

MAX_CANDIDATES = 25
MAX_FILES = 60
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_FILE_BYTES = 25 * 1024 * 1024
TIME_BUDGET_S = 60.0
NO_NEW_FILE_AFTER_S = 45.0
PER_FILE_DEADLINE_S = 30.0
SOCKET_TIMEOUT_S = 15.0
MAX_ATTEMPTS = 2                                          # 1 intento + 1 reintento (solo ante fallos transitorios y con presupuesto)
RETRY_AFTER_CAP_S = 5.0
RETRY_BACKOFF_S = 1.0
TRANSIENT_HTTP = {429, 500, 502, 503, 504}                # mismo criterio que `cv_history_inventory.TRANSIENT_STATUSES`
MAX_CONCURRENT = 2
EST_SECONDS_PER_FILE = 1.0                                # estimación conservadora (medido: 0,45–0,83 s por CV en DigitalOcean)
EST_SECONDS_PER_MB = 0.2
STALE_TEMP_S = 3600
TEMP_PREFIX = "dwcvx-"
GRAPH_BASE = profile_document_service.GRAPH_BASE

GRAPH_ID_RE = re.compile(r"^[A-Za-z0-9!_,.\-]{1,256}$")
OPERATION_ID_RE = re.compile(r"^[0-9a-f]{32}$")

REASON_EMPTY = "empty"
REASON_OVER_CANDIDATES = "over_candidates"
REASON_OVER_FILES = "over_files"
REASON_OVER_TOTAL = "over_total"
REASON_NONE_AVAILABLE = "none_available"
REASON_NO_DOCUMENTS = "no_documents"
REASON_MESSAGES = {
    REASON_EMPTY: "No hay candidatos seleccionados.",
    REASON_OVER_CANDIDATES: f"Se pueden descargar CVs de hasta {MAX_CANDIDATES} candidatos por vez. Quitá algunos de la selección y volvé a intentar.",
    REASON_OVER_FILES: f"La selección tiene más de {MAX_FILES} archivos (el máximo por descarga). Quitá candidatos y volvé a intentar.",
    REASON_OVER_TOTAL: f"El tamaño total supera {MAX_TOTAL_BYTES // (1024 * 1024)} MB (el máximo por descarga). Quitá candidatos y volvé a intentar.",
    REASON_NONE_AVAILABLE: "Ninguno de los candidatos seleccionados está disponible (puede que se hayan dado de baja).",
    REASON_NO_DOCUMENTS: "Los candidatos seleccionados no tienen CVs que se puedan descargar.",
}
OMIT_MESSAGES = {"too_large": f"supera los {MAX_FILE_BYTES // (1024 * 1024)} MB por archivo", "no_location": "sin ubicación válida en SharePoint"}
FAILURE_MESSAGES = {
    "forbidden": "sin permiso en SharePoint (403)", "not_found": "no se encontró en SharePoint (404)", "throttled": "Microsoft limitó las solicitudes (429)",
    "token_expired": "venció tu sesión de Microsoft", "timeout": "tardó demasiado", "network": "falló la conexión con Microsoft", "incomplete": "la descarga quedó incompleta",
    "too_large": "supera el tamaño permitido", "graph_error": "error del servicio de Microsoft", "time_budget": "no alcanzó el tiempo de la descarga", "not_attempted": "no se intentó",
    "cancelled": "cancelado", "error": "error inesperado",
}


class ExportSourceError(Exception):
    """No se pudo leer el modelo (perfiles/documentos). Sin detalle hacia el usuario."""


class ExportBusy(Exception):
    """Ya hay una exportación en curso (del mismo usuario o el máximo global)."""


class ExportAuditUnavailable(Exception):
    """La auditoría durable no se pudo confirmar: la exportación no empieza (o no se entrega)."""


class ExportInputError(Exception):
    """Falta algo obligatorio en la solicitud (p. ej. el token delegado)."""


# ───────────────────────── plan (solo metadata local, sin Graph) ─────────────────────────
def _document_label(document: dict) -> str:
    return str(document.get("filename") or "").strip() or "CV"


@dataclass
class ExportPlan:
    reason: str | None = None
    candidates: list[dict] = field(default_factory=list)        # [{"id","name","docs_total","docs_included","docs_omitted"}]
    docs: list[dict] = field(default_factory=list)              # a descargar: {"document_id","profile_id","candidate","filename","size","drive_id","item_id"}
    omitted: list[dict] = field(default_factory=list)           # conocidos de antemano: {"document_id","candidate","filename","reason"}
    unavailable: list[str] = field(default_factory=list)
    requested: int = 0
    declared_bytes: int = 0
    digest: str = ""
    estimate_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.reason is None

    @property
    def message(self) -> str:
        return REASON_MESSAGES.get(self.reason or "", "")

    @property
    def risk_time(self) -> bool:
        return self.estimate_s > NO_NEW_FILE_AFTER_S


def _digest(docs: list[dict], omitted: list[dict]) -> str:
    canon = json.dumps({"docs": sorted(d["document_id"] for d in docs), "omitted": sorted([o["document_id"], o["reason"]] for o in omitted)}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def build_plan(raw_ids, *, get_profile: Callable[[str], dict | None] = None, list_documents: Callable[[str], list[dict]] = None) -> ExportPlan:
    """Resuelve la selección contra el modelo vigente y aplica los límites (el de candidatos ANTES de leer ningún perfil). No habla con Graph. Todos los CVs de cada candidato entran al plan
    (salvo los que no se pueden descargar: más de 25 MB o sin ubicación válida, que se informan como «omitidos»). `reason` ≠ None ⇒ no se puede exportar (la selección excede un límite)."""
    get_profile = get_profile or human_capital_service.get_profile
    list_documents = list_documents or profile_document_service.list_documents_for_profile
    ids, _invalid = human_capital_share.normalize_ids(raw_ids)
    plan = ExportPlan(requested=len(ids))
    if not ids:
        plan.reason = REASON_EMPTY
        return plan
    if len(ids) > MAX_CANDIDATES:
        plan.reason = REASON_OVER_CANDIDATES
        return plan
    try:
        for profile_id in ids:
            profile = get_profile(profile_id)
            if not profile or profile.get("deleted_at"):
                plan.unavailable.append(profile_id)
                continue
            name = human_capital_share.full_name(profile)
            documents = sorted(list_documents(profile_id) or [], key=lambda d: (str(d.get("uploaded_at") or ""), str(d.get("document_id") or "")), reverse=True)
            info = {"id": profile_id, "name": name, "docs_total": 0, "docs_included": 0, "docs_omitted": 0}
            for document in documents:
                document_id = str(document.get("document_id") or "")
                if not document_id:
                    continue
                info["docs_total"] += 1
                size = document.get("size_bytes")
                size = size if isinstance(size, int) and size >= 0 else None
                drive_id, item_id = str(document.get("drive_id") or ""), str(document.get("item_id") or "")
                entry = {"document_id": document_id, "profile_id": profile_id, "candidate": name, "filename": _document_label(document), "size": size, "drive_id": drive_id, "item_id": item_id}
                if not (GRAPH_ID_RE.match(drive_id) and GRAPH_ID_RE.match(item_id)):
                    plan.omitted.append({"document_id": document_id, "candidate": name, "filename": entry["filename"], "reason": "no_location"})
                    info["docs_omitted"] += 1
                elif size is not None and size > MAX_FILE_BYTES:
                    plan.omitted.append({"document_id": document_id, "candidate": name, "filename": entry["filename"], "reason": "too_large"})
                    info["docs_omitted"] += 1
                else:
                    plan.docs.append(entry)
                    info["docs_included"] += 1
                    plan.declared_bytes += size or 0
            plan.candidates.append(info)
    except Exception as exc:  # noqa: BLE001 — cualquier falla del almacén: sin detalle hacia el usuario
        raise ExportSourceError(type(exc).__name__) from None
    if not plan.candidates:
        plan.reason = REASON_NONE_AVAILABLE
    elif not plan.docs:
        plan.reason = REASON_NO_DOCUMENTS
    elif len(plan.docs) > MAX_FILES:
        plan.reason = REASON_OVER_FILES
    elif plan.declared_bytes > MAX_TOTAL_BYTES:
        plan.reason = REASON_OVER_TOTAL
    plan.digest = _digest(plan.docs, plan.omitted)
    plan.estimate_s = round(len(plan.docs) * EST_SECONDS_PER_FILE + (plan.declared_bytes / (1024 * 1024)) * EST_SECONDS_PER_MB, 1)
    return plan


# ───────────────────────── nombres seguros dentro del ZIP ─────────────────────────
_BAD_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_component(text: str, fallback: str = "archivo", max_len: int = 80) -> str:
    """Un componente de ruta seguro: sin separadores, `..`, caracteres de control ni nombres reservados de Windows; con largo acotado."""
    value = unicodedata.normalize("NFC", str(text or ""))
    value = _BAD_CHARS.sub("_", value)
    value = re.sub(r"\.{2,}", "_", value).strip(" .")                # nada de «..», ni siquiera como texto dentro de un componente
    value = re.sub(r"_{2,}", "_", value)
    if not value or set(value) <= {"_"}:
        value = fallback
    if value.split(".")[0].upper() in _WINDOWS_RESERVED:
        value = "_" + value
    return value[:max_len].rstrip(" .") or fallback


def safe_filename(filename: str, fallback: str = "cv") -> str:
    base, dot, ext = str(filename or "").rpartition(".")
    if not dot or not re.fullmatch(r"[A-Za-z0-9]{1,8}", ext) or not base:
        return safe_component(filename, fallback)
    return safe_component(base, fallback, 70) + "." + ext.lower()


class _NameAllocator:
    def __init__(self):
        self._used: set[str] = set()

    def allocate(self, folder: str, filename: str) -> str:
        stem, dot, ext = filename.rpartition(".")
        stem, ext = (stem, "." + ext) if dot else (filename, "")
        candidate, number = f"{folder}/{filename}", 1
        while candidate.lower() in self._used:
            number += 1
            candidate = f"{folder}/{stem} ({number}){ext}"
        self._used.add(candidate.lower())
        return candidate


# ───────────────────────── auditoría durable (fail-closed) ─────────────────────────
def audit_root() -> Path:
    return Path(config.BASE_DIR) / "human-capital" / "cv-exports"


def _write_audit(path: Path, payload: dict) -> None:
    if remote_store.read_only_mode():
        raise ExportAuditUnavailable("modo de solo lectura")
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)
    try:
        outcome = remote_store.write_through(path, text)
    except OSError:
        raise ExportAuditUnavailable("no se pudo escribir el registro") from None
    if outcome != remote_store.DURABLE_REMOTE and not remote_store.local_only():
        raise ExportAuditUnavailable("registro no confirmado en Spaces")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_intent(operation_id: str, *, actor: str, plan: ExportPlan, parent_operation_id: str | None, allow_partial: bool, ack_failed: list[str]) -> None:
    _write_audit(audit_root() / operation_id / "intent.json", {
        "schema": 1, "operation_id": operation_id, "parent_operation_id": parent_operation_id, "kind": "cv_export", "started_at": _now_iso(), "actor": actor,
        "mode": "partial_consented" if allow_partial else "full",
        "limits": {"candidates": MAX_CANDIDATES, "files": MAX_FILES, "total_bytes": MAX_TOTAL_BYTES, "file_bytes": MAX_FILE_BYTES, "budget_s": TIME_BUDGET_S},
        "counts": {"candidates": len(plan.candidates), "documents": len(plan.docs), "declared_bytes": plan.declared_bytes, "omitted": len(plan.omitted)},
        "profile_ids": [c["id"] for c in plan.candidates], "document_ids": [d["document_id"] for d in plan.docs],
        "omitted": [{"document_id": o["document_id"], "reason": o["reason"]} for o in plan.omitted], "plan_digest": plan.digest, "acknowledged_failed_ids": sorted(ack_failed),
    })


def write_result(operation_id: str, *, outcome: str, results: list[dict], files_ok: int, bytes_ok: int, zip_bytes: int, elapsed_ms: int) -> None:
    _write_audit(audit_root() / operation_id / "result.json", {
        "schema": 1, "operation_id": operation_id, "finished_at": _now_iso(), "outcome": outcome, "files_ok": files_ok, "bytes_ok": bytes_ok, "zip_bytes": zip_bytes, "elapsed_ms": elapsed_ms,
        "documents": [{k: r.get(k) for k in ("document_id", "outcome", "http_status", "bytes", "ms", "attempts")} for r in results],
    })


def read_audit(operation_id: str) -> dict:
    """Lectura para pruebas/soporte: devuelve {"intent": …, "result": …} (None si falta)."""
    if not OPERATION_ID_RE.match(operation_id):
        return {"intent": None, "result": None}
    out = {}
    for key in ("intent", "result"):
        path = audit_root() / operation_id / f"{key}.json"
        try:
            out[key] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out[key] = None
    return out


# ───────────────────────── concurrencia (en proceso) ─────────────────────────
_slots_lock = threading.Lock()
_active_actors: set[str] = set()


@contextmanager
def _slot(actor: str):
    key = (actor or "").strip().lower() or "anon"
    with _slots_lock:
        if key in _active_actors or len(_active_actors) >= MAX_CONCURRENT:
            raise ExportBusy()
        _active_actors.add(key)
    try:
        yield
    finally:
        with _slots_lock:
            _active_actors.discard(key)


def _sweep_stale_temp(max_age_s: float = STALE_TEMP_S) -> int:
    """Borra restos viejos de operaciones anteriores (`dwcvx-*` con más de `max_age_s`), p. ej. tras un corte en Windows. Solo toca directorios con NUESTRO prefijo."""
    removed = 0
    root = Path(tempfile.gettempdir())
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.name.startswith(TEMP_PREFIX) and entry.is_dir() and time.time() - entry.stat().st_mtime > max_age_s:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


# ───────────────────────── ejecución ─────────────────────────
@dataclass
class ExportOutcome:
    operation_id: str
    status: str                                   # delivered_full | delivered_partial | withheld_partial | failed | cancelled
    plan: ExportPlan
    results: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)          # {"document_id","candidate","filename","code","message"}
    files_ok: int = 0
    bytes_ok: int = 0
    elapsed_ms: int = 0
    zip_stream: object | None = None
    zip_size: int = 0
    zip_name: str = ""
    _cleanup: Callable[[], None] = lambda: None

    def cleanup(self) -> None:
        self._cleanup()

    @property
    def delivered(self) -> bool:
        return self.status in ("delivered_full", "delivered_partial")


def _public_code(result: dict) -> str:
    outcome = result.get("outcome", "")
    status = result.get("http_status")
    return {"http_401": "token_expired", "http_403": "forbidden", "http_404": "not_found", "http_429": "throttled", "timeout": "timeout", "deadline": "timeout", "url_error": "network",
            "short_read": "incomplete", "oversize_declared": "too_large", "oversize_header": "too_large", "oversize_stream": "too_large", "cancelled": "cancelled"}.get(
        outcome, "graph_error" if outcome.startswith("http_") and (status or 0) >= 500 else "error")


def _drive_content_url(base: str, drive_id: str, item_id: str) -> str:
    return f"{base}/drives/{quote(drive_id, safe='!,')}/items/{quote(item_id, safe='')}/content"


def _leeme(operation_id: str, omitted: list[dict], failed: list[dict]) -> str:
    lines = [f"Descarga de CVs — operación {operation_id}", f"Generado: {_now_iso()}", "", "Los siguientes documentos NO se incluyeron en este ZIP:", ""]
    for item in omitted:
        lines.append(f"- {item['candidate']} — {item['filename']}: {OMIT_MESSAGES.get(item['reason'], item['reason'])}")
    for item in failed:
        lines.append(f"- {item['candidate']} — {item['filename']}: {item['message']}")
    return "\n".join(lines) + "\n"


def execute(*, actor: str, raw_ids, token: str, expected_digest: str, ack_omitted: bool, allow_partial: bool = False, ack_failed=(), parent_operation_id: str | None = None,
            cancel: threading.Event | None = None, downloader: Callable | None = None, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] | None = None,
            get_profile=None, list_documents=None, graph_base: str = GRAPH_BASE) -> ExportOutcome:
    """Plan → auditoría de intención (fail-closed) → lectura acotada → ZIP → auditoría de resultado (fail-closed) → entrega. Ver el docstring del módulo."""
    if not token:
        raise ExportInputError("token")
    if parent_operation_id and not OPERATION_ID_RE.match(parent_operation_id):
        parent_operation_id = None
    downloader = downloader or microsoft365.graph_download_capped
    ack_failed = {str(i) for i in ack_failed if human_capital_share.ID_RE.match(str(i) or "")}
    plan = build_plan(raw_ids, get_profile=get_profile, list_documents=list_documents)
    operation_id = uuid.uuid4().hex
    if not plan.ok:
        return ExportOutcome(operation_id, "blocked", plan)
    if plan.digest != expected_digest or (plan.omitted and not ack_omitted):
        return ExportOutcome(operation_id, "stale_plan", plan)                      # la selección o sus CVs cambiaron desde el resumen que el usuario confirmó
    with _slot(actor):
        _sweep_stale_temp()
        base_tmp = Path(tempfile.gettempdir()).resolve()
        if base_tmp == Path(config.BASE_DIR).resolve() or Path(config.BASE_DIR).resolve() in base_tmp.parents:
            raise ExportAuditUnavailable("directorio temporal dentro de la aplicación")      # un ZIP nunca debe caer bajo rutas persistidas
        write_intent(operation_id, actor=actor, plan=plan, parent_operation_id=parent_operation_id, allow_partial=allow_partial, ack_failed=sorted(ack_failed))   # FAIL-CLOSED
        started = clock()
        workdir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
        cleanup_needed = True
        try:
            outcome = _run(operation_id, actor, plan, workdir, token, allow_partial, ack_failed, started, cancel, downloader, clock, sleep, graph_base)
            cleanup_needed = not outcome.delivered
            return outcome
        except BaseException:
            try:
                write_result(operation_id, outcome="error", results=[], files_ok=0, bytes_ok=0, zip_bytes=0, elapsed_ms=int((clock() - started) * 1000))
            except Exception:  # noqa: BLE001 — nada que entregar: solo se intenta dejar constancia
                pass
            raise
        finally:
            if cleanup_needed:
                shutil.rmtree(workdir, ignore_errors=True)


def _run(operation_id, actor, plan, workdir: Path, token, allow_partial, ack_failed, started, cancel, downloader, clock, sleep, graph_base) -> ExportOutcome:
    zip_path = workdir / "cvs.zip"
    names = _NameAllocator()
    results: list[dict] = []
    failed: list[dict] = []
    bytes_ok = 0
    token_dead = False
    wait = sleep or (lambda seconds: cancel.wait(seconds) if cancel is not None else time.sleep(seconds))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as archive:
        for doc in plan.docs:
            entry = {"document_id": doc["document_id"], "outcome": "", "http_status": None, "bytes": 0, "ms": 0, "attempts": 0}
            elapsed = clock() - started
            if cancel is not None and cancel.is_set():
                entry["outcome"] = "cancelled"
            elif token_dead:
                entry["outcome"] = "not_attempted"
            elif elapsed >= NO_NEW_FILE_AFTER_S:
                entry["outcome"] = "time_budget"
            else:
                part = workdir / f"part-{len(results)}.bin"
                fetched = _fetch_one(doc, part, token, bytes_ok, started, cancel, downloader, clock, wait, graph_base)
                entry.update({k: fetched[k] for k in ("http_status", "bytes", "ms", "attempts")})
                if fetched["outcome"] == "ok":
                    entry["outcome"] = "ok"
                    bytes_ok += fetched["bytes"]
                    archive.write(part, names.allocate(safe_component(doc["candidate"], "candidato"), safe_filename(doc["filename"])))
                else:
                    entry["outcome"] = _public_code(fetched)
                    token_dead = token_dead or entry["outcome"] == "token_expired"
                part.unlink(missing_ok=True)                                         # el parcial se borra apenas entra al ZIP (o falla)
            results.append(entry)
            if entry["outcome"] != "ok":
                failed.append({"document_id": doc["document_id"], "candidate": doc["candidate"], "filename": doc["filename"], "code": entry["outcome"],
                               "message": FAILURE_MESSAGES.get(entry["outcome"], FAILURE_MESSAGES["error"])})
        files_ok = len(plan.docs) - len(failed)
        failed_ids = {f["document_id"] for f in failed}
        was_cancelled = cancel is not None and cancel.is_set()
        consented = allow_partial and failed_ids <= ack_failed and files_ok > 0
        if was_cancelled:
            status = "cancelled"
        elif not failed:
            status = "delivered_full"
        elif consented:
            status = "delivered_partial"
        elif files_ok == 0:
            status = "failed"
        else:
            status = "withheld_partial"
        if status in ("delivered_full", "delivered_partial") and (plan.omitted or failed):
            archive.writestr("LEEME_OMITIDOS.txt", _leeme(operation_id, plan.omitted, failed))
    elapsed_ms = int((clock() - started) * 1000)
    outcome = ExportOutcome(operation_id, status, plan, results=results, failed=failed, files_ok=files_ok, bytes_ok=bytes_ok, elapsed_ms=elapsed_ms)
    delivering = status in ("delivered_full", "delivered_partial")
    zip_size = zip_path.stat().st_size if (delivering and zip_path.exists()) else 0
    audit_outcome = {"delivered_full": "prepared_full", "delivered_partial": "prepared_partial"}.get(status, status)
    try:
        write_result(operation_id, outcome=audit_outcome, results=results, files_ok=files_ok, bytes_ok=bytes_ok, zip_bytes=zip_size, elapsed_ms=elapsed_ms)
    except ExportAuditUnavailable:
        if delivering:
            raise                                                                    # el resultado no quedó confirmado: NO se entrega (fail-closed)
        _logger.warning("cv_export %s: no se pudo confirmar el registro de resultado (%s); no se entregó nada", operation_id, status)
    _logger.info("cv_export %s status=%s files_ok=%d failed=%d bytes=%d elapsed_ms=%d", operation_id, status, files_ok, len(failed), bytes_ok, elapsed_ms)
    if delivering:
        handle = open(zip_path, "rb")
        if os.name == "posix":
            try:
                os.unlink(zip_path)                                                   # sin enlace en disco: si el cliente se desconecta o el proceso muere, el SO reclama el espacio
                shutil.rmtree(workdir, ignore_errors=True)
            except OSError:
                pass

        def _cleanup() -> None:
            try:
                handle.close()
            finally:
                shutil.rmtree(workdir, ignore_errors=True)
        outcome.zip_stream, outcome.zip_size = handle, zip_size
        outcome.zip_name = f"cvs-candidatos-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.zip"
        outcome._cleanup = _cleanup
    return outcome


def _fetch_one(doc, part: Path, token, bytes_so_far, started, cancel, downloader, clock, wait, graph_base) -> dict:
    """UN CV con lectura acotada y a lo sumo un reintento ante fallos transitorios (429/5xx/red/corte), respetando `Retry-After` (tope corto) y el presupuesto de la solicitud."""
    url = _drive_content_url(graph_base, doc["drive_id"], doc["item_id"])
    attempts = 0
    t0 = clock()
    while True:
        attempts += 1
        remaining = TIME_BUDGET_S - (clock() - started)
        if remaining <= 1:
            return {"outcome": "deadline", "http_status": None, "bytes": 0, "ms": int((clock() - t0) * 1000), "attempts": attempts}
        cap = min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - bytes_so_far)
        with open(part, "wb") as sink:
            result = downloader(url, token, sink, cap_bytes=cap, socket_timeout=min(SOCKET_TIMEOUT_S, remaining), deadline_s=min(PER_FILE_DEADLINE_S, remaining), cancel=cancel,
                                declared_size=doc.get("size"))
        outcome = result.get("outcome", "")
        transient = outcome in ("timeout", "url_error", "short_read") or (outcome.startswith("http_") and (result.get("http_status") in TRANSIENT_HTTP))
        if outcome != "ok" and transient and attempts < MAX_ATTEMPTS and not (cancel is not None and cancel.is_set()):
            delay = min(float(result.get("retry_after_s") or RETRY_BACKOFF_S), RETRY_AFTER_CAP_S)
            if (clock() - started) + delay < NO_NEW_FILE_AFTER_S:
                wait(delay)
                continue
        return {"outcome": outcome, "http_status": result.get("http_status"), "bytes": int(result.get("bytes") or 0), "ms": int((clock() - t0) * 1000), "attempts": attempts}
