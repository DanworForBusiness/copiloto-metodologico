"""publication_remote.py — confirmación DURABLE de una publicación en Spaces, con verificación del estado remoto y compensación (P1).

``remote_store.persist_changed`` informa si cada PUT se confirmó, pero una respuesta perdida después de una escritura efectiva se ve igual que un fallo, y un fallo a mitad deja Spaces con
una mezcla. Este módulo agrega, SOLO para la publicación segura y sin otro sistema de persistencia, lo que falta para poder decir «confirmado» o «deshecho» con verificación:

  * ``preflight``       antes de tocar nada: Spaces legible, restore completo y sin locales sin reconciliar bajo el cliente, e INVENTARIO remoto del cliente (un listado que falla es un
                        resultado explícito, nunca «no hay huérfanos»). Devuelve los huérfanos remotos que un restore resucitaría.
  * ``confirm``         borra lo retirado, sube el cuerpo y el marcador de activación AL FINAL, y verifica por BYTES EXACTOS. Si algo no se confirma, RECONCILIA leyendo el remoto antes de
                        declarar fallo (una escritura efectiva con respuesta perdida cuenta como confirmada).
  * ``persistence_gap`` (B2) ¿la persistencia EFECTIVA cubre lo que la publicación va a escribir (Catálogo y almacén de assessments)? Se valida la configuración real (modo, credenciales,
                        rutas persistidas y el directorio al que realmente escribe cada almacén), no la presencia de un texto.
  * ``converge``        deja el remoto IDÉNTICO al local en un conjunto de rutas (sube lo distinto, borra lo sobrante) y lo verifica; es la base de ``confirm`` (local = versión nueva) y de la
                        compensación (local = versión previa restaurada).

Resultados: ``CONFIRMED`` · ``FAILED`` (se pudo leer el remoto y se verificó que NO está la versión → se puede deshacer) · ``UNCERTAIN`` (no se pudo leer/verificar: ni se confirma ni se
deshace; la compuerta de lectores permanece cerrada y la recuperación lo reintenta).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

CONFIRMED, FAILED, UNCERTAIN = "CONFIRMED", "FAILED", "UNCERTAIN"


@dataclass
class RemoteOutcome:
    status: str
    reconciled: bool = False                   # hubo que reconciliar (respuesta perdida / subida parcial) para llegar a CONFIRMED
    detail: str = ""
    problems: list[str] = field(default_factory=list)


@dataclass
class Preflight:
    ok: bool
    code: str = ""                            # REMOTE_NOT_READY | REMOTE_UNREADABLE | REMOTE_LISTING_FAILED | REMOTE_NOT_RECONCILED
    detail: str = ""
    listing: dict[str, dict] = field(default_factory=dict)       # inventario de catalog_local (ruta relativa → metadatos); vacío si no se pidió remoto


def preflight(rs, persisted_names: Iterable[str], client_prefixes: Iterable[str], *, probe: Path | None = None) -> Preflight:
    """Antes de aplicar nada. ``client_prefixes``: claves ``<dir persistido>/<prefijo>/`` del cliente (pool, linaje, Catálogo, gobernados)."""
    reason = rs.sync_blocked_reason()
    if reason:
        return Preflight(False, "REMOTE_NOT_READY", reason)
    listing: dict[str, dict] = {}
    try:
        if probe is not None:
            rs.persisted_object_exists(probe)                       # lectura de prueba: HEAD (ausencia confirmada es un resultado válido)
        for name in persisted_names:
            for rel, meta in rs.list_persisted_objects(name).items():
                listing[f"{name}/{rel}"] = meta
    except rs.RemoteReadError as exc:
        return Preflight(False, "REMOTE_LISTING_FAILED", str(exc)[:200])
    unrec = [k for k in client_prefixes_unreconciled(rs, client_prefixes)]
    if unrec:
        return Preflight(False, "REMOTE_NOT_RECONCILED", f"{len(unrec)} archivo(s) locales difieren del remoto y no se reconciliaron")
    return Preflight(True, listing=listing)


def _within(child: Path, parent: Path) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False


def persistence_gap(rs, *, mirror_up: bool, catalog_root: Path, durable_store: "tuple[str, Path | None] | None") -> "tuple[str, str] | None":
    """B2. ``None`` si la persistencia efectiva cubre la publicación; si no, ``(código, detalle)`` y la publicación NO debe empezar.

    ``durable_store``: ``(nombre persistido, carpeta del cliente)`` del almacén que la etapa exige durable (los assessments gobernados), o ``None`` si no hay etapa.
    Reglas (sobre la configuración EFECTIVA de ``remote_store``):
      * modo distinto de ``remote`` con credenciales de Spaces presentes → lo publicado quedaría SOLO en el disco efímero (el despliegue olvidó ``DANWOR_PERSISTENCE=remote``);
      * modo ``remote`` sin escritura habilitada, o sin pedir la confirmación remota (``mirror_up``) → una publicación «exitosa» no estaría confirmada en Spaces;
      * el Catálogo fuera de los directorios persistidos, o el almacén de la etapa inexistente / fuera de su directorio persistido efectivo → no sobrevive a un redeploy.
    Sin credenciales (desarrollo local explícito) el disco es la fuente de verdad y solo se exige que el almacén exista. Sin almacén durable exigido (``None``) no hay nada que cubrir."""
    if durable_store is None:
        return None                                                            # sin etapa que exija un almacén durable (p. ej. el CLI sin assessment): la durabilidad se informa en el resultado, como siempre
    if durable_store[1] is None:
        return "ASSESSMENT_STORE_UNAVAILABLE", "el almacén de assessments no tiene un directorio válido (cae bajo el Catálogo o el pool): no habría dónde guardarlos"
    import config
    mode = rs.persistence_mode()
    if mode != "remote":
        if getattr(config, "SPACES_CONFIGURED", False):
            return "PERSISTENCE_MODE_NOT_REMOTE", f"hay credenciales de Spaces pero DANWOR_PERSISTENCE={mode}: lo publicado quedaría solo en el disco efímero"
        return None
    if not rs.write_enabled():
        return "PERSISTENCE_NOT_WRITABLE", "DANWOR_PERSISTENCE=remote pero la escritura en Spaces no está habilitada (credenciales incompletas)"
    if not mirror_up:
        return "PERSISTENCE_NOT_REQUESTED", "la publicación segura exige confirmar en Spaces (DANWOR_PUBLICATION_MIRROR_UP): sin eso lo publicado quedaría solo en el disco efímero"
    dirs = {name: Path(local).resolve() for name, local in rs._persist_dirs()}
    if not any(_within(catalog_root, base) for base in dirs.values()):
        return "CATALOG_NOT_PERSISTED", "el directorio del Catálogo no figura entre los directorios persistidos (SPACES_PERSIST_PATHS)"
    name, folder = durable_store
    base = dirs.get(name)
    if base is None:
        return "ASSESSMENT_NOT_PERSISTED", f"'{name}' no figura en SPACES_PERSIST_PATHS: los assessments solo quedarían en el disco efímero"
    if not _within(folder, base):
        return "ASSESSMENT_NOT_PERSISTED", f"el almacén de assessments efectivo no está dentro del directorio persistido '{name}'"
    return None


def client_prefixes_unreconciled(rs, prefixes: Iterable[str]) -> list[str]:
    keys = list(getattr(rs, "_unreconciled", ()) or ())
    wanted = tuple(rs._key(p) for p in prefixes)
    return [k for k in keys if k.startswith(wanted)]


def orphans(listing: dict[str, dict], prefixes: Iterable[str], local_keys: Iterable[str]) -> list[str]:
    """Claves remotas bajo los prefijos del cliente que ya NO existen en local: un restore las traería de vuelta (resurrección). Solo se calcula con un local autoritativo."""
    keep, pre = set(local_keys), tuple(prefixes)
    return sorted(k for k in listing if k.startswith(pre) and k not in keep)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reconciled(rs, key: str) -> None:
    """El local y el remoto de ``key`` quedaron verificados idénticos: ya no es un archivo «sin reconciliar» tras un restore que preservó un local distinto."""
    unrec = getattr(rs, "_unreconciled", None)
    if unrec:
        unrec.discard(rs._key(key))


def converge(rs, keyed: dict[str, Path], last: Iterable[str] = ()) -> RemoteOutcome:
    """Deja el remoto IDÉNTICO al local en las rutas de ``keyed`` (clave → ruta): si el archivo local EXISTE, el objeto remoto debe tener sus bytes exactos; si NO existe, el objeto remoto debe
    estar AUSENTE. Lee el remoto ANTES de escribir (una escritura efectiva con respuesta perdida no se repite ni se declara fallo) y verifica DESPUÉS. Las claves de ``last`` (marcadores de
    activación) se procesan al FINAL. ``UNCERTAIN`` si cualquier lectura de verificación es irresoluble; ``FAILED`` si se leyó el remoto y se comprobó que no refleja el local."""
    problems: list[str] = []
    uncertain = False
    wrote = False
    tail = set(last)
    ordered = [*(k for k in sorted(keyed) if k not in tail), *(k for k in sorted(keyed) if k in tail)]
    for key in ordered:
        path = keyed[key]
        if path.exists():
            try:
                local = path.read_bytes()
            except OSError:
                problems.append(f"local ilegible {key}")
                return RemoteOutcome(FAILED, wrote, "local ilegible", problems)
            try:
                remote = rs.read_object_bytes(path)
            except rs.RemoteReadError:
                uncertain = True
                problems.append(f"remoto ilegible {key}")
                continue
            if remote is not None and _sha(remote) == _sha(local):
                _reconciled(rs, key)
                continue
            wrote = True
            pushed = rs.push_path(path)
            try:
                after = rs.read_object_bytes(path)
            except rs.RemoteReadError:
                uncertain = True
                problems.append(f"remoto no verificable {key}")
                continue
            if after is None or _sha(after) != _sha(local):
                problems.append(f"no se confirmó {key}" + ("" if pushed else " (PUT rechazado)"))
            else:
                _reconciled(rs, key)
        else:
            try:
                exists = rs.persisted_object_exists(path)
            except rs.RemoteReadError:
                uncertain = True
                problems.append(f"remoto ilegible {key}")
                continue
            if not exists:
                _reconciled(rs, key)
                continue
            wrote = True
            rs.delete_path(path)
            try:
                still = rs.persisted_object_exists(path)
            except rs.RemoteReadError:
                uncertain = True
                problems.append(f"remoto no verificable {key}")
                continue
            if still:
                problems.append(f"no se borró {key}")
            else:
                _reconciled(rs, key)
    if uncertain:
        return RemoteOutcome(UNCERTAIN, wrote, "no se pudo verificar el remoto", problems)
    if problems:
        return RemoteOutcome(FAILED, wrote, "el remoto no refleja la versión", problems)
    return RemoteOutcome(CONFIRMED, wrote)


def confirm(rs, names: Iterable[str], markers: Iterable[str], files: dict[str, Path], removed: dict[str, Path], marker_path: Path) -> RemoteOutcome:
    """Confirma la versión NUEVA: borra lo retirado, sube el cuerpo y el marcador al final (``persist_changed``) y verifica. Cualquier discrepancia se RECONCILIA contra el remoto real."""
    markers = tuple(markers)
    for path in removed.values():
        rs.delete_path(path)
    try:
        out = rs.persist_changed(tuple(names), last=markers)
        status = str(out.get("status"))
    except Exception:                                  # noqa: BLE001 — una excepción en la subida no decide nada: se verifica el remoto
        status = "failed"
    fast = RemoteOutcome(CONFIRMED)
    if status == "remote":
        try:
            fast = _verify_marker(rs, marker_path, removed)
        except rs.RemoteReadError:
            fast = RemoteOutcome(UNCERTAIN, detail="no se pudo leer el marcador remoto")
        if fast.status == CONFIRMED:
            return fast
    marks = set(markers)
    result = converge(rs, {**files, **removed}, last=[k for k, p in files.items() if p.name in marks])
    result.reconciled = True
    return result


def _verify_marker(rs, marker_path: Path, removed: dict[str, Path]) -> RemoteOutcome:
    remote = rs.read_object_bytes(marker_path)
    if remote is None or _sha(remote) != _sha(marker_path.read_bytes()):
        return RemoteOutcome(FAILED, detail="el marcador remoto no es el local")
    for path in removed.values():
        if rs.persisted_object_exists(path):
            return RemoteOutcome(FAILED, detail="un objeto retirado sigue en el remoto")
    return RemoteOutcome(CONFIRMED)
