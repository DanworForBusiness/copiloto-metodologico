"""cv_inbox.py — BANDEJA OPERATIVA de Carga masiva (Capital Humano): CVs que Capital Humano deposita en `Perfiles/carga-masiva` (su zona de entrada) y que, tras una revisión humana, se incorporan al Banco y al
repositorio gobernado `CVs` (exclusivo del Hub) sin Excel ni renombrados.

NO es otra importación histórica ni un framework: orquesta lo que ya existe —extractor (`cv_extract`), conciliación (`cv_reconcile` + `CandidateIndex`), precarga y evidencia legible (`cv_history_review`), alta
idempotente (`human_capital_service.create_profile_for_import`), conciliación y subida verificada con el contrato documental vigente (`cv_incorporation.reconcile_or_upload` → `profile_document_service`), tabla de
errores (`cv_incorporation.classify`) y persistencia/candados/auditoría (`cv_history_manifest`, área `cv-inbox`, separada de los lotes de Históricos)—.

SEPARACIÓN DE RESPONSABILIDADES: `Perfiles/carga-masiva` es la zona de entrada que administra Capital Humano, en una ubicación FIJA de código (`config.HC_CV_INBOX_FOLDER_URL`, el link de compartir de esa
carpeta MISMA; sin variables de entorno): se resuelve con el MISMO descubrimiento Graph por link que usó la importación histórica, obteniendo `drive_id`/`item_id` en cada uso, y se verifica que sea `carga-masiva`
dentro de `Perfiles` y fuera de `CVs`. NO se busca ninguna subcarpeta adentro. Si el link no resuelve se informa (no se buscan alternativas ni se crea nada). Solo se leen los archivos DIRECTOS de esa carpeta (no se recorre el resto de `Perfiles` ni sus subcarpetas). `CVs` (`HC_CV_ROOT_ITEM_ID`) es el repositorio definitivo y solo lo escribe el Hub, con el mismo contrato documental de siempre
(carpeta por candidato, `<document_id>__<nombre>`, metadata con drive/item/web_url). Por eso la incorporación COPIA el contenido verificado (descarga + subida, que solo requiere escritura sobre `CVs`, ya vigente) y no
mueve el original: el origen no se modifica hasta que el documento definitivo y su metadata están confirmados. Recién entonces se RETIRA el original de la bandeja moviéndolo a `carga-masiva/incorporados`
(PATCH dentro del mismo drive, sin borrar nada: recuperable por Capital Humano). El retiro es un paso aparte y reintentable: si falla (p. ej. sin permiso de escritura sobre `carga-masiva`), el CV queda
INCORPORADO igual (el libro lo dice) y nunca reaparece como pendiente.

IDENTIDAD DOCUMENTAL ESTABLE: `item_key = sha256(driveId:itemId)[:16]` (el id de Graph no cambia al renombrar). El contenido se identifica por `cTag`/tamaño/quickXorHash y por el sha256 que se calcula al
analizar. Un archivo con otro cTag que el analizado está MODIFICADO y se vuelve a analizar.

CICLO DE VIDA (el estado vive en el Hub):
  * ANALIZAR (tandas acotadas): descarga en memoria, valida, extrae y guarda el registro protegido (`staging/<item_key>.json`, con datos personales). NUNCA crea candidatos ni escribe en SharePoint.
  * REVISAR: el estado de cada fila se DERIVA en cada vista (registro + borrador + libro + Banco VIGENTE + resto de la bandeja). La revisión humana (datos, perfil/skills y la decisión) se persiste como
    BORRADOR durable con control optimista (`drafts/<item_key>.json`).
  * ACEPTAR (por candidato y documento, individual o por lote), bajo el candado del libro de ese documento: se revalida la huella de lo que la persona vio y el archivo actual en la bandeja; libro ACCEPTED (decisión
    durable, sin datos personales) → perfil (id determinístico) o perfil existente revalidado → libro UPLOADING → conciliación/subida verificada → libro DONE → retiro. Ningún paso se declara hecho sin confirmación; un
    reintento retoma desde el libro y concilia con el estado real (ids determinísticos: nunca duplica perfiles ni CVs).
  * Los rechazados y los que tienen errores quedan en la bandeja hasta una decisión explícita: no hay limpieza automática.

REGLAS DE IDENTIDAD (lo aprendido de las consolidaciones de duplicados): nunca se crea un perfil si hay una coincidencia FUERTE o PROBABLE —contra el Banco o contra otro CV de la bandeja— sin una resolución humana
explícita; nunca se vincula por celular solo; el lote solo incluye filas seguras (identidad corroborada y email único, sin coincidencias; o vinculación por email exacto corroborado) o resueltas por una persona; las
altas sin email solo de a una y con confirmación. Vincular NO modifica el perfil existente.

LÍMITES (separados para análisis y aceptación; ver `_handoffs/CCHH_CV_INBOX_DISCOVERY.md`): 10 MiB por archivo (el del Banco), 500 archivos listados, análisis 15 archivos/45 s por tanda, aceptación 25 filas/45 s
por envío, 3 intentos por documento. Secuencial: sin colas ni workers. Datos personales: nunca en logs ni en el libro/auditoría."""
from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import uuid
from pathlib import Path

import config
from core import microsoft365
from webapp.services import (cv_extract, cv_history_drafts as drafts, cv_history_inventory as inventory, cv_history_manifest as manifest, cv_history_pilot as pilot, cv_history_review as review,
                             cv_incorporation as core, cv_reconcile, human_capital_service, profile_document_repository, profile_document_validation, profile_repository, sharepoint_graph_diagnostics as diag)
from webapp.services.cv_history_candidate_index import CandidateIndex

_log = logging.getLogger("danwor.cchh.cv_inbox")                                    # solo códigos: jamás nombres, emails, nombres de archivo ni tokens

AREA = manifest.INBOX
FOLDER_NAME = config.HC_CV_INBOX_FOLDER_NAME
RETIRED_FOLDER = "incorporados"
DISCARDED_FOLDER = "descartados"
NAMESPACE = uuid.UUID("8c1b6f0e-3d2a-4e57-9a4b-6f1d2c3b4a5e")
TAG = "Carga masiva SharePoint"
ORIGIN_KIND = "sharepoint_inbox"
GRAPH_BASE = inventory.GRAPH_BASE
MAX_FILE_BYTES = profile_document_validation.MAX_CV_UPLOAD_BYTES
MAX_LISTED = 500
PAGE_SIZE = 200
ANALYZE_MAX_DOCS, ANALYZE_BUDGET_S = 15, 45.0
ACCEPT_MAX_SELECTION, ACCEPT_BUDGET_S = 25, 45.0
MAX_ATTEMPTS = 3
AUTO_RETIRE_MAX = 3                                                                 # intentos del retiro físico que la bandeja reintenta SOLA (luego queda el botón «Reintentar el retiro»)
RECENT_DONE = 50                                                                    # incorporados recientes que se siguen mostrando (filtro «Incorporados»)
LIST_SELECT = "id,name,size,file,folder,eTag,cTag,lastModifiedDateTime,parentReference,webUrl"
FIELDS = ("first_name", "last_name", "email", "phone", "profile_skills")
SKILLS_MAX = 500

# estados del libro
ACCEPTED, PROFILE_READY, UPLOADING, DONE, BLOCKED, DISMISSED, REOPENED = "ACCEPTED", "PROFILE_READY", "UPLOADING", "DONE", "BLOCKED", "DISMISSED", "REOPENED"
ACTIVE = (ACCEPTED, PROFILE_READY, UPLOADING, BLOCKED)
RETIRE_PENDING, RETIRE_DONE, RETIRE_BLOCKED = "PENDING", "DONE", "BLOCKED"
DISMISS_REASONS = {"NOT_A_CV": "No es un CV", "NOT_A_CANDIDATE": "No es un candidato", "DUPLICATE": "Duplicado", "OTHER": "Otro motivo"}

# estados de la bandeja (derivados)
PENDING, MODIFIED, READY, REVIEW, INCOMPLETE, PROCESSED, DUPLICATE, ACCEPTING, INCORPORATED, DISCARDED, ERROR_RETRY, ERROR, MISSING = (
    "PENDING", "MODIFIED", "READY", "REVIEW", "INCOMPLETE", "PROCESSED", "DUPLICATE", "ACCEPTING", "INCORPORATED", "DISCARDED", "ERROR_RETRY", "ERROR", "MISSING")
