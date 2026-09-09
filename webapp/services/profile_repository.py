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
local.

FAILURE ATOMICITY CORRECTIVE (Phase 3.1 — bug encontrado en Phase 3):
`create()` original dejaba el archivo local escrito AUNQUE `push_path()`
devolviera `False`, razonando (correctamente para el caller HTTP, MAL para
el estado observable) que "nunca se reporta éxito sin confirmación". El
problema: `list()`/`get()` leen SOLO el filesystem local, así que ESE
mismo archivo "fallido" reaparecía como una creación exitosa en el
próximo GET — y peor, `remote_store.mirror_up()` (el ciclo periódico,
`_persist_dirs()` + `rglob("*")` sobre CUALQUIER archivo bajo un directorio
persistido) podía terminar subiéndolo de verdad más tarde, convirtiendo un
"fallo reportado" en un "éxito silencioso" asincrónico — exactamente el
contrato que Phase 3.1 prohíbe. Fix: si `push_path()` no confirma el
respaldo remoto, el archivo local se BORRA antes de levantar la excepción
(`_cleanup_failed_write`, ver abajo) — así ni `list()`/`get()` ni un
`mirror_up()` posterior pueden volver a verlo. El write atómico
(tempfile→flush→fsync→os.replace) NO cambia: la limpieza es un paso
POSTERIOR y separado, nunca reemplaza la escritura crash-safe.

CLASIFICACIÓN DE FALLO (Phase 3.1 §5): `remote_store.enabled() is False`
(Spaces sin credenciales configuradas) es `ProfileConfigIncompleteError`
— NO un fallo transitorio, es el filesystem efímero actuando como
"datastore" temporal, inaceptable para producción por diseño (ver Phase 2
§2/§10). `enabled() is True` pero `push_path()` igual devuelve `False` es
`ProfileStorageError` (SOURCE_ERROR) — un fallo externo/transitorio,
potencialmente reintentable (sin construir acá ningún framework de
retry). `ProfileConfigIncompleteError` es subclase de `ProfileStorageError`
— un `except ProfileStorageError` genérico (como el que ya usa `list()`/
`get()` en el router) sigue capturando ambas sin cambios; el router de
`create()` distingue explícitamente cuando corresponde (ver
`webapp/routers/human_capital.py`).

En un entorno sin credenciales de Spaces (dev local, CI): TODO `create()`
sigue reportando fallo (`ProfileConfigIncompleteError`) — Phase 3 §12
("NO convertir automáticamente enabled()==False en éxito") sigue vigente
sin cambios — pero AHORA el archivo local NO sobrevive esa llamada, así
que un retry en el mismo entorno nunca deja más de un archivo huérfano
acumulándose.

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
directamente, ver Phase 2 §1/§7).

REMOTE WRITE OUTCOME (Phase 3.3, corrective en `core/remote_store.py`, no
en este archivo): `remote_store.push_path()` ya NO puede devolver `False`
para un PUT remoto que en realidad se completó — el bookkeeping local de
firma (`_signatures`, usado sólo por `mirror_up()`) se separó del
resultado del PUT en sí. `create()` no cambió: sigue leyendo el mismo
booleano exactamente igual (`if remote_store.push_path(path): ...`), pero
ahora ese booleano es más fiel a la durabilidad remota real — ver
`_cleanup_failed_write` para el detalle de qué defensa quedó redundante."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import config
from core import remote_store

