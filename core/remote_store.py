"""Persistencia de artefactos runtime contra DO Spaces (S3-compatible).

App Platform corre sobre un filesystem efímero: sin esto, todo lo que el copiloto
genera (catálogo, casos, feedback, adopción, analítica) se pierde en cada deploy o
reciclado de la plataforma. Este módulo respalda esos directorios en un bucket
S3-compatible y los restaura al arrancar.

Estrategia (bajo acoplamiento, no toca los writers):
  - restore()   → al boot: baja el estado previo desde el bucket al FS local.
  - mirror_up() → periódico + al apagar: sube los archivos nuevos/cambiados.
  - push_path() → helper para write-through inmediato de un archivo puntual (opcional).

MODO DE PERSISTENCIA (``DANWOR_PERSISTENCE``, ver config.py): las credenciales indican qué está DISPONIBLE, no qué está AUTORIZADO. Cada operación
exige su capacidad:
  - ``local`` (default)  → ninguna: no-op total, sin cliente S3, aunque haya credenciales (localhost nunca toca el bucket productivo).
  - ``read-only``        → solo LECTURA remota explícita (``restore`` manual, ``read_object_bytes``); NUNCA escribe (``mirror_up``/``push_path``/``delete_path``).
  - ``remote``           → producción: restore en el arranque, sincronización periódica y write-through.
Además ``restore`` NO sobrescribe archivos locales existentes por defecto (los que difieren del remoto quedan "sin reconciliar" y ``mirror_up`` no los sube), y
``mirror_up`` no corre mientras el restore esté en curso, haya fallado o haya quedado incompleto.

Si faltan credenciales o el modo no lo autoriza TODO es no-op → dev local sin cambios.
boto3 se importa de forma lazy para no volverlo dependencia dura del import.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from pathlib import Path

import config

log = logging.getLogger("danwor.remote_store")

_client = None
_client_lock = threading.Lock()
# Firma (tamaño, mtime_ns) del último estado subido/bajado por key remoto, para
# saltear re-subidas de archivos sin cambios dentro de la vida del proceso.
_signatures: dict[str, tuple[int, int]] = {}

# RESTORE-COMPLETION coordination (Integral Cold Start Fase 1, ver HANDOFF):
# un consumidor lazy (hoy sólo `reporting_time_logs_durable.py`) necesita
# saber si el restore de arranque YA bajó su estado durable al FS local
# ANTES de intentar leerlo — leer disco mientras el restore sigue en vuelo
# reportaría un miss falso aunque el archivo esté a segundos de aparecer.
# `_restore_state` empieza en "not_applicable" (nunca se tocó: o bien
# `enabled()` es False, o `restore()` nunca se llamó todavía) — SOLO
# `restore()` mismo lo mueve a "in_progress"/"completed"/"failed", nunca un
# caller externo (evita un segundo punto de verdad sobre CUÁNDO arrancó).
# `threading.Event`, no `asyncio.Event`: el consumidor real corre en un
# request handler SYNC de FastAPI (threadpool), nunca en el event loop.
_restore_state = "not_applicable"
# `restore()` quedó DEGRADADO (falló un listado o una descarga): el estado local ya no es una copia fiel del remoto. Es un eje APARTE de
# `_restore_state` (que sigue "completed" en una falla parcial, contrato histórico): solo bloquea `mirror_up`, nunca a los consumidores de lectura.
_restore_degraded = False
# Keys remotos cuyo archivo LOCAL existe y NO se reconcilió con el remoto (restore sin sobrescribir, o descarga fallida): `mirror_up` jamás los sube; se
# resuelven con un `restore(overwrite=True)` explícito (o borrando el local). Evita que el sync pise el bucket con copias locales no verificadas.
_unreconciled: set[str] = set()
last_restore_report: dict = {}
_restore_state_lock = threading.Lock()
_restore_done = threading.Event()
_restore_elapsed_ms: float | None = None


class RemoteReadError(Exception):
    """`read_object_bytes()` no pudo DETERMINAR el contenido remoto de una key
    (cliente no inicializable, persistencia deshabilitada, o el GET falló por
    un motivo que NO es "el objeto no existe"). Es el estado *irresoluble* de
    la verificación por bytes: el llamador (p. ej. la máquina de estados del
    refresh de catálogos CCHH) NO puede concluir si el objeto remoto es A, B
    o ausente, y debe tratarlo como durabilidad no confirmada. Distinto de un
    `None` de retorno, que significa EXACTAMENTE "el objeto no existe"."""


def enabled() -> bool:
    """Persistencia remota COMPLETA autorizada (``DANWOR_PERSISTENCE=remote`` + credenciales): restore de arranque, sync periódico y write-through. Es lo que
    consumen los repositorios para decidir si hay durabilidad remota (en ``local``/``read-only`` se comportan como "Spaces sin configurar")."""
    return bool(config.PERSIST_ENABLED)


def read_enabled() -> bool:
    """Se permite LEER del bucket (``restore`` explícito, ``read_object_bytes``): modo ``remote`` o ``read-only`` con credenciales."""
    return bool(config.PERSIST_ENABLED) or bool(getattr(config, "PERSIST_READ_ONLY", False))


def write_enabled() -> bool:
    """Se permite ESCRIBIR en el bucket (``mirror_up``, ``push_path``, ``delete_path``): SOLO el modo ``remote`` con credenciales."""
    return bool(config.PERSIST_ENABLED)


def sync_blocked_reason() -> str | None:
    """Por qué ``mirror_up`` NO debe correr ahora (``None`` = puede). Nunca sincronizar sobre un restore incompleto: los archivos locales aún no están
    reconciliados con el remoto y subirlos podría pisar objetos productivos con copias viejas o parciales."""
    if _restore_state == "in_progress":
        return "restore en curso"
    if _restore_state == "failed":
        return "el restore falló"
    if _restore_degraded:
        return "el restore quedó incompleto (fallos de listado o descarga)"
    return None


def mode_warning() -> str | None:
    """Aviso de arranque si hay credenciales Spaces pero la persistencia remota completa NO está autorizada (típico de un despliegue al que le falta
    ``DANWOR_PERSISTENCE=remote``: no persistiría nada). ``None`` si no hay nada que advertir (sin credenciales, o modo ``remote``)."""
    if not getattr(config, "SPACES_CONFIGURED", False) or enabled():
        return None
    mode = getattr(config, "PERSISTENCE_MODE", "local")
    return (f"Credenciales Spaces presentes pero DANWOR_PERSISTENCE={mode}: restore, sincronización y escrituras remotas están "
            f"{'LIMITADOS a lectura explícita' if mode == 'read-only' else 'DESACTIVADOS'}. En producción debe ser DANWOR_PERSISTENCE=remote.")


def describe_mode() -> str:
    """Una línea de diagnóstico del modo efectivo, para el log de arranque."""
    mode = getattr(config, "PERSISTENCE_MODE", "local")
    configured = bool(getattr(config, "SPACES_CONFIGURED", False))
    return (f"DANWOR_PERSISTENCE={mode} · credenciales Spaces {'presentes' if configured else 'ausentes'} · "
            f"lectura remota {'sí' if read_enabled() else 'no'} · escritura remota {'sí' if write_enabled() else 'no'}")


def restore_status() -> str:
    """'not_applicable' (Spaces deshabilitado, o `restore()` nunca corrió) |
    'in_progress' | 'completed' | 'failed'. Lectura pura, nunca bloquea."""
    return _restore_state


def restore_elapsed_ms() -> float | None:
    """`None` hasta que `restore()` termina al menos una vez en este
    proceso — SOLO diagnóstico (§10 del ticket), nunca decide comportamiento."""
    return _restore_elapsed_ms


def await_restore(timeout: float) -> str:
    """Bloqueante (uso desde un thread SYNC, nunca desde el event loop) —
    si el restore YA terminó (o nunca aplica), devuelve de inmediato. Si
    sigue en curso, espera hasta `timeout` segundos y devuelve el estado
    que encuentre — puede seguir siendo 'in_progress' si el timeout venció
    primero; el caller SIEMPRE debe tratar cualquier resultado que no sea
    'completed' como "sin garantía de estado durable disponible" y
    degradar con gracia (nunca lanza, nunca bloquea más allá de
    `timeout`)."""
    if _restore_state != "in_progress":
        return _restore_state
    _restore_done.wait(timeout=timeout)
    return _restore_state


def _key(rel_posix: str) -> str:
    """Antepone el prefijo opcional (separación de ambientes) al key remoto."""
    prefix = config.SPACES_PREFIX
    return f"{prefix}/{rel_posix}" if prefix else rel_posix


def _persist_dirs() -> list[tuple[str, Path]]:
    """(nombre-lógico, ruta-local) por cada directorio a persistir. El catálogo
    respeta DANWOR_CATALOG_DIR si está definido; el resto cuelga de BASE_DIR."""
    base = config.BASE_DIR
    dirs: list[tuple[str, Path]] = []
    for name in config.PERSIST_PATHS:
        if name == "catalog_local":
            local = Path(os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(base / "catalog_local"))
        elif name == config.GOVERNED_ASSESSMENTS_NAME:
            local = config.governed_assessments_dir()          # DANWOR_GOVERNED_ASSESSMENTS_DIR si está definido
        else:
            local = base / name
        dirs.append((name, local))
    return dirs


def _get_client():
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            import boto3
            from botocore.config import Config as BotoConfig

            _client = boto3.client(
                "s3",
                region_name=config.SPACES_REGION,
                endpoint_url=config.SPACES_ENDPOINT,
                aws_access_key_id=config.SPACES_KEY,
                aws_secret_access_key=config.SPACES_SECRET,
                # virtual-hosted → host = <bucket>.<region>.digitaloceanspaces.com
                config=BotoConfig(s3={"addressing_style": "virtual"}, retries={"max_attempts": 3}),
            )
    return _client


def _sig(path: Path) -> tuple[int, int]:
    st = path.stat()
    return (st.st_size, st.st_mtime_ns)


def _matches_remote(path: Path, size, etag) -> bool:
    """El archivo local YA es la copia del objeto remoto (mismo tamaño y, si el ETag es un md5 simple, mismo contenido). Sin metadatos → no se puede afirmar."""
    try:
        if size is None or path.stat().st_size != int(size):
            return False
        etag = (etag or "").strip('"')
        if not etag or "-" in etag:                      # sin ETag o multipart: el tamaño es lo máximo verificable
            return True
        import hashlib
        return hashlib.md5(path.read_bytes()).hexdigest() == etag  # noqa: S324 — verificación de identidad, no criptografía
    except OSError:
        return False


def _seed_digest(key: str, etag) -> None:
    """Contenido ya idéntico en Spaces, conocido por su ETag cuando es un md5 simple (sin multipart ni cifrado): metadato que el restore ya recibe; no se lee ningún archivo."""
    e = str(etag or "").strip('"').lower()
    if len(e) == 32 and all(c in "0123456789abcdef" for c in e):
        _digests[key] = e


def restore(overwrite: bool = False) -> int:
    """Baja al FS local lo persistido en el bucket. Pensado para el boot, cuando el
    FS efímero arranca vacío. Devuelve la cantidad de archivos bajados.

    NO sobrescribe archivos locales existentes por defecto (``overwrite=False``): si el local ya es idéntico al remoto se da por reconciliado; si difiere queda
    registrado en ``_unreconciled`` y ``mirror_up`` nunca lo sube. ``overwrite=True`` es el reemplazo EXPLÍCITO y controlado (restauración manual). Requiere
    capacidad de lectura (``read-only`` o ``remote``).

    RESTORE-COMPLETION coordination (ver arriba): marca `_restore_state`
    'in_progress' apenas arranca (SOLO si `enabled()`, si no queda
    'not_applicable' para siempre — ningún consumidor lazy debe esperar por
    algo que nunca va a pasar) y 'completed'/'failed' al terminar, siempre
    despertando a `_restore_done` — así un waiter que ya estaba bloqueado en
    `await_restore` se libera apenas hay un resultado, sin polling."""
    global _restore_state, _restore_elapsed_ms, _restore_degraded
    if not read_enabled():
        log.info("remote_store: lectura remota no autorizada (%s) — restore no-op", describe_mode())
        return 0
    _restore_degraded = False
    with _restore_state_lock:
        _restore_state = "in_progress"
    _restore_started = time.perf_counter()
    try:
        client = _get_client()
    except Exception as exc:  # boto3 ausente o config inválida
        log.warning("remote_store.restore: no se pudo inicializar el cliente: %s", exc)
        with _restore_state_lock:
            _restore_state = "failed"
        _restore_elapsed_ms = (time.perf_counter() - _restore_started) * 1000
        _restore_done.set()
        return 0

    bucket = config.SPACES_BUCKET
    # 1) Listar todo lo persistido (rápido) y 2) bajar en paralelo: el restore
    # corre en background al boot, y cuanto antes termine, antes queda disponible
    # la memoria (menos ventana con catálogo/casos vacíos tras un deploy).
    jobs: list[tuple[str, Path, object, object]] = []
    listing_failed = False
    for name, local in _persist_dirs():
        root_key = _key(name)
        paginator = client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=bucket, Prefix=root_key + "/"):
                for obj in page.get("Contents", []) or []:
                    key = obj["Key"]
                    sub = key[len(root_key) + 1:]
                    if not sub or key.endswith("/"):
                        continue  # marcador de carpeta
                    jobs.append((key, local / sub, obj.get("Size"), obj.get("ETag")))
        except Exception as exc:
            listing_failed = True
            log.warning("remote_store.restore: fallo listando '%s': %s", name, exc)

    downloaded = reconciled = skipped = failed = 0
    if jobs:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _download(job: tuple) -> str:
            key, dest, size, etag = job
            if dest.exists() and not overwrite:
                if _matches_remote(dest, size, etag):
                    _signatures[key] = _sig(dest)            # el local ya ES la copia remota: reconciliado
                    _seed_digest(key, etag)
                    return "reconciled"
                _unreconciled.add(key)                        # el local difiere (o no se puede verificar): se PRESERVA y mirror_up no lo sube
                return "skipped"
            dest.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(bucket, key, str(dest))
            # Sembramos la firma para que el primer mirror_up no re-suba lo bajado.
            _signatures[key] = _sig(dest)
            _seed_digest(key, etag)                           # y el contenido confirmado (ETag md5), para que una regeneración que reescribe lo MISMO no lo vuelva a subir
            _unreconciled.discard(key)
            return "downloaded"

        with ThreadPoolExecutor(max_workers=min(16, len(jobs))) as pool:
            futures = {pool.submit(_download, job): job for job in jobs}
            for future in as_completed(futures):
                try:
                    outcome = future.result()
                    downloaded += outcome == "downloaded"
                    reconciled += outcome == "reconciled"
                    skipped += outcome == "skipped"
                except Exception as exc:
                    failed += 1
                    _unreconciled.add(futures[future][0])    # la descarga falló: si existe un local previo, queda sin reconciliar
                    log.warning("remote_store.restore: fallo bajando un objeto: %s", exc)
    _restore_degraded = bool(listing_failed or failed)
    last_restore_report.clear()
    last_restore_report.update({"downloaded": downloaded, "reconciled": reconciled, "skipped_existing": skipped, "failed": failed,
                                "listing_failed": listing_failed, "overwrite": overwrite, "unreconciled": len(_unreconciled)})
    log.info("remote_store.restore: %d archivos restaurados desde Spaces (ya idénticos: %d · locales preservados sin reconciliar: %d · fallidos: %d)",
             downloaded, reconciled, skipped, failed)
    if _restore_degraded:
        log.warning("remote_store.restore: restore INCOMPLETO (fallidos=%d, listados fallidos=%s): mirror_up queda BLOQUEADO hasta un restore completo.",
                    failed, listing_failed)
    if _unreconciled:
        log.warning("remote_store.restore: %d archivo(s) local(es) difieren del remoto y se PRESERVARON; mirror_up no los subirá "
                    "(reconciliar con restore(overwrite=True) o resolver a mano).", len(_unreconciled))
    with _restore_state_lock:
        _restore_state = "completed"
    _restore_elapsed_ms = (time.perf_counter() - _restore_started) * 1000
    _restore_done.set()
    return downloaded


def mirror_up() -> int:
    """Sube al bucket los archivos nuevos o modificados de cada directorio. Salta
    los que no cambiaron respecto de la última sincronización de este proceso.

    Requiere capacidad de ESCRITURA (solo ``remote``) y NO corre mientras el restore esté en curso, haya fallado o haya quedado incompleto
    (``sync_blocked_reason``); además jamás sube un key en ``_unreconciled`` (local que difiere del remoto y no se reconcilió)."""
    if not write_enabled():
        return 0
    blocked = sync_blocked_reason()
    if blocked is not None:
        (log.info if _restore_state == "in_progress" else log.warning)("remote_store.mirror_up: BLOQUEADO — %s; no se sube nada.", blocked)
        return 0
    try:
        client = _get_client()
    except Exception as exc:
        log.warning("remote_store.mirror_up: no se pudo inicializar el cliente: %s", exc)
        return 0

    bucket = config.SPACES_BUCKET
    uploaded = 0
    for name, local in _persist_dirs():
        if not local.is_dir():
            continue
        with _hold_cv:                                         # «¿está retenido?» y «entro a publicarlo» son UN solo paso atómico: un hold no puede colarse entre ambos
            if _held.get(name):                                # un productor lo está regenerando con persistencia explícita: no se publican resultados a medias
                log.info("remote_store.mirror_up: '%s' retenido durante una regeneración en curso; se omite en este ciclo.", name)
                continue
            _inflight[name] = _inflight.get(name, 0) + 1       # ciclo EN VUELO sobre este directorio: un hold nuevo espera a que termine (barrera)
        try:
            uploaded += _mirror_dir(client, bucket, name, local)
        finally:
            with _hold_cv:
                _inflight[name] = _inflight.get(name, 1) - 1
                if _inflight[name] <= 0:
                    _inflight.pop(name, None)
                _hold_cv.notify_all()
    if uploaded:
        log.info("remote_store.mirror_up: %d archivos sincronizados a Spaces", uploaded)
    return uploaded


def _mirror_dir(client, bucket: str, name: str, local: Path) -> int:
    uploaded = 0
    root_key = _key(name)
    for path in local.rglob("*"):
        if _held.get(name):                                    # DEFENSA ADICIONAL por archivo: un hold adquirido durante el barrido detiene la publicación en el siguiente archivo
            log.info("remote_store.mirror_up: '%s' pasó a retenido durante el barrido; se detiene en este ciclo (lo restante se publica cuando se libere).", name)
            break
        if not path.is_file():
            continue
        key = f"{root_key}/{path.relative_to(local).as_posix()}"
        if key in _unreconciled:
            continue
        try:
            sig = _sig(path)
        except OSError:
            continue
        if _signatures.get(key) == sig:
            continue
        if _withheld.get(key) == sig:                          # resultado parcial de una regeneración fallida/incompleta, sin cambios desde entonces: no se publica
            continue
        try:
            client.upload_file(str(path), bucket, key)
            _signatures[key] = sig
            uploaded += 1
        except Exception as exc:
            log.warning("remote_store.mirror_up: fallo subiendo '%s': %s", key, exc)
    return uploaded


def push_path(local_path: str | Path) -> bool:
    """Sube un archivo puntual (write-through opcional). El archivo debe caer bajo
    alguno de los directorios persistidos; si no, se ignora. Devuelve True si subió.

    CONTRATO (Capital Humano Phase 3.3 — Remote Store Durability Outcome
    corrective): `True` significa EXACTAMENTE "el PUT remoto quedó
    establecido exitosamente" — nada más. `False` significa EXACTAMENTE
    "no se estableció durabilidad remota" (deshabilitado, path fuera de
    los directorios persistidos, o el PUT en sí falló). El bookkeeping
    LOCAL posterior al PUT (`_signatures[key] = _sig(path)`, que existe
    únicamente para que `mirror_up()` no re-suba innecesariamente lo que
    `push_path()` ya subió) es deliberadamente un try/except SEPARADO,
    DESPUÉS de que el PUT ya se confirmó: un fallo ahí (p. ej. el archivo
    local desaparece entre el upload y el `stat()` de la firma) NUNCA
    puede convertir un upload remoto exitoso en `False` — `_signatures`
    es una optimización de ancho de banda para `mirror_up()`, no la
    fuente de verdad; perder una entrada en el peor caso cuesta una
    resubida redundante del mismo contenido en el próximo ciclo de
    `mirror_up()`, nunca pérdida de datos (ver su propio docstring)."""
    if not write_enabled():
        return False
    path = Path(local_path).resolve()
    for name, local in _persist_dirs():
        try:
            rel = path.relative_to(local.resolve()).as_posix()
        except ValueError:
            continue
        key = f"{_key(name)}/{rel}"
        try:
            client = _get_client()
            client.upload_file(str(path), config.SPACES_BUCKET, key)
        except Exception as exc:
            log.warning("remote_store.push_path: fallo subiendo '%s': %s", path, exc)
            return False
        # El PUT remoto YA está establecido en este punto -- lo que sigue
        # es bookkeeping local best-effort, nunca debe poder revertir ese
        # éxito (ver docstring de arriba).
        try:
            _signatures[key] = _sig(path)
        except Exception as exc:  # noqa: BLE001 — best-effort, nunca enmascara el éxito remoto ya confirmado
            log.warning(
                "remote_store.push_path: PUT remoto de '%s' confirmado, pero no se pudo "
                "actualizar la firma local de mirror_up (%s) -- el próximo mirror_up podría "
                "resubirlo de forma redundante; sin pérdida de datos.", path, exc,
            )
        return True
    return False


# ── persistencia explícita por evento ─────────────────────────────────────────────────────────────────────────────
DURABLE_REMOTE, DURABLE_LOCAL = "remote", "local"
_pending_push: dict[str, Path] = {}          # PUT fallidos de write_through: se reintentan en el SIGUIENTE evento de escritura (no por reloj)
_PENDING_MAX = 500                           # tope de la lista (memoria acotada si Spaces está caído mucho tiempo)


def _is_persisted_path(path: Path) -> bool:
    for _name, local in _persist_dirs():
        try:
            path.resolve().relative_to(local.resolve())
            return True
        except ValueError:
            continue
    return False


def retry_pending(limit: int = 20) -> int:
    """Reintenta (como máximo ``limit``) los PUT que fallaron en un ``write_through`` anterior, de CUALQUIER archivo pendiente. Dirigido por eventos: lo invoca el
    propio ``write_through``. Se detiene en el PRIMER fallo (si Spaces está caído no se multiplica la latencia del evento: como mucho 1 reintento + el PUT propio)."""
    done = 0
    for key, path in list(_pending_push.items())[:limit]:
        if not path.is_file():
            _pending_push.pop(key, None)
            continue
        if not push_path(path):
            break
        _pending_push.pop(key, None)
        done += 1
    return done


def write_through(path: str | Path, data: str | bytes, *, encoding: str = "utf-8") -> str:
    """Escritura ATÓMICA + persistencia EXPLÍCITA al completarse el evento (sin depender del ciclo de ``mirror_up`` ni del flush al apagar).

    1. escribe a un temporal único junto al destino y hace ``os.replace`` (nunca queda un archivo a medias);
    2. si la persistencia remota está autorizada (``DANWOR_PERSISTENCE=remote`` + credenciales) y la ruta cae bajo un directorio persistido, ``push_path``;
    3. devuelve la durabilidad REALMENTE lograda: ``"remote"`` (PUT confirmado) o ``"local"`` (persistencia deshabilitada, ruta no persistida o PUT fallido).
    Un PUT fallido NO es un error del productor (el archivo local es válido): se registra, queda pendiente y se reintenta en el siguiente ``write_through``.
    Los errores de E/S locales sí se propagan (el llamador decide si son best-effort).

    CONTRATO DE FALLO REMOTO (best-effort, explícito):
      · ``"remote"`` es la ÚNICA señal de persistencia confirmada; ``"local"`` significa «no confirmada» (deshabilitada, ruta no persistida o PUT fallido) y nunca debe
        presentarse como éxito remoto.
      · el reintento es en memoria y por eventos: el siguiente ``write_through`` reintenta TODOS los pendientes (tope ``limit``), no solo el mismo archivo.
      · si el proceso termina antes del reintento no hay garantía propia: el archivo queda sin firma en ``_signatures``, por lo que el respaldo vigente (``mirror_up``,
        incluido el flush al apagar) lo sube mientras ese mecanismo exista; ante un crash sin flush se pierde.
      · el PUT es incondicional (last-writer-wins, sin ETag/If-Match): no detecta que otra instancia/proceso haya escrito una versión más nueva de la MISMA clave.
        Los productores migrados escriben claves nuevas y únicas (``feedback_log``, ``case_<ts>_<id>``); solo ``case_library.update_feedback`` reescribe su propio caso."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        if isinstance(data, bytes):
            tmp.write_bytes(data)
        else:
            tmp.write_text(data, encoding=encoding)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    if not write_enabled() or not _is_persisted_path(path):
        return DURABLE_LOCAL
    try:
        retry_pending()
    except Exception as exc:  # noqa: BLE001 — un reintento viejo nunca rompe el evento actual
        log.warning("remote_store.write_through: reintento de pendientes falló: %s", exc)
    if push_path(path):
        _pending_push.pop(str(path), None)
        return DURABLE_REMOTE
    _pending_push[str(path)] = path
    while len(_pending_push) > _PENDING_MAX:                     # el archivo sigue en disco (y bajo el respaldo de mirror_up): solo se acota la memoria
        _pending_push.pop(next(iter(_pending_push)), None)
    log.warning("remote_store.write_through: escrito localmente pero NO confirmado en Spaces (%s); queda pendiente y se reintenta en el próximo evento de escritura", path)
    return DURABLE_LOCAL