STATUS_VIEW = {
    PENDING: ("Pendiente de análisis", "status-badge--otro"),
    MODIFIED: ("Modificado: volver a analizar", "status-badge--otro"),
    READY: ("Lista para incorporar", "status-badge--activo"),
    REVIEW: ("Requiere revisión", "status-badge--tentativo"),
    INCOMPLETE: ("Identidad incompleta", "status-badge--tentativo"),
    PROCESSED: ("Ya está en el Banco", "status-badge--inactivo"),
    DUPLICATE: ("Archivo repetido en la bandeja", "status-badge--inactivo"),
    ACCEPTING: ("Incorporación sin terminar", "status-badge--tentativo"),
    INCORPORATED: ("Incorporado", "status-badge--activo"),
    DISCARDED: ("Descartado", "status-badge--inactivo"),
    ERROR_RETRY: ("Error recuperable", "status-badge--tentativo"),
    ERROR: ("No se pudo leer", "status-badge--tentativo"),
    MISSING: ("Ya no está en la carpeta", "status-badge--inactivo"),
}
REASON_TEXT = {
    "SAME_PERSON_IN_INBOX": "Otro CV de la bandeja parece ser de la misma persona.",
    "MATCHES": "Hay perfiles del Banco que podrían ser esta persona.",
    "NAME_UNCORROBORATED": "El nombre propuesto no está corroborado por un dato independiente (por ejemplo el email).",
    "NO_EMAIL": "Sin email: solo se puede aceptar de a uno, con confirmación.",
    "NO_NAME": "No se encontró nombre y apellido con evidencia suficiente: completalos desde el CV.",
    "EXTRACTION_FAILED": "No se pudo leer el texto del documento (por ejemplo escaneado o protegido): completá los datos desde el CV.",
    "RESOLVED": "Revisado por una persona.",
    "RESOLUTION_STALE": "Cambió algo desde la revisión (el archivo o las coincidencias): revisalo de nuevo.",
    "LINK_EMAIL": "El email coincide con un perfil del Banco: se propone vincular el CV.",
    "OVERSIZE": "Supera el máximo de 10 MB del Banco.",
    "FORMAT_INVALID": "Formato no admitido o archivo dañado (se aceptan PDF, DOC y DOCX).",
    "DOWNLOAD_FORBIDDEN": "Tu usuario no puede leer este archivo en SharePoint.",
    "DOWNLOAD_ERROR": "No se pudo descargar el archivo de SharePoint.",
    "SOURCE_MISSING": "El archivo ya no está en la carpeta de carga masiva.",
    "SOURCE_CHANGED": "El archivo cambió en SharePoint después del análisis: volvé a analizarlo.",
    "REMOTE_CONTENT_MISMATCH": "En la carpeta del candidato ya hay otro archivo con ese nombre: no se pisó.",
    "PROFILE_ID_CONFLICT": "Ya existe un perfil con ese identificador y otros datos: no se pisó.",
    "DOC_ID_CONFLICT": "El documento ya está asociado a otro candidato.",
    "LINK_TARGET_CHANGED": "El perfil elegido cambió o se dio de baja desde la revisión.",
    "INVALID_PROFILE_FIELDS": "Los datos del perfil no son válidos.",
    "INVALID_DOCUMENT": "El documento no cumple el formato del Banco.",
    "RETRIES_EXHAUSTED": "Se agotaron los reintentos automáticos: reintentá a mano.",
    "PROFILE_CREATED_CV_PENDING": "El perfil ya se creó; falta guardar el CV en su carpeta.",
    "UPLOAD_PENDING_CONFIRMATION": "El CV puede haberse guardado pero no se confirmó el registro: reintentá para conciliar (no se duplica).",
    "RETIRE_PENDING": "Falta retirar el archivo de la carpeta de carga masiva. La decisión ya quedó registrada: reintentá el retiro.",
    "RETIRE_TOKEN": "Falta retirar el archivo de la carpeta de carga masiva: la autorización de Microsoft 365 venció antes de terminar. La decisión ya quedó registrada: reintentá el retiro.",
    "RETIRE_GRAPH": "Falta retirar el archivo de la carpeta de carga masiva: SharePoint no confirmó la operación a tiempo. La decisión ya quedó registrada: reintentá el retiro.",
    "RETIRE_LOCAL": "El Hub no pudo preparar el retiro del archivo. La incorporación ya quedó registrada. Si persiste al reintentar, contactá a soporte; no vuelvas a incorporar el CV.",
    "RETIRE_INTERRUPTED": "Falta retirar el archivo de la carpeta de carga masiva: el retiro quedó interrumpido. La decisión ya quedó registrada: reintentá el retiro o eliminá el original.",
    "DEFINITIVE_MISSING": "No se eliminó el original: no se encontró el CV definitivo en el repositorio «CVs» o su asociación con el candidato.",
    "DEFINITIVE_MISMATCH": "No se eliminó el original: el CV definitivo en «CVs» no coincide con el original (tamaño o contenido). Revisalo antes de eliminar.",
    "ORIGINAL_ELSEWHERE": "No se eliminó el original: ya no está en «carga-masiva» ni en «carga-masiva/incorporados» y el Hub no toca archivos fuera de esas carpetas.",
    "ORIGINAL_CHANGED": "No se eliminó el original: su contenido ya no coincide con el CV incorporado.",
    "RETIRE_PERMISSION": "El Hub no tiene permiso para retirar el archivo de la carpeta de carga masiva. La decisión ya quedó registrada.",
    "RETIRE_CHANGED": "El archivo cambió en SharePoint después de la decisión: no se movió ni se eliminó. Revisalo en la carpeta.",
    "MOVED_TO_DISCARDED": "Movido a «carga-masiva/descartados» (se puede recuperar desde SharePoint).",
    "DELETED_ORIGINAL": "Archivo original eliminado de SharePoint.",
    "SOURCE_GONE": "El archivo ya no estaba en la carpeta de carga masiva.",
}
STOP_TEXT = {
    "PERMISSION_DENIED": "Sin permiso de escritura sobre la carpeta de CVs en SharePoint. No se escribió nada más.",
    "CONFIG_INCOMPLETE": "Falta la configuración de SharePoint o de persistencia del Hub.",
    "PERSISTENCE_ERROR": "La persistencia del Hub no confirmó la escritura: reintentá en un momento.",
    "TOKEN_EXPIRED": "La sesión de Microsoft 365 venció: reconectá y reintentá.",
}


