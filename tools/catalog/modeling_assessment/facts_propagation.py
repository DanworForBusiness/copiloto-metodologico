"""Hechos de PROPAGATION (M3) — como evoluciona el contrato a lo largo de los eslabones de linaje. Solo HECHOS.

Propagation se describe por EVIDENCIA ESTRUCTURAL de linaje, no por la capa inferida (criterio compuesto que usa el rol declarado: seria circular): la capa
inferida solo se copia como contexto (con su base). NO se decide si un eslabon es pass-through problematico, si una re-derivacion
es un defecto ni donde "deberia" originarse una asociacion o medida: son senales/revision posteriores (el CASE 1
prueba que una propagacion casi pass-through puede ser metodologicamente justificada).

Hechos:
  propagation.contract_delta (subject "prov->cons")  B  por eslabon de LINAJE cuyo proveedor no es una hoja (los
        eslabones desde hojas viven en `ingestion.field_retention`) y cuyo consumidor no es el AM terminal (esos son
        `am.contract_delta`): campos agregados/quitados/retenidos, cambios de tipo, medidas, asociaciones introducidas
        vs heredadas, claves/grano, derivados y descriptores de continuidad. Listas completas.
  propagation.derived_fields (subject = artefacto)   A  campos con expresion visible, medidas vs no-medidas
  propagation.contract_occurrences (closure)         B  para cada asociacion/medida/derivado del AM: (A) puntos de
        introduccion sobre la espina de linaje directa y (B) TODAS las ocurrencias en la clausura semantica; no hay
        un "origen" unico (en un grafo con ramas el mismo contrato puede declararse en varios artefactos)
  propagation.rederivation (closure)                 B  mismo campo con `derived` en un artefacto y en un ancestro de
        linaje (distancia 1 = eslabones consecutivos)
  propagation.derivation_semantics (closure)         B  semantica ESTRUCTURAL de las derivaciones desde el CSN compilado (G3): operaciones, insumos, huellas
        exacta y de forma, resolubilidad; la relacion de cada repeticion (DUPLICATED_TRANSFORMATION | CHAINED_REFINEMENT | DISTINCT_TRANSFORMATION |
        UNKNOWN) vive en `propagation.rederivation`. La semantica de NEGOCIO de las formulas no se infiere.

Limite explicito (L3): `derived` solo existe para columnas con expresion visible en el query (xpr/func/val/cast);
si el artefacto no trae query, el flag no esta disponible y el hecho queda PARTIALLY_ASSESSED.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import contract as C
from .derivation import DERIVATION_EXTRACTOR_VERSION, EXPOSURE_BASIS_SAME_NAME, REL_UNKNOWN, RELATIONS, classify_relation
from .facts_support import NoEvidence, entity_refs, make_fact, not_applicable, sort_facts
from .graph import EdgeKind
from .identity import IdentityClassification, classify
from .model import (
    ApplicabilityReason, Area, Assessability, Cause, EvidenceClass, EvidenceRef, Fact, Plane,
)
from .scope import AssessmentUniverse

L = EdgeKind.LINEAGE
P_ = Area.PROPAGATION


def _is_system(ident: Mapping[str, IdentityClassification], name: str) -> bool:
    return name in ident and ident[name].identity.value == "SYSTEM"


def propagation_facts(
    universe: AssessmentUniverse,
    entities: Mapping[str, Any],
    am: str,
    *,
    identity: Mapping[str, IdentityClassification] | None = None,
    evidence: Any | None = None,
) -> tuple[Fact, ...]:
    ev = evidence if evidence is not None else NoEvidence()
    graph = universe.graph
    closure = universe.closure_of(am)
    closure_set = set(closure)
    ident = identity or {n: classify(entities[n]) for n in closure}
    is_am = universe.root_type_of(am) == "analytic-model"       # si la raiz NO es AM, sus derivados y su arista final no viven en `am.*` (N/A)
    non_leaf = [n for n in closure if graph.providers(n)]
    non_leaf_set = set(non_leaf)
    lin_dist = graph.distances(am, [L])
    facts: list[Fact] = []

    def lin_providers(n: str) -> list[str]:
        return [p for p in graph.providers(n, [L]) if p in closure_set]

    # ---- campos derivados por artefacto (A) --------------------------------------------------------
    for n in non_leaf:
        if n == am and is_am:
            continue                                  # los derivados del AM viven en `am.contract_inventory`
        e = entities[n]
        if _is_system(ident, n):
            facts.append(make_fact("propagation.derived_fields", P_, n, None, EvidenceClass.A,
                                   refs=entity_refs(e, ["fields"]),
                                   applicability=not_applicable(ApplicabilityReason.SYSTEM_ARTIFACT),
                                   assessability=Assessability.NOT_APPLICABLE))
            continue
        recs = C.derived_records(e)
        visible = C.derivation_visible(e)
        facts.append(make_fact(
            "propagation.derived_fields", P_, n,
            {"declared_role": C.declared_role(e), **C.layer_inference(e),
             "derivation_visible": visible, "n_fields": len(C.field_names(e)),
             "n_derived": len(recs), "n_derived_measures": sum(1 for r in recs if r["is_measure"]),
             "n_derived_non_measures": sum(1 for r in recs if not r["is_measure"]), "derived_fields": recs},
            EvidenceClass.A, refs=entity_refs(e, ["fields"]),
            assessability=Assessability.ASSESSED if visible else Assessability.PARTIALLY_ASSESSED,
            cause=None if visible else Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT))

    # ---- delta de contrato por eslabon de linaje (B) -----------------------------------------------
    for consumer in closure:
        if consumer == am and is_am:
            continue                                  # la arista final proveedor->AM vive en `am.contract_delta`
        provs = lin_providers(consumer)
        for provider in provs:
            if provider not in non_leaf_set:
                continue                              # eslabon desde una hoja: `ingestion.field_retention`
            refs = [*entity_refs(entities[provider], ["fields", "measures", "associations", "keys"]),
                    *entity_refs(entities[consumer], ["fields", "measures", "associations", "keys"])]
            if _is_system(ident, consumer) or _is_system(ident, provider):
                facts.append(make_fact("propagation.contract_delta", P_, f"{provider}->{consumer}", None, EvidenceClass.B,
                                       refs=refs, applicability=not_applicable(ApplicabilityReason.SYSTEM_ARTIFACT),
                                       assessability=Assessability.NOT_APPLICABLE))
                continue
            facts.append(make_fact(
                "propagation.contract_delta", P_, f"{provider}->{consumer}",
                C.edge_delta(entities[provider], entities[consumer], consumer_lineage_provider_count=len(provs)),
                EvidenceClass.B, refs=refs))

    # ---- ocurrencias del contrato del AM (B) -------------------------------------------------------
    # NO existe un "origen" unico: en un grafo con ramas el mismo contrato puede declararse en varios artefactos.
    # Se separan dos conceptos: (A) puntos de introduccion sobre la ESPINA DE LINAJE directa del AM y
    # (B) TODAS las ocurrencias en la clausura semantica. Que haya varias NO se interpreta (ni duplicado ni capa erronea).
    spine_set = set(universe.lineage_closure_of(am))
    sem_dist = graph.distances(am)

    def spine_providers(n: str) -> list[str]:
        return [p for p in graph.providers(n, [L]) if p in spine_set]

    am_entity = entities[am]
    field_rec = {n: {r["field"]: r for r in C.field_records(entities[n])} for n in closure}
    assoc_targets = {n: set(C.associations_by_target(entities[n])) for n in closure}
    derived_sets = {n: set(C.derived_names(entities[n])) for n in closure}

    def occurrence(n: str, field: str | None) -> dict:
        rec = field_rec[n].get(field) if field is not None else None
        return {"artifact": n, "on_strict_lineage_spine": n in spine_set, "lineage_distance": lin_dist.get(n),
                "semantic_distance": sem_dist.get(n), **C.layer_inference(entities[n]),
                "as_measure": bool(rec["measure"]) if rec is not None else None,
                "derived": (field in derived_sets[n]) if field is not None else None}

    def element(kind: str, fields: list[str], target: str | None, holds) -> dict:
        held = sorted((n for n in closure if holds(n)), key=lambda n: (sem_dist.get(n, 0), n))
        held_spine = {n for n in held if n in spine_set}
        intro = [n for n in held if n in spine_set and not any(p in held_spine for p in spine_providers(n))]
        probe = fields[0] if kind != "association" else None
        return {"kind": kind, "fields": fields, "target": target,
                "closure_occurrences": [occurrence(n, probe) for n in held],
                "direct_spine_introduction_points": intro, "introduction_point_count": len(intro)}

    records = []
    for target, fields in C.associations_by_target(am_entity).items():
        records.append(element("association", fields, target, lambda n, t=target: t in assoc_targets[n]))
    for mm in C.measure_records(am_entity):
        records.append(element("measure", [mm["field"]], None, lambda n, f=mm["field"]: f in field_rec[n]))
    for d in C.derived_records(am_entity):
        records.append(element("derived_field", [d["field"]], None, lambda n, f=d["field"]: f in derived_sets[n]))
    facts.append(make_fact(
        "propagation.contract_occurrences", P_, "closure",
        {"terminal_am": am, "elements": records,
         "introduction_point_definition": "artifacts on the strict lineage spine that hold the element while none of their direct lineage providers does; several may exist on a branched spine, so this is NOT a unique origin",
         "closure_occurrences_definition": "every artifact of the semantic closure that declares the same association target / field name / derived field"},
        EvidenceClass.B, refs=[EvidenceRef(am, "dependency_graph", None, Plane.MODELING)]))

    # ---- derivaciones estructurales (G3): AST compilado por artefacto ------------------------------------
    root_fields = set(C.field_names(entities[am]))

    def cols_of(n: str, field: str | None = None):
        d = ev.derivation_evidence(n)
        if d is None:
            return ()
        return tuple(c for c in d.columns if field is None or c.field == field)

    # ---- re-derivacion del mismo campo en ancestros de linaje (B) ----------------------------------
    redo = []
    for n in closure:
        if _is_system(ident, n) or n not in non_leaf_set:
            continue
        mine = C.derived_names(entities[n])
        if not mine:
            continue
        ancestors = graph.distances(n, [L])
        direct = lin_providers(n)
        for anc, dist in sorted(ancestors.items(), key=lambda kv: (kv[1], kv[0])):
            if anc == n or dist < 1 or anc not in closure_set or _is_system(ident, anc):
                continue
            theirs = set(C.derived_names(entities[anc]))
            chain_sources = [q for q in direct if q == anc or anc in graph.distances(q, [L])]
            for f in mine:
                if f in theirs:
                    relation, reason = classify_relation(cols_of(anc, f), cols_of(n, f), field=f, chain_sources=chain_sources)                         if ev.derivation_evidence(anc) is not None and ev.derivation_evidence(n) is not None else (REL_UNKNOWN, "NO_QUERY_EVIDENCE")
                    redo.append({"field": f, "consumer": n, "provider": anc, "lineage_distance": dist, "adjacent": dist == 1,
                                 "consumer_layer_inference": C.layer_inference(entities[n]),
                                 "provider_layer_inference": C.layer_inference(entities[anc]),
                                 "relation": relation, "relation_reason": reason,
                                 "exposed_by_root": f in root_fields, "exposure_basis": EXPOSURE_BASIS_SAME_NAME})
    facts.append(make_fact(
        "propagation.rederivation", P_, "closure",
        {"occurrences": redo, "adjacent_count": sum(1 for r in redo if r["adjacent"]),
         "relation_counts": {rel: sum(1 for r in redo if r["relation"] == rel) for rel in RELATIONS},
         "match_rule": "same technical field name flagged derived in an artifact and in a lineage ancestor (exact match); the RELATION of each occurrence is classified structurally from the compiled query AST (a repeated name alone decides nothing)"},
        EvidenceClass.B, refs=[EvidenceRef(am, "dependency_graph", None, Plane.MODELING)]))

    # ---- semantica estructural de las derivaciones (B) ---------------------------------------------
    fields_json, ambiguity_counts, with_evidence, missing = [], {}, 0, []
    for n in closure:
        if _is_system(ident, n):
            continue
        d = ev.derivation_evidence(n)
        if d is None:
            if n in non_leaf_set and C.derivation_visible(entities[n]):
                missing.append(n)
            continue
        with_evidence += 1
        direct = lin_providers(n)
        for c in d.columns:
            rec = c.to_json()
            rec["reads_provider_same_field"] = c.reads(c.field, direct)
            rec["exposed_by_root"] = c.field in root_fields
            rec["exposure_basis"] = EXPOSURE_BASIS_SAME_NAME
            fields_json.append(rec)
            for a in c.ambiguity:
                ambiguity_counts[a] = ambiguity_counts.get(a, 0) + 1
    sem_refs = [EvidenceRef(am, "compiled_query", None, Plane.MODELING)]
    if with_evidence == 0:
        facts.append(make_fact("propagation.derivation_semantics", P_, "closure", None, EvidenceClass.C1, refs=sem_refs,
                               assessability=Assessability.NOT_ASSESSED, cause=Cause.NOT_IN_SNAPSHOT))
    else:
        facts.append(make_fact(
            "propagation.derivation_semantics", P_, "closure",
            {"extractor_version": DERIVATION_EXTRACTOR_VERSION, "artifacts_with_query_evidence": with_evidence,
             "artifacts_without_query_evidence": sorted(missing), "fields": fields_json,
             "counts": {"derived_fields": len(fields_json), "unresolvable": sum(1 for r in fields_json if not r["resolvable"]),
                        "ambiguity": [{"code": k, "count": v} for k, v in sorted(ambiguity_counts.items())]},
             "exposure_basis": EXPOSURE_BASIS_SAME_NAME},
            EvidenceClass.B, refs=sem_refs,
            assessability=Assessability.PARTIALLY_ASSESSED if missing else Assessability.ASSESSED,
            cause=Cause.NOT_IN_SNAPSHOT if missing else None))

    return sort_facts(facts)
