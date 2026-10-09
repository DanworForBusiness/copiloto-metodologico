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
                    return "reconciled"
                _unreconciled.add(key)                        # el local difiere (o no se puede verificar): se PRESERVA y mirror_up no lo sube
                return "skipped"
            dest.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(bucket, key, str(dest))
            # Sembramos la firma para que el primer mirror_up no re-suba lo bajado.
            _signatures[key] = _sig(dest)
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
        root_key = _key(name)
        for path in local.rglob("*"):
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
            try:
                client.upload_file(str(path), bucket, key)
                _signatures[key] = sig
                uploaded += 1
            except Exception as exc:
                log.warning("remote_store.mirror_up: fallo subiendo '%s': %s", key, exc)
    if uploaded:
        log.info("remote_store.mirror_up: %d archivos sincronizados a Spaces", uploaded)
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
