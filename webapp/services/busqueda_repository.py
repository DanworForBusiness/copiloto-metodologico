"""busqueda_repository.py — persistencia de BÚSQUEDAS (procesos de selección).

Una "búsqueda" es lo que la app de referencia (app-seleccion) modela como
`proceso`: un puesto a cubrir con su pipeline de candidatos. Este repositorio
sigue EXACTAMENTE el mismo patrón de almacenamiento que `profile_repository`
(un JSON por entidad bajo `human-capital/`, write atómico crash-safe,
durabilidad write-through vía `core.remote_store`, lock global para el
read-modify-write) — NO un mecanismo de storage nuevo/paralelo.

MODELO (aggregate): cada búsqueda EMBEBE su lista de candidatos
(postulaciones). Un candidato de la búsqueda es la relación explícita
persona↔búsqueda pedida por el owner (CandidateApplication): referencia al
Banco de CVs por `profile_id` (el Banco sigue siendo el maestro de la persona;
acá NUNCA se copian datos personales), + `etapa` del pipeline + `resultado_final`
+ `historial` de transiciones. Una persona puede estar en 0..N búsquedas.

SEMÁNTICA DE DOMINIO (verbatim de la referencia, sin inventar):
  - estado de la búsqueda: 'activo' | 'cerrado'
  - etapas del pipeline (orden): Preselección → Entrevista CCHH → Entrevista
    Líder → Decisión Final → Finalizado
  - resultado_final (SÓLO válido en 'Finalizado'): Ingreso | Rechazado | Desistió
  - modalidad: Híbrido | Remoto (o vacío)
  - área de la consultora: OPERACIONES | ADMINISTRACION/CCHH | COMERCIAL (o vacío)
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import config
from core import remote_store

_logger = logging.getLogger("danwor.human_capital")

_SCHEMA_VERSION = 1
_PERSIST_DIR_NAME = "human-capital"
_BUSQUEDAS_SUBDIR = "busquedas"
_RESTORE_WAIT_TIMEOUT_S = 15.0

# Lock global de módulo (MISMO criterio que profile_repository): cubre la
# sección crítica completa de cualquier read-modify-write sobre una búsqueda
# ya conocida (update + mutaciones de pipeline).
_write_lock = threading.Lock()

# ── Constantes de dominio (verbatim de la referencia) ────────────────────────
ESTADOS = ("activo", "cerrado")
ETAPAS = ("Preselección", "Entrevista CCHH", "Entrevista Líder", "Decisión Final", "Finalizado")
ETAPA_INICIAL = "Preselección"
ETAPA_FINAL = "Finalizado"
RESULTADOS_FINALES = ("Ingreso", "Rechazado", "Desistió")
MODALIDADES = ("Híbrido", "Remoto")
# `area_departamento` y `area_consultora` NO son enums de este repositorio: son
# catálogos GOBERNADOS (`area` y `area_consultora`, human_capital_catalog_
# repository) — la validación migration-safe vive en el service, MISMO criterio
# que Seniority/Fuente. Por eso acá no hay tuplas de áreas.

# Campos EDITABLES de una búsqueda (los que el form de alta/edición maneja).
# `estado`/`fecha_cierre`/`candidatos` NUNCA se tocan por esta vía — tienen sus
# operaciones propias (close / mutaciones de pipeline).
EDITABLE_FIELDS = (
    "nombre_puesto", "fecha_apertura", "descripcion", "area_departamento",
    "modalidad", "area_consultora", "pedido_formal_recibido", "jd_texto", "palabras_clave",
)


class BusquedaStorageError(Exception):
    """Fallo de PERSISTENCIA (disco o remote_store), nunca de validación."""


class BusquedaConfigIncompleteError(BusquedaStorageError):
    """`remote_store.enabled() is False` — Spaces sin configurar."""


class BusquedaNotFoundError(Exception):
    """Operación sobre una `busqueda_id` inexistente (→ 404 en el router)."""


class BusquedaConcurrentModificationError(Exception):
    """La búsqueda cambió desde que se leyó la `revision` esperada."""


class BusquedaRemoteOutcomeUnknownError(Exception):
    """El estado remoto final no pudo establecerse tras un fallo de push."""


class ApplicationNotFoundError(Exception):
    """Operación sobre una `application_id` que no existe en esa búsqueda."""


# ── Helpers de storage (MISMO patrón que profile_repository) ─────────────────

def _busquedas_dir() -> Path:
    return config.BASE_DIR / _PERSIST_DIR_NAME / _BUSQUEDAS_SUBDIR


def _busqueda_path(busqueda_id: str) -> Path:
    return _busquedas_dir() / f"{busqueda_id}.json"


def _ensure_restored() -> None:
    if not remote_store.enabled():
        return
    remote_store.await_restore(timeout=_RESTORE_WAIT_TIMEOUT_S)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    except OSError as exc:
        raise BusquedaStorageError(f"No se pudo escribir la búsqueda en disco: {exc}") from exc
    finally:
        if tmp_name is not None:
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def _cleanup_failed_write(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        _logger.warning("human_capital.busqueda_cleanup_failed reason=%s", type(exc).__name__)
    try:
        remote_store.delete_path(path)
    except Exception as exc:  # noqa: BLE001 — best-effort
        _logger.warning("human_capital.busqueda_remote_cleanup_failed reason=%s", type(exc).__name__)


def _with_revision_default(payload: dict) -> dict:
    payload.setdefault("revision", 1)
    payload.setdefault("candidatos", [])
    return payload


# ── Lecturas ─────────────────────────────────────────────────────────────────

def get(busqueda_id: str) -> dict | None:
    _ensure_restored()
    path = _busqueda_path(busqueda_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise BusquedaStorageError(f"No se pudo leer la búsqueda: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return _with_revision_default(payload) if isinstance(payload, dict) else None


def list() -> list[dict]:  # noqa: A001 — nombre del contrato, no el builtin
    _ensure_restored()
    directory = _busquedas_dir()
    if not directory.exists():
        return []
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        raise BusquedaStorageError(f"No se pudo listar las búsquedas: {exc}") from exc
    out: list[dict] = []
    for entry in entries:
        if not entry.is_file() or entry.suffix != ".json":
            continue
        try:
            payload = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # corrupto/ilegible → se saltea, no rompe el resto
        if isinstance(payload, dict) and payload.get("busqueda_id"):
            out.append(_with_revision_default(payload))
    return out


# ── Escritura durable (write-through, MISMO contrato que profile_repository) ──

def _persist(path: Path, payload: dict, *, previous: dict | None) -> dict:
    """Escribe `payload` local atómico y confirma durabilidad remota.
    `previous` = el estado anterior a restaurar si la compensación aplica
    (None en un create). Devuelve `payload` en éxito; levanta la excepción de
    clasificación que corresponda si la durabilidad no se confirma."""
    _write_atomic(path, payload)
    if remote_store.push_path(path):
        return payload
    if not remote_store.enabled():
        # Determinístico: nunca "unknown" en este entorno.
        if previous is None:
            _cleanup_failed_write(path)
        else:
            _write_atomic(path, previous)
        raise BusquedaConfigIncompleteError(
            "La sección de Búsquedas todavía no está configurada para este entorno."
        )
    # Spaces habilitado: intento de compensación / limpieza.
    if previous is None:
        if remote_store.push_path(path):  # reintento del create
            return payload
        _cleanup_failed_write(path)
        raise BusquedaRemoteOutcomeUnknownError(
            "No pudimos confirmar si esta búsqueda se guardó. Revisá el listado antes de reintentar."
        )
    _write_atomic(path, previous)  # el local vuelve a A de inmediato
    if remote_store.push_path(path):
        raise BusquedaStorageError(
            "No pudimos confirmar el respaldo del cambio; se mantuvo la versión anterior de la búsqueda."
        )
    raise BusquedaRemoteOutcomeUnknownError(
        "No pudimos confirmar el estado remoto de esta búsqueda tras un fallo de guardado."
    )


def create(fields: dict) -> dict:
    """Crea una búsqueda nueva (uuid propio, `estado='activo'`, sin candidatos).
    `fields` ya viene validado/shapeado por el service."""
    _ensure_restored()
    busqueda_id = str(uuid4())
    now = _now_iso()
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "busqueda_id": busqueda_id,
        "revision": 1,
        "estado": "activo",
        "fecha_cierre": None,
        **{k: fields.get(k) for k in EDITABLE_FIELDS},
        "candidatos": [],
        "created_at": now,
        "updated_at": now,
    }
    return _persist(_busqueda_path(busqueda_id), payload, previous=None)


def _mutate(busqueda_id: str, mutator, *, expected_revision: int | None = None) -> dict:
    """Read-modify-write durable bajo el lock global. `mutator(doc)` recibe una
    COPIA mutable del documento actual y la modifica in-place (o levanta). Se le
    bumpea `revision`+`updated_at` y se persiste con compensación. Si
    `expected_revision` se pasa, se valida concurrencia optimista ANTES de mutar."""
    with _write_lock:
        current = get(busqueda_id)
        if current is None:
            raise BusquedaNotFoundError(f"La búsqueda {busqueda_id} no existe.")
        if expected_revision is not None and current.get("revision", 1) != expected_revision:
            raise BusquedaConcurrentModificationError(
                "Esta búsqueda cambió desde que abriste el formulario."
            )
        candidate = json.loads(json.dumps(current))  # copia profunda
        mutator(candidate)
        candidate["busqueda_id"] = busqueda_id
        candidate["schema_version"] = _SCHEMA_VERSION
        candidate["created_at"] = current.get("created_at")
        candidate["revision"] = current.get("revision", 1) + 1
        candidate["updated_at"] = _now_iso()
        return _persist(_busqueda_path(busqueda_id), candidate, previous=current)


def update(busqueda_id: str, expected_revision: int, fields: dict) -> dict:
    """Edita los campos de una búsqueda (form de edición). Concurrencia
    optimista por `revision`. NO toca estado/fecha_cierre/candidatos."""
    def _apply(doc: dict) -> None:
        for k in EDITABLE_FIELDS:
            if k in fields:
                doc[k] = fields[k]
    return _mutate(busqueda_id, _apply, expected_revision=expected_revision)


# ── Mutaciones del pipeline (candidatos embebidos) ──────────────────────────

def _find_app(doc: dict, application_id: str) -> dict:
    for app in doc.get("candidatos", []):
        if app.get("application_id") == application_id:
            return app
    raise ApplicationNotFoundError(f"La postulación {application_id} no existe en esta búsqueda.")


def add_candidate(busqueda_id: str, profile_id: str) -> dict:
    """Asocia un Profile del Banco a la búsqueda (nueva postulación en
    Preselección, con su primera fila de historial). Idempotente-ish: si el
    profile ya tiene una postulación ACTIVA (no finalizada) en ESTA búsqueda,
    no la duplica."""
    def _apply(doc: dict) -> None:
        for app in doc.get("candidatos", []):
            if app.get("profile_id") == profile_id and app.get("etapa") != ETAPA_FINAL:
                return  # ya está en la búsqueda, no duplicar
        now = _now_iso()
        doc.setdefault("candidatos", []).append({
            "application_id": str(uuid4()),
            "profile_id": profile_id,
            "etapa": ETAPA_INICIAL,
            "resultado_final": None,
            "fecha_cambio_etapa": now,
            "historial": [{"etapa": ETAPA_INICIAL, "fecha": now}],
            "created_at": now,
        })
    return _mutate(busqueda_id, _apply)


def move_stage(busqueda_id: str, application_id: str, etapa: str, resultado_final: str | None) -> dict:
    """Mueve una postulación a `etapa` (registrando la transición en historial).
    `resultado_final` se fuerza a None salvo que etapa == Finalizado."""
    def _apply(doc: dict) -> None:
        app = _find_app(doc, application_id)
        now = _now_iso()
        app["etapa"] = etapa
        app["resultado_final"] = resultado_final if etapa == ETAPA_FINAL else None
        app["fecha_cambio_etapa"] = now
        app.setdefault("historial", []).append({"etapa": etapa, "fecha": now})
    return _mutate(busqueda_id, _apply)


def remove_candidate(busqueda_id: str, application_id: str) -> dict:
    """Quita una postulación de la búsqueda (el Profile del Banco no se toca)."""
    def _apply(doc: dict) -> None:
        before = doc.get("candidatos", [])
        after = [a for a in before if a.get("application_id") != application_id]
        if len(after) == len(before):
            raise ApplicationNotFoundError(f"La postulación {application_id} no existe en esta búsqueda.")
        doc["candidatos"] = after
    return _mutate(busqueda_id, _apply)


def delete(busqueda_id: str) -> None:
    """Hard delete -- MISMO patrón/contrato de durabilidad exacto que
    `entrevista_repository.delete` (sin precedente en la referencia: `procesos`
    no tiene una acción de borrado ahí, esto es una capacidad propia del Hub,
    owner request explícito). El service (`human_capital_service.delete_busqueda`)
    es quien decide SI se puede borrar (sólo "placeholder": sin postulaciones y
    sin Entrevistas vinculadas) -- este repositorio, como el resto, NUNCA valida
    negocio, sólo persiste. Si el borrado remoto no se confirma, ROLLBACK del
    borrado local (se reescribe el contenido previo) para que local/remoto
    nunca diverjan."""
    path = _busqueda_path(busqueda_id)
    previous = get(busqueda_id)
    if previous is None:
        raise BusquedaNotFoundError(f"La búsqueda {busqueda_id} no existe.")
    try:
        path.unlink()
    except FileNotFoundError:
        raise BusquedaNotFoundError(f"La búsqueda {busqueda_id} no existe.") from None
    except OSError as exc:
        raise BusquedaStorageError(f"No se pudo eliminar la búsqueda: {exc}") from exc
    if remote_store.delete_path(path):
        return
    if not remote_store.enabled():
        raise BusquedaConfigIncompleteError(
            "La sección de Búsquedas todavía no está configurada para este entorno."
        )
    _write_atomic(path, previous)  # rollback: local vuelve a existir
    raise BusquedaRemoteOutcomeUnknownError(
        "No pudimos confirmar si el borrado se sincronizó. Revisá el listado antes de reintentar."
    )


def close(busqueda_id: str, finalize_application_ids: list[str] | None = None) -> dict:
    """Cierra la búsqueda: las postulaciones pendientes indicadas pasan a
    Finalizado · Rechazado, y la búsqueda queda `estado='cerrado'` con
    `fecha_cierre`=hoy."""
    finalize = set(finalize_application_ids or [])
    def _apply(doc: dict) -> None:
        now = _now_iso()
        for app in doc.get("candidatos", []):
            if app.get("application_id") in finalize and app.get("etapa") != ETAPA_FINAL:
                app["etapa"] = ETAPA_FINAL
                app["resultado_final"] = "Rechazado"
                app["fecha_cambio_etapa"] = now
                app.setdefault("historial", []).append({"etapa": ETAPA_FINAL, "fecha": now})
        doc["estado"] = "cerrado"
        doc["fecha_cierre"] = datetime.now(timezone.utc).date().isoformat()
    return _mutate(busqueda_id, _apply)
