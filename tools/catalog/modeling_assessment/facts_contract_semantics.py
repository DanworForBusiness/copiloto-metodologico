"""Hechos del CONTRATO PUBLICADO del Modelo Analitico (S2, evidence release). Solo HECHOS descriptivos: ningun conteo es un proxy de calidad.

La pregunta que estos hechos permiten responder DESPUES (no aqui): ¿que tan autonomo y coherente es el contrato semantico que se publica al consumo?

Hechos (subject = AM terminal):
  am.association_semantics  A/B  por asociacion: destino, identidad del destino, rol DECLARADO del destino, CATEGORIA y su base explicita
        (BUSINESS_DIMENSION | TIME_PLATFORM_DIMENSION | OTHER_PLATFORM | UNKNOWN); agregados (total, por categoria, destinos distintos) y las dimensiones
        declaradas del cierre que el AM NO expone como asociacion. La categoria se decide por evidencia EXPLICITA (identidad de plataforma
        probada, rol de dimension declarado por el export), nunca por el nombre tecnico. El NUMERO de asociaciones no se puntua.
  am.field_semantics        A/B  por campo/medida publicado: nombre tecnico, etiqueta, rol, tipo de dato (familia, precision, escala), si la etiqueta es
        igual al nombre tecnico y si el nombre coincide EXACTAMENTE con una columna de una fuente remota (de la clausura o de la cadena de procedencia);
        mas la COMPOSICION DE TIPOS de las medidas. La heterogeneidad de tipos de medida es la norma en los pools reales (24 de 29 AM con >= 2 medidas):
        es un hecho descriptivo, no un defecto.

No se introduce ningun clasificador de "nombre opaco" por forma (regex): la forma de un nombre (``ALNUM_4_6``) mezcla codigos de la fuente con palabras de
negocio. La evidencia disponible es objetiva y verificable: igualdad con el nombre tecnico y coincidencia exacta con una columna remota.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import contract as C
from . import naming_rules as R
from .boundary import resolve_physical_boundaries
from .facts_support import entity_refs, make_fact, sort_facts
from .facts_source import source_state_of
from .graph import EdgeKind
from .identity import IdentityClassification, classify
from .model import Area, EvidenceClass, Fact
from .provenance import ASSESSED, resolve_leaf_provenance
from .scope import AssessmentUniverse

AM_ = Area.ANALYTIC_MODEL

CAT_BUSINESS, CAT_TIME_PLATFORM, CAT_OTHER_PLATFORM, CAT_UNKNOWN = "BUSINESS_DIMENSION", "TIME_PLATFORM_DIMENSION", "OTHER_PLATFORM", "UNKNOWN"
ASSOCIATION_CATEGORIES = (CAT_BUSINESS, CAT_TIME_PLATFORM, CAT_OTHER_PLATFORM, CAT_UNKNOWN)
BASIS_SYSTEM_TIME = "SYSTEM_IDENTITY_SAP_TIME_NAMESPACE"
BASIS_SYSTEM_OTHER = "SYSTEM_IDENTITY"
BASIS_DECLARED_DIMENSION = "DECLARED_DIMENSION_ROLE"
BASIS_NO_DIMENSION_ROLE = "NO_DECLARED_DIMENSION_ROLE"
BASIS_TARGET_NOT_IN_GRAPH = "TARGET_NOT_IN_GRAPH"
CATEGORIZATION_BASES = (BASIS_SYSTEM_TIME, BASIS_SYSTEM_OTHER, BASIS_DECLARED_DIMENSION, BASIS_NO_DIMENSION_ROLE, BASIS_TARGET_NOT_IN_GRAPH)
TIME_PLATFORM_PREFIX = "SAP.TIME."

ROLE_MEASURE, ROLE_KEY, ROLE_ASSOCIATION_KEY, ROLE_ATTRIBUTE = "MEASURE", "KEY", "ASSOCIATION_KEY", "ATTRIBUTE"
FIELD_ROLES = (ROLE_MEASURE, ROLE_KEY, ROLE_ASSOCIATION_KEY, ROLE_ATTRIBUTE)

FAM_DECIMAL, FAM_DECIMAL_FLOAT, FAM_FLOAT, FAM_INTEGER, FAM_STRING, FAM_TEMPORAL, FAM_BOOLEAN, FAM_BINARY, FAM_OTHER, FAM_UNRESOLVED = (
    "DECIMAL", "DECIMAL_FLOAT", "FLOAT", "INTEGER", "STRING", "TEMPORAL", "BOOLEAN", "BINARY", "OTHER", "UNRESOLVED")
DATATYPE_FAMILIES = (FAM_DECIMAL, FAM_DECIMAL_FLOAT, FAM_FLOAT, FAM_INTEGER, FAM_STRING, FAM_TEMPORAL, FAM_BOOLEAN, FAM_BINARY, FAM_OTHER, FAM_UNRESOLVED)
_FAMILY_OF_TYPE = {
    "decimal": FAM_DECIMAL, "smalldecimal": FAM_DECIMAL, "decimalfloat": FAM_DECIMAL_FLOAT, "double": FAM_FLOAT, "float": FAM_FLOAT, "real": FAM_FLOAT,
    "integer": FAM_INTEGER, "int16": FAM_INTEGER, "int32": FAM_INTEGER, "int64": FAM_INTEGER, "integer64": FAM_INTEGER, "smallint": FAM_INTEGER, "tinyint": FAM_INTEGER,
    "bigint": FAM_INTEGER, "uint8": FAM_INTEGER, "string": FAM_STRING, "largestring": FAM_STRING, "varchar": FAM_STRING, "date": FAM_TEMPORAL, "time": FAM_TEMPORAL,
    "timestamp": FAM_TEMPORAL, "datetime": FAM_TEMPORAL, "boolean": FAM_BOOLEAN, "binary": FAM_BINARY, "largebinary": FAM_BINARY, "uuid": FAM_OTHER,
}
_MAX_LISTED_SOURCES = 5


def datatype_of(declared_type: str | None, length: str | None) -> dict:
    """``{declared_type, family, precision, scale, length_text}``. Precision/escala solo para las familias con precision declarada (``Decimal`` = ``p,s``)."""
    fam = _FAMILY_OF_TYPE.get(str(declared_type or "").lower(), FAM_OTHER) if declared_type else FAM_UNRESOLVED
    precision = scale = None
    text = str(length) if length not in (None, "") else None
    if fam == FAM_DECIMAL and text and "," in text:
        a, _, b = text.partition(",")
        if a.strip().isdigit() and b.strip().isdigit():
            precision, scale = int(a), int(b)
    return {"declared_type": declared_type or None, "family": fam, "precision": precision, "scale": scale, "length_text": text}


def remote_source_columns(universe: AssessmentUniverse, entities: Mapping[str, Any], root: str, evidence: Any | None) -> dict[str, list[str]]:
    """Columna (nombre EXACTO) -> artefactos remotos que la tienen, entre los remotos de la clausura y los bordes remotos que revela la cadena de procedencia
    de las hojas locales. Evidencia verificable (igualdad exacta de nombre), no una heuristica de forma."""
    graph = universe.graph
    closure = universe.closure_of(root)
    remote: set[str] = {n for n in closure if getattr(entities[n], "remote_entity", "")}
    if evidence is not None and hasattr(evidence, "target_flows"):
        for n in closure:
            e = entities[n]
            if graph.providers(n) or getattr(e, "remote_entity", ""):
                continue
            rec = resolve_leaf_provenance(n, evidence, entities)
            if rec["status"] == ASSESSED and rec["producer_flows"]:
                pb = resolve_physical_boundaries(n, rec, evidence, entities, graph, closure, lambda re: source_state_of(re).value)
                remote |= {b["boundary_entity"] for b in pb["boundaries"] if b["boundary_entity"]}
    out: dict[str, list[str]] = {}
    for art in sorted(remote):
        for f in (getattr(entities[art], "fields", None) or []):
            out.setdefault(f["campo"], []).append(art)
    return out


def _association_category(target: str, known: bool, ident: IdentityClassification | None, role: dict | None) -> tuple[str, str]:
    if ident is not None and ident.identity.value == "SYSTEM":
        return (CAT_TIME_PLATFORM, BASIS_SYSTEM_TIME) if target.startswith(TIME_PLATFORM_PREFIX) else (CAT_OTHER_PLATFORM, BASIS_SYSTEM_OTHER)
    if not known:
        return CAT_UNKNOWN, BASIS_TARGET_NOT_IN_GRAPH
    if role is not None and role["declares_dimension"]:
        return CAT_BUSINESS, BASIS_DECLARED_DIMENSION
    return CAT_UNKNOWN, BASIS_NO_DIMENSION_ROLE


def published_contract_facts(
    universe: AssessmentUniverse,
    entities: Mapping[str, Any],
    am: str,
    *,
    identity: Mapping[str, IdentityClassification] | None = None,
    evidence: Any | None = None,
) -> tuple[Fact, ...]:
    if universe.root_type_of(am) != "analytic-model":
        return ()                                                  # contrato del AM: las superficies quedan NOT_APPLICABLE para otras raices
    graph = universe.graph
    closure = universe.closure_of(am)
    ident = identity or {n: classify(entities[n]) for n in closure}
    e = entities[am]
    facts: list[Fact] = []

    # ---- asociaciones: categoria con base explicita -----------------------------------------------------------------------------------------------------
    recs, targets = [], set()
    fks = {}
    for f in C.field_records(e):
        if f["association_target"]:
            fks.setdefault(f["association_target"], []).append(f["field"])
    for a in C.association_records(e):
        t = a["target"]
        known = bool(t) and graph.has_node(t)
        te = entities.get(t) if known else None
        role = C.declared_role(te) if te is not None else None
        i = ident.get(t) or (classify(te) if te is not None else None)
        cat, basis = _association_category(t or "", known, i, role)
        targets.add(t)
        recs.append({"field": a.get("field"), "target": t, "target_in_graph": known, "target_identity": i.identity.value if i is not None else None,
                     "target_declared_role": role, "category": cat, "categorization_basis": basis, "foreign_key_fields": sorted(fks.get(t, []))})
    by_cat = {c: sum(1 for r in recs if r["category"] == c) for c in ASSOCIATION_CATEGORIES}
    tcat: dict[str, set] = {c: set() for c in ASSOCIATION_CATEGORIES}
    for r in recs:
        tcat[r["category"]].add(r["target"])
    associated = {r["target"] for r in recs}
    dims_open = sorted(n for n in closure if n != am and n in entities and C.declared_role(entities[n])["declares_dimension"]
                       and not (n in ident and ident[n].identity.value == "SYSTEM") and n not in associated)
    facts.append(make_fact(
        "am.association_semantics", AM_, am,
        {"associations": recs, "aggregates": {"total": len(recs), "by_category": by_cat, "distinct_targets": len({r["target"] for r in recs}),
                                               "distinct_targets_by_category": {c: len(v) for c, v in tcat.items()}},
         "declared_dimensions_in_closure_not_associated": dims_open,
         "definition": "category from explicit evidence (platform identity / declared dimension role); the number of associations is not a quality measure"},
        EvidenceClass.B, refs=entity_refs(e, ["associations"])))

    # ---- campos y medidas publicados --------------------------------------------------------------------------------------------------------------------
    remote_cols = remote_source_columns(universe, entities, am, evidence)
    raw = {f["campo"]: f for f in (getattr(e, "fields", None) or [])}
    fields, sigs = [], {}
    n_measures = unresolved = 0
    for f in C.field_records(e):
        name = f["field"]
        lab = raw.get(name, {}).get("label")
        rel = R.label_relations(name, lab)
        role = ROLE_MEASURE if f["measure"] else ROLE_KEY if f["key"] else ROLE_ASSOCIATION_KEY if f["association_target"] else ROLE_ATTRIBUTE
        dt = datatype_of(f["type"], f["length"])
        srcs = remote_cols.get(name, [])
        fields.append({"technical_name": name, "label": rel["label"], "role": role, "datatype": dt, "business_name_equals_technical": rel["label_equals_field"],
                       "label_matches_technical_normalized": rel["label_matches_field_normalized"], "derived": f["derived"],
                       "name_equals_remote_source_column": bool(srcs), "remote_source_artifact_count": len(srcs), "remote_source_artifacts": srcs[:_MAX_LISTED_SOURCES]})
        if role == ROLE_MEASURE:
            n_measures += 1
            if dt["family"] == FAM_UNRESOLVED:
                unresolved += 1
            else:
                sigs[(dt["family"], dt["precision"], dt["scale"])] = sigs.get((dt["family"], dt["precision"], dt["scale"]), 0) + 1
    composition = {"measure_count": n_measures,
                   "signatures": [{"family": k[0], "precision": k[1], "scale": k[2], "count": v} for k, v in sorted(sigs.items(), key=lambda kv: (kv[0][0], kv[0][1] or -1, kv[0][2] or -1))],
                   "family_count": len({k[0] for k in sigs}), "signature_count": len(sigs), "unresolved_type_count": unresolved,
                   "definition": "descriptive composition of measure datatypes; heterogeneity is common and is not a defect"}
    facts.append(make_fact(
        "am.field_semantics", AM_, am,
        {"fields": fields, "measure_type_composition": composition,
         "counts": {"fields": len(fields), "measures": n_measures, "business_name_equals_technical": sum(1 for x in fields if x["business_name_equals_technical"]),
                    "name_equals_remote_source_column": sum(1 for x in fields if x["name_equals_remote_source_column"])}},
        EvidenceClass.B, refs=entity_refs(e, ["fields", "measures"])))
    return sort_facts(facts)
