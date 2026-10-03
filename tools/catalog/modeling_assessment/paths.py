"""Destino de salida del shadow y guardas de seguridad.

DECISION DEL OWNER (correccion de M0): la salida shadow es LOCAL-ONLY y NO PERSISTIDA, fuera
de TODOS los directorios de `PERSIST_PATHS`. Por eso NO existe un destino por defecto dentro
de `catalog_local`: el contrato del assessment no esta atado a esa carpeta y el destino es
SIEMPRE explicito y configurable (`resolve_output_dir`).

Por que una guarda del lado del escritor "solo si el espejado esta activo" NO alcanza:
`core.remote_store.mirror_up()` sube recursivamente y sin exclusiones todo lo que cuelgue de
los directorios persistidos. Un archivo creado mientras el espejado esta apagado (p.ej. en una
maquina sin credenciales de Spaces) lo subiria despues, sin aviso, otra ejecucion de
`mirror_up()` en un entorno con credenciales. Por eso la guarda principal es INDEPENDIENTE del
estado del espejado: se rechaza cualquier salida dentro de un directorio persistido.

(Antes de M7 se inspeccionaran las convenciones del repo por un directorio local-only ya
ignorado; si no existe uno adecuado, el CLI seguira exigiendo una ruta explicita. No se inventa
una ubicacion persistente por comodidad.)

Defensa en profundidad adicional: el cargador de flujos del catalogo hace
`rglob("flujo_*.md")`; ningun archivo de salida puede empezar con ese prefijo.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

# El cargador de flujos del catalogo hace `rglob("flujo_*.md")` bajo el catalogo: ningun archivo
# de salida del shadow puede empezar con ese prefijo (defensa en profundidad; NO resuelve el
# problema del espejado remoto).
RESERVED_FILENAME_PREFIXES = ("flujo_",)


class OutputDirRequiredError(ValueError):
    """No se indico un destino de salida: no hay destino por defecto (decision del owner)."""


class ShadowOutputPersistenceError(RuntimeError):
    """La salida shadow caeria dentro de un directorio persistido/espejado a Spaces."""


def repo_root() -> Path:
    # <repo>/tools/catalog/modeling_assessment/paths.py
    return Path(__file__).resolve().parents[3]


_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def profile_filename(am_name: str, ext: str = "json") -> str:
    """`<AM>.profile.<ext>`; nunca empieza con un prefijo reservado del catalogo."""
    slug = _SLUG_RE.sub("_", str(am_name)).strip("._") or "am"
    if slug.lower().startswith(RESERVED_FILENAME_PREFIXES):
        slug = f"am-{slug}"
    return f"{slug}.profile.{ext}"


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _default_persisted_dirs() -> list[Path]:
    from . import legacy_bridge          # carga perezosa: `paths` se mantiene liviano
    return legacy_bridge.persisted_dirs()


def persisted_dir_containing(
    out_dir: Path, *, persisted_dirs: Callable[[], list[Path]] | None = None,
) -> Path | None:
    """Directorio persistido que contiene a `out_dir` (o None). INDEPENDIENTE de que el
    espejado este activo ahora: es la deteccion que importa para no dejar archivos que un
    `mirror_up()` posterior pueda subir."""
    for d in (persisted_dirs or _default_persisted_dirs)():
        if _is_within(Path(out_dir), d):
            return d
    return None


def output_dir_is_remote_mirrored(
    out_dir: Path,
    *,
    persist_enabled: Callable[[], bool] | None = None,
    persisted_dirs: Callable[[], list[Path]] | None = None,
) -> bool:
    """Deteccion original de M0 (conservada): dentro de un directorio persistido Y con el
    espejado activo AHORA. Es informativa; la guarda efectiva usa `persisted_dir_containing`."""
    if persist_enabled is None:
        from . import legacy_bridge
        persist_enabled = legacy_bridge.persist_enabled
    if not persist_enabled():
        return False
    return persisted_dir_containing(out_dir, persisted_dirs=persisted_dirs) is not None


def resolve_output_dir(
    explicit: str | Path | None,
    *,
    allow_persisted: bool = False,
    persisted_dirs: Callable[[], list[Path]] | None = None,
) -> Path:
    """Devuelve el destino de salida EXPLICITO, o falla CERRADO.

    * sin destino explicito -> `OutputDirRequiredError` (no hay default);
    * destino dentro de un directorio persistido -> `ShadowOutputPersistenceError`
      (aunque el espejado este apagado), salvo `allow_persisted=True` (excepcion explicita).
    """
    if explicit is None or str(explicit).strip() == "":
        raise OutputDirRequiredError(
            "Indicar una ruta de salida local-only explicita (fuera de los directorios persistidos): "
            "no hay destino por defecto.")
    out = Path(explicit)
    if not allow_persisted:
        hit = persisted_dir_containing(out, persisted_dirs=persisted_dirs)
        if hit is not None:
            raise ShadowOutputPersistenceError(
                f"{out} esta dentro del directorio persistido {hit}: `mirror_up()` lo subiria al bucket "
                "(aunque hoy el espejado este apagado). Elegir un destino fuera de los directorios persistidos.")
    return out
