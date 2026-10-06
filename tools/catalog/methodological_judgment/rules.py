"""Reglas del Juicio metodologico v1.1 (Gate 8C.1 / 8D.1) como PREDICADOS sobre la evidencia canonica persistida. Sin nombres de cliente/raiz, sin puntajes, sin pesos.

Familias:  SRC- (Fuente/Ingesta) · HAR- (Armonizacion) · PRO-/LEK- (Propagacion) · PUB- (Publicado) · GOV- (naming) · MNT- (mantenibilidad) · FOC- (foco).

MATERIALIDAD = causalidad arquitectonica: (1) existe una responsabilidad esperada, (2) no esta resuelta en el contrato esperado, (3) esta compensada aguas abajo / por otra capa /
por el consumidor. NO es un conteo. Nada de lo siguiente es MATERIAL por si solo: tabla fisica cruda, vista grafica o SQL, proyeccion 1:1, ausencia/presencia de asociaciones,
reconvergencia, heterogeneidad de tipos, divergencia de naming, cantidad de campos, ausencia de hallazgos, tecnologia de la fuente.

Los unicos CORTES numericos del modulo son de CALIFICADORES (``design_coherence``, ``COMPLEX_GRAPHICAL_CHAIN_REVIEW``, estado de naming) y no tienen efecto sobre el nivel.
"""
from __future__ import annotations

from typing import Any, Mapping

from tools.catalog.methodological_judgment.profile_access import (
    EXTERNAL_CONTRACT_UNDETERMINED, GOVERNED_SAP_CONTRACT, K_BOUNDARY, K_HARM, K_LOCAL_LEAF, K_VIEW, PHYSICAL_RAW_BOUNDARY, ProfileEvidence, sorted_unique,
)

from . import contract as C

# ───────── cortes de CALIFICADORES (sin efecto sobre el nivel) ─────────
GRAPHICAL_CHAIN_MIN_DEPTH = 3
GRAPHICAL_CHAIN_MIN_SELECT_NODES = 3
NAMING_MINOR_MAX_RATIO = 0.25
NAMING_SYSTEMATIC_MIN_RATIO = 0.5
HARMONIZATION_DEPTH_SPREAD_MIN = 2           # HAR-REL-02: nodos de rol no declarado a >= 2 distancias distintas (estructura de capas, no severidad)

_GOVERNED_BOUNDARY_CLASSES = ("GOVERNED_CDS_CONTRACT", "STANDARD_VIEW_ON_PHYSICAL_TABLE")


# ───────────────────────────── constructores ─────────────────────────────
def _reason(rule_id: str, classification: str, dimension: str, statement_key: str, scope: str, *, artifacts, refs, basis, secondary: str | None = None,
            causal: Mapping[str, str] | None = None, disposition_allowed: bool = True, max_class: str | None = None, evaluability: str = C.EVALUATED,
            ev: ProfileEvidence) -> dict:
    out = {
        "reason_id": f"{rule_id}:{scope}", "rule_id": rule_id, "classification": classification, "dimension": dimension, "statement_key": statement_key,
        "affected_artifacts": sorted_unique(artifacts), "evidence_refs": sorted_unique(r for r in refs if ev.ref_exists(r)), "deterministic_basis": list(basis),
        "evaluability": evaluability,
        "disposition": {"allowed": disposition_allowed, "required": classification in (C.MATERIAL_DEVIATION, C.RELEVANT_WEAKNESS) and disposition_allowed,
                        "max_class_if_confirmed": max_class or classification},
        "state": C.OPEN_CANDIDATE,
    }
    if secondary:
        out["secondary_dimension"] = secondary
    if causal:
        out["causal_conditions"] = dict(causal)
    return out


def _strength(rule_id: str, dimension: str, statement_key: str, *, artifacts, refs, basis, ev: ProfileEvidence) -> dict:
    return {"strength_id": f"{rule_id}:closure", "rule_id": rule_id, "classification": C.POSITIVE_EVIDENCE, "dimension": dimension, "statement_key": statement_key,
            "affected_artifacts": sorted_unique(artifacts), "evidence_refs": sorted_unique(r for r in refs if ev.ref_exists(r)), "deterministic_basis": list(basis)}


def _review(review_id: str, rule_id: str, family: str, state: str, *, artifacts=(), refs=(), level_effect: str = "NONE", closure_effect: str = "NONE", ev: ProfileEvidence,
            extra: Mapping[str, Any] | None = None) -> dict:
    out = {"review_id": review_id, "rule_id": rule_id, "family": family, "state": state, "affected_artifacts": sorted_unique(artifacts),
           "evidence_refs": sorted_unique(r for r in refs if ev.ref_exists(r)), "level_effect": level_effect, "closure_effect": closure_effect}
    if extra:
        out.update(extra)
    return out


def _limitation(key: str, *, refs=(), artifacts=(), can_change_level: bool = False, ev: ProfileEvidence) -> dict:
    return {"limitation_id": key, "key": key, "affected_artifacts": sorted_unique(artifacts), "evidence_refs": sorted_unique(r for r in refs if ev.ref_exists(r)),
            "can_change_level": can_change_level}


def _fact_ref(ev: ProfileEvidence, fact_id: str, subject: str) -> str:
    return f"fact:{fact_id}@{subject}"


# ───────────────────────────── utilidades de dominio ─────────────────────────────
def _local_leaf_source_class(ev: ProfileEvidence, leaf: str) -> str:
    """Clase de contacto de una hoja LOCAL alimentada por un flujo, segun las fronteras de su cadena de procedencia (plano de procedencia)."""
    rec = next((l for l in (ev.section("physical_source_boundaries").get("leaves") or ()) if l.get("leaf") == leaf), None)
    if not rec or not rec.get("boundaries"):
        return EXTERNAL_CONTRACT_UNDETERMINED
    classes = {b.get("boundary_class") for b in rec["boundaries"]}
    if any(c == "PHYSICAL_TABLE" for c in classes):
        return PHYSICAL_RAW_BOUNDARY
    if all(c in _GOVERNED_BOUNDARY_CLASSES for c in classes):
        return GOVERNED_SAP_CONTRACT
    return EXTERNAL_CONTRACT_UNDETERMINED


