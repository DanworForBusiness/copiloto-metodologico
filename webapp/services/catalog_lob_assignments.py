"""catalog_lob_assignments.py — asignación MANUAL de línea de negocio desde el tile del Catálogo («Reclasificar»).

Persistencia PROPIA, independiente del flujo curado: ``<catalog_local>/.lob_assignments/<entry_id>.json`` (mismo patrón que ``.retired`` de
``catalog_governance``: escritura atómica, espejo a Spaces y falla cerrado si no se confirma). Nunca escribe el ``flujo_*.md`` ni toca CSN, pool,
assessment, Juicio ni la clasificación automática persistida.

Precedencia (decisión del owner): (1) asignación desde el tile → (2) asignación histórica del flujo (``manual.linea_de_negocio``, solo lectura) →
(3) automática de confianza alta → (4) sin clasificar. «Restaurar» retira SOLO la asignación del tile: el registro queda en ``CLEARED`` con su
historial (auditoría) y deja de aplicar; la histórica del flujo nunca se oculta ni se modifica.

Compatibilidad con identificadores HISTÓRICOS: el ``entry_id`` sale de la ruta del flujo y cambia si el dominio se renombra (p. ej. la corrección de
«General»). Si no hay registro con el id actual, se acepta UN registro huérfano (el ``flow_path`` que guardó ya no existe en el Catálogo) del MISMO
cliente cuyo ``terminal_am`` figura entre los artefactos del tile, y solo si ESE tile es el ÚNICO tile visible del cliente que contiene ese modelo
(tiles duplicados u homónimos ⇒ ambiguo ⇒ no se aplica: falla segura). Más de un huérfano candidato también es ambiguo.

Concurrencia: toda escritura (leer → agregar al historial → escribir → retirar el huérfano) corre bajo el candado de ESCRITOR ÚNICO del Catálogo
(``publication_txn.writer_lock``, consumido sin modificarlo; misma precondición de réplica única). Ocupado ⇒ ``AssignmentBusyError`` sin escribir nada. La lectura nunca escribe: la próxima
asignación o restauración lo migra al id actual (con su historial) y retira el huérfano.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import remote_store
from tools.catalog import publication_txn as txn
from webapp.services import catalog_governance as gov

log = logging.getLogger("danwor.catalog_lob_assignments")

SCHEMA = 1
ASSIGN_DIR = ".lob_assignments"
ASSIGNED, CLEARED = "ASSIGNED", "CLEARED"
MAX_HISTORY = 50


class AssignmentStorageError(Exception):
    """No se pudo escribir (o confirmar en Spaces) la asignación: la operación no se da por hecha."""


class AssignmentBusyError(AssignmentStorageError):
    """Otra escritura del Catálogo está en curso (candado de escritor único): no se escribió nada; reintentar."""


LOCK_TIMEOUT_S = 5.0


def _dir() -> Path:
    return gov.catalog_root() / ASSIGN_DIR


def _path(entry_id: str) -> Path:
    return _dir() / f"{entry_id}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == SCHEMA else None


def _write(path: Path, payload: dict) -> None:
    try:
        gov._write(path, payload)
    except gov.GovernanceStorageError as exc:
        raise AssignmentStorageError(str(exc)) from exc


def _flow_path(entry: Any) -> str | None:
    """Ruta del flujo relativa a la raíz del Catálogo (o None si cae fuera)."""
    try:
        return Path(str(getattr(entry, "path", "") or "")).resolve().relative_to(gov.catalog_root()).as_posix()
    except (OSError, ValueError):
        return None


def _is_orphan(rec: dict) -> bool:
    fp = rec.get("flow_path")
    return bool(fp) and not (gov.catalog_root() / fp).is_file()


def _flow_artefacts(path: Path) -> set[str]:
    try:
        from webapp.services.content_service import split_front_matter
        meta, _body = split_front_matter(path.read_text(encoding="utf-8"))
        return {str(a) for a in (((meta or {}).get("auto") or {}).get("artefactos") or [])}
    except Exception:                                                  # noqa: BLE001 — ilegible ⇒ cuenta como posible reclamante (falla segura)
        return {"*"}


def _entry_id_of(rel: str) -> str:
    """Mismo ``entry_id`` que el cargador del Catálogo (slug de la ruta relativa sin ``.md``)."""
    import re
    from tools.catalog.catalog_service import _slug
    return _slug(re.sub(r"\.md$", "", rel, flags=re.IGNORECASE))


def _claimants(entry: Any, am: str) -> int:
    """Cuántos tiles VISIBLES del mismo cliente (misma carpeta de cliente del Catálogo) contienen ``am``. Un tile ilegible cuenta (nunca se subestima)."""
    own = _flow_path(entry)
    if not own:
        return 0
    root = gov.catalog_root()
    client_dir = root / Path(own).parts[0]
    retired = gov.retired_ids()
    n = 0
    for flow in sorted(client_dir.glob("*/flujo_*.md")):
        rel = flow.relative_to(root).as_posix()
        if _entry_id_of(rel) in retired:
            continue                                                   # retirado: no es un tile visible
        arts = _flow_artefacts(flow)
        if am in arts or "*" in arts:
            n += 1
    return n


def _orphans_for(entry: Any) -> list[dict]:
    """Registros huérfanos (su flujo ya no existe) del mismo cliente y con su modelo entre los artefactos del tile."""
    cliente = str(getattr(entry, "cliente", "") or "").strip().lower()
    artefactos = {str(a) for a in (getattr(entry, "artefactos", None) or [])}
    out = []
    try:
        files = sorted(_dir().glob("*.json"))
    except OSError:
        return []
    for p in files:
        if p.stem == str(getattr(entry, "entry_id", "")):
            continue
        rec = _read(p)
        if rec and _is_orphan(rec) and str(rec.get("cliente") or "").strip().lower() == cliente and rec.get("terminal_am") and rec["terminal_am"] in artefactos:
            out.append({**rec, "_source_id": p.stem})
    return out


def record_for(entry: Any) -> dict | None:
    """Registro del tile (con el id actual o, por compatibilidad, un huérfano inequívoco). Solo lectura."""
    entry_id = str(getattr(entry, "entry_id", "") or "")
    if not gov.valid_entry_id(entry_id):
        return None
    rec = _read(_path(entry_id))
    if rec is not None:
        return {**rec, "_source_id": entry_id}
    orphans = _orphans_for(entry)
    if len(orphans) != 1:
        return None                                                    # ninguno, o varios candidatos: ambiguo
    if _claimants(entry, orphans[0]["terminal_am"]) != 1:
        return None                                                    # otro tile visible del cliente también contiene ese modelo: ambiguo
    return orphans[0]


def tile_lob(entry: Any) -> dict | None:
    """Asignación ACTIVA del tile ({line_of_business, actor, at, relinked_from}) o ``None`` (sin registro o restaurada)."""
    rec = record_for(entry)
    if not rec or rec.get("mode") != ASSIGNED or not rec.get("line_of_business"):
        return None
    relinked = rec["_source_id"] if rec["_source_id"] != entry.entry_id else None
    return {"line_of_business": rec["line_of_business"], "actor": rec.get("actor"), "at": rec.get("at"), "relinked_from": relinked}


def _save(entry: Any, mode: str, lob: str | None, actor: str, terminal_am: str | None, previous: dict | None) -> dict:
    try:
        with txn.writer_lock(timeout=LOCK_TIMEOUT_S):
            return _save_locked(entry, mode, lob, actor, terminal_am, previous)
    except txn.CatalogBusyError as exc:
        raise AssignmentBusyError("otra operación sobre el Catálogo está en curso") from exc


def _save_locked(entry: Any, mode: str, lob: str | None, actor: str, terminal_am: str | None, previous: dict | None) -> dict:
    current = record_for(entry) or {}
    history = list(current.get("history") or [])
    history.append({"at": _now(), "actor": actor, "mode": mode, "to": lob, "from": previous})
    record = {"schema": SCHEMA, "entry_id": entry.entry_id, "cliente": entry.cliente, "dominio": entry.dominio,
              "terminal_am": terminal_am or current.get("terminal_am"), "flow_path": _flow_path(entry), "mode": mode, "line_of_business": lob if mode == ASSIGNED else None,
              "actor": actor, "at": history[-1]["at"], "history": history[-MAX_HISTORY:]}
    _write(_path(entry.entry_id), record)
    old = current.get("_source_id")
    if old and old != entry.entry_id:                                   # migración del id histórico: el huérfano se retira recién con el registro nuevo confirmado
        try:
            p = _path(old)
            p.unlink(missing_ok=True)
            remote_store.delete_path(p)
        except OSError:
            log.warning("catalog_lob_assignments: no se pudo retirar el registro histórico %s", old)
    return record


def assign(entry: Any, lob: str, actor: str, *, terminal_am: str | None, previous: dict | None) -> dict:
    return _save(entry, ASSIGNED, lob, (actor or "").strip().lower(), terminal_am, previous)


def restore(entry: Any, actor: str, *, terminal_am: str | None, previous: dict | None) -> dict:
    """Retira SOLO la asignación del tile (el registro queda ``CLEARED`` con su historial)."""
    return _save(entry, CLEARED, None, (actor or "").strip().lower(), terminal_am, previous)
