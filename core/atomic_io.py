"""atomic_io.py — escritura atómica de archivos (H6 de la fase 0 de republicación del Catálogo).

`write_text` / `write_bytes` directos dejan un archivo TRUNCADO si el proceso muere a mitad de la escritura. Acá se escribe a un temporal en el MISMO directorio
(mismo dispositivo ⇒ `os.replace` atómico), se hace `fsync` y recién entonces se reemplaza el destino: un lector ve el contenido viejo completo o el nuevo
completo, nunca uno intermedio. Módulo sin dependencias del repo (lo importan tanto el generador como el exportador, que a veces corren como scripts).

El temporal lleva el prefijo ``TMP_PREFIX``: la recuperación (``tools/catalog/publication_txn.recover_pending``) borra los huérfanos que deje una caída.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

TMP_PREFIX = ".tmp-atomic-"


def _fsync_dir(directory: Path) -> None:
    if os.name == "nt":                          # Windows no permite abrir un directorio para fsync
        return
    try:
        fd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def write_bytes(path: str | Path, data: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=TMP_PREFIX, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    _fsync_dir(target.parent)


def write_text(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    """Mismo contenido que ``Path.write_text`` (incluida la traducción de saltos de línea del sistema), pero atómico."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=TMP_PREFIX, suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    _fsync_dir(target.parent)


def remove_orphan_temporaries(directory: str | Path) -> int:
    """Borra los temporales ``.tmp-atomic-*`` que dejó una caída bajo ``directory`` (recursivo). Devuelve cuántos."""
    removed = 0
    root = Path(directory)
    if not root.is_dir():
        return 0
    for p in root.rglob(TMP_PREFIX + "*"):
        if p.is_file():
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed
