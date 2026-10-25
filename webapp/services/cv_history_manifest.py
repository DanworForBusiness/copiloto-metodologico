"""cv_history_manifest.py — manifiesto PERSISTENTE, reanudable y auditable de la importación histórica de CVs (piloto de extracción y conciliación).

Vive bajo `human-capital/import-history/<run_id>/` con el MISMO mecanismo de persistencia que el resto de CCHH (`remote_store.write_through`: escritura atómica local + write-through a Spaces); no hay un
segundo mecanismo. Separa por diseño lo personal de lo auditable:

  run.json                  metadatos del lote (origen sin nombres, autorización del owner, población, contadores). SIN datos personales de candidatos.
  staging/<content_key>.json  ÁREA TEMPORAL PROTEGIDA: por contenido de documento, los datos extraídos (nombre, email, celular), nombre de archivo, candidatos y coincidencias. CONTIENE datos personales:
                            solo se lee desde la vista del owner; no se registra en logs ni en reportes generales; está pensada para descartarse al cerrar la importación.
  verdicts/<content_key>.json DICTAMEN DEL OWNER por documento y POR LOTE (propuesta correcta / corrección manual / identidad no determinada), con historial append-only y la propuesta evaluada. Es un archivo
                            APARTE de `staging/`: el resultado de la extracción nunca se reescribe al dictaminar. Una corrección manual contiene datos personales: vive en el área temporal protegida y se
                            elimina junto con ella al descartar el lote.
  import-index.json         (solo lotes de IMPORTACIÓN) índice de los contenidos del origen con su procedencia (incluye NOMBRES DE ARCHIVO, que suelen llevar el nombre de la persona): área protegida, se elimina con el descarte.
  import-plan.json          (solo lotes de IMPORTACIÓN) plan de importación: decisión por contenido (acción, motivos, grupo, perfil destino). SIN datos personales: sobrevive al descarte como registro técnico durable.
  ledger/<content_key>.json   (solo lotes de IMPORTACIÓN) LIBRO DE EJECUCIÓN: correspondencia durable `content_key → profile_id → document_id` y estado de cada contenido (con el id del archivo en SharePoint y su sha256).
                            SIN datos personales (solo identificadores y códigos): sobrevive al descarte como registro técnico y es la base de la idempotencia de la importación.
  audit/<content_key>.json    AUDITORÍA: una lista de eventos (estado, código de excepción, hora). SIN datos personales: sobrevive al descarte del área temporal.
  drafts/<content_key>.json   (solo lotes de IMPORTACIÓN) BORRADOR del owner por excepción («Históricos sin validación»): nombre, apellido, email, teléfono y observaciones que el owner va completando, con `revision`
                            (control optimista), autor, fechas e historial de cambios. CONTIENE datos personales (área protegida, junto al lote; NUNCA en `human-capital/profiles/`). No es un perfil ni crea nada en
                            el Banco. NO se borra solo ni lo borra el descarte del área temporal: la política de retención está pendiente de definición.

Una unidad = un CONTENIDO de documento (`content_key`, derivado del hash de contenido que informa Graph, o del id Graph si no hay hash). Un mismo contenido que aparece en varias carpetas es UN registro con TODAS
sus especialidades. Cada registro lleva `revision`; el estado por documento y las excepciones quedan persistidos tras cada paso, de modo que un corte (token vencido, tiempo, error) se reanuda sin repetir trabajo.

DURABILIDAD: en modo `remote`, si Spaces no confirma una escritura se lanza `ManifestDurabilityError` y el lote se detiene (nunca se avanza sin checkpoint confirmado). En `local` (desarrollo/tests) alcanza con el
disco. En `read-only` no se escribe."""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import config
from core import remote_store

SCHEMA = "cv-history-manifest/v1"
STAGING_RETENTION_DAYS = 14          # vigencia del área temporal con datos personales; NO hay borrado automático: la eliminación es una acción explícita del owner (`discard_staging`)
_DIR_NAME, _SUB = "human-capital", "import-history"
RUN_ID_RE = re.compile(r"^ch-\d{8}T\d{6}Z(?:-[a-z0-9]{4})?$")
KEY_RE = re.compile(r"^[0-9a-f]{16}$")
_lock = threading.Lock()
_RESTORE_WAIT_TIMEOUT_S = 15.0


class ManifestError(Exception):
    """Base."""


class ManifestNotFoundError(ManifestError):
    pass


class ManifestDurabilityError(ManifestError):
    """La escritura no quedó confirmada en Spaces (o el modo no permite escribir): el lote se detiene, sin avanzar sin checkpoint."""


