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
`_cleanup_failed_write` para el detalle de qué defensa quedó redundante.

SAFE PROFILE EDIT (Phase 3.5 — `update()`, nuevo en esta fase): a
diferencia de `create()` (siempre un `profile_id` nuevo, nunca hay una
versión previa que proteger), `update()` sobrescribe un Profile
EXISTENTE — Phase 3.4 (forensic, sin código) estableció que ni un
remote-read-back de verificación (rechazado explícitamente: la
consistencia de overwrite de DO Spaces no está probada en este proyecto
como para confiar en comparar un GET inmediato) ni un `delete_path()`
como compensación (borraría datos de negocio reales) son aceptables acá.
El contrato adoptado es COMPENSATING OVERWRITE (Phase 3.5 §2/§3): si el
push del candidato B no se confirma, se sobrescribe local Y remoto de
nuevo con la versión anterior A (`_write_atomic` + `push_path`, MISMOS
primitivos ya probados, nunca un mecanismo nuevo) — nunca un GET de
verificación, nunca un delete. Esto es correcto sea que B nunca haya
llegado al remoto O que haya llegado pero el ACK se perdió: en AMBOS
casos, volver a escribir A produce un estado remoto conocido (A) si esa
segunda escritura se confirma.

CONCURRENCIA (Phase 3.4 §8-11, Phase 3.5 §8-9): un `threading.Lock()`
GLOBAL de módulo (`_update_lock`, NUNCA un diccionario de locks por
Profile — ver Phase 3.4 §10, "no lock dictionary") serializa la sección
crítica COMPLETA de `update()` (leer versión actual → comparar revision
→ escribir B → intentar push → eventual rollback a A → resultado final)
dentro de ESTE proceso. Un lock process-local NO protege contra múltiples
réplicas (Phase 3.4 §11/Phase 3.5 §9) — production Profile update sigue
gateado por `App Platform replica count == 1`, sin resolver acá.

REVISION (Phase 3.5 §5/§6): token técnico de concurrencia optimista,
separado de `schema_version` (formato) y `updated_at` (auditoría/display).
`create()` fija `revision=1`. Un Profile de fases anteriores (3/3.1/3.2)
sin `revision` en disco se LEE como `revision=1` (`get()`/`list()`, ver
abajo) — nunca una migración/rewrite masivo, nunca un write disparado
solo por una lectura.

CREATE — IN-DOUBT REMOTE DURABILITY (Phase 3.6): Phase 3.4/3.6 probaron
que un `push_path()` que devuelve `False` (post Phase 3.3) NO prueba
ausencia remota — una excepción de transporte solo prueba que el caller
no recibió confirmación, nunca que el PUT no haya llegado a completarse
del lado del servidor (ack perdido). `delete_path()` tiene EXACTAMENTE
la misma ambigüedad (su propio docstring ya lo dice: best-effort) — un
`False` tampoco prueba que el objeto siga existiendo, y un `True` tampoco
prueba que existiera antes (`DeleteObject` sobre una key inexistente
también "confirma" sin error). Por lo tanto NINGUNA combinación de
push/delete booleanos puede, por sí sola, demostrar la ausencia remota de
un `create()` fallido — y `remote_store.restore()` no tiene forma de
distinguir un objeto "committed" de uno de un `create()` sin resolver: si
ese objeto quedó huérfano en el bucket, un restore posterior lo bajaría
como un Profile legítimo más.

