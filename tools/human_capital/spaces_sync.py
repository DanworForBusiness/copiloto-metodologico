"""spaces_sync — restauración y verificación de datos persistidos de Capital Humano para los CLI de mantenimiento, que corren en un proceso APARTE de la app web.

`profile_repository`, el catálogo gobernado y el manifiesto de importación leen SOLO el disco local. En producción ese disco lo puebla `remote_store.restore()`, que corre únicamente en el arranque de la app web: un CLI independiente no
comparte ese estado y vería carpetas vacías. Estas funciones reutilizan ESE mismo mecanismo (acotado a los directorios que el CLI necesita, sin sobrescribir lo local) y lo verifican contra el inventario remoto
(`list_persisted_objects`). Solo LEEN del bucket (la restauración escribe únicamente copias en el disco local del proceso). Extraído de `fix_contador_sr_profiles` para que los CLI no dupliquen la lógica."""
from __future__ import annotations

import contextlib
from pathlib import Path


@contextlib.contextmanager
def persisting_only(cfg, names: list[str]):
    """Acota SOLO durante la restauración los directorios persistidos del proceso (`restore()` y `list_persisted_objects()` los leen de `config.PERSIST_PATHS`): este CLI no necesita bajar catálogos de casos, reportes ni
    el resto de `human-capital`. Se restaura el valor original al salir, así `push_path` y el resto de la ejecución ven la configuración completa."""
    saved = cfg.PERSIST_PATHS
    cfg.PERSIST_PATHS = list(names)
    try:
        yield
    finally:
        cfg.PERSIST_PATHS = saved


def sync_from_spaces(deps: dict, directory: str) -> dict:
    """Trae al disco local lo persistido en Spaces bajo `directory` con el mecanismo existente (`remote_store.restore`, sin sobrescribir lo local) y lo verifica contra el inventario remoto. Solo lectura del bucket. Devuelve el
    informe (`aplica=False` si no hay lectura remota autorizada: modo `local` o sin credenciales, donde la fuente de verdad es el disco) y `bloqueos`."""
    rs, cfg = deps["remote_store"], deps["config"]
    info: dict = {"aplica": False, "bloqueos": []}
    if not rs.read_enabled():
        return info
    local_dir = Path(cfg.BASE_DIR) / directory

    def local_count() -> int:
        return sum(1 for f in local_dir.rglob("*") if f.is_file() and not f.name.startswith(".tmp-")) if local_dir.exists() else 0

    info.update(aplica=True, locales_antes=local_count())
    with persisting_only(cfg, [directory]):
        try:
            remote = rs.list_persisted_objects(directory)
        except rs.RemoteReadError:
            info["bloqueos"].append("RESTORE_LISTADO_REMOTO_FALLIDO")
            return info
        info["remotos"] = len(remote)
        rs.restore()                                                       # MISMA función que el arranque de la app web; overwrite=False: nunca pisa un archivo local distinto
    report = dict(rs.last_restore_report)
    info.update(descargados=report.get("downloaded", 0), identicos=report.get("reconciled", 0), locales_distintos=report.get("skipped_existing", 0), fallidos=report.get("failed", 0),
                estado=rs.restore_status(), locales_despues=local_count())
    if rs.restore_status() != "completed" or report.get("failed") or report.get("listing_failed") or info["locales_despues"] < info["remotos"]:
        info["bloqueos"].append("RESTORE_INCOMPLETO")
    if report.get("skipped_existing"):
        info["bloqueos"].append("RESTORE_LOCAL_DISTINTO_DEL_REMOTO")
    return info
