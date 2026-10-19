"""Hechos del CONTRATO de los artefactos candidatos a ARMONIZACION (S3, evidence release). Solo HECHOS descriptivos: NO hay puntaje de calidad de armonizacion.

Hecho (subject = artefacto candidato): ``harmonization.contract_character``. Es candidato un artefacto del cierre (no el raiz, no de plataforma, no importado
remoto) cuya capa inferida es 2 (por nombre de seccion 28 o por rol declarado) o que DECLARA rol de dimension. La base de la candidatura queda registrada.

Por candidato se persiste COMO se arma su contrato, desde la evidencia estructural disponible:
  * query-defined: la proyeccion del SELECT externo (IDENTITY mismo nombre / RENAME otro nombre / DERIVED expresion / CAST) y las derivaciones (AST);
  * tabla producida por un Data Flow: los ``attributeMappings`` del flujo (identidad / renombre / expresion; la expresion del flujo es TEXTO: no se parsea);
  * tabla sin productor adquirido: el mapeo NO es observable (se dice, no se supone).
Mas: campos retenidos de la fuente, campos renombrados, origen de la ETIQUETA de cada campo (igual al nombre propio / igual a la etiqueta del proveedor / igual al
nombre tecnico del proveedor / distinta de ambos), clases de transformacion, y arrastre del vocabulario fuente = el nombre del campo coincide EXACTAMENTE con una
columna de una fuente remota de su cadena (linaje de la clausura o procedencia del flujo). No es linaje de campo probado (G5 abierto) y no es un clasificador por forma.

CARACTER (descriptivo, solo con varias dimensiones y solo si es determinable; sin umbrales de razon):
  TECHNICAL_MATERIALIZATION         mapeo y etiquetas determinables; ninguna derivacion/cast y ninguna etiqueta propia (distinta del nombre y del proveedor);
  SEMANTIC_TRANSFORMATION_PRESENT   mapeo y etiquetas determinables; hay derivacion/cast Y hay etiquetas propias;
  MIXED                             determinable; solo UNA de las dos senales esta presente;
  UNKNOWN                           el mapeo o las etiquetas del proveedor no son determinables (se informa la razon). Nunca se fuerza una etiqueta.
Ninguna razon aislada (identidad, forma del nombre, tipo de artefacto) clasifica: un artefacto con mayoria de mapeos identidad puede ser MIXED.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import contract as C
from . import naming_rules as R
from .boundary import resolve_physical_boundaries
from .derivation import MAP_CAST, MAP_DERIVED, MAP_IDENTITY, MAP_RENAME, MAP_UNKNOWN, MAPPING_KINDS, TRANSFORMATION_CLASSES, transformation_classes
from .facts_source import source_state_of
from .facts_support import entity_refs, make_fact, sort_facts
from .graph import EdgeKind
from .identity import IdentityClassification, classify
from .model import Area, EvidenceClass, EvidenceRef, Fact, Plane
from .provenance import ASSESSED, resolve_leaf_provenance
from .readers import DATA_FLOW
from .scope import AssessmentUniverse

H_ = Area.HARMONIZATION

IMPL_QUERY, IMPL_FLOW_TABLE, IMPL_TABLE_NO_PRODUCER, IMPL_REMOTE = "QUERY_DEFINED", "TABLE_PRODUCED_BY_FLOW", "TABLE_NO_ACQUIRED_PRODUCER", "REMOTE_IMPORTED"
IMPLEMENTATIONS = (IMPL_QUERY, IMPL_FLOW_TABLE, IMPL_TABLE_NO_PRODUCER, IMPL_REMOTE)
MAPPING_BASES = ("QUERY_PROJECTION", "FLOW_ATTRIBUTE_MAPPINGS")
UNDETERMINABLE_REASONS = ("NO_ACQUIRED_PRODUCER", "NO_ATTRIBUTE_MAPPINGS_EXPORTED", "NO_QUERY_EVIDENCE", "NO_FIELDS", "MAPPING_NOT_DETERMINABLE", "LABELS_NOT_DETERMINABLE")
LO_NO_LABEL, LO_OWN_NAME, LO_PROVIDER_LABEL, LO_PROVIDER_TECHNICAL, LO_DIFFERENT, LO_UNKNOWN = (
    "NO_LABEL", "EQUALS_OWN_NAME", "EQUALS_PROVIDER_LABEL", "EQUALS_PROVIDER_TECHNICAL_NAME", "DIFFERENT_FROM_PROVIDER", "UNKNOWN_PROVIDER_LABEL")
LABEL_ORIGINS = (LO_NO_LABEL, LO_OWN_NAME, LO_PROVIDER_LABEL, LO_PROVIDER_TECHNICAL, LO_DIFFERENT, LO_UNKNOWN)
CH_TECHNICAL, CH_SEMANTIC, CH_MIXED, CH_UNKNOWN = "TECHNICAL_MATERIALIZATION", "SEMANTIC_TRANSFORMATION_PRESENT", "MIXED", "UNKNOWN"
CHARACTERS = (CH_TECHNICAL, CH_SEMANTIC, CH_MIXED, CH_UNKNOWN)


def _ratio(a: int, b: int) -> float | None:
    return None if b == 0 else round(a / b, 4)


def _label_origin(target: str, label: str | None, source_field: str | None, provider_label: str | None, provider_known: bool) -> str:
    text = " ".join(str(label or "").split())
    if not text:
        return LO_NO_LABEL
    if text == target:
        return LO_OWN_NAME
    if provider_label is not None and text == " ".join(provider_label.split()):
        return LO_PROVIDER_LABEL
    if source_field is not None and text == source_field:
        return LO_PROVIDER_TECHNICAL
    return LO_DIFFERENT if provider_known else LO_UNKNOWN


def _provider_label(entities: Mapping[str, Any], provider: str | None, field: str | None) -> tuple[str | None, bool]:
    """(etiqueta del campo en el proveedor, ¿se pudo consultar el proveedor?). Una etiqueta ausente en un proveedor conocido es 'conocido sin etiqueta'."""
    if provider is None or field is None or provider not in entities:
        return None, False
    for f in (getattr(entities[provider], "fields", None) or []):
        if f["campo"] == field:
            lab = " ".join(str(f.get("label") or "").split())
            return (lab or None), True
    return None, False


def resolve_unqualified_provider(col: Any, entities: Mapping[str, Any]) -> str | None:
    """Proveedor de una columna SIN calificar en un FROM con varios origenes. SQL exige que esa referencia sea inequivoca: si TODOS los proveedores del FROM son entidades con campos conocidos y
    EXACTAMENTE UNO expone el campo (comparacion exacta, como los identificadores entrecomillados), ese es el proveedor. Si algun proveedor es desconocido (sin campos en el pool) o mas de uno
    expone el nombre, no se adivina (``None``). Es evidencia presente en el catalogo que el lector por archivo no podia cruzar."""
    cands = tuple(getattr(col, "candidates", ()) or ())
    if not cands or col.source_field is None:
        return None
    holders = []
    for p in cands:
        flds = getattr(entities.get(p), "fields", None) if p in entities else None
        if not flds:
            return None                                                       # proveedor sin catalogo de campos: no se puede afirmar la unicidad
        if any(f.get("campo") == col.source_field for f in flds):
            holders.append(p)
    return holders[0] if len(holders) == 1 else None


def _flow_properties(ev: Any, kind: str, name: str):
    for fp in ev.flow_properties:
        if fp.kind == kind and fp.name == name:
            return fp
    return None


def candidate_basis(name: str, entity: Any, am: str, ident: Mapping[str, IdentityClassification]) -> list[str]:
    """Base de la candidatura de ``name`` a armonizacion (vacia = no es candidato). Es candidato un artefacto del cierre (no el raiz, no de plataforma, no importado remoto)
    cuya capa inferida es 2 (por nombre de seccion 28 o por rol declarado) o que DECLARA rol de dimension. Fuente unica: la reutiliza el linaje de campo."""
    if name == am or (name in ident and ident[name].identity.value == "SYSTEM") or getattr(entity, "remote_entity", ""):
        return []
    layer = C.layer_inference(entity)
    role = C.declared_role(entity)
    basis = []
    if layer["inferred_layer"] == "2":
        basis.append(f"LAYER_2_BY_{layer['inferred_layer_basis']}")
    if role["declares_dimension"]:
        basis.append("DECLARED_DIMENSION_ROLE")
    return basis


def harmonization_contract_facts(
    universe: AssessmentUniverse,
    entities: Mapping[str, Any],
    am: str,
    *,
    identity: Mapping[str, IdentityClassification] | None = None,
    evidence: Any | None = None,
) -> tuple[Fact, ...]:
    ev = evidence
    graph = universe.graph
    closure = universe.closure_of(am)
    closure_set = set(closure)
    ident = identity or {n: classify(entities[n]) for n in closure}
    facts: list[Fact] = []

    # bordes remotos por hoja producida por flujo (para el arrastre del vocabulario fuente)
    chain_remote: dict[str, set[str]] = {}
    if ev is not None and hasattr(ev, "target_flows"):
        for n in closure:
            e = entities[n]
            if graph.providers(n) or getattr(e, "remote_entity", ""):
                continue
            rec = resolve_leaf_provenance(n, ev, entities)
            if rec["status"] == ASSESSED and rec["producer_flows"]:
                pb = resolve_physical_boundaries(n, rec, ev, entities, graph, closure, lambda re: source_state_of(re).value)
                chain_remote[n] = {b["boundary_entity"] for b in pb["boundaries"] if b["boundary_entity"]}

    for n in closure:
        e = entities[n]
        basis = candidate_basis(n, e, am, ident)
        if not basis:
            continue
        layer = C.layer_inference(e)
        role = C.declared_role(e)

        fields = [f for f in (getattr(e, "fields", None) or [])]
        names = [f["campo"] for f in fields]
        raw_label = {f["campo"]: f.get("label") for f in fields}
        d = ev.derivation_evidence(n) if ev is not None else None
        targets, _amb = (ev.target_flows(n, entities.keys()) if ev is not None and hasattr(ev, "target_flows") else ((), None))
        df_targets = [fl for fl, _m in targets if fl.kind == DATA_FLOW]
        sources: list[str] = []
        producers = sorted(f"{fl.kind}:{fl.name}" for fl, _m in targets)
        mapping: dict[str, tuple[str, str | None, str | None]] = {}          # target -> (kind, source_provider, source_field)
        mapping_basis, undet = None, None
        derived_cols = []
        if d is not None and d.has_query:
            impl = IMPL_QUERY
            sources = [p for p in graph.providers(n, [EdgeKind.LINEAGE]) if p in closure_set]
            proj = [c for c in d.projections if c.branch == 0]
            if proj:
                mapping_basis = "QUERY_PROJECTION"
                for c in proj:
                    prov = c.source_provider if c.source_provider is not None else resolve_unqualified_provider(c, entities)
                    mapping[c.output] = (c.kind, prov, c.source_field)
            else:
                undet = "NO_QUERY_EVIDENCE"
            derived_cols = [c for c in d.columns if c.branch == 0]
        elif df_targets:
            impl = IMPL_FLOW_TABLE
            fl = df_targets[0]
            sources = sorted(fl.source_entities)
            fp = _flow_properties(ev, fl.kind, fl.name)
            node = next((nd for nd in (fp.properties.get("nodes") if fp else []) if nd["role"] == "target" and nd["entity"] == n), None)
            if node and node["attribute_mappings"]:
                mapping_basis = "FLOW_ATTRIBUTE_MAPPINGS"
                src_entity = sources[0] if len(sources) == 1 else None
                for m in node["attribute_mappings"]:
                    expr = m["expression"]
                    col = expr[1:-1] if isinstance(expr, str) and len(expr) >= 3 and expr[0] == '"' and expr[-1] == '"' and '"' not in expr[1:-1] else None
                    if col is None:
                        mapping[m["target"]] = (MAP_DERIVED, None, None)
                    else:
                        mapping[m["target"]] = (MAP_IDENTITY if col == m["target"] else MAP_RENAME, src_entity, col)
            else:
                undet = "NO_ATTRIBUTE_MAPPINGS_EXPORTED"
        elif targets:
            impl, undet = IMPL_FLOW_TABLE, "NO_ATTRIBUTE_MAPPINGS_EXPORTED"
            sources = sorted({s for fl, _m in targets for s in fl.source_entities})
        else:
            impl, undet = IMPL_TABLE_NO_PRODUCER, "NO_ACQUIRED_PRODUCER"
        if not names:
            undet = undet or "NO_FIELDS"

        # ---- conteos de mapeo -------------------------------------------------------------------------------------------------------------------------
        counts = {k: 0 for k in MAPPING_KINDS}
        rows = []
        for t in names:
            kind, prov, sf = mapping.get(t, (MAP_UNKNOWN, None, None))
            if mapping_basis is None:
                kind, prov, sf = MAP_UNKNOWN, None, None
            counts[kind] += 1
            rows.append((t, kind, prov, sf))
        n_mapped = sum(v for k, v in counts.items() if k != MAP_UNKNOWN)
        mapping_determinable = mapping_basis is not None and counts[MAP_UNKNOWN] == 0 and bool(names)

        # ---- retencion de campos fuente ---------------------------------------------------------------------------------------------------------------
        retained = []
        by_src: dict[str, set[str]] = {}
        for _t, kind, prov, sf in rows:
            if kind in (MAP_IDENTITY, MAP_RENAME, MAP_CAST) and prov and sf:
                by_src.setdefault(prov, set()).add(sf)
        for s, used in sorted(by_src.items()):
            total = len(getattr(entities.get(s), "fields", None) or []) if s in entities else None
            retained.append({"source": s, "source_field_count": total, "retained_source_fields": len(used), "ratio": _ratio(len(used), total) if total else None})

        # ---- origen de etiquetas ----------------------------------------------------------------------------------------------------------------------
        lo = {k: 0 for k in LABEL_ORIGINS}
        labels_determinable = bool(names)
        for t, kind, prov, sf in rows:
            pl, known = _provider_label(entities, prov, sf)
            origin = _label_origin(t, raw_label.get(t), sf, pl, known)
            lo[origin] += 1
            if origin == LO_UNKNOWN:
                labels_determinable = False
        renamed = [{"target": t, "source_field": sf} for t, kind, _p, sf in rows if kind == MAP_RENAME]

        # ---- transformaciones (clases) ----------------------------------------------------------------------------------------------------------------
        classes: set[str] = set()
        for c in derived_cols:
            classes |= set(transformation_classes(c.operations, c.functions))
        n_derived = counts[MAP_DERIVED] + counts[MAP_CAST]

        # ---- arrastre del vocabulario fuente (igualdad EXACTA con una columna remota de la cadena) ------------------------------------------------
        remote_set = {a for a in graph.lineage_closure(n) if a != n and getattr(entities[a], "remote_entity", "")} if graph.has_node(n) else set()
        for leaf in ([n] + [x for x in graph.lineage_closure(n)]):
            remote_set |= chain_remote.get(leaf, set())
        remote_cols: dict[str, list[str]] = {}
        for a in sorted(remote_set):
            for f in (getattr(entities.get(a), "fields", None) or []):
                remote_cols.setdefault(f["campo"], []).append(a)
        with_src = [t for t in names if t in remote_cols]
        vocab = {"basis": "NAME_EQUALS_REMOTE_SOURCE_COLUMN_IN_CHAIN", "remote_source_artifacts": sorted(remote_set), "fields_with_source_column_name": len(with_src),
                 "ratio": _ratio(len(with_src), len(names)) if remote_set else None, "determinable": bool(remote_set)}

        # ---- caracter -----------------------------------------------------------------------------------------------------------------------------------
        has_transformation = n_derived > 0
        has_authored = lo[LO_DIFFERENT] > 0
        if not (mapping_determinable and labels_determinable):
            character, reason = CH_UNKNOWN, undet or ("LABELS_NOT_DETERMINABLE" if mapping_determinable else "MAPPING_NOT_DETERMINABLE")
        elif not has_transformation and not has_authored:
            character, reason = CH_TECHNICAL, None
        elif has_transformation and has_authored:
            character, reason = CH_SEMANTIC, None
        else:
            character, reason = CH_MIXED, None
        facts.append(make_fact(
            "harmonization.contract_character", H_, n,
            {"artifact": n, **layer, "candidate_basis": basis, "declared_role": role, "implementation": impl, "sources": sources, "producer_flows": producers,
             "target_field_count": len(names), "mapping_basis": mapping_basis, "mapping_determinable": mapping_determinable,
             "mapping_counts": counts, "mapping_count": n_mapped, "identity_mapping_ratio": _ratio(counts[MAP_IDENTITY], n_mapped) if n_mapped else None,
             "retained_source_fields": retained, "renamed_fields": renamed, "label_origin_counts": lo, "labels_differing_from_name_and_provider": lo[LO_DIFFERENT],
             "business_name_enrichment_ratio": _ratio(lo[LO_DIFFERENT], len(names)) if labels_determinable else None, "labels_determinable": labels_determinable,
             "derived_field_count": n_derived, "transformation_classes": [c for c in TRANSFORMATION_CLASSES if c in classes],
             "source_vocabulary": vocab,
             "character": {"value": character, "reason_unknown": reason,
                           "dimensions": {"mapping_determinable": mapping_determinable, "labels_determinable": labels_determinable, "has_transformation": has_transformation,
                                          "has_authored_vocabulary": has_authored, "pure_projection_only": mapping_determinable and n_derived == 0}}},
            EvidenceClass.B, refs=[*entity_refs(e, ["fields"]), *[EvidenceRef(n, "flow_reference", fl.source_file, Plane.PROCESSING) for fl, _m in targets]]))
    return sort_facts(facts)
