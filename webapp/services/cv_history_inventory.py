"""cv_history_inventory.py — INVENTARIO de SOLO LECTURA del histórico de CVs de SharePoint (primer incremento de la importación histórica al Banco de CCHH).

QUÉ HACE: resuelve el link compartido de la carpeta histórica (MISMO `discover_sharing_link` de la sonda de diagnóstico) y la recorre en profundidad con `GET …/children` (paginado, con
`$select`), acumulando SOLO agregados: carpetas, archivos, distribución por especialidad (= subcarpeta de primer nivel), formatos y tamaños, compatibilidad con el alta documental del Hub,
repetidos por identidad Graph y posibles duplicados por hash (el `quickXorHash` que Graph ya informa en el listado), y los errores de acceso/paginación.

QUÉ NO HACE (garantías, con tests):
  * NO descarga contenido (ninguna URL `/content`), NO extrae datos personales, NO crea perfiles, NO escribe ni persiste nada (el resultado es efímero, no hay manifiesto en esta fase).
  * Solo GET (`microsoft365.graph_get_json`); este módulo no importa helpers de escritura ni servicios de Perfil/Documento.
  * El resultado y los logs NO contienen nombres de archivo (suelen llevar el nombre de la persona), ni el token, ni el link compartido, ni query strings. De cada elemento solo se conserva
    la clasificación; los nombres de carpeta de primer nivel (la especialidad) sí se muestran porque son el objeto del inventario.
  * Mismo token y mismos permisos que el resto del Hub (token delegado de LECTURA del navegador): no hay una segunda autenticación.

LÍMITES (Graph y de la petición): páginas de 200 elementos, reintento acotado ante 429/5xx respetando `Retry-After`, presupuesto de tiempo, tope de elementos y de carpetas, guarda contra
`@odata.nextLink` repetido. Si algo corta el recorrido, el informe lo dice (`complete: False` + `stopped_reason`) y NUNCA presenta un inventario parcial como completo.

La clasificación es por EXTENSIÓN y tamaño (no hay descarga): el formato real (firma) se verifica recién al descargar, en la fase siguiente."""
from __future__ import annotations

import hashlib
import logging
import statistics
import time
import unicodedata
import urllib.error
import urllib.parse
from collections import Counter, defaultdict

from core import microsoft365
from webapp.services import profile_document_validation as validation
from webapp.services import sharepoint_graph_diagnostics as diag

LOGGER = logging.getLogger("danwor.cchh.cv_history_inventory")

GRAPH_BASE = diag.GRAPH_BASE
CHILDREN_SELECT = "id,name,size,file,folder,remoteItem,package"
PAGE_SIZE = 200                      # máximo que acepta Graph en `children`
MAX_ITEMS = 20000                    # tope de elementos vistos (carpetas + archivos)
MAX_FOLDERS = 1000
MAX_PAGES_PER_FOLDER = 200
TIME_BUDGET_S = 80.0                 # por debajo del timeout típico del proxy de DigitalOcean
TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4                     # 1 intento + 3 reintentos
RETRY_AFTER_CAP_S = 10.0
BACKOFF_S = (1.0, 2.0, 4.0)
ROOT_LABEL = "(raíz)"

# Clasificación por archivo (excluyentes, en este orden de precedencia).
IGNORED, EMPTY, UNSUPPORTED, DOC_MANUAL, TOO_LARGE, COMPATIBLE = "ignored", "empty", "unsupported", "doc_manual", "too_large", "compatible"
CLASSES = (COMPATIBLE, DOC_MANUAL, UNSUPPORTED, TOO_LARGE, EMPTY, IGNORED)
_IGNORED_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
_SIZE_BUCKETS = (("<100 KB", 100 * 1024), ("100 KB–1 MB", 1024 * 1024), ("1–5 MB", 5 * 1024 * 1024), ("5–10 MB", 10 * 1024 * 1024))
_OVER = ">10 MB"


class InventoryInputError(ValueError):
    """Entrada inválida (link): mensaje seguro, sin eco del link."""