_held: dict[str, int] = {}                  # nombre de directorio persistido → regeneraciones en curso (mirror_up lo omite mientras > 0)
_withheld: dict[str, tuple] = {}            # key → firma local retenida tras una regeneración fallida/incompleta (mirror_up no la sube mientras el archivo no cambie)
_hold_lock = threading.Lock()
_hold_cv = threading.Condition(_hold_lock)  # protege _held e _inflight; notifica cuando termina un ciclo de mirror_up
_inflight: dict[str, int] = {}              # nombre de directorio persistido → ciclos de mirror_up publicándolo ahora mismo
HOLD_WAIT_TIMEOUT = 300.0                   # tope (s) que ``hold_publication`` espera a un ciclo en vuelo; un barrido completo tras un arranque tardó ~90 s


class PublicationHold:
    """Protege los archivos de directorios persistidos (``names``) del ``mirror_up`` periódico mientras un productor los REGENERA con persistencia explícita.
    Con ``with hold_publication(names) as hold:`` el directorio no se publica durante la regeneración; si ésta termina FALLIDA o INCOMPLETA (``hold.fail()`` o una
    excepción), todo lo que había sin confirmar en Spaces queda RETENIDO (el ciclo no lo sube mientras ese archivo no cambie). Si termina bien y ``persist_changed`` lo
    confirmó completo, la retención se levanta. Todo en memoria: un reinicio la limpia (el restore deja Spaces como única verdad) y la siguiente regeneración completa
    también. Solo afecta a ``names``: los demás directorios y productores siguen sincronizando."""

    def __init__(self, names, wait_timeout: float | None = None):
        self.names = tuple(names)
        self.failed = False
        self.wait_timeout = HOLD_WAIT_TIMEOUT if wait_timeout is None else wait_timeout
        self.exclusive = True                  # False si venció la espera y todavía había un ciclo de mirror_up publicando alguno de ``names``

    def fail(self) -> None:
        self.failed = True

    def __enter__(self):
        """Adquiere el hold y ESPERA (acotado) a que terminen los ciclos de ``mirror_up`` ya en vuelo sobre ``names``: los nuevos ya lo ven retenido y los en vuelo se detienen en su siguiente
        archivo, así que la espera es corta. Si vence ``wait_timeout`` no se bloquea para siempre: ``exclusive`` queda en False y el productor decide (APIS_ONLY aborta; los demás siguen como antes)."""
        with _hold_cv:
            for n in self.names:
                _held[n] = _held.get(n, 0) + 1
            try:
                deadline = time.monotonic() + self.wait_timeout
                while any(_inflight.get(n) for n in self.names):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.exclusive = False
                        log.warning("remote_store.hold_publication: venció la espera (%.0fs) a un ciclo de mirror_up en vuelo sobre %s; el hold no es exclusivo.", self.wait_timeout, list(self.names))
                        break
                    _hold_cv.wait(remaining)
            except BaseException:                                         # una interrupción durante la espera no debe dejar el directorio retenido para siempre
                for n in self.names:
                    _held[n] = max(0, _held.get(n, 0) - 1)
                    if not _held[n]:
                        _held.pop(n, None)
                raise
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.failed = True
        with _hold_lock:
            for n in self.names:
                _held[n] = max(0, _held.get(n, 0) - 1)
                if not _held[n]:
                    _held.pop(n, None)
            if self.failed and write_enabled():
                _withhold_unsynced(self.names)
        return False


