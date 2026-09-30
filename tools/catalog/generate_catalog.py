"""
generate_catalog.py — Generador del Catálogo de Vistas CDS (Danwor)
===================================================================
PILOTO / LOCALHOST — NO productivo. Lee los exports CSN (.json) de Datasphere de
un proyecto (una carpeta por cliente/dominio) + los SQL preview (.txt) de vistas
gráficas, reconstruye el linaje cruzando los archivos, infiere capa/artefacto/
variante contra la convención §28 del módulo 04, detecta desvíos metodológicos
(semáforo de cumplimiento) y emite markdown para el catálogo.

Cada .md separa el frontmatter en:
  auto:   generado por el script desde los .json (NO editar a mano)
  manual: lo completa el consultor / senior (intención de negocio, veredicto)

Uso:
    python generate_catalog.py --in <carpeta_cliente> --out <carpeta_salida>
    (el cliente/proyecto se deriva de la ruta: <in>/<Cliente>/<Dominio>/*.json)
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Portabilidad BDC (dimensión cross-runtime; standalone, mismo dir). Import defensivo
# para no acoplar el generador si el módulo/PYTHONPATH no está disponible.
try:
    from portability_bdc import evaluate_portability as _eval_portability, load_matrix as _load_port_matrix
except Exception:  # pragma: no cover
    try:
        from tools.catalog.portability_bdc import evaluate_portability as _eval_portability, load_matrix as _load_port_matrix
    except Exception:
        _eval_portability = _load_port_matrix = None

_PORT_MATRIX_CACHE: dict = {}


def _portability_matrix(pack: str = "datasphere_core") -> dict | None:
    """Matriz de portabilidad del pack (cacheada). None si el módulo no está disponible."""
    if _load_port_matrix is None:
        return None
    if pack not in _PORT_MATRIX_CACHE:
        _PORT_MATRIX_CACHE[pack] = _load_port_matrix(pack)
    return _PORT_MATRIX_CACHE[pack]


def portability_columns(entities: dict) -> list[dict]:
    """Aplana los campos de todas las entidades a columnas para el evaluador de
    portabilidad, cada una etiquetada con su artefacto. Reusable por ambos generadores."""
    cols: list[dict] = []
    for e in entities.values():
        for f in (getattr(e, "fields", None) or []):
            cols.append({**f, "artefacto": e.name})
    return cols

from tools.catalog.compliance_criteria import APTO_CANONICAL, classify_extraction


# ── Vocabulario de dominios de negocio (para inferir el dominio del contenido) ──
# El dominio es una propiedad del CONTENIDO (el asunto del modelo), no de la carpeta
# de cliente (que es gobierno). Stock e Inventario van unificados. El senior puede
# override el dominio en el bloque manual del flujo_*.md.
_DOMAIN_VOCAB = [
    ("Stock / Inventario", ("stock", "inventario", "inventarios", "existencias", "almacen", "ivt")),
    ("Ventas", ("venta", "ventas", "facturacion", "comercial", "sales")),
    ("Compras", ("compra", "compras", "abastecimiento", "purchasing")),
    ("Finanzas", ("finanzas", "financiero", "costeo", "costos", "contable")),
    ("Producción", ("produccion", "manufactura", "fabricacion")),
]


def _norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower())


def _infer_domain(entities: dict) -> str:
    """Infiere el dominio de negocio desde el 'asunto' de los artefactos de CONSUMO
    (cubo/hecho): sus labels + nombres, matcheados contra el vocabulario. Si no hay
    consumo modelado, usa todos los nombres. 'General' si no hay match."""
    consumo, todos = [], []
    for e in entities.values():
        sem = f"{e.data_category or ''} {e.modeling_pattern or ''}".upper()
        txt = f"{e.label or ''} {e.name or ''}"
        todos.append(txt)
        if "CUBE" in sem or "FACT" in sem:
            consumo.append(txt)
    text = _norm_text(" ".join(consumo or todos))
    best, best_hits = "General", 0
    for canonical, kws in _DOMAIN_VOCAB:
        hits = sum(text.count(kw) for kw in kws)
        if hits > best_hits:
            best, best_hits = canonical, hits
    return best


# ── Convención de nombres §28 (módulo 04) ────────────────────────────────
CAPA = {"1": "Ingesta", "2": "Armonización", "3": "Propagación", "4": "Consumo"}
ARTEFACTO = {"T": "Tabla", "V": "Vista"}                       # solo T/V llevan prefijo de capa
USO = {"F": "Fact", "R": "Relacional", "D": "Dimensión",
       "A": "Analítica", "T": "Texto", "H": "Jerarquía"}       # usos homologados §28
# "Otros Artefactos": sin prefijo de capa, patrón <Abrev>_<LoB>_<libre>
OTROS = {"DF": "Data Flow", "RF": "Replication Flow", "FT": "Transformation Flow",
         "IL": "Intelligent Look-up", "TC": "Task Chain", "AM": "Analytic Model",
         "EM": "Entity Relationship Model", "DAC": "Data Access Controls"}
# LoB (MD/FNC/IVT/PUR/SND) y sufijos (_PBI/_SPM) son SUGERIDOS, no obligatorios → no se marcan.
CAPA_RE = re.compile(r"^([1-4])([A-Z])([A-Z]+)_(.+)$")
NOCAPA_RE = re.compile(r"^([A-Z])([A-Z]+)_(.+)$")


def _typed_artifact(name: str) -> tuple[str | None, str | None]:
    """(clave OTROS, dígito de capa indebido) si `name` es un artefacto tipado —con o
    SIN prefijo de capa— o (None, None) si no lo es. Un artefacto tipado (AM/DF/RF/…)
    se nombra §29 SIN número de capa: un dígito adelante (p. ej. `4AM`) es el desvío
    real, no una tokenización 4|A|M. Genérico para todos los OTROS."""
    pref = str(name).split("_", 1)[0]
    if pref in OTROS:
        return pref, None
    if len(pref) >= 2 and pref[0] in "1234" and pref[1:] in OTROS:
        return pref[1:], pref[0]
    return None, None


def parse_name(name: str) -> dict:
    """Descompone el nombre contra §28. Marca solo desvíos reales (uso/artefacto
    no homologado, o tabla/vista sin prefijo de capa). LoB/sufijos son opcionales."""
    tk, dig = _typed_artifact(name)
    if tk and not dig:
        return {"canonical": True, "tipo": "otro", "capa": None,
                "capa_nombre": OTROS[tk], "issues": []}
    if tk and dig:
        # Artefacto tipado con prefijo de capa indebido (§29). La capa se conserva
        # SOLO como evidencia del error; un único finding, sin tokenizar el nombre.
        return {"canonical": False, "tipo": "otro", "capa": dig, "capa_nombre": OTROS[tk],
                "issues": [f"artefacto tipado '{tk}' no debe llevar prefijo de capa '{dig}' "
                           f"(§29: los artefactos tipados se nombran sin número de capa)"]}
    m = CAPA_RE.match(name)
    if m:
        capa, arte, uso, _libre = m.groups()
        issues = []
        if arte not in ARTEFACTO:
            issues.append(f"artefacto '{arte}' no homologado (§28: T/V; los modelos/flows son 'Otros Artefactos')")
        if uso not in USO:
            issues.append(f"uso semántico '{uso}' no homologado (§28: F/R/D/A/T/H)")
        return {"canonical": not issues, "capa": capa, "capa_nombre": CAPA.get(capa, "?"),
                "artefacto": ARTEFACTO.get(arte, arte), "uso": USO.get(uso, uso), "issues": issues}
    m2 = NOCAPA_RE.match(name)
    if m2 and m2.group(1) in ARTEFACTO:
        return {"canonical": False, "capa": None, "capa_nombre": "?",
                "issues": ["tabla/vista sin prefijo de capa (§28: <Capa><Artefacto><Uso>_)"]}
    return {"canonical": False, "capa": None, "capa_nombre": "?",
            "issues": ["no sigue §28 <Capa><Artefacto><Uso>_ ni un artefacto homologado (DF/RF/FT/AM/…)"]}




def _is_sapi_remote(remote_entity: str) -> bool:
    """Origen ODP-SAPI remoto: la convenci?n de nombres Datasphere no aplica."""
    return (remote_entity or "").upper().startswith("SAPI.")

# ── Modelo de entidad ─────────────────────────────────────────────────────
@dataclass
class Entity:
    name: str
    file: str
    label: str = ""
    kind: str = ""
    modeling_pattern: str = ""
    data_category: str = ""            # @Analytics.dataCategory (CUBE/FACT/DIMENSION)
    remote_connection: str = ""
    remote_entity: str = ""
    repository_type: str = ""          # @DataWarehouse.repository.technicalType (# ), p. ej. DWC_LOCAL_TABLE
    is_dataflow: bool = False          # viene de un archivo DF_*
    has_query: bool = False
    has_sql_preview: bool = False
    keys: list = field(default_factory=list)
    measures: list = field(default_factory=list)
    dimensions: list = field(default_factory=list)
    associations: list = field(default_factory=list)
    fields: list = field(default_factory=list)   # [{campo,label,tipo,longitud,key,medida}]
    n_elements: int = 0
    upstreams: list = field(default_factory=list)         # linaje DIRECTO (FROM/JOIN)
    assoc_upstreams: list = field(default_factory=list)   # relaciones por ASOCIACIÓN (indirecto)

    @property
    def kind_label(self) -> str:
        if self.is_dataflow:
            return "Data Flow (tabla local)"
        # Discriminante AUTORITATIVO por sobre la heurística de `query`: una Local Table
        # persistida (delta-active) trae un `query` que es solo la proyección de activación
        # de su tabla `_Delta` — NO es una vista. Datasphere lo marca explícitamente con
        # @DataWarehouse.repository.technicalType = DWC_LOCAL_TABLE. La persistencia gana
        # incluso si hay remote/replicación.
        if self.repository_type == "DWC_LOCAL_TABLE":
            return "Tabla local"
        # Remote Table federada: el dato vive en el origen (no persiste local). La remotez
        # del ORIGEN (remote_entity · conexión) se muestra en la fila "Origen", NO como
        # tipo de artefacto — así "Artefacto" describe el objeto, no de dónde se nutre.
        if self.remote_entity:
            return "Tabla remota"
        if self.has_query or self.has_sql_preview:
            return "Vista"
        return "Tabla local"


def _collect_refs(node) -> list:
    """Junta todos los ref[0] de un subárbol (from/SET/join). Filtramos luego.

    La fuente de un ref puede venir como nombre plano (`ref: ["NAME"]`) o como
    entidad PARAMETRIZADA (`ref: [{"id":"NAME","args":{...}}]`); en ambos casos la
    dependencia es NAME. En el caso parametrizado NO se recursa dentro de `args`:
    esos refs son parámetros (P_*), no upstreams."""
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "ref" and isinstance(v, list) and v:
                first = v[0]
                if isinstance(first, str):
                    out.append(first)
                elif isinstance(first, dict) and first.get("id"):
                    out.append(str(first["id"]))   # FROM parametrizado: source = id, no los args
                else:
                    out.extend(_collect_refs(v))
            else:
                out.extend(_collect_refs(v))
    elif isinstance(node, list):
        for item in node:
            out.extend(_collect_refs(item))
    return out


def _from_sources(node) -> list:
    """Refs de entidad SOLO en la cláusula FROM/JOIN (recursivo por el árbol de
    joins y por SET/unions). NO recorre columnas, on-conditions ni mixins."""
    out = []
    if isinstance(node, dict):
        sel = node.get("SELECT")
        if isinstance(sel, dict):
            out.extend(_collect_refs(sel.get("from")))   # árbol de joins (args)
        st = node.get("SET")
        if isinstance(st, dict):
            for arg in st.get("args", []) or []:
                out.extend(_from_sources(arg))
    return out


def _extract_query_sources(defn: dict) -> list:
    """Fuentes DIRECTAS del linaje: solo FROM/JOIN de la query. Las relaciones vía
    asociación (kind entity / foreignKey / mixin) son INDIRECTAS y se tratan aparte
    (ent.associations → assoc_upstreams). Así el linaje directo = lo que realmente
    consume el SQL (FROM/LEFT JOIN), no todo ref que aparezca en el CSN compilado."""
    q = defn.get("query")
    if not isinstance(q, dict):
        return []
    return _from_sources(q)


def _from_source_id(ref) -> str:
    """id real de una fuente FROM: `ref: ["NAME"]` → NAME; fuente parametrizada
    `ref: [{"id":"NAME","args":{...}}]` → NAME. "" si no hay id."""
    if isinstance(ref, list) and ref:
        first = ref[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return str(first.get("id") or "")
    return ""


def _from_aliases(defn: dict) -> dict[str, str]:
    """Mapa `alias -> id real` declarado en el FROM del query (incluye joins/unions
    y fuentes parametrizadas). Sólo metadata CSN, sin heurística por nombre. El
    problema genérico es que `from.as` puede diferir del id real de la fuente (p. ej.
    Datasphere trunca el alias de una entidad parametrizada); esto lo resuelve para
    CUALQUIER FROM con alias, tenga `args` o no."""
    out: dict[str, str] = {}

    def _from_node(fr) -> None:
        if isinstance(fr, list):
            for x in fr:
                _from_node(x)
            return
        if not isinstance(fr, dict):
            return
        rid = _from_source_id(fr.get("ref"))
        alias = fr.get("as")
        if alias and rid:
            out[str(alias)] = rid
        for x in (fr.get("args") or []):   # árbol de joins
            _from_node(x)

    def _walk(node) -> None:
        if not isinstance(node, dict):
            return
        sel = node.get("SELECT")
        if isinstance(sel, dict):
            _from_node(sel.get("from"))
        st = node.get("SET")
        if isinstance(st, dict):
            for arg in st.get("args") or []:
                _walk(arg)

    q = defn.get("query")
    if isinstance(q, dict):
        _walk(q)
    return out


def _type_length(e: dict) -> tuple[str, str]:
    """Tipo de dato (sin prefijo cds.) y longitud legible (length; o precision,scale
    para decimales)."""
    tipo = str(e.get("type", "")).split(".")[-1] or "—"
    prec, scale, length = e.get("precision"), e.get("scale"), e.get("length")
    if prec is not None:
        longitud = f"{prec},{scale}" if scale is not None else str(prec)
    elif length is not None:
        longitud = str(length)
    else:
        longitud = ""
    return tipo, longitud


_I18N_PLACEHOLDER_RE = re.compile(r"\{\s*i18n\s*>", re.I)
_I18N_KEY_RE = re.compile(r"^\{\s*i18n\s*>\s*(.+?)\s*\}$", re.I)


def _resolve_label(raw, fallback: str, i18n_map: dict) -> str:
    """Resuelve un @EndUserText.label. Algunos exports dejan la traducción SIN resolver
    como un placeholder i18n — '{i18n>Remote.BWBRIDGE.ZIM_V01@ENDUSERTEXT.LABEL}'. Eso
    NO es la descripción: es una CLAVE que mapea a un texto real en la tabla i18n del
    propio CSN (top-level `i18n` = {idioma: {clave: texto}}). Se resuelve contra esa
    tabla (preferimos español); si la clave no está, se cae al fallback (nombre técnico
    para entidades, vacío para campos → se muestra '—')."""
    s = str(raw or "").strip()
    if not s:
        return fallback
    m = _I18N_KEY_RE.match(s)
    if m:
        text = (i18n_map or {}).get(m.group(1).strip())
        if text and not _I18N_PLACEHOLDER_RE.search(str(text)):
            return str(text).strip()
        return fallback
    # Label normal; chequeo defensivo por si llega un tag suelto sin forma {i18n>…}.
    low = s.lower()
    if "@enduserext.label" in low or (s.startswith("{") and s.endswith("}") and "@" in s):
        return fallback
    return s


def _fk_assoc_name(e: dict) -> str:
    """Nombre de la asociación referenciada por un atributo foreign-key
    (@ObjectModel.foreignKey.association), sea dict {'=': '_X'} o string."""
    fk = e.get("@ObjectModel.foreignKey.association")
    if isinstance(fk, dict):
        return str(fk.get("=") or "")
    return str(fk or "") if isinstance(fk, str) else ""


def _native_type(e: dict) -> str:
    """@DataWarehouse.native.dataType (p. ej. DECIMAL/DECFLOAT), string o {'#': ...}.
    Portabilidad BDC lo usa para distinguir un DecimalFloat que el `type` CDS oculta."""
    nt = e.get("@DataWarehouse.native.dataType")
    if isinstance(nt, dict):
        return str(nt.get("#") or "")
    return str(nt or "") if isinstance(nt, str) else ""


def _assoc_on_map(assoc_name: str, on_clause) -> dict:
    """Del `on` de una asociación, mapa {columna_origen: columna_target}. El `on` es una
    lista plana de tokens; cada '=' une dos refs. El ref cuyo primer elemento es el nombre
    de la asociación (o tiene &gt;1 segmento) es el TARGET; el otro (col simple) es el origen.
    Es lo que permite comparar el tipo de una FK contra el de su PK (portabilidad relacional)."""
    if not isinstance(on_clause, list):
        return {}
    refs = [tok.get("ref") for tok in on_clause
            if isinstance(tok, dict) and isinstance(tok.get("ref"), list)]
    out: dict[str, str] = {}
    # Emparejamos refs de a dos en el orden en que aparecen (lhs = col local, rhs = target).
    for j in range(0, len(refs) - 1, 2):
        a, b = refs[j], refs[j + 1]
        a = [x for x in a if isinstance(x, str)]
        b = [x for x in b if isinstance(x, str)]
        if not a or not b:
            continue
        # target = el ref con prefijo de la asociación (o multi-segmento); origen = el otro.
        if b and (b[0] == assoc_name or len(b) > 1):
            src, tgt = a[-1], b[-1]
        elif a and (a[0] == assoc_name or len(a) > 1):
            src, tgt = b[-1], a[-1]
        else:
            src, tgt = a[-1], b[-1]
        out[src] = tgt
    return out


def _extract_cast(node) -> str:
    """Primer CAST explícito dentro de la expresión de una columna
    ({'cast': {'type':'cds.Decimal','precision':p,'scale':s}}) → 'Decimal(p,s)'. "" si no
    hay. Portabilidad BDC lo usa para saber si un campo derivado FIJÓ su tipo (vs inferido)."""
    if isinstance(node, dict):
        c = node.get("cast")
        if isinstance(c, dict) and c.get("type"):
            t = str(c["type"]).split(".")[-1]
            p, s = c.get("precision"), c.get("scale")
            if p is not None and s is not None:
                return f"{t}({p},{s})"
            return f"{t}({p})" if p is not None else t
        for v in node.values():
            r = _extract_cast(v)
            if r:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _extract_cast(v)
            if r:
                return r
    return ""


def _extract_elements(defn: dict, ent: Entity) -> None:
    elems = defn.get("elements") or {}
    ent.n_elements = len(elems)
    # Mapa asociación → target (para resolver los atributos FK que apuntan a una
    # dimensión: p. ej. MATNR → _MATNR → VD_Material). Los elementos `_assoc`
    # (type cds.Association) NO son atributos visibles: respaldan al atributo FK.
    assoc_target = {en: e.get("target", "") for en, e in elems.items()
                    if isinstance(e, dict) and str(e.get("type", "")).endswith("Association")}
    # Columna target por FK (portabilidad relacional PK/FK): {assoc_name: {src_col: tgt_col}}.
    assoc_on = {en: _assoc_on_map(en, e.get("on"))
                for en, e in elems.items()
                if isinstance(e, dict) and str(e.get("type", "")).endswith("Association")}
    seen = {f["campo"] for f in ent.fields}   # dedupe si la entidad aparece en >1 archivo
    for ename, e in elems.items():
        if not isinstance(e, dict):
            continue
        if str(e.get("type", "")).endswith("Association"):
            ent.associations.append({"campo": ename, "target": e.get("target", ""),
                                     "on": assoc_on.get(ename, {})})
            continue
        if e.get("key"):
            ent.keys.append(ename)
        # medida: agregación por defecto o measureType analítico
        is_measure = "@Aggregation.default" in e or "@AnalyticsDetails.measureType" in e
        if is_measure:
            agg = (e.get("@Aggregation.default") or {}).get("#", "")
            ent.measures.append({"campo": ename, "agg": agg})
        elif e.get("@Analytics.dimension"):
            ent.dimensions.append(ename)
        if ename not in seen:
            tipo, longitud = _type_length(e)
            _fk = _fk_assoc_name(e)
            assoc = assoc_target.get(_fk, "")                 # dimensión destino si es FK
            assoc_col = (assoc_on.get(_fk, {}) or {}).get(ename, "")   # columna PK referenciada
            ent.fields.append({
                "campo": ename, "label": str(e.get("@EndUserText.label") or ""),
                "tipo": tipo, "longitud": longitud,
                "key": bool(e.get("key")), "medida": is_measure, "assoc": assoc,
                # Extensión portabilidad BDC (aditivo, defaults seguros):
                "native": _native_type(e), "assoc_col": assoc_col,
                "derived": False, "cast": "",   # los completa el post-pass del query
            })
            seen.add(ename)


class CatalogCollisionError(Exception):
    """Mixed-mode: la MISMA identidad lógica (nombre de entidad) aparece en dos
    archivos de carpetas FÍSICAS distintas del mismo pool (cliente/dominio) con
    contenido DISTINTO. Se reporta de forma determinística — nunca last-write-wins
    (contrato owner §5). Dos representaciones idénticas se deduplican en silencio;
    sólo el contenido divergente es una colisión."""


def _semantic_definition_signature(definition: dict) -> str:
    """Firma canónica de una entidad para dedup/colisión mixed-mode.

    Ignora ÚNICAMENTE `_meta.dependencies.folderAssignment` — bookkeeping
    interno de SAP Datasphere (en qué carpeta visual del Data Builder está
    filed el objeto), nunca leído por este generador. Forense de producción
    2026-09-30 (`_handoffs/catalog-cli-production-regeneration-regression.md`,
    CASE B): 28/28 colisiones reales mixed-mode de un cliente real resultaron
    ser únicamente este campo — el export legacy lo escribe, el export CLI
    no, sin ninguna otra divergencia de definición. Cualquier otra diferencia
    (incluida cualquier otra clave bajo `_meta`) sigue formando parte de la
    firma sin cambios — nunca se afloja el criterio general de colisión."""
    normalized = copy.deepcopy(definition)
    deps = (normalized.get("_meta") or {}).get("dependencies")
    if isinstance(deps, dict) and "folderAssignment" in deps:
        del deps["folderAssignment"]
        if not deps:
            del normalized["_meta"]["dependencies"]
        if not normalized["_meta"]:
            del normalized["_meta"]
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False, default=str)


def load_folder(folder: Path | None = None, *, json_files=None, txt_files=None) -> tuple[dict, dict]:
    """Devuelve (entities_by_name, sql_by_name).

    Legacy: `load_folder(folder)` recorre PLANO `folder.glob("*.json"/"*.txt")`.
    Pool unificado (export CLI, contrato owner §4): `load_folder(json_files=[...],
    txt_files=[...])` carga TODAS las carpetas-tipo de un (cliente, dominio) en UN
    solo pool, así el linaje resuelve upstreams cross-folder (una view en views/
    encuentra su tabla en local-tables/). La identidad es SEMÁNTICA (nombre de
    entidad), nunca el path: dos archivos que definen la misma entidad se
    deduplican si su contenido canónico coincide, y levantan `CatalogCollisionError`
    si difieren Y vienen de carpetas físicas distintas (mixed-mode)."""
    entities: dict[str, Entity] = {}
    sql_by_name: dict[str, str] = {}
    df_membership: dict[str, dict] = {}   # DF file → {targets, sources}
    raw_elems: dict[str, dict] = {}       # entidad → dict crudo de elements (para heredar tipos)
    col_refs: dict[str, dict] = {}        # entidad → {alias: ref} de query.SELECT.columns
    from_alias: dict[str, dict] = {}      # entidad → {alias FROM: id real} (resuelve as != id)
    col_expr: dict[str, dict] = {}        # entidad → {alias: {derived, cast}} (columnas calculadas)
    i18n_map: dict[str, str] = {}         # clave i18n → texto (para resolver labels placeholder)
    # Detección de colisión cross-folder: por nombre de entidad, (sig canónica del
    # definition, carpeta física de origen) del PRIMER archivo que la definió.
    _defn_origin: dict[str, tuple[str, Path]] = {}
    _collisions: list[str] = []

    _txts = list(txt_files) if txt_files is not None else sorted((folder or Path()).glob("*.txt"))
    _jsons = list(json_files) if json_files is not None else sorted((folder or Path()).glob("*.json"))

    # SQL preview de vistas gráficas: <nombre>.txt
    for txt in _txts:
        sql_by_name[txt.stem] = txt.read_text(encoding="utf-8", errors="ignore")

    for jf in _jsons:
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] no se pudo leer {jf.name}: {exc}")
            continue
        if not isinstance(data, dict):
            print(f"[WARN] se omite {jf.name}: JSON raíz no es objeto ({type(data).__name__})")
            continue
        # Tabla i18n del CSN ({idioma: {clave: texto}}) para resolver los labels que
        # vienen como placeholder '{i18n>clave}'. Se mergea entre archivos; iteramos
        # en → es para que el español (si existe) gane.
        _i18n = data.get("i18n")
        if isinstance(_i18n, dict):
            for _lang in ("en", "es"):
                _tbl = _i18n.get(_lang)
                if isinstance(_tbl, dict):
                    for _k, _v in _tbl.items():
                        if _v:
                            i18n_map[str(_k)] = str(_v)
        is_df = jf.stem.upper().startswith("DF_")
        defs = data.get("definitions") or {}
        for name, defn in defs.items():
            if not isinstance(defn, dict):
                continue
            # Identidad SEMÁNTICA + colisión cross-folder (contrato owner §5). La
            # firma canónica del definition detecta contenido divergente; sólo se
            # considera colisión si además proviene de OTRA carpeta física (el
            # merge intra-carpeta legacy queda idéntico — dos archivos de la misma
            # carpeta que aportan a una entidad no cambian de comportamiento).
            _sig = _semantic_definition_signature(defn)
            _prev = _defn_origin.get(name)
            if _prev is None:
                _defn_origin[name] = (_sig, jf.parent)
            elif _prev[1] != jf.parent and _prev[0] != _sig:
                _collisions.append(
                    f"'{name}' definido distinto en {_prev[1].name}/ y {jf.parent.name}/ ({jf.name})")
            ent = entities.get(name) or Entity(name=name, file=jf.name)
            ent.label = str(defn.get("@EndUserText.label") or ent.label or name)
            ent.kind = defn.get("kind", ent.kind)
            ent.modeling_pattern = (defn.get("@ObjectModel.modelingPattern") or {}).get("#", ent.modeling_pattern)
            ent.data_category = (defn.get("@Analytics.dataCategory") or {}).get("#", ent.data_category)
            ent.remote_connection = defn.get("@DataWarehouse.remote.connection", ent.remote_connection)
            ent.remote_entity = defn.get("@DataWarehouse.remote.entity", ent.remote_entity)
            ent.repository_type = (defn.get("@DataWarehouse.repository.technicalType") or {}).get("#", ent.repository_type)
            ent.has_query = ent.has_query or ("query" in defn)
            _extract_elements(defn, ent)
            ent._raw_refs = getattr(ent, "_raw_refs", []) + _extract_query_sources(defn)  # type: ignore
            _fa = _from_aliases(defn)
            if _fa:
                from_alias.setdefault(name, {}).update(_fa)
            # Guardar elements crudos + mapa alias→ref de las columnas del query, para
            # heredar tipo/longitud en artefactos que proyectan columnas sin cast inline
            # (p. ej. un Analytic Model cuyo cubo referencia las medidas del hecho).
            raw_elems.setdefault(name, {}).update(defn.get("elements") or {})
            _q = defn.get("query")
            if isinstance(_q, dict):
                for _c in ((_q.get("SELECT") or {}).get("columns") or []):
                    if not isinstance(_c, dict):
                        continue
                    _has_ref = isinstance(_c.get("ref"), list) and _c["ref"]
                    _ref = [x for x in _c["ref"] if isinstance(x, str)] if _has_ref else []
                    if _ref:
                        col_refs.setdefault(name, {})[str(_c.get("as") or _ref[-1])] = _ref
                    # Columna CALCULADA (xpr/func/val o un ref con cast) → registrar que es
                    # derivada y si fijó su tipo con CAST explícito (portabilidad BDC).
                    _alias = str(_c.get("as") or (_ref[-1] if _ref else "")).strip()
                    _cast = _extract_cast(_c)
                    if _alias and (("xpr" in _c) or ("func" in _c) or ("val" in _c) or _cast):
                        col_expr.setdefault(name, {})[_alias] = {"derived": True, "cast": _cast}
            # el target de un DF es la entidad "principal" del archivo (mismo stem sin DF_)
            if is_df:
                slot = df_membership.setdefault(jf.stem, {"targets": set(), "sources": set()})
                if defn.get("query") is None and not defn.get("@DataWarehouse.remote.entity"):
                    ent.is_dataflow = True
                    slot["targets"].add(name)
                if defn.get("@DataWarehouse.remote.entity") or parse_name(name).get("capa") == "1":
                    slot["sources"].add(name)
            entities[name] = ent

    # Resolución de labels i18n: el label de la entidad y de cada campo puede venir
    # como placeholder '{i18n>clave}'; se resuelve contra la tabla i18n del CSN
    # (español preferido). Si no hay traducción, cae al nombre técnico (entidad) o a
    # vacío (campo → '—'). Descartar sin resolver perdería descripciones válidas.
    for name, ent in entities.items():
        ent.label = _resolve_label(ent.label, name, i18n_map)
        for f in ent.fields:
            f["label"] = _resolve_label(f.get("label"), "", i18n_map)

    # Post-pass portabilidad: marcar campos derivados + su cast explícito (de col_expr).
    for name, ent in entities.items():
        exprs = col_expr.get(name) or {}
        for f in ent.fields:
            info = exprs.get(f.get("campo"))
            if info:
                f["derived"] = True
                if info.get("cast"):
                    f["cast"] = info["cast"]

    # Herencia de tipo/longitud vía el ref del query: un artefacto que proyecta una
    # columna sin cast inline (típico del Analytic Model / cubo, cuyos elements solo
    # traen label + measureType) hereda el tipo de la entidad fuente referenciada
    # (p. ej. AM_BASE_STOCKS.Stock_Libre_Utilizacion → VF_BASE_STOCK.Stock_Libre_Utilizacion,
    # cds.Decimal 17,3). Se sigue la cadena de refs hasta encontrar un tipo inline.
    def _inherit_typelen(ent_name: str, field: str, depth: int = 0):
        if depth > 6:
            return None
        e = (raw_elems.get(ent_name) or {}).get(field)
        if isinstance(e, dict):
            t = str(e.get("type") or "")
            if t and not t.endswith("Association"):
                return _type_length(e)
        ref = (col_refs.get(ent_name) or {}).get(field)
        if ref and len(ref) >= 2:
            # El token fuente del ref (ref[0]) es el ALIAS declarado en el FROM, que
            # puede diferir del id real de la entidad (as != id). Se traduce vía el
            # mapa alias→id del propio artefacto antes de seguir la cadena.
            src = (from_alias.get(ent_name) or {}).get(ref[0], ref[0])
            return _inherit_typelen(src, ref[-1], depth + 1)
        return None

    for name, ent in entities.items():
        if not col_refs.get(name):
            continue
        for f in ent.fields:
            if (not f.get("tipo") or f["tipo"] == "—") and not f.get("longitud"):
                resolved = _inherit_typelen(name, f["campo"])
                if resolved:
                    f["tipo"], f["longitud"] = resolved
        # Descartar campos RESIDUALES: proyecciones que quedaron sin tipo NI longitud
        # resolubles (p. ej. atributos de navegación de un cubo que apuntan a un
        # mixin/asociación y no forman parte real del modelo). Se preservan siempre
        # claves, medidas y relaciones a dimensión (assoc), que nunca deben perderse.
        ent.fields = [
            f for f in ent.fields
            if f.get("key") or f.get("medida") or f.get("assoc")
            or (f.get("tipo") and f["tipo"] != "—") or f.get("longitud")
        ]

    # SQL preview → marca + refs
    for name, ent in entities.items():
        if name in sql_by_name:
            ent.has_sql_preview = True
            refs = re.findall(r'(?:FROM|JOIN)\s+"?([A-Za-z0-9_.]+)"?', sql_by_name[name], re.I)
            ent._raw_refs = getattr(ent, "_raw_refs", []) + refs  # type: ignore

    # Resolver upstreams (linaje de flujo de datos):
    #  - refs de query/SQL (el `from`) siempre.
    #  - targets de asociaciones SOLO si NO es un cubo (modelo analítico): un cubo
    #    re-expone las dimensiones de su hecho, no las consume directo → su única
    #    fuente es el hecho (su `from`). Un hecho/vista sí consume dims por asociación.
    def _is_transform_base(x: str) -> bool:
        """¿`x` es una base transformacional (vista relacional / hecho) y no una
        dimensión ni una fuente cruda? Determina si un input de una entidad analítica
        cuenta como linaje DIRECTO (la base) o como ASOCIACIÓN (dim/texto/crudo)."""
        xe = entities.get(x)
        if not xe:
            return False
        sem = f"{xe.data_category or ''} {xe.modeling_pattern or ''}".upper()
        return (not xe.remote_entity) and ("DIMENSION" not in sem)

    for name, ent in entities.items():
        raw = getattr(ent, "_raw_refs", [])
        direct = {r for r in raw if r in entities and r != name}          # FROM/JOIN
        assoc = {a["target"] for a in ent.associations if a["target"] in entities and a["target"] != name}
        sem = f"{ent.data_category or ''} {ent.modeling_pattern or ''}".upper()
        analytical = any(k in sem for k in ("FACT", "DIMENSION", "CUBE"))
        if analytical:
            # Entidad analítica (hecho/dimensión/cubo): su linaje DIRECTO es su BASE
            # transformacional (vista relacional / hecho); DEGRADAMOS a asociación las
            # dimensiones, textos y fuentes crudas que joinea. Solo se degrada (nunca
            # se promueve una asociación a directa) — igual que Datasphere (el hecho VF
            # parte solo de 2VR; sus dims son asociaciones).
            demote = {x for x in direct if not _is_transform_base(x)}
            direct -= demote
            assoc |= demote
        else:
            assoc = assoc - direct   # vista relacional/transformación: todo FROM/JOIN es directo
        ent.upstreams = sorted(direct)
        ent.assoc_upstreams = sorted(assoc)

    # Data Flow: el target persistido hereda el upstream de su vista de origen si el
    # DF no lo expone directamente (un DF lee de una vista y materializa una tabla).
    for slot in df_membership.values():
        for tname in slot["targets"]:
            t = entities.get(tname)
            if t and not t.upstreams:
                srcs = {s for s in slot["sources"] if s != tname}
                t.upstreams = sorted(srcs)

    if _collisions:
        raise CatalogCollisionError(
            "Colisión de identidad mixed-mode (misma entidad, contenido distinto en "
            "carpetas físicas diferentes): " + "; ".join(sorted(set(_collisions))))
    return entities, sql_by_name


# ── Semáforo de cumplimiento ──────────────────────────────────────────────
@dataclass
class Finding:
    id: str
    severidad: str   # alto | medio | nota
    titulo: str
    evidencia: str


def evaluate_compliance(entities: dict) -> tuple[list, str, str]:
    findings: list[Finding] = []
    classes: dict[str, list] = {}
    for e in entities.values():
        if e.remote_entity:
            classes.setdefault(classify_extraction(e.remote_entity), []).append(e)

    # ── Eje 1: calidad de la extracción ──────────────────────────────────
    if classes.get("ideal"):
        findings.append(Finding("cds_extraction", "nota",
            "Extracción vía CDS view corporativa (ABAP_CDS) — desacople correcto ✓",
            ", ".join(f"{e.name}→{e.remote_entity}" for e in classes["ideal"])))
    if classes.get("poor_raw"):
        raw = classes["poor_raw"]
        # Rúbrica PROPORCIONAL (mirada de arquitectura): una tabla cruda no degrada
        # el flujo entero por su sola presencia. Pesa (1) MATERIALIDAD y (2) CENTRALIDAD.
        # El anti-patrón es específico del sourcing ABAP (tabla cruda vs CDS view /
        # API corporativa), así que la materialidad se mide sobre la FAMILIA ABAP del
        # flujo (ABAP_CDS + ABAPTABLES), no sobre todos los orígenes: los extractores
        # SAPI/BW son otro eje y no deberían diluir la métrica. Sistémico (alto →
        # parcial) cuando la cruda es material (> 1/3 del sourcing ABAP) o alimenta el
        # hecho/cubo; reserva puntual (medio, no baja a parcial) cuando es minoritaria
        # y periférica.
        decoupled = len(classes.get("ideal", [])) + len(classes.get("acceptable_view", []))
        abap_family = decoupled + len(raw)              # orígenes ABAP (crudos + desacoplados)
        raw_on_fact = any(_is_cube(e) for e in raw)
        raw_ratio = (len(raw) / abap_family) if abap_family else 1.0
        raw_systemic = raw_on_fact or raw_ratio > (1 / 3)
        ratio_txt = (f"{decoupled} de {abap_family} orígenes ABAP desacoplan vía CDS/vista "
                     f"estándar; {len(raw)} tabla(s) cruda(s)")
        origins = ", ".join(f"{e.name}→{e.remote_entity} @{e.remote_connection}" for e in raw)
        if raw_systemic:
            motivo = "alimenta el hecho/cubo" if raw_on_fact else f"peso material ({raw_ratio:.0%} del sourcing ABAP)"
            findings.append(Finding("abap_direct", "alto",
                "Importa tablas ABAP crudas directas (no desacopla vía CDS view / API corporativa)",
                f"{origins} — {ratio_txt}; sistémico: {motivo}"))
        else:
            findings.append(Finding("abap_direct", "medio",
                "Importa una tabla ABAP cruda minoritaria y periférica (reserva puntual, no degrada el flujo)",
                f"{origins} — {ratio_txt}; periférica (no alimenta el hecho)"))
    if classes.get("acceptable_view"):
        findings.append(Finding("abap_std_view", "nota",
            "Origen vía vista estándar SAP (ABAPTABLES.NSDM_V_*/_V_*) — aceptable, no es tabla cruda",
            ", ".join(f"{e.name}→{e.remote_entity}" for e in classes["acceptable_view"])))
    if classes.get("acceptable_bw"):
        findings.append(Finding("bw_bridge", "medio",
            "Origen vía BW Bridge (virtualización) — válido pero específico; no es CDS/API directo desde el ERP",
            ", ".join(e.name for e in classes["acceptable_bw"])))

    # ── Eje 2: fuentes en paralelo (misma entidad por >1 conexión) ────────
    by_src: dict[str, set] = {}
    for e in entities.values():
        if e.remote_entity:
            by_src.setdefault(e.remote_entity, set()).add(e.remote_connection or "?")
    for src, conns in by_src.items():
        if len(conns) > 1:
            findings.append(Finding("dual_source", "alto",
                "Misma entidad origen replicada por múltiples conexiones en paralelo (sin refactor)",
                f"{src} vía {sorted(conns)}"))

    # ── Eje 3: notas (no bloquean el veredicto, son oportunidades) ────────
    naming_issues = []
    for e in entities.values():
        if e.remote_entity:
            continue
        pn = parse_name(e.name)
        if pn["issues"]:
            naming_issues.append(f"{e.name}: {'; '.join(pn['issues'])}")
    if naming_issues:
        findings.append(Finding("naming", "nota",
            "Oportunidades en la convención de nombres §28 (módulo 04)",
            " | ".join(naming_issues)))

    # ── Eje 4: nombre vs. realidad (1.1) ──────────────────────────────────
    # Un artefacto cuyo NOMBRE lleva `ZCDS` se presenta como CDS view corporativa
    # (extracción desacoplada vía API corporativa). Si su ORIGEN real NO es ABAP_CDS.*
    # —p. ej. ABAPTABLES.NSDM_V_* (vista estándar SAP) o una tabla cruda— el nombre
    # no refleja el patrón de extracción real. Es una nota de TRANSPARENCIA: no baja
    # el veredicto (la calidad del desacople ya la puntúa el Eje 1), pero expone la
    # discrepancia para que el consumidor no asuma un contrato que el objeto no cumple.
    name_reality = []
    for e in entities.values():
        if not e.remote_entity:
            continue
        if "ZCDS" in (e.name or "").upper() and not e.remote_entity.upper().startswith("ABAP_CDS."):
            name_reality.append(f"{e.name}→{e.remote_entity}")
    if name_reality:
        findings.append(Finding("name_vs_reality", "nota",
            "Nombre vs. realidad: nombrado como CDS corporativa (ZCDS) pero el origen no es ABAP_CDS — el nombre no refleja el patrón de extracción real",
            " | ".join(name_reality)))

    # (No se marca "duplicación" por sujeto: una tabla persistida + su vista de
    #  origen son una CADENA, no un duplicado. El riesgo real de fuentes paralelas
    #  ya lo cubre `dual_source`.)

    # ── Veredicto: la extracción es el eje dominante ──────────────────────
    # Las frases `apto` que tienen equivalente canónico se CONSTRUYEN desde
    # APTO_CANONICAL (fuente única compartida con catalog_service); las variantes
    # descriptivas (reserva puntual / vista estándar / revisar) no tienen canónico.
    altos = sum(1 for f in findings if f.severidad == "alto")
    if altos:
        return findings, "parcial", APTO_CANONICAL["parcial"]
    # Reserva puntual: hay una cruda pero la rúbrica proporcional la calificó `medio`
    # (minoritaria + periférica). Es `buena`, no `plena` ni `parcial`.
    if any(f.id == "abap_direct" and f.severidad == "medio" for f in findings):
        return findings, "buena", "Reutilizable con reserva puntual — origen crudo minoritario y periférico"
    if classes.get("acceptable_bw"):
        return findings, "buena", f"{APTO_CANONICAL['buena']} (BW Bridge)"
    if classes.get("ideal"):
        return findings, "plena", f"{APTO_CANONICAL['plena']} (atender notas menores)"
    if classes.get("acceptable_view"):
        return findings, "buena", "Reutilizable — origen por vista estándar SAP"
    return findings, "buena", "Reutilizable — revisar origen"


# ── Render markdown ───────────────────────────────────────────────────────
def _fm(auto: dict, manual: dict) -> str:
    body = yaml.safe_dump({"auto": auto, "manual": manual}, allow_unicode=True, sort_keys=False)
    return ("---\n"
            "# AUTO · generado por el script desde los .json (NO editar a mano)\n"
            "# MANUAL · lo completa el consultor / senior\n"
            f"{body}---\n")


def _read_manual(path: Path) -> dict:
    """Lee el bloque `manual:` de un .md existente (para no pisarlo al regenerar)."""
    if not path.exists():
        return {}
    m = re.match(r"^---\n(.*?)\n---", path.read_text(encoding="utf-8"), re.S)
    if not m:
        return {}
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:
        return {}
    return fm.get("manual") or {}


def artifact_metadata_rows(
    *, capa, artefacto, uso=None, origen=None, deriva_de=(), deriva_via=None,
    asociaciones=(), modeling_pattern=None, extra_rows=(),
) -> list[tuple[str, str]]:
    """Filas (label, valor) de la tabla de metadata de un artefacto, en el ORDEN
    CANÓNICO. FUENTE ÚNICA compartida por Dominios (`render_vista`) y Data Products
    (`generate_data_products_catalog`): cambiar el set/orden acá se refleja en ambos
    catálogos, sin replicar a mano. Cada consumidor pasa sus valores ya formateados;
    lo específico de cada mundo (p. ej. capa HDLF/Object Store o el origen vía
    Replication Flow en Data Products) entra como override de esos mismos campos."""
    # "Capa" solo cuando aplica: un artefacto tipado (AM/DF/…) NO tiene capa (§29), así
    # que el consumidor pasa capa vacía y la fila se omite (no se muestra "Capa: Consumo").
    rows: list[tuple[str, str]] = ([("Capa", capa)] if capa else []) + [("Artefacto", artefacto)]
    if uso:
        rows.append(("Uso semántico", uso))
    if origen:
        rows.append(("Origen", origen))
    if deriva_de:
        # `deriva_via` anota CÓMO se derivó (p. ej. "Transformation Flow" para la
        # publicación HANA→HDLF en Data Products), simétrico a "Origen · vía …".
        derived = ", ".join(f"`{u}`" for u in deriva_de)
        rows.append(("Deriva de", f"{derived} · vía {deriva_via}" if deriva_via else derived))
    if asociaciones:
        rows.append(("Asociaciones", ", ".join(f"`{u}`" for u in asociaciones)))
    if modeling_pattern:
        rows.append(("Patrón de modelado", modeling_pattern))
    rows.extend(extra_rows)
    return rows


def render_vista(ent: Entity, cliente: str, proyecto: str, manual_override: dict | None = None) -> str:
    pn = parse_name(ent.name)
    is_sapi = _is_sapi_remote(ent.remote_entity)
    uso = "N/A" if is_sapi else pn.get("uso")   # solo presente en nombres §28 canónicos <Capa><Artefacto><Uso>_
    pref = ent.name.split("_", 1)[0]
    typed_key, _typed_layer = _typed_artifact(ent.name)   # AM/DF/… con o sin prefijo de capa
    # Capa = responsabilidad metodológica declarada por convención §28.
    # Si una vista/tabla no declara capa y no es fuente remota, no inferimos la capa
    # desde dataCategory/modelingPattern: DIMENSION/FACT/CUBE describen semántica
    # técnica, no necesariamente responsabilidad Danwor.
    if is_sapi:
        capa_nombre = "Ingesta"
    elif pn.get("capa") and not typed_key:
        capa_nombre = CAPA.get(pn["capa"], "Sin definir")
    elif typed_key:
        capa_nombre = CAPA.get(_capa_of(ent), "Sin definir")
    elif ent.remote_entity:
        capa_nombre = "Ingesta"
    else:
        capa_nombre = "Sin definir"
    # Artefacto = tipo §28 real: "Analytic Model"/"Data Flow"/… para Otros Artefactos,
    # o el tipo técnico (Vista/Tabla/Origen remoto) para T/V. El patrón crudo del
    # JSON (p. ej. ANALYTICAL_CUBE) queda en la fila "Patrón de modelado".
    artefacto = "Extractor ODP-SAPI" if is_sapi else OTROS.get(typed_key, ent.kind_label)
    auto = {
        "tipo": "vista", "artefacto_tecnico": artefacto, "nombre": ent.name,
        "label": ent.label, "capa": capa_nombre,
        "uso_semantico": uso or None,
        "modeling_pattern": ent.modeling_pattern or None,
        "data_category": ent.data_category or None,
        "origen_remoto": ent.remote_entity or None,
        "conexion": ent.remote_connection or None,
        "upstreams": ent.upstreams or [],
        "upstreams_asociacion": ent.assoc_upstreams or [],
        "claves": ent.keys, "medidas": [m["campo"] for m in ent.measures],
        "n_campos": ent.n_elements, "campos": ent.fields,
        "desvios_nombre": [] if is_sapi else pn.get("issues", []),
    }
    manual = {"cliente": cliente, "proyecto": proyecto,
              "descripcion_negocio": "", "notas": "",
              **(manual_override or {})}
    # Cuerpo = dos tablas (el copiloto las renderiza con el estilo de contenido de
    # cualquier módulo): (1) resumen de la entidad, (2) campos con tipo/longitud/clave.
    rows = artifact_metadata_rows(
        # Artefacto tipado (AM/DF/…) no tiene capa metodológica (§29): se omite la fila
        # en el render (el finding ya explica el prefijo indebido). `auto.capa` interno
        # se conserva para el linaje.
        capa="" if typed_key else capa_nombre,
        artefacto=artefacto,
        uso=uso or None,
        origen=(f"`{ent.remote_entity}` · conexión `{ent.remote_connection}`"
                if ent.remote_entity else None),
        deriva_de=ent.upstreams or (),
        modeling_pattern=ent.modeling_pattern or None,
    )
    # Sin subtítulo `>`: la descripción (label) la usa el detalle del flujo como
    # título de sección ("Descripción {NOMBRE}"); repetirla acá sería redundante.
    md = [_fm(auto, manual), f"# {ent.name}", "",
          "| Propiedad | Valor |", "|---|---|"]
    md += [f"| {k} | {v} |" for k, v in rows]
    md.append("")

    # Medidas y Atributos separados (como los muestra Datasphere). Si no hay medidas
    # es una tabla/vista sin indicadores → una sola tabla "Campos".
    measures = [f for f in ent.fields if f.get("medida")]
    attrs = [f for f in ent.fields if not f.get("medida")]

    def _attr_rows(items: list) -> list:
        out = ["| Campo | Descripción | Tipo | Long. | Clave | Relación |",
               "|---|---|---|---|:--:|---|"]
        for f in items:
            rel = f"→ `{f['assoc']}`" if f.get("assoc") else ""
            out.append(
                f"| `{f['campo']}` | {f['label'] or '—'} | {f['tipo']} | "
                f"{f['longitud'] or '—'} | {'🔑' if f['key'] else ''} | {rel} |")
        return out

    if measures:
        md += [f"#### Medidas ({len(measures)})", "",
               "| Medida | Descripción | Tipo | Long. |", "|---|---|---|---|"]
        md += [f"| `{f['campo']}` | {f['label'] or '—'} | {f['tipo']} | {f['longitud'] or '—'} |"
               for f in measures]
        md.append("")
    if attrs:
        md += [f"#### {'Atributos' if measures else 'Campos'} ({len(attrs)})", ""]
        md += _attr_rows(attrs)
        md.append("")
    return "\n".join(md)


def _capa_of(e) -> str:
    """Capa de responsabilidad (1-4) para orden/display, robusta. Prioridad:
    (1) fuente remota cruda → Ingesta; (2) prefijo §28 explícito
    (autoridad del modelador); (3) patrón de modelado / dataCategory de
    Datasphere (dimensión → Armonización, hecho → Propagación, cubo → Consumo)
    — señal fiable del JSON, no heurística de nombre; (4) prefijos de Otros
    Artefactos como último recurso."""
    if e.remote_entity:
        return "1"
    pn = parse_name(e.name)
    if pn.get("capa"):
        return pn["capa"]
    sem = f"{e.data_category or ''} {e.modeling_pattern or ''}".upper()
    if "CUBE" in sem:
        return "4"
    if "FACT" in sem:
        return "3"
    if "DIMENSION" in sem:
        return "2"
    pref = e.name.split("_", 1)[0]
    if pref in ("AM", "EM"):          # Analytic/Entity Model → consumo
        return "4"
    if pref[:1] in ("V",) or pref in ("VF", "VD", "VR"):
        return "3"            # vista sin prefijo → propagación (inferido)
    return "9"


def render_flujo(cliente: str, proyecto: str, dominio: str, entities: dict,
                 findings: list, veredicto: str, apto: str,
                 manual_override: dict | None = None) -> str:
    ordered = sorted(entities.values(), key=lambda e: (_capa_of(e), e.name))

    auto = {
        "tipo": "flujo", "dominio": dominio,
        "artefactos": [e.name for e in ordered],
        "linaje": {e.name: (e.upstreams or None) for e in ordered if e.upstreams or e.remote_entity},
        "origenes": sorted({e.remote_entity for e in entities.values() if e.remote_entity}),
        "compliance": {
            "mirada_danwor": veredicto,
            "apto_para": apto,
            "hallazgos": [{"severidad": f.severidad, "titulo": f.titulo, "evidencia": f.evidencia}
                          for f in findings],
        },
    }
    # Dimensión PARALELA: portabilidad BDC (preventiva para un dominio). No toca el
    # veredicto de extracción; se persiste aparte y el runtime la renderiza.
    if _eval_portability is not None:
        try:
            auto["portabilidad_bdc"] = _eval_portability(
                portability_columns(entities), "dominio", _portability_matrix())
        except Exception:
            pass
    manual = {"cliente": cliente, "proyecto": proyecto,
              "dominio": "",   # override del senior: si lo completa, gana sobre auto.dominio
              "intent_negocio": "", "extraccion_corporativa": "",
              "compliance_veredicto_confirmado": "", "notas_senior": "",
              **(manual_override or {})}

    icon = {"plena": "🟢", "buena": "🟡", "parcial": "🟠"}.get(veredicto, "⚪")
    md = [_fm(auto, manual), f"# Flujo · {dominio} — {cliente}", "",
          f"**Cumplimiento:** {icon} mirada Danwor **{veredicto}** — {apto}", "",
          "## Linaje", "", "```text"]
    for e in ordered:
        arrow = f"  ← {', '.join(e.upstreams)}" if e.upstreams else (
            f"  (origen: {e.remote_entity})" if e.remote_entity else "")
        md.append(f"{_capa_of(e)} · {e.name}{arrow}")
    md.append("```")
    md.append("")
    md.append("## Hallazgos de cumplimiento")
    md.append("")
    if not findings:
        md.append("Sin desvíos detectados.")
    for f in findings:
        sev = {"alto": "🔴", "medio": "🟡", "nota": "🔵"}.get(f.severidad, "•")
        md.append(f"- {sev} **{f.titulo}**  \n  _Evidencia:_ {f.evidencia}")
    md.append("")
    return "\n".join(md)


def _is_cube(e) -> bool:
    """¿La entidad es un modelo analítico / cubo (el 'producto' consumible del flujo)?"""
    return "CUBE" in f"{e.data_category or ''} {e.modeling_pattern or ''}".upper()


def _cube_subgraph(entities: dict, cube) -> dict:
    """Sub-conjunto de entidades del flujo de un cubo: el cubo + todos sus ancestros
    (linaje directo + asociaciones, recursivo). Las dims compartidas quedan incluidas
    en cada cubo que las use — sin duplicar el archivo fuente."""
    keep: dict = {}
    stack = [cube.name]
    while stack:
        n = stack.pop()
        if n in keep or n not in entities:
            continue
        e = entities[n]
        keep[n] = e
        for up in (list(e.upstreams or []) + list(e.assoc_upstreams or [])):
            if up in entities and up not in keep:
                stack.append(up)
    return keep


def _domain_from_label(cube) -> str:
    """Nombre de dominio a partir del label del cubo cuando el vocabulario no matchea.
    'Modelo Analítico - Despachos' → 'Despachos'; sin separador, usa el label/nombre."""
    lab = str(cube.label or cube.name).strip()
    parts = re.split(r"\s[-–—:]\s", lab, maxsplit=1)
    tail = parts[-1].strip() if len(parts) > 1 else lab
    return tail or cube.name


def _domain_for_cube(cube, sub: dict) -> str:
    dom = _infer_domain(sub)
    return dom if (dom and dom != "General") else _domain_from_label(cube)


def _slug_domain(dom: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", dom).strip("_") or "flujo"


def _write_flow(out_dir: Path, cliente: str, dom: str, sub: dict, folder: str) -> str:
    """Escribe vistas + flujo de un dominio (preserva bloques manual). Devuelve línea de log."""
    proyecto = f"{cliente} · {dom}"
    findings, veredicto, apto = evaluate_compliance(sub)
    (out_dir / "vistas").mkdir(parents=True, exist_ok=True)
    for ent in sub.values():
        vp = out_dir / "vistas" / f"{ent.name}.md"
        vp.write_text(render_vista(ent, cliente, proyecto, _read_manual(vp)), encoding="utf-8")
    fp = out_dir / f"flujo_{folder}.md"
    fp.write_text(
        render_flujo(cliente, proyecto, dom, sub, findings, veredicto, apto, _read_manual(fp)),
        encoding="utf-8")
    icon = {"plena": "🟢", "buena": "🟡", "parcial": "🟠"}.get(veredicto, "⚪")
    return f"{icon} {dom}: {len(sub)} artefactos · {veredicto}"


def process_folder(src: Path, out_root: Path, cliente: str | None = None,
                   dominio: str | None = None, *, json_files=None, txt_files=None) -> str:
    """Procesa una carpeta de artefactos → escribe flujo + vistas, preservando
    el bloque `manual:` de los .md existentes (no pisa la curaduría del senior).
    cliente/dominio se pueden pasar explícitos (los calcula generate_all según la
    profundidad real); si no, se infieren de la ruta (<Cliente>/<Dominio>).

    Pool unificado (export CLI): si se pasan `json_files`/`txt_files`, el pool se
    arma sobre TODAS las carpetas-tipo del (cliente, dominio) — no sobre un solo
    folder — así el linaje resuelve upstreams cross-folder. `src` queda como
    referencia de mensajes; cliente/dominio SIEMPRE vienen explícitos en ese modo.

    SEGMENTACIÓN: si la carpeta es 1-nivel (dominio 'General') y tiene ≥2 modelos
    analíticos (cubos), se emite UN dominio por cubo (Stock, Despachos…), cada uno con
    su linaje. Así los .json se dejan planos en SharePoint (dedup de dims compartidas)
    y el copiloto separa los dominios solo."""
    src, out_root = Path(src), Path(out_root)
    if cliente is None:
        cliente = src.parent.name.replace("_", " ")
    if dominio is None:
        dominio = src.name
    if json_files is not None:
        entities, _ = load_folder(json_files=json_files, txt_files=txt_files or [])
    else:
        entities, _ = load_folder(src)
    if not entities:
        return f"[skip] {src.name}: sin entidades"
    cli_dir = out_root / cliente.replace(" ", "_")
    cubes = [e for e in entities.values() if _is_cube(e)]

    if dominio in (None, "General") and len(cubes) >= 2:
        # Un dominio por modelo analítico. Se limpia el flujo 'General' merged viejo
        # (migración) para que no queden dominios duplicados en el dropdown.
        old = cli_dir / "General"
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        lines, used = [], set()
        for cube in sorted(cubes, key=lambda c: c.name):
            sub = _cube_subgraph(entities, cube)
            dom = _domain_for_cube(cube, sub)
            folder = _slug_domain(dom)
            if folder in used:                       # colisión de nombre → desambiguar
                folder = f"{folder}_{_slug_domain(cube.name)}"
            used.add(folder)
            lines.append("  · " + _write_flow(cli_dir / folder, cliente, dom, sub, folder))
        # Limpieza de dominios viejos del mismo cliente. Cuando una carpeta plana
        # pasa a segmentarse por modelos analíticos, las salidas anteriores
        # quedan físicamente en catalog_local y el dropdown las sigue levantando.
        # Solo tocamos carpetas de dominio generadas por este catálogo (tienen
        # flujo_*.md directo); no toca vistas, APIs, arquitectura ni seguridad.
        for child in cli_dir.iterdir() if cli_dir.exists() else []:
            if child.is_dir() and child.name not in used and any(child.glob("flujo_*.md")):
                shutil.rmtree(child, ignore_errors=True)
        return f"[OK] {cliente}: {len(cubes)} dominios (por modelo analítico):\n" + "\n".join(lines)

    # Un solo flujo (carpeta con dominio real 2-niveles, o ≤1 cubo).
    dom_final = dominio if (dominio and dominio != "General") else _infer_domain(entities)
    folder = dominio.replace(" ", "_") if (dominio and dominio != "General") else _slug_domain(dom_final)
    if dominio == "General":
        for child in cli_dir.iterdir() if cli_dir.exists() else []:
            if child.is_dir() and child.name != folder and any(child.glob("flujo_*.md")):
                shutil.rmtree(child, ignore_errors=True)
    return "[OK] " + _write_flow(cli_dir / folder, cliente, dom_final, entities, folder)


# ── Caché de descargas SharePoint (incremental por cTag/eTag) ──────────────
# El cuello de botella de "Regenerar" es la descarga de Graph (una GET por archivo),
# no la generación local. Guardamos los crudos en un caché PERSISTENTE y un manifest
# con el tag remoto de cada archivo; en la próxima descarga, si el tag no cambió y el
# crudo sigue en caché, NO se vuelve a bajar. El manifest NO lleva extensión .json
# para que los generadores (que globean *.json/*.csv/*.xlsx) no lo levanten como fuente.
CSN_DOWNLOAD_MANIFEST = ".download_manifest"


def _remote_tag(item: dict) -> str:
    """Firma remota de un driveItem: cTag (cambia con el contenido) → eTag → tamaño
    + última modificación. Es lo que comparamos para decidir si re-descargar."""
    return str(item.get("cTag") or item.get("eTag")
               or f"{item.get('size')}:{item.get('lastModifiedDateTime')}")


def _download_manifest_path(cache_root) -> Path:
    return Path(cache_root) / CSN_DOWNLOAD_MANIFEST


def _load_download_manifest(cache_root) -> dict:
    p = _download_manifest_path(cache_root)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_download_manifest(cache_root, manifest: dict) -> None:
    p = _download_manifest_path(cache_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def fetch_shared_file(sharing_url: str, token: str, dest_dir, *, fallback_name: str = "",
                      cache_root=None) -> str:
    """Descarga un archivo individual compartido por SharePoint/Graph /shares.

    Se usa para activos técnicos que no cuelgan de la carpeta CSN principal
    (por ejemplo, matrices Excel de seguridad). Conserva el nombre del archivo
    original cuando Graph lo informa y escribe binario para no corromper .xlsx.
    Si se pasa `cache_root`, es incremental: si el tag remoto no cambió y el archivo
    ya está en caché, no lo vuelve a bajar (usa el bloque `shared` del manifest)."""
    import base64
    import urllib.request
    import catalog_service as cs

    dest = Path(dest_dir)
    enc = "u!" + base64.urlsafe_b64encode(sharing_url.strip().encode()).decode().rstrip("=")
    item = cs._graph_get_json(
        f"https://graph.microsoft.com/v1.0/shares/{enc}/driveItem", token)
    name = str(item.get("name") or fallback_name or "sharepoint_asset.xlsx")
    target = dest / name
    tag = _remote_tag(item)

    if cache_root is not None:
        root = Path(cache_root)
        manifest = _load_download_manifest(root)
        shared = dict(manifest.get("shared") or {})
        try:
            rel = target.relative_to(root).as_posix()
        except ValueError:
            rel = name
        if target.exists() and shared.get(rel) == tag:
            return f"Activo SharePoint sin cambios (caché): {name}"

    durl = item.get("@microsoft.graph.downloadUrl")
    if not durl:
        raise ValueError(f"SharePoint no devolvió downloadUrl para {name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(durl), timeout=30) as response:
        target.write_bytes(response.read())

    if cache_root is not None:
        shared[rel] = tag
        manifest["shared"] = shared
        _save_download_manifest(cache_root, manifest)
    return f"Activo SharePoint descargado: {target.name}"


def fetch_csn_folder(sharing_url: str, token: str, dest_dir) -> str:
    """Descarga el árbol de la carpeta CSN de SharePoint (resuelta por su link de
    compartir vía Graph /shares) a `dest_dir`, preservando la estructura
    <Cliente>/<Dominio>/*.json. INCREMENTAL: `dest_dir` es un caché persistente; con
    el manifest de descargas (`.download_manifest`, bloque `folder`) solo baja los
    archivos cuyo tag remoto (cTag/eTag) cambió, reusa de caché los que no, y borra
    los que desaparecieron en SharePoint. Reusa los helpers Graph de catalog_service."""
    import base64
    import urllib.request
    import catalog_service as cs
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    manifest = _load_download_manifest(dest)
    folder_prev = dict(manifest.get("folder") or {})
    folder_now: dict[str, str] = {}
    downloaded = reused = 0

    enc = "u!" + base64.urlsafe_b64encode(sharing_url.strip().encode()).decode().rstrip("=")
    root = cs._graph_get_json(
        f"https://graph.microsoft.com/v1.0/shares/{enc}/driveItem?$select=id,parentReference", token)
    if not isinstance(root, dict):
        raise TypeError(f"Respuesta Graph inesperada para carpeta raíz: {type(root).__name__}")
    drive = root["parentReference"]["driveId"]
    stack = [(root["id"], Path())]
    while stack:
        item_id, rel = stack.pop()
        url = f"https://graph.microsoft.com/v1.0/drives/{drive}/items/{item_id}/children"
        while url:
            payload = cs._graph_get_json(url, token)
            if isinstance(payload, list):
                items = payload
                next_url = None
            elif isinstance(payload, dict):
                items = payload.get("value", [])
                next_url = payload.get("@odata.nextLink")
            else:
                raise TypeError(f"Respuesta Graph inesperada para children: {type(payload).__name__}")
            for it in items:
                if not isinstance(it, dict):
                    continue
                name = it.get("name", "")
                if it.get("folder"):
                    stack.append((it["id"], rel / name))
                    continue
                if not (it.get("file") and name.lower().endswith((".json", ".txt", ".md", ".csv", ".xlsx"))):
                    continue
                relpath = (rel / name).as_posix()
                tag = _remote_tag(it)
                target = dest / rel / name
                # Reuso de caché: mismo tag y el crudo sigue en disco → no bajar.
                if target.exists() and folder_prev.get(relpath) == tag:
                    folder_now[relpath] = tag
                    reused += 1
                    continue
                durl = it.get("@microsoft.graph.downloadUrl")
                if not durl:
                    # Sin URL de descarga: si hay copia en caché, la conservamos con
                    # su tag previo (fuerza reintento la próxima); si no, se omite.
                    if target.exists():
                        folder_now[relpath] = folder_prev.get(relpath, "")
                        reused += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if name.lower().endswith(".xlsx"):
                    with urllib.request.urlopen(urllib.request.Request(durl), timeout=30) as response:
                        target.write_bytes(response.read())
                else:
                    target.write_text(cs._download_text(durl), encoding="utf-8")
                folder_now[relpath] = tag
                downloaded += 1
            url = next_url

    # Prune: archivos que estaban en el caché (bloque folder) y ya no están en SharePoint.
    pruned = 0
    for old_rel in folder_prev:
        if old_rel not in folder_now:
            stale = dest / old_rel
            try:
                if stale.is_file():
                    stale.unlink()
                    pruned += 1
            except OSError:
                pass
    manifest["folder"] = folder_now
    _save_download_manifest(dest, manifest)

    bits = [f"{downloaded} descargado(s)"]
    if reused:
        bits.append(f"{reused} sin cambios (caché)")
    if pruned:
        bits.append(f"{pruned} eliminado(s)")
    return ", ".join(bits) + " — SharePoint/CSN"


CATALOG_MANIFEST = ".catalog_manifest.json"
CATALOG_SOURCE_EXTENSIONS = {".json", ".txt"}
CATALOG_IGNORED_DIRS = {"api", "apis", "cds", "espacios", "conexiones", "seguridad", "data products", "data_products"}
CATALOG_RESERVED_OUTPUT_DIRS = {"apis", "arquitectura", "seguridad", "data_products", ".csn_cache"}


def _catalog_dir_kind(name: str) -> str:
    """Normaliza carpetas SharePoint con prefijos visuales: `02. Data Products`."""
    clean = re.sub(r"^[^\w\d]+", "", str(name)).strip()
    clean = re.sub(r"^\s*\d+\s*[\.\-_]\s*", "", clean).strip().lower()
    return clean.replace("-", " ").replace("_", " ")


# Carpetas de TIPO de artefacto del export CLI de Datasphere: FÍSICAS (provenance),
# NUNCA dominios (contrato owner §1/§2). Se reconocen por ALLOWLIST (normalizadas
# con `_catalog_dir_kind` — que ya pasa "-"/"_" a espacio y lowercasea) y se
# ELIMINAN del camino al resolver (cliente, dominio). Una carpeta desconocida NO
# se reinterpreta como tipo (y por lo tanto no se aplana): sigue siendo un nivel de
# dominio como hoy — nunca se ingiere con `rglob` irrestricto (§6).
CATALOG_ARTIFACT_TYPE_DIRS = {
    "data access controls", "task chains", "transformation flows", "data flows",
    "remote tables", "analytic models", "local tables", "replication flows", "views",
}


def _is_artifact_type_dir(name: str) -> bool:
    return _catalog_dir_kind(name) in CATALOG_ARTIFACT_TYPE_DIRS


def _client_domain_for(rel_parts) -> tuple[str, str] | None:
    """(cliente, dominio) de un `.json` relativo a `inbound_root`, QUITANDO un
    segmento FINAL de carpeta-tipo (físico, no semántico). `None` si el `.json` no
    cuelga de un cliente. Contrato owner §2:
      <cli>/A.json              → (cli, "General")
      <cli>/views/A.json        → (cli, "General")   (carpeta-tipo eliminada)
      <cli>/<dom>/A.json        → (cli, <dom>)
      <cli>/<dom>/views/A.json  → (cli, <dom>)        (carpeta-tipo eliminada)
    Nunca infiere `views`/`local-tables`/… como nombre de dominio."""
    dirs = list(rel_parts[:-1])
    if dirs and _is_artifact_type_dir(dirs[-1]):
        dirs = dirs[:-1]
    if not dirs:
        return None
    cliente = dirs[0]
    dominio = dirs[1] if len(dirs) >= 2 else "General"
    return cliente, dominio


def _catalog_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _catalog_generator_signature() -> str:
    """Firma del generador. Si cambia el script, no reutilizamos salidas viejas."""
    try:
        return _catalog_sha256(Path(__file__))
    except Exception:
        return "unknown"


def _catalog_manifest_path(out_root: Path) -> Path:
    return Path(out_root) / CATALOG_MANIFEST


def _load_catalog_manifest(out_root: Path) -> dict:
    path = _catalog_manifest_path(out_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_catalog_manifest(out_root: Path, manifest: dict) -> None:
    path = _catalog_manifest_path(out_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _catalog_leaf_key(leaf: Path, inbound_root: Path) -> str:
    return leaf.relative_to(inbound_root).as_posix()


def _files_signature(files, inbound_root: Path) -> dict:
    """Firma (sha256 + tamaño + ruta) de una LISTA explícita de archivos — la
    identidad estable de un grupo (cliente, dominio) del pool unificado, que puede
    abarcar varias carpetas-tipo. MISMO criterio que `_catalog_leaf_signature` (sin
    mtime: la descarga va a un temp nuevo cada vez)."""
    out = []
    for path in sorted(f for f in files if f.is_file()):
        if path.suffix.lower() not in CATALOG_SOURCE_EXTENSIONS:
            continue
        stat = path.stat()
        out.append({
            "path": path.relative_to(inbound_root).as_posix(),
            "size": stat.st_size,
            "sha256": _catalog_sha256(path),
        })
    return {"files": out}


def _catalog_leaf_signature(leaf: Path, inbound_root: Path) -> dict:
    files = []
    for path in sorted(p for p in leaf.rglob("*") if p.is_file()):
        if path.suffix.lower() not in CATALOG_SOURCE_EXTENSIONS:
            continue
        stat = path.stat()
        # OJO: NO incluir mtime — la descarga de SharePoint va a un temp nuevo cada
        # vez, así que el mtime siempre cambia aunque el contenido sea idéntico. La
        # identidad estable del archivo es su sha256 (contenido) + ruta relativa.
        files.append({
            "path": path.relative_to(inbound_root).as_posix(),
            "size": stat.st_size,
            "sha256": _catalog_sha256(path),
        })
    return {"files": files}


def _catalog_outputs_exist(out_root: Path, cliente: str, dominio: str) -> bool:
    out_dir = Path(out_root) / cliente.replace(" ", "_") / dominio.replace(" ", "_")
    return (out_dir / "vistas").is_dir() and (out_dir / f"flujo_{dominio}.md").exists()


def _prune_stale_domain_outputs(
    out_root: Path,
    expected_dirs: set[tuple[str, str]],
    explicit_client_dirs: set[str],
    current_client_dirs: set[str],
) -> int:
    """Elimina salidas viejas de dominios que ya no existen en el inbound actual.

    La UI lee `catalog_local`, por lo que un dominio eliminado o reagrupado en
    SharePoint seguiría apareciendo si dejamos su markdown viejo. La poda es
    deliberadamente estrecha: solo elimina subcarpetas de cliente que tienen un
    `flujo_*.md` directo, que es la firma de salida de este generador.
    """
    out_root = Path(out_root)
    if not out_root.exists():
        return 0
    pruned = 0
    for client_dir in sorted(p for p in out_root.iterdir() if p.is_dir()):
        client_key = client_dir.name
        if client_key.lower() in CATALOG_RESERVED_OUTPUT_DIRS or client_key.startswith("."):
            continue
        remove_all_client_domains = client_key not in current_client_dirs
        prune_to_expected = client_key in explicit_client_dirs
        if not remove_all_client_domains and not prune_to_expected:
            continue
        for domain_dir in sorted(p for p in client_dir.iterdir() if p.is_dir()):
            if not any(domain_dir.glob("flujo_*.md")):
                continue
            domain_key = domain_dir.name
            if remove_all_client_domains or (client_key, domain_key) not in expected_dirs:
                shutil.rmtree(domain_dir, ignore_errors=True)
                pruned += 1
        try:
            if not any(client_dir.iterdir()):
                client_dir.rmdir()
        except OSError:
            pass
    return pruned

def _sync_apis(inbound_root: Path, out_root: Path) -> int:
    """Copia el código de las CDS views (extractores) desde la carpeta compartida
    del origen (APIs/ o CDS/, a nivel raíz) a <out>/APIs. Es agnóstica de cliente:
    una sola copia por CDS view, referenciada por el nombre del extractor
    (ABAP_CDS.<X> → <X>.md). Devuelve la cantidad copiada."""
    src = next((
        p for p in sorted(inbound_root.iterdir() if inbound_root.is_dir() else [])
        if p.is_dir() and _catalog_dir_kind(p.name) in {"api", "apis", "cds"}
    ), None)
    if src is None:
        return 0
    dest = Path(out_root) / "APIs"
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for md in src.glob("*.md"):
        (dest / md.name).write_text(md.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        count += 1
    return count


def generate_all(inbound_root, out_root) -> str:
    """Procesa las carpetas hoja con .json bajo inbound_root.

    Usa un manifest local por flujo para evitar regenerar dominios sin cambios.
    Si cambia este generador, se invalida el manifest completo para no dejar
    markdown viejo con reglas metodológicas anteriores.
    """
    inbound_root = Path(inbound_root)
    out_root = Path(out_root)
    # Agrupar los .json por (cliente, dominio) tras QUITAR las carpetas-tipo del
    # export CLI (contrato owner §4: pool unificado a nivel cliente/dominio).
    # Legacy (<cli>/*.json, <cli>/<dom>/*.json) produce EXACTAMENTE los mismos
    # grupos que antes; el CLI junta las carpetas-tipo de un mismo dominio en UN
    # solo pool para que el linaje resuelva upstreams cross-folder. Sólo se
    # ingieren carpetas legacy + carpetas-tipo de la allowlist: una carpeta
    # DESCONOCIDA no se aplana (sigue siendo un nivel de dominio, como hoy) y las
    # especiales (espacios/conexiones/…) siguen excluidas por CATALOG_IGNORED_DIRS.
    def _eligible(rel_parts) -> bool:
        return (not any(_catalog_dir_kind(part) in CATALOG_IGNORED_DIRS for part in rel_parts[:-1])
                and not (rel_parts and rel_parts[0].upper().startswith("DP_")))

    groups: dict[tuple[str, str], dict] = {}
    for p in inbound_root.rglob("*.json"):
        parts = p.relative_to(inbound_root).parts
        if not _eligible(parts):
            continue
        cd = _client_domain_for(parts)
        if cd is None:
            continue
        groups.setdefault(cd, {"jsons": [], "txts": []})["jsons"].append(p)
    for p in inbound_root.rglob("*.txt"):
        parts = p.relative_to(inbound_root).parts
        if not _eligible(parts):
            continue
        cd = _client_domain_for(parts)
        if cd is not None and cd in groups:
            groups[cd]["txts"].append(p)
    if not groups:
        return f"Sin .json bajo {inbound_root}. Nada que generar."

    generator_sig = _catalog_generator_signature()
    manifest = _load_catalog_manifest(out_root)
    previous_sig = manifest.get("generator_sha256")
    force_all = previous_sig != generator_sig
    entries = manifest.get("flows", {}) if not force_all else {}

    lines = []
    generated = 0
    skipped = 0
    next_entries = {}
    expected_dirs: set[tuple[str, str]] = set()
    explicit_client_dirs: set[str] = set()
    current_client_dirs: set[str] = set()

    for (cliente_dir, dominio_dir) in sorted(groups):
        g = groups[(cliente_dir, dominio_dir)]
        cliente = cliente_dir.replace("_", " ")
        dominio = dominio_dir.replace("_", " ")
        client_dir_key = cliente.replace(" ", "_")
        current_client_dirs.add(client_dir_key)
        if dominio != "General":
            explicit_client_dirs.add(client_dir_key)
            expected_dirs.add((client_dir_key, dominio.replace(" ", "_")))
        key = f"{cliente_dir}/{dominio_dir}"
        signature = _files_signature(g["jsons"] + g["txts"], inbound_root)
        previous = entries.get(key, {})

        if (not force_all
                and previous.get("signature") == signature
                and _catalog_outputs_exist(out_root, cliente, dominio)):
            skipped += 1
            next_entries[key] = previous
            continue

        # `src` es sólo referencia de mensajes; cliente/dominio van explícitos y el
        # pool se arma con la lista de archivos del grupo (todas sus carpetas-tipo).
        src = inbound_root / cliente_dir / dominio_dir
        lines.append(process_folder(src, out_root, cliente, dominio,
                                    json_files=sorted(g["jsons"]), txt_files=sorted(g["txts"])))
        generated += 1
        next_entries[key] = {
            "cliente": cliente,
            "dominio": dominio,
            "signature": signature,
        }

    apis = _sync_apis(inbound_root, out_root)
    pruned = _prune_stale_domain_outputs(out_root, expected_dirs, explicit_client_dirs, current_client_dirs)
    next_manifest = dict(manifest)
    next_manifest.update({
        "generator_sha256": generator_sig,
        "flows": next_entries,
    })
    _save_catalog_manifest(out_root, next_manifest)

    if not lines:
        lines.append("[OK] Sin cambios en flujos; se conserva el catálogo generado.")
    if force_all and previous_sig:
        lines.insert(0, "[info] Cambió el generador; se regeneraron los flujos para evitar salidas viejas.")

    if pruned:
        lines.append(f"[limpieza] {pruned} salida(s) de dominio obsoleta(s) removida(s).")
    tail = f"\n+ {apis} código(s) CDS (APIs/) sincronizado(s)." if apis else ""
    summary = f"Catálogo regenerado ({generated} flujo(s), {skipped} sin cambios):"
    return summary + "\n" + "\n".join(lines) + tail

def main() -> None:
    ap = argparse.ArgumentParser(description="Generador del catálogo de vistas CDS Danwor (piloto)")
    ap.add_argument("--in", dest="inp", help="Carpeta <Cliente>/<Dominio> con los .json/.txt")
    ap.add_argument("--all", dest="inbound", help="Raíz de entrada (procesa todas las <Cliente>/<Dominio>)")
    ap.add_argument("--out", dest="out", required=True, help="Carpeta de salida")
    args = ap.parse_args()
    if args.inbound:
        print(generate_all(args.inbound, args.out))
    elif args.inp:
        print(process_folder(Path(args.inp), Path(args.out)))
    else:
        ap.error("Indicá --in <carpeta> o --all <raíz>")


if __name__ == "__main__":
    main()