Contrato adoptado (mínimo, evidencia-first, sin GET de verificación —
Phase 3.4/3.5 ya establecieron que la consistencia de overwrite de DO
Spaces no está probada en este proyecto como para confiar en un GET
inmediato, y esa misma razón aplica acá): UN reintento explícito al
MISMO `profile_id`/key/contenido (nunca un UUID nuevo — un reintento de
persistencia interno no es una nueva intención de crear un Profile) antes
de concluir cualquier cosa. Si el reintento confirma, éxito normal. Si el
reintento TAMPOCO confirma, NO se reporta un `source_error` común (eso
afirmaría una certeza de fallo que no existe) — se levanta
`ProfileRemoteOutcomeUnknownError` (reusa la clase de Phase 3.5, mismo
significado semántico exacto: "el estado remoto final no puede
establecerse"). El archivo local se limpia en TODOS los casos de fallo
(mismo invariante de Phase 3.1, sin relajar) — la limpieza está separada
de la clasificación de qué tan cierto es ese fallo. `_cleanup_failed_write`
(delete local + `delete_path` best-effort) sigue siendo la única acción
de compensación — nunca se interpreta su resultado como prueba de nada
(§10 del ticket).

Riesgo residual, DIVULGADO explícitamente, no resuelto: si AMBOS intentos
de PUT en realidad llegaron al remoto pero AMBOS acks se perdieron (una
doble falla de red consecutiva, un escenario genuinamente raro), el
objeto queda huérfano en el bucket y un restore futuro podría
resucitarlo como un Profile ordinario. Cerrar esto por completo
requeriría un protocolo de staging/promote (key temporal → CopyObject →
delete) con más estados remotos y más máquina de recuperación — el
ticket explícitamente pide NO construir eso sin evidencia que lo
justifique, y una doble-pérdida-de-ack consecutiva no es esa evidencia."""
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
_PROFILES_SUBDIR = "profiles"
# Acotado, MISMO valor/razonamiento que
# reporting_time_logs_durable._RESTORE_WAIT_TIMEOUT_S: generoso frente al
# costo real que evita, pero nunca indefinido.
_RESTORE_WAIT_TIMEOUT_S = 15.0

# Phase 3.5 §8: lock GLOBAL de módulo, NUNCA por-Profile (sin diccionario
# de locks, sin framework) — cubre la sección crítica COMPLETA de
# update() de punta a punta, nunca se libera entre el chequeo de revision
# y el resultado final (ver update()).
_update_lock = threading.Lock()

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


class ProfileNotFoundError(Exception):
    """`update()` sobre un `profile_id` que no existe (Phase 3.5 §10) —
    NUNCA una subclase de `ProfileStorageError`: no es un fallo de
    persistencia, es que el objetivo de la operación no existe. Distinto
    del criterio fail-soft de `get()` (que devuelve `None`, nunca levanta):
    `update()` necesita un target concreto para operar, así que un
    `profile_id` inexistente es un error del caller/dominio, no un miss
    silencioso — el router lo traduce a un 404 HTTP genuino, nunca a uno
    de los estados renderizados dentro del panel."""


class ProfileConcurrentModificationError(Exception):
    """El Profile cambió desde que se leyó la `revision` esperada (Phase
    3.4 §12/Phase 3.5 §10) — se detecta ANTES de cualquier escritura en
    disco (ver `update()`, el chequeo corre antes del write atómico).
    Deliberadamente NO es un `ProfileStorageError`: no es un fallo de
    persistencia, es una precondición de concurrencia optimista que no se
    cumplió — el router lo traduce a un estado `concurrent_modification`
    propio, nunca a `No disponible` ni a un error de validación genérico
    (Phase 3.5 §16)."""


class ProfileRemoteOutcomeUnknownError(Exception):
    """El estado remoto final no puede establecerse — MISMO significado
    semántico en sus DOS orígenes distintos:
      - `update()` (Phase 3.5 §2B): el candidato B no se confirmó Y la
        compensación (reescribir A) TAMPOCO se confirmó. El archivo LOCAL
        ya fue restaurado a A en ese punto — nunca queda en B.
      - `create()` (Phase 3.6 §7): el push inicial no se confirmó Y el
        reintento explícito al MISMO profile_id/key TAMPOCO se confirmó.
        El archivo LOCAL ya fue limpiado en ese punto (`_cleanup_failed_write`,
        MISMO invariante de Phase 3.1) — nunca sobrevive un create sin
        resolver.
    Deliberadamente NO es un `ProfileStorageError`: un `except
    ProfileStorageError` genérico NUNCA debe atrapar esto por accidente y
    aplanarlo a un SOURCE_ERROR común (Phase 3.5 §10, explícito: "Do not
    silently flatten this into source_error") — es una situación semántica
    propia, distinta, que el router debe manejar con su propio `except`
    explícito en AMBOS flujos (crear/editar)."""


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
    + `revision=1` (Phase 3.5, token de concurrencia optimista — ver
    docstring del módulo) + `created_at`/`updated_at`, escribe el JSON
    localmente (crash-safe) y confirma el write-through a Spaces vía
    `remote_store.push_path()` ANTES de devolver — ver DURABILITY CONTRACT
    arriba. `profile` ya debe venir validado/shapeado por el caller
    (`human_capital_service.create_profile`) — este repository no conoce
    reglas de negocio, solo persiste lo que se le pasa.

    Secuencia de fallo (Phase 3.6 — ver "CREATE — IN-DOUBT REMOTE
    DURABILITY" en el docstring del módulo):
      1. `push_path(path)` — si confirma, ÉXITO inmediato.
      2. Si NO confirma y `remote_store.enabled() is False`: determinístico
         — ni este intento ni un reintento pueden confirmar jamás en este
         entorno, así que NO se reintenta. Limpieza + `ProfileConfigIncompleteError`.
      3. Si NO confirma y `enabled() is True`: UN reintento explícito al
         MISMO `path`/key/contenido (nunca un `profile_id` nuevo — un
         reintento de persistencia interno no es una nueva intención de
         crear). Si el reintento confirma, ÉXITO (mismo `profile_id` de
         siempre, nunca un Profile duplicado).
      4. Si el reintento TAMPOCO confirma: no podemos concluir que el
         remoto nunca tuvo el Profile (un `push_path()`/`delete_path()`
         que falla no prueba ausencia ni existencia, §10 del ticket) —
         limpieza + `ProfileRemoteOutcomeUnknownError`, NUNCA un
         `ProfileStorageError`/SOURCE_ERROR común que afirmaría una
         certeza de fallo que no existe.

    En NINGÚN caso de fallo (determinístico o desconocido) el archivo
    local sobrevive a esta llamada (Phase 3.1, ver `_cleanup_failed_write`)
    — un GET inmediato, un restart/restore, o el ciclo periódico de
    `mirror_up()` nunca pueden resucitar LOCALMENTE una creación ya
    reportada como fallida (el riesgo residual de un objeto REMOTO
    huérfano tras una doble pérdida de ack está divulgado, no resuelto,
    ver docstring del módulo)."""
    _ensure_restored()
    now = _now_iso()
    profile_id = str(uuid4())
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "profile_id": profile_id,
        "revision": 1,
        **profile,
        "created_at": now,
        "updated_at": now,
    }
    path = _profile_path(profile_id)
    _write_atomic(path, payload)
    if remote_store.push_path(path):
        return payload
    if not remote_store.enabled():
        # Determinístico (Phase 3.6 §6/§7): reintentar acá no puede
        # cambiar nada, `enabled()` sigue False siempre -- ni se intenta.
        _cleanup_failed_write(path)
        raise ProfileConfigIncompleteError(
            "El Banco de Perfiles todavía no está configurado para este entorno."
        )
    # Primer push NO confirmado, Spaces SÍ configurado -- un solo
    # reintento explícito al MISMO profile_id/key/contenido (Phase 3.6
    # §5/§6) antes de concluir cualquier cosa.
    if remote_store.push_path(path):
        return payload
    # El reintento TAMPOCO confirmó -- clasificación honesta: no podemos
    # probar ausencia remota (Phase 3.6 §7/§10), así que NUNCA se reporta
    # como un source_error común.
    _cleanup_failed_write(path)
    raise ProfileRemoteOutcomeUnknownError(
        "No pudimos confirmar si este Profile se guardó. Antes de reintentar, "
        "revisá el listado — si ya aparece, no lo vuelvas a cargar."
    )


def _with_revision_default(payload: dict) -> dict:
    """Backward compatibility (Phase 3.5 §6): un Profile persistido en
    fases anteriores (3/3.1/3.2/3.3) nunca escribió `revision` — se LEE
    como `revision=1` acá, en memoria, cada vez que se carga. Nunca
    reescribe el archivo en disco: "no rewrite merely because it was
    read" es explícito en el ticket — el campo solo queda persistido de
    verdad la próxima vez que ese Profile pase por un write real
    (`create()` ya lo fija; `update()` lo incrementa)."""
    payload.setdefault("revision", 1)
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
    return _with_revision_default(payload) if isinstance(payload, dict) else None


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
            profiles.append(_with_revision_default(payload))
    return profiles


def update(profile_id: str, expected_revision: int, fields: dict) -> dict:
    """Sobrescribe un Profile EXISTENTE con COMPENSATING OVERWRITE (Phase
    3.5 §2/§3, ver docstring del módulo) — nunca un remote-read-back de
    verificación (rechazado en Phase 3.5 §1: la consistencia de overwrite
    de DO Spaces no está probada en este proyecto), nunca `delete_path()`
    (Phase 3.5 §4: el Profile anterior es dato de negocio real, jamás se
    borra como parte de un rollback de update).

    `fields` ya debe venir validado/shapeado por el caller
    (`human_capital_service.update_profile`) — MISMA división de
    responsabilidad que `create()`. `expected_revision` es el token de
    concurrencia optimista leído por el caller ANTES de construir el
    candidato — se compara contra la `revision` actual DENTRO del lock,
    nunca antes (un check-then-write sin lock no alcanza, Phase 3.4 §10).

    Secuencia (lock GLOBAL de módulo, sostenido de punta a punta — Phase
    3.5 §8, nunca se libera entre el chequeo de revision y el resultado
    final):
      1. `get(profile_id)` — si no existe, `ProfileNotFoundError` (ver esa
         clase).
      2. Si `revision` actual != `expected_revision`:
         `ProfileConcurrentModificationError` ANTES de cualquier write —
         el Profile en disco queda completamente intacto.
      3. Construye el candidato B (`revision` = actual+1, `updated_at`
         nuevo, `created_at`/`profile_id`/`schema_version` preservados de
         A, nunca pisados por `fields`) y lo escribe local atómicamente.
      4. `push_path(B)`:
         - `True` → ÉXITO. Devuelve B. Local B, remoto B.
         - `False` y `remote_store.enabled() is False` → CONFIG_INCOMPLETE
           determinístico (nunca "unknown": ni B ni una eventual
           compensación pueden confirmarse jamás en este entorno) —
           restaura A localmente (el local NUNCA debe quedar en B sin
           confirmación) y levanta `ProfileConfigIncompleteError`, sin
           intentar un segundo push que ya se sabe que también fallaría.
         - `False` y `enabled() is True` → intenta la COMPENSACIÓN:
           reescribe A localmente (atómico, SIEMPRE corre, sea que la
           compensación remota después confirme o no — el local queda en
           A de inmediato) y `push_path(A)`:
             - `True` → rollback confirmado: local A, remoto A, estado
               consistente y conocido. `ProfileStorageError` (SOURCE_ERROR
               — "no pudimos confirmar B, pero A sigue intacto").
             - `False` → rollback NO confirmado: local A (ya restaurado),
               remoto DESCONOCIDO. `ProfileRemoteOutcomeUnknownError` —
               nunca se aplana a `ProfileStorageError` (ver esa clase)."""
    with _update_lock:
        current = get(profile_id)
        if current is None:
            raise ProfileNotFoundError(f"El Profile {profile_id} no existe.")
        current_revision = current.get("revision", 1)
        if current_revision != expected_revision:
            raise ProfileConcurrentModificationError(
                "Este Profile cambió desde que abriste el formulario de edición."
            )
        path = _profile_path(profile_id)
        candidate = {
            **current,
            **fields,
            "profile_id": profile_id,
            "schema_version": _SCHEMA_VERSION,
            "created_at": current.get("created_at"),
            "revision": current_revision + 1,
            "updated_at": _now_iso(),
        }
        _write_atomic(path, candidate)
        if remote_store.push_path(path):
            return candidate
        # B no confirmado.
        if not remote_store.enabled():
            # Determinístico, nunca "unknown": en este entorno ni B ni una
            # eventual compensación pueden confirmarse jamás -- restaura A
            # sin intentar un segundo push que ya se sabe inútil.
            _write_atomic(path, current)
            raise ProfileConfigIncompleteError(
                "El Banco de Perfiles todavía no está configurado para este entorno."
            )
        # Compensating overwrite -- Spaces SÍ está configurado, así que la
        # compensación puede genuinamente confirmar o no. El local queda
        # en A de inmediato, independientemente de si el push que sigue
        # confirma (Phase 3.5 §11: "local state should remain A").
        _write_atomic(path, current)
        if remote_store.push_path(path):
            raise ProfileStorageError(
                "No pudimos confirmar el respaldo del cambio; se mantuvo la versión anterior del Profile."
            )
        raise ProfileRemoteOutcomeUnknownError(
            "No pudimos confirmar el estado remoto de este Profile tras un fallo de guardado. "
            "La versión local se mantuvo en el último estado conocido."
        )