def hold_publication(names, wait_timeout: float | None = None) -> PublicationHold:
    return PublicationHold(names, wait_timeout)


def _withhold_unsynced(names) -> int:
    """Retiene (con su firma actual) todo archivo de ``names`` que ``mirror_up`` subiría ahora: lo que no coincide con lo último confirmado en Spaces."""
    wanted, n = set(names), 0
    for name, local in _persist_dirs():
        if name not in wanted or not local.is_dir():
            continue
        root_key = _key(name)
        for path in local.rglob("*"):
            if not path.is_file():
                continue
            key = f"{root_key}/{path.relative_to(local).as_posix()}"
            try:
                sig = _sig(path)
            except OSError:
                continue
            if _signatures.get(key) != sig:
                _withheld[key] = sig
                n += 1
    if n:
        log.warning("remote_store: %d archivo(s) de %s retenido(s) del ciclo periódico (regeneración fallida o incompleta); se liberan con la próxima regeneración completa o al reiniciar.", n, sorted(wanted))
    return n


def _release_withheld(names) -> None:
    prefixes = tuple(_key(n) + "/" for n in names)
    for k in [k for k in _withheld if k.startswith(prefixes)]:
        _withheld.pop(k, None)


_digests: dict[str, str] = {}                # key → md5 del contenido que ESTE proceso confirmó en Spaces (evita re-subir lo que un generador solo «tocó» con el mismo contenido)


