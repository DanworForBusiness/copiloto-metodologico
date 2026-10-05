"""Contrato EXPLICITO de las secciones NUEVAS de ``canonical_evidence`` v2 (evidence release). Vocabularios CERRADOS tomados de los modulos que producen cada
hecho + invariantes internos. Sin JSON Schema ni dependencias. Es evidencia, no juicio: ningun invariante habla de calidad.

Los invariantes protegen el contrato descriptivo: por ejemplo que ningun item de nomenclatura sea direccional, que ``inferences`` de los flujos quede VACIO (nada de
FULL / DELTA / federado / replicado inferido), que una frontera de procedencia nunca cuente como ``DIRECT_RAW_TABLE_ACCESS`` y que el cierre corregido nunca quite
miembros del cierre previo.
"""
from __future__ import annotations

from typing import Any

from .boundary import BOUNDARY_BASES, BOUNDARY_CLASSES, CHAIN_GAPS
from .derivation import AMBIGUITY_CODES, CLAUSES, INPUT_STATES, MAPPING_KINDS, OCCURRENCE_KINDS, OPERATION_CLASSES, TRANSFORMATION_CLASSES
from .evidence_errors import _expect, _fail, _is_int, _keys
from .facts_closure import CLOSURE_RULESET, REFERENCE_STATES
from .facts_contract_semantics import ASSOCIATION_CATEGORIES, BASIS_DECLARED_DIMENSION, BASIS_SYSTEM_TIME, CATEGORIZATION_BASES, DATATYPE_FAMILIES, FAM_DECIMAL, FIELD_ROLES, ROLE_MEASURE
from .facts_expressions import CONTAINMENTS
from .facts_harmonization_contract import CHARACTERS, CH_TECHNICAL, IMPLEMENTATIONS, LABEL_ORIGINS, MAPPING_BASES, UNDETERMINABLE_REASONS
from .facts_processing import FLOW_KINDS_WITH_PROPERTIES, UNOBSERVED_PROPERTIES
from .facts_reconvergence import ASYMMETRIES
from .facts_view_complexity import VIEW_TYPES
from .graph import PLANE_POLICY_MODELING_MEMBERSHIP
from .naming_evidence import KINDS as NAMING_KINDS, NAMING_CATEGORIES, NAMING_STATUSES

_STR_LIST = "debe ser una lista de strings"


def _strs(v: Any, path: str) -> None:
    _expect(isinstance(v, list) and all(isinstance(x, str) for x in v), path, _STR_LIST)


def _ratio(v: Any, path: str) -> None:
    _expect(v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= v <= 1.0), path, "razon fuera de [0, 1]")


