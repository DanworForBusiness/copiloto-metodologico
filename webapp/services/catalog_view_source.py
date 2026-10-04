"""
catalog_view_source.py — fuente SQL de las vistas de Datasphere para "Abrir SQL" (Catálogo → detalle de flujo).

Contrato:
  * la fuente sale del JSON REAL del pool de crudos (``definitions.<nombre técnico>["@DataWarehouse.sqlEditor.query"]``), nunca del Markdown
    generado; se muestra EXACTA (sin ejecutar, reescribir ni inferir);
  * el tipo de vista es el del clasificador ÚNICO (`tools/catalog/dsp_view_type.py`, el mismo de exportador y Assessment): solo `SQL` + una
    consulta no vacía habilita "Abrir SQL". GRAPHICAL, UNKNOWN o SQL sin consulta → ninguna acción (no hay botón roto);
  * se resuelve por IDENTIDAD TÉCNICA (cliente + nombre técnico del artefacto), con la MISMA pertenencia que el Assessment: carpeta-tipo `views`
    cuando el pool la informa; en pools planos solo el marcador del exportador, excluyendo tablas remotas/locales y modelos analíticos;
  * el índice en memoria solo guarda (nombre → archivo) por huella del pool; el texto SQL NO se copia ni se persiste: se relee al abrirlo.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from tools.catalog import dsp_view_type as vt
from webapp.services import assessment_service as asvc

log = logging.getLogger("danwor.catalog_view_source")

_NON_VIEW_MARKERS = ("@DataWarehouse.remote.entity", "@DataWarehouse.remote.connection")
_LOCAL_TABLE = "DWC_LOCAL_TABLE"


@dataclass(frozen=True)
class ViewSql:
    name: str
    source_file: str                      # relativo a la raíz del pool
    view_type: str
    view_type_basis: str
    sql: str


_lock = threading.RLock()
_memo: dict[tuple[str, str], tuple[tuple, dict[str, str]]] = {}


def _family_of_dir(dir_name: str) -> str | None:
    """Familia normalizada de una carpeta-tipo del export CLI (contrato de carpetas del legado), o None en pools planos."""
    from tools.catalog import generate_catalog as gc
    kind = gc._catalog_dir_kind(dir_name)
    return kind if kind in gc.CATALOG_ARTIFACT_TYPE_DIRS else None


def _is_non_view_definition(defn: dict) -> bool:
    """Exclusiones del fallback de pools planos: tabla remota, tabla local y modelo analítico (cubo)."""
    if any(k in defn for k in _NON_VIEW_MARKERS):
        return True
    if str(defn.get("@DataWarehouse.repository.technicalType") or "") == _LOCAL_TABLE:
        return True
    pattern = defn.get("@ObjectModel.modelingPattern")
    pattern = pattern.get("#") if isinstance(pattern, dict) else pattern
    return str(pattern or "").upper() == "ANALYTICAL_CUBE"


def _load(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def sql_view_of(data: dict, name: str, family: str | None) -> tuple[str, str, str] | None:
    """(tipo, base, sql) si ``name`` es una vista SQL con consulta en ``data``; si no, None. Misma pertenencia que el Assessment."""
    definitions = data.get("definitions")
    defn = definitions.get(name) if isinstance(definitions, dict) else None
    if not isinstance(defn, dict):
        return None
    if family is not None:                                    # el pool informa la familia: solo `views`
        if family != "views":
            return None
    elif _is_non_view_definition(defn):                       # pool plano: solo marcador estructural, sin tablas ni cubos
        return None
    view_type, basis = vt.view_type_of_export(data, name)
    if view_type != vt.SQL:
        return None
    query = vt.sql_query_of(data, name)
    return (view_type, basis, query) if query else None


def _index(client: str, domain: str) -> tuple[Path, dict[str, str]]:
    found = asvc.pool_json_files(client, domain)
    if found is None:
        return Path("."), {}
    root, files = found
    fp = tuple(sorted((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files))
    key = (str(root), client.lower())
    with _lock:
        cached = _memo.get(key)
        if cached is not None and cached[0] == fp:
            return root, cached[1]
    index: dict[str, str] = {}
    for path in sorted(files):
        data = _load(path)
        if data is None:
            continue
        rel = path.resolve().relative_to(root.resolve()).as_posix()
        family = _family_of_dir(path.parent.name) if path.parent != root else None
        for name in (data.get("definitions") or {}):
            if name not in index and sql_view_of(data, name, family) is not None:
                index[name] = rel
    with _lock:
        _memo[key] = (fp, index)
    return root, index


def sql_view_names(client: str, domain: str) -> frozenset[str]:
    """Nombres técnicos de las vistas SQL CON consulta del cliente (para decidir qué artefactos muestran "Abrir SQL"). Sin texto SQL."""
    try:
        return frozenset(_index(client, domain)[1])
    except Exception:                                         # noqa: BLE001 — jamás rompe el detalle del flujo
        log.exception("catalog_view_source: no se pudo indexar el pool de %s", client)
        return frozenset()


def read_view_sql(client: str, domain: str, name: str) -> ViewSql | None:
    """La fuente SQL EXACTA de ``name`` leída del JSON del pool; None si no es una vista SQL con consulta (o el pool no está)."""
    if not name or "/" in name or "\\" in name:
        return None
    try:
        root, index = _index(client, domain)
    except Exception:                                         # noqa: BLE001
        log.exception("catalog_view_source: no se pudo indexar el pool de %s", client)
        return None
    rel = index.get(name)
    if rel is None:
        return None
    path = (root / rel)
    data = _load(path)
    family = _family_of_dir(path.parent.name) if path.parent != root else None
    hit = sql_view_of(data, name, family) if data is not None else None
    if hit is None:
        return None
    view_type, basis, sql = hit
    return ViewSql(name, rel, view_type, basis, sql)