def _md5(path: Path) -> str:
    import hashlib
    return hashlib.md5(path.read_bytes()).hexdigest()  # noqa: S324 — identidad de contenido, no criptografía


def persist_changed(names, *, last=(), workers: int = 8) -> dict:
    """Evento de persistencia ACOTADO: sube a Spaces, al terminar un productor, solo lo que cambió bajo los directorios persistidos ``names`` (p. ej. el catálogo
    legacy al completar «Generar catálogo»), sin esperar al ciclo de ``mirror_up``. «Cambió» = la firma local difiere de la última confirmada en Spaces
    (``_signatures``: la MISMA vara de ``mirror_up``), así que un archivo que un evento anterior dejó sin confirmar se incluye; si solo cambió la firma (mtime) pero el
    contenido es idéntico al que este proceso ya confirmó (``_digests``), no se vuelve a subir. No barre directorios ajenos a ``names``.

    Mismos resguardos que ``mirror_up``: nada si la persistencia remota no está autorizada, ni mientras el restore esté en curso/incompleto, ni sobre un key
    ``_unreconciled``. No borra objetos remotos (como ``mirror_up``).

    NO publica resultados parciales como completos: los archivos cuyo nombre figura en ``last`` (marcadores de completitud, p. ej. ``.catalog_manifest.json``) se suben
    SOLO al final y SOLO si todo lo demás quedó confirmado; ante el primer fallo no se intentan más PUT (sin multiplicar latencia con Spaces caído), los pendientes
    quedan registrados para el siguiente evento de escritura y el marcador NO se publica.

    Devuelve ``{"status", "changed", "uploaded", "failed", "skipped_unreconciled", "marker_withheld", "reason"}`` con ``status`` en:
    ``disabled`` (no autorizado) · ``blocked`` (restore) · ``remote`` (todo lo cambiado quedó confirmado) · ``partial`` (algo no se confirmó) · ``failed`` (sin cliente)."""
    result = {"status": "remote", "changed": 0, "uploaded": 0, "failed": [], "skipped_unreconciled": 0, "marker_withheld": False, "reason": None}
    if not write_enabled():
        result["status"] = "disabled"
        return result
    blocked = sync_blocked_reason()
    if blocked is not None:
        result.update(status="blocked", reason=blocked)
        log.warning("remote_store.persist_changed: BLOQUEADO — %s; no se sube nada.", blocked)
        return result
    try:
        _get_client()
    except Exception as exc:  # noqa: BLE001
        result.update(status="failed", reason=f"no se pudo inicializar el cliente: {exc}")
        log.warning("remote_store.persist_changed: no se pudo inicializar el cliente: %s", exc)
        return result

    wanted, markers = set(names), set(last)
    body: list[Path] = []
    tail: list[Path] = []
    keys: dict[Path, str] = {}
    for name, local in _persist_dirs():
        if name not in wanted or not local.is_dir():
            continue
        root_key = _key(name)
        for path in local.rglob("*"):
            if not path.is_file():
                continue
            key = f"{root_key}/{path.relative_to(local).as_posix()}"
            if key in _unreconciled:
                result["skipped_unreconciled"] += 1
                continue
            try:
                sig = _sig(path)
                if _signatures.get(key) == sig:
                    continue
                if key in _digests and _digests[key] == _md5(path):          # mismo contenido que lo ya confirmado: solo cambió la firma
                    _signatures[key] = sig
                    continue
            except OSError:
                continue
            keys[path] = key
            (tail if path.name in markers else body).append(path)
    result["changed"] = len(body) + len(tail) + result["skipped_unreconciled"]

    abort = threading.Event()

    def _push(path: Path) -> bool:
        if abort.is_set():
            return False
        try:
            digest = _md5(path)
        except OSError:
            digest = None
        if push_path(path):
            _pending_push.pop(str(path), None)
            if digest is not None:
                _digests[keys[path]] = digest
            return True
        abort.set()                                            # primer fallo: no se intentan más PUT
        return False

    if body:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(body)))) as pool:
            outcomes = list(pool.map(_push, body))
        result["uploaded"] += sum(outcomes)
        for path, ok in zip(body, outcomes):
            if not ok:
                result["failed"].append(str(path))
                _pending_push[str(path)] = path
        while len(_pending_push) > _PENDING_MAX:
            _pending_push.pop(next(iter(_pending_push)), None)
    if result["failed"] or result["skipped_unreconciled"]:
        result["marker_withheld"] = bool(tail)
        result["status"] = "partial"
    else:
        for path in tail:                                       # marcadores de completitud: al final y solo con todo lo demás confirmado
            if _push(path):
                result["uploaded"] += 1
            else:
                result["failed"].append(str(path))
                _pending_push[str(path)] = path
                result["status"] = "partial"
    if result["status"] == "partial":
        log.warning("remote_store.persist_changed: %d de %d archivo(s) confirmados; fallidos=%d sin reconciliar=%d marcador retenido=%s",
                    result["uploaded"], result["changed"], len(result["failed"]), result["skipped_unreconciled"], result["marker_withheld"])
    else:
        _release_withheld(wanted)                               # todo lo cambiado quedó confirmado: nada que retener
        if result["uploaded"]:
            log.info("remote_store.persist_changed: %d archivo(s) confirmados en Spaces", result["uploaded"])
    return result