# ───────────────────────────── closure ─────────────────────────────
def validate_closure(c: Any) -> None:
    p = ".closure"
    _keys(c, {"rules", "plane_policy", "reference_resolution", "plane_context"}, p, closed=True)
    _expect(c["rules"] == CLOSURE_RULESET, p, "rules no soportada")
    rr = c["reference_resolution"]
    _keys(rr, {"members_total", "counts", "platform_provided_total", "members_with_open_references"}, p + ".reference_resolution", closed=True)
    _expect(_is_int(rr["members_total"]) and rr["members_total"] >= 0, p, "members_total invalido")
    _keys(rr["counts"], set(REFERENCE_STATES), p + ".reference_resolution.counts", closed=True)
    _expect(all(_is_int(v) and v >= 0 for v in rr["counts"].values()) and _is_int(rr["platform_provided_total"]) and rr["platform_provided_total"] >= 0, p, "conteos invalidos")
    open_counts = {"UNRESOLVED": 0, "EXCLUDED_BY_PLANE": 0, "AMBIGUOUS": 0}
    for i, m in enumerate(rr["members_with_open_references"]):
        mp = f"{p}.reference_resolution.members_with_open_references[{i}]"
        _keys(m, {"artifact", "unresolved", "excluded_by_plane", "ambiguous"}, mp, closed=True)
        _expect(m["unresolved"] or m["excluded_by_plane"] or m["ambiguous"], mp, "un miembro abierto debe tener al menos una referencia abierta")
        open_counts["UNRESOLVED"] += len(m["unresolved"])
        open_counts["EXCLUDED_BY_PLANE"] += len(m["excluded_by_plane"])
        open_counts["AMBIGUOUS"] += len(m["ambiguous"])
        for r in m["ambiguous"]:
            _expect(isinstance(r.get("candidates"), list) and r["candidates"], mp, "una referencia AMBIGUA exige candidatos")
    _expect(all(rr["counts"][k] == v for k, v in open_counts.items()), p, "los conteos abiertos no coinciden con el detalle")
    pc = c["plane_context"]
    if pc is None:                                                         # sin raiz evaluable (NOT_APPLICABLE): no hay cierre que describir
        _expect(rr["members_total"] == 0 and c["plane_policy"] is None, p, "sin contexto de planos solo es valido un cierre vacio")
        return
    _expect(c["plane_policy"] == PLANE_POLICY_MODELING_MEMBERSHIP, p, "plane_policy no soportada")
    _keys(pc, {"closure_rules", "plane_policy", "flow_produced_members", "processing_only_entities", "loader_file_flag_closure_size", "added_vs_loader_file_flag",
               "removed_vs_loader_file_flag", "definition"}, p + ".plane_context", closed=True)
    _expect(pc["closure_rules"] == CLOSURE_RULESET and pc["plane_policy"] == PLANE_POLICY_MODELING_MEMBERSHIP, p + ".plane_context", "regla/politica no soportada")
    members = set()
    for i, m in enumerate(pc["flow_produced_members"]):
        mp = f"{p}.plane_context.flow_produced_members[{i}]"
        _keys(m, {"artifact", "semantic_consumers", "semantic_providers", "producer_flows", "loader_file_flag", "membership_basis", "excluded_before"}, mp, closed=True)
        _expect(m["semantic_providers"] == [], mp, "un miembro producido por un flujo es una HOJA: sin proveedores semanticos (su linaje es procedencia)")
        _expect(m["membership_basis"] == "READ_BY_SEMANTIC_MEMBER" and m["excluded_before"] == "LOADER_FILE_FLAG" and m["loader_file_flag"] is True, mp, "base de membresia invalida")
        _expect(m["semantic_consumers"], mp, "un miembro producido por un flujo debe ser LEIDO por al menos un miembro semantico")
        _strs(m["semantic_consumers"], mp)
        _strs(m["producer_flows"], mp)
        members.add(m["artifact"])
    _expect(set(pc["added_vs_loader_file_flag"]) <= members, p + ".plane_context", "todo miembro agregado debe estar explicado como producido por un flujo y leido por Modeling")
    _expect(pc["removed_vs_loader_file_flag"] == [], p + ".plane_context", "el cierre corregido nunca quita miembros del cierre previo")
    _expect(_is_int(pc["loader_file_flag_closure_size"]), p + ".plane_context", "tamano invalido")


# ───────────────────────────── physical_source_boundaries ─────────────────────────────
def validate_boundaries(b: Any) -> None:
    p = ".physical_source_boundaries"
    _keys(b, {"leaves", "boundaries_by_class", "definition"}, p, closed=True)
    total = 0
    seen = set()
    for i, rec in enumerate(b["leaves"]):
        lp = f"{p}.leaves[{i}]"
        _keys(rec, {"leaf", "boundaries", "chain_gaps", "chain_complete", "flows"}, lp, closed=True)
        _expect(rec["leaf"] not in seen, lp, "hoja duplicada")
        seen.add(rec["leaf"])
        _expect(rec["chain_complete"] == (not rec["chain_gaps"]) and isinstance(rec["chain_complete"], bool), lp, "chain_complete incoherente con chain_gaps")
        _strs(rec["flows"], lp)
        for j, g in enumerate(rec["chain_gaps"]):
            _keys(g, {"entity", "reason", "path"}, f"{lp}.chain_gaps[{j}]", closed=True)
            _expect(g["reason"] in CHAIN_GAPS, f"{lp}.chain_gaps[{j}]", "reason fuera del vocabulario")
        for j, x in enumerate(rec["boundaries"]):
            bp = f"{lp}.boundaries[{j}]"
            _keys(x, {"boundary_entity", "remote_connection", "remote_entity", "source_state", "boundary_class", "basis", "container", "path", "via_flows", "plane",
                      "is_semantic_closure_member", "counts_as_direct_raw_table_access"}, bp, closed=True)
            _expect(x["boundary_class"] in BOUNDARY_CLASSES and x["basis"] in BOUNDARY_BASES, bp, "clase/base fuera del vocabulario")
            _expect(x["plane"] == "PROVENANCE", bp, "el plano de una frontera de procedencia es PROVENANCE")
            _expect(x["counts_as_direct_raw_table_access"] is False, bp, "una frontera de procedencia NUNCA cuenta como DIRECT_RAW_TABLE_ACCESS")
            if x["basis"] == "REMOTE_ENTITY_NAMESPACE":
                _expect(x["boundary_entity"] and x["source_state"] and x["remote_entity"], bp, "base REMOTE_ENTITY_NAMESPACE exige entidad, estado y entidad remota")
            else:
                _expect(x["boundary_entity"] is None, bp, "base RF_SOURCE_CONTAINER no tiene entidad de Modeling")
            _strs(x["path"], bp)
            _strs(x["via_flows"], bp)
            total += 1
    _expect(sum(c["count"] for c in b["boundaries_by_class"]) == total, p, "boundaries_by_class inconsistente")


# ───────────────────────────── published_contract ─────────────────────────────
def validate_published(pc: Any) -> None:
    if pc is None:
        return
    p = ".published_contract"
    _keys(pc, {"associations", "fields"}, p, closed=True)
    a = pc["associations"]
    _keys(a, {"associations", "aggregates", "declared_dimensions_in_closure_not_associated", "definition"}, p + ".associations", closed=True)
    cats = {c: 0 for c in ASSOCIATION_CATEGORIES}
    for i, r in enumerate(a["associations"]):
        rp = f"{p}.associations.associations[{i}]"
        _keys(r, {"field", "target", "target_in_graph", "target_identity", "target_declared_role", "category", "categorization_basis", "foreign_key_fields"}, rp, closed=True)
        _expect(r["category"] in ASSOCIATION_CATEGORIES and r["categorization_basis"] in CATEGORIZATION_BASES, rp, "categoria/base fuera del vocabulario")
        if r["category"] == "TIME_PLATFORM_DIMENSION":
            _expect(r["categorization_basis"] == BASIS_SYSTEM_TIME, rp, "TIME_PLATFORM_DIMENSION exige identidad de plataforma probada")
        if r["category"] == "BUSINESS_DIMENSION":
            _expect(r["categorization_basis"] == BASIS_DECLARED_DIMENSION, rp, "BUSINESS_DIMENSION exige un rol de dimension declarado")
        cats[r["category"]] += 1
    ag = a["aggregates"]
    _keys(ag, {"total", "by_category", "distinct_targets", "distinct_targets_by_category"}, p + ".associations.aggregates", closed=True)
    _expect(ag["total"] == len(a["associations"]) and ag["by_category"] == cats, p, "agregados de asociaciones inconsistentes")
    _expect(ag["distinct_targets"] == len({r["target"] for r in a["associations"]}), p, "distinct_targets inconsistente")
    _strs(a["declared_dimensions_in_closure_not_associated"], p)
    f = pc["fields"]
    _keys(f, {"fields", "measure_type_composition", "counts"}, p + ".fields", closed=True)
    n_meas = n_equal = 0
    sigs: dict[tuple, int] = {}
    unresolved = 0
    for i, r in enumerate(f["fields"]):
        rp = f"{p}.fields.fields[{i}]"
        _keys(r, {"technical_name", "label", "role", "datatype", "business_name_equals_technical", "label_matches_technical_normalized", "derived",
                  "name_equals_remote_source_column", "remote_source_artifact_count", "remote_source_artifacts"}, rp, closed=True)
        _expect(r["role"] in FIELD_ROLES, rp, "role fuera del vocabulario")
        dt = r["datatype"]
        _keys(dt, {"declared_type", "family", "precision", "scale", "length_text"}, rp + ".datatype", closed=True)
        _expect(dt["family"] in DATATYPE_FAMILIES, rp, "familia de tipo fuera del vocabulario")
        _expect((dt["precision"] is None and dt["scale"] is None) or (dt["family"] == FAM_DECIMAL and _is_int(dt["precision"]) and _is_int(dt["scale"])), rp,
                "precision/escala solo para la familia DECIMAL con precision declarada")
        _expect(r["business_name_equals_technical"] == (r["label"] == r["technical_name"] and r["label"] is not None), rp, "business_name_equals_technical incoherente con la etiqueta")
        _expect(r["name_equals_remote_source_column"] == (r["remote_source_artifact_count"] > 0), rp, "name_equals_remote_source_column incoherente")
        n_equal += 1 if r["business_name_equals_technical"] else 0
        if r["role"] == ROLE_MEASURE:
            n_meas += 1
            if dt["family"] == "UNRESOLVED":
                unresolved += 1
            else:
                k = (dt["family"], dt["precision"], dt["scale"])
                sigs[k] = sigs.get(k, 0) + 1
    comp = f["measure_type_composition"]
    _keys(comp, {"measure_count", "signatures", "family_count", "signature_count", "unresolved_type_count", "definition"}, p + ".fields.measure_type_composition", closed=True)
    _expect(comp["measure_count"] == n_meas and comp["unresolved_type_count"] == unresolved, p, "measure_type_composition inconsistente con los campos")
    _expect(sum(s["count"] for s in comp["signatures"]) == n_meas - unresolved and comp["signature_count"] == len(sigs), p, "firmas de tipo inconsistentes")
    _expect(f["counts"]["fields"] == len(f["fields"]) and f["counts"]["measures"] == n_meas and f["counts"]["business_name_equals_technical"] == n_equal, p, "counts inconsistentes")


