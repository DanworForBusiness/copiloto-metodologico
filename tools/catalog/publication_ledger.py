"""publication_ledger.py — marcador de activación y bitácora mínima de las publicaciones de un cliente (P1).

Un archivo por cliente, ``catalog_local/.publication_state/<cliente>.publication.json``, DENTRO de lo que ya se persiste (``catalog_local``): no hay otro sistema de persistencia.

  * ``active``   la versión vigente: operación, operación PREVIA, raíz/Space, resumen del assessment (qué AM se regeneró / se conservó / quedó incierto / se retiró) y los digests sha256 de
                 TODOS los archivos de la unidad (pool, manifiestos de linaje, Catálogo materializado y assessments gobernados del cliente). Es lo que permite verificar, tras un restore
                 o una caída, que lo que hay en disco es EXACTAMENTE una versión publicada y no una mezcla.
  * ``history``  las últimas ``HISTORY_KEEP`` activaciones (operación, previa, instante, raíz, conteos): la trazabilidad de «versión anterior → versión nueva → ejecución».

Se sube a Spaces el ÚLTIMO (después de todo el cuerpo de la unidad): su presencia remota con el digest esperado es la confirmación durable de la activación. Sin dependencias de la app.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

try:
    from core import atomic_io
except ImportError:                               # pragma: no cover - ruta de script
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core import atomic_io

LEDGER_SCHEMA = "publication-ledger/v1"
STATE_DIR = ".publication_state"
SUFFIX = ".publication.json"
HISTORY_KEEP = 50

L_ABSENT, L_OK, L_UNREADABLE, L_INVALID = "ABSENT", "OK", "UNREADABLE", "INVALID_SCHEMA"


def ledger_path(catalog_root: str | Path, client_dir: str) -> Path:
    return Path(catalog_root) / STATE_DIR / f"{client_dir}{SUFFIX}"


def load(catalog_root: str | Path, client_dir: str) -> tuple[str, dict]:
    """Carga TRI-ESTADO como los manifiestos: un ledger ilegible NUNCA equivale a «sin historia»."""
    path = ledger_path(catalog_root, client_dir)
    if not path.exists():
        return L_ABSENT, {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return L_UNREADABLE, {}
    if not isinstance(data, dict) or data.get("schema") != LEDGER_SCHEMA or not isinstance(data.get("history", []), list) or not isinstance(data.get("active", {}), dict):
        return L_INVALID, {}
    return L_OK, data


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def unit_files(catalog_root: str | Path, csn_cache_root: str | Path, client_dir: str, governed_client_dir: str | Path | None, governed_name: str = "governed-assessments") -> dict[str, Path]:
    """``{clave estable: ruta}`` de los archivos de la unidad de un cliente. La clave es ``<directorio persistido>/<ruta relativa>`` (la misma forma en que Spaces los guarda)."""
    root, pool = Path(catalog_root), Path(csn_cache_root)
    out: dict[str, Path] = {}

    def add(base: Path, prefix: str, folder: Path) -> None:
        if folder.is_dir():
            for p in sorted(folder.rglob("*")):
                if p.is_file() and not p.name.startswith(".tmp-atomic-"):
                    out[f"{prefix}/{p.relative_to(base).as_posix()}"] = p

    if _within(pool, root):                      # un pool fuera de catalog_local no se persiste: no forma parte de la unidad remota
        add(root, "catalog_local", pool / client_dir)
    add(root, "catalog_local", root / ".lineage_runs" / client_dir)
    add(root, "catalog_local", root / client_dir)
    marker = root / ".catalog_manifest.json"
    if marker.is_file():
        out["catalog_local/.catalog_manifest.json"] = marker
    if governed_client_dir is not None and Path(governed_client_dir).is_dir():
        gdir = Path(governed_client_dir)
        for p in sorted(gdir.rglob("*")):
            if p.is_file() and not p.name.startswith(".tmp-"):
                parts = p.relative_to(gdir.parents[1]).as_posix()           # <raíz>/v1/<cliente>/archivo → v1/<cliente>/archivo
                out[f"{governed_name}/{parts}"] = p
    return out


def _within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def digests(files: dict[str, Path]) -> dict[str, str]:
    return {key: sha256_file(path) for key, path in sorted(files.items())}


def verify(ledger: dict, files: dict[str, Path]) -> list[str]:
    """Diferencias entre lo que dice el ledger vigente y el disco: lo que falta, lo que sobra y lo que difiere. Vacío = el disco ES la versión activa."""
    expected = (ledger.get("active") or {}).get("files") or {}
    actual = digests(files)
    problems = [f"falta {k}" for k in sorted(set(expected) - set(actual))]
    problems += [f"sobra {k}" for k in sorted(set(actual) - set(expected))]
    problems += [f"difiere {k}" for k in sorted(set(expected) & set(actual)) if expected[k] != actual[k]]
    return problems


def build(ledger: dict, *, operation_id: str, activated_at: str, client_dir: str, space: str, root_object: str, root_type: str, extra: dict[str, Any],
          file_digests: dict[str, str], counts: dict[str, Any], outcome: str = "ACTIVATED") -> dict:
    """Ledger NUEVO con ``active`` = esta operación (previa = la anterior ``active``) y la bitácora acotada."""
    previous = (ledger.get("active") or {}).get("operation_id")
    active = {"operation_id": operation_id, "previous_operation_id": previous, "activated_at": activated_at, "client_dir": client_dir, "space": space,
              "root_object": root_object, "root_type": root_type, **extra, "files": file_digests}
    entry = {"operation_id": operation_id, "previous_operation_id": previous, "activated_at": activated_at, "space": space, "root_object": root_object,
             "outcome": outcome, **counts}
    history = [*(ledger.get("history") or []), entry][-HISTORY_KEEP:]
    return {"schema": LEDGER_SCHEMA, "client_dir": client_dir, "active": active, "history": history}


def write(catalog_root: str | Path, client_dir: str, ledger: dict) -> Path:
    path = ledger_path(catalog_root, client_dir)
    atomic_io.write_text(path, json.dumps(ledger, indent=1, ensure_ascii=False, sort_keys=True))
    return path