def _ref(value: str) -> str:
    """Referencia corta y estable de un id (no reversible) para citar un elemento sin exponer nombres."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:8]


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name.strip(".") else ""


def _fold(value: str) -> str:
    """Clave de comparación de nombres de carpeta: sin acentos, minúsculas, espacios colapsados (detecta variantes del mismo rótulo, no las fusiona)."""
    text = unicodedata.normalize("NFKD", value or "")
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).lower().split())


def classify(name: str, size: int) -> str:
    """Compatibilidad con el alta documental del Hub (PDF/DOCX ≤ tope; DOC binario → revisión manual) SOLO por extensión y tamaño."""
    ext = _ext(name)
    if name.startswith("~$") or name.startswith(".") or name.lower() in _IGNORED_NAMES or ext in {"tmp", "lnk"}:
        return IGNORED
    if size <= 0:
        return EMPTY
    if ext == "doc":
        return DOC_MANUAL
    if ext not in validation.ACCEPTED_CONTENT_TYPES:
        return UNSUPPORTED
    return TOO_LARGE if size > validation.MAX_CV_UPLOAD_BYTES else COMPATIBLE


def _bucket(size: int) -> str:
    for label, limit in _SIZE_BUCKETS:
        if size < limit:
            return label
    return _OVER


def _validate_share_url(url: str) -> str:
    value = (url or "").strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc.lower().endswith(".sharepoint.com"):
        raise InventoryInputError("El link tiene que ser https://*.sharepoint.com/…")
    return value


def _error(scope: str, kind: str, *, folder: str | None = None, depth: int | None = None, page: int | None = None, detail: dict | None = None) -> dict:
    """Registro de error SIN mensajes libres de Graph ni nombres (el `message` puede citar nombres de elementos)."""
    detail = detail or {}
    return {"scope": scope, "kind": kind, "folder": folder, "depth": depth, "page": page,
            "status": detail.get("status"), "code": detail.get("code"), "reason": (detail.get("reason") or None), "request_id": detail.get("request_id")}


class _Walker:
    def __init__(self, token: str, *, clock, sleep, time_budget: float, max_items: int, max_folders: int):
        self.token, self.clock, self.sleep = token, clock, sleep
        self.time_budget, self.max_items, self.max_folders = time_budget, max_items, max_folders
        self.started = clock()
        self.calls = self.pages = self.throttled = 0
        self.errors: list[dict] = []

    def out_of_time(self) -> bool:
        return (self.clock() - self.started) >= self.time_budget

    def get(self, url: str) -> dict:
        """UN GET con reintento acotado ante 429/5xx/red. Lanza el último error si no se resuelve; jamás registra URL ni token."""
        attempt = 0
        while True:
            attempt += 1
            self.calls += 1
            try:
                return microsoft365.graph_get_json(url, self.token)
            except urllib.error.HTTPError as exc:
                if exc.code not in TRANSIENT_STATUSES or attempt >= MAX_ATTEMPTS:
                    raise
                retry_after = None
                try:
                    retry_after = float((exc.headers or {}).get("Retry-After"))
                except (TypeError, ValueError):
                    pass
                self.throttled += 1
                self.sleep(min(retry_after if retry_after is not None else BACKOFF_S[min(attempt - 1, len(BACKOFF_S) - 1)], RETRY_AFTER_CAP_S))
            except urllib.error.URLError:
                if attempt >= MAX_ATTEMPTS:
                    raise
                self.throttled += 1
                self.sleep(BACKOFF_S[min(attempt - 1, len(BACKOFF_S) - 1)])


def run_inventory(token: str, share_url: str, *, clock=time.monotonic, sleep=time.sleep, time_budget: float = TIME_BUDGET_S,
                  max_items: int = MAX_ITEMS, max_folders: int = MAX_FOLDERS) -> dict:
    """Inventario completo (ver docstring del módulo). Nunca lanza por fallas de Graph: las informa en `errors`. Lanza `InventoryInputError` solo ante un link inválido."""
    url = _validate_share_url(share_url)
    walker = _Walker(token, clock=clock, sleep=sleep, time_budget=time_budget, max_items=max_items, max_folders=max_folders)
    report: dict = {"ok": False, "complete": False, "stopped_reason": None, "root": None, "errors": walker.errors}

    walker.calls += 1
    record, found = diag.discover_sharing_link(url, token)
    if not found or not found.get("drive_id") or not found.get("item_id"):
        walker.errors.append(_error("link", "unresolved", detail=record))
        report["stats"] = _stats(walker)
        return report
    if not found.get("is_folder"):
        walker.errors.append(_error("link", "not_a_folder"))
        report["stats"] = _stats(walker)
        return report
    drive_id, root_id = found["drive_id"], found["item_id"]
    report["root"] = {"item_ref": _ref(root_id), "host": urllib.parse.urlsplit(url).netloc.lower()}

    seen_ids: set[str] = set()
    seen_remote: set[str] = set()
    totals = Counter()
    by_ext: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    classes = Counter()
    sizes: list[int] = []
    buckets = Counter()
    specialties: dict[str, dict] = {}
    spec_subfolders: Counter = Counter()
    hash_groups: dict[tuple, list[str]] = defaultdict(list)
    dup_ids = dup_remote = with_hash = without_hash = 0
    max_depth = 0
    empty_folders = 0

    def spec(name: str) -> dict:
        return specialties.setdefault(name, {"name": name, "files": 0, "bytes": 0, **{c: 0 for c in CLASSES}})

    queue: list[tuple[str, str | None, int]] = [(root_id, None, 0)]       # (item_id, especialidad, profundidad)
    folders_listed = 0
    stopped: str | None = None
    interrupted = False                                  # el corte llegó A MITAD de una carpeta: esa carpeta también queda sin recorrer por completo
    while queue:
        if walker.out_of_time():
            stopped = "TIME_BUDGET"
            break
        if folders_listed >= max_folders:
            stopped = "MAX_FOLDERS"
            break
        folder_id, specialty, depth = queue.pop(0)
        folders_listed += 1
        max_depth = max(max_depth, depth)
        label = specialty or ROOT_LABEL
        next_url = (f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive_id, safe='!,')}/items/{urllib.parse.quote(folder_id, safe='')}/children"
                    f"?$select={CHILDREN_SELECT}&$top={PAGE_SIZE}")
        visited_links: set[str] = set()
        page = 0
        children_seen = 0
        while next_url:
            if walker.out_of_time():
                stopped = "TIME_BUDGET"
                interrupted = True
                break
            if page >= MAX_PAGES_PER_FOLDER or next_url in visited_links:
                walker.errors.append(_error("folder", "pagination_loop" if next_url in visited_links else "pagination_limit", folder=label, depth=depth, page=page + 1))
                break
            visited_links.add(next_url)
            page += 1
            try:
                payload = walker.get(next_url)
            except urllib.error.HTTPError as exc:
                walker.errors.append(_error("folder", "throttled" if exc.code == 429 else ("pagination" if page > 1 else "access"), folder=label, depth=depth, page=page, detail=diag.graph_error_detail(exc)))
                break
            except (urllib.error.URLError, ValueError, OSError) as exc:
                walker.errors.append(_error("folder", "pagination" if page > 1 else "network", folder=label, depth=depth, page=page, detail={"reason": type(exc).__name__}))
                break
            walker.pages += 1
            for item in payload.get("value") or []:
                children_seen += 1
                totals["seen"] += 1
                if totals["seen"] > max_items:
                    stopped = "MAX_ITEMS"
                    interrupted = True
                    break
                iid = str(item.get("id") or "")
                if iid and iid in seen_ids:
                    dup_ids += 1
                    continue
                seen_ids.add(iid)
                remote = item.get("remoteItem")
                if isinstance(remote, dict):                               # acceso directo: apunta a OTRO lugar; no se sigue ni se cuenta como archivo de este árbol
                    totals["shortcuts"] += 1
                    rid = str(remote.get("id") or "")
                    if rid and rid in seen_remote:
                        dup_remote += 1
                    seen_remote.add(rid)
                    continue
                name = str(item.get("name") or "")
                if "folder" in item:
                    totals["folders"] += 1
                    child_specialty = specialty if specialty is not None else (name or ROOT_LABEL)
                    if specialty is None:
                        spec(child_specialty)
                    else:
                        spec_subfolders[specialty] += 1
                    queue.append((iid, child_specialty, depth + 1))
                elif "file" in item:
                    size = int(item.get("size") or 0)
                    cls = classify(name, size)
                    label_spec = specialty or ROOT_LABEL
                    entry = spec(label_spec)
                    entry["files"] += 1
                    entry["bytes"] += size
                    entry[cls] += 1
                    classes[cls] += 1
                    totals["files"] += 1
                    totals["bytes"] += size
                    ext = _ext(name) or "(sin extensión)"
                    by_ext[ext][0] += 1
                    by_ext[ext][1] += size
                    if cls not in (IGNORED, EMPTY):
                        sizes.append(size)
                        buckets[_bucket(size)] += 1
                        hashes = (item.get("file") or {}).get("hashes") or {}
                        algo = next((a for a in ("sha256Hash", "quickXorHash", "sha1Hash") if hashes.get(a)), None)
                        if algo:
                            with_hash += 1
                            hash_groups[(algo, hashes[algo], size)].append(label_spec)
                        else:
                            without_hash += 1
                else:
                    totals["other"] += 1                                   # paquete (p. ej. OneNote) u otro: ni archivo ni carpeta
            if stopped == "MAX_ITEMS":
                break
            next_url = payload.get("@odata.nextLink") or ""
        if children_seen == 0 and not any(e["scope"] == "folder" and e["folder"] == label and e["depth"] == depth for e in walker.errors):
            empty_folders += 1
        if stopped:
            break

    pending = len(queue) + (1 if interrupted else 0)
    if pending and not stopped:
        stopped = "INCOMPLETE"
    dup_groups = {k: v for k, v in hash_groups.items() if len(v) > 1}
    variants: dict[str, set[str]] = defaultdict(set)
    for name in specialties:
        if name != ROOT_LABEL:
            variants[_fold(name)].add(name)
    sizes_sorted = sorted(sizes)
    report.update({
        "ok": True,
        "complete": stopped is None and not walker.errors,
        "stopped_reason": stopped,
        "totals": {"folders": totals["folders"], "files": totals["files"], "bytes": totals["bytes"], "max_depth": max_depth, "empty_folders": empty_folders,
                   "shortcuts": totals["shortcuts"], "other_items": totals["other"], "pending_folders": pending},
        "specialties": sorted(({**e, "subfolders": spec_subfolders.get(e["name"], 0)} for e in specialties.values()), key=lambda e: (-e["files"], e["name"])),
        "specialty_variants": sorted(sorted(names) for names in variants.values() if len(names) > 1),
        "formats": sorted(({"ext": ext, "files": n, "bytes": b} for ext, (n, b) in by_ext.items()), key=lambda r: (-r["files"], r["ext"])),
        "classification": {c: classes.get(c, 0) for c in CLASSES},
        "sizes": {"considered_files": len(sizes_sorted), "min": sizes_sorted[0] if sizes_sorted else 0, "median": int(statistics.median(sizes_sorted)) if sizes_sorted else 0,
                  "max": sizes_sorted[-1] if sizes_sorted else 0, "total": sum(sizes_sorted), "limit_bytes": validation.MAX_CV_UPLOAD_BYTES,
                  "buckets": {label: buckets.get(label, 0) for label in [b[0] for b in _SIZE_BUCKETS] + [_OVER]}},
        "duplicates": {
            "graph_identity_repeated": dup_ids, "remote_item_repeated": dup_remote,
            "hash": {"with_hash": with_hash, "without_hash": without_hash, "groups": len(dup_groups), "files_in_groups": sum(len(v) for v in dup_groups.values()),
                     "surplus_copies": sum(len(v) - 1 for v in dup_groups.values()), "cross_specialty_groups": sum(1 for v in dup_groups.values() if len(set(v)) > 1)},
        },
        "stats": _stats(walker, folders_listed=folders_listed),
        "notes": ["Clasificación por extensión y tamaño: el formato real se verifica al descargar (fase siguiente).",
                  "Sin descarga ni extracción de datos personales; no se guardó nada."],
    })
    LOGGER.info("cv_history_inventory folders=%s files=%s errors=%s complete=%s stopped=%s calls=%s", totals["folders"], totals["files"], len(walker.errors), report["complete"], stopped, walker.calls)
    return report


def _stats(walker: _Walker, *, folders_listed: int = 0) -> dict:
    return {"graph_calls": walker.calls, "pages": walker.pages, "throttled_retries": walker.throttled, "folders_listed": folders_listed,
            "elapsed_s": round(walker.clock() - walker.started, 2),
            "limits": {"time_budget_s": walker.time_budget, "max_items": walker.max_items, "max_folders": walker.max_folders, "page_size": PAGE_SIZE}}