# ───────────────────────────── naming_evidence ─────────────────────────────
def validate_naming(n: Any) -> None:
    p = ".naming_evidence"
    _keys(n, {"taxonomy", "hygiene", "semantic_contract"}, p, closed=True)
    names = set()
    for i, t in enumerate(n["taxonomy"]):
        tp = f"{p}.taxonomy[{i}]"
        _keys(t, {"item", "kind", "category", "status", "directional", "evidence_facts", "note"}, tp, closed=True)
        _expect(t["category"] in NAMING_CATEGORIES and t["status"] in NAMING_STATUSES and t["kind"] in NAMING_KINDS, tp, "categoria/estado/tipo fuera del vocabulario")
        _expect(t["directional"] is False, tp, "ningun item de nomenclatura es direccional en esta entrega")
        _expect(t["item"] not in names, tp, "item duplicado")
        names.add(t["item"])
    _expect({"FIELD_VOCABULARY_MIX", "LABEL_NOT_ENRICHING", "OPAQUE_FIELD_NAME", "LABEL_NAME_MISMATCH"} <= names, p, "la taxonomia debe clasificar las reglas de nomenclatura vigentes")
    _keys(n["hygiene"], {"artifact_name_populations"}, p + ".hygiene", closed=True)
    sc = n["semantic_contract"]
    _keys(sc, {"published_contract", "harmonization_contract"}, p + ".semantic_contract", closed=True)
    _keys(sc["harmonization_contract"], {"artifacts", "by_character"}, p + ".semantic_contract.harmonization_contract", closed=True)


# ───────────────────────────── harmonization_contract ─────────────────────────────
def validate_harmonization(h: Any) -> None:
    p = ".harmonization_contract"
    _keys(h, {"artifacts", "count"}, p, closed=True)
    _expect(h["count"] == len(h["artifacts"]), p, "count inconsistente")
    seen = set()
    for i, a in enumerate(h["artifacts"]):
        ap = f"{p}.artifacts[{i}]"
        _keys(a, {"artifact", "inferred_layer", "inferred_layer_basis", "candidate_basis", "declared_role", "implementation", "sources", "producer_flows", "target_field_count",
                  "mapping_basis", "mapping_determinable", "mapping_counts", "mapping_count", "identity_mapping_ratio", "retained_source_fields", "renamed_fields",
                  "label_origin_counts", "labels_differing_from_name_and_provider", "business_name_enrichment_ratio", "labels_determinable", "derived_field_count",
                  "transformation_classes", "source_vocabulary", "character"}, ap, closed=True)
        _expect(a["artifact"] not in seen, ap, "artefacto duplicado")
        seen.add(a["artifact"])
        _expect(a["implementation"] in IMPLEMENTATIONS and (a["mapping_basis"] is None or a["mapping_basis"] in MAPPING_BASES), ap, "implementacion/base fuera del vocabulario")
        _keys(a["mapping_counts"], set(MAPPING_KINDS), ap + ".mapping_counts", closed=True)
        _keys(a["label_origin_counts"], set(LABEL_ORIGINS), ap + ".label_origin_counts", closed=True)
        n = a["target_field_count"]
        _expect(sum(a["mapping_counts"].values()) == n and sum(a["label_origin_counts"].values()) == n, ap, "los conteos de mapeo/etiquetas deben sumar los campos")
        _expect(a["mapping_count"] == n - a["mapping_counts"]["UNKNOWN"], ap, "mapping_count inconsistente")
        _expect(set(a["transformation_classes"]) <= set(TRANSFORMATION_CLASSES), ap, "clases de transformacion fuera de la taxonomia")
        _expect(a["derived_field_count"] == a["mapping_counts"]["DERIVED"] + a["mapping_counts"]["CAST"], ap, "derived_field_count inconsistente")
        _ratio(a["identity_mapping_ratio"], ap)
        _ratio(a["business_name_enrichment_ratio"], ap)
        sv = a["source_vocabulary"]
        _keys(sv, {"basis", "remote_source_artifacts", "fields_with_source_column_name", "ratio", "determinable"}, ap + ".source_vocabulary", closed=True)
        _expect(sv["determinable"] == bool(sv["remote_source_artifacts"]) and (sv["ratio"] is None) == (not sv["determinable"]), ap, "source_vocabulary incoherente")
        ch = a["character"]
        _keys(ch, {"value", "reason_unknown", "dimensions"}, ap + ".character", closed=True)
        _expect(ch["value"] in CHARACTERS, ap, "caracter fuera del vocabulario")
        _expect((ch["value"] == "UNKNOWN") == (ch["reason_unknown"] is not None), ap, "reason_unknown solo para UNKNOWN")
        _expect(ch["reason_unknown"] is None or ch["reason_unknown"] in UNDETERMINABLE_REASONS, ap, "reason_unknown fuera del vocabulario")
        if ch["value"] == CH_TECHNICAL:                                    # invariante: la materializacion tecnica exige AUSENCIA de transformacion y de vocabulario propio
            _expect(a["derived_field_count"] == 0 and a["labels_differing_from_name_and_provider"] == 0 and a["mapping_determinable"] and a["labels_determinable"], ap,
                    "TECHNICAL_MATERIALIZATION exige mapeo y etiquetas determinables y ausencia de derivacion y de etiquetas propias")


