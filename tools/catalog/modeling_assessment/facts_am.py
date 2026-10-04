"""Hechos del MODELO ANALITICO (M3) — contrato semantico del artefacto terminal. Solo HECHOS.

No se decide si el AM es "rico", si "subutiliza" la capacidad semantica ni si su valor agregado es suficiente:
eso son senales posteriores y/o juicio con contexto (D). Aqui se RETIENE, en listas completas, lo que el AM
declara y en que se diferencia de sus proveedores directos de linaje.

Hechos (subject = AM terminal, salvo indicacion):
  am.contract_inventory                       A  rol declarado, campos (con flags), medidas, asociaciones, claves,
                                                 derivados, cobertura de labels, elementos vs campos
  am.association_targets                      A  por asociacion: destino, resolucion en el grafo, rol DECLARADO del
                                                 destino, asociaciones propias del destino, campos FK que la usan
  am.contract_delta   (subject "prov->AM")    B  que agrega/quita/hereda el AM respecto de CADA proveedor directo de linaje
  am.numeric_attributes_not_declared_as_measures  B  campos de tipo numerico DECLARADO que no son medida/clave/FK
  am.closure_composition                      B  composicion DESCRIPTIVA del cierre semantico: artefactos alcanzables por linaje estricto desde el AM
                                                 (incluido el propio AM) y artefactos mediados solo por asociacion (contexto de arquitectura, no senal)

No se usa ningun conteo (medidas, asociaciones, campos, derivados) como proxy de calidad: son contexto de rol.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import contract as C
from .facts_support import entity_refs, make_fact, sort_facts
from .graph import EdgeKind
from .identity import IdentityClassification, classify
from .model import Area, EvidenceClass, Fact
from .scope import AssessmentUniverse

L, ASSOC = EdgeKind.LINEAGE, EdgeKind.ASSOCIATION
AM_ = Area.ANALYTIC_MODEL


def am_facts(
    universe: AssessmentUniverse,
    entities: Mapping[str, Any],
    am: str,
    *,
    identity: Mapping[str, IdentityClassification] | None = None,
) -> tuple[Fact, ...]:
    graph = universe.graph
    closure = universe.closure_of(am)
    closure_set = set(closure)
    if universe.root_type_of(am) != "analytic-model":
        # Raiz que NO es Modelo Analitico (vista / tabla): los hechos de contrato/asociaciones/delta del "modelo analitico" no aplican (sus
        # superficies quedan NOT_APPLICABLE); solo se describe la composicion del cierre, que es estructura de grafo valida para cualquier raiz.
        return sort_facts([_closure_composition_fact(universe, entities, am, closure_set)])
    ident = identity or {n: classify(entities[n]) for n in closure}
    e = entities[am]
    lin_providers = [p for p in graph.providers(am, [L]) if p in closure_set]
    assoc_providers = [p for p in graph.providers(am, [ASSOC]) if p in closure_set]
    facts: list[Fact] = []

    # ---- inventario del contrato (A) ---------------------------------------------------------------
    facts.append(make_fact(
        "am.contract_inventory", AM_, am,
        {"declared_role": C.declared_role(e), **C.layer_inference(e),
         "summary": C.contract_summary(e),
         "direct_lineage_providers": lin_providers, "direct_association_providers": assoc_providers,
         "fields": C.field_records(e), "measures": C.measure_records(e),
         "associations": C.association_records(e), "derived_fields": C.derived_records(e),
         "keys": C.keys_of(e), "declared_dimensions": list(getattr(e, "dimensions", None) or []),
         "label_coverage": C.label_coverage(e)},
        EvidenceClass.A, refs=entity_refs(e, ["fields", "measures", "associations", "keys", "n_elements"])))

    # ---- destinos de asociacion (A) ----------------------------------------------------------------
    edge_origin = {(ed.provider, ed.kind): ed.origin.value for ed in graph.edges_of(am)}
    records = []
    for a in C.association_records(e):
        target = a["target"]
        known = bool(target) and graph.has_node(target)
        t = entities.get(target) if known else None
        records.append({
            **a,
            "target_in_graph": known,
            "target_in_semantic_closure": target in closure_set,
            "edge_origin": edge_origin.get((target, ASSOC)),
            "target_declared_role": C.declared_role(t) if t is not None else None,
            "target_layer_inference": C.layer_inference(t) if t is not None else None,
            "target_identity": ident[target].identity.value if target in ident else None,
            "target_own_association_count": len(getattr(t, "associations", None) or []) if t is not None else None,
            "foreign_key_fields": [f["field"] for f in C.field_records(e) if f["association_target"] == target],
        })
    facts.append(make_fact("am.association_targets", AM_, am, {"associations": records}, EvidenceClass.A,
                           refs=entity_refs(e, ["associations"])))

    # ---- delta contra cada proveedor directo de linaje (B) -----------------------------------------
    for p in lin_providers:
        facts.append(make_fact(
            "am.contract_delta", AM_, f"{p}->{am}",
            C.edge_delta(entities[p], e, consumer_lineage_provider_count=len(lin_providers)),
            EvidenceClass.B, refs=[*entity_refs(entities[p], ["fields", "measures", "associations", "keys"]),
                                   *entity_refs(e, ["fields", "measures", "associations", "keys"])]))

    # ---- atributos numericos que no son medida (B) -------------------------------------------------
    measure_in = {p: {m["field"] for m in C.measure_records(entities[p])} for p in lin_providers}
    facts.append(make_fact(
        "am.numeric_attributes_not_declared_as_measures", AM_, am,
        {"fields": [{**r, "measure_in_direct_lineage_providers": [p for p in lin_providers if r["field"] in measure_in[p]]}
                    for r in C.numeric_non_measure_fields(e)],
         "numeric_declared_types": sorted(C.NUMERIC_DECLARED_TYPES)},
        EvidenceClass.B, refs=entity_refs(e, ["fields"])))

    # ---- composicion del cierre semantico: linaje estricto vs asociacion (B; descriptivo, sin juicio) ------------
    facts.append(_closure_composition_fact(universe, entities, am, closure_set))

    return sort_facts(facts)


def _closure_composition_fact(universe: AssessmentUniverse, entities: Mapping[str, Any], am: str, closure_set: set[str]) -> Fact:
    lineage_set = set(universe.lineage_closure_of(am))
    association_only = sorted(closure_set - lineage_set)
    return make_fact(
        "am.closure_composition", AM_, am,
        {"semantic_closure_size": len(closure_set), "strict_lineage_size": len(lineage_set), "association_mediated_size": len(association_only),
         "strict_lineage_artifacts": sorted(lineage_set), "association_mediated_artifacts": association_only,
         "definition": "semantic closure = lineage + association; strict lineage = reachable from the root object by lineage edges only (root included)"},
        EvidenceClass.B, refs=entity_refs(entities[am], ["associations"]))
