"""cv_history_manifest.py — manifiesto PERSISTENTE, reanudable y auditable de la importación histórica de CVs (piloto de extracción y conciliación).

Vive bajo `human-capital/import-history/<run_id>/` con el MISMO mecanismo de persistencia que el resto de CCHH (`remote_store.write_through`: escritura atómica local + write-through a Spaces); no hay un
segundo mecanismo. Separa por diseño lo personal de lo auditable:

  run.json                  metadatos del lote (origen sin nombres, autorización del owner, población, contadores). SIN datos personales de candidatos.
  staging/<content_key>.json  ÁREA TEMPORAL PROTEGIDA: por contenido de documento, los datos extraídos (nombre, email, celular), nombre de archivo, candidatos y coincidencias. CONTIENE datos personales:
                            solo se lee desde la vista del owner; no se registra en logs ni en reportes generales; está pensada para descartarse al cerrar la importación.
  audit/<content_key>.json    AUDITORÍA: una lista de eventos (estado, código de excepción, hora). SIN datos personales: sobrevive al descarte del área temporal.

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


def delete_staging_files(run_id: str) -> int:
    """Elimina, UNO por UNO (nunca un borrado de directorio), los archivos del área temporal con datos personales: local y Spaces, con rollback local si el remoto no se confirma. Idempotente (lo ya
    eliminado no cuenta). Devuelve cuántos archivos eliminó en esta llamada. La auditoría y `run.json` NO se tocan."""
    if remote_store.read_only_mode():
        raise ManifestDurabilityError("modo de solo lectura")
    directory = _run_dir(run_id) / "staging"
    if not directory.is_dir():
        return 0
    removed = 0
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


def staging_files_present(run_id: str) -> int:
    _ensure_restored()
    directory = _run_dir(run_id) / "staging"
    return sum(1 for e in directory.iterdir() if e.is_file() and e.suffix == ".json" and KEY_RE.match(e.stem)) if directory.is_dir() else 0


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