# ───────────────────────────── expression_occurrences ─────────────────────────────
def validate_expressions(e: Any) -> None:
    if e is None:
        return
    p = ".expression_occurrences"
    _keys(e, {"extractor_version", "occurrences", "trivial_predicates", "reuse_groups", "counts", "definition"}, p, closed=True)
    for i, o in enumerate(e["occurrences"]):
        op = f"{p}.occurrences[{i}]"
        _keys(o, {"artifact", "select_node", "clause", "kind", "output_name", "operations", "functions", "transformation_classes", "inputs", "exact_fp", "shape_fp", "resolvable",
                  "ambiguity", "layer", "layer_basis"}, op, closed=True)
        _expect(o["clause"] in CLAUSES and o["kind"] in OCCURRENCE_KINDS, op, "clausula/tipo fuera del vocabulario")
        _expect(isinstance(o["operations"], list) and set(o["operations"]) <= set(OPERATION_CLASSES), op, "operations fuera de la taxonomia")
        _expect(set(o["transformation_classes"]) <= set(TRANSFORMATION_CLASSES), op, "transformation_classes fuera de la taxonomia")
        _expect(set(o["ambiguity"]) <= AMBIGUITY_CODES and o["resolvable"] == (not o["ambiguity"]), op, "ambiguedad/resolubilidad inconsistentes")
        for inp in o["inputs"]:
            _expect(inp["state"] in INPUT_STATES, op, "state fuera del vocabulario")
        _expect(o["exact_fp"] and o["shape_fp"], op, "huellas vacias")
    for i, g in enumerate(e["reuse_groups"]):
        gp = f"{p}.reuse_groups[{i}]"
        _keys(g, {"exact_fp", "shape_fp", "transformation_classes", "functions", "occurrences", "occurrence_count", "artifacts", "clauses", "layers"}, gp, closed=True)
        _expect(g["occurrence_count"] == len(g["occurrences"]) and g["occurrence_count"] >= 2, gp, "un grupo de reuso exige >= 2 ocurrencias")
        _expect(set(g["clauses"]) <= set(CLAUSES) and set(g["transformation_classes"]) <= set(TRANSFORMATION_CLASSES), gp, "clausulas/clases fuera del vocabulario")
        for m in g["occurrences"]:
            _keys(m, {"artifact", "clause", "select_node", "containment", "kind", "layer"}, gp, closed=True)
            _expect(m["containment"] in CONTAINMENTS and m["clause"] in CLAUSES, gp, "containment/clause fuera del vocabulario")
    _expect(e["counts"]["occurrences"] == len(e["occurrences"]) and e["counts"]["reuse_groups"] == len(e["reuse_groups"]), p, "counts inconsistentes")