class InboxError(Exception):
    """Error de uso o de configuración, con código estable y mensaje para la persona (sin datos personales)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ═══════════════════ identidades ═══════════════════
def item_key(drive_id: str, item_id: str) -> str:
    return hashlib.sha256(f"{drive_id}:{item_id}".encode("utf-8")).hexdigest()[:16]


def profile_id_for(key: str, sha256: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"cchh-inbox-profile:{key}:{sha256}"))


def document_id_for(profile_id: str, key: str, sha256: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"cchh-inbox-document:{profile_id}:{key}:{sha256}"))


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


# ═══════════════════ zona de entrada (SharePoint) ═══════════════════
_INBOX_TEXT = {
    "CONFIG_INCOMPLETE": "Falta en el Hub la ubicación de la carpeta «Perfiles» o de la carpeta de CVs.",
    "INBOX_UNAVAILABLE": f"No se pudo acceder a la carpeta «Perfiles/{FOLDER_NAME}» de SharePoint con tus permisos actuales.",
    "INBOX_MISSING": f"Falta la carpeta «Perfiles/{FOLDER_NAME}». Pedí que la creen en SharePoint para usar la bandeja.",
    "INBOX_FORBIDDEN": f"Tu usuario no tiene acceso a la carpeta «Perfiles/{FOLDER_NAME}» en SharePoint.",
    "INBOX_INVALID": f"El enlace configurado no apunta a la carpeta «Perfiles/{FOLDER_NAME}» (o apunta al repositorio de CVs del Hub).",
    "TOKEN_EXPIRED": "La sesión de Microsoft 365 venció: reconectá y reintentá.",
    "GRAPH_UNAVAILABLE": "Microsoft 365 no respondió bien. Reintentá en un momento.",
    "NOT_FOUND": "Ese archivo ya no figura en la bandeja.",
}


def _fail(code: str) -> InboxError:
    return InboxError(code, _INBOX_TEXT.get(code, code))


def _graph_code(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return {401: "TOKEN_EXPIRED", 403: "INBOX_FORBIDDEN", 404: "INBOX_MISSING"}.get(exc.code, "GRAPH_UNAVAILABLE")
    return "GRAPH_UNAVAILABLE"


def _items(drive_id: str) -> str:
    return f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive_id, safe='!,')}/items"


def configured() -> bool:
    return bool(config.HC_CV_INBOX_FOLDER_URL and config.HC_SHAREPOINT_SITE_ID and config.HC_SHAREPOINT_DRIVE_ID and config.HC_CV_ROOT_ITEM_ID)


def _parent_name(parent: dict) -> str:
    """Nombre de la carpeta padre según Graph (`parentReference.name`, o el último tramo de `parentReference.path`); vacío si Graph no lo informa."""
    name = str(parent.get("name") or "")
    if not name and parent.get("path"):
        name = urllib.parse.unquote(str(parent["path"]).rstrip("/").rsplit("/", 1)[-1].split(":", 1)[-1])
    return name


def resolve_inbox(token: str, *, sleep=time.sleep) -> dict:
    """`{id, drive_id, web_url, same_drive}` de `Perfiles/carga-masiva`. El link fijo apunta a la carpeta de entrada MISMA: se descubre con `/shares` (como Históricos) y se relee por id para verificar que
    es la carpeta `carga-masiva`, hija de `Perfiles` y fuera de `CVs`. NUNCA se busca una subcarpeta adentro. Lanza `InboxError` (sin acceso, inválida, token vencido)."""
    if not configured():
        raise _fail("CONFIG_INCOMPLETE")
    record, found = diag.discover_sharing_link(config.HC_CV_INBOX_FOLDER_URL, token)
    if not found or not found.get("item_id") or not found.get("drive_id"):
        raise _fail({401: "TOKEN_EXPIRED"}.get(record.get("status"), "INBOX_UNAVAILABLE"))
    drive = found["drive_id"]
    url = f"{_items(drive)}/{urllib.parse.quote(found['item_id'], safe='')}?$select=id,name,folder,webUrl,parentReference"
    try:
        item = inventory.call_with_retry(lambda: microsoft365.graph_get_json(url, token), sleep=sleep)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise _fail(_graph_code(exc)) from None
    parent = item.get("parentReference") or {}
    parent_name = _parent_name(parent)
    if not (isinstance(item.get("folder"), dict) and item.get("id") == found["item_id"] and str(item.get("name") or "").casefold() == FOLDER_NAME.casefold()
            and item["id"] != config.HC_CV_ROOT_ITEM_ID and parent.get("id") != config.HC_CV_ROOT_ITEM_ID
            and (not parent_name or parent_name.casefold() == config.HC_CV_INBOX_PARENT_NAME.casefold())):
        raise _fail("INBOX_INVALID")
    return {"id": item["id"], "drive_id": drive, "web_url": _safe_url(item.get("webUrl")), "same_drive": drive == config.HC_SHAREPOINT_DRIVE_ID}


def list_inbox(token: str, inbox: dict, *, sleep=time.sleep) -> dict:
    """Archivos DIRECTOS de la bandeja (solo metadatos; sin descargar; subcarpetas —incluida `incorporados`— ignoradas). `{files, truncated}`. Paginado con guarda de `nextLink` repetido y tope `MAX_LISTED`."""
    url = f"{_items(inbox['drive_id'])}/{urllib.parse.quote(inbox['id'], safe='')}/children?$select={LIST_SELECT}&$top={PAGE_SIZE}"
    files: list[dict] = []
    seen: set[str] = set()
    truncated = False
    while url:
        try:
            page = inventory.call_with_retry(lambda u=url: microsoft365.graph_get_json(u, token), sleep=sleep)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise _fail(_graph_code(exc)) from None
        for it in page.get("value") or []:
            if "file" not in it or not it.get("id"):
                continue
            if len(files) >= MAX_LISTED:
                truncated = True
                break
            hashes = (it.get("file") or {}).get("hashes") or {}
            algo = next((a for a in ("quickXorHash", "sha256Hash", "sha1Hash") if hashes.get(a)), None)
            files.append({"key": item_key(inbox["drive_id"], it["id"]), "item_id": it["id"], "drive_id": inbox["drive_id"], "parent_id": inbox["id"], "name": str(it.get("name") or ""),
                          "size": int(it.get("size") or 0), "ctag": it.get("cTag"), "etag": it.get("eTag"), "hash": {"algo": algo, "value": hashes[algo]} if algo else None,
                          "modified": it.get("lastModifiedDateTime"), "web_url": _safe_url(it.get("webUrl"))})
        nxt = page.get("@odata.nextLink") or ""
        if truncated or not nxt or nxt in seen:
            break
        seen.add(nxt)
        url = nxt
    return {"files": files, "truncated": truncated}


def _safe_url(value) -> str | None:
    """Enlace para «Ver CV original» (con los permisos de quien lo abre): solo https de SharePoint; nunca se persiste."""
    text = str(value or "")
    host = urllib.parse.urlsplit(text).netloc.lower()
    return text if text.startswith("https://") and (host.endswith(".sharepoint.com") or host == "sp") else None


def _get_item(drive_id: str, item_id: str, token: str) -> dict | None:
    """`driveItem` de la bandeja por id (solo lectura). None = 404 confirmado; otros fallos se propagan (nunca se interpretan como «no existe»)."""
    try:
        return microsoft365.graph_get_json(f"{_items(drive_id)}/{urllib.parse.quote(item_id, safe='')}?$select={LIST_SELECT}", token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _same_content(item: dict, record: dict) -> bool:
    if int(item.get("size") or -1) != int(record.get("size") or -2):
        return False
    if record.get("ctag") and item.get("cTag") and item["cTag"] != record["ctag"]:
        return False
    hashes = (item.get("file") or {}).get("hashes") or {}
    algo, value = (record.get("hash") or {}).get("algo"), (record.get("hash") or {}).get("value")
    return not (algo and value and hashes.get(algo) and hashes[algo] != value)


# ═══════════════════ análisis (nunca crea candidatos ni escribe en SharePoint) ═══════════════════
def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _same_listing(record: dict | None, entry: dict) -> bool:
    return bool(record) and record.get("ctag") == entry.get("ctag") and int(record.get("size") or -1) == int(entry["size"])


def _needs_analysis(entry: dict, record: dict | None, ledger: dict | None) -> bool:
    if ledger and ledger.get("state") in (*ACTIVE, DONE, DISMISSED):
        return False
    if not _same_listing(record, entry):
        return True
    return record.get("state") == "FAILED" and bool(record.get("retryable")) and int(record.get("attempts") or 0) < MAX_ATTEMPTS


def _save_record(entry: dict, **fields) -> dict:
    base = {k: entry.get(k) for k in ("item_id", "drive_id", "parent_id", "name", "size", "ctag", "etag", "hash", "modified")}
    return manifest.put_group(AREA, entry["key"], {**base, **fields})


def _analysis_lock():
    return manifest._interprocess_lock(Path(manifest._run_dir(AREA)) / "analysis", timeout=0)


def analyze(token: str, *, actor: str, max_docs: int = ANALYZE_MAX_DOCS, time_budget: float = ANALYZE_BUDGET_S, clock=time.monotonic, sleep=time.sleep) -> dict:
    """Una tanda de análisis: descarga (solo lectura) y extrae los archivos pendientes o modificados. Reanudable. Devuelve conteos (`analyzed`, `failed`, `remaining`) y el motivo de corte."""
    started = clock()
    limit = max(1, min(int(max_docs), ANALYZE_MAX_DOCS))
    try:
        with _analysis_lock():
            inbox = resolve_inbox(token, sleep=sleep)
            listing = list_inbox(token, inbox, sleep=sleep)
            records = {g["content_key"]: g for g in manifest.list_groups(AREA)}
            ledgers = manifest.list_ledgers(AREA)
            todo = [e for e in listing["files"] if _needs_analysis(e, records.get(e["key"]), ledgers.get(e["key"]))]
            done = failed = 0
            stopped = None
            for entry in todo:
                if done + failed >= limit:
                    stopped = "BATCH_LIMIT"
                    break
                if clock() - started >= time_budget:
                    stopped = "TIME_BUDGET"
                    break
                outcome = _analyze_one(entry, records.get(entry["key"]), token, actor, sleep)
                if outcome in ("TOKEN_EXPIRED", "PERMISSION_DENIED"):
                    stopped = outcome
                    break
                done, failed = (done + 1, failed) if outcome == "ok" else (done, failed + 1)
            remaining = len(todo) - done - failed
    except manifest.ManifestBusyError:
        return {"analyzed": 0, "failed": 0, "remaining": None, "stopped_reason": "BUSY"}
    except manifest.ManifestDurabilityError:
        return {"analyzed": 0, "failed": 0, "remaining": None, "stopped_reason": "PERSISTENCE_ERROR"}
    _log.info("cchh.cv_inbox.analyzed analyzed=%s failed=%s remaining=%s stopped=%s", done, failed, remaining, stopped)
    return {"analyzed": done, "failed": failed, "remaining": remaining, "stopped_reason": stopped}


def _analyze_one(entry: dict, previous: dict | None, token: str, actor: str, sleep) -> str:
    attempts = int((previous or {}).get("attempts") or 0) + 1 if _same_listing(previous, entry) else 1

    def failure(code: str, retryable: bool = False) -> str:
        _save_record(entry, state="FAILED", failure=code, retryable=retryable, attempts=attempts, extraction=None, sha256=None, analyzed_at=manifest.now_iso())
        manifest.append_audit(AREA, entry["key"], "ANALYSIS_FAILED", code=code)
        return "failed"

    if _ext(entry["name"]) not in profile_document_validation.ACCEPTED_CONTENT_TYPES:
        return failure("FORMAT_INVALID")
    if entry["size"] > MAX_FILE_BYTES:
        return failure("OVERSIZE")
    try:
        data = pilot._download(entry["item_id"], entry["drive_id"], token, sleep)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return "TOKEN_EXPIRED"
        code = {403: "DOWNLOAD_FORBIDDEN", 404: "SOURCE_MISSING"}.get(exc.code, "DOWNLOAD_ERROR")
        return failure(code, retryable=code == "DOWNLOAD_ERROR")
    except (urllib.error.URLError, OSError, ValueError):
        return failure("DOWNLOAD_ERROR", retryable=True)
    if len(data) > MAX_FILE_BYTES:
        return failure("OVERSIZE")
    try:
        profile_document_validation.validate_upload(entry["name"], data)
    except profile_document_validation.ProfileDocumentValidationError:
        return failure("FORMAT_INVALID")
    digest = hashlib.sha256(data).hexdigest()
    extraction = cv_extract.extract(entry["name"], data)
    del data
    _save_record(entry, state="ANALYZED", failure=None, retryable=False, attempts=attempts, extraction=extraction, sha256=digest, analyzed_at=manifest.now_iso(), analyzed_by=actor)
    manifest.append_audit(AREA, entry["key"], "ANALYZED", code=extraction.get("failure"))
    return "ok"


# ═══════════════════ clasificación (pura: sin escribir nada) ═══════════════════
def _draft_applies(record: dict, draft: dict | None) -> bool:
    """Una revisión vale solo para el contenido sobre el que se hizo: si el archivo cambió (otro sha256), se vuelve a partir de la extracción nueva."""
    return bool(draft and draft.get("fields") and draft.get("sha256") and draft["sha256"] == record.get("sha256"))


def _inbox_proposal(ext: dict | None) -> dict | None:
    """Propuesta de identidad para la BANDEJA. Parte de la del extractor (la misma de Históricos); si el extractor no propuso porque la identidad no está corroborada, la bandeja igual propone
    (Capital Humano revisa cada alta y puede corregirla): (a) la ÚNICA identidad en la que coinciden el texto y el nombre de archivo, aunque haya otras líneas que parezcan nombres; o (b) la única
    identidad encontrada. Nunca con una falla de lectura ni con dos identidades igual de fuertes."""
    ext = ext or {}
    proposal = pilot._proposal(ext)
    if proposal or ext.get("failure"):
        return proposal
    decision = ext.get("name_decision") or {}
    alternatives = [a for a in decision.get("alternatives") or [] if a.get("tokens")]
    agree = [a for a in alternatives if "filename" in (a.get("sources") or []) and "text" in (a.get("sources") or [])]
    pick = agree[0] if len(agree) == 1 else alternatives[0] if len(alternatives) == 1 else None
    if not pick:
        return None
    first, last, _uncertain = cv_extract._split(str(pick["tokens"]).split())
    if not (first and last):
        return None
    return {"first_name": first, "last_name": last, "rule": "INBOX_AGREEMENT" if pick in agree else "INBOX_SINGLE", "state": pick.get("state")}


def _effective_fields(record: dict, draft: dict | None) -> tuple[dict, str]:
    if _draft_applies(record, draft):
        return {f: draft["fields"].get(f, "") for f in FIELDS}, "review"
    ext = record.get("extraction") or {}
    pre = review._prefill(ext) if ext else {}
    proposal = _inbox_proposal(ext) or {}
    return {"first_name": proposal.get("first_name") or "", "last_name": proposal.get("last_name") or "", "email": pre.get("email", ""), "phone": pre.get("phone", ""), "profile_skills": ""}, "extraction"


def _evidence(record: dict) -> dict:
    ext = record.get("extraction") or {}
    pre = review._prefill(ext) if ext else {"first_name": ""}
    decision = ext.get("name_decision") or {}
    state = decision.get("state") or ext.get("name_state")
    if ext.get("failure"):
        label, level = "No se pudo leer el documento", "none"
    elif not _inbox_proposal(ext):
        label, level = "Sin nombre con evidencia suficiente", "none"
    elif pre.get("first_name"):
        label, level = "Corroborado por un dato independiente", "strong"
    elif (_inbox_proposal(ext) or {}).get("rule") == "INBOX_AGREEMENT":
        label, level = "Coincide con el nombre del archivo", "weak"
    elif state == "DECLARED":
        label, level = "Declarado en el CV, sin corroborar", "weak"
    else:
        label, level = "Inferido, sin corroborar", "weak"
    proposed = decision.get("proposed")
    return {"label": label, "level": level, "safe": bool(pre.get("first_name")), "emails": pre.get("emails") or [],
            "identities": [review._identity_view(a, i == proposed) for i, a in enumerate(decision.get("alternatives") or [])]}


def _relevant(matches: list[dict]) -> list[dict]:
    return [m for m in matches if m["strength"] in (cv_reconcile.STRONG, cv_reconcile.PROBABLE)]


def _shares_word(fields: dict, profile: dict) -> bool:
    return bool(cv_reconcile._tokens(fields.get("first_name"), fields.get("last_name")) & cv_reconcile._tokens(profile.get("first_name"), profile.get("last_name")))


class Context:
    """Lo que hace falta para clasificar filas en UNA vista (por request): listado, registros, borradores, libros, Banco vigente indexado y hashes ya incorporados."""

    def __init__(self, listing: list[dict] | None):
        self.listing = {e["key"]: e for e in (listing or [])}
        self.records = {g["content_key"]: g for g in manifest.list_groups(AREA)}
        self.drafts = manifest.list_drafts(AREA)
        self.ledgers = manifest.list_ledgers(AREA)
        self.profiles = {p["profile_id"]: p for p in profile_repository.list() if not p.get("deleted_at") and not profile_repository.is_locked(p)}
        self.index = CandidateIndex(list(self.profiles.values()))
        try:
            self.processed = profile_document_repository.content_sha256_index()
        except profile_document_repository.ProfileDocumentStorageError:
            self.processed = set()
        self.processed |= {e.get("sha256") for e in self.ledgers.values() if e.get("state") == DONE and e.get("sha256")}
        self._persons: dict[str, dict] | None = None

    def persons(self) -> dict[str, dict]:
        """Identidad efectiva de cada CV ABIERTO y analizado de la bandeja (para detectar la misma persona dentro del propio lote)."""
        if self._persons is None:
            self._persons = {}
            for key, rec in self.records.items():
                state = (self.ledgers.get(key) or {}).get("state")
                if rec.get("state") != "ANALYZED" or state in (DONE, DISMISSED) or (key not in self.listing and state not in ACTIVE):
                    continue
                fields, _src = _effective_fields(rec, self.drafts.get(key))
                if any(fields.get(f) for f in ("first_name", "last_name", "email", "phone")):
                    self._persons[key] = fields
        return self._persons

    def peers(self, key: str, fields: dict) -> list[dict]:
        out = []
        sha = (self.records.get(key) or {}).get("sha256")
        for other, person in self.persons().items():
            if other == key or (sha and (self.records.get(other) or {}).get("sha256") == sha):
                continue
            level = cv_reconcile.strength(cv_reconcile.signals_between(fields, person))
            if level in (cv_reconcile.STRONG, cv_reconcile.PROBABLE):
                out.append({"key": other, "strength": level, "name": f"{person.get('first_name', '')} {person.get('last_name', '')}".strip() or "(sin nombre)",
                            "file": (self.records.get(other) or {}).get("name") or ""})
        return sorted(out, key=lambda p: p["key"])

    def duplicate_of(self, key: str) -> str | None:
        """Otro archivo de la bandeja con el MISMO contenido que tiene prioridad (incorporándose, o analizado antes): este queda como repetido."""
        sha = (self.records.get(key) or {}).get("sha256")
        if not sha:
            return None
        same = [k for k, r in self.records.items() if r.get("sha256") == sha and (k in self.listing or (self.ledgers.get(k) or {}).get("state") in ACTIVE)
                and (self.ledgers.get(k) or {}).get("state") not in (DISMISSED, DONE)]
        same.sort(key=lambda k: (0 if (self.ledgers.get(k) or {}).get("state") in ACTIVE else 1, self.records[k].get("analyzed_at") or "", k))
        return same[0] if same and same[0] != key else None


def _match_view(ctx: Context, m: dict) -> dict:
    p = ctx.profiles.get(m["profile_id"]) or {}
    return {"profile_id": m["profile_id"], "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(), "email": p.get("email") or "", "phone": p.get("phone") or "",
            "strength": m["strength"], "signals": m["signals"], "revision": int(p.get("revision") or 1)}


def fingerprint(record: dict, matches: list[dict], peers: list[dict]) -> str:
    """Huella de lo que la persona vio: contenido analizado + universo de coincidencias relevantes (perfil, revisión, fuerza, señales) + CVs de la misma persona en la bandeja."""
    return _sha({"sha": record.get("sha256"), "ctag": record.get("ctag"), "matches": [(m["profile_id"], m["revision"], m["strength"], sorted(m["signals"])) for m in matches],
                 "peers": [p["key"] for p in peers]})[:24]


def _status(row: dict, status: str, *, reasons: list | None = None, op: str | None = None, profile_id: str | None = None, retire: dict | None = None) -> dict:
    label, badge = STATUS_VIEW[status]
    row.update({"status": status, "label": label, "badge": badge})
    # Retiro físico pendiente después de una decisión durable: la bandeja lo reintenta SOLA (acotado a AUTO_RETIRE_MAX intentos en total) salvo que falte permiso o el archivo haya cambiado,
    # casos que un reintento no resuelve. Agotados los intentos, queda el botón manual y el motivo en la fila.
    info = retire or {}
    row["auto_retire"] = bool(row.get("can_retire")) and int(info.get("attempts") or 0) < AUTO_RETIRE_MAX and info.get("code") not in ("PERMISSION", "CHANGED")
    if reasons is not None:
        row["reasons"] = [r for r in reasons if r]
    if op and not row.get("op"):
        row["op"] = op
    if profile_id:
        row["profile_id"] = profile_id
    row["reason_text"] = [REASON_TEXT.get(r, DISMISS_REASONS.get(r, r)) for r in row["reasons"]]
    return row


def classify(ctx: Context, key: str) -> dict | None:
    """Fila de la bandeja con su estado DERIVADO, la identidad efectiva, la evidencia, las coincidencias y si puede ir en una aceptación por lote. PURA (no escribe)."""
    record, entry, ledger, draft = ctx.records.get(key), ctx.listing.get(key), ctx.ledgers.get(key), ctx.drafts.get(key)
    state = (ledger or {}).get("state")
    base = entry or record or {}
    row = {"key": key, "name": base.get("name") or "", "web_url": (entry or {}).get("web_url"), "size_kb": max(1, int(base.get("size") or 0) // 1024), "item_id": base.get("item_id"), "drive_id": base.get("drive_id"), "listed": entry is not None,
           "batch_ok": False, "can_accept": False, "can_dismiss": False, "can_reopen": False, "can_retry": False, "can_retire": False, "can_purge": False, "op": None, "target": None, "reasons": [], "matches": [], "peers": [],
           "fields": None, "evidence": None, "fingerprint": None, "draft_revision": int((draft or {}).get("revision") or 0), "field_source": None}
    if state == DONE:
        retire_info = ledger.get("retire") or {}
        row["can_retire"] = entry is not None and retire_info.get("state") != RETIRE_DONE
        row["can_purge"] = not (retire_info.get("mode") == MODE_DELETE and retire_info.get("state") == RETIRE_DONE)
        # el original ya no existe (lo eliminó el Hub o ya no estaba al retirarlo): no queda nada de esta fila en la carpeta de carga masiva
        row["original_removed"] = retire_info.get("state") == RETIRE_DONE and (retire_info.get("mode") == MODE_DELETE or retire_info.get("code") in ("GONE", "ELSEWHERE"))
        return _status(row, INCORPORATED, reasons=[retire_reason(ledger)] if row["can_retire"] else [], profile_id=ledger.get("profile_id"), retire=retire_info,
                       op=(ledger.get("decision") or {}).get("op"))
    if state in ACTIVE:
        reason = ledger.get("reason") or {UPLOADING: "UPLOAD_PENDING_CONFIRMATION", PROFILE_READY: "PROFILE_CREATED_CV_PENDING"}.get(state)
        row.update({"can_retry": True, "can_reopen": not ledger.get("effects")})
        return _status(row, ACCEPTING, reasons=[reason], profile_id=ledger.get("profile_id"))
    if state == DISMISSED:
        retired = (ledger.get("retire") or {}).get("state") == RETIRE_DONE
        row.update({"can_retire": entry is not None and bool(ledger.get("retire")) and not retired, "can_reopen": entry is not None or not retired,
                    "retire_mode": (ledger.get("retire") or {}).get("mode")})
        return _status(row, DISCARDED, reasons=[ledger.get("dismiss_reason"), retire_reason(ledger)], retire=ledger.get("retire"))
    if entry is None:
        if record is None:
            return None
        row["can_dismiss"] = True
        return _status(row, MISSING, reasons=["SOURCE_MISSING"])
    row["can_dismiss"] = True
    if record is None:
        return _status(row, PENDING)
    if not _same_listing(record, entry):
        return _status(row, MODIFIED, reasons=["SOURCE_CHANGED"])
    if record.get("state") == "FAILED":
        retry = bool(record.get("retryable")) and int(record.get("attempts") or 0) < MAX_ATTEMPTS
        return _status(row, ERROR_RETRY if retry else ERROR, reasons=[record.get("failure")])
    if record.get("sha256") in ctx.processed:
        return _status(row, PROCESSED)
    dup = ctx.duplicate_of(key)
    if dup:
        row["duplicate_of"] = dup
        return _status(row, DUPLICATE)
    fields, source = _effective_fields(record, draft)
    evidence = _evidence(record)
    matches = [_match_view(ctx, m) for m in _relevant(ctx.index.reconcile(fields))]
    peers = ctx.peers(key, fields)
    row.update({"fields": fields, "field_source": source, "evidence": evidence, "matches": matches, "peers": peers, "fingerprint": fingerprint(record, matches, peers), "can_accept": True})
    resolution = ((draft or {}).get("resolution") or {}) if _draft_applies(record, draft) else {}
    if draft and draft.get("fields") and not _draft_applies(record, draft):
        row["reasons"].append("RESOLUTION_STALE")
    if resolution.get("op"):
        valid = resolution.get("sha256") == record.get("sha256") and resolution.get("fingerprint") == row["fingerprint"]
        if valid and (resolution["op"] == "create" or resolution.get("profile_id") in {m["profile_id"] for m in matches}):
            if fields["first_name"] and fields["last_name"]:
                row.update({"op": resolution["op"], "target": resolution.get("profile_id"), "batch_ok": resolution["op"] == "link" or bool(fields["email"])})
                return _status(row, READY, reasons=["RESOLVED"] + ([] if row["batch_ok"] else ["NO_EMAIL"]))
        row["reasons"].append("RESOLUTION_STALE")
    if not (fields["first_name"] and fields["last_name"]):
        return _status(row, INCOMPLETE, reasons=row["reasons"] + ["EXTRACTION_FAILED" if (record.get("extraction") or {}).get("failure") else "NO_NAME"])
    if peers:
        return _status(row, REVIEW, reasons=row["reasons"] + ["SAME_PERSON_IN_INBOX"])
    if not matches:
        row.update({"op": "create", "batch_ok": bool(fields["email"]) and not row["reasons"]})
        return _status(row, READY, reasons=row["reasons"] + ([] if fields["email"] else ["NO_EMAIL"]))
    only = matches[0]
    if len(matches) == 1 and cv_reconcile.EMAIL_EXACT in only["signals"] and _shares_word(fields, ctx.profiles.get(only["profile_id"]) or {}) and not row["reasons"]:
        row.update({"op": "link", "target": only["profile_id"], "batch_ok": True})
        return _status(row, READY, reasons=["LINK_EMAIL"])
    return _status(row, REVIEW, reasons=row["reasons"] + ["MATCHES"])


_ORDER = {s: i for i, s in enumerate((ACCEPTING, READY, REVIEW, INCOMPLETE, ERROR_RETRY, MODIFIED, PENDING, ERROR, PROCESSED, DUPLICATE, MISSING, DISCARDED, INCORPORATED))}


def _residual(row: dict) -> bool:
    """Un CV incorporado que ya no tiene nada en la carpeta de carga masiva sale de la vista: (a) su original fue ELIMINADO (o ya no existía al retirarlo), o (b) su original ya no está en la carpeta y su
    candidato ya no existe en el Banco (dado de baja o eliminado). Su libro se conserva como auditoría. Si el original sigue en la carpeta, la fila se mantiene (hay un archivo que decidir). Ante una
    lectura dudosa del Banco, la fila se muestra."""
    if row["status"] != INCORPORATED or row["listed"]:
        return False
    if row.get("original_removed"):
        return True
    if not row.get("profile_id"):
        return False
    try:
        profile = profile_repository.get(row["profile_id"])
    except Exception:  # noqa: BLE001 — sin certeza no se oculta nada
        return False
    return profile is None or bool(profile.get("deleted_at"))


def overview(token: str) -> dict:
    """Vista completa de la bandeja: carpeta, filas clasificadas y conteos. Lee SharePoint (listado) y el Hub; no escribe nada."""
    inbox = resolve_inbox(token)
    listing = list_inbox(token, inbox)
    ctx = Context(listing["files"])
    keys = set(ctx.listing) | set(ctx.records) | {k for k, e in ctx.ledgers.items() if e.get("state") in ACTIVE}
    recent = set()
    for final in (DONE, DISMISSED):                                                 # historial reciente de decididos que ya salieron de la carpeta (filtros «Incorporados» / «Descartados»)
        recent |= set(sorted((k for k, e in ctx.ledgers.items() if e.get("state") == final), key=lambda k: ctx.ledgers[k].get("updated_at") or "", reverse=True)[:RECENT_DONE])
    keys |= recent
    rows = [r for r in (classify(ctx, k) for k in keys) if r and (r["status"] not in (INCORPORATED, DISCARDED) or r["key"] in recent or r["listed"])]
    rows = [r for r in rows if not _residual(r)]
    rows.sort(key=lambda r: (_ORDER[r["status"]], r["name"].casefold(), r["key"]))
    counts = {s: 0 for s in STATUS_VIEW}
    for r in rows:
        counts[r["status"]] += 1
    return {"inbox": inbox, "rows": rows, "counts": counts, "truncated": listing["truncated"], "pending_analysis": counts[PENDING] + counts[MODIFIED] + counts[ERROR_RETRY],
            "batch_ready": sum(1 for r in rows if r["batch_ok"]), "retire_pending": sum(1 for r in rows if r["can_retire"])}


def current_listing(token: str) -> tuple[dict, list[dict]]:
    inbox = resolve_inbox(token)
    return inbox, list_inbox(token, inbox)["files"]


def row_for(token: str, key: str, listing: list[dict] | None = None) -> tuple[dict, Context]:
    """Fila vigente de UN archivo (con el listado actual de la bandeja, o el que ya leyó el llamador en este mismo envío)."""
    ctx = Context(current_listing(token)[1] if listing is None else listing)
    row = classify(ctx, key)
    if row is None:
        raise _fail("NOT_FOUND")
    return row, ctx


# ═══════════════════ revisión humana (borrador durable) ═══════════════════
def clean_fields(raw: dict) -> dict:
    base = drafts.clean({**{k: raw.get(k, "") for k in ("first_name", "last_name", "email", "phone")}, "notes": ""})
    drafts.validate(base)
    skills = " ".join(str(raw.get("profile_skills") or "").split())
    if drafts._CONTROL.search(skills) or len(skills) > SKILLS_MAX:
        raise drafts.DraftError(f"El campo «perfil / skills» admite hasta {SKILLS_MAX} caracteres sin caracteres de control.")
    return {**{k: base[k] for k in ("first_name", "last_name", "email", "phone")}, "profile_skills": skills}


def save_review(key: str, raw: dict, *, actor: str, expected_revision, resolution: dict | None = None) -> dict:
    """Guarda (con control optimista) los datos revisados y, opcionalmente, la DECISIÓN humana. No crea ni vincula nada. Lanza `DraftError`/`DraftConflict`."""
    fields = clean_fields(raw)
    sha = (manifest.get_group(AREA, key) or {}).get("sha256")
    if not sha:
        raise drafts.DraftError("Este archivo todavía no se analizó (o cambió): analizalo antes de revisarlo.")
    if (manifest.get_ledger(AREA, key) or {}).get("state") in (*ACTIVE, DONE):
        raise drafts.DraftError("Este CV ya tiene una incorporación en curso o terminada: no se puede editar su revisión.")
    expected = drafts._parse_revision(expected_revision)

    def build(previous):
        current = int((previous or {}).get("revision") or 0)
        if current != expected:
            raise drafts.DraftConflict(previous)
        now = manifest.now_iso()
        return {"fields": fields, "sha256": sha, "resolution": resolution or None, "revision": current + 1, "created_at": (previous or {}).get("created_at") or now,
                "created_by": (previous or {}).get("created_by") or actor, "updated_at": now, "updated_by": actor}
    return manifest.update_draft(AREA, key, build)


# ═══════════════════ aceptación ═══════════════════
ACK_MATCHES, ACK_NO_EMAIL = "matches", "no_email"


def _decision_problem(row: dict, op: str, target: str | None, acks: set, *, batch: bool) -> str | None:
    if row["status"] not in (READY, REVIEW, INCOMPLETE) or not row["can_accept"]:
        return "Este CV no se puede aceptar en su estado actual."
    fields = row["fields"] or {}
    if not (fields.get("first_name") and fields.get("last_name")):
        return "Completá nombre y apellido antes de aceptar."
    if batch and not row["batch_ok"]:
        return "Requiere una resolución individual."
    if op not in ("create", "link"):
        return "Elegí una decisión: crear un candidato nuevo o vincular a uno existente."
    if (row["matches"] or row["peers"]) and ACK_MATCHES not in acks and not batch:            # en el lote solo entran filas seguras o ya resueltas por una persona (que confirmó al resolver)
        return "Hay posibles coincidencias: confirmá que las revisaste antes de decidir."
    if op == "link":
        return None if target in {m["profile_id"] for m in row["matches"]} else "Elegí uno de los perfiles coincidentes para vincular."
    if not fields.get("email") and (batch or ACK_NO_EMAIL not in acks):
        return "Sin email: confirmá explícitamente el alta, de a uno (la detección de duplicados queda solo por nombre y teléfono)."
    return None


def _prepare(key: str, token: str, *, actor: str, raw: dict, op: str, target: str | None, fingerprint_seen: str, expected_revision, acks: set) -> tuple[dict | None, Context | None, dict | None]:
    """Guarda los datos revisados (durable), recalcula la fila con ellos y, si nada relevante cambió respecto de lo que la persona vio y la decisión es admisible, guarda también la DECISIÓN con la huella vigente.
    Devuelve `(fila, contexto, None)` o `(None, None, error)`. Requiere el candado del libro de `key`."""
    saved = save_review(key, raw, actor=actor, expected_revision=expected_revision)
    row, ctx = row_for(token, key)
    if fingerprint_seen and row["fingerprint"] != fingerprint_seen:
        return None, None, {"ok": False, "stale": True, "error": "Las coincidencias cambiaron con los datos revisados: revisalas antes de confirmar. Tus datos quedaron guardados."}
    problem = _decision_problem(row, op, target, acks, batch=False)
    if problem:
        return None, None, {"ok": False, "error": problem + " Tus datos quedaron guardados."}
    save_review(key, raw, actor=actor, expected_revision=saved["revision"],
                resolution={"op": op, "profile_id": target if op == "link" else None, "fingerprint": row["fingerprint"], "sha256": (ctx.records.get(key) or {}).get("sha256"), "acks": sorted(acks)})
    return row, ctx, None


def save_draft(key: str, token: str, *, actor: str, raw: dict, expected_revision) -> dict:
    """«Guardar» sin decisión: solo los datos revisados."""
    with manifest.ledger_lock(AREA, key, timeout=0):
        row_for(token, key)
        save_review(key, raw, actor=actor, expected_revision=expected_revision)
    return {"ok": True}


def save_decision(key: str, token: str, *, actor: str, raw: dict, op: str, target_profile_id: str = "", fingerprint_seen: str = "", expected_revision=None, acks=()) -> dict:
    """«Guardar revisión» con una decisión (crear / vincular): queda resuelta por una persona y, si corresponde, habilitada para la aceptación por lote. No crea ni vincula nada."""
    with manifest.ledger_lock(AREA, key, timeout=0):
        _row, _ctx, error = _prepare(key, token, actor=actor, raw=raw, op=op, target=(target_profile_id or "").strip() or None, fingerprint_seen=fingerprint_seen,
                                     expected_revision=expected_revision, acks=set(acks))
    return error or {"ok": True}


def accept_one(key: str, token: str, *, actor: str, raw: dict | None = None, op: str = "", target_profile_id: str = "", fingerprint_seen: str = "", expected_revision=None, acks=()) -> dict:
    """Aceptación INDIVIDUAL desde el panel de revisión (guarda la revisión y la decisión, revalida e incorpora), o REINTENTO de una incorporación sin terminar (`raw=None`). Devuelve `{ok, outcome|error|stale|conflict}`."""
    with manifest.ledger_lock(AREA, key, timeout=0):
        row, ctx = row_for(token, key)
        if row["status"] == ACCEPTING:
            return _execute(key, (manifest.get_ledger(AREA, key) or {}).get("decision") or {}, token, actor)
        if raw is None:
            return {"ok": False, "error": "Este CV no tiene una incorporación para reintentar."}
        target = (target_profile_id or "").strip() or None
        row, ctx, error = _prepare(key, token, actor=actor, raw=raw, op=op, target=target, fingerprint_seen=fingerprint_seen, expected_revision=expected_revision, acks=set(acks))
        if error:
            return error
        return _execute(key, _new_decision(row, ctx, op, target, actor), token, actor)


def accept_batch(selection: list[tuple[str, str]], token: str, *, actor: str, time_budget: float = ACCEPT_BUDGET_S, clock=time.monotonic) -> dict:
    """Aceptación COLECTIVA: solo filas seguras o ya resueltas por una persona (`batch_ok`) cuya huella coincide con la que se vio. Cada fila es independiente (su propio candado y su libro). Devuelve conteos y el
    resultado por fila; lo no procesado por tiempo queda pendiente, sin efectos."""
    started = clock()
    results: list[dict] = []
    stop = None
    listing = current_listing(token)[1]
    for key, fp in selection[:ACCEPT_MAX_SELECTION]:
        if stop or clock() - started >= time_budget:
            results.append({"key": key, "outcome": "PENDING", "message": STOP_TEXT.get(stop, "No se llegó a procesar en este envío: volvé a aceptarlo.")})
            continue
        try:
            with manifest.ledger_lock(AREA, key, timeout=0):
                row, ctx = row_for(token, key, listing)
                if row["status"] == ACCEPTING:
                    res = _execute(key, (manifest.get_ledger(AREA, key) or {}).get("decision") or {}, token, actor)
                elif row.get("fingerprint") != fp:
                    res = {"ok": False, "stale": True, "error": "Cambió algo desde que lo viste: revisalo de nuevo."}
                else:
                    problem = _decision_problem(row, row["op"] or "", row.get("target"), set(), batch=True)
                    if problem:
                        res = {"ok": False, "error": problem}
                    else:
                        row["fields"] = _snapshot_review(key, row, ctx, actor)
                        res = _execute(key, _new_decision(row, ctx, row["op"], row.get("target"), actor), token, actor)
        except manifest.ManifestBusyError:
            res = {"ok": False, "error": "Otra persona está procesando este CV en este momento."}
        except (drafts.DraftError, drafts.DraftConflict):
            res = {"ok": False, "error": "No se pudo guardar la revisión de este CV: abrilo y revisalo."}
        except manifest.ManifestDurabilityError:
            res = {"ok": False, "stop": "PERSISTENCE_ERROR", "error": STOP_TEXT["PERSISTENCE_ERROR"]}
        except InboxError as exc:
            res = {"ok": False, "error": str(exc), **({"stop": exc.code} if exc.code == "TOKEN_EXPIRED" else {})}
        stop = stop or res.get("stop")
        results.append({"key": key, "outcome": res.get("outcome") or ("DONE" if res.get("ok") else "ERROR"), "message": res.get("error") or "", "profile_id": res.get("profile_id"),
                        "retire": res.get("retire")})
    counts = {"done": sum(1 for r in results if r["outcome"] == "DONE"), "pending": sum(1 for r in results if r["outcome"] == "PENDING"),
              "errors": sum(1 for r in results if r["outcome"] not in ("DONE", "PENDING"))}
    _log.info("cchh.cv_inbox.batch_accept done=%s pending=%s errors=%s", counts["done"], counts["pending"], counts["errors"])
    return {"results": results, "counts": counts, "stopped_reason": stop}


def _snapshot_review(key: str, row: dict, ctx: Context, actor: str) -> dict:
    """Una aceptación por lote deja la revisión durable (los datos que se incorporan) antes de cualquier efecto. Devuelve los datos tal como quedaron guardados."""
    draft = manifest.get_draft(AREA, key)
    if _draft_applies(ctx.records.get(key) or {}, draft):
        return {f: draft["fields"].get(f, "") for f in FIELDS}
    saved = save_review(key, row["fields"], actor=actor, expected_revision=int((draft or {}).get("revision") or 0),
                        resolution={"op": row["op"], "profile_id": row.get("target"), "fingerprint": row["fingerprint"], "sha256": (ctx.records.get(key) or {}).get("sha256"), "acks": []})
    return {f: saved["fields"].get(f, "") for f in FIELDS}


def _new_decision(row: dict, ctx: Context, op: str, target: str | None, actor: str) -> dict:
    sha = ctx.records[row["key"]]["sha256"]
    pid = profile_id_for(row["key"], sha) if op == "create" else target
    return {"op": op, "profile_id": pid, "target_revision": int((ctx.profiles.get(target) or {}).get("revision") or 1) if op == "link" else None,
            "document_id": document_id_for(pid, row["key"], sha), "sha256": sha, "confirmed_sha256": _sha({"op": op, "pid": pid, "fields": row["fields"]}), "by": actor, "at": manifest.now_iso()}


def _same(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("op", "profile_id", "confirmed_sha256"))


def _save_ledger(key: str, entry: dict, event: str | None = None) -> dict:
    if event:
        entry["history"] = [*entry.get("history", []), {"at": manifest.now_iso(), "event": event, "state": entry["state"], **({"reason": entry["reason"]} if entry.get("reason") else {})}][-20:]
    saved = manifest.put_ledger(AREA, key, entry)
    if event:
        decision = entry.get("decision") or {}
        manifest.append_audit(AREA, key, "INBOX_" + event, code=entry.get("reason"), op=decision.get("op"), state=entry["state"], by=decision.get("by") or entry.get("by"))
    return saved


def _execute(key: str, decision: dict, token: str, actor: str) -> dict:
    """Incorporación de UN CV, reanudable desde el libro. Requiere el candado del libro de `key` (lo toma el llamador)."""
    entry = manifest.get_ledger(AREA, key) or {}
    record = manifest.get_group(AREA, key) or {}
    if entry.get("state") == DONE:
        if _same(entry.get("decision") or {}, decision):
            return {"ok": True, "outcome": "DONE", "idempotent": True, "profile_id": entry.get("profile_id"), "retire": (entry.get("retire") or {}).get("state")}
        return {"ok": False, "conflict": True, "error": "Este CV ya se incorporó con otra decisión."}
    if entry.get("state") in ACTIVE:
        if not decision or not _same(entry.get("decision") or {}, decision):
            return {"ok": False, "conflict": True, "error": "Este CV tiene una incorporación sin terminar con otra decisión: reintentala o cancelala."}
        decision = entry["decision"]
    else:
        if not decision.get("op") or not record.get("sha256"):
            return {"ok": False, "error": "No hay una decisión válida para este CV."}
        try:
            current = _get_item(record["drive_id"], record["item_id"], token)
        except Exception as exc:                                                    # noqa: BLE001 — se traduce con la tabla común
            return _failure(key, None, _as_graph_error(exc))
        if current is None or (current.get("parentReference") or {}).get("id") != record.get("parent_id"):
            return {"ok": False, "revalidate": True, "error": REASON_TEXT["SOURCE_MISSING"]}
        if not _same_content(current, record):
            return {"ok": False, "revalidate": True, "error": REASON_TEXT["SOURCE_CHANGED"]}
        entry = {"state": ACCEPTED, "decision": decision, "profile_id": decision["profile_id"], "document_id": decision["document_id"], "sha256": record["sha256"], "effects": False, "attempts": 0,
                 "reason": None, "last_error": None, "history": entry.get("history", [])}
        try:
            _save_ledger(key, entry, "ACCEPTED")
        except manifest.ManifestError:
            return {"ok": False, "stop": "PERSISTENCE_ERROR", "error": STOP_TEXT["PERSISTENCE_ERROR"]}
    before = bool(entry.get("effects"))
    try:
        if entry["state"] in (ACCEPTED, BLOCKED) and entry.get("stage") != "document":
            blocked = _ensure_profile(key, entry, record)
            if blocked:
                return _block(key, entry, blocked)
            entry.update({"state": PROFILE_READY, "stage": "document", "effects": before or entry["decision"]["op"] == "create", "reason": None})
            _save_ledger(key, entry, "PROFILE_READY")
            before = entry["effects"]
        existing = profile_document_repository.get(entry["document_id"])
        if existing is None:
            entry.update({"state": UPLOADING, "effects": True, "attempts": int(entry.get("attempts") or 0) + 1, "reason": None})
            _save_ledger(key, entry)
            profile = profile_repository.get(entry["profile_id"]) or {}
            group = {"size": record["size"], "hash": record.get("hash"), "sha256": record["sha256"]}
            state, reason, info = core.reconcile_or_upload(
                profile_id=entry["profile_id"], document_id=entry["document_id"], group=group, filename=record["name"],
                fetch=lambda: pilot._download(record["item_id"], record["drive_id"], token, time.sleep), token=token, actor=actor,
                first_name=profile.get("first_name"), last_name=profile.get("last_name"),
                extra_fields={"import_origin": {"kind": ORIGIN_KIND, "item_key": key, "source_item_id": record["item_id"], "sha256": record["sha256"]}})
            if state != core.DONE:
                if reason == "SOURCE_CHANGED":
                    entry["effects"] = before                                       # no se subió nada
                return _block(key, entry, reason)
            entry.update(info)
        elif existing.get("profile_id") != entry["profile_id"]:
            return _block(key, entry, "DOC_ID_CONFLICT")
        else:
            entry.update({"item_id": existing.get("item_id"), "folder_item_id": existing.get("folder_item_id")})
        entry.update({"state": DONE, "effects": True, "reason": None, "last_error": None, "retire": {"state": RETIRE_PENDING}})
        _save_ledger(key, entry, "DONE")
        _log.info("cchh.cv_inbox.incorporated op=%s", entry["decision"]["op"])
    except urllib.error.HTTPError as exc:                                            # descarga del origen (solo lectura) durante la subida
        if exc.code == 404:
            entry["effects"] = before
            return _block(key, entry, "SOURCE_MISSING")
        return _failure(key, entry, _as_graph_error(exc))
    except Exception as exc:                                                        # noqa: BLE001 — tabla común; lo desconocido se relanza
        return _failure(key, entry, exc)
    retire = _retire(key, entry, record, token)
    return {"ok": True, "outcome": "DONE", "profile_id": entry["profile_id"], "retire": retire}


def _as_graph_error(exc: BaseException) -> BaseException:
    """Un error crudo de Graph sobre la bandeja se traduce a la familia que entiende la tabla común (403 → permiso; resto → fuente no disponible); 401 se conserva como causa."""
    from webapp.services import profile_document_service as docsvc
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 403:
        wrapped: BaseException = docsvc.ProfileDocumentPermissionDeniedError("Sin permiso sobre la carpeta de carga masiva.")
    elif isinstance(exc, (urllib.error.URLError, OSError)):
        wrapped = docsvc.ProfileDocumentGraphSourceError("SharePoint no respondió.")
    else:
        return exc
    wrapped.__cause__ = exc
    return wrapped


def _block(key: str, entry: dict, reason: str) -> dict:
    entry.update({"state": BLOCKED, "reason": reason})
    _save_ledger(key, entry, "BLOCKED")
    return {"ok": False, "outcome": "BLOCKED", "error": REASON_TEXT.get(reason, reason)}


def _failure(key: str, entry: dict | None, exc: BaseException) -> dict:
    verdict = core.classify(exc)
    if verdict is None:
        raise exc
    kind, code = verdict
    if kind in (core.STOP, core.TOKEN):
        if entry and entry.get("state"):
            entry["last_error"] = code
            try:
                manifest.put_ledger(AREA, key, entry)
            except manifest.ManifestError:
                pass
        return {"ok": False, "stop": code, "error": STOP_TEXT.get(code, code)}
    if entry is None:
        return {"ok": False, "error": REASON_TEXT["DOWNLOAD_ERROR"] if kind == core.RETRY else REASON_TEXT.get(code, code)}
    if kind in (core.FAILED, core.BLOCKED):
        return _block(key, entry, code)
    entry["last_error"] = code
    if int(entry.get("attempts") or 0) >= MAX_ATTEMPTS:
        entry["attempts"] = 0
        return _block(key, entry, "RETRIES_EXHAUSTED")
    _save_ledger(key, entry, "RETRY")
    return {"ok": False, "outcome": "RETRY", "error": "SharePoint o la persistencia no confirmaron la operación: reintentá (no se duplica nada)."}


def _ensure_profile(key: str, entry: dict, record: dict) -> str | None:
    """Alta (id determinístico, solo-crea) o revalidación del perfil existente. Devuelve un motivo de bloqueo o None."""
    decision = entry["decision"]
    pid = decision["profile_id"]
    if decision["op"] == "link":
        target = profile_repository.get(pid)
        if target is None or target.get("deleted_at") or profile_repository.is_locked(target) or int(target.get("revision") or 1) != int(decision.get("target_revision") or 1):
            return "LINK_TARGET_CHANGED"
        return None
    existing = profile_repository.get(pid)
    if existing is not None:
        return None if (existing.get("import_origin") or {}).get("item_key") == key else "PROFILE_ID_CONFLICT"
    fields = (manifest.get_draft(AREA, key) or {}).get("fields") or {}
    fields = {f: fields.get(f, "") for f in FIELDS}
    if _sha({"op": "create", "pid": pid, "fields": fields}) != decision["confirmed_sha256"]:
        return "RESOLUTION_STALE"
    shaped = human_capital_service.validate_and_shape_fields({**fields, "tags": TAG})
    origin = {"kind": ORIGIN_KIND, "item_key": key, "drive_item_id": record["item_id"], "sha256": record["sha256"], "accepted_by": decision["by"], "accepted_at": decision["at"], "imported_at": manifest.now_iso()}
    human_capital_service.create_profile_for_import(pid, {**shaped, "import_origin": origin})
    return None


# ═══════════════════ retiro físico del original (incorporado o descartado) ═══════════════════
# Solo dentro de `carga-masiva` y solo DESPUÉS de una decisión durable (libro DONE o DISMISSED): MOVER a `incorporados/` o `descartados/` (subcarpetas de la bandeja: archivo TRANSITORIO, nunca un segundo
# repositorio; su depuración segura queda para una política posterior) o, SOLO para un descarte con doble confirmación explícita, ELIMINAR. Antes de mover o eliminar se verifica identidad (mismo `item_id`, todavía en
# la bandeja) y versión (tamaño/cTag/hash del momento de la decisión, `If-Match` con el eTag leído). Idempotente: se concilia con el estado real (el `item_id` no cambia al mover). Un fallo no deshace la decisión: queda
# «retiro pendiente» y se reintenta sin repetirla. Nunca toca `CVs`, ni nada fuera de `carga-masiva`, ni elimina un original incorporado (ese solo se mueve a `incorporados/`).
MODE_INCORPORATED, MODE_DISCARDED, MODE_DELETE = RETIRED_FOLDER, DISCARDED_FOLDER, "delete"
DELETE_CONFIRMATION = "ELIMINAR"


def _subfolder(inbox_drive: str, inbox_id: str, name: str, token: str) -> str:
    """Id de `carga-masiva/<name>` (la crea si falta, `conflictBehavior=fail`; 409 → relectura). Solo dentro de la zona de entrada."""
    base = _items(inbox_drive)
    by_path = f"{base}/{urllib.parse.quote(inbox_id, safe='')}:/{name}"

    def lookup():
        try:
            item = microsoft365.graph_get_json(by_path, token)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        return item["id"] if isinstance(item.get("folder"), dict) and (item.get("parentReference") or {}).get("id") == inbox_id else "INVALID"

    found = lookup()
    if found is None:
        try:
            microsoft365.graph_post_json(f"{base}/{urllib.parse.quote(inbox_id, safe='')}/children", token, {"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})
        except urllib.error.HTTPError as exc:
            if exc.code != 409:
                raise
        except (urllib.error.URLError, OSError):
            pass                                                                    # respuesta incierta: la relectura decide
        found = lookup()
    if not found or found == "INVALID":
        raise urllib.error.URLError("subcarpeta de la bandeja no verificada")
    return found


def _physical(mode: str, source: dict, token: str) -> tuple[str, str | None]:
    """UNA operación física sobre el original, verificada. Devuelve `(estado, código)`; los errores de permiso/token se propagan."""
    drive, item_id, inbox_id = source["drive_id"], source["item_id"], source["parent_id"]
    item = _get_item(drive, item_id, token)
    if item is None:
        return RETIRE_DONE, "GONE"
    if (item.get("parentReference") or {}).get("id") != inbox_id:
        return RETIRE_DONE, "ELSEWHERE"                                             # ya no está en la bandeja (un intento anterior o una persona): fuera de `carga-masiva` no se toca nada
    if not _same_content(item, source):
        return RETIRE_BLOCKED, "CHANGED"
    if_match = {"If-Match": item["eTag"]} if item.get("eTag") else None
    url = f"{_items(drive)}/{urllib.parse.quote(item_id, safe='')}"
    try:
        if mode == MODE_DELETE:
            microsoft365.graph_delete(url, token, extra_headers=if_match)
        else:
            target = _subfolder(drive, inbox_id, mode, token)
            microsoft365.graph_patch_json(url + "?%40microsoft.graph.conflictBehavior=rename", token, {"parentReference": {"id": target}}, extra_headers=if_match)
        return RETIRE_DONE, None
    except urllib.error.HTTPError as exc:
        if exc.code == 412:
            return RETIRE_BLOCKED, "CHANGED"
        if exc.code == 404:
            return RETIRE_DONE, "GONE"
        if exc.code < 500:
            raise
        failure: BaseException = exc
    except (urllib.error.URLError, OSError) as exc:
        failure = exc
    again = _get_item(drive, item_id, token)                                        # respuesta incierta: ¿ocurrió igual?
    if again is None or (again.get("parentReference") or {}).get("id") != inbox_id:
        return RETIRE_DONE, None
    raise urllib.error.URLError("retiro no confirmado") from failure


def _retire(key: str, entry: dict, source: dict, token: str) -> str:
    """Retiro físico según `entry["retire"]["mode"]` (incorporados por defecto). Registra el resultado en el libro y en la auditoría. Devuelve DONE / PENDING / BLOCKED."""
    info = entry.get("retire") or {}
    mode = info.get("mode") or MODE_INCORPORATED
    if mode == MODE_DELETE and entry.get("state") != DISMISSED:
        mode = MODE_INCORPORATED                                                    # defensa: un original incorporado nunca se elimina
    try:
        state, code = _physical(mode, source, token)
    except urllib.error.HTTPError as exc:
        state, code = RETIRE_PENDING, {401: "TOKEN", 403: "PERMISSION"}.get(exc.code, "GRAPH")
        detail = diag.graph_error_detail(exc)
        _log.warning("cchh.cv_inbox.retire_failed mode=%s status=%s code=%s inner=%s", "delete" if mode == MODE_DELETE else "move", exc.code, detail.get("code"), detail.get("inner_code"))
    except (urllib.error.URLError, OSError, KeyError) as exc:
        state, code = RETIRE_PENDING, "GRAPH"
        cause = getattr(exc, "__cause__", None) or getattr(exc, "reason", None)
        _log.warning("cchh.cv_inbox.retire_failed mode=%s error=%s cause=%s", "delete" if mode == MODE_DELETE else "move", type(exc).__name__,
                     (diag.graph_error_detail(cause).get("status") if isinstance(cause, urllib.error.HTTPError) else type(cause).__name__ if cause else None))
    except Exception as exc:                                                        # noqa: BLE001 — la decisión ya es durable: un fallo inesperado del retiro NUNCA corta la respuesta
        state, code = RETIRE_PENDING, "LOCAL" if isinstance(exc, TypeError) else "GRAPH"
        _log.exception("cchh.cv_inbox.retire_unexpected error=%s", type(exc).__name__)
    entry["retire"] = {**info, "mode": mode, "state": state, "code": code, "at": manifest.now_iso(), "attempts": int(info.get("attempts") or 0) + 1}
    try:
        _save_ledger(key, entry, "RETIRE_" + state)
    except manifest.ManifestError:
        pass                                                                        # el próximo intento concilia con SharePoint (el item_id no cambia al mover)
    _log.info("cchh.cv_inbox.retire mode=%s state=%s code=%s", "delete" if mode == MODE_DELETE else "move", state, code)
    return state


def retire_reason(entry: dict) -> str | None:
    """Código legible del retiro físico (para la vista)."""
    info = entry.get("retire") or {}
    if not info:
        return None
    if info.get("state") == RETIRE_DONE:
        if info.get("code") in ("GONE", "ELSEWHERE"):
            return "SOURCE_GONE"
        return {MODE_DELETE: "DELETED_ORIGINAL", MODE_DISCARDED: "MOVED_TO_DISCARDED"}.get(info.get("mode"))
    return {"PERMISSION": "RETIRE_PERMISSION", "CHANGED": "RETIRE_CHANGED", "TOKEN": "RETIRE_TOKEN", "GRAPH": "RETIRE_GRAPH", "LOCAL": "RETIRE_LOCAL", None: "RETIRE_INTERRUPTED"}.get(info.get("code"), "RETIRE_PENDING")


def _source_of(key: str, entry: dict) -> dict:
    return entry.get("source") or manifest.get_group(AREA, key) or {}


def retire(key: str, token: str) -> dict:
    """Reintento explícito del retiro físico (de un CV incorporado o descartado), sin repetir la decisión ni duplicar operaciones."""
    with manifest.ledger_lock(AREA, key, timeout=0):
        entry = manifest.get_ledger(AREA, key) or {}
        if entry.get("state") not in (DONE, DISMISSED) or not entry.get("retire"):
            return {"ok": False, "error": "Solo se retira de la carpeta un CV ya incorporado o descartado."}
        if entry["retire"].get("state") == RETIRE_DONE:
            return {"ok": True, "retire": RETIRE_DONE, "idempotent": True}
        source = _source_of(key, entry)
        if not source.get("item_id"):
            return {"ok": False, "error": REASON_TEXT["SOURCE_GONE"]}
        state = _retire(key, entry, source, token)
    return {"ok": state == RETIRE_DONE, "retire": state, **({} if state == RETIRE_DONE else {"error": REASON_TEXT[retire_reason(entry)]})}


# ═══════════════════ eliminar un CV incorporado (como «Eliminar» de Candidatos) ═══════════════════
# «Eliminar» en la vista Incorporados hace lo MISMO que eliminar el candidato desde Candidatos, más el original de la bandeja:
#   * CV que CREÓ un candidato: se eliminan sus documentos de «CVs» (`profile_document_service.delete_profile_documents`, el mismo flujo controlado de Candidatos: cada documento confirmado; si
#     alguno no se confirma, el candidato NO se da de baja) y recién entonces la baja del candidato (`human_capital_service.soft_delete_profile`);
#   * CV que se SUMÓ a un candidato que ya existía: se elimina SOLO ese documento (`delete_document`); la persona sigue en el Banco con el resto de sus CVs;
#   * y el ORIGINAL de `carga-masiva` (o `carga-masiva/incorporados`), verificado ANTES de tocar nada (mismo item, dentro de esas carpetas, mismo sha256) y eliminado con `If-Match`.
# Si el original no se puede verificar (cambió o está fuera de la bandeja) no se elimina nada. Idempotente: un reintento solo procesa lo que falta. Requiere `confirm == "ELIMINAR"`.
def _check_original(source: dict, sha256: str, token: str) -> tuple[str, dict | None]:
    """Verifica el original SIN eliminarlo. `("OK", item)`, `("GONE", None)` (ya no existe), `("ELSEWHERE"|"CHANGED", None)`. Permiso/token se propagan."""
    drive, item_id, inbox_id = source["drive_id"], source["item_id"], source["parent_id"]
    item = _get_item(drive, item_id, token)
    if item is None:
        return "GONE", None
    parent = (item.get("parentReference") or {}).get("id")
    if parent != inbox_id:
        try:
            sub = microsoft365.graph_get_json(f"{_items(drive)}/{urllib.parse.quote(inbox_id, safe='')}:/{RETIRED_FOLDER}?$select=id,folder", token)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            sub = {}
        if not (isinstance(sub.get("folder"), dict) and sub.get("id") == parent):
            return "ELSEWHERE", None
    if hashlib.sha256(microsoft365.graph_get_bytes(f"{_items(drive)}/{urllib.parse.quote(item_id, safe='')}/content", token)).hexdigest() != sha256:
        return "CHANGED", None
    return "OK", item


def _delete_original(source: dict, item: dict, token: str) -> str:
    """Elimina el original ya verificado, con `If-Match`. `DONE` / `GONE` / `CHANGED`; permiso/token se propagan."""
    url = f"{_items(source['drive_id'])}/{urllib.parse.quote(source['item_id'], safe='')}"
    try:
        microsoft365.graph_delete(url, token, extra_headers={"If-Match": item["eTag"]} if item.get("eTag") else None)
    except urllib.error.HTTPError as exc:
        if exc.code == 412:
            return "CHANGED"
        if exc.code == 404:
            return "GONE"
        raise
    return "DONE"


def _remove_from_banco(entry: dict, token: str, actor: str) -> dict:
    """La parte de Candidatos: documentos en «CVs» y baja del candidato (alta), o solo el documento (vinculación). Lanza `InboxError` con el motivo si algo no se confirma."""
    from webapp.services import human_capital_service as hcs, profile_document_service as docsvc
    pid, did = entry.get("profile_id"), entry.get("document_id")
    profile = profile_repository.get(pid) if pid else None
    if not profile or profile.get("deleted_at"):
        return {"candidate": "ALREADY_GONE", "documents": 0}
    try:
        if ((entry.get("decision") or {}).get("op") or "create") == "link":
            try:
                docsvc.delete_document(profile_id=pid, document_id=did, access_token=token, remove_empty_folder=False)
                return {"candidate": "KEPT", "documents": 1}
            except docsvc.ProfileDocumentNotFoundError:
                return {"candidate": "KEPT", "documents": 0}
        summary = docsvc.delete_profile_documents(profile_id=pid, access_token=token)
        try:
            hcs.soft_delete_profile(pid, actor)
        except profile_repository.ProfileNotFoundError:
            pass
        folders = [m for m in (docsvc.folder_kept_message(st) for st in summary["folders"].values()) if m]
        return {"candidate": "DELETED", "documents": summary["deleted"], "folder_notice": " ".join(dict.fromkeys(folders))}
    except docsvc.ProfileDocumentsBulkDeleteIncompleteError as exc:
        raise InboxError("DELETE_INCOMPLETE", f"{exc} Se eliminaron {exc.deleted} documento(s) y quedan {exc.remaining} sin confirmar. El candidato NO fue dado de baja: podés reintentar.") from None
    except (docsvc.ProfileDocumentDeletePermissionDeniedError, docsvc.ProfileDocumentPermissionDeniedError):
        raise InboxError("PERMISSION", "SharePoint no permitió eliminar el CV del candidato en «CVs». No se eliminó nada.") from None
    except (docsvc.ProfileDocumentDeleteGraphSourceError, docsvc.ProfileDocumentDeletePhysicalOutcomeUnknownError, docsvc.ProfileDocumentMetadataDeleteOutcomeUnknownError,
            docsvc.ProfileDocumentGraphSourceError, docsvc.ProfileDocumentConfigIncompleteError, profile_document_repository.ProfileDocumentStorageError) as exc:
        raise InboxError("DELETE_UNCONFIRMED", f"No se pudo confirmar la eliminación del CV en «CVs» ({exc}). Reintentá: no se duplica nada.") from None
    except (profile_repository.ProfileStorageError, profile_repository.ProfileRemoteOutcomeUnknownError, profile_repository.ProfileConcurrentModificationError) as exc:
        raise InboxError("BAJA_UNCONFIRMED", f"Los documentos se eliminaron, pero la baja del candidato no se confirmó ({exc}). Reintentá.") from None


def delete_incorporated(key: str, token: str, *, actor: str, confirm: str = "") -> dict:
    """Elimina un CV incorporado como lo haría «Eliminar» de Candidatos, más su original en la bandeja (ver arriba)."""
    if confirm != DELETE_CONFIRMATION:
        return {"ok": False, "error": "Eliminar requiere una confirmación explícita. No se eliminó nada."}
    with manifest.ledger_lock(AREA, key, timeout=0):
        entry = manifest.get_ledger(AREA, key) or {}
        if entry.get("state") != DONE:
            return {"ok": False, "error": "Solo se elimina desde acá un CV ya incorporado al Banco."}
        info = entry.get("retire") or {}
        source = _source_of(key, entry)
        sha256 = entry.get("sha256") or source.get("sha256")
        original_done = info.get("mode") == MODE_DELETE and info.get("state") == RETIRE_DONE
        try:
            check, item = ("GONE", None) if original_done or not source.get("item_id") or not sha256 else _check_original(source, sha256, token)
            if check in ("ELSEWHERE", "CHANGED"):
                return {"ok": False, "error": REASON_TEXT["ORIGINAL_ELSEWHERE" if check == "ELSEWHERE" else "ORIGINAL_CHANGED"]}
            removed = _remove_from_banco(entry, token, actor)
            outcome = _delete_original(source, item, token) if check == "OK" else "GONE"
        except InboxError as exc:
            return {"ok": False, "error": str(exc)}
        except urllib.error.HTTPError as exc:
            code = {401: "TOKEN", 403: "PERMISSION"}.get(exc.code, "GRAPH")
            _log.warning("cchh.cv_inbox.delete_failed status=%s code=%s", exc.code, diag.graph_error_detail(exc).get("code"))
            return {"ok": False, "error": {"TOKEN": STOP_TEXT["TOKEN_EXPIRED"], "PERMISSION": REASON_TEXT["RETIRE_PERMISSION"]}.get(code, REASON_TEXT["RETIRE_GRAPH"]),
                    "stop": {"TOKEN": "TOKEN_EXPIRED", "PERMISSION": "PERMISSION_DENIED"}.get(code)}
        except (urllib.error.URLError, OSError) as exc:
            _log.warning("cchh.cv_inbox.delete_failed error=%s", type(exc).__name__)
            return {"ok": False, "error": REASON_TEXT["RETIRE_GRAPH"]}
        if outcome == "CHANGED":
            return {"ok": False, "error": REASON_TEXT["ORIGINAL_CHANGED"], "removed": removed}
        entry["retire"] = {**info, "mode": MODE_DELETE, "state": RETIRE_DONE, "code": None if outcome == "DONE" else "GONE", "at": manifest.now_iso(), "attempts": int(info.get("attempts") or 0) + 1,
                           "requested_by": actor, "previous_mode": info.get("mode")}
        entry["removed"] = {**removed, "by": actor, "at": manifest.now_iso()}
        try:
            _save_ledger(key, entry, "DELETED")
        except manifest.ManifestError:
            return {"ok": False, "stop": "PERSISTENCE_ERROR", "error": STOP_TEXT["PERSISTENCE_ERROR"]}
    _log.info("cchh.cv_inbox.deleted candidate=%s documents=%s original=%s", removed["candidate"], removed["documents"], outcome)
    return {"ok": True, "removed": removed}


# ═══════════════════ otras decisiones explícitas ═══════════════════
def dismiss(key: str, token: str = "", *, actor: str, reason: str, delete_original: bool = False, confirm_delete: str = "", listing: list[dict] | None = None) -> dict:
    """Descarta un CV (decisión durable en el Hub) y retira el original de `carga-masiva`: lo MUEVE a `descartados/` (predeterminado, recuperable) o, SOLO con doble confirmación explícita
    (`delete_original` + `confirm_delete == "ELIMINAR"`), lo ELIMINA de SharePoint. Lo que se mueve o elimina es la versión que estaba en la bandeja al decidir (instantánea del listado). Si Graph falla, el descarte queda
    registrado y el retiro físico pendiente; reintentar (`retire`) no repite el descarte."""
    if reason not in DISMISS_REASONS:
        return {"ok": False, "error": "Elegí un motivo de descarte."}
    if delete_original and confirm_delete != DELETE_CONFIRMATION:
        return {"ok": False, "error": "Eliminar el archivo original requiere una segunda confirmación. No se descartó nada."}
    with manifest.ledger_lock(AREA, key, timeout=0):
        entry = manifest.get_ledger(AREA, key) or {}
        if entry.get("state") in (*ACTIVE, DONE):
            return {"ok": False, "error": "Este CV tiene una incorporación en curso o terminada: no se puede descartar."}
        if entry.get("state") == DISMISSED:
            return {"ok": False, "error": "Este CV ya estaba descartado."}
        snapshot = {e["key"]: e for e in (listing if listing is not None else current_listing(token)[1])}.get(key) if token else None
        source = {k: snapshot.get(k) for k in ("item_id", "drive_id", "parent_id", "name", "size", "ctag", "etag", "hash")} if snapshot else None
        mode = MODE_DELETE if delete_original else MODE_DISCARDED
        if source:
            info = {"mode": mode, "state": RETIRE_PENDING, "code": None}
        else:
            info = {"mode": mode, "state": RETIRE_PENDING if not token else RETIRE_DONE, "code": "TOKEN" if not token else "GONE"}
        entry = {"state": DISMISSED, "dismiss_reason": reason, "decision": None, "by": actor, "source": source, "retire": {**info, "requested_by": actor, "requested_at": manifest.now_iso()},
                 "history": entry.get("history", [])}
        _save_ledger(key, entry, "DISMISSED")
        state = _retire(key, entry, source, token) if source else info["state"]
    out = {"ok": True, "retire": state, "mode": mode}
    if state != RETIRE_DONE:
        out["warning"] = REASON_TEXT[retire_reason(entry)]
    return out


def dismiss_many(keys: list[str], token: str, *, actor: str, reason: str, delete_original: bool = False, confirm_delete: str = "", time_budget: float = ACCEPT_BUDGET_S,
                 clock=time.monotonic) -> dict:
    """Descarte por LOTE: lo MISMO que descartar cada CV por separado (misma decisión, mismo retiro físico verificado y, solo con la doble confirmación, la eliminación), con UN listado de la bandeja y el mismo
    presupuesto de tiempo que la aceptación por lote. Cada CV es independiente: lo que no se llega a procesar queda sin cambios."""
    if reason not in DISMISS_REASONS:
        return {"ok": False, "error": "Elegí un motivo de descarte."}
    if delete_original and confirm_delete != DELETE_CONFIRMATION:
        return {"ok": False, "error": "Eliminar los archivos originales requiere una segunda confirmación. No se descartó nada."}
    started = clock()
    listing = current_listing(token)[1]
    results = []
    for key in keys[:ACCEPT_MAX_SELECTION]:
        if clock() - started >= time_budget:
            results.append({"key": key, "outcome": "PENDING", "message": "No se llegó a procesar en este envío: volvé a seleccionarlo."})
            continue
        try:
            res = dismiss(key, token, actor=actor, reason=reason, delete_original=delete_original, confirm_delete=confirm_delete, listing=listing)
        except manifest.ManifestBusyError:
            res = {"ok": False, "error": "Otra persona está procesando este CV en este momento."}
        except manifest.ManifestError:
            res = {"ok": False, "error": STOP_TEXT["PERSISTENCE_ERROR"]}
        outcome = "DONE" if res.get("ok") and res.get("retire") == RETIRE_DONE else ("PARTIAL" if res.get("ok") else "ERROR")
        results.append({"key": key, "outcome": outcome, "message": res.get("error") or res.get("warning") or ""})
    counts = {"done": sum(1 for r in results if r["outcome"] in ("DONE", "PARTIAL")), "partial": sum(1 for r in results if r["outcome"] == "PARTIAL"),
              "pending": sum(1 for r in results if r["outcome"] == "PENDING"), "errors": sum(1 for r in results if r["outcome"] == "ERROR")}
    _log.info("cchh.cv_inbox.batch_dismiss done=%s partial=%s pending=%s errors=%s mode=%s", counts["done"], counts["partial"], counts["pending"], counts["errors"], "delete" if delete_original else "move")
    return {"ok": True, "results": results, "counts": counts, "mode": MODE_DELETE if delete_original else MODE_DISCARDED}


def reopen(key: str, *, actor: str, token: str = "") -> dict:
    """Vuelve a dejar por resolver un CV descartado cuyo archivo sigue —o volvió a estar— en la bandeja, o una aceptación SIN efectos (bloqueada antes de crear o subir nada)."""
    with manifest.ledger_lock(AREA, key, timeout=0):
        entry = manifest.get_ledger(AREA, key) or {}
        if entry.get("state") == DISMISSED:
            info = entry.get("retire") or {}
            if info.get("state") == RETIRE_DONE and not (token and key in {e["key"] for e in current_listing(token)[1]}):
                if info.get("mode") == MODE_DELETE:
                    return {"ok": False, "error": "El archivo original se eliminó de SharePoint: no se puede reabrir."}
                return {"ok": False, "error": f"El archivo está en «{FOLDER_NAME}/{DISCARDED_FOLDER}». Para reabrirlo, movelo de nuevo a «{FOLDER_NAME}» desde SharePoint."}
        elif not (entry.get("state") in (ACCEPTED, BLOCKED) and not entry.get("effects")):
            return {"ok": False, "error": "Solo se puede reabrir un CV descartado o una aceptación que no produjo efectos."}
        _save_ledger(key, {**entry, "state": REOPENED, "decision": None, "stage": None, "reason": None, "retire": None, "by": actor}, "REOPENED")
    return {"ok": True}