def _leaf_reaches_product(ev: ProfileEvidence, leaf: str) -> bool:
    nodes = set(ev.product_nodes)
    return bool(nodes & (set(ev.descendants(leaf)) | {leaf}))


def _temporal_reuse_groups(ev: ProfileEvidence) -> list[Mapping[str, Any]]:
    out = []
    for g in ev.reuse_groups:
        if "TEMPORAL" not in (g.get("transformation_classes") or ()):
            continue
        sites = {(o.get("artifact"), o.get("clause")) for o in g.get("occurrences") or ()}
        arts = {a for a, _ in sites}
        if len(sites) >= 2 and not (arts & set(ev.harmonization)):
            out.append(g)
    return out


def _graphical_views(ev: ProfileEvidence) -> list[Mapping[str, Any]]:
    return [v for v in ev.views.values() if v.get("view_type") == "GRAPHICAL"]


def _logic_descendants(ev: ProfileEvidence, leaf: str) -> list[str]:
    return [d for d in ev.descendants(leaf) if d not in ev.harmonization and ev.view_has_logic(d)]


# ───────────────────────────── causalidad compartida (Gate 8D.1b) ─────────────────────────────
def _raw_on_strict_spine(ev: ProfileEvidence, leaf: str) -> bool:
    return ev.raw_occurrences.get(leaf, {}).get("reaches_terminal_by_strict_lineage") is True or (leaf not in ev.raw_occurrences and _leaf_reaches_product(ev, leaf))


def _raw_bypass_compensated(ev: ProfileEvidence, policy: str) -> dict[str, list[str]]:
    """Hoja fisica cruda (en la espina estricta) CUYO camino hacia el producto OMITE la frontera reutilizable (ningun candidato de armonizacion en ese camino: frontera ausente u omitida)
    y es COMPENSADA por logica de Modelado (nodos no candidatos con joins/derivaciones/filtros/uniones sobre ese camino). Una cruda en la espina que SI atraviesa un candidato, o que
    no es compensada, es evidencia de fuente (LOCAL/apoyo), no una debilidad de nivel. hoja -> nodos de compensacion."""
    out: dict[str, list[str]] = {}
    for leaf in sorted(l for l in ev.remote_leaves if ev.classify_source(l, policy) == PHYSICAL_RAW_BOUNDARY):
        if not _raw_on_strict_spine(ev, leaf):
            continue
        logic = [n for n in ev.bypass_path_nodes(leaf) if n not in ev.harmonization and ev.view_has_logic(n)]
        if logic:
            out[leaf] = logic
    return out


def _downstream_created_semantics(ev: ProfileEvidence) -> tuple[list[dict], list[dict]]:
    """L1(b) generico: una responsabilidad semantica reutilizable (expresion TEMPORAL reutilizada que NINGUN candidato posee) que debio resolverse en una frontera de armonizacion queda
    creada/poseida AGUAS ABAJO. Califica solo si converge A (existe aguas arriba de los sitios una frontera de armonizacion demostrablemente TECNICA, es decir, que no la resuelve) y
    B (la responsabilidad se crea/reutiliza en >=2 nodos distintos no candidatos aguas abajo de esa frontera: la reutilizacion es la evidencia de propiedad duplicada). No se infiere por
    nombres de campo. (calificados, no calificados)."""
    qual, unq = [], []
    for g in _temporal_reuse_groups(ev):
        arts = sorted({o.get("artifact") for o in g.get("occurrences") or () if o.get("artifact")})
        tech = sorted({c for a in arts for c in ev.ancestors(a) if c in ev.harmonization and (ev.harmonization[c].get("character") or {}).get("value") == "TECHNICAL_MATERIALIZATION"})
        owned_downstream = len(arts) >= 2 and all(a not in ev.harmonization and a in ev.views for a in arts)
        rec = {"exact_fp": g.get("exact_fp"), "artifacts": arts, "technical_upstream_candidates": tech, "A_boundary_demonstrably_technical": bool(tech), "B_owned_downstream_in_multiple_nodes": owned_downstream}
        (qual if (tech and owned_downstream) else unq).append(rec)
    return qual, unq


# Procedencia canonica de cada familia: SOLO informativa (trazabilidad). La independencia metodologica NO se decide por seccion (dos secciones != dos verdades): se decide por TESTIGOS, es decir
# por hechos subyacentes distintos. La seccion/fuente es un mecanismo de implementacion para no contar dos veces la misma observacion, no la regla.
FAMILY_PROVENANCE = {"ASSOCIATION_AUTONOMY": "published_contract.associations", "MEASURE_SEMANTICS": "published_contract.fields", "ATTRIBUTE_LABEL_ENRICHMENT": "published_contract.fields",
                     "SOURCE_VOCABULARY_CARRY_THROUGH": "published_contract.fields", "TECHNICAL_PROJECTION_REACHING_PUBLICATION": "harmonization_contract"}
CONSUMER_FAMILIES = ("ASSOCIATION_AUTONOMY", "MEASURE_SEMANTICS", "ATTRIBUTE_LABEL_ENRICHMENT", "SOURCE_VOCABULARY_CARRY_THROUGH")      # evidencia del contrato publicado / consumidor