# ───────────────────────────── branch_reconvergence ─────────────────────────────
def validate_reconvergence(r: Any) -> None:
    if r is None:
        return
    p = ".branch_reconvergence"
    _keys(r, {"extractor_version", "reconvergences", "counts", "definition"}, p, closed=True)
    for i, x in enumerate(r["reconvergences"]):
        xp = f"{p}.reconvergences[{i}]"
        _keys(x, {"shared_provider", "branch_a", "branch_b", "fields_consumed_a", "fields_consumed_b", "overlap", "jaccard", "subset_ratio", "consumer_asymmetry", "shared_fields",
                  "common_upstream_count", "common_upstream_identities", "common_upstream_truncated", "reconvergence_artifact", "branch_depths", "paths_root_to_shared_provider",
                  "path_depth_min", "path_depth_max"}, xp, closed=True)
        _expect(x["consumer_asymmetry"] in ASYMMETRIES, xp, "asimetria fuera del vocabulario")
        _expect(x["branch_a"] != x["branch_b"] and x["reconvergence_artifact"] not in (x["branch_a"], x["branch_b"]), xp, "ramas/reconvergencia incoherentes")
        _expect(0 <= x["overlap"] <= min(x["fields_consumed_a"], x["fields_consumed_b"]), xp, "overlap fuera de rango")
        _ratio(x["jaccard"], xp)
        _ratio(x["subset_ratio"], xp)
        _expect(_is_int(x["paths_root_to_shared_provider"]) and x["paths_root_to_shared_provider"] >= 1 and x["path_depth_min"] <= x["path_depth_max"], xp, "caminos/profundidad invalidos")
    _expect(r["counts"]["pairs"] == len(r["reconvergences"]), p, "counts inconsistentes")


# ───────────────────────────── view_complexity ─────────────────────────────
def validate_complexity(v: Any) -> None:
    p = ".view_complexity"
    _keys(v, {"views", "count"}, p, closed=True)
    _expect(v["count"] == len(v["views"]), p, "count inconsistente")
    for i, a in enumerate(v["views"]):
        ap = f"{p}.views[{i}]"
        _keys(a, {"artifact", "view_type", "view_type_basis", "inferred_layer", "inferred_layer_basis", "providers", "provider_names", "providers_in_closure",
                  "dependents_in_closure", "metrics"}, ap, closed=True)
        _expect(a["view_type"] in VIEW_TYPES and a["providers"] == len(a["provider_names"]), ap, "tipo de vista/proveedores invalidos")
        m = a["metrics"]
        _expect(isinstance(m, dict) and all(_is_int(m[k]) and m[k] >= 0 for k in ("select_nodes", "graph_depth", "joins", "unions", "output_columns", "columns_total", "derived_columns",
                                                                                     "function_calls", "expressions")), ap, "metricas invalidas")
        _expect(m["graph_depth"] >= 1 and m["select_nodes"] >= 1, ap, "una vista con query tiene al menos un nodo SELECT")


# ───────────────────────────── processing_flow_properties ─────────────────────────────
def validate_processing(f: Any) -> None:
    if f is None:
        return
    p = ".processing_flow_properties"
    _keys(f, {"extractor_version", "flows", "counts", "unobserved_properties", "inferences", "definition"}, p, closed=True)
    _expect(f["inferences"] == [], p, "no se infiere FULL / DELTA / incremental / federado / replicado: inferences debe quedar vacio")
    _expect([u["property"] for u in f["unobserved_properties"]] == list(UNOBSERVED_PROPERTIES) and all(u["reason"] == "NOT_IN_EXPORT" for u in f["unobserved_properties"]), p,
            "las propiedades no observadas deben declararse (calendarizacion, estado federado/replicado, modo de ejecucion)")
    for i, fl in enumerate(f["flows"]):
        fp = f"{p}.flows[{i}]"
        _keys(fl, {"flow_kind", "flow_name", "closure_targets", "sources", "targets", "properties", "observed"}, fp, closed=True, optional={"source_filters"})
        _expect(fl["flow_kind"] in FLOW_KINDS_WITH_PROPERTIES and fl["closure_targets"], fp, "tipo de flujo invalido o sin destino en el cierre")
    _expect(f["counts"]["flows"] == len(f["flows"]), p, "counts inconsistentes")


def validate_v2_sections(ce: dict, root_is_am: bool | None = None) -> None:
    validate_closure(ce["closure"])
    validate_boundaries(ce["physical_source_boundaries"])
    validate_published(ce["published_contract"])
    validate_naming(ce["naming_evidence"])
    validate_harmonization(ce["harmonization_contract"])
    validate_expressions(ce["expression_occurrences"])
    validate_reconvergence(ce["branch_reconvergence"])
    validate_complexity(ce["view_complexity"])
    validate_processing(ce["processing_flow_properties"])
