"""governed_backup.py — respaldo VERIFICABLE y restauracion POR OBJETO de los sobres gobernados que una migracion va a reemplazar (sin asumir versionado de Spaces).

Respaldo (``create_backup``, ANTES de cualquier escritura; si no puede garantizarse => ``BackupError`` y la migracion NO escribe):
  * por cada sobre afectado guarda los BYTES EXACTOS locales (``objects/<ruta relativa al almacen>``) y, si el almacen remoto esta habilitado, los bytes EXACTOS del objeto remoto
    (``objects_remote/...`` solo si difieren del local; si no se puede leer el remoto, el respaldo FALLA: no se garantiza);
  * ``manifest.json``: esquema, migracion, ``plan_digest``, huella del entorno, y por objeto clave / tamano / sha256 local / estado y sha256 remoto; ``manifest.sha256`` sella el manifiesto;
  * se re-lee todo lo escrito y se compara contra el manifiesto (``BACKUP_VERIFY_FAILED``); el destino debe estar VACIO y fuera del almacen gobernado;
  * ``journal.jsonl`` append-only: un evento por objeto y paso (``BACKED_UP`` / ``COMMITTED`` (sha256 nuevo) / ``REMOTE_CONFIRMED`` / ``FAILED`` / ``RESTORED`` / ``RESTORE_CONFLICT`` ...).
Restauracion (``restore_object``): POR OBJETO y sin pisar cambios concurrentes: solo se restaura si el sobre local actual es EXACTAMENTE el que escribio la migracion (sha256 del evento ``COMMITTED``)
o ya es el respaldado (idempotente); cualquier otro contenido => ``CONFLICT_CONCURRENT_CHANGE`` y no se toca. Se escriben los bytes originales (atomico) y se vuelve a publicar en remoto verificando por bytes."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

BACKUP_SCHEMA = "governed-backup/v1"
EV_BACKED_UP, EV_COMMITTED, EV_REMOTE_CONFIRMED, EV_REMOTE_PENDING, EV_FAILED = "BACKED_UP", "COMMITTED", "REMOTE_CONFIRMED", "REMOTE_PENDING", "FAILED"
EV_RESTORED, EV_RESTORE_CONFLICT, EV_RESTORE_FAILED = "RESTORED", "RESTORE_CONFLICT", "RESTORE_FAILED"
R_SAME, R_DIFFERS, R_ABSENT, R_UNVERIFIABLE, R_DISABLED = "SAME", "DIFFERS", "ABSENT", "UNVERIFIABLE", "DISABLED"


class BackupError(Exception):
    """El respaldo no pudo garantizarse. ``code`` estable; la migracion NO debe escribir nada."""
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def manifest_digest(manifest: dict) -> str:
    return _sha(json.dumps({k: manifest[k] for k in ("schema", "migration", "plan_digest", "environment", "objects")}, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def append_journal(dest: Path, event: dict) -> dict:
    """Evento append-only (una linea JSON); un fallo al escribir el diario se propaga: no se sigue sin registro. Devuelve el evento escrito (con ``at``)."""
    rec = {"at": _now(), **event}
    line = json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n"
    with open(Path(dest) / "journal.jsonl", "ab") as f:
        f.write(line.encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
    return rec


def read_journal(dest: Path) -> list[dict]:
    p = Path(dest) / "journal.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.is_file() else []


def create_backup(dest: Path, targets: list[dict], *, governed_dir: Path, migration: str, plan_digest: str, environment: str,
                  remote_read: Callable[[Path], bytes | None] | None = None, require_remote: bool = False) -> dict:
    """``targets``: ``[{"client","root","path"}]`` (``path`` = archivo del sobre gobernado). ``remote_read(path)``: bytes exactos remotos | None (ausente) | lanza (irresoluble)."""
    dest, governed_dir = Path(dest).resolve(), Path(governed_dir).resolve()
    if not targets:
        raise BackupError("NOTHING_TO_BACK_UP")
    if dest == governed_dir or governed_dir in dest.parents or dest in governed_dir.parents:
        raise BackupError("BACKUP_DIR_INSIDE_GOVERNED_STORE", "el respaldo debe estar fuera del almacen gobernado")
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise BackupError("BACKUP_DIR_NOT_EMPTY", "el destino debe estar vacio (no se mezcla con otro respaldo)")
    try:
        dest.mkdir(parents=True, exist_ok=True)
        objects = []
        for t in sorted(targets, key=lambda t: (str(t["client"]), str(t["root"]))):
            p = Path(t["path"]).resolve()
            try:
                key = p.relative_to(governed_dir).as_posix()
            except ValueError:
                raise BackupError("TARGET_OUTSIDE_GOVERNED_STORE", str(t["client"])) from None
            if not p.is_file():
                raise BackupError("TARGET_MISSING", key)
            data = p.read_bytes()
            _atomic_write(dest / "objects" / key, data)
            remote_state, remote_sha = R_DISABLED, None
            if remote_read is not None:
                try:
                    rb = remote_read(p)
                except Exception as exc:                                              # noqa: BLE001 — no se puede garantizar el respaldo remoto
                    if require_remote:
                        raise BackupError("REMOTE_READ_FAILED", f"{key}: {type(exc).__name__}") from None
                    rb, remote_state = None, R_UNVERIFIABLE
                else:
                    remote_state = R_ABSENT if rb is None else (R_SAME if rb == data else R_DIFFERS)
                if rb is not None:
                    remote_sha = _sha(rb)
                    if rb != data:
                        _atomic_write(dest / "objects_remote" / key, rb)
            elif require_remote:
                raise BackupError("REMOTE_READ_FAILED", "almacen remoto no disponible")
            objects.append({"key": key, "client": t["client"], "root": t["root"], "size": len(data), "sha256": _sha(data), "remote_state": remote_state, "remote_sha256": remote_sha})
        manifest = {"schema": BACKUP_SCHEMA, "created_at": _now(), "migration": migration, "plan_digest": plan_digest, "environment": environment, "objects": objects}
        manifest["manifest_sha256"] = manifest_digest(manifest)
        _atomic_write(dest / "manifest.json", json.dumps(manifest, indent=1, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        _atomic_write(dest / "manifest.sha256", (manifest["manifest_sha256"] + "\n").encode("ascii"))
        problems = verify_backup(dest)
        if problems:
            raise BackupError("BACKUP_VERIFY_FAILED", "; ".join(problems[:3]))
        for o in objects:
            append_journal(dest, {"event": EV_BACKED_UP, "key": o["key"], "sha256": o["sha256"], "remote_state": o["remote_state"]})
        return manifest
    except BackupError:
        raise
    except OSError as exc:
        raise BackupError("BACKUP_WRITE_FAILED", type(exc).__name__) from None


def load_manifest(dest: Path) -> dict:
    try:
        manifest = json.loads((Path(dest) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise BackupError("MANIFEST_UNREADABLE") from None
    if manifest.get("schema") != BACKUP_SCHEMA:
        raise BackupError("MANIFEST_BAD_SCHEMA")
    return manifest


def verify_backup(dest: Path) -> list[str]:
    """Problemas del respaldo (vacio = integro): sello del manifiesto y bytes/tamano/sha256 de cada objeto (local y remoto)."""
    dest = Path(dest)
    try:
        m = load_manifest(dest)
    except BackupError as exc:
        return [exc.code]
    out = []
    try:
        seal = (dest / "manifest.sha256").read_text(encoding="ascii").strip()
    except OSError:
        seal = ""
    if m.get("manifest_sha256") != manifest_digest(m) or seal != m.get("manifest_sha256"):
        out.append("MANIFEST_SEAL_MISMATCH")
    for o in m.get("objects") or []:
        f = dest / "objects" / o["key"]
        if not f.is_file() or f.stat().st_size != o["size"] or _sha(f.read_bytes()) != o["sha256"]:
            out.append(f"OBJECT_CORRUPT:{o['key']}")
        if o.get("remote_state") == R_DIFFERS:
            r = dest / "objects_remote" / o["key"]
            if not r.is_file() or _sha(r.read_bytes()) != o.get("remote_sha256"):
                out.append(f"REMOTE_OBJECT_CORRUPT:{o['key']}")
    return out


def committed_sha(dest: Path, key: str) -> str | None:
    """sha256 del ULTIMO sobre que la migracion escribio para ``key`` (evento COMMITTED), o None si nunca se escribio."""
    sha = None
    for ev in read_journal(dest):
        if ev.get("key") == key and ev.get("event") == EV_COMMITTED:
            sha = ev.get("sha256")
    return sha


def restore_object(dest: Path, key: str, *, governed_dir: Path, apply: bool = False, push_remote: Callable[[Path], bool] | None = None,
                   read_remote: Callable[[Path], bytes | None] | None = None, require_remote: bool = False) -> dict:
    """Restaura UN sobre desde el respaldo. Estados: ``WOULD_RESTORE`` (dry-run) | ``RESTORED`` | ``ALREADY_RESTORED`` (idempotente) | ``NOT_COMMITTED`` (la migracion nunca lo escribio: nada que restaurar)
    | ``CONFLICT_CONCURRENT_CHANGE`` (el local, o el remoto, cambiaron despues de la migracion: NO se pisa) | ``BACKUP_OBJECT_CORRUPT`` | ``RESTORE_REMOTE_PENDING`` (local restaurado, remoto sin confirmar)."""
    dest, governed_dir = Path(dest), Path(governed_dir)
    m = load_manifest(dest)
    obj = next((o for o in m["objects"] if o["key"] == key), None)
    if obj is None:
        return {"key": key, "status": "UNKNOWN_OBJECT"}
    if verify_backup(dest):
        return {"key": key, "status": "BACKUP_OBJECT_CORRUPT"}
    original = (dest / "objects" / key).read_bytes()
    path = governed_dir / key
    cur = path.read_bytes() if path.is_file() else None
    cur_sha = _sha(cur) if cur is not None else None
    migrated = committed_sha(dest, key)
    res = {"key": key, "backup_sha256": obj["sha256"], "current_sha256": cur_sha, "migrated_sha256": migrated}
    if cur_sha == obj["sha256"]:                                                     # el local YA es el original: idempotente (si el remoto no lo es, solo se re-publica)
        rb = None
        if read_remote is not None:
            try:
                rb = read_remote(path)
            except Exception:                                                        # noqa: BLE001
                return {**res, "status": "RESTORE_REMOTE_PENDING", "where": "remote_unreadable"}
            if rb is not None and rb == original:
                return {**res, "status": "ALREADY_RESTORED"}
            if rb is not None and _sha(rb) != migrated:                              # el remoto no es lo que escribio la migracion ni el original: alguien lo cambio, NO se pisa
                return {**res, "status": "CONFLICT_CONCURRENT_CHANGE", "where": "remote"}
        else:
            return {**res, "status": "ALREADY_RESTORED"}
        return _republish(res, path, push_remote, read_remote, original, dest) if apply else {**res, "status": "RESTORE_REMOTE_PENDING"}
    if migrated is None:
        return {**res, "status": "NOT_COMMITTED"}
    if cur_sha != migrated:
        return {**res, "status": "CONFLICT_CONCURRENT_CHANGE", "where": "local"}
    if read_remote is not None:                                                      # el remoto tambien debe ser lo que escribio la migracion (o ya el original)
        try:
            rb = read_remote(path)
        except Exception:                                                            # noqa: BLE001
            return {**res, "status": "CONFLICT_CONCURRENT_CHANGE", "where": "remote_unreadable"}
        if rb is not None and _sha(rb) not in (migrated, obj["sha256"]):
            return {**res, "status": "CONFLICT_CONCURRENT_CHANGE", "where": "remote"}
    if not apply:
        return {**res, "status": "WOULD_RESTORE"}
    try:
        _atomic_write(path, original)
    except OSError as exc:
        append_journal(dest, {"event": EV_RESTORE_FAILED, "key": key, "error": type(exc).__name__})
        return {**res, "status": "RESTORE_FAILED"}
    return _republish({**res, "current_sha256": obj["sha256"]}, path, push_remote, read_remote, original, dest)


def _republish(res: dict, path: Path, push_remote, read_remote, original: bytes, dest: Path) -> dict:
    key = res["key"]
    if push_remote is None:
        append_journal(dest, {"event": EV_RESTORED, "key": key, "sha256": res["backup_sha256"], "remote": "DISABLED"})
        return {**res, "status": "RESTORED", "remote": "DISABLED"}
    confirmed = False
    try:
        if push_remote(path) and (read_remote is None or read_remote(path) == original):
            confirmed = True
    except Exception:                                                                # noqa: BLE001
        confirmed = False
    append_journal(dest, {"event": EV_RESTORED if confirmed else EV_RESTORE_FAILED, "key": key, "sha256": res["backup_sha256"], "remote": "CONFIRMED" if confirmed else "PENDING"})
    return {**res, "status": "RESTORED" if confirmed else "RESTORE_REMOTE_PENDING", "remote": "CONFIRMED" if confirmed else "PENDING"}