def _published_families(ev: ProfileEvidence, *, include_projection: bool) -> dict[str, set[str]]:
    """CONSUMER_SEMANTIC_DEFICIENCY_EVIDENCE: senales de que el publicado no restaura la semantica. Cada senal es EVIDENCIA DE DEFICIENCIA (nunca exculpatoria) y aporta TESTIGOS (hechos
    subyacentes: ``field:<campo>``, ``dim:<dimension>``, ``assoc:...``, ``cand:<candidato>``).
    - ASSOCIATION_AUTONOMY: autonomia de maestros debil = BRECHAS concretas: dimensiones analiticas declaradas en el cierre que el publicado NO asocia (evidencia por dimension, independiente de
      cuantas otras asociaciones existan). La EXISTENCIA de una o varias asociaciones es evidencia positiva LOCALIZADA: no prueba autonomia suficiente y NO veta nada. La AUSENCIA total de asociaciones
      es solo evidencia de apoyo (``supporting_evidence.published_business_association_targets`` vacio): no es una familia ni establece nada por si sola.
    - MEASURE_SEMANTICS / ATTRIBUTE_LABEL_ENRICHMENT: medidas / atributos visibles cuya etiqueta es su propio nombre tecnico (una medida o un campo enriquecido tampoco prueba restauracion).
    - SOURCE_VOCABULARY_CARRY_THROUGH: campos visibles con nombre = columna de la fuente remota.
    - TECHNICAL_PROJECTION_REACHING_PUBLICATION (fuera del contrato publicado): frontera de armonizacion aguas arriba del producto que es proyeccion tecnica con vocabulario fuente (premisa de
      HAR-REL-01, por eso ``include_projection=False`` en esa regla).
    Presencia categorica por familia; ningun conteo ni proporcion; ninguna senal suficiente por si sola."""
    fam: dict[str, set[str]] = {}
    gaps = {f"dim:{d if isinstance(d, str) else d.get('artifact', str(d))}" for d in ev.declared_dimensions_not_associated}
    if gaps:
        fam["ASSOCIATION_AUTONOMY"] = gaps
    fields = ev.published_fields
    meas = {f"field:{f.get('technical_name')}" for f in fields if f.get("role") == "MEASURE" and f.get("business_name_equals_technical")}
    attr = {f"field:{f.get('technical_name')}" for f in fields if f.get("role") != "MEASURE" and f.get("business_name_equals_technical")}
    voc = {f"field:{f.get('technical_name')}" for f in fields if f.get("name_equals_remote_source_column")}
    if meas:
        fam["MEASURE_SEMANTICS"] = meas
    if attr:
        fam["ATTRIBUTE_LABEL_ENRICHMENT"] = attr
    if voc:
        fam["SOURCE_VOCABULARY_CARRY_THROUGH"] = voc
    if include_projection:
        upstream = set(ev.product_nodes) | {a for n in ev.product_nodes for a in ev.ancestors(n)}
        proj = {f"cand:{c}" for c in upstream if c in ev.harmonization and (ev.harmonization[c].get("character") or {}).get("value") == "TECHNICAL_MATERIALIZATION"
                and ((ev.harmonization[c].get("source_vocabulary") or {}).get("fields_with_source_column_name") or 0) > 0}
        if proj:
            fam["TECHNICAL_PROJECTION_REACHING_PUBLICATION"] = proj
    return fam


def _converging_families(fam: Mapping[str, set[str]]) -> list[str]:
    """Familias que convergen: pares donde CADA familia aporta testigos que la otra no tiene (hechos subyacentes distintos: dos proyecciones del mismo campo/hecho NO son dos evidencias) y al menos UNA familia del contrato
    publicado/consumidor en el par. Ninguna senal es suficiente por si sola ni exculpatoria; no hay conteos ni umbrales."""
    names = sorted(fam)
    out: set[str] = set()
    for i, x in enumerate(names):
        for y in names[i + 1:]:
            if (x in CONSUMER_FAMILIES or y in CONSUMER_FAMILIES) and (fam[x] - fam[y]) and (fam[y] - fam[x]):
                out.update((x, y))
    return sorted(out)


# ───────────────────────────── MATERIAL ─────────────────────────────
def rule_pro_mat_01(ev: ProfileEvidence, policy: str) -> list[dict]:
    """PRO-MAT-01 — el nodo de PRODUCTO (posicion: proveedor directo del AM) re-ejecuta integracion/armonizacion: (i) universo mixto (consume un candidato de
    armonizacion Y una hoja de frontera directa) o (ii) integra >=2 hojas de frontera (>=1 fisica cruda) sin ningun candidato de armonizacion en el cierre.
    Las hojas EXTERNAL_CONTRACT_UNDETERMINED y de sistema quedan EXCLUIDAS (no crean MATERIAL)."""
    out: list[dict] = []
    for node in ev.product_nodes:
        eff = ev.effective_providers(node)
        qualified = {p: ev.classify_source(p, policy) for p in eff.get(K_BOUNDARY, ())}
        qualified = {p: c for p, c in qualified.items() if c in (GOVERNED_SAP_CONTRACT, PHYSICAL_RAW_BOUNDARY)}
        harm = eff.get(K_HARM, [])
        form = None
        fact_level_harm = [h for h in harm if not ((ev.harmonization.get(h) or {}).get("declared_role") or {}).get("declares_dimension")]
        if harm and qualified and (fact_level_harm or PHYSICAL_RAW_BOUNDARY in qualified.values()):
            # universo mixto: el producto consume un contrato armonizado de nivel hecho (no solo una dimension reutilizable) o alcanza una tabla fisica cruda, Y ademas
            # lee hojas de frontera directamente. Componer un hecho gobernado con una dimension armonizada es composicion normal, no re-integracion.
            form = "i_mixed_universe"
        elif len(qualified) >= 2 and PHYSICAL_RAW_BOUNDARY in qualified.values() and not ev.harmonization:
            form = "ii_multi_boundary_integration_without_harmonization"
        if not form:
            continue
        refs = [f"ce:view_complexity/{node}", f"ce:assembly_topology/{node}", *[_fact_ref(ev, "source.contract", p) for p in qualified], *[f"ce:harmonization_contract/{h}" for h in harm]]
        out.append(_reason(
            "PRO-MAT-01", C.MATERIAL_DEVIATION, C.PROPAGATION, "pro.product_node_reperforms_integration", node, ev=ev, secondary=C.HARMONIZATION,
            artifacts=[node, *qualified, *harm], refs=refs,
            basis=["PRO.product_position_node", f"PRO.form.{form}", "PRO.boundary_leaf_directly_consumed", *(["PRO.harmonized_contract_also_consumed"] if harm else []),
                   *(["PRO.no_harmonization_candidate_in_closure"] if not ev.harmonization else [])],
            causal={"expected_responsibility": "product composes an already resolved reusable contract",
                    "unresolved": "boundary-level input reaches the product node without a resolved reusable boundary",
                    "compensation": "the product node performs source integration / harmonization itself"}))
    return out