def read_object_bytes(local_path: str | Path) -> bytes | None:
    """Lee del bucket los bytes EXACTOS del objeto correspondiente a `local_path`
    (MISMO mapeo local→key que `push_path`). Es el primitivo de verificación
    por contenido que la máquina de estados del refresh de catálogos CCHH usa
    para desambiguar un `push_path()` que devolvió `False`.

    Contrato de retorno (tres resultados, deliberadamente distintos):
      - `bytes`         → el objeto EXISTE; se devuelven sus bytes tal cual (el
                          llamador compara por igualdad de bytes EXACTA, nunca
                          por ETag/tamaño — esos no son identidad de contenido).
      - `None`          → el objeto NO existe (404 / NoSuchKey): ausencia
                          confirmada, no un error.
      - raise `RemoteReadError` → NO se pudo determinar (persistencia
                          deshabilitada, path fuera de los directorios
                          persistidos, cliente no inicializable, o un GET que
                          falló por algo que no es "no existe"). Irresoluble.

    Nunca escribe en disco ni toca el estado local (a diferencia de `restore()`);
    es una lectura pura para verificación."""
    if not read_enabled():
        raise RemoteReadError("remote_store deshabilitado: no se puede leer el objeto remoto")
    path = Path(local_path).resolve()
    key: str | None = None
    for name, local in _persist_dirs():
        try:
            rel = path.relative_to(local.resolve()).as_posix()
        except ValueError:
            continue
        key = f"{_key(name)}/{rel}"
        break
    if key is None:
        raise RemoteReadError(f"'{path}' no cae bajo ningún directorio persistido")
    try:
        client = _get_client()
    except Exception as exc:  # noqa: BLE001 — cliente no inicializable = irresoluble
        raise RemoteReadError(f"no se pudo inicializar el cliente S3: {exc}") from exc
    try:
        resp = client.get_object(Bucket=config.SPACES_BUCKET, Key=key)
        return resp["Body"].read()
    except Exception as exc:  # noqa: BLE001
        # "No existe" es un resultado (None), no un error. Cualquier otra cosa
        # es irresoluble. boto3/botocore no comparten una jerarquía común acá,
        # así que se inspecciona el shape del error de forma defensiva.
        code = ""
        resp_meta = getattr(exc, "response", None)
        if isinstance(resp_meta, dict):
            code = str(resp_meta.get("Error", {}).get("Code", ""))
        status = ""
        if isinstance(resp_meta, dict):
            status = str(resp_meta.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
        if code in {"NoSuchKey", "404", "NotFound"} or status == "404" or exc.__class__.__name__ == "NoSuchKey":
            return None
        raise RemoteReadError(f"GET remoto de '{key}' falló de forma irresoluble: {exc}") from exc


def delete_path(local_path: str | Path) -> bool:
    """Borra en el bucket el objeto correspondiente a un archivo local (mismo mapeo
    que push_path). Pensado para reflejar un purge local en el bucket, así el
    borrado es consistente de punta a punta. Best-effort; no-op si está deshabilitado.
    Funciona aunque el archivo local ya no exista (las rutas son operación pura). Requiere capacidad de ESCRITURA."""
    if not write_enabled():
        return False
    path = Path(local_path).resolve()
    for name, local in _persist_dirs():
        try:
            rel = path.relative_to(local.resolve()).as_posix()
        except ValueError:
            continue
        try:
            client = _get_client()
            key = f"{_key(name)}/{rel}"
            client.delete_object(Bucket=config.SPACES_BUCKET, Key=key)
            _signatures.pop(key, None)
            return True
        except Exception as exc:
            log.warning("remote_store.delete_path: fallo borrando '%s': %s", path, exc)
            return False
    return False


# ── namespace EXCLUSIVO de respaldos de migracion (escrituras DIRIGIDAS, fuera de PERSIST_PATHS) ─────────────────────────────────────────────────────────
# ``migration-backups/<migracion>/<run_id>/...``: NO es un directorio persistido, asi que ``restore``/``mirror_up``/``push_path``/``delete_path`` jamas lo tocan (todos parten de
# ``_persist_dirs()``) y nada aqui borra: el respaldo solo se ESCRIBE (una vez por clave, jamas se sobrescribe) y se LEE. Lo usa ``webapp/services/durable_backup``.
BACKUP_NAMESPACE = "migration-backups"


class RemoteBackupExists(Exception):
    """La clave del respaldo YA existe: un respaldo (sellado o no) jamas se sobrescribe."""


def backup_namespace_collision() -> str | None:
    """None si el namespace de respaldos NO colisiona con ningun directorio persistido (``PERSIST_PATHS``); si no, la causa. Se exige antes de escribir un respaldo."""
    for name in config.PERSIST_PATHS:
        n = str(name).strip("/")
        if n == BACKUP_NAMESPACE or n.startswith(BACKUP_NAMESPACE + "/") or BACKUP_NAMESPACE.startswith(n + "/"):
            return f"'{name}' en SPACES_PERSIST_PATHS colisiona con el namespace {BACKUP_NAMESPACE}/ (restore/mirror_up lo sincronizarian)"
    return None


def _backup_key(rel: str) -> str:
    rel = str(rel).replace("\\", "/")
    if not rel.startswith(BACKUP_NAMESPACE + "/") or ".." in rel.split("/") or rel.endswith("/") or "//" in rel:
        raise RemoteReadError(f"clave de respaldo fuera del namespace {BACKUP_NAMESPACE}/: '{rel}'")
    return _key(rel)


def put_backup_object(rel: str, data: bytes) -> None:
    """PUT dirigido de UN objeto del namespace de respaldos, SIN sobrescribir (``RemoteBackupExists`` si la clave ya existe). Requiere capacidad de ESCRITURA y que el namespace no
    colisione con ``PERSIST_PATHS``; cualquier otra falla lanza (el llamador trata el respaldo como NO garantizado). Nota: check-then-PUT (Spaces no ofrece PUT condicional fiable);
    la unicidad del ``run_id`` acota la carrera."""
    if not write_enabled():
        raise RemoteReadError("escritura remota no autorizada (DANWOR_PERSISTENCE != remote)")
    collision = backup_namespace_collision()
    if collision:
        raise RemoteReadError(collision)
    key = _backup_key(rel)
    client = _get_client()
    if get_backup_object(rel) is not None:
        raise RemoteBackupExists(rel)
    client.put_object(Bucket=config.SPACES_BUCKET, Key=key, Body=bytes(data))


def get_backup_object(rel: str) -> bytes | None:
    """Bytes EXACTOS de un objeto del namespace de respaldos, ``None`` si no existe; ``RemoteReadError`` si no se puede determinar."""
    if not read_enabled():
        raise RemoteReadError("lectura remota no autorizada")
    key = _backup_key(rel)
    try:
        client = _get_client()
        return client.get_object(Bucket=config.SPACES_BUCKET, Key=key)["Body"].read()
    except RemoteReadError:
        raise
    except Exception as exc:  # noqa: BLE001
        code = str((getattr(exc, "response", None) or {}).get("Error", {}).get("Code", ""))
        status = (getattr(exc, "response", None) or {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in ("NoSuchKey", "404", "NotFound") or status == 404:
            return None
        raise RemoteReadError(f"GET fallo: {type(exc).__name__}") from exc


def list_backup_keys(prefix_rel: str) -> list[str]:
    """Claves (sin el prefijo de ambiente) bajo ``prefix_rel`` (dentro del namespace de respaldos). ``RemoteReadError`` si no se puede listar."""
    if not read_enabled():
        raise RemoteReadError("lectura remota no autorizada")
    base = _backup_key(prefix_rel.rstrip("/") + "/x")[:-1]
    strip = _key("")
    out: list[str] = []
    try:
        client = _get_client()
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=config.SPACES_BUCKET, Prefix=base):
            out.extend(o["Key"][len(strip):] for o in page.get("Contents", []) or [])
    except Exception as exc:  # noqa: BLE001
        raise RemoteReadError(f"listado fallo: {type(exc).__name__}") from exc
    return sorted(out)


async def sync_loop() -> None:
    """Loop periódico de mirror_up para correr como background task del app.
    Cada mirror corre en un thread para no bloquear el event loop."""
    interval = max(30, int(config.SPACES_SYNC_INTERVAL))
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(mirror_up)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("remote_store.sync_loop: iteración falló: %s", exc)


def _cli(argv: list[str] | None = None) -> int:
    """Restauración EXPLÍCITA desde el bucket (``python -m core.remote_store restore [--overwrite]``). Requiere ``DANWOR_PERSISTENCE=read-only`` o ``remote``
    (más credenciales). Por defecto NO sobrescribe archivos locales existentes; ``--overwrite`` es el reemplazo controlado. Solo lee del bucket."""
    import argparse
    parser = argparse.ArgumentParser(prog="python -m core.remote_store", description=_cli.__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("restore", help="baja lo persistido al FS local (sin sobrescribir salvo --overwrite)")
    r.add_argument("--overwrite", action="store_true", help="reemplaza los archivos locales existentes por la copia remota")
    sub.add_parser("status", help="muestra el modo de persistencia efectivo")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(describe_mode())
    if args.command == "status":
        return 0
    if not read_enabled():
        print("Lectura remota NO autorizada: definí DANWOR_PERSISTENCE=read-only (o remote) y las credenciales SPACES_*. No se hizo nada.")
        return 2
    n = restore(overwrite=args.overwrite)
    print(f"restore: {n} archivo(s) bajado(s) · reporte: {last_restore_report}")
    return 0 if restore_status() == "completed" and not _restore_degraded else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