_logger = logging.getLogger("danwor.human_capital")

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
    contrato de error `No disponible`/SOURCE_ERROR del Hub, nunca a `Sin
    resultados`. Clasificación por defecto: SOURCE_ERROR (fallo externo/
    transitorio, potencialmente reintentable) — ver `ProfileConfigIncompleteError`
    para la excepción explícita cuando la causa real es Spaces sin
    configurar, no un fallo puntual."""


class ProfileConfigIncompleteError(ProfileStorageError):
    """Subclase de `ProfileStorageError` para el caso específico
    `remote_store.enabled() is False` (Spaces sin credenciales) — Phase
    3.1 §5: CONFIG_INCOMPLETE, NUNCA se trata como un fallo transitorio
    (nunca se convierte en éxito local-only, nunca se ofrece reintentar
    como si la próxima request pudiera resolverlo sola). Sigue siendo un
    `ProfileStorageError` a propósito: cualquier caller que capture la
    clase base (p. ej. el manejo de `list()`/`get()` en el router) sigue
    funcionando sin cambios; solo el flujo de `create()` distingue."""


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


def _cleanup_failed_write(path: Path) -> None:
    """Revierte un write local cuya durabilidad remota NUNCA se confirmó
    (Phase 3.1 root cause, ver docstring del módulo) — el archivo deja de
    existir para que NINGÚN lector posterior (`list()`/`get()`) ni el
    ciclo periódico `remote_store.mirror_up()` (que camina TODO archivo
    bajo un directorio persistido, sin importar cómo llegó ahí) puedan
    completar asincrónicamente una creación que ya se reportó como
    fallida.

    También intenta un `remote_store.delete_path(path)` best-effort.
    NOTA (Phase 3.3 — Remote Store Durability Outcome corrective): esto
    defendía originalmente contra una carrera real en `push_path()` — que
    podía devolver `False` incluso con el `PUT` remoto YA completado, si
    la excepción ocurría en el bookkeeping POSTERIOR a la subida
    (`_signatures[key] = _sig(path)`). Esa carrera está CERRADA en la
    fuente desde Phase 3.3: `push_path()` ahora separa el PUT remoto del
    bookkeeping de firma en dos `try` distintos, así que un `False` de
    `push_path()` HOY significa siempre "no se estableció durabilidad
    remota" — nunca "el PUT succeeded pero el bookkeeping local falló".
    Este `delete_path()` queda entonces como defensa-en-profundidad
    PROVABLEMENTE redundante contra ese escenario específico (nunca
    debería encontrar nada que borrar), pero se deja intacto
    deliberadamente — es inocuo (un DELETE sobre una key que nunca se
    llegó a subir es un no-op best-effort, ver `delete_path()`) y
    remover código de limpieza que sigue siendo seguro está fuera del
    scope mínimo de esta fase. `delete_path()` opera EXCLUSIVAMENTE sobre
    el objeto remoto (nunca toca disco, ver su propio docstring) — llamarlo
    después de borrar el archivo local es seguro (no depende de que el
    archivo siga existiendo). Nunca deja escapar sus propios errores: son
    best-effort, no deben enmascarar la excepción de clasificación
    (`ProfileConfigIncompleteError`/`ProfileStorageError`) que el llamador
    ya va a levantar."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        _logger.warning("human_capital.profile_cleanup_failed reason=%s", type(exc).__name__)
    try:
        remote_store.delete_path(path)
    except Exception as exc:  # noqa: BLE001 — best-effort, nunca enmascara el fallo real
        _logger.warning("human_capital.profile_remote_cleanup_failed reason=%s", type(exc).__name__)


def create(profile: dict) -> dict:
    """Crea un Profile nuevo: genera `profile_id` (uuid4) + `schema_version`
    + `created_at`/`updated_at`, escribe el JSON localmente (crash-safe) y
    confirma el write-through a Spaces vía `remote_store.push_path()` ANTES
    de devolver — ver DURABILITY CONTRACT arriba. `profile` ya debe venir
    validado/shapeado por el caller (`human_capital_service.create_profile`)
    — este repository no conoce reglas de negocio, solo persiste lo que se
    le pasa.

    Levanta `ProfileConfigIncompleteError` si Spaces no está configurado
    (`remote_store.enabled() is False`) o `ProfileStorageError` (SOURCE_ERROR)
    si está configurado pero `push_path()` igual no confirma el respaldo —
    en NINGÚN caso se reporta éxito sin esa confirmación, Y en NINGÚN caso
    el archivo local sobrevive a un fallo reportado (Phase 3.1, ver
    `_cleanup_failed_write`) — un GET inmediato, un restart/restore, o el
    ciclo periódico de `mirror_up()` nunca pueden resucitar una creación ya
    reportada como fallida."""
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
    if remote_store.push_path(path):
        return payload
    # Durabilidad remota NO confirmada -- la creación es un FALLO de punta
    # a punta: el archivo local NUNCA debe sobrevivir a esta llamada (ver
    # docstring del módulo, Phase 3.1 root cause).
    _cleanup_failed_write(path)
    if not remote_store.enabled():
        raise ProfileConfigIncompleteError(
            "El Banco de Perfiles todavía no está configurado para este entorno."
        )
    raise ProfileStorageError(
        "No pudimos confirmar el respaldo del Profile en este momento."
    )


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