def rule_lek_mat_02(ev: ProfileEvidence, policy: str) -> list[dict]:
    """LEK-MAT-02 — fuga de responsabilidad DISTRIBUIDA con publicacion no restaurada.
    L1 (se exige (a) o (b); (c) solo APOYA): (a) tabla fisica cruda en la espina que OMITE la frontera reutilizable y es compensada por logica de Modelado; (b) semantica reutilizable creada
    aguas abajo sobre una frontera de armonizacion tecnica (ver ``_downstream_created_semantics``); (c) hoja de frontera clasificada consumida directamente en el nodo de producto (apoyo).
    L2: esa responsabilidad es absorbida por nodos que NO son candidatos de armonizacion y ejecutan logica.
    L3: el publicado no la restaura: convergen >=2 FAMILIAS independientes de evidencia semantica (asociacion/maestros, enriquecimiento de etiquetas, arrastre de vocabulario fuente,
    proyeccion tecnica). Una sola familia (p. ej. una medida sin enriquecer) NO alcanza; no hay proporcion ni mayoria."""
    elements: dict[str, list[str]] = {}
    l2_nodes: set[str] = set()
    refs: list[str] = []
    for leaf, logic in _raw_bypass_compensated(ev, policy).items():
        elements.setdefault("a_physical_raw_bypassing_harmonization_compensated_downstream", []).append(leaf)
        l2_nodes.update(logic)
        refs += [_fact_ref(ev, "source.downstream_reach", leaf), _fact_ref(ev, "source.contract", leaf)]
    qual, _ = _downstream_created_semantics(ev)
    for q in qual:
        elements.setdefault("b_reusable_semantics_created_downstream_over_technical_boundary", []).extend([*q["artifacts"], *q["technical_upstream_candidates"]])
        l2_nodes.update(q["artifacts"])
        refs += ["ce:expression_occurrences", *[f"ce:harmonization_contract/{c}" for c in q["technical_upstream_candidates"]]]
    for node in ev.product_nodes:
        eff = ev.effective_providers(node)
        leaves = [p for p in eff.get(K_BOUNDARY, ()) if ev.classify_source(p, policy) in (GOVERNED_SAP_CONTRACT, PHYSICAL_RAW_BOUNDARY)]
        if leaves and ev.view_has_logic(node):
            elements.setdefault("c_boundary_leaf_at_product_node", []).extend([node, *leaves])
            l2_nodes.add(node)
            refs += [f"ce:view_complexity/{node}", *[_fact_ref(ev, "source.contract", p) for p in leaves]]
    if not (elements.keys() & {"a_physical_raw_bypassing_harmonization_compensated_downstream", "b_reusable_semantics_created_downstream_over_technical_boundary"}):
        return []                                                                      # (c) por si solo NO basta: una hoja en el nodo de producto es solo evidencia de apoyo
    fam = _published_families(ev, include_projection=True)
    converging = _converging_families(fam)
    if not converging:
        return []                                                                      # L3 no se cumple: ninguna convergencia de >=2 familias independientes
    refs += ["ce:published_contract/fields", "ce:published_contract/associations"]
    if "TECHNICAL_PROJECTION_REACHING_PUBLICATION" in converging:
        refs += ["ce:harmonization_contract"]
    arts = [a for v in elements.values() for a in v] + sorted(l2_nodes)
    return [_reason(
        "LEK-MAT-02", C.MATERIAL_DEVIATION, C.PROPAGATION, "lek.unrestored_responsibility_leakage", "closure", ev=ev, secondary=C.PUBLISHED_CONTRACT,
        artifacts=arts, refs=refs,
        basis=[*[f"LEK.L1.{k}" for k in sorted(elements)], "LEK.L2.absorbed_by_non_candidate_nodes_with_logic", "LEK.L3.published_non_restoration_independent_families",
               *[f"LEK.L3.family.{f}" for f in converging]],
        causal={"expected_responsibility": "reusable source/semantic responsibility resolved at its expected boundary",
                "unresolved": ", ".join(sorted(elements)), "compensation": "absorbed by downstream non-harmonization nodes",
                "published_not_restored": "converging independent evidence families: " + ", ".join(converging)})]


