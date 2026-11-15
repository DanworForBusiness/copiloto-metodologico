"""Activacion GOBERNADA del ruleset de Juicio (``ruleset-activation/v1``): desacopla el DESPLIEGUE de codigo de la ACTIVACION de un ruleset nuevo.

El codigo puede traer un ruleset nuevo (p. ej. ``judgment-rules/v5``) DISPONIBLE e INACTIVO: mientras no haya marcador, el ruleset vigente es ``CURRENT_RULESET_VERSION`` (contrato del
motor) y la conducta es byte a byte la previa. SOLO la adopcion asegurada (``assessment_backfill --backfill-judgment --apply`` firmada) escribe el marcador, en el MISMO acto en que
recalifica el padron, con esta maquina de estados (un unico archivo, escritura atomica + write-through al almacen durable):

    (sin marcador) ──► ACTIVATING ──► ACTIVE ──► FINALIZED
                           │             │
                           └──► ROLLED_BACK ◄┘

  · ACTIVATING: se ESCRIBEN los Juicios candidatos (clave hermana ``judgment_candidate`` de cada sobre); lo VISIBLE sigue siendo el ruleset previo para TODO (gobernado y ad hoc).
  · ACTIVE: un unico cambio atomico (este archivo) hace visible el ruleset nuevo a la vez para gobernado y ad hoc: la seleccion del Juicio visible lee el marcador. Reversible al instante
    (ROLLED_BACK) mientras los candidatos conviven con el Juicio previo.
  · FINALIZED: los candidatos se consolidaron como el UNICO Juicio vivo de cada sobre (``previous_judgment`` escalar); el rollback pasa a ser restauracion de respaldo.
  · ROLLED_BACK: vuelve a ser visible el ruleset previo.
Marcador ausente, ilegible, de otra version o con un ruleset desconocido => ruleset por defecto (fail-safe). Ningun proceso del web escribe el marcador."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.catalog.methodological_judgment.contract import CURRENT_RULESET_VERSION, RULESETS

ACTIVATION_VERSION = "ruleset-activation/v1"
MARKER_NAME = "ruleset-activation.json"
ACTIVATING, ACTIVE, FINALIZED, ROLLED_BACK = "ACTIVATING", "ACTIVE", "FINALIZED", "ROLLED_BACK"
STATES = (ACTIVATING, ACTIVE, FINALIZED, ROLLED_BACK)
_TARGET_VISIBLE = (ACTIVE, FINALIZED)

_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "marker": None}


def marker_path() -> Path | None:
    """Ruta del marcador dentro del almacen gobernado (``None`` sin almacen durable)."""
    from webapp.services import assessment_service as svc
    d = svc.governed_dir()
    return None if d is None else d / MARKER_NAME


def _valid(m: Any) -> bool:
    return (isinstance(m, dict) and m.get("version") == ACTIVATION_VERSION and m.get("state") in STATES
            and m.get("ruleset_version") in RULESETS and m.get("previous_ruleset_version") in RULESETS)


def read_marker(path: Path | None = None) -> dict | None:
    """Marcador VALIDO vigente o ``None``. Cacheado por (ruta, mtime_ns, tamano): una escritura de otro proceso se ve en la siguiente lectura."""
    p = path if path is not None else marker_path()
    if p is None:
        return None
    try:
        st = p.stat()
    except OSError:
        with _lock:
            _cache.update(key=None, marker=None)
        return None
    key = (str(p), st.st_mtime_ns, st.st_size)
    with _lock:
        if _cache["key"] == key:
            return _cache["marker"]
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        m = None
    m = m if _valid(m) else None
    with _lock:
        _cache.update(key=key, marker=m)
    return m


def token() -> tuple | None:
    """Huella del estado de activacion (para invalidar caches de sobres en memoria cuando cambia)."""
    m = read_marker()
    return None if m is None else (m["state"], m["ruleset_version"], m["previous_ruleset_version"], m.get("updated_at"))


def active_ruleset() -> str:
    """Ruleset VISIBLE: el nuevo solo en ACTIVE/FINALIZED; el previo en ACTIVATING/ROLLED_BACK; el por defecto sin marcador valido."""
    m = read_marker()
    if m is None:
        return CURRENT_RULESET_VERSION
    return m["ruleset_version"] if m["state"] in _TARGET_VISIBLE else m["previous_ruleset_version"]


def state() -> str | None:
    m = read_marker()
    return None if m is None else m["state"]


def in_transition() -> bool:
    """ACTIVATING o ACTIVE (candidatos sin consolidar): otras operaciones de escritura del Juicio deben abstenerse."""
    return state() in (ACTIVATING, ACTIVE)


def write_marker(state_: str, *, ruleset_version: str, previous_ruleset_version: str, plan_digest: str, operator: str, approval_ref: str | None = None,
                 path: Path | None = None, extra: dict | None = None) -> str:
    """Escribe el marcador (atomico + write-through). SOLO lo invoca la adopcion asegurada. Devuelve la durabilidad lograda."""
    from core import remote_store
    if state_ not in STATES or ruleset_version not in RULESETS or previous_ruleset_version not in RULESETS:
        raise ValueError("marcador de activacion invalido")
    p = path if path is not None else marker_path()
    if p is None:
        raise RuntimeError("NO_GOVERNED_STORE")
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"version": ACTIVATION_VERSION, "state": state_, "ruleset_version": ruleset_version, "previous_ruleset_version": previous_ruleset_version,
           "plan_digest": plan_digest, "operator": operator, "approval_ref": approval_ref, "updated_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
           **(extra or {})}
    durable = remote_store.write_through(p, json.dumps(doc, ensure_ascii=False, sort_keys=True))
    with _lock:
        _cache.update(key=None, marker=None)
    return durable


def remove_marker(path: Path | None = None) -> bool:
    """Elimina el marcador local (vuelve el ruleset por defecto). La copia remota se reemplaza escribiendo ROLLED_BACK antes de borrar; ver la adopcion."""
    p = path if path is not None else marker_path()
    if p is None or not p.exists():
        return False
    os.remove(p)
    with _lock:
        _cache.update(key=None, marker=None)
    return True
