"""
profile_repository.py — persistencia de Profile (Capital Humano Phase 3 —
First Profile Slice, metadata únicamente, SIN CV/SharePoint).

Contrato de disco (`human-capital/profiles/<profile_id>.json`), UN archivo
por Profile, escritura CRASH-SAFE (tempfile en el mismo directorio → flush
+ fsync → `os.replace` atómico) — MISMO patrón exacto que
`webapp/services/reporting_time_logs_durable.py::save_closed_year_entry`
(ver Phase 2 forensic, §1: es el ÚNICO precedente real de "un JSON por
entidad" en este repo, reusado acá tal cual, no reinventado).

DURABILITY CONTRACT (Phase 2 §10, no negociable): Profile es dato de
negocio, NO una cache re-descargable — a diferencia de
`reporting_time_logs_durable.py` (que tolera el ciclo periódico de
`remote_store.mirror_up()`, aceptable porque perder una escritura ahí solo
implica re-descargar de SharePoint), acá `create()` llama
`remote_store.push_path()` de forma SÍNCRONA e inmediata tras el write
local. Si `push_path()` devuelve `False` (Spaces deshabilitado O la subida
falló), `create()` levanta `ProfileStorageError` — el archivo local YA
quedó escrito (crash-safe, nunca se borra: es la única copia del dato que
el usuario acaba de cargar) pero el caller NUNCA debe reportar éxito sobre
un Profile que no confirmó estar respaldado en Spaces. Ver Phase 2 §10 y
Phase 3 §10 para el razonamiento completo — esto es una consecuencia
DELIBERADA del contrato pedido, no un bug: en un entorno sin credenciales
de Spaces configuradas (dev local, CI), `remote_store.enabled()` es
siempre `False`, así que TODO `create()` reporta `ProfileStorageError` —
el archivo local existe igual (y `list()`/`get()` lo siguen mostrando,
porque ambos leen SOLO del filesystem local, nunca de Spaces
directamente).

RESTORE COORDINATION (Phase 2 §11 / Phase 3 §11): antes de `list`/`get`/
`create`, se espera (acotado, NUNCA polling) a que el restore de arranque
contra Spaces termine si genuinamente sigue en curso — MISMO mecanismo
exacto que `reporting_time_logs_durable.ensure_restored()`
(`remote_store.await_restore`), reusado tal cual, nunca un sleep/polling
propio.

SIN cache/índice (Phase 3 §12, explícito): `list()` lee y parsea cada
archivo del directorio en cada llamada — no hay `ProfileIndex`,
`lru_cache`, ni estructura de búsqueda precomputada. Sin evidencia
todavía de que el volumen lo justifique (ver Phase 2 §9/§14).

SIN concurrencia resuelta más allá de integridad de archivo (Phase 3 §13,
explícito): esta fase solo crea `uuid4()` nuevos — nunca hay un
read-modify-write concurrente sobre el MISMO Profile todavía (eso
aparece recién con `update()`, fuera de esta fase). El `os.replace`
atómico protege la INTEGRIDAD del archivo (un lector nunca ve un JSON a
medio escribir) — NO es control de concurrencia (dos writers concurrentes
sobre el mismo `profile_id` sencillamente el último gana, sin detección
de conflicto). No aplica todavía porque `create()` siempre genera un
`profile_id` nuevo.

Repository puro: NO conoce `Request`, NO renderiza HTML, NO importa
FastAPI — solo filesystem + `core.remote_store` (NUNCA boto3/S3
directamente, ver Phase 2 §1/§7)."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import config
from core import remote_store

_SCHEMA_VERSION = 1
_PERSIST_DIR_NAME = "human-capital"
_PROFILES_SUBDIR = "profiles"
# Acotado, MISMO valor/razonamiento que
# reporting_time_logs_durable._RESTORE_WAIT_TIMEOUT_S: generoso frente al
# costo real que evita, pero nunca indefinido.
_RESTORE_WAIT_TIMEOUT_S = 15.0

# Únicos valores válidos de `source` (Legacy Profile Contract, Phase 2 §5 —
# "Flyer" SÍ es válido acá: el bug legacy fue que el CHECK de la base lo
# rechazaba pese a que el tipo/UI lo permitían — nunca reproducir ese
# mismatch, así que el mismo conjunto gobierna UI + validación + tests).
VALID_SOURCES = ("LinkedIn", "Referido", "Hunting", "Flyer", "Otro")


class ProfileStorageError(Exception):
    """Fallo de PERSISTENCIA (filesystem local o remote_store/Spaces) —
    NUNCA de validación de negocio (eso es `ValueError`, ver
    `human_capital_service.create_profile`). El router lo traduce al
    contrato de error `No disponible` del Hub, nunca a `Sin resultados`."""


def _profiles_dir() -> Path:
    return config.BASE_DIR / _PERSIST_DIR_NAME / _PROFILES_SUBDIR


def _profile_path(profile_id: str) -> Path:
    return _profiles_dir() / f"{profile_id}.json"


def _ensure_restored() -> None:
    """Espera (acotado, nunca indefinido) a que el restore de arranque
    contra Spaces termine, SOLO si genuinamente sigue en curso — MISMO
    criterio que `reporting_time_logs_durable.ensure_restored()`. Sin
    Spaces configurado (`enabled()` False — dev local/CI), es un no-op
    inmediato: no hay ningún restore que esperar."""
    if not remote_store.enabled():
        return
    remote_store.await_restore(timeout=_RESTORE_WAIT_TIMEOUT_S)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_atomic(path: Path, payload: dict) -> None:
    """Escritura CRASH-SAFE: tempfile en el MISMO directorio → flush +
    fsync → `os.replace` atómico — MISMO patrón exacto que
    `reporting_time_logs_durable.save_closed_year_entry`. Nunca deja un
    archivo a medio escribir visible para un lector concurrente."""
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
        raise ProfileStorageError(f"No se pudo escribir el Profile en disco: {exc}") from exc
    finally:
        if tmp_name is not None:
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def create(profile: dict) -> dict:
    """Crea un Profile nuevo: genera `profile_id` (uuid4) + `schema_version`
    + `created_at`/`updated_at`, escribe el JSON localmente (crash-safe) y
    confirma el write-through a Spaces vía `remote_store.push_path()` ANTES
    de devolver — ver DURABILITY CONTRACT arriba. `profile` ya debe venir
    validado/shapeado por el caller (`human_capital_service.create_profile`)
    — este repository no conoce reglas de negocio, solo persiste lo que se
    le pasa.

    Levanta `ProfileStorageError` si el write local falla O si
    `push_path()` no confirma el respaldo remoto — en NINGÚN caso se
    reporta éxito sin esa confirmación (Phase 3 §10, no negociable)."""
    _ensure_restored()
    now = _now_iso()
    profile_id = str(uuid4())
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "profile_id": profile_id,
        **profile,
        "created_at": now,
        "updated_at": now,
    }
    path = _profile_path(profile_id)
    _write_atomic(path, payload)
    if not remote_store.push_path(path):
        # El archivo local YA existe (crash-safe) — nunca se borra: es la
        # única copia del dato recién cargado. `list()`/`get()` lo seguirán
        # viendo. Lo único que NO podemos hacer es reportarle éxito al
        # caller sobre un Profile que no confirmó estar en Spaces.
        raise ProfileStorageError(
            "El Profile se guardó localmente pero no se pudo confirmar su respaldo en Spaces."
        )
    return payload


def get(profile_id: str) -> dict | None:
    """`None` si el archivo no existe o está corrupto — MISMO criterio
    fail-soft que `reporting_time_logs_durable.load_closed_year_entry`
    (un miss limpio, nunca una excepción hacia el caller por un dato
    ilegible)."""
    _ensure_restored()
    path = _profile_path(profile_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ProfileStorageError(f"No se pudo leer el Profile: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def list() -> list[dict]:  # noqa: A001 — nombre del contrato pedido (§8), no el builtin
    """Lee y parsea CADA archivo del directorio en esta misma llamada — sin
    cache/índice (Phase 3 §12, explícito). Un archivo individual corrupto
    se SALTEA (nunca rompe el listado completo, Phase 3 §19) — se trata
    igual que un miss de `get()`. Si el directorio en sí es inaccesible
    (permisos/disco, no "está vacío" — un directorio ausente/vacío es un
    resultado legítimo, no un fallo), levanta `ProfileStorageError`, MISMA
    distinción que el resto de este módulo entre "fuente vacía" (`[]`
    válido) y "fuente inaccesible" (excepción)."""
    _ensure_restored()
    directory = _profiles_dir()
    if not directory.exists():
        return []
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        raise ProfileStorageError(f"No se pudo listar los Profiles: {exc}") from exc
    profiles: list[dict] = []
    for entry in entries:
        if not entry.is_file() or entry.suffix != ".json":
            continue
        try:
            raw = entry.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, ValueError):
            continue  # archivo corrupto/ilegible -- se saltea, no rompe el resto
        if isinstance(payload, dict) and payload.get("profile_id"):
            profiles.append(payload)
    return profiles