# ───────────────────────────── RELEVANT / LOCAL ─────────────────────────────
def rule_src_rel_loc(ev: ProfileEvidence, policy: str) -> list[dict]:
    """SRC-REL-01 (RELEVANT): tabla fisica cruda en la espina cuyo camino al producto OMITE la frontera reutilizable (ausente u omitida) Y la responsabilidad es compensada por logica de
    Modelado. Cualquier otra cruda (fuera de la espina, a traves de un candidato, o sin compensacion) es SRC-LOC-01 (LOCAL, evidencia de fuente). Nunca MATERIAL sola; no es 'cruda en espina'."""
    comp = _raw_bypass_compensated(ev, policy)
    rel = sorted(comp)
    loc = sorted(l for l in ev.remote_leaves if ev.classify_source(l, policy) == PHYSICAL_RAW_BOUNDARY and l not in comp)
    out = []
    if rel:
        out.append(_reason("SRC-REL-01", C.RELEVANT_WEAKNESS, C.SOURCE_INGESTION, "src.physical_boundary_bypassed_and_compensated_downstream", "closure", ev=ev,
                           artifacts=[*rel, *sorted({n for v in comp.values() for n in v})], refs=[_fact_ref(ev, "source.downstream_reach", l) for l in rel],
                           basis=["SRC.physical_abap_table", "SRC.on_strict_spine", "SRC.path_to_product_bypasses_harmonization", "SRC.compensated_by_modeling_logic"]))
    if loc:
        out.append(_reason("SRC-LOC-01", C.LOCAL_WEAKNESS, C.SOURCE_INGESTION, "src.physical_reach_without_bypass_and_compensation", "closure", ev=ev, artifacts=loc,
                           refs=[_fact_ref(ev, "source.downstream_reach", l) for l in loc], basis=["SRC.physical_abap_table", "SRC.off_spine_or_harmonized_or_not_compensated"]))
    return out


def _aligns_branches(ev: ProfileEvidence, node: str) -> bool:
    """El nodo de producto completa la alineacion/normalizacion de ramas (union + derivaciones) sobre vistas/contratos, sin hojas de frontera: trabajo que la armonizacion no resolvio."""
    m = ((ev.views.get(node) or {}).get("metrics")) or {}
    return bool(m.get("unions") and m.get("derived_columns")) and not (set(ev.effective_providers(node)) - {K_VIEW, K_HARM})


def rule_har_rel_01(ev: ProfileEvidence) -> list[dict]:
    """HAR-REL-01 — Armonizacion tecnica/no resuelta (todos los candidatos observables son materializacion tecnica que conserva el vocabulario de la fuente) MAS una CONSECUENCIA semantica
    observable aguas abajo: (i) el publicado no restaura la semantica (>=2 familias independientes; la proyeccion tecnica es la premisa, no cuenta) o (ii) semantica reutilizable creada
    aguas abajo sobre esa frontera tecnica. Una armonizacion tecnica SOLA es evidencia descriptiva (``supporting_evidence.technical_harmonization_candidates``), no debilidad."""
    cands = [c for c in ev.harmonization.values() if (c.get("character") or {}).get("value") != "UNKNOWN"]
    if not cands:
        return []
    if any((c.get("character") or {}).get("value") != "TECHNICAL_MATERIALIZATION" for c in cands):
        return []
    carry = [c["artifact"] for c in cands if ((c.get("source_vocabulary") or {}).get("fields_with_source_column_name") or 0) > 0]
    if len(carry) != len(cands):
        return []
    consequence, refs = [], []
    conv = _converging_families(_published_families(ev, include_projection=False))
    if conv:
        consequence += ["HAR.consequence.published_non_restoration_independent_families", *[f"HAR.consequence.family.{f}" for f in conv]]
        refs += ["ce:published_contract/fields", "ce:published_contract/associations"]
    qual, _ = _downstream_created_semantics(ev)
    if qual:
        consequence.append("HAR.consequence.reusable_semantics_created_downstream")
        refs.append("ce:expression_occurrences")
    aligned = [n for n in ev.product_nodes if _aligns_branches(ev, n)]
    if aligned:
        consequence.append("HAR.consequence.propagation_completes_branch_alignment")
        refs += [f"ce:view_complexity/{n}" for n in aligned]
    if not consequence:
        return []
    return [_reason("HAR-REL-01", C.RELEVANT_WEAKNESS, C.HARMONIZATION, "har.technical_projection_with_downstream_semantic_consequence", "closure", ev=ev,
                    artifacts=carry, refs=[*[f"ce:harmonization_contract/{a}" for a in carry], *refs],
                    basis=["HAR.all_observable_candidates_technical_materialization", "HAR.source_vocabulary_carried_through", *consequence])]


def rule_har_rel_02(ev: ProfileEvidence) -> list[dict]:
    """HAR-REL-02 — responsabilidades no separables por rol declarado: nodos de composicion con rol NO declarado a >=2 distancias distintas + reconvergencia + candidatos de
    armonizacion coexistiendo con integracion aguas arriba. Es estructura de capas; no cuenta nodos ni mide complejidad."""
    undeclared = {n: a for n, a in ev.assembly_nodes.items() if (ev.views.get(n) or {}).get("inferred_layer_basis") not in (None, "DECLARED_ROLE")
                  and a.get("lineage_distance_to_root") is not None}
    depths = {a["lineage_distance_to_root"] for a in undeclared.values()}
    if len(depths) < HARMONIZATION_DEPTH_SPREAD_MIN or not ev.reconvergences or not ev.harmonization:
        return []
    return [_reason("HAR-REL-02", C.RELEVANT_WEAKNESS, C.HARMONIZATION, "har.fragmented_layers_with_undeclared_roles", "closure", ev=ev, artifacts=sorted(undeclared),
                    refs=["ce:assembly_topology", "ce:branch_reconvergence", *[f"ce:assembly_topology/{n}" for n in undeclared]],
                    basis=["HAR.undeclared_role_nodes_at_multiple_distances", "HAR.branch_reconvergence_present", "HAR.harmonization_candidates_coexist"])]


