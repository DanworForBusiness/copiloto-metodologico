"""Persistencia de artefactos runtime contra DO Spaces (S3-compatible).

App Platform corre sobre un filesystem efímero: sin esto, todo lo que el copiloto
genera (catálogo, casos, feedback, adopción, analítica) se pierde en cada deploy o
reciclado de la plataforma. Este módulo respalda esos directorios en un bucket
S3-compatible y los restaura al arrancar.

Estrategia (bajo acoplamiento, no toca los writers):
  - restore()   → al boot: baja el estado previo desde el bucket al FS local.
  - mirror_up() → periódico + al apagar: sube los archivos nuevos/cambiados.
  - push_path() → helper para write-through inmediato de un archivo puntual (opcional).

Si faltan credenciales (PERSIST_ENABLED=False) TODO es no-op → dev local sin cambios.
boto3 se importa de forma lazy para no volverlo dependencia dura del import.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path

import config

log = logging.getLogger("danwor.remote_store")

_client = None
_client_lock = threading.Lock()
# Firma (tamaño, mtime_ns) del último estado subido/bajado por key remoto, para
# saltear re-subidas de archivos sin cambios dentro de la vida del proceso.
_signatures: dict[str, tuple[int, int]] = {}


def enabled() -> bool:
    return bool(config.PERSIST_ENABLED)


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


def restore() -> int:
    """Baja al FS local todo lo persistido en el bucket. Pensado para el boot,
    cuando el FS efímero arranca vacío. Devuelve la cantidad de archivos bajados."""
    if not enabled():
        log.info("remote_store deshabilitado (sin credenciales Spaces) — no-op")
        return 0
    try:
        client = _get_client()
    except Exception as exc:  # boto3 ausente o config inválida
        log.warning("remote_store.restore: no se pudo inicializar el cliente: %s", exc)
        return 0

    bucket = config.SPACES_BUCKET
    downloaded = 0
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
                    dest = local / sub
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    client.download_file(bucket, key, str(dest))
                    # Sembramos la firma para que el primer mirror_up no re-suba lo
                    # que acabamos de bajar sin cambios.
                    _signatures[key] = _sig(dest)
                    downloaded += 1
        except Exception as exc:
            log.warning("remote_store.restore: fallo bajando '%s': %s", name, exc)
    log.info("remote_store.restore: %d archivos restaurados desde Spaces", downloaded)
    return downloaded


def mirror_up() -> int:
    """Sube al bucket los archivos nuevos o modificados de cada directorio. Salta
    los que no cambiaron respecto de la última sincronización de este proceso."""
    if not enabled():
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
    alguno de los directorios persistidos; si no, se ignora. Devuelve True si subió."""
    if not enabled():
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
            client.upload_file(str(path), config.SPACES_BUCKET, key)
            _signatures[key] = _sig(path)
            return True
        except Exception as exc:
            log.warning("remote_store.push_path: fallo subiendo '%s': %s", path, exc)
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