class ManifestInvalidIdError(ManifestError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id(now: datetime | None = None, suffix: str = "") -> str:
    base = "ch-" + (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"{base}-{suffix}" if suffix else base


def _root() -> Path:
    return Path(config.BASE_DIR) / _DIR_NAME / _SUB


def _run_dir(run_id: str) -> Path:
    if not RUN_ID_RE.match(str(run_id or "")):
        raise ManifestInvalidIdError("run_id inválido")
    return _root() / run_id


def _key_ok(key: str) -> str:
    if not KEY_RE.match(str(key or "")):
        raise ManifestInvalidIdError("content_key inválido")
    return key


def _ensure_restored() -> None:
    if remote_store.enabled():
        remote_store.await_restore(timeout=_RESTORE_WAIT_TIMEOUT_S)


def _read(path: Path) -> dict | None:
    _ensure_restored()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise ManifestError("manifiesto ilegible") from None


def _write(path: Path, payload: dict) -> None:
    if remote_store.read_only_mode():
        raise ManifestDurabilityError("modo de solo lectura")
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)
    with _lock:
        outcome = remote_store.write_through(path, text)
    if outcome != remote_store.DURABLE_REMOTE and not remote_store.local_only():
        raise ManifestDurabilityError("escritura no confirmada en Spaces")


# ── lote ──────────────────────────────────────────────────────────────────────
def create_run(run_id: str, meta: dict) -> dict:
    path = _run_dir(run_id) / "run.json"
    if _read(path) is not None:
        raise ManifestError("el lote ya existe")
    doc = {"schema": SCHEMA, "run_id": run_id, "revision": 1, "created_at": now_iso(), "updated_at": now_iso(), **meta}
    _write(path, doc)
    return doc


def get_run(run_id: str) -> dict:
    doc = _read(_run_dir(run_id) / "run.json")
    if doc is None:
        raise ManifestNotFoundError("lote inexistente")
    return doc


def update_run(run_id: str, mutate) -> dict:
    with _lock:
        doc = get_run(run_id)
        mutate(doc)
        doc["revision"] = int(doc.get("revision", 1)) + 1
        doc["updated_at"] = now_iso()
    _write(_run_dir(run_id) / "run.json", doc)
    return doc


def list_runs() -> list[dict]:
    _ensure_restored()
    root = _root()
    if not root.is_dir():
        return []
    out = []
    for entry in sorted(root.iterdir(), reverse=True):
        if entry.is_dir() and RUN_ID_RE.match(entry.name):
            try:
                doc = get_run(entry.name)
            except ManifestError:
                continue
            out.append(doc)
    return out


# ── registros por contenido (área temporal protegida) ─────────────────────────
def put_group(run_id: str, key: str, doc: dict) -> dict:
    path = _run_dir(run_id) / "staging" / f"{_key_ok(key)}.json"
    previous = _read(path)
    doc = {**doc, "content_key": key, "revision": int((previous or {}).get("revision", 0)) + 1, "updated_at": now_iso()}
    doc.setdefault("created_at", (previous or {}).get("created_at") or now_iso())
    _write(path, doc)
    return doc


def get_group(run_id: str, key: str) -> dict | None:
    return _read(_run_dir(run_id) / "staging" / f"{_key_ok(key)}.json")


def list_groups(run_id: str) -> list[dict]:
    _ensure_restored()
    directory = _run_dir(run_id) / "staging"
    if not directory.is_dir():
        return []
    out = []
    for entry in sorted(directory.iterdir()):
        if entry.is_file() and entry.suffix == ".json" and KEY_RE.match(entry.stem):
            doc = _read(entry)
            if doc:
                out.append(doc)
    return out


def _delete_dir_files(directory: Path) -> int:
    removed = 0
    if not directory.is_dir():
        return 0
    for entry in sorted(directory.iterdir()):
        if not (entry.is_file() and entry.suffix == ".json" and KEY_RE.match(entry.stem)):
            continue
        previous = entry.read_bytes()
        entry.unlink()
        if not (remote_store.local_only() or remote_store.delete_path(entry)):
            entry.write_bytes(previous)                                   # rollback: local y remoto nunca divergen
            raise ManifestDurabilityError("borrado no confirmado en Spaces")
        removed += 1
    return removed


def _delete_file(path: Path) -> int:
    if not path.is_file():
        return 0
    previous = path.read_bytes()
    path.unlink()
    if not (remote_store.local_only() or remote_store.delete_path(path)):
        path.write_bytes(previous)                                        # rollback: local y remoto nunca divergen
        raise ManifestDurabilityError("borrado no confirmado en Spaces")
    return 1


def delete_staging_files(run_id: str) -> int:
    """Elimina, UNO por UNO (nunca un borrado de directorio), los archivos con datos personales del lote —el área temporal y los dictámenes con correcciones manuales—: local y Spaces, con rollback local si el
    remoto no se confirma. Idempotente (lo ya eliminado no cuenta). Devuelve cuántos archivos eliminó en esta llamada. La auditoría y `run.json` NO se tocan."""
    if remote_store.read_only_mode():
        raise ManifestDurabilityError("modo de solo lectura")
    return _delete_dir_files(_run_dir(run_id) / "staging") + _delete_dir_files(_run_dir(run_id) / "verdicts") + _delete_file(_run_dir(run_id) / "import-index.json")


def staging_files_present(run_id: str) -> int:
    _ensure_restored()
    total = 1 if (_run_dir(run_id) / "import-index.json").is_file() else 0
    for sub in ("staging", "verdicts"):
        directory = _run_dir(run_id) / sub
        if directory.is_dir():
            total += sum(1 for e in directory.iterdir() if e.is_file() and e.suffix == ".json" and KEY_RE.match(e.stem))
    return total


# ── lotes de IMPORTACIÓN: índice de contenidos (protegido) y plan (sin datos personales) ───────────────────
def put_index(run_id: str, doc: dict) -> dict:
    """Índice de los contenidos del origen (uno por `content_key`, con su procedencia). Contiene nombres de archivo: va en el área protegida. Se escribe UNA vez al crear el lote; los registros por contenido
    se materializan al procesarlos (un lote de ~800 contenidos no puede escribir 800 archivos en una sola petición)."""
    doc = {**doc, "schema": SCHEMA, "run_id": run_id, "created_at": now_iso()}
    _write(_run_dir(run_id) / "import-index.json", doc)
    return doc


def get_index(run_id: str) -> dict | None:
    return _read(_run_dir(run_id) / "import-index.json")


def put_plan(run_id: str, doc: dict) -> dict:
    """Plan de importación: decisión por contenido. SIN datos personales (solo códigos, `content_key` y el `profile_id` de perfiles existentes). Reemplaza al anterior y suma `revision`."""
    path = _run_dir(run_id) / "import-plan.json"
    previous = _read(path)
    doc = {**doc, "schema": SCHEMA, "run_id": run_id, "revision": int((previous or {}).get("revision", 0)) + 1}
    _write(path, doc)
    return doc


def get_plan(run_id: str) -> dict | None:
    return _read(_run_dir(run_id) / "import-plan.json")


# ── libro de ejecución de una importación (durable, sin datos personales) ───────────────────────────────────
def put_ledger(run_id: str, key: str, entry: dict) -> dict:
    path = _run_dir(run_id) / "ledger" / f"{_key_ok(key)}.json"
    previous = _read(path)
    doc = {**entry, "schema": SCHEMA, "run_id": run_id, "content_key": key, "revision": int((previous or {}).get("revision", 0)) + 1, "updated_at": now_iso()}
    doc.setdefault("created_at", (previous or {}).get("created_at") or now_iso())
    _write(path, doc)
    return doc


def get_ledger(run_id: str, key: str) -> dict | None:
    return _read(_run_dir(run_id) / "ledger" / f"{_key_ok(key)}.json")


def list_ledgers(run_id: str) -> dict[str, dict]:
    _ensure_restored()
    directory = _run_dir(run_id) / "ledger"
    out: dict[str, dict] = {}
    if not directory.is_dir():
        return out
    for entry in sorted(directory.iterdir()):
        if entry.is_file() and entry.suffix == ".json" and KEY_RE.match(entry.stem):
            doc = _read(entry)
            if doc:
                out[entry.stem] = doc
    return out


# ── dictámenes del owner (por lote; append-only; NUNCA reescriben el resultado de la extracción) ──────────────
def get_verdict(run_id: str, key: str) -> dict | None:
    return _read(_run_dir(run_id) / "verdicts" / f"{_key_ok(key)}.json")


def append_verdict(run_id: str, key: str, entry: dict) -> dict:
    """Agrega un dictamen al historial del documento EN ESTE LOTE y lo deja como vigente. El historial no se edita ni se borra (salvo el descarte del lote). Los dictámenes de otro lote son otro archivo."""
    path = _run_dir(run_id) / "verdicts" / f"{_key_ok(key)}.json"
    with _lock:
        doc = _read(path) or {"schema": SCHEMA, "run_id": run_id, "content_key": key, "history": []}
        stamped = {**entry, "ts": now_iso(), "seq": len(doc["history"]) + 1}
        doc["history"].append(stamped)
        doc["current"] = stamped
    _write(path, doc)
    return doc


def list_verdicts(run_id: str) -> dict[str, dict]:
    _ensure_restored()
    directory = _run_dir(run_id) / "verdicts"
    out: dict[str, dict] = {}
    if not directory.is_dir():
        return out
    for entry in sorted(directory.iterdir()):
        if entry.is_file() and entry.suffix == ".json" and KEY_RE.match(entry.stem):
            doc = _read(entry)
            if doc:
                out[entry.stem] = doc
    return out


# ── borradores del owner por excepción (área protegida del lote; NO son perfiles ni tocan el Banco) ─────────────────
DRAFT_SCHEMA = "cv-history-draft/v1"
_draft_lock = threading.Lock()


def get_draft(run_id: str, key: str) -> dict | None:
    return _read(_run_dir(run_id) / "drafts" / f"{_key_ok(key)}.json")


def list_drafts(run_id: str) -> dict[str, dict]:
    _ensure_restored()
    directory = _run_dir(run_id) / "drafts"
    out: dict[str, dict] = {}
    if not directory.is_dir():
        return out
    for entry in sorted(directory.iterdir()):
        if entry.is_file() and entry.suffix == ".json" and KEY_RE.match(entry.stem):
            doc = _read(entry)
            if doc:
                out[entry.stem] = doc
    return out


def count_drafts(run_id: str) -> int:
    """Cantidad de borradores guardados del lote (solo cuenta archivos: no lee ni expone su contenido)."""
    _ensure_restored()
    directory = _run_dir(run_id) / "drafts"
    return sum(1 for e in directory.iterdir() if e.is_file() and e.suffix == ".json" and KEY_RE.match(e.stem)) if directory.is_dir() else 0


def update_draft(run_id: str, key: str, build) -> dict | None:
    """Lee el borrador vigente (o None), llama `build(previous)` y persiste lo que devuelva, TODO bajo un mismo candado: la comparación de revisión que hace `build` y la escritura no se pisan entre peticiones.
    `build` puede lanzar (validación o conflicto de revisión): en ese caso no se escribe nada. Devuelve el documento escrito, o el vigente si `build` devuelve None (sin cambios).

    Errores parciales: si la escritura local se hizo pero Spaces no la confirmó (`ManifestDurabilityError`), se RESTAURA el contenido anterior (local y remoto nunca divergen) y se relanza: el llamador informa que NO se guardó, y
    reintentar con la misma revisión funciona. Tras escribir se relee el archivo: si otro proceso del mismo disco lo pisó en el medio, falla en vez de informar un éxito falso. Escribe SOLO `drafts/<content_key>.json`: ni
    `run.json`, ni el libro, ni el staging, ni la auditoría, ni el Banco. No hay borrado: el historial vive dentro del borrador."""
    path = _run_dir(run_id) / "drafts" / f"{_key_ok(key)}.json"
    if remote_store.read_only_mode():
        raise ManifestDurabilityError("modo de solo lectura")
    with _draft_lock:
        previous = _read(path)
        built = build(previous)
        if built is None:
            return previous
        doc = {**built, "schema": DRAFT_SCHEMA, "run_id": run_id, "content_key": key}
        try:
            _write(path, doc)
        except ManifestDurabilityError:
            _restore_draft(path, previous)
            raise
        again = _read(path) or {}
        if (again.get("revision"), again.get("updated_at"), again.get("updated_by")) != (doc.get("revision"), doc.get("updated_at"), doc.get("updated_by")):
            raise ManifestError("el borrador cambió durante el guardado")
        return doc


def _restore_draft(path: Path, previous: dict | None) -> None:
    try:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            remote_store.write_through(path, json.dumps(previous, ensure_ascii=False, sort_keys=True, indent=1))
    except OSError:
        pass                                                                                     # si ni siquiera se puede restaurar, el error original (no confirmado) ya informa que NO se guardó


# ── auditoría (sin datos personales) ──────────────────────────────────────────
def append_audit(run_id: str, key: str, event: str, *, code: str | None = None, **extra) -> dict:
    """Agrega un evento SIN datos personales (solo estado, código de excepción y contadores). `extra` se limita a escalares."""
    path = _run_dir(run_id) / "audit" / f"{_key_ok(key)}.json"
    doc = _read(path) or {"schema": SCHEMA, "run_id": run_id, "content_key": key, "events": []}
    entry = {"ts": now_iso(), "event": event}
    if code:
        entry["code"] = code
    entry.update({k: v for k, v in extra.items() if isinstance(v, (str, int, float, bool)) or v is None})
    doc["events"].append(entry)
    _write(path, doc)
    return doc


def get_audit(run_id: str, key: str) -> dict | None:
    return _read(_run_dir(run_id) / "audit" / f"{_key_ok(key)}.json")
