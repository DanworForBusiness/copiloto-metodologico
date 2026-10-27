"""publication_intent.py — registro de INTENCIÓN de una publicación: la evidencia que permite recuperarla desde Spaces aunque se pierda el disco (P1 · B1).

El journal y el undo viven en el disco local (``.publication_runtime``): un filesystem efímero perdido en mitad de la confirmación los pierde, y Spaces puede quedar con parte de la versión
nueva y parte de la previa. Para poder decidir —verificando— si la versión nueva quedó COMPLETA (se activa) o no (se vuelve a la anterior), antes de escribir un solo objeto del cuerpo se
registra, DENTRO de lo que ya se persiste (``catalog_local/.publication_state/<cliente>.intent/``, sin otro sistema de persistencia), lo mínimo e imprescindible:

  * ``intent.json``  por cada archivo que CAMBIA: digest PREVIO y digest OBJETIVO (ausente = ``null``); cuáles son marcadores de activación; operación y operación previa.
  * ``pre/<sha256>`` los bytes PREVIOS de lo que cambia o se retira (la imagen previa: sin ella no hay forma de volver atrás una vez sobrescrito el objeto).
  * ``post/<sha256>`` los bytes OBJETIVO de los marcadores (para completar hacia adelante si el cuerpo quedó completo y solo faltó el marcador).

``intent.json`` se sube AL FINAL del registro y ANTES de tocar el cuerpo: sin él nada se escribió en el cuerpo. Se retira cuando la publicación queda confirmada o deshecha.

``resolve`` clasifica el estado REAL de Spaces contra el registro (nunca asume): cuerpo completo en el objetivo → se completa hacia adelante (marcadores incluidos); cualquier otra mezcla →
se vuelve a la imagen previa, local y remota. Un objeto que no es ni el previo ni el objetivo, o una imagen previa que falta, NO se adivina: queda BLOQUEADO con un código explícito.
Idempotente: cada paso lee el remoto antes de escribir, así que repetirlo tras otra interrupción converge al mismo resultado.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

try:
    from core import atomic_io
except ImportError:                               # pragma: no cover - ruta de script
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core import atomic_io

from tools.catalog import publication_remote as pr

SCHEMA = "publication-intent/v1"
STATE_DIR = ".publication_state"
SUFFIX = ".intent"
FILE = "intent.json"

# resultados de ``resolve``
NONE, COMMITTED, ROLLED_BACK, DIVERGED, UNCERTAIN, BLOCKED = "NONE", "COMMITTED", "ROLLED_BACK", "DIVERGED", "UNCERTAIN", "BLOCKED"
# códigos explícitos de bloqueo (se muestran al operador y al lector)
INTENT_UNREADABLE, REMOTE_UNEXPECTED_STATE, PRE_IMAGE_MISSING, RESTORE_INCOMPLETE, REMOTE_UNREADABLE, KEY_NOT_PERSISTED, REMOTE_NOT_AUTHORIZED = (
    "INTENT_UNREADABLE", "REMOTE_UNEXPECTED_STATE", "PRE_IMAGE_MISSING", "RESTORE_INCOMPLETE", "REMOTE_UNREADABLE", "KEY_NOT_PERSISTED", "REMOTE_NOT_AUTHORIZED")


@dataclass
class Resolution:
    status: str
    client_dir: str = ""
    operation_id: str = ""
    code: str = ""
    detail: str = ""
    problems: list[str] = field(default_factory=list)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fault(point: str, **ctx) -> None:
    """Punto de inyección de fallos de la RECUPERACIÓN (no-op fuera de las pruebas): permite interrumpir la recuperación misma y comprobar que otra pasada converge."""
    from tools.catalog import publication_txn
    publication_txn.fault(point, **ctx)


def intent_dir(catalog_root: str | Path, client_dir: str) -> Path:
    return Path(catalog_root) / STATE_DIR / f"{client_dir}{SUFFIX}"


def intent_dirs(catalog_root: str | Path) -> list[Path]:
    base = Path(catalog_root) / STATE_DIR
    try:
        return sorted(p for p in base.iterdir() if p.is_dir() and p.name.endswith(SUFFIX))
    except OSError:
        return []


def client_of(folder: Path) -> str:
    return folder.name[: -len(SUFFIX)]


def open_intents(catalog_root: str | Path) -> list[str]:
    """Clientes con un registro COMMITIDO (``intent.json`` presente) sin cerrar: una publicación cuya confirmación remota no se resolvió. Barato y sin candado."""
    return [client_of(d) for d in intent_dirs(catalog_root) if (d / FILE).exists()]


def dirs_of(rs) -> dict[str, Path]:
    """Directorios persistidos (nombre lógico → ruta local) tal como los resuelve ``remote_store`` (la MISMA fuente de verdad del mapeo local→clave)."""
    return {name: Path(local).resolve() for name, local in rs._persist_dirs()}


def key_of(dirs: dict[str, Path], path: Path) -> str | None:
    resolved = Path(path).resolve()
    for name, base in dirs.items():
        try:
            return f"{name}/{resolved.relative_to(base).as_posix()}"
        except ValueError:
            continue
    return None


def path_of(dirs: dict[str, Path], key: str) -> Path | None:
    name, _, rel = key.partition("/")
    return dirs[name] / rel if name in dirs and rel else None


# ═════════════════════════ escribir el registro (antes de tocar el cuerpo) ═════════════════════════
class IntentError(RuntimeError):
    """No se pudo registrar la intención de forma confirmada: el cuerpo remoto NO se tocó."""


def write(rs, catalog_root: str | Path, client_dir: str, *, operation_id: str, previous_operation_id: str | None, created_at: str, keyed: dict[str, Path],
          removed: dict[str, Path], listing: dict[str, dict], markers: Iterable[str]) -> Path:
    """Construye, escribe en local y CONFIRMA en Spaces el registro. ``keyed``: clave → ruta de cada archivo de la versión NUEVA (incluye los marcadores); ``removed``: lo que la versión
    nueva retira; ``listing``: inventario remoto previo (clave → metadatos). Devuelve la ruta de ``intent.json``. ``IntentError`` si no quedó confirmado (el cuerpo remoto sigue intacto)."""
    names = tuple(markers)
    dirs = dirs_of(rs)
    folder = intent_dir(catalog_root, client_dir)
    shutil.rmtree(folder, ignore_errors=True)                                  # un registro viejo sin cerrar jamás se mezcla con el nuevo
    spec: dict[str, dict] = {}
    pre: dict[str, bytes] = {}
    post: dict[str, bytes] = {}
    try:
        for key, path in sorted(keyed.items()):
            data = Path(path).read_bytes()
            target, prev, meta = _sha(data), None, listing.get(key)
            if meta is not None:
                etag = str(meta.get("etag") or "")
                if etag and "-" not in etag and etag == hashlib.md5(data).hexdigest() and (meta.get("size") is None or int(meta["size"]) == len(data)):  # noqa: S324 — identidad de contenido
                    continue                                                   # idéntico a lo que ya hay en Spaces: no cambia, no necesita imagen previa
                remote = rs.read_object_bytes(path)
                if remote is not None:
                    if _sha(remote) == target:
                        continue
                    prev = _sha(remote)
                    pre[prev] = remote
            spec[key] = {"prev": prev, "target": target}
            if Path(path).name in names:
                post[target] = data
        for key, path in sorted(removed.items()):
            if key not in listing:
                continue                                                       # ya no está en Spaces: nada que deshacer
            remote = rs.read_object_bytes(path)
            if remote is None:
                continue
            prev = _sha(remote)
            pre[prev] = remote
            spec[key] = {"prev": prev, "target": None}
    except rs.RemoteReadError as exc:
        raise IntentError("no se pudo leer el estado previo en Spaces") from exc
    marker_keys = sorted(k for k, p in keyed.items() if Path(p).name in names)
    document = {"schema": SCHEMA, "operation_id": operation_id, "previous_operation_id": previous_operation_id, "client_dir": client_dir, "created_at": created_at,
                "markers": sorted(names), "marker_keys": marker_keys, "keys": spec}
    files: dict[str, Path] = {}
    for sha, data in sorted(pre.items()):
        files[f"pre/{sha}"] = folder / "pre" / sha
        atomic_io.write_bytes(folder / "pre" / sha, data)
    for sha, data in sorted(post.items()):
        files[f"post/{sha}"] = folder / "post" / sha
        atomic_io.write_bytes(folder / "post" / sha, data)
    atomic_io.write_text(folder / FILE, json.dumps(document, indent=1, ensure_ascii=False, sort_keys=True))
    keyed_blobs: dict[str, Path] = {}
    for path in files.values():
        key = key_of(dirs, path)
        if key is None:
            raise IntentError("el registro de intención cae fuera de lo persistido")
        keyed_blobs[key] = path
    main_key = key_of(dirs, folder / FILE)
    if main_key is None:
        raise IntentError("el registro de intención cae fuera de lo persistido")
    if keyed_blobs:                                                            # las imágenes primero y VERIFICADAS: ``intent.json`` solo se sube si todo lo que promete existe
        blobs = pr.converge(rs, keyed_blobs)
        if blobs.status != pr.CONFIRMED:
            raise IntentError("las imágenes previas no quedaron confirmadas en Spaces (" + blobs.status + ")")
    outcome = pr.converge(rs, {main_key: folder / FILE})
    if outcome.status != pr.CONFIRMED:
        raise IntentError("el registro de intención no quedó confirmado en Spaces (" + outcome.status + ")")
    return folder / FILE


def close(rs, catalog_root: str | Path, client_dir: str) -> bool:
    """Retira el registro (la publicación quedó confirmada o deshecha). ``intent.json`` primero y verificado: sin él el registro ya no existe para la recuperación. True si quedó cerrado."""
    folder = intent_dir(catalog_root, client_dir)
    dirs = dirs_of(rs)
    main = folder / FILE
    if rs.write_enabled() and main.exists():
        rs.delete_path(main)
        try:
            if rs.persisted_object_exists(main):
                return False                                                   # sigue en Spaces: el registro queda abierto (se resuelve de nuevo, idempotente)
        except rs.RemoteReadError:
            return False
    with contextlib.suppress(OSError):
        main.unlink(missing_ok=True)
    if rs.write_enabled():
        for blob in sorted((folder / "pre").glob("*")) + sorted((folder / "post").glob("*")):
            if key_of(dirs, blob):
                rs.delete_path(blob)                                           # best-effort: un blob huérfano sin intent.json es inocuo (se limpia en la próxima limpieza)
    shutil.rmtree(folder, ignore_errors=True)
    return True


def close_if(rs, catalog_root: str | Path, client_dir: str, operation_id: str) -> bool:
    """``close`` solo si el registro vigente es el de ``operation_id`` (jamás se retira el de otra operación)."""
    intent = _load(intent_dir(catalog_root, client_dir))
    if intent is not None and intent.get("operation_id") != operation_id:
        return False
    return close(rs, catalog_root, client_dir)


def purge_uncommitted(rs, catalog_root: str | Path, client_dir: str) -> None:
    """Blobs de un registro que NUNCA se confirmó (sin ``intent.json``): el cuerpo remoto no se tocó, se limpian y listo."""
    folder = intent_dir(catalog_root, client_dir)
    if not folder.is_dir() or (folder / FILE).exists():
        return
    dirs = dirs_of(rs)
    if rs.write_enabled():
        for blob in [*sorted((folder / "pre").glob("*")), *sorted((folder / "post").glob("*"))]:
            if key_of(dirs, blob):
                rs.delete_path(blob)
    shutil.rmtree(folder, ignore_errors=True)


# ═════════════════════════ resolver (recuperación) ═════════════════════════
def _load(folder: Path) -> dict | None:
    try:
        data = json.loads((folder / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("keys"), dict) or not isinstance(data.get("marker_keys"), list):
        return None
    for spec in data["keys"].values():
        if not isinstance(spec, dict) or "prev" not in spec or "target" not in spec:
            return None
    return data


def operation_of(folder: Path) -> str | None:
    intent = _load(folder)
    return None if intent is None else str(intent.get("operation_id") or "")


def _state(remote: bytes | None, spec: dict) -> str:
    """``target`` · ``prev`` · ``other`` (el objeto remoto no es ni la versión previa ni la objetivo)."""
    digest = None if remote is None else _sha(remote)
    if digest == spec["target"]:
        return "target"
    if digest == spec["prev"]:
        return "prev"
    return "other"


def _local_sha(path: Path) -> str | None:
    try:
        return _sha(path.read_bytes()) if path.is_file() else None
    except OSError:
        return None


def _set_local(path: Path, data: bytes | None) -> None:
    if data is None:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    atomic_io.write_bytes(path, data)


def resolve(rs, catalog_root: str | Path, client_dir: str) -> Resolution:
    """Resuelve el registro de UN cliente verificando Spaces. Ver el docstring del módulo. Al terminar (``COMMITTED``/``ROLLED_BACK``) el registro queda cerrado."""
    folder = intent_dir(catalog_root, client_dir)
    base = Resolution(NONE, client_dir)
    if not (folder / FILE).exists():
        purge_uncommitted(rs, catalog_root, client_dir)
        return base
    intent = _load(folder)
    if intent is None:
        return Resolution(BLOCKED, client_dir, "", INTENT_UNREADABLE, "el registro de intención está ilegible o fue alterado")
    op = str(intent.get("operation_id") or "")
    base = Resolution(NONE, client_dir, op)

    def result(status: str, code: str = "", detail: str = "", problems: list[str] | None = None) -> Resolution:
        return Resolution(status, client_dir, op, code, detail, list(problems or []))

    if not rs.read_enabled():
        return result(UNCERTAIN, REMOTE_NOT_AUTHORIZED, "Spaces no está autorizado para lectura en este modo")
    blocked = rs.sync_blocked_reason()
    if blocked:
        return result(UNCERTAIN, RESTORE_INCOMPLETE, blocked)
    dirs = dirs_of(rs)
    keys: dict[str, dict] = intent["keys"]
    paths: dict[str, Path] = {}
    for key in keys:
        p = path_of(dirs, key)
        if p is None:
            return result(BLOCKED, KEY_NOT_PERSISTED, f"la clave {key} ya no cae bajo un directorio persistido")
        paths[key] = p
    marker_keys = set(intent["marker_keys"])
    states: dict[str, str] = {}
    remote_bytes: dict[str, bytes | None] = {}
    try:
        for key, spec in keys.items():
            remote_bytes[key] = rs.read_object_bytes(paths[key])
            states[key] = _state(remote_bytes[key], spec)
    except rs.RemoteReadError as exc:
        return result(UNCERTAIN, REMOTE_UNREADABLE, str(exc)[:160])
    foreign = sorted(k for k, s in states.items() if s == "other" and k not in marker_keys)
    if foreign:
        return result(BLOCKED, REMOTE_UNEXPECTED_STATE, "objetos que no son ni la versión previa ni la nueva", foreign[:10])

    body = [k for k in keys if k not in marker_keys]
    forward = all(states[k] == "target" for k in body)
    _fault("intent_classified", forward=forward)
    if forward:
        done = _forward(rs, catalog_root, client_dir, folder, keys, paths, marker_keys, remote_bytes, result)
        if done is not None:
            return done
        # el marcador no se pudo completar: se vuelve atrás (la versión previa es la última confirmada)
    return _backward(rs, catalog_root, client_dir, folder, keys, paths, marker_keys, result, dirs, states, remote_bytes)


def _forward(rs, catalog_root, client_dir: str, folder: Path, keys: dict, paths: dict, marker_keys: set, remote_bytes: dict, result) -> Resolution | None:
    """El cuerpo remoto ya es la versión nueva: deja local y remoto idénticos al objetivo (marcadores incluidos). ``None`` si no se pudo completar (se vuelve atrás)."""
    for key, spec in keys.items():
        path, target = paths[key], spec["target"]
        if target is None:
            _set_local(path, None)
            continue
        if _local_sha(path) == target:
            continue
        data = remote_bytes.get(key) if remote_bytes.get(key) is not None and _sha(remote_bytes[key]) == target else None
        if data is None:
            blob = folder / "post" / target
            data = blob.read_bytes() if blob.is_file() and _local_sha(blob) == target else None
        if data is None:
            return None
        _set_local(path, data)
    _fault("intent_local_applied", direction="forward")
    out = pr.converge(rs, paths, last=[k for k in keys if k in marker_keys])
    if out.status == pr.UNCERTAIN:
        return result(UNCERTAIN, REMOTE_UNREADABLE, "no se pudo verificar el remoto al completar la versión nueva", out.problems)
    if out.status != pr.CONFIRMED:
        return None
    if any((_local_sha(paths[k]) != spec["target"]) for k, spec in keys.items()):
        return None
    _fault("intent_before_close", direction="forward")
    close(rs, catalog_root, client_dir)
    return result(COMMITTED)


def _backward(rs, catalog_root, client_dir: str, folder: Path, keys: dict, paths: dict, marker_keys: set, result, dirs: dict, states: dict, remote_bytes: dict) -> Resolution:
    """Vuelve a la imagen previa en local y en Spaces. Sin la imagen previa de un objeto que ya no es el previo NO se adivina: BLOQUEADO."""
    needed: dict[str, bytes | None] = {}
    for key, spec in keys.items():
        prev = spec["prev"]
        if prev is None:
            needed[key] = None
            continue
        if states.get(key) == "prev" and remote_bytes.get(key) is not None:      # Spaces ya tiene la versión previa: es su propia imagen
            needed[key] = remote_bytes[key]
            continue
        data = None
        blob = folder / "pre" / prev
        try:
            if blob.is_file():
                candidate = blob.read_bytes()
                data = candidate if _sha(candidate) == prev else None
            if data is None:
                key_blob = key_of(dirs, blob)
                if key_blob:
                    remote = rs.read_object_bytes(blob)
                    data = remote if remote is not None and _sha(remote) == prev else None
        except (OSError, rs.RemoteReadError):
            data = None
        if data is None:
            return result(BLOCKED, PRE_IMAGE_MISSING, f"falta la imagen previa de {key}", [key])
        needed[key] = data
    for key, data in needed.items():
        _set_local(paths[key], data)
    _fault("intent_local_applied", direction="backward")
    out = pr.converge(rs, paths, last=[k for k in keys if k in marker_keys])
    if out.status == pr.UNCERTAIN:
        return result(UNCERTAIN, REMOTE_UNREADABLE, "no se pudo verificar el remoto al volver a la versión previa", out.problems)
    if out.status != pr.CONFIRMED:
        return result(DIVERGED, "", "el local ya es la versión previa pero Spaces todavía no la refleja", out.problems)
    for key, spec in keys.items():
        if _local_sha(paths[key]) != spec["prev"]:
            return result(BLOCKED, REMOTE_UNEXPECTED_STATE, f"{key} no coincide con la versión previa tras restaurar", [key])
    _fault("intent_before_close", direction="backward")
    close(rs, catalog_root, client_dir)
    return result(ROLLED_BACK)
