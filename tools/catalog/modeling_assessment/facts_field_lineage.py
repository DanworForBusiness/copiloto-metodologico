"""Hechos de LINAJE DE CAMPO PUBLICADO y CLASE DE CONTRATO DE FUENTE por hoja (evidence release v3). Solo HECHOS de procedencia; NINGUN juicio de calidad.

``am.published_field_lineage`` (subject = AM raiz): para cada campo/medida publicado, el camino estructural hacia atras desde el AM, por las vistas del cierre (a traves de subselects
anidados, joins y uniones del query compilado), cruzando o no contratos de armonizacion, hasta las HOJAS frontera del cierre. Reutiliza el reader de queries (``readers`` /
``derivation``), la extension ``field_lineage`` (resolucion por columna) y el grafo/cierre existentes; no hay un segundo motor de linaje ni parseo de texto SQL.
  * ``completeness``: COMPLETE (todo camino llega a una hoja frontera o a un origen sin insumo de campo) · PARTIAL (algun camino llega y otro queda sin resolver) · UNRESOLVED (ningun
    camino llega) · AMBIGUOUS (algun salto tiene varios proveedores posibles que no se pueden desambiguar: NUNCA se elige uno). Es una propiedad EPISTEMICA (evaluabilidad), no de calidad.
  * ``lineage_paths``: hasta ``MAX_SAMPLE_PATHS`` caminos de muestra (nodos + aristas con tipo de mapeo); el total y las propiedades por hoja se calculan sobre TODOS los caminos.
  * ``frontier_leaves``: por hoja alcanzada, la clase de contrato de fuente y el caracter de los caminos que llegan a ella (solo identidad / identidad o renombre / con cast / con
    derivacion) y cuantos cruzan un contrato de armonizacion.

``source.contract_class`` (subject = hoja del cierre): clase EXPLICITA del contrato de fuente de cada hoja frontera, con la base de la clasificacion. Nunca se infiere gobierno ni
"crudo" de la tecnologia: una fuente no-SAP queda ``EXTERNAL_CONTRACT_UNDETERMINED`` salvo evidencia explicita.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from . import contract as C
from .boundary import resolve_physical_boundaries
from .facts_contract_semantics import datatype_of
from .facts_harmonization_contract import candidate_basis
from .facts_source import source_state_of
from .facts_support import entity_refs, make_fact, sort_facts
from .field_lineage import FIELD_LINEAGE_EXTRACTOR_VERSION, MT_CAST, MT_DERIVED, MT_EXPRESSION, MT_IDENTITY, MT_RENAME, Origin, resolve_query
from .graph import EdgeKind
from .identity import IdentityClassification, classify
from .model import Area, EvidenceClass
from .provenance import ASSESSED, PROV_DECLARED, resolve_leaf_provenance
from .readers import DELTA_SHADOW_SUFFIX
from .scope import AssessmentUniverse

# ───────────────────────────── vocabularios ─────────────────────────────
COMPLETE, PARTIAL, UNRESOLVED, AMBIGUOUS = "COMPLETE", "PARTIAL", "UNRESOLVED", "AMBIGUOUS"
COMPLETENESS_STATES = (COMPLETE, PARTIAL, UNRESOLVED, AMBIGUOUS)

PC_IDENTITY_ONLY, PC_IDENTITY_OR_RENAME, PC_WITH_CAST, PC_WITH_DERIVATION = "IDENTITY_ONLY", "IDENTITY_OR_RENAME", "WITH_CAST", "WITH_DERIVATION"
PATH_CHARACTERS = (PC_IDENTITY_ONLY, PC_IDENTITY_OR_RENAME, PC_WITH_CAST, PC_WITH_DERIVATION)
_RANK = {MT_IDENTITY: 0, MT_RENAME: 1, MT_CAST: 2, MT_DERIVED: 3, MT_EXPRESSION: 4}
_CHARACTER_OF_RANK = {0: PC_IDENTITY_ONLY, 1: PC_IDENTITY_OR_RENAME, 2: PC_WITH_CAST, 3: PC_WITH_DERIVATION, 4: PC_WITH_DERIVATION}

UNRESOLVED_REASONS = ("NO_QUERY_EVIDENCE", "FIELD_NOT_IN_PROVIDER_OUTPUT", "PROVIDER_OUTSIDE_POOL", "PROVIDER_OUTSIDE_CLOSURE", "CYCLE", "DEPTH_LIMIT")
PATH_BASIS = "COMPILED_QUERY_AST"
DELTA_BASIS = "DELTA_SHADOW_SUFFIX_NORMALIZATION"
MAX_SAMPLE_PATHS = 1
_MAX_DEPTH = 40

# clases de contrato de fuente por hoja
SCC_CDS, SCC_ODP_SAPI, SCC_ABAP_TABLE = "GOVERNED_SAP_CDS_CONTRACT", "GOVERNED_SAP_ODP_SAPI_CONTRACT", "PHYSICAL_ABAP_TABLE_BOUNDARY"
SCC_STD_VIEW, SCC_BW_BRIDGE = "SAP_STANDARD_VIEW_ON_PHYSICAL_TABLE", "SAP_BW_BRIDGE_VIRTUALIZATION"
SCC_EXT_GOVERNED, SCC_EXT_PHYSICAL, SCC_EXT_UNDETERMINED = "GOVERNED_EXTERNAL_CONTRACT", "EXTERNAL_PHYSICAL_OR_TECHNICAL", "EXTERNAL_CONTRACT_UNDETERMINED"
SCC_PLATFORM, SCC_MIXED, SCC_UNKNOWN = "PLATFORM_PROVIDED", "MIXED_BOUNDARY_CLASSES", "UNKNOWN"
SOURCE_CONTRACT_CLASSES = (SCC_CDS, SCC_ODP_SAPI, SCC_ABAP_TABLE, SCC_STD_VIEW, SCC_BW_BRIDGE, SCC_EXT_GOVERNED, SCC_EXT_PHYSICAL, SCC_EXT_UNDETERMINED, SCC_PLATFORM, SCC_MIXED, SCC_UNKNOWN)
CB_NAMESPACE, CB_RF_CONTAINER, CB_CONNECTION_TYPE, CB_EXPLICIT_METADATA, CB_PLATFORM, CB_NO_NAMESPACE, CB_NO_PRODUCER, CB_NOT_ASSESSED, CB_CHAIN_INCOMPLETE, CB_MULTIPLE, CB_CONVERGING, CB_UNKNOWN = (
    "REMOTE_ENTITY_NAMESPACE", "RF_SOURCE_CONTAINER", "CONNECTION_TYPE", "EXPLICIT_SOURCE_METADATA", "PLATFORM_OBJECT", "NO_RECOGNIZED_NAMESPACE", "NO_ACQUIRED_PRODUCER",
    "PROVENANCE_NOT_ASSESSED", "PROVENANCE_CHAIN_INCOMPLETE", "MULTIPLE_BOUNDARY_CLASSES", "MULTIPLE_CONVERGING_BASES", "UNKNOWN")
CLASSIFICATION_BASES = (CB_NAMESPACE, CB_RF_CONTAINER, CB_CONNECTION_TYPE, CB_EXPLICIT_METADATA, CB_PLATFORM, CB_NO_NAMESPACE, CB_NO_PRODUCER, CB_NOT_ASSESSED, CB_CHAIN_INCOMPLETE, CB_MULTIPLE, CB_CONVERGING, CB_UNKNOWN)
LEAF_KINDS = ("REMOTE_IMPORTED", "LOCAL_FLOW_PRODUCED", "LOCAL_DECLARED_SOURCE", "LOCAL_NO_PRODUCER", "LOCAL_PROVENANCE_NOT_ASSESSED", "PLATFORM_OBJECT")

_CLASS_OF_SOURCE_STATE = {"CDS_VIEW": SCC_CDS, "SAPI_ODP": SCC_ODP_SAPI, "RAW_PHYSICAL_TABLE": SCC_ABAP_TABLE, "STANDARD_VIEW_ON_PHYSICAL_TABLE": SCC_STD_VIEW, "BW_BRIDGE": SCC_BW_BRIDGE}
_CLASS_OF_BOUNDARY = {"GOVERNED_CDS_CONTRACT": SCC_CDS, "GOVERNED_SAPI_EXTRACTOR": SCC_ODP_SAPI, "PHYSICAL_TABLE": SCC_ABAP_TABLE, "STANDARD_VIEW_ON_PHYSICAL_TABLE": SCC_STD_VIEW,
                      "BW_BRIDGE": SCC_BW_BRIDGE, "UNCLASSIFIED": SCC_EXT_UNDETERMINED}


# ───────────────────────────── clase de contrato de fuente por hoja ─────────────────────────────
def frontier_leaves(universe: AssessmentUniverse, entities: Mapping[str, Any], am: str) -> list[str]:
    """Hojas frontera del cierre: miembros sin proveedores semanticos de linaje (importadas remotas, locales con/sin productor, de plataforma). La raiz nunca lo es."""
    graph = universe.graph
    return [n for n in universe.closure_of(am) if n != am and n in entities and not graph.providers(n, [EdgeKind.LINEAGE])]


def _local_leaf_class(n: str, ev: Any, entities: Mapping[str, Any], graph: Any, closure: tuple[str, ...]) -> dict:
    rec = resolve_leaf_provenance(n, ev, entities)
    out: dict[str, Any] = {"leaf_kind": "LOCAL_PROVENANCE_NOT_ASSESSED", "contract_class": SCC_UNKNOWN, "classification_basis": CB_NOT_ASSESSED, "classification_bases": [],
                           "boundary_classes": [], "source_systems": [], "remote_entities": []}
    if rec["status"] == ASSESSED and rec.get("producer_flows"):
        pb = resolve_physical_boundaries(n, rec, ev, entities, graph, closure, lambda re: source_state_of(re).value)
        classes = sorted({_CLASS_OF_BOUNDARY.get(b["boundary_class"], SCC_EXT_UNDETERMINED) for b in pb["boundaries"]})
        bases = sorted({b["basis"] for b in pb["boundaries"]})
        out.update({"leaf_kind": "LOCAL_FLOW_PRODUCED", "boundary_classes": classes,
                    "source_systems": sorted({(s.get("connection_id"), s.get("connection_type"), s.get("container")) for s in ((rec.get("origin") or {}).get("systems") or [])}, key=str),
                    "remote_entities": sorted({b["remote_entity"] for b in pb["boundaries"] if b["remote_entity"]})})
        out["source_systems"] = [{"connection_id": a, "connection_type": b, "container": c} for a, b, c in out["source_systems"]]
        if not classes:
            out.update({"contract_class": SCC_UNKNOWN, "classification_basis": CB_CHAIN_INCOMPLETE, "classification_bases": [CB_CHAIN_INCOMPLETE]})
        elif len(classes) == 1:
            basis = bases[0] if len(bases) == 1 else CB_CONVERGING
            out.update({"contract_class": classes[0], "classification_basis": basis if basis in CLASSIFICATION_BASES else CB_UNKNOWN, "classification_bases": bases})
        else:
            out.update({"contract_class": SCC_MIXED, "classification_basis": CB_MULTIPLE, "classification_bases": bases})
        return out
    if rec["status"] == ASSESSED and rec.get("provenance_state") == PROV_DECLARED:
        out.update({"leaf_kind": "LOCAL_DECLARED_SOURCE", "contract_class": SCC_EXT_UNDETERMINED, "classification_basis": CB_EXPLICIT_METADATA, "classification_bases": [CB_EXPLICIT_METADATA]})
        return out
    if rec["status"] == ASSESSED:
        out.update({"leaf_kind": "LOCAL_NO_PRODUCER", "contract_class": SCC_UNKNOWN, "classification_basis": CB_NO_PRODUCER, "classification_bases": [CB_NO_PRODUCER]})
    return out


def source_contract_class_facts(universe: AssessmentUniverse, entities: Mapping[str, Any], am: str, *, identity: Mapping[str, IdentityClassification] | None = None,
                                evidence: Any | None = None) -> tuple:
    graph = universe.graph
    closure = universe.closure_of(am)
    ident = identity or {n: classify(entities[n]) for n in closure}
    facts = []
    for n in sorted(frontier_leaves(universe, entities, am)):
        e = entities[n]
        rec: dict[str, Any]
        if n in ident and ident[n].identity.value == "SYSTEM":
            rec = {"leaf_kind": "PLATFORM_OBJECT", "contract_class": SCC_PLATFORM, "classification_basis": CB_PLATFORM, "classification_bases": [CB_PLATFORM], "boundary_classes": [],
                   "source_systems": [], "remote_entities": []}
        elif getattr(e, "remote_entity", ""):
            state = source_state_of(e.remote_entity).value
            cls = _CLASS_OF_SOURCE_STATE.get(state, SCC_EXT_UNDETERMINED)
            basis = CB_NAMESPACE if cls != SCC_EXT_UNDETERMINED else CB_NO_NAMESPACE
            rec = {"leaf_kind": "REMOTE_IMPORTED", "contract_class": cls, "classification_basis": basis, "classification_bases": [basis], "boundary_classes": [], "source_systems": [],
                   "remote_entities": [e.remote_entity]}
        elif evidence is not None and hasattr(evidence, "target_flows"):
            rec = _local_leaf_class(n, evidence, entities, graph, closure)
        else:
            rec = {"leaf_kind": "LOCAL_PROVENANCE_NOT_ASSESSED", "contract_class": SCC_UNKNOWN, "classification_basis": CB_NOT_ASSESSED, "classification_bases": [], "boundary_classes": [],
                   "source_systems": [], "remote_entities": []}
        facts.append(make_fact("source.contract_class", Area.SOURCE_EXTRACTION, n,
                               {"leaf": n, **rec, "remote_connection": getattr(e, "remote_connection", None) or None,
                                "definition": "explicit source-contract class of the frontier leaf; technology alone never classifies governance or rawness"},
                               EvidenceClass.B, refs=entity_refs(e, ["remote_entity", "remote_connection"])))
    return sort_facts(facts)


# ───────────────────────────── linaje de campo publicado ─────────────────────────────
class _Sum:
    """Resumen de TODOS los caminos desde un nodo (entidad, campo) hacia abajo."""
    __slots__ = ("leaves", "unresolved", "ambiguous", "no_input", "crossed")

    def __init__(self) -> None:
        self.leaves: dict[tuple[str, str], Counter] = {}          # (hoja, campo) -> Counter[(rank, cruza_armonizacion)] = n caminos
        self.unresolved: Counter = Counter()
        self.ambiguous: Counter = Counter()
        self.no_input = 0
        self.crossed: set[str] = set()

    def total(self) -> int:
        return sum(sum(c.values()) for c in self.leaves.values()) + sum(self.unresolved.values()) + sum(self.ambiguous.values()) + self.no_input


def published_field_lineage_facts(universe: AssessmentUniverse, entities: Mapping[str, Any], am: str, *, identity: Mapping[str, IdentityClassification] | None = None,
                                  evidence: Any | None = None, leaf_classes: Mapping[str, dict] | None = None) -> tuple:
    if universe.root_type_of(am) != "analytic-model" or evidence is None:
        return ()
    closure = universe.closure_of(am)
    closure_set = set(closure)
    ident = identity or {n: classify(entities[n]) for n in closure}
    e_am = entities[am]
    candidates = {n for n in closure if candidate_basis(n, entities[n], am, ident)}
    classes = dict(leaf_classes) if leaf_classes is not None else {f.subject: f.value for f in source_contract_class_facts(universe, entities, am, identity=ident, evidence=evidence)}

    def canon(n: str) -> str:
        """Nombre del proveedor en el pool. La UNICA normalizacion conocida (la misma que ``readers.target_flows``): la tabla de captura de cambios ``X_Delta`` que un query referencia
        es la tabla local ``X`` del pool, solo si no existe una entidad homonima ``X_Delta``. Sin matching difuso."""
        if n not in entities and n.endswith(DELTA_SHADOW_SUFFIX) and n[: -len(DELTA_SHADOW_SUFFIX)] in entities:
            return n[: -len(DELTA_SHADOW_SUFFIX)]
        return n

    def fields_of(n: str) -> tuple[str, ...] | None:
        n = canon(n)
        return tuple(f["campo"] for f in (getattr(entities[n], "fields", None) or [])) if n in entities else None

    traces: dict[str, dict[str, list[Origin]] | None] = {}

    def trace(n: str) -> dict[str, list[Origin]] | None:
        if n not in traces:
            d = evidence.derivation_evidence(n)
            traces[n] = resolve_query(d.query_ir, fields_of) if d is not None and getattr(d, "query_ir", None) is not None else None
        return traces[n]

    memo: dict[tuple[str, str], _Sum] = {}

    def summary(n: str, f: str, stack: tuple[tuple[str, str], ...]) -> _Sum:
        key = (n, f)
        if key in memo:
            return memo[key]
        s = _Sum()
        if key in stack:
            s.unresolved["CYCLE"] += 1
            return s
        if len(stack) >= _MAX_DEPTH:
            s.unresolved["DEPTH_LIMIT"] += 1
            return s
        if n not in entities:
            s.unresolved["PROVIDER_OUTSIDE_POOL"] += 1
        elif n not in closure_set:
            s.unresolved["PROVIDER_OUTSIDE_CLOSURE"] += 1
        else:
            names = fields_of(n)
            if n in classes:                                                                          # hoja frontera del cierre (sin proveedores de linaje): el grafo manda, aunque
                if names is None or f in names:                                                       # su definicion traiga un query (p. ej. la tabla ``X`` que lee su sombra ``X_Delta``)
                    s.leaves[(n, f)] = Counter({(0, False): 1})
                else:
                    s.unresolved["FIELD_NOT_IN_PROVIDER_OUTPUT"] += 1
                memo[key] = s
                return s
            t = trace(n)
            if t is None:
                s.unresolved["NO_QUERY_EVIDENCE"] += 1
            elif f not in t:
                s.unresolved["FIELD_NOT_IN_PROVIDER_OUTPUT"] += 1
            else:
                is_cand = n in candidates
                for o in t[f]:
                    if o.ambiguity:
                        s.ambiguous[o.ambiguity] += 1
                        continue
                    if o.provider is None:
                        s.no_input += 1
                        continue
                    child = summary(canon(o.provider), o.field or f, stack + (key,))
                    rank = _RANK.get(o.mapping_type, 4)
                    for lk, cnt in child.leaves.items():
                        dst = s.leaves.setdefault(lk, Counter())
                        for (r, cr), m in cnt.items():
                            dst[(max(r, rank), cr or is_cand)] += m
                    s.unresolved.update(child.unresolved)
                    s.ambiguous.update(child.ambiguous)
                    s.no_input += child.no_input
                    s.crossed |= child.crossed
                if is_cand:
                    s.crossed.add(n)
        memo[key] = s
        return s

    def node_json(n: str, f: str) -> dict:
        e = entities.get(n)
        node: dict[str, Any] = {"artifact": n, "field": f}
        layer = C.layer_inference(e)["inferred_layer"] if e is not None else None
        if layer:
            node["layer"] = layer
        if n in candidates:
            node["harmonization_candidate"] = True
        if n in classes:
            node["frontier_leaf"] = True
        return node

    def sample_paths(f: str) -> list[dict]:
        out: list[dict] = []
        seen_leaves: set[str] = set()

        def dfs(n: str, fld: str, nodes: list[dict], edges: list[dict], rank: int, crossed: bool, stack: tuple) -> None:
            if len(out) >= MAX_SAMPLE_PATHS or len(nodes) > _MAX_DEPTH:
                return
            key = (n, fld)
            if key in stack:
                return
            t = trace(n) if (n in entities and n in closure_set and n not in classes) else None
            if n in classes:
                if n in seen_leaves:
                    return                                                                            # una muestra por hoja alcanzada
                seen_leaves.add(n)
                out.append({"nodes": list(nodes), "edges": list(edges), "character": _CHARACTER_OF_RANK[rank], "crosses_harmonization": crossed})
                return
            if t is None or fld not in t:
                return
            is_cand = n in candidates
            for o in t[fld]:
                prov = canon(o.provider) if o.provider else None
                if o.ambiguity or prov is None or prov not in entities or prov not in closure_set:
                    continue
                edge = {"mapping_type": o.mapping_type}
                if prov != o.provider:
                    edge["normalization"] = DELTA_BASIS                                              # el unico caso en que la arista no es una referencia directa del AST
                dfs(prov, o.field or fld, nodes + [node_json(prov, o.field or fld)], edges + [edge],
                    max(rank, _RANK.get(o.mapping_type, 4)), crossed or is_cand, stack + (key,))
                if len(out) >= MAX_SAMPLE_PATHS:
                    return

        dfs(am, f, [node_json(am, f)], [], 0, False, ())
        return out

    # ---- campos publicados ----------------------------------------------------------------------------------------------------------------------------
    am_trace = trace(am)
    rows = []
    counts = {s: 0 for s in COMPLETENESS_STATES}
    for rec in C.field_records(e_am):
        name = rec["field"]
        raw = next((x for x in (getattr(e_am, "fields", None) or []) if x["campo"] == name), {})
        role = "MEASURE" if rec["measure"] else "KEY" if rec["key"] else "ASSOCIATION_KEY" if rec["association_target"] else "ATTRIBUTE"
        s = summary(am, name, ())
        origins = (am_trace or {}).get(name) or []
        imm = [({"artifact": canon(o.provider), "field": o.field, "mapping_type": o.mapping_type} if o.provider else {"artifact": None, "field": None, "mapping_type": o.mapping_type,
                                                                                                                 "ambiguity": o.ambiguity}) for o in origins]
        n_leaf_paths = sum(sum(c.values()) for c in s.leaves.values())
        n_unres, n_amb = sum(s.unresolved.values()), sum(s.ambiguous.values())
        if n_amb:
            comp = AMBIGUOUS
        elif n_unres and n_leaf_paths + s.no_input:
            comp = PARTIAL
        elif n_unres:
            comp = UNRESOLVED
        else:
            comp = COMPLETE
        counts[comp] += 1
        leaves_json = []
        for (leaf, lf), cnt in sorted(s.leaves.items()):
            chars: dict[str, int] = {}
            through = 0
            for (r, cr), m in cnt.items():
                chars[_CHARACTER_OF_RANK[r]] = chars.get(_CHARACTER_OF_RANK[r], 0) + m
                if cr:
                    through += m
            cls = classes.get(leaf) or {}
            leaves_json.append({"leaf": leaf, "leaf_field": lf, "source_contract_class": cls.get("contract_class", SCC_UNKNOWN), "paths": sum(cnt.values()),
                                "path_characters": {c: chars[c] for c in PATH_CHARACTERS if c in chars}, "paths_through_harmonization": through})
        rows.append({"technical_name": name, "label": " ".join(str(raw.get("label") or "").split()) or None, "role": role,
                     "datatype_family": datatype_of(rec["type"], rec["length"])["family"], "immediate_providers": imm, "completeness": comp,
                     "paths": {"total": s.total(), "to_frontier_leaf": n_leaf_paths, "no_field_input": s.no_input, "unresolved": n_unres, "ambiguous": n_amb},
                     "unresolved_reasons": sorted(s.unresolved), "ambiguity_codes": sorted(s.ambiguous), "harmonization_boundaries_crossed": sorted(s.crossed),
                     "frontier_leaves": leaves_json, "lineage_paths": sample_paths(name)})
    value = {"extractor_version": FIELD_LINEAGE_EXTRACTOR_VERSION, "root": am, "edge_basis": PATH_BASIS, "fields": rows, "counts": {"fields": len(rows), **counts},
             "harmonization_candidates_in_closure": sorted(candidates),
             "definition": "factual backward provenance of each published field through the compiled queries of the closure to the frontier leaves; ambiguity is explicit, never resolved by choosing "
                           "a provider; no quality judgment"}
    return (make_fact("am.published_field_lineage", Area.ANALYTIC_MODEL, am, value, EvidenceClass.B, refs=entity_refs(e_am, ["fields", "query"])),)
