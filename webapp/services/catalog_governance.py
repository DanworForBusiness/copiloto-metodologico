"""
catalog_governance.py — procedencia de publicación y RETIRO lógico de tiles del Catálogo, con la política de permisos del owner.

PROBLEMA (diagnóstico): hoy NADA prueba quién subió o actualizó un catálogo. El flujo ``flujo_*.md`` no lleva autor; el manifiesto de linaje (v2) no registra actor; el assessment gobernado
tampoco; solo existe ``activity_log`` (``datasphere_publication_run``), que es opcional (``LOG_ACTIVITY``), se escribe al terminar (una caída no deja ni el intento) y se enlaza al tile por
(cliente, space, raíz), no por ``entry_id``. Por eso la regla «quien lo subió o actualizó» no se puede aplicar sobre lo que ya está publicado.

AJUSTE MÍNIMO: al publicar con éxito, ``datasphere_publication_service.run`` registra la PROCEDENCIA de cada tile resultante en un sidecar propio
(``<catalog_local>/.publication_actors/<entry_id>.json``: primer publicador, ÚLTIMO publicador, instantes y operación). Es aditivo, está dentro de lo que ya se persiste y no altera el flujo, el pool ni los
manifiestos. Los tiles publicados ANTES de este cambio no tienen procedencia: solo el dueño puede retirarlos (falla cerrado; nunca se infiere un autor).

REGLA (decisión del owner, base = ÚLTIMO PUBLICADOR): APP_OWNER retira cualquier tile; un usuario con perfil completo solo retira el tile cuyo ÚLTIMO publicador es él (el actor más reciente, que es el dato que el
sistema registra de forma fiable); cualquier otro caso: no ve el ícono y el endpoint responde 403. Si el owner prefiere «autor original o cualquier actualizador», el sidecar ya guarda ambos extremos.

RETIRO LÓGICO (no borrado físico): un tombstone ``<catalog_local>/.retired/<entry_id>.json`` hace que ``visible_entries`` (Catálogo y asistente) deje de devolver el tile; el cargador ``catalog_service`` NO se toca.
No se borra el flujo, el pool, los manifiestos, el assessment ni nada en Spaces: hoy el borrado físico NO es seguro (el generador regenera el flujo desde el pool, los objetos del pool pueden ser compartidos entre
raíces, los borrados no se propagan a Spaces —H2— y un restore los resucitaría). Volver a publicar el modelo levanta el retiro (la publicación es la intención contraria). Reversible por diseño.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import config
from core import remote_store

log = logging.getLogger("danwor.catalog_governance")

SCHEMA = 1
PROVENANCE_DIR = ".publication_actors"
RETIRED_DIR = ".retired"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,200}$")


class GovernanceStorageError(Exception):
    """No se pudo escribir (o confirmar) el registro: la operación no se da por hecha."""


def catalog_root() -> Path:
    return Path(os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(config.BASE_DIR / "catalog_local")).resolve()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def valid_entry_id(entry_id: str) -> bool:
    return bool(_ID_RE.match(entry_id or ""))


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(path: Path, payload: dict) -> None:
    """Escritura atómica + persistencia explícita; fail-closed salvo modo local (como la auditoría de CCHH)."""
    try:
        outcome = remote_store.write_through(path, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1))
    except OSError as exc:
        raise GovernanceStorageError("no se pudo escribir el registro") from exc
    if outcome != remote_store.DURABLE_REMOTE and not remote_store.local_only():
        raise GovernanceStorageError("registro no confirmado en Spaces")


# ───────────────────────── procedencia ─────────────────────────
def provenance(entry_id: str) -> dict | None:
    if not valid_entry_id(entry_id):
        return None
    data = _read(catalog_root() / PROVENANCE_DIR / f"{entry_id}.json")
    return data if data and data.get("last_publisher") else None


def record_publication(entries, actor: str, operation_id: str = "") -> list[str]:
    """Registra al ``actor`` como ÚLTIMO publicador (y como primero si el tile no tenía procedencia) de cada tile en ``entries``. Devuelve los ``entry_id`` registrados. Sin actor no se registra nada."""
    actor = (actor or "").strip().lower()
    if not actor:
        return []
    done: list[str] = []
    for entry in entries:
        entry_id = str(getattr(entry, "entry_id", "") or "")
        if not valid_entry_id(entry_id):
            continue
        path = catalog_root() / PROVENANCE_DIR / f"{entry_id}.json"
        previous = _read(path) or {}
        now = _now()
        _write(path, {"schema": SCHEMA, "entry_id": entry_id, "cliente": getattr(entry, "cliente", ""), "dominio": getattr(entry, "dominio", ""),
                      "first_publisher": previous.get("first_publisher") or actor, "first_published_at": previous.get("first_published_at") or now,
                      "last_publisher": actor, "last_published_at": now, "operation_id": operation_id, "publications": int(previous.get("publications") or 0) + 1})
        done.append(entry_id)
    return done


# ───────────────────────── retiro lógico ─────────────────────────
def is_retired(entry_id: str) -> bool:
    return valid_entry_id(entry_id) and (catalog_root() / RETIRED_DIR / f"{entry_id}.json").is_file()


def retire(entry, actor: str) -> dict:
    """Retira (lógicamente) el tile. Idempotente: un tile ya retirado conserva su registro original."""
    entry_id = str(entry.entry_id)
    path = catalog_root() / RETIRED_DIR / f"{entry_id}.json"
    existing = _read(path)
    if existing:
        return existing
    record = {"schema": SCHEMA, "entry_id": entry_id, "cliente": entry.cliente, "dominio": entry.dominio, "retired_by": (actor or "").strip().lower(), "retired_at": _now(),
              "provenance_at_retirement": provenance(entry_id)}
    _write(path, record)
    return record


def retired_ids(root: str | Path | None = None) -> set[str]:
    """``entry_id`` de los tiles retirados (solo lectura; sin directorio ⇒ ninguno)."""
    base = Path(root) if root else catalog_root()
    try:
        return {p.stem for p in (base / RETIRED_DIR).glob("*.json")}
    except OSError:
        return set()


def visible_entries(entries, root: str | Path | None = None) -> list:
    """Los tiles del catálogo SIN los retirados lógicamente. ÚNICO punto de filtrado: lo usan el Catálogo (``catalog_reads``) y el asistente. ``catalog_service`` (el cargador del piloto y del scoring legacy) NO se toca:
    sigue devolviendo todo lo que hay en disco."""
    gone = retired_ids(root)
    return [e for e in entries if e.entry_id not in gone] if gone else list(entries)


def lift_retirement(entry_ids) -> list[str]:
    """Levanta el retiro de los tiles que se acaban de VOLVER A PUBLICAR. Best-effort: nunca rompe la publicación."""
    lifted: list[str] = []
    for entry_id in entry_ids:
        path = catalog_root() / RETIRED_DIR / f"{entry_id}.json"
        if not valid_entry_id(entry_id) or not path.is_file():
            continue
        try:
            path.unlink()
            remote_store.delete_path(path)
            lifted.append(entry_id)
        except OSError:
            log.warning("catalog_governance: no se pudo levantar el retiro de un tile republicado")
    return lifted


# ───────────────────────── permisos ─────────────────────────
def can_retire(user: dict | None, entry_id: str) -> bool:
    """La política exacta del owner. ``user`` = claims de sesión (o None). Falla cerrado ante cualquier duda."""
    from webapp import access
    from webapp.services import auth_service
    if not user or not valid_entry_id(entry_id):
        return False
    if access.is_owner_user(user):
        return True
    sub = str(user.get("sub") or "").strip().lower()
    if not sub or auth_service.user_tier(sub) != "full":
        return False                                                    # solo lectura / fuera de la allowlist: jamás
    record = provenance(entry_id)
    return bool(record and str(record.get("last_publisher") or "").strip().lower() == sub)


def deletable_ids(user: dict | None, flows) -> set[str]:
    """Los ``entry_id`` de ``flows`` que ESTE usuario puede retirar (alimenta el ícono de cada tile: la UI y el endpoint usan el MISMO predicado)."""
    return {f.entry_id for f in flows if can_retire(user, f.entry_id)}
