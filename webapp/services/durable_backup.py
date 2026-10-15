"""durable_backup.py — copia DURABLE (Spaces) del respaldo ``governed_backup`` de una migracion: ``migration-backups/<migracion>/<run_id>/`` en el bucket existente.

Garantias (todas con tests; ver ``tests/webapp/test_durable_backup.py``):
  * namespace EXCLUSIVO y fuera de ``PERSIST_PATHS``: ``restore``/``mirror_up``/``push_path``/``delete_path`` jamas lo tocan; se comprueba que no colisiona antes de escribir;
  * escrituras DIRIGIDAS (``remote_store.put_backup_object``), una vez por clave: NUNCA se sobrescribe (``RemoteBackupExists``); el ``run_id`` debe ser nuevo (prefijo vacio);
  * contenido: bytes originales (``objects/``, ``objects_remote/``), ``manifest.json`` + ``manifest.sha256``, la aprobacion usada (``approval.json``) y, durante la migracion, el diario como
    objetos inmutables ``journal/<seq>.json`` (un objeto por evento: no hay nada que reescribir);
  * CADA objeto se confirma por BYTES (GET posterior == lo subido); recien entonces se escribe ``sealed.json`` (manifiesto + hash de cada archivo). Un run sin sello NO es restaurable;
  * ``fetch`` baja un run a un directorio VACIO verificando el sello, el manifiesto y cada hash, y reconstruye ``journal.jsonl`` para usar la restauracion por objeto de ``governed_backup``;
  * NO existe ninguna operacion de borrado: la limpieza es un acto manual y explicito del dueño (ver el diseño), nunca automatico.
Orden obligatorio en ``secured_migrate``: respaldo local verificado -> ``publish`` (sellado y confirmado) -> RECIEN ENTONCES se modifica el primer Assessment."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import remote_store
from webapp.services import governed_backup as gb

SEAL = "sealed.json"


class DurableBackupError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def new_run_id(plan_digest: str, now: datetime | None = None) -> str:
    return f"{(now or datetime.now(timezone.utc)).strftime('%Y%m%dT%H%M%SZ')}-{plan_digest[:12]}"


def prefix(migration: str, run_id: str) -> str:
    if not migration.replace("_", "").isalnum() or not run_id.replace("-", "").isalnum():
        raise DurableBackupError("BAD_RUN_ID", "migracion/run_id solo alfanumericos, '-' y '_'")
    return f"{remote_store.BACKUP_NAMESPACE}/{migration}/{run_id}"


def _put_verified(rel: str, data: bytes) -> None:
    """PUT sin sobrescribir + confirmacion por bytes. Cualquier falla => ``DurableBackupError`` (respaldo no garantizado)."""
    try:
        remote_store.put_backup_object(rel, data)
        if remote_store.get_backup_object(rel) != data:
            raise DurableBackupError("REMOTE_BYTES_MISMATCH", rel)
    except remote_store.RemoteBackupExists:
        raise DurableBackupError("REMOTE_OBJECT_EXISTS", rel) from None
    except DurableBackupError:
        raise
    except Exception as exc:                                                       # noqa: BLE001
        raise DurableBackupError("REMOTE_WRITE_FAILED", f"{rel}: {type(exc).__name__}") from None


def publish(backup_dir: Path, migration: str, run_id: str, approval: Any = None) -> dict:
    """Publica el respaldo LOCAL ya verificado (``gb.create_backup``) y lo SELLA. Falla de forma segura (``DurableBackupError``): el llamador NO debe modificar ningun Assessment."""
    backup_dir = Path(backup_dir)
    collision = remote_store.backup_namespace_collision()
    if collision:
        raise DurableBackupError("NAMESPACE_COLLISION", collision)
    if gb.verify_backup(backup_dir):
        raise DurableBackupError("LOCAL_BACKUP_NOT_VERIFIED")
    base = prefix(migration, run_id)
    try:
        if remote_store.list_backup_keys(base):
            raise DurableBackupError("RUN_ID_ALREADY_USED", run_id)                # jamas se mezcla ni se sobrescribe un run existente
    except remote_store.RemoteReadError as exc:
        raise DurableBackupError("REMOTE_LIST_FAILED", str(exc)) from None
    files = sorted(p for p in (backup_dir / "objects").rglob("*") if p.is_file()) + sorted(p for p in (backup_dir / "objects_remote").rglob("*") if p.is_file())
    sent: dict[str, str] = {}
    for p in files:
        data = p.read_bytes()
        rel = p.relative_to(backup_dir).as_posix()
        _put_verified(f"{base}/{rel}", data)
        sent[rel] = _sha(data)
    for name in ("manifest.json", "manifest.sha256"):
        data = (backup_dir / name).read_bytes()
        _put_verified(f"{base}/{name}", data)
        sent[name] = _sha(data)
    if approval is not None:
        data = json.dumps(approval, indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8")
        _put_verified(f"{base}/approval.json", data)
        sent["approval.json"] = _sha(data)
    manifest = gb.load_manifest(backup_dir)
    seal = {"schema": "durable-backup-seal/v1", "migration": migration, "run_id": run_id, "manifest_sha256": manifest["manifest_sha256"], "plan_digest": manifest["plan_digest"],
            "objects": len(manifest["objects"]), "files": sent, "sealed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    _put_verified(f"{base}/{SEAL}", json.dumps(seal, indent=1, sort_keys=True).encode("utf-8"))             # el sello va ULTIMO: sin el, el run no es restaurable
    return {"run_id": run_id, "prefix": base, "files": len(sent), "objects": len(manifest["objects"]), "manifest_sha256": manifest["manifest_sha256"], "sealed": True}


def publish_event(migration: str, run_id: str, seq: int, event: dict) -> None:
    """Un evento del diario como objeto INMUTABLE (``journal/<seq>.json``)."""
    data = json.dumps({"seq": seq, **event}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    _put_verified(f"{prefix(migration, run_id)}/journal/{seq:06d}.json", data)


def list_runs(migration: str) -> list[dict]:
    """Runs del namespace (``sealed`` indica si son restaurables). Solo lectura."""
    root = f"{remote_store.BACKUP_NAMESPACE}/{migration}"
    runs: dict[str, bool] = {}
    for k in remote_store.list_backup_keys(root):
        parts = k.split("/")
        if len(parts) >= 4:
            runs[parts[2]] = runs.get(parts[2], False) or parts[3] == SEAL
    return [{"run_id": r, "sealed": s} for r, s in sorted(runs.items())]


def fetch(migration: str, run_id: str, dest: Path) -> dict:
    """Baja un run SELLADO a ``dest`` (debe estar vacio), verificando sello, manifiesto y cada hash; reconstruye ``journal.jsonl``. Devuelve el manifiesto."""
    dest = Path(dest)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise DurableBackupError("FETCH_DIR_NOT_EMPTY")
    base = prefix(migration, run_id)
    try:
        raw = remote_store.get_backup_object(f"{base}/{SEAL}")
    except remote_store.RemoteReadError as exc:
        raise DurableBackupError("REMOTE_READ_FAILED", str(exc)) from None
    if raw is None:
        raise DurableBackupError("RUN_NOT_SEALED", run_id)
    seal = json.loads(raw.decode("utf-8"))
    dest.mkdir(parents=True, exist_ok=True)
    for rel, sha in sorted(seal["files"].items()):
        try:
            data = remote_store.get_backup_object(f"{base}/{rel}")
        except remote_store.RemoteReadError as exc:
            raise DurableBackupError("REMOTE_READ_FAILED", str(exc)) from None
        if data is None or _sha(data) != sha:
            raise DurableBackupError("REMOTE_OBJECT_CORRUPT", rel)
        gb._atomic_write(dest / rel, data)
    problems = gb.verify_backup(dest)
    manifest = gb.load_manifest(dest)
    if problems or manifest["manifest_sha256"] != seal["manifest_sha256"]:
        raise DurableBackupError("BACKUP_NOT_VERIFIED", "; ".join(problems[:3]) or "sello != manifiesto")
    events = []
    for k in remote_store.list_backup_keys(f"{base}/journal"):
        ev = json.loads((remote_store.get_backup_object(k) or b"{}").decode("utf-8"))
        events.append(ev)
    for ev in sorted(events, key=lambda e: e.get("seq", 0)):
        ev.pop("seq", None)
        with open(dest / "journal.jsonl", "ab") as f:
            f.write((json.dumps(ev, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
    return manifest