def rule_pro_loc_01(ev: ProfileEvidence, mat_nodes: set[str]) -> list[dict]:
    out = []
    for node in ev.product_nodes:
        v = ev.views.get(node) or {}
        m = v.get("metrics") or {}
        eff = ev.effective_providers(node)
        if node in mat_nodes or not (m.get("unions") and m.get("derived_columns")) or set(eff) - {K_VIEW, K_HARM}:
            continue
        out.append(_reason("PRO-LOC-01", C.LOCAL_WEAKNESS, C.PROPAGATION, "pro.branch_alignment_completed_at_product", node, ev=ev, artifacts=[node],
                           refs=[f"ce:view_complexity/{node}"], basis=["PRO.product_node_union_with_derivations", "PRO.no_boundary_leaf_at_product"]))
    return out


# ───────────────────────────── FORTALEZAS ─────────────────────────────
def strengths(ev: ProfileEvidence, policy: str, has_pro_mat: bool) -> list[dict]:
    out: list[dict] = []
    # SRC-STR-01 — adquisicion gobernada (todos los contactos con la fuente en clase gobernada; al menos uno)
    remote = [l for l in ev.remote_leaves]
    local = [l for l in ev.local_leaves]
    contacts_ok = ([ev.classify_source(l, policy) == GOVERNED_SAP_CONTRACT for l in remote]
                   + [(_local_leaf_source_class(ev, l) == GOVERNED_SAP_CONTRACT and (ev.local_leaves[l].get("provenance_state") in ("ESTABLISHED", "DECLARED"))) for l in local])
    if contacts_ok and all(contacts_ok):
        out.append(_strength("SRC-STR-01", C.SOURCE_INGESTION, "src.governed_acquisition_explicit", ev=ev, artifacts=[*remote, *local],
                             refs=["ce:physical_source_boundaries", "ce:leaf_provenance", *[_fact_ref(ev, "source.contract", l) for l in remote]],
                             basis=["SRC.every_source_contact_in_governed_class", "SRC.local_leaves_provenance_established"]))
    # SRC-STR-02 — carga explicita observada (descriptiva; sin inferir FULL/DELTA)
    flows = (ev.section("processing_flow_properties").get("flows") or ())
    delta_obs = []
    for f in flows:
        p = f.get("properties") or {}
        tasks = p.get("tasks") or []
        if p.get("delta_load_trigger") or any(t.get("change_capture_properties") or t.get("delta_check_interval") for t in tasks):
            delta_obs.append(f.get("flow_name"))
    if delta_obs:
        out.append(_strength("SRC-STR-02", C.SOURCE_INGESTION, "src.explicit_incremental_acquisition_observed", ev=ev, artifacts=[], refs=["ce:processing_flow_properties"],
                             basis=["SRC.explicit_change_capture_or_delta_property_present_in_export"]))
    # HAR-STR-01 / HAR-STR-02
    cands = [c for c in ev.harmonization.values()]
    if cands and all((c.get("character") or {}).get("value") == "TECHNICAL_MATERIALIZATION" for c in cands):
        legit = []
        for c in cands:
            srcs = c.get("sources") or []
            gov = bool(srcs) and all((ev.classify_source(s, policy) == GOVERNED_SAP_CONTRACT) if s in ev.remote_leaves
                                     else (s in ev.local_leaves and _local_leaf_source_class(ev, s) == GOVERNED_SAP_CONTRACT) for s in srcs)
            lab = c.get("label_origin_counts") or {}
            no_carry = ((c.get("source_vocabulary") or {}).get("fields_with_source_column_name") or 0) == 0
            legit.append(gov and no_carry and not lab.get("DIFFERENT_FROM_PROVIDER") and not lab.get("UNKNOWN_PROVIDER_LABEL") and not lab.get("NO_LABEL"))
        if all(legit):
            out.append(_strength("HAR-STR-01", C.HARMONIZATION, "har.legitimate_reusable_boundary_over_governed_contract", ev=ev, artifacts=[c["artifact"] for c in cands],
                                 refs=[f"ce:harmonization_contract/{c['artifact']}" for c in cands],
                                 basis=["HAR.one_to_one_over_governed_contract", "HAR.business_vocabulary_preserved", "HAR.no_downstream_compensation"]))
    enriched = [c["artifact"] for c in cands if (c.get("label_origin_counts") or {}).get("DIFFERENT_FROM_PROVIDER") or (c.get("character") or {}).get("value") == "SEMANTIC_TRANSFORMATION_PRESENT"]
    if enriched and all(ev.descendants(a) for a in enriched):
        out.append(_strength("HAR-STR-02", C.HARMONIZATION, "har.reusable_semantic_enrichment", ev=ev, artifacts=enriched,
                             refs=[f"ce:harmonization_contract/{a}" for a in enriched], basis=["HAR.candidate_owns_enrichment", "HAR.consumed_by_downstream_artifact"]))
    # PRO-STR-01 — producto sobre insumos resueltos
    if ev.product_nodes and not has_pro_mat:
        bad = False
        for node in ev.product_nodes:
            eff = ev.effective_providers(node)
            if any(ev.classify_source(p, policy) in (GOVERNED_SAP_CONTRACT, PHYSICAL_RAW_BOUNDARY) for p in eff.get(K_BOUNDARY, ())) or eff.get(K_LOCAL_LEAF):
                bad = True
        if not bad and all(node in ev.views for node in ev.product_nodes):
            out.append(_strength("PRO-STR-01", C.PROPAGATION, "pro.product_composes_resolved_contracts", ev=ev, artifacts=list(ev.product_nodes),
                                 refs=[f"ce:view_complexity/{n}" for n in ev.product_nodes], basis=["PRO.product_position_nodes_without_direct_boundary_leaf"]))
    # PUB-STR-01 — contrato publicado autonomo
    if ev.published_measures and not any(m.get("business_name_equals_technical") for m in ev.published_measures) and ev.business_association_targets:
        out.append(_strength("PUB-STR-01", C.PUBLISHED_CONTRACT, "pub.semantically_autonomous_contract", ev=ev, artifacts=[ev.am] if ev.am else [],
                             refs=["ce:published_contract/fields", "ce:published_contract/associations"],
                             basis=["PUB.every_measure_has_business_identity", "PUB.business_dimension_association_present"]))
    return sorted(out, key=lambda s: s["rule_id"])


