"""Secciones NUEVAS de ``canonical_evidence`` v2 (evidence release): se arman desde el indice de hechos, sin reabrir el pool crudo. Es evidencia, no juicio.

v1 conserva sus cuatro secciones (``leaf_provenance``, ``derivation_semantics``, ``expression_repetition``, ``assembly_topology``). v2 agrega:
  closure                       completitud (resolucion de referencias) y contexto de planos del cierre semantico (S1)
  physical_source_boundaries    bordes externos de integracion que revela la procedencia de las hojas producidas por un flujo (S1)
  published_contract            asociaciones por categoria y campos/medidas publicados con su tipo de dato (S2); None si la raiz no es un AM
  naming_evidence               taxonomia higiene / contrato semantico + agregados de ambos lados (S2)
  harmonization_contract        caracter del contrato de los artefactos candidatos a armonizacion (S3)
  expression_occurrences        expresiones por clausula, clases de transformacion y reuso estructural (S4)
  branch_reconvergence          ramas paralelas con proveedor compartido que reconvergen (S5)
  view_complexity               complejidad estructural de las vistas junto al tipo de vista (S6)
  processing_flow_properties    propiedades de carga EXACTAS exportadas por los flujos productores (S1/S7 lectura)
"""
from __future__ import annotations

from typing import Any

from .facts_closure import CLOSURE_RULESET, REF_AMBIGUOUS, REF_EXCLUDED_BY_PLANE, REF_RESOLVED, REF_UNRESOLVED, REFERENCE_STATES
from .naming_evidence import NAMING_TAXONOMY

V2_SECTIONS = ("closure", "physical_source_boundaries", "published_contract", "naming_evidence", "harmonization_contract", "expression_occurrences", "branch_reconvergence",
               "view_complexity", "processing_flow_properties")


def _val(idx: Any, fact_id: str, subject: str) -> dict | None:
    f = idx.usable_get(fact_id, subject)
    return f.value if f is not None else None


def _closure_section(idx: Any) -> dict:
    members = idx.all("am.closure_reference_resolution")
    totals = {s: 0 for s in REFERENCE_STATES}
    platform_total = 0
    open_members = []
    for f in members:
        if f.value is None:
            continue
        for s in REFERENCE_STATES:
            totals[s] += f.value["counts"][s]
        platform_total += len(f.value.get("platform_provided", []))
        if f.value["unresolved"] or f.value["excluded_by_plane"] or f.value["ambiguous"]:
            open_members.append({"artifact": f.subject, "unresolved": f.value["unresolved"], "excluded_by_plane": f.value["excluded_by_plane"], "ambiguous": f.value["ambiguous"]})
    ctx = _val(idx, "am.closure_plane_context", "closure")
    return {"rules": CLOSURE_RULESET, "plane_policy": ctx["plane_policy"] if ctx else None,
            "reference_resolution": {"members_total": len(members), "counts": totals, "platform_provided_total": platform_total, "members_with_open_references": open_members},
            "plane_context": ctx}


def _boundaries_section(idx: Any) -> dict:
    leaves = [f.value for f in idx.all("source.physical_boundary") if f.value is not None]
    by_class: dict[str, int] = {}
    for rec in leaves:
        for b in rec["boundaries"]:
            by_class[b["boundary_class"]] = by_class.get(b["boundary_class"], 0) + 1
    return {"leaves": leaves, "boundaries_by_class": [{"boundary_class": k, "count": v} for k, v in sorted(by_class.items())],
            "definition": "external integration boundaries revealed by the provenance chain of flow-produced local leaves; provenance plane: not semantic closure members and not "
                          "DIRECT_RAW_TABLE_ACCESS evidence"}


def _published_section(idx: Any) -> dict | None:
    assoc = _val(idx, "am.association_semantics", next((f.subject for f in idx.all("am.association_semantics")), ""))
    fields = _val(idx, "am.field_semantics", next((f.subject for f in idx.all("am.field_semantics")), ""))
    if assoc is None and fields is None:
        return None
    return {"associations": assoc, "fields": fields}


def _naming_section(idx: Any, published: dict | None, harmonization: dict) -> dict:
    pop = idx.usable_get("naming.artifact_name_populations", "closure")
    hyg = {"artifact_name_populations": pop.value if pop is not None else None}
    pub = None
    if published is not None and published["fields"] is not None:
        c = published["fields"]["counts"]
        pub = {"fields": c["fields"], "measures": c["measures"], "business_name_equals_technical": c["business_name_equals_technical"],
               "name_equals_remote_source_column": c["name_equals_remote_source_column"]}
    by_char: dict[str, int] = {}
    for a in harmonization["artifacts"]:
        by_char[a["character"]["value"]] = by_char.get(a["character"]["value"], 0) + 1
    return {"taxonomy": [dict(t) for t in NAMING_TAXONOMY], "hygiene": hyg,
            "semantic_contract": {"published_contract": pub, "harmonization_contract": {"artifacts": len(harmonization["artifacts"]),
                                                                                       "by_character": [{"character": k, "count": v} for k, v in sorted(by_char.items())]}}}


def build_v2_sections(idx: Any) -> dict:
    published = _published_section(idx)
    harm_facts = [f.value for f in idx.all("harmonization.contract_character") if f.value is not None]
    harmonization = {"artifacts": harm_facts, "count": len(harm_facts)}
    views = [f.value for f in idx.all("harmonization.view_complexity") if f.value is not None]
    return {
        "closure": _closure_section(idx),
        "physical_source_boundaries": _boundaries_section(idx),
        "published_contract": published,
        "naming_evidence": _naming_section(idx, published, harmonization),
        "harmonization_contract": harmonization,
        "expression_occurrences": _val(idx, "propagation.expression_occurrences", "closure"),
        "branch_reconvergence": _val(idx, "propagation.branch_reconvergence", "closure"),
        "view_complexity": {"views": views, "count": len(views)},
        "processing_flow_properties": _val(idx, "ingestion.flow_load_properties", "closure"),
    }
