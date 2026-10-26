"""publication_txn.py — primitivas transaccionales de la publicación del Catálogo (FASE 0: H1/H3/H5/H6).

Sin dependencias de la app (las usa el exportador, que también corre como script). Todo lo que acá vive es **inerte** salvo que ``DANWOR_PUBLICATION_SAFE``
esté activo: con el interruptor apagado el camino de publicación es el de siempre (ver ``docs/PUBLICATION_SAFE_RUNBOOK.md``).

Piezas:
  * ``safe_mode_enabled`` — interruptor ``DANWOR_PUBLICATION_SAFE`` (apagado por defecto).
  * ``writer_lock`` — candado de ESCRITOR ÚNICO de ``catalog_local``, de hilo y de SISTEMA OPERATIVO (``flock``/``msvcrt``): protege entre procesos que comparten el
    disco; NO protege entre instancias con discos distintos (App Platform debe tener réplica única: precondición temporal).
  * ``load_manifest`` / ``seal_manifest`` — manifiestos con integridad y carga TRI-ESTADO (ausente · ok · ilegible · esquema inválido · digest incorrecto):
    un manifiesto que no se puede leer **nunca** equivale a «no posee nada».
  * ``Journal`` + ``UndoLog`` + ``recover_pending`` — journal por operación y copia de deshacer: ante cualquier falla se restaura el estado previo; una caída se
    recupera al inicio de la siguiente operación.
  * ``fault`` — punto de inyección de fallos (no-op salvo en pruebas).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:                                              # el exportador puede correr como script con otro sys.path
    from core import atomic_io
except ImportError:                               # pragma: no cover - ruta de script
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core import atomic_io

SAFE_ENV = "DANWOR_PUBLICATION_SAFE"
LOCK_DIR_ENV = "DANWOR_CATALOG_LOCK_DIR"
DEFAULT_LOCK_TIMEOUT_S = 30.0
_LOCK_POLL_S = 0.05
JOURNAL_KEEP = 50

# ── estados del contrato de resultado (ver diseño §3) ──
COMPLETED = "COMPLETED"
FAILED_NO_CHANGE = "FAILED_NO_CHANGE"
ROLLED_BACK = "ROLLED_BACK"
COMMITTED_UNCONFIRMED = "COMMITTED_UNCONFIRMED"
COMMITTED_CLEANUP_DEFERRED = "COMMITTED_CLEANUP_DEFERRED"
RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
SUCCESS_STATUSES = frozenset({COMPLETED})
PARTIAL_STATUSES = frozenset({COMMITTED_UNCONFIRMED, COMMITTED_CLEANUP_DEFERRED})

# durabilidad
DURABILITY_REMOTE = "remote_confirmed"
DURABILITY_LOCAL_BY_CONFIG = "local_only_by_config"
DURABILITY_UNCONFIRMED = "unconfirmed"
DURABILITY_NOT_REQUESTED = "not_requested"


def safe_mode_enabled() -> bool:
    """Interruptor ``DANWOR_PUBLICATION_SAFE`` (apagado por defecto). Se lee UNA vez por operación (la operación en curso no cambia de camino a mitad)."""
    return os.getenv(SAFE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_operation_id() -> str:
    return uuid.uuid4().hex


def host_id() -> str:
    return os.getenv("HOSTNAME") or socket.gethostname()


# ═════════════════════════ inyección de fallos (solo pruebas) ═════════════════════════
FAULT_HOOK: Callable[..., None] | None = None


def fault(point: str, **ctx: Any) -> None:
    """Punto de inyección nombrado. En producción ``FAULT_HOOK`` es ``None`` (no hace nada); las pruebas lo fijan para lanzar una excepción o simular una caída."""
    hook = FAULT_HOOK
    if hook is not None:
        hook(point, **ctx)


# ═════════════════════════ candado de escritor único ═════════════════════════
class CatalogBusyError(RuntimeError):
    """El candado de escritor de ``catalog_local`` no se obtuvo a tiempo; no se escribió nada."""


class RecoveryError(RuntimeError):
    """No se pudo recuperar una operación a medias; no se publica hasta resolverlo."""


_thread_lock = threading.RLock()
_state = threading.local()


def _lock_file() -> Path:
    base = os.getenv(LOCK_DIR_ENV, "").strip() or str(Path(tempfile.gettempdir()) / "danwor-catalog-locks")
    return Path(base) / "catalog-writer.lock"


if os.name == "nt":
    import msvcrt

    def _lock_handle(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_handle(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_handle(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_handle(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def writer_lock(timeout: float | None = None):
    """Exclusión de ESCRITOR ÚNICO sobre ``catalog_local``: candado de hilo (``RLock``) y, encima, uno de SISTEMA OPERATIVO sobre un archivo FUERA de los directorios
    persistidos (no se sincroniza con Spaces) que el SO libera si el proceso muere. Orden fijo hilo→proceso. REENTRANTE por hilo. ``timeout``: segundos (``0`` =
    un solo intento). Vencido ⇒ ``CatalogBusyError`` sin haber escrito nada. NO protege entre instancias con discos distintos (precondición: réplica única)."""
    wait = DEFAULT_LOCK_TIMEOUT_S if timeout is None else max(0.0, float(timeout))
    depth = getattr(_state, "depth", 0)
    if depth > 0:
        _state.depth = depth + 1
        try:
            yield
        finally:
            _state.depth -= 1
        return
    deadline = time.monotonic() + wait
    if not _thread_lock.acquire(timeout=wait):
        raise CatalogBusyError("otra operación sobre el Catálogo está en curso (hilo)")
    handle = None
    try:
        path = _lock_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+b")
        while True:
            try:
                _lock_handle(handle)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise CatalogBusyError("otra operación sobre el Catálogo está en curso (proceso)") from None
                time.sleep(_LOCK_POLL_S)
        _state.depth = 1
        try:
            yield
        finally:
            _state.depth = 0
            _unlock_handle(handle)
    finally:
        if handle is not None:
            handle.close()
        _thread_lock.release()


@contextlib.contextmanager
def external_writer_guard(timeout: float = 0.0):
    """Para los OTROS productores de ``catalog_local`` (regeneración legacy, APIS_ONLY): con el interruptor APAGADO no hace nada (comportamiento de siempre); con
    el interruptor ENCENDIDO toman el mismo candado entre procesos y, si está ocupado, lanzan ``CatalogBusyError`` sin haber escrito nada."""
    if not safe_mode_enabled():
        yield
        return
    with writer_lock(timeout):
        yield


# ═════════════════════════ manifiestos: integridad y carga tri-estado ═════════════════════════
M_ABSENT, M_OK, M_UNREADABLE, M_INVALID_SCHEMA, M_DIGEST_MISMATCH = "ABSENT", "OK", "UNREADABLE", "INVALID_SCHEMA", "DIGEST_MISMATCH"
_VOLATILE_KEYS = ("createdAt", "operationId", "hostId", "integrity", "cleanup")


@dataclass(frozen=True)
class ManifestLoad:
    state: str
    data: dict = field(default_factory=dict)
    path: Path | None = None

    @property
    def usable(self) -> bool:
        """Se puede confiar en su contenido como ownership (ausente NO es utilizable: no hay nada que confiar)."""
        return self.state == M_OK


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def canonical_digest(manifest: dict) -> str:
    return hashlib.sha256(_canonical({k: v for k, v in manifest.items() if k != "integrity"}).encode("utf-8")).hexdigest()


def semantic_signature(manifest: dict) -> str:
    """Firma del CONTENIDO del manifiesto sin los campos volátiles (``createdAt``, ``operationId``, ``hostId``, ``integrity``): dos publicaciones sin cambios la comparten."""
    return hashlib.sha256(_canonical({k: v for k, v in manifest.items() if k not in _VOLATILE_KEYS}).encode("utf-8")).hexdigest()


def seal_manifest(manifest: dict) -> dict:
    sealed = {k: v for k, v in manifest.items() if k != "integrity"}
    sealed["integrity"] = {"algo": "sha256", "digest": canonical_digest(sealed)}
    return sealed


def _schema_ok(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    objects = data.get("objects", [])
    if not isinstance(objects, list):
        return False
    for obj in objects:
        if not isinstance(obj, dict) or not isinstance(obj.get("object_type"), str) or not isinstance(obj.get("technical_name"), str):
            return False
    return True


def load_manifest(path: str | Path) -> ManifestLoad:
    """Carga TRI-ESTADO. ``ABSENT`` (no existe) · ``OK`` · ``UNREADABLE`` (truncado/JSON inválido/ilegible) · ``INVALID_SCHEMA`` · ``DIGEST_MISMATCH`` (v3 alterado).
    Un manifiesto v2 (sin ``integrity``) es legado válido si parsea y respeta el esquema. Jamás se interpreta un fallo como «vacío»."""
    p = Path(path)
    if not p.exists():
        return ManifestLoad(M_ABSENT, {}, p)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return ManifestLoad(M_UNREADABLE, {}, p)
    if not _schema_ok(data):
        return ManifestLoad(M_INVALID_SCHEMA, {}, p)
    integrity = data.get("integrity")
    if integrity is not None:
        if not isinstance(integrity, dict) or integrity.get("digest") != canonical_digest(data):
            return ManifestLoad(M_DIGEST_MISMATCH, {}, p)
    return ManifestLoad(M_OK, data, p)


# ═════════════════════════ deshacer (undo) ═════════════════════════
@dataclass
class _UndoEntry:
    path: str
    existed: bool
    is_dir: bool
    backup: str | None


class UndoLog:
    """Copia de lo que una operación va a tocar. ``capture`` ANTES de escribir; ``restore`` deja los archivos como estaban (incluye borrar lo creado)."""

    def __init__(self, op_dir: Path) -> None:
        self.op_dir = Path(op_dir)
        self.entries: list[_UndoEntry] = []
        self._n = 0

    @classmethod
    def load(cls, op_dir: Path) -> "UndoLog":
        log = cls(op_dir)
        meta = Path(op_dir) / "undo.json"
        data = json.loads(meta.read_text(encoding="utf-8"))
        log.entries = [_UndoEntry(**e) for e in data["entries"]]
        return log

    def capture(self, path: str | Path) -> None:
        p = Path(path)
        if any(e.path == str(p) for e in self.entries):
            return
        backup = None
        if p.exists():
            self._n += 1
            backup = f"b{self._n}"
            dest = self.op_dir / "backup" / backup
            dest.parent.mkdir(parents=True, exist_ok=True)
            if p.is_dir():
                shutil.copytree(p, dest)
            else:
                shutil.copy2(p, dest)
        self.entries.append(_UndoEntry(str(p), p.exists(), p.is_dir(), backup))

    def save(self) -> None:
        atomic_io.write_text(self.op_dir / "undo.json", json.dumps({"entries": [e.__dict__ for e in self.entries]}, indent=1))

    def restore(self) -> list[str]:
        """Restaura en orden inverso. Devuelve la lista de rutas que NO quedaron idénticas a lo capturado (vacía = verificado)."""
        problems: list[str] = []
        for e in reversed(self.entries):
            target = Path(e.path)
            try:
                if not e.existed:
                    if target.is_dir():
                        shutil.rmtree(target)
                    elif target.exists():
                        target.unlink()
                    continue
                src = self.op_dir / "backup" / str(e.backup)
                if e.is_dir:
                    if target.exists():
                        shutil.rmtree(target) if target.is_dir() else target.unlink()
                    shutil.copytree(src, target)
                else:
                    if target.is_dir():
                        shutil.rmtree(target)
                    atomic_io.write_bytes(target, src.read_bytes())
            except OSError:
                problems.append(e.path)
        for e in self.entries:                                    # verificación final
            target = Path(e.path)
            if not e.existed:
                if target.exists():
                    problems.append(e.path)
            elif not e.is_dir and (not target.is_file() or _sha_file(target) != _sha_file(self.op_dir / "backup" / str(e.backup))):
                problems.append(e.path)
            elif e.is_dir and not target.is_dir():
                problems.append(e.path)
        return sorted(set(problems))


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text_file(path: Path) -> str:
    """sha256 del TEXTO normalizado (saltos de línea LF), igual al de ``sha256_text``: es lo que registra el manifiesto."""
    return sha256_text(Path(path).read_text(encoding="utf-8"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


# ═════════════════════════ journal y recuperación ═════════════════════════
J_PLANNED, J_APPLYING, J_LOCAL_COMMITTED, J_REMOTE_CONFIRMED, J_REMOTE_PENDING, J_ROLLED_BACK, J_DISCARDED = (
    "PLANNED", "APPLYING", "LOCAL_COMMITTED", "REMOTE_CONFIRMED", "REMOTE_PENDING", "ROLLED_BACK", "DISCARDED")
_PENDING_STATES = frozenset({J_PLANNED, J_APPLYING})


def runtime_dir(catalog_root: str | Path) -> Path:
    """Directorio de runtime de la publicación (journal, undo): HERMANO de ``catalog_local`` —en el mismo dispositivo— y FUERA de ``PERSIST_PATHS``: ``mirror_up``
    recorre también las carpetas con punto de lo persistido, así que nada transitorio puede vivir bajo ``catalog_local``."""
    return Path(catalog_root).resolve().parent / ".publication_runtime"


def journal_dir(catalog_root: str | Path) -> Path:
    return runtime_dir(catalog_root) / "journal"


def undo_dir(catalog_root: str | Path, operation_id: str) -> Path:
    return runtime_dir(catalog_root) / "undo" / operation_id


class Journal:
    def __init__(self, catalog_root: str | Path, operation_id: str, data: dict) -> None:
        self.catalog_root = Path(catalog_root)
        self.operation_id = operation_id
        self.data = dict(data)
        self.path = journal_dir(catalog_root) / f"{operation_id}.json"

    def update(self, state: str, **extra: Any) -> None:
        self.data.update(extra)
        self.data["state"] = state
        self.data["updated_at"] = now_iso()
        atomic_io.write_text(self.path, json.dumps(self.data, indent=1, ensure_ascii=False))

    @property
    def state(self) -> str:
        return str(self.data.get("state") or "")


def _prune_journals(catalog_root: Path) -> None:
    files = sorted(journal_dir(catalog_root).glob("*.json"), key=lambda p: p.stat().st_mtime)
    for stale in files[:-JOURNAL_KEEP]:
        try:
            if json.loads(stale.read_text(encoding="utf-8")).get("state") not in _PENDING_STATES:
                stale.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


def has_pending(catalog_root: str | Path) -> bool:
    """¿Hay un journal sin cerrar (PLANNED/APPLYING)? Barato y sin candado: lo usan los caminos que no toman el candado para negarse a trabajar sobre un estado a medias."""
    for jf in journal_dir(catalog_root).glob("*.json"):
        try:
            if json.loads(jf.read_text(encoding="utf-8")).get("state") in _PENDING_STATES:
                return True
        except (OSError, ValueError):
            return True                                              # un journal ilegible cuenta como pendiente: no se trabaja encima
    return False


def recover_pending(catalog_root: str | Path, *, timeout: float | None = None) -> list[dict]:
    """Recupera las operaciones que quedaron a medias (caída del proceso). Bajo el candado de escritor. ``PLANNED`` ⇒ se descarta (no se aplicó nada); ``APPLYING`` ⇒ se
    restaura el estado previo desde el undo. Idempotente; sin journal pendiente no hace nada. ``RecoveryError`` si una restauración no pudo verificarse (el journal queda
    ``APPLYING`` y no se publica hasta resolverlo)."""
    root = Path(catalog_root)
    if not journal_dir(root).is_dir() or not has_pending(root):
        return []
    results: list[dict] = []
    with writer_lock(timeout):
        for jf in sorted(journal_dir(root).glob("*.json")):
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RecoveryError(f"journal ilegible: {jf.name}") from exc
            state = data.get("state")
            if state not in _PENDING_STATES:
                continue
            op = str(data.get("operation_id") or jf.stem)
            journal = Journal(root, op, data)
            if state == J_PLANNED:
                _clean_temporaries(data)
                journal.update(J_DISCARDED, recovered=True)
                results.append({"operation_id": op, "from": state, "action": "DISCARDED"})
                continue
            od = undo_dir(root, op)
            try:
                problems = UndoLog.load(od).restore()
            except (OSError, ValueError, KeyError) as exc:
                raise RecoveryError(f"no se pudo restaurar la operación {op}") from exc
            if problems:
                raise RecoveryError(f"la restauración de la operación {op} no quedó verificada ({len(problems)} ruta(s))")
            _clean_temporaries(data)
            journal.update(J_ROLLED_BACK, recovered=True)
            shutil.rmtree(od, ignore_errors=True)
            results.append({"operation_id": op, "from": state, "action": "ROLLED_BACK"})
        _prune_journals(root)
    return results


def _clean_temporaries(journal_data: dict) -> None:
    for d in journal_data.get("touched_dirs") or []:
        atomic_io.remove_orphan_temporaries(d)