# ───────────────────────────── CALIFICADORES / REVISIONES ─────────────────────────────
def qualifier_design_coherence(ev: ProfileEvidence, har_rel_02: bool, complex_graph: bool) -> dict:
    if not ev.views:
        return {"value": "NOT_DETERMINABLE", "basis": ["MNT.no_view_complexity_evidence"]}
    g = _graphical_views(ev)
    predominant = len(g) * 2 > len(ev.views)
    if har_rel_02:
        return {"value": "WEAK", "basis": ["MNT.role_ambiguity_with_reconvergence" + ("+complex_graphical_chain" if complex_graph else "")]}
    if predominant and g and max(((v.get("metrics") or {}).get("graph_depth") or 0) for v in g) >= 2:
        return {"value": "OPPORTUNITIES", "basis": ["MNT.graphical_view_on_view_chains"]}
    return {"value": "SOLID", "basis": ["MNT.no_role_ambiguity_no_graphical_chain_opportunity"]}


def review_complex_graphical_chain(ev: ProfileEvidence) -> dict | None:
    g = _graphical_views(ev)
    if not g or len(g) * 2 <= len(ev.views):
        return None
    deep = [v["artifact"] for v in g if ((v.get("metrics") or {}).get("graph_depth") or 0) >= GRAPHICAL_CHAIN_MIN_DEPTH and ((v.get("metrics") or {}).get("select_nodes") or 0) >= GRAPHICAL_CHAIN_MIN_SELECT_NODES]
    logic = [v["artifact"] for v in g if ev.view_has_logic(v["artifact"])]
    if not deep or not logic:
        return None
    return _review("COMPLEX_GRAPHICAL_CHAIN_REVIEW", "MNT-QUAL-01", "COMPLEX_GRAPHICAL_CHAIN_REVIEW", "OPEN", artifacts=sorted(set(deep) | set(logic)),
                   refs=["ce:view_complexity"], ev=ev,
                   extra={"basis": ["MNT.graphical_predominance", "MNT.deep_chained_graphical_views", "MNT.integration_or_transformation_logic_in_graphical_views"]})


def qualifier_product_focus(ev: ProfileEvidence) -> dict:
    derived = (ev.section("derivation_semantics").get("counts") or {}).get("derived_fields")
    clauses = {o.get("clause") for o in ev.expression_occurrences}
    chain = [v for n, v in ev.views.items() if n not in ev.local_leaves]            # las hojas locales alimentadas por flujos no son logica de producto
    union_or_filter = any((v.get("metrics") or {}).get("unions") for v in chain)         # los predicados triviales NO se interpretan (revision de predicados deshabilitada en v1)
    if derived is None:
        return {"value": "NOT_DETERMINABLE", "state": "SUGGESTED_NEEDS_CONTEXT", "basis": ["FOC.no_derivation_evidence"]}
    if derived == 0 and not (clauses & {"WHERE", "GROUP_BY", "HAVING"}) and not union_or_filter:
        return {"value": "GENERIC", "state": "SUGGESTED_NEEDS_CONTEXT", "basis": ["FOC.no_derivations", "FOC.no_nontrivial_predicates_or_unions"]}
    return {"value": "FOCUSED", "state": "SUGGESTED_NEEDS_CONTEXT", "basis": ["FOC.product_specific_logic_present"]}


def qualifier_naming(ev: ProfileEvidence, har_rel_02: bool) -> dict:
    """Naming = calificador/revision. NUNCA cambia el nivel ni el cierre por si solo (Gate 8D.1 A). El % de conformidad NO es un puntaje."""
    s28 = ((ev.section("naming_evidence").get("hygiene") or {}).get("artifact_name_populations") or {}).get("section_28") or {}
    applicable, bad = s28.get("applicable") or 0, s28.get("non_compliant") or 0
    if not applicable:
        return {"state": "NOT_DETERMINABLE", "disposition": None, "level_effect": "NONE", "closure_effect": "NONE"}
    ratio = bad / applicable
    if bad == 0:
        state = "NONE"
    elif ratio <= NAMING_MINOR_MAX_RATIO:
        state = "MINOR_NAMING_DEVIATION"
    elif ratio > NAMING_SYSTEMATIC_MIN_RATIO:
        state = "SYSTEMATIC_GOVERNANCE_DIVERGENCE"
    else:
        state = "PARTIAL_DIVERGENCE"
    if state in ("SYSTEMATIC_GOVERNANCE_DIVERGENCE", "PARTIAL_DIVERGENCE") and har_rel_02:
        state = "NAMING_PLUS_ROLE_AMBIGUITY"
    return {"state": state, "disposition": C.NEEDS_CONTEXT if state not in ("NONE", "NOT_DETERMINABLE") else None, "level_effect": "NONE", "closure_effect": "NONE"}


