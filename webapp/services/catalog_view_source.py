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
from webapp.services import catalog_reads

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


@dataclass(frozen=True)
class ArtifactIdentity:
    """Identidad visible de un artefacto en su página de código: nombre de negocio (o el técnico si no hay) + capa y uso semántico canónicos del Catálogo."""
    business_name: str
    layer: str | None
    semantic_use: str | None

    def meta_segments(self, technical_name: str) -> list[str]:
        """``[capa, uso semántico, nombre técnico]`` sin los segmentos no disponibles; el nombre técnico siempre es el último."""
        return [*(s for s in (self.layer, self.semantic_use) if s), technical_name]


# Valores de la metadata del Catálogo que significan "no hay dato" (la propia generación los usa): se omiten, no se muestran como identidad.
_NO_VALUE = {"", "sin definir", "n/a", "none", "null"}


def _meta_value(raw) -> str | None:
    s = str(raw or "").strip()
    return None if s.lower() in _NO_VALUE else s


def _entry_for(name: str, cliente: str, dominio: str, flow: str):
    """La entrada del Catálogo desde la que se abrió el SQL: por identidad estable (`flow`); si falta, la que lista al artefacto para (cliente, dominio)."""
    mod, entries = catalog_reads._catalog_entries()
    if not mod:
        return None
    if flow:
        found = mod.find_flow_by_id(entries, flow)
        if found is not None:
            return found
    wanted = (cliente or "").strip().lower()
    for e in entries or []:
        if str(getattr(e, "cliente", "")).strip().lower() == wanted and name in (getattr(e, "artefactos", None) or [])                 and (not dominio or str(getattr(e, "dominio", "")).strip().lower() == dominio.strip().lower()):
            return e
    return None


def artifact_identity(name: str, cliente: str, dominio: str, flow: str) -> ArtifactIdentity:
    """Nombre de negocio, capa y uso semántico del artefacto, leídos de la MISMA metadata del Catálogo que usa el detalle del flujo
    (`vistas/<nombre>.md` → `auto`: label / capa / uso_semantico). Nada se infiere del nombre técnico ni del tipo de vista; sin dato, el nombre
    técnico hace de título y se omiten capa/uso."""
    from webapp.services.content_service import split_front_matter
    auto: dict = {}
    try:
        entry = _entry_for(name, cliente, dominio, flow)
        path = Path(entry.path).parent / "vistas" / f"{name}.md" if entry is not None else None
        if path is not None and path.is_file():
            meta, _body = split_front_matter(path.read_text(encoding="utf-8", errors="ignore"))
            auto = (meta or {}).get("auto") or {}
    except Exception:                                         # noqa: BLE001 — la identidad es presentación: jamás rompe el visor
        log.exception("catalog_view_source: no se pudo leer la metadata de %s", name)
    return ArtifactIdentity(catalog_reads.catalog_clean_label(auto.get("label"), name), _meta_value(auto.get("capa")), _meta_value(auto.get("uso_semantico")))


_lock = threading.RLock()
_memo: dict[tuple[str, str], tuple[tuple, dict[str, str]]] = {}


def family_of_dir(dir_name: str) -> str | None:
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


def load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


MEMBERSHIP_FAMILY = "FAMILY_DIRECTORY"        # la vista cuelga de la carpeta-tipo `views` (el pool informa familias)
MEMBERSHIP_STRUCTURAL = "STRUCTURAL_MARKER"   # pool plano: solo se reconoce por el marcador del exportador (GRAPHICAL / SQL)


def view_record(data: dict, name: str, family: str | None) -> tuple[str, str, str] | None:
    """(tipo, procedencia del tipo, base de pertenencia) si ``name`` es una VISTA de Datasphere en ``data``; si no, None. Misma pertenencia que el
    Assessment (`facts_view_type.view_membership`): carpeta `views` cuando el pool informa familias; en pools planos solo el marcador del exportador
    (GRAPHICAL / SQL), excluyendo tablas remotas/locales y modelos analíticos. El tipo sale del clasificador ÚNICO."""
    definitions = data.get("definitions")
    defn = definitions.get(name) if isinstance(definitions, dict) else None
    if not isinstance(defn, dict):
        return None
    view_type, basis = vt.view_type_of_export(data, name)
    if family is not None:                                    # el pool informa la familia: solo `views`
        return (view_type, basis, MEMBERSHIP_FAMILY) if family == "views" else None
    if view_type == vt.UNKNOWN or _is_non_view_definition(defn):   # pool plano: solo marcador estructural, sin tablas ni cubos
        return None
    return view_type, basis, MEMBERSHIP_STRUCTURAL


def sql_view_of(data: dict, name: str, family: str | None) -> tuple[str, str, str] | None:
    """(tipo, base, sql) si ``name`` es una vista SQL con consulta en ``data``; si no, None. Misma pertenencia que el Assessment."""
    rec = view_record(data, name, family)
    if rec is None or rec[0] != vt.SQL:
        return None
    query = vt.sql_query_of(data, name)
    return (rec[0], rec[1], query) if query else None


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
        data = load_json(path)
        if data is None:
            continue
        rel = path.resolve().relative_to(root.resolve()).as_posix()
        family = family_of_dir(path.parent.name) if path.parent != root else None
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
    data = load_json(path)
    family = family_of_dir(path.parent.name) if path.parent != root else None
    hit = sql_view_of(data, name, family) if data is not None else None
    if hit is None:
        return None
    view_type, basis, sql = hit
    return ViewSql(name, rel, view_type, basis, sql)