def reviews_and_limitations(ev: ProfileEvidence, policy: str) -> tuple[list[dict], list[dict]]:
    rev, lim = [], []
    # predicados fijos: NO evaluados en v1
    trivial = (ev.section("expression_occurrences").get("trivial_predicates") or ())
    if ev.product_nodes and (any(t.get("count") for t in trivial) or any(o.get("clause") == "WHERE" for o in ev.expression_occurrences)
                             or any(((v.get("metrics") or {}).get("filters") or {}).get("where_nodes") for v in ev.views.values())):
        rev.append(_review("FIXED_PREDICATE_OWNERSHIP_REVIEW", "PRO-REV-01", "FIXED_PREDICATE_OWNERSHIP_REVIEW", f"NOT_EVALUATED/{C.DISABLED_PENDING_EVIDENCE}", ev=ev,
                           refs=["ce:expression_occurrences"], extra={"possible_ownership": ["PRODUCT_SEMANTICS", "ACQUISITION_FILTER", "BUSINESS_SCOPE", "SECURITY_AUTHORIZATION", "DAC_CANDIDATE"]}))
        lim.append(_limitation("predicate_ownership_not_evaluated", refs=["ce:expression_occurrences"], ev=ev))
    # fuentes externas indeterminadas
    if ev.has_external_origins:
        und = [l for l in sorted(ev.remote_leaves) if ev.classify_source(l, policy) == EXTERNAL_CONTRACT_UNDETERMINED]
        rev.append(_review("EXTERNAL_CONTRACT_UNDETERMINED", "SRC-REV-01", "EXTERNAL_CONTRACT_UNDETERMINED", "OPEN", artifacts=und, ev=ev,
                           refs=["area:SOURCE_EXTRACTION"], extra={"count_in_closure": ev.source_state_counts.get("OTHER_ORIGIN", 0)}))
        lim.append(_limitation("external_source_contract_undetermined", artifacts=und, refs=["area:SOURCE_EXTRACTION"], ev=ev))
    # semantica temporal reutilizada que NO califica como creada aguas abajo sobre una frontera tecnica (L1(b)): revision, sin efecto en nivel
    _, unq = _downstream_created_semantics(ev)
    if unq:
        rev.append(_review("REUSED_TEMPORAL_DERIVATION_REVIEW", "LEK-REV-01", "REUSED_TEMPORAL_DERIVATION_REVIEW", "OPEN", artifacts=sorted({a for u in unq for a in u["artifacts"]}),
                           refs=["ce:expression_occurrences"], ev=ev,
                           extra={"missing_for_material": sorted({k for u in unq for k in ("A_boundary_demonstrably_technical", "B_owned_downstream_in_multiple_nodes") if not u[k]})}))
    # modo de replicacion
    if ev.surface_state("source.replication_mode") == "NOT_ASSESSED":
        rev.append(_review("REPLICATION_MODE_REVIEW", "SRC-REV-02", "REPLICATION_MODE_UNOBSERVED", "OPEN", refs=["area:SOURCE_EXTRACTION"], ev=ev))
        lim.append(_limitation("replication_mode_not_observable", refs=["area:SOURCE_EXTRACTION"], ev=ev))
    # procedencia de hojas locales no evaluada
    unprov = [l for l, r in sorted(ev.local_leaves.items()) if r.get("provenance_state") in (None, "NOT_OBSERVABLE") or r.get("assessability") == "NOT_ASSESSED"]
    if unprov:
        lim.append(_limitation("local_leaf_provenance_not_evaluated", artifacts=unprov, refs=["ce:leaf_provenance"], ev=ev))
    # mapeo de armonizacion no observable
    nomap = [a for a, c in sorted(ev.harmonization.items()) if c.get("mapping_determinable") is False]
    if nomap:
        lim.append(_limitation("harmonization_mapping_not_observable", artifacts=nomap, refs=["ce:harmonization_contract"], ev=ev))
    return rev, lim


def supporting_evidence(ev: ProfileEvidence) -> dict:
    """Evidencia DESCRIPTIVA / de apoyo. Los conteos de aqui NO son umbrales de calidad ni alimentan el nivel."""
    fields = ev.published_fields
    return {
        "published_business_association_targets": list(ev.business_association_targets),
        "published_measures_without_business_identity": sorted(m.get("technical_name") for m in ev.published_measures if m.get("business_name_equals_technical")),
        "published_fields_with_source_column_name": sum(1 for f in fields if f.get("name_equals_remote_source_column")),
        "published_fields_with_label_equal_to_technical_name": sum(1 for f in fields if f.get("business_name_equals_technical")),
        "published_fields_total": len(fields),
        "declared_dimensions_not_associated": [d if isinstance(d, str) else d.get("artifact", str(d)) for d in ev.declared_dimensions_not_associated],
        "technical_harmonization_candidates": sorted(a for a, c in ev.harmonization.items() if (c.get("character") or {}).get("value") == "TECHNICAL_MATERIALIZATION"),
        "published_non_restoration_families": {k: sorted(v) for k, v in sorted(_published_families(ev, include_projection=True).items())},
        "branch_reconvergence_pairs": len(ev.reconvergences),
        "source_state_counts": dict(sorted(ev.source_state_counts.items())),
    }


def evaluate_rules(ev: ProfileEvidence, policy: str = "UNDETERMINED") -> dict:
    pro = rule_pro_mat_01(ev, policy)
    lek = rule_lek_mat_02(ev, policy)
    src = rule_src_rel_loc(ev, policy)
    h1, h2 = rule_har_rel_01(ev), rule_har_rel_02(ev)
    loc = rule_pro_loc_01(ev, {r["reason_id"].split(":", 1)[1] for r in pro})
    reasons = [*pro, *lek, *src, *h1, *h2, *loc]
    st = strengths(ev, policy, has_pro_mat=bool(pro))
    cg = review_complex_graphical_chain(ev)
    reviews, limitations = reviews_and_limitations(ev, policy)
    if cg:
        reviews.append(cg)
    qualifiers = {
        "design_coherence": qualifier_design_coherence(ev, bool(h2), bool(cg)),
        "product_focus": qualifier_product_focus(ev),
        "naming_governance": qualifier_naming(ev, bool(h2)),
    }
    return {"reasons": reasons, "strengths": st, "qualifiers": qualifiers, "review_candidates": reviews, "evidence_limitations": limitations, "supporting_evidence": supporting_evidence(ev)}
