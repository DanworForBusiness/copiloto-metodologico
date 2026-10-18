"""Reglas del Juicio metodologico v1.1 (Gate 8C.1 / 8D.1) como PREDICADOS sobre la evidencia canonica persistida. Sin nombres de cliente/raiz, sin puntajes, sin pesos.

Familias:  SRC- (Fuente/Ingesta) · HAR- (Armonizacion) · PRO-/LEK- (Propagacion) · PUB- (Publicado) · GOV- (naming) · MNT- (mantenibilidad) · FOC- (foco).

MATERIALIDAD = causalidad arquitectonica: (1) existe una responsabilidad esperada, (2) no esta resuelta en el contrato esperado, (3) esta compensada aguas abajo / por otra capa /
por el consumidor. NO es un conteo. Nada de lo siguiente es MATERIAL por si solo: tabla fisica cruda, vista grafica o SQL, proyeccion 1:1, ausencia/presencia de asociaciones,
reconvergencia, heterogeneidad de tipos, divergencia de naming, cantidad de campos, ausencia de hallazgos, tecnologia de la fuente.

Los unicos CORTES numericos del modulo son de CALIFICADORES (``design_coherence``, ``COMPLEX_GRAPHICAL_CHAIN_REVIEW``, estado de naming) y no tienen efecto sobre el nivel.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from tools.catalog import leaf_materialization as LM
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
            ev: ProfileEvidence, extra: Mapping[str, Any] | None = None) -> dict:
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
    if extra:
        out.update(extra)
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


# ───────────────────────────── exposicion del consumidor por LINAJE DE CAMPO publicado (canonical-evidence/v3) ─────────────────────────────
# Todo lo de aqui es CAUSAL: parte de caminos trazables campo publicado -> hoja frontera, no de senales por existencia. No hay conteos, proporciones ni umbrales: una frontera
# no resuelta "se expone" al consumidor cuando existe al menos un camino trazable que lleva su vocabulario (o la semantica creada aguas abajo a partir de ella) hasta un campo publicado.
NEAR_IDENTITY_CHARACTERS = frozenset({"IDENTITY_ONLY", "IDENTITY_OR_RENAME"})
VERBATIM_LABEL_RELATIONS = frozenset({"EQUALS_LEAF_LABEL", "EQUALS_LEAF_FIELD_NAME", "NO_LABEL"})
DERIVED_CHARACTERS = frozenset({"WITH_CAST", "WITH_DERIVATION"})


def _verbatim_vocabulary(entry: Mapping[str, Any]) -> bool:
    """El campo publicado conserva el nombre del campo de la hoja Y su etiqueta es la de la hoja (o su nombre de campo, o no hay etiqueta): el vocabulario llega SIN CAMBIOS."""
    v = entry.get("vocabulary") or {}
    return bool(v.get("name_equals_leaf_field")) and v.get("label_relation") in VERBATIM_LABEL_RELATIONS


def _paths(entry: Mapping[str, Any], *, characters: frozenset, through: bool) -> int:
    return sum(q["paths"] for q in entry.get("path_profiles") or () if q["character"] in characters and q["through_harmonization"] is through)


def vocabulary_exposure(ev: ProfileEvidence, leaves: Iterable[str]) -> dict[str, list[str]]:
    """hoja -> campos publicados que heredan SU vocabulario sin cambios por un camino casi-identidad que NO cruza un contrato de armonizacion (tipo A: el vocabulario tecnico de una
    frontera no resuelta llega al consumidor)."""
    wanted = set(leaves)
    out: dict[str, list[str]] = {}
    for name, r in ev.lineage_fields.items():
        for lf in r.get("frontier_leaves") or ():
            if lf["leaf"] in wanted and _verbatim_vocabulary(lf) and _paths(lf, characters=NEAR_IDENTITY_CHARACTERS, through=False):
                out.setdefault(lf["leaf"], []).append(name)
    return {k: sorted(set(v)) for k, v in sorted(out.items())}


def downstream_semantics_exposure(ev: ProfileEvidence, artifacts: Iterable[str]) -> list[str]:
    """Campos publicados cuyo linaje ATRAVIESA alguno de ``artifacts`` (los sitios donde se creo semantica reutilizable aguas abajo) por un camino con derivacion/cast que NO cruza un
    contrato de armonizacion: el consumidor expone semantica creada aguas abajo, no resuelta en un contrato reutilizable."""
    wanted = set(artifacts)
    out = []
    for name, r in ev.lineage_fields.items():
        if wanted & set(r.get("artifacts_traversed") or ()) and any(_paths(lf, characters=DERIVED_CHARACTERS, through=False) for lf in r.get("frontier_leaves") or ()):
            out.append(name)
    return sorted(out)


def harmonization_passthrough_exposure(ev: ProfileEvidence) -> list[str]:
    """Campos publicados cuyo vocabulario atraviesa un contrato de armonizacion casi-identidad y llega SIN CAMBIOS desde la hoja: la frontera de armonizacion existe pero es una
    proyeccion tecnica (no resolvio el vocabulario) y el consumidor lo hereda."""
    out = []
    for name, r in ev.lineage_fields.items():
        if any(_verbatim_vocabulary(lf) and _paths(lf, characters=NEAR_IDENTITY_CHARACTERS, through=True) for lf in r.get("frontier_leaves") or ()):
            out.append(name)
    return sorted(out)


def decision_critical_undetermined_source(ev: ProfileEvidence, policy: str, exclude: frozenset = frozenset()) -> dict[str, list[str]]:
    """Hojas frontera ``EXTERNAL_CONTRACT_UNDETERMINED`` cuyo vocabulario llega a un campo publicado VERBATIM por un camino casi-identidad
    que NO cruza un contrato de armonizacion (``vocabulary_exposure``). La clase de esa fuente es DECISION-CRITICA para el contrato publicado:
    si fuese cruda/tecnica, es vocabulario no restaurado que llega al consumidor (debilidad del contrato publicado); si fuese gobernada, es
    reuso legitimo. La tecnologia NO la resuelve, y mientras la clase siga indeterminada el motor no puede afirmar la calidad del contrato
    publicado para esos campos -> ``NOT_DETERMINABLE``. NARROW: exige la exposicion publicada verbatim (una hoja indeterminada SIN consecuencia
    publicada no alcanza; por eso Prefacturacion, cuyo vocabulario externo se resuelve antes de publicar, no dispara). hoja -> campos publicados.
    ``exclude`` (SOLO variante B5 GOVERNED_CONTRACT_RATIFIED): hojas cuya indeterminacion contractual resolvio una ratificacion vigente; la clase de la hoja NO cambia para ninguna otra regla."""
    und = [l for l in ev.remote_leaves if ev.classify_source(l, policy) == EXTERNAL_CONTRACT_UNDETERMINED and l not in exclude]
    return vocabulary_exposure(ev, und)


def published_positive_families(ev: ProfileEvidence, policy: str) -> tuple[dict[str, set[str]], bool, bool]:
    """(familias AFIRMATIVAS, linaje_completo, hay_fuga) de la fortaleza del contrato publicado. Familias (ninguna es obligatoria en todo producto, ninguna es un puntaje):
    GOVERNED_HARMONIZATION_REUSE (campos publicados trazables a una hoja gobernada a traves de un contrato de armonizacion) · AUTHORED_SEMANTICS (campos cuya etiqueta es PROPIA:
    distinta de la de TODAS las hojas de las que provienen) · BUSINESS_ASSOCIATION (evidencia positiva LOCALIZADA: existe una asociacion a una dimension de negocio).
    ``hay_fuga``: algun campo hereda sin cambios, por un camino casi-identidad sin armonizacion, el vocabulario de una hoja NO gobernada (cruda / externa indeterminada / desconocida)."""
    fam: dict[str, set[str]] = {}
    leak = False
    for name, r in ev.lineage_fields.items():
        entries = r.get("frontier_leaves") or ()
        if any(ev.classify_source(lf["leaf"], policy) == GOVERNED_SAP_CONTRACT and _paths(lf, characters=frozenset({"IDENTITY_ONLY", "IDENTITY_OR_RENAME", "WITH_CAST", "WITH_DERIVATION"}), through=True) for lf in entries):
            fam.setdefault("GOVERNED_HARMONIZATION_REUSE", set()).add(f"field:{name}")
        if entries and all((lf.get("vocabulary") or {}).get("label_relation") == "DIFFERENT_FROM_LEAF" for lf in entries):
            fam.setdefault("AUTHORED_SEMANTICS", set()).add(f"field:{name}")
        for lf in entries:
            cls = ev.classify_source(lf["leaf"], policy)
            if cls is not None and cls != GOVERNED_SAP_CONTRACT and _verbatim_vocabulary(lf) and _paths(lf, characters=NEAR_IDENTITY_CHARACTERS, through=False):
                leak = True
    if ev.business_association_targets:
        fam["BUSINESS_ASSOCIATION"] = {f"dim:{d}" for d in ev.business_association_targets}
    complete = bool(ev.lineage_fields) and not ev.lineage_incomplete_fields
    return fam, complete, leak


def _independent_pair_exists(fam: Mapping[str, set[str]]) -> bool:
    """Dos familias afirmativas son INDEPENDIENTES si no son proyecciones del MISMO conjunto de hechos subyacentes (testigos identicos = un solo hecho contado dos veces). Hechos distintos
    sobre el mismo campo (p. ej. 'reutiliza un contrato gobernado' y 'tiene etiqueta propia') son afirmaciones distintas; el numero de familias no es un puntaje."""
    names = sorted(fam)
    return any(fam[a] != fam[b] for i, a in enumerate(names) for b in names[i + 1:])


def published_contract_conformance(ev: ProfileEvidence, policy: str) -> dict:
    """Condiciones del reconocimiento de la fortaleza del contrato publicado (PUB-STR-01), UNA sola fuente para la fortaleza y para la explicación de LVL-06.
    Vía 1 (original): ≥2 familias afirmativas independientes. Vía 2 (conformidad con sustancia, sin asociaciones obligatorias): ≥1 familia afirmativa A NIVEL DE CAMPO (reuso gobernado trazable o semántica propia; una asociación sola no alcanza) + evidencia de SUSTANCIA del contrato
    publicado (≥1 medida publicada, identidad de negocio en TODAS las medidas, ningún campo publicado con el nombre de la columna fuente). Ambas exigen linaje completo y ausencia de fuga.
    ``declared_dimensions_not_associated`` es solo INFORMATIVA: no bloquea ninguna vía (es una observación; ver ``supporting_evidence``). Sin umbrales, nombres ni reglas por cliente."""
    fam, lineage_complete, leak = published_positive_families(ev, policy)
    measures = ev.published_measures
    conds = {
        "lineage_complete": lineage_complete,
        "no_vocabulary_leak": not leak,
        "field_level_affirmative_family": any(f != "BUSINESS_ASSOCIATION" for f in fam),   # una asociación SOLA nunca completa la fortaleza (decisión de owner 8D.1d)
        "measures_published": bool(measures),
        "measures_business_identity": not any(m.get("business_name_equals_technical") for m in measures),
        "no_source_column_names": not any(f.get("name_equals_remote_source_column") for f in ev.published_fields),
    }
    base = lineage_complete and not leak
    via_pair = base and _independent_pair_exists(fam)
    substance = conds["measures_published"] and conds["measures_business_identity"] and conds["no_source_column_names"]
    via_substance = base and conds["field_level_affirmative_family"] and substance
    return {"families": fam, "conditions": conds, "via": "INDEPENDENT_FAMILIES" if via_pair else ("SUBSTANCE_CONFORMANCE" if via_substance else None)}


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
    """LEK-MAT-02 — fuga de responsabilidad DISTRIBUIDA con consecuencia TRAZABLE en el contrato publicado.
    L1 (se exige (a) o (b); (c) solo APOYA): (a) tabla fisica cruda en la espina que OMITE la frontera reutilizable y es compensada por logica de Modelado; (b) semantica reutilizable creada
    aguas abajo sobre una frontera de armonizacion tecnica (ver ``_downstream_created_semantics``); (c) hoja de frontera clasificada consumida directamente en el nodo de producto (apoyo).
    L2: esa responsabilidad es absorbida por nodos que NO son candidatos de armonizacion y ejecutan logica.
    L3 (CAUSAL, por linaje de campo publicado): el contrato publicado sigue exponiendo las consecuencias del contrato no resuelto por CAMINOS TRAZABLES: para (a), algun campo publicado
    hereda sin cambios (nombre y etiqueta) el vocabulario de esa hoja por un camino casi-identidad que no cruza un contrato de armonizacion; para (b), algun campo publicado atraviesa un
    sitio de la semantica creada aguas abajo por un camino con derivacion sin armonizacion. Evidencia por elemento, sin conteos ni proporciones; sin linaje (v2) no hay MATERIAL."""
    elements: dict[str, list[str]] = {}
    l2_nodes: set[str] = set()
    refs: list[str] = []
    exposed: dict[str, list[str]] = {}
    raw = _raw_bypass_compensated(ev, policy)
    vexp = vocabulary_exposure(ev, raw)
    for leaf, logic in raw.items():
        if leaf not in vexp:
            continue                                                                   # la frontera cruda esta compensada pero NO llega al consumidor con su vocabulario: sin consecuencia trazable
        elements.setdefault("a_physical_raw_bypassing_harmonization_compensated_downstream", []).append(leaf)
        exposed[leaf] = vexp[leaf]
        l2_nodes.update(logic)
        refs += [_fact_ref(ev, "source.downstream_reach", leaf), _fact_ref(ev, "source.contract", leaf), f"ce:source_contract_classes/{leaf}"]
    qual, _ = _downstream_created_semantics(ev)
    for q in qual:
        sites = [a for a in q["artifacts"]]
        dexp = downstream_semantics_exposure(ev, sites)
        if not dexp:
            continue
        elements.setdefault("b_reusable_semantics_created_downstream_over_technical_boundary", []).extend([*q["artifacts"], *q["technical_upstream_candidates"]])
        exposed[f"sites:{'+'.join(sites)}"] = dexp
        l2_nodes.update(q["artifacts"])
        refs += ["ce:expression_occurrences", *[f"ce:harmonization_contract/{c}" for c in q["technical_upstream_candidates"]]]
    for node in ev.product_nodes:
        eff = ev.effective_providers(node)
        leaves = [p for p in eff.get(K_BOUNDARY, ()) if ev.classify_source(p, policy) in (GOVERNED_SAP_CONTRACT, PHYSICAL_RAW_BOUNDARY)]
        if leaves and ev.view_has_logic(node):
            elements.setdefault("c_boundary_leaf_at_product_node", []).extend([node, *leaves])
            l2_nodes.add(node)
            refs += [f"ce:view_complexity/{node}", *[_fact_ref(ev, "source.contract", p) for p in leaves]]
    has_a = "a_physical_raw_bypassing_harmonization_compensated_downstream" in elements
    has_b = "b_reusable_semantics_created_downstream_over_technical_boundary" in elements
    if not (has_a or has_b):
        return []                                                                      # (c) por si solo NO basta; sin consecuencia trazable en el publicado tampoco
    witness = sorted({f for v in exposed.values() for f in v})
    refs += ["ce:published_field_lineage", *[f"ce:published_field_lineage/{f}" for f in witness]]
    arts = [a for v in elements.values() for a in v] + sorted(l2_nodes)
    kinds = []
    if has_a:
        kinds.append("LEK.L3.unresolved_boundary_vocabulary_reaches_the_consumer_verbatim_through_traceable_paths")
    if has_b:
        kinds.append("LEK.L3.downstream_created_semantics_reaches_the_consumer_through_traceable_paths")
    base_basis = [*[f"LEK.L1.{k}" for k in sorted(elements)], "LEK.L2.absorbed_by_non_candidate_nodes_with_logic", *kinds]
    exposed_txt = "traceable published paths expose the unresolved contract: " + "; ".join(
        f"{k} -> {', '.join(v[:6])}{' ...' if len(v) > 6 else ''}" for k, v in sorted(exposed.items()))
    if has_b:
        # L1(b): la MISMA responsabilidad semantica reutilizable se crea/posee en >=2 nodos aguas abajo sobre una frontera TECNICA. Esa
        # reutilizacion estructural ES la prueba generica de centralidad arquitectonica (no un conteo de campos ni un proxy de medida/clave);
        # el testigo publicado solo prueba que la consecuencia llega al consumidor. Por eso (b) sostiene MATERIAL con >=1 testigo publicado.
        return [_reason(
            "LEK-MAT-02", C.MATERIAL_DEVIATION, C.PROPAGATION, "lek.unrestored_responsibility_leakage", "closure", ev=ev, secondary=C.PUBLISHED_CONTRACT,
            artifacts=arts, refs=refs, basis=base_basis,
            causal={"expected_responsibility": "reusable source/semantic responsibility resolved at its expected boundary",
                    "unresolved": ", ".join(sorted(elements)), "compensation": "absorbed by downstream non-harmonization nodes",
                    "published_not_restored": exposed_txt})]
    # Solo (a): una fuga cruda/bypass trazable hasta el publicado, SIN evidencia estructural de centralidad de contrato (no hay (b): la misma
    # responsabilidad no se crea/posee en multiples nodos aguas abajo), NO es MATERIAL por si sola. Es una debilidad RELEVANTE del contrato
    # publicado: vocabulario tecnico no restaurado que llega al consumidor. Sin conteos, proporciones, mayoria ni proxies. Si otra regla MATERIAL
    # ya ratificada (p. ej. PRO-MAT-01) aplica de forma independiente, esa determina PARCIAL; LEK no la duplica.
    return [_reason(
        "LEK-REL-01", C.RELEVANT_WEAKNESS, C.PUBLISHED_CONTRACT, "lek.unrestored_raw_vocabulary_reaches_published_without_contract_centrality", "closure", ev=ev,
        secondary=C.SOURCE_INGESTION, artifacts=arts, refs=refs,
        basis=[*base_basis, "LEK.no_structural_contract_centrality_reusable_responsibility_not_owned_in_multiple_downstream_nodes"],
        causal={"expected_responsibility": "raw/bypass vocabulary resolved at a reusable governed boundary",
                "unresolved": ", ".join(sorted(elements)), "compensation": "absorbed by downstream non-harmonization nodes",
                "published_not_restored": exposed_txt})]


# ───────────────────────────── RELEVANT / LOCAL ─────────────────────────────
_SCC_MIXED, _SCC_PHYSICAL = "MIXED_BOUNDARY_CLASSES", "PHYSICAL_ABAP_TABLE_BOUNDARY"


def _verified_provenance(ev: ProfileEvidence, leaf: str, rec: Mapping[str, Any], m: Mapping[str, Any] | None) -> bool:
    """Procedencia comprobada comun: ESTABLISHED, cadena completa y sin huecos, sin evidencia contradictoria y al menos un productor EXACT o resuelto por CSN."""
    return bool(m is not None and rec.get("provenance_state") == "ESTABLISHED" and m["chain_complete"] is True and not m["chain_gaps"] and LM.U_CONTRADICTORY not in m["unknown_reasons"]
                and any(p["target_match"] == LM.EXACT or p["resolved_via"] for p in m["producers"]))


def local_mixed_physical_components_materialized(ev: ProfileEvidence, policy: str) -> list[dict]:
    """T5 integrada — FRONTERAS MIXED con COMPONENTE FISICO COMPROBADO. Una tabla local cuyos bordes terminales son gobernados Y fisicos (``MIXED``) no es una deficiencia por si misma: solo se
    reconoce el componente fisico (tablas ABAP fisicas alcanzadas), y unicamente con procedencia comprobada (cadena completa, sin huecos ni bordes sin clasificar —garantizado por la clase derivada—,
    sin contradiccion) y con la clase explicita de ``source_contract_classes`` CORROBORANDO la mezcla y el componente fisico. Cada componente informa si participa de la ESPINA del producto
    (la hoja alimenta un nodo de producto por linaje) o es una RAMA AUXILIAR. Devuelve ``[{leaf, physical_entities, governed_entities, on_spine}]``."""
    out = []
    for leaf, rec in sorted(ev.local_leaves.items()):
        m = ev.materialization_of(leaf)
        scc = ev.leaf_class_records.get(leaf) or {}
        if (not _verified_provenance(ev, leaf, rec, m) or m["derived_boundary_class"] != LM.DBC_MIXED or scc.get("contract_class") != _SCC_MIXED
                or _SCC_PHYSICAL not in (scc.get("boundary_classes") or ())):
            continue
        tb = m["terminal_boundaries"]
        out.append({"leaf": leaf, "physical_entities": sorted({b["remote_entity"] for b in tb if b["boundary_class"] in LM.PHYSICAL_BOUNDARY_CLASSES and b["remote_entity"]}),
                    "governed_entities": sorted({b["remote_entity"] for b in tb if b["boundary_class"] in LM.GOVERNED_BOUNDARY_CLASSES and b["remote_entity"]}),
                    "on_spine": _leaf_reaches_product(ev, leaf)})
    return [c for c in out if c["physical_entities"]]


def local_physical_boundaries_materialized(ev: ProfileEvidence, policy: str) -> list[str]:
    """T5 integrada con ``leaf-materialization/v1`` — tablas LOCALES con procedencia FISICA COMPROBADA: clase de borde derivada ``PHYSICAL_DERIVED`` (todos los bordes terminales son tablas ABAP
    fisicas, cadena completa y sin huecos), al menos un productor EXACT (Data Flow o Replication Flow) con procedencia ESTABLISHED y sin evidencia contradictoria. La CLASE DE FUENTE sigue viniendo
    de ``source_contract_classes`` (identidad/gobierno): la derivacion solo la CORROBORA (si el registro explicito discrepa, la hoja no cuenta). Es INDEPENDIENTE de ``materialization_kind`` (una
    copia, una replica o una transformacion de tablas fisicas son igualmente contacto con la fuente fisica) y de ``filter_nature``; ninguna tabla local entra solo por serlo ni por su nombre."""
    out = []
    for leaf, rec in sorted(ev.local_leaves.items()):
        m = ev.materialization_of(leaf)
        if (m is None or rec.get("provenance_state") != "ESTABLISHED" or m["derived_boundary_class"] != LM.DBC_PHYSICAL or m["chain_complete"] is not True
                or LM.U_CONTRADICTORY in m["unknown_reasons"] or ev.classify_source(leaf, policy) != PHYSICAL_RAW_BOUNDARY):
            continue
        if any(p["target_match"] == LM.EXACT or p["resolved_via"] for p in m["producers"]):
            out.append(leaf)
    out += [c["leaf"] for c in local_mixed_physical_components_materialized(ev, policy) if c["on_spine"]]        # MIXED: solo el componente fisico comprobado Y en la espina del producto
    return sorted(out)


def local_physical_boundaries(ev: ProfileEvidence, policy: str, materialized: bool = False) -> list[str]:
    """T5 — tablas LOCALES cuya clase de contrato es tabla ABAP fisica (``source_contract_classes``) Y cuya procedencia fue COMPROBADA por un Data Flow (``leaf_provenance``: ESTABLISHED/DATA_FLOW):
    el Data Flow materializa en Datasphere el contenido de una tabla fisica. Ni cualquier tabla local es frontera cruda, ni se deduce por nombre: se exige la clase explicita Y la procedencia comprobada.
    Una tabla replicada de un contrato gobernado (Replication Flow), una transformada con clase mixta/indeterminada o una sin procedencia NO entran."""
    if materialized:
        return local_physical_boundaries_materialized(ev, policy)
    out = []
    for leaf, rec in sorted(ev.local_leaves.items()):
        if rec.get("provenance_state") == "ESTABLISHED" and rec.get("basis") == "DATA_FLOW" and ev.classify_source(leaf, policy) == PHYSICAL_RAW_BOUNDARY:
            out.append(leaf)
    return out


def rule_src_rel_loc(ev: ProfileEvidence, policy: str, variants: frozenset = frozenset()) -> list[dict]:
    """SRC-REL-01 (RELEVANT): tabla fisica cruda en la espina cuyo camino al producto OMITE la frontera reutilizable (ausente u omitida) Y la responsabilidad es compensada por logica de
    Modelado. Cualquier otra cruda (fuera de la espina, a traves de un candidato, o sin compensacion) es SRC-LOC-01 (LOCAL, evidencia de fuente). Nunca MATERIAL sola; no es 'cruda en espina'."""
    comp = _raw_bypass_compensated(ev, policy)
    rel = sorted(comp)
    loc = sorted(l for l in ev.remote_leaves if ev.classify_source(l, policy) == PHYSICAL_RAW_BOUNDARY and l not in comp)
    materialized = C.VARIANT_MATERIALIZED_INPUT in variants
    local_phys = [l for l in local_physical_boundaries(ev, policy, materialized) if l not in loc] if C.VARIANT_T5_LOCAL_PHYSICAL in variants else []
    mixed = {c["leaf"]: c for c in local_mixed_physical_components_materialized(ev, policy) if c["on_spine"] and c["leaf"] in local_phys} if materialized and local_phys else {}
    out = []
    if rel:
        out.append(_reason("SRC-REL-01", C.RELEVANT_WEAKNESS, C.SOURCE_INGESTION, "src.physical_boundary_bypassed_and_compensated_downstream", "closure", ev=ev,
                           artifacts=[*rel, *sorted({n for v in comp.values() for n in v})], refs=[_fact_ref(ev, "source.downstream_reach", l) for l in rel],
                           basis=["SRC.physical_abap_table", "SRC.on_strict_spine", "SRC.path_to_product_bypasses_harmonization", "SRC.compensated_by_modeling_logic"]))
    if loc or local_phys:
        out.append(_reason("SRC-LOC-01", C.LOCAL_WEAKNESS, C.SOURCE_INGESTION, "src.physical_reach_without_bypass_and_compensation", "closure", ev=ev, artifacts=[*loc, *local_phys],
                           refs=[*[_fact_ref(ev, "source.downstream_reach", l) for l in loc], *[f"ce:leaf_provenance/{l}" for l in local_phys], *[f"ce:source_contract_classes/{l}" for l in local_phys],
                                 *([f"ce:physical_source_boundaries/{l}" for l in local_phys] if materialized else [])],
                           basis=["SRC.physical_abap_table", "SRC.off_spine_or_harmonized_or_not_compensated",
                                  *((["SRC.local_table_physical_provenance_verified_by_leaf_materialization"] if materialized else ["SRC.local_table_materialized_from_physical_abap_table_by_established_data_flow"]) if local_phys else []),
                                  *(["SRC.mixed_boundary_physical_component_verified_on_product_spine"] if mixed else [])],
                           extra=({"mixed_boundary_physical_components": [{k: c[k] for k in ("leaf", "physical_entities", "governed_entities", "on_spine")} for _, c in sorted(mixed.items())]} if mixed else None)))
    return out


def _aligns_branches(ev: ProfileEvidence, node: str) -> bool:
    """El nodo de producto completa la alineacion/normalizacion de ramas (union + derivaciones) sobre vistas/contratos, sin hojas de frontera: trabajo que la armonizacion no resolvio."""
    m = ((ev.views.get(node) or {}).get("metrics")) or {}
    return bool(m.get("unions") and m.get("derived_columns")) and not (set(ev.effective_providers(node)) - {K_VIEW, K_HARM})


def rule_har_rel_01(ev: ProfileEvidence) -> list[dict]:
    """HAR-REL-01 — Armonizacion tecnica/no resuelta (todos los candidatos observables son materializacion tecnica que conserva el vocabulario de la fuente) MAS una CONSECUENCIA semantica
    observable aguas abajo: (i) el vocabulario atraviesa esa frontera tecnica y llega SIN CAMBIOS a un campo publicado (linaje de campo: casi-identidad, nombre y etiqueta de la hoja),
    (ii) semantica reutilizable creada aguas abajo sobre esa frontera tecnica o (iii) Propagacion completa la alineacion de ramas. Una armonizacion tecnica SOLA es evidencia descriptiva
    (``supporting_evidence.technical_harmonization_candidates``), no debilidad."""
    cands = [c for c in ev.harmonization.values() if (c.get("character") or {}).get("value") != "UNKNOWN"]
    if not cands:
        return []
    if any((c.get("character") or {}).get("value") != "TECHNICAL_MATERIALIZATION" for c in cands):
        return []
    carry = [c["artifact"] for c in cands if ((c.get("source_vocabulary") or {}).get("fields_with_source_column_name") or 0) > 0]
    if len(carry) != len(cands):
        return []
    consequence, refs = [], []
    passthrough = harmonization_passthrough_exposure(ev)
    if passthrough:
        consequence.append("HAR.consequence.technical_vocabulary_passes_through_the_harmonization_boundary_to_the_consumer")
        refs += ["ce:published_field_lineage", *[f"ce:published_field_lineage/{f}" for f in passthrough]]
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
_HAR_NON_BUSINESS_LABELS = ("NO_LABEL", "UNKNOWN_PROVIDER_LABEL", "EQUALS_OWN_NAME", "EQUALS_PROVIDER_TECHNICAL_NAME", "DIFFERENT_FROM_PROVIDER")


def _har_sources_governed(ev: ProfileEvidence, cand: Mapping[str, Any], policy: str, seen: frozenset = frozenset()) -> bool:
    """T4 — TODAS las fuentes del candidato resuelven a hojas gobernadas, directamente o A TRAVES de otros candidatos de armonizacion tecnica 1:1 (una dimension reutilizable construida sobre otra
    dimension reutilizable). Un origen que no es hoja ni candidato (p. ej. una vista cualquiera) o una hoja no gobernada NO califica."""
    srcs = cand.get("sources") or []
    if not srcs:
        return False
    for s in srcs:
        sub = ev.harmonization.get(s)
        if sub is not None:
            if s in seen or (sub.get("character") or {}).get("value") != "TECHNICAL_MATERIALIZATION" or not _har_sources_governed(ev, sub, policy, seen | {cand.get("artifact")}):
                return False
        elif ev.classify_source(s, policy) != GOVERNED_SAP_CONTRACT:
            return False
    return True


def _har_business_labels(cand: Mapping[str, Any]) -> bool:
    """T4 — el vocabulario de negocio se preserva por ETIQUETA: todo campo conserva la etiqueta del proveedor gobernado (ninguno sin etiqueta, con la etiqueta = su propio nombre tecnico o distinta
    del proveedor). Conservar el NOMBRE tecnico de columna en una proyeccion 1:1 no es un defecto: es la responsabilidad de una frontera reutilizable que no renombra."""
    lab = cand.get("label_origin_counts") or {}
    return not any(lab.get(k) for k in _HAR_NON_BUSINESS_LABELS) and (lab.get("EQUALS_PROVIDER_LABEL") or 0) > 0


def har_str_01_reformulated(ev: ProfileEvidence, policy: str) -> list[str]:
    """T4 — candidatos que califican como FRONTERA REUTILIZABLE LEGITIMA sobre un contrato gobernado (lista vacia = no hay fortaleza). La fortaleza exige EVIDENCIA AFIRMATIVA (>=1 candidato
    1:1 sobre contrato gobernado, con etiquetas de negocio preservadas y consumido aguas abajo) Y la AUSENCIA de las tres consecuencias que definen HAR-REL-01 (vocabulario tecnico que
    atraviesa al consumidor, semantica reutilizable creada aguas abajo, alineacion de ramas en Propagacion). Candidatos de caracter UNKNOWN/MIXED no bloquean (no son deficiencia demostrada)
    ni cuentan como fortaleza; la ausencia de defectos por si sola nunca la otorga."""
    cands = [c for c in ev.harmonization.values() if (c.get("character") or {}).get("value") == "TECHNICAL_MATERIALIZATION"]
    qualified = [c["artifact"] for c in cands if _har_sources_governed(ev, c, policy) and _har_business_labels(c) and ev.descendants(c["artifact"])]
    if not qualified:
        return []
    if harmonization_passthrough_exposure(ev) or _downstream_created_semantics(ev)[0] or any(_aligns_branches(ev, n) for n in ev.product_nodes):
        return []
    return sorted(qualified)


def strengths(ev: ProfileEvidence, policy: str, has_pro_mat: bool, variants: frozenset = frozenset()) -> list[dict]:
    out: list[dict] = []
    # SRC-STR-01 — adquisicion gobernada: TODO contacto con la fuente (hoja frontera, salvo plataforma) en clase gobernada, segun la clase explicita por hoja (v3); al menos uno
    contacts = ev.source_contact_leaves
    if contacts and all(ev.classify_source(l, policy) == GOVERNED_SAP_CONTRACT for l in contacts):
        out.append(_strength("SRC-STR-01", C.SOURCE_INGESTION, "src.governed_acquisition_explicit", ev=ev, artifacts=list(contacts),
                             refs=["ce:source_contract_classes", *[f"ce:source_contract_classes/{l}" for l in contacts]],
                             basis=["SRC.every_source_contact_in_governed_class", "SRC.class_established_per_leaf_by_explicit_evidence"]))
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
    har_str_01 = False
    if cands and all((c.get("character") or {}).get("value") == "TECHNICAL_MATERIALIZATION" for c in cands):
        legit = []
        for c in cands:
            srcs = c.get("sources") or []
            gov = bool(srcs) and all(ev.classify_source(s, policy) == GOVERNED_SAP_CONTRACT for s in srcs)                 # clase explicita por hoja (v3); un origen que no es hoja no es gobernado
            lab = c.get("label_origin_counts") or {}
            no_carry = ((c.get("source_vocabulary") or {}).get("fields_with_source_column_name") or 0) == 0
            legit.append(gov and no_carry and not lab.get("DIFFERENT_FROM_PROVIDER") and not lab.get("UNKNOWN_PROVIDER_LABEL") and not lab.get("NO_LABEL"))
        if all(legit):
            har_str_01 = True
            out.append(_strength("HAR-STR-01", C.HARMONIZATION, "har.legitimate_reusable_boundary_over_governed_contract", ev=ev, artifacts=[c["artifact"] for c in cands],
                                 refs=[f"ce:harmonization_contract/{c['artifact']}" for c in cands],
                                 basis=["HAR.one_to_one_over_governed_contract", "HAR.business_vocabulary_preserved", "HAR.no_downstream_compensation"]))
    if C.VARIANT_T4_HAR in variants and not har_str_01:                       # camino ADICIONAL (la condicion vigente queda intacta): nunca quita una fortaleza ya reconocida
        qual = har_str_01_reformulated(ev, policy)
        if qual:
            out.append(_strength("HAR-STR-01", C.HARMONIZATION, "har.legitimate_reusable_boundary_over_governed_contract", ev=ev, artifacts=qual, refs=[f"ce:harmonization_contract/{a}" for a in qual],
                                 basis=["HAR.one_to_one_over_governed_contract", "HAR.business_vocabulary_preserved_by_provider_labels", "HAR.consumed_by_downstream_artifact", "HAR.no_downstream_compensation"]))
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
    # PUB-STR-01 — contrato publicado intencional/autonomo: evidencia AFIRMATIVA convergente (>=2 familias INDEPENDIENTES) con linaje COMPLETO y SIN fuga de vocabulario no gobernado.
    # La existencia de asociaciones es evidencia positiva LOCALIZADA: ni su numero ni una sola completan la fortaleza; ninguna familia es obligatoria en todo producto.
    pub = published_contract_conformance(ev, policy)
    fam = pub["families"]
    if pub["via"] == "INDEPENDENT_FAMILIES":                                   # vía original, intacta
        out.append(_strength("PUB-STR-01", C.PUBLISHED_CONTRACT, "pub.semantically_intentional_consumer_contract", ev=ev, artifacts=[ev.am] if ev.am else [],
                             refs=["ce:published_field_lineage", "ce:published_contract/fields", "ce:published_contract/associations"],
                             basis=["PUB.published_lineage_complete_for_every_field", "PUB.no_unresolved_vocabulary_reaches_the_consumer_verbatim",
                                    *[f"PUB.family.{f}" for f in sorted(fam)], "PUB.at_least_two_independent_affirmative_families"]))
    elif pub["via"] == "SUBSTANCE_CONFORMANCE":                                # vía 2: conformidad con sustancia; las asociaciones son fortaleza adicional, no requisito
        out.append(_strength("PUB-STR-01", C.PUBLISHED_CONTRACT, "pub.semantically_intentional_consumer_contract", ev=ev, artifacts=[ev.am] if ev.am else [],
                             refs=["ce:published_field_lineage", "ce:published_contract/fields", "ce:published_contract/associations"],
                             basis=["PUB.published_lineage_complete_for_every_field", "PUB.no_unresolved_vocabulary_reaches_the_consumer_verbatim",
                                    *[f"PUB.family.{f}" for f in sorted(fam)], "PUB.conformance_with_substance_without_required_associations",
                                    "PUB.published_measures_with_business_identity", "PUB.no_source_column_names_in_the_consumption_vocabulary"]))
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


def reviews_and_limitations(ev: ProfileEvidence, policy: str, ratified: frozenset = frozenset()) -> tuple[list[dict], list[dict]]:
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
        # DECISION-CRITICA sii alguna de esas hojas indeterminadas expone su vocabulario verbatim al contrato publicado (ver
        # decision_critical_undetermined_source): entonces la limitacion SI puede cambiar el nivel (la clase no resuelta decide leak vs reuso).
        crit = bool(decision_critical_undetermined_source(ev, policy, ratified))
        lim.append(_limitation("external_source_contract_undetermined", artifacts=und, refs=["area:SOURCE_EXTRACTION"], can_change_level=crit, ev=ev))
    # semantica temporal reutilizada que NO califica como creada aguas abajo sobre una frontera tecnica (L1(b)): revision, sin efecto en nivel
    _, unq = _downstream_created_semantics(ev)
    if unq:
        rev.append(_review("REUSED_TEMPORAL_DERIVATION_REVIEW", "LEK-REV-01", "REUSED_TEMPORAL_DERIVATION_REVIEW", "OPEN", artifacts=sorted({a for u in unq for a in u["artifacts"]}),
                           refs=["ce:expression_occurrences"], ev=ev,
                           extra={"missing_for_material": sorted({k for u in unq for k in ("A_boundary_demonstrably_technical", "B_owned_downstream_in_multiple_nodes") if not u[k]})}))
    # linaje de campo publicado no establecido para todos los campos: limita lo que se puede afirmar del consumidor (evaluabilidad, no calidad)
    if ev.lineage_incomplete_fields:
        lim.append(_limitation("published_field_lineage_incomplete", refs=["ce:published_field_lineage"], ev=ev))
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
        "published_field_lineage": {"completeness": dict((ev.lineage or {}).get("counts") or {}), "fields_with_unharmonized_verbatim_near_identity_path_from_a_non_governed_leaf":
                                    sorted({n for n, r in ev.lineage_fields.items() for lf in r.get("frontier_leaves") or ()
                                            if ev.classify_source(lf["leaf"]) in (PHYSICAL_RAW_BOUNDARY, EXTERNAL_CONTRACT_UNDETERMINED) and _verbatim_vocabulary(lf)
                                            and _paths(lf, characters=NEAR_IDENTITY_CHARACTERS, through=False)})},
        "branch_reconvergence_pairs": len(ev.reconvergences),
        "source_state_counts": dict(sorted(ev.source_state_counts.items())),
    }


# ───────────────────────────── RESOLUCION DE NIVEL EXPLORATORIA (variante LEVEL_RESOLUTION) ─────────────────────────────
def _no_integration_scope(ev: ProfileEvidence) -> list[str]:
    """Justificacion ARQUITECTONICA de «armonizacion no aplicable»: NINGUN candidato de armonizacion en el cierre, NINGUNA vista del cierre integra varias entradas (0 joins y 0 uniones: metricas
    observadas) y ningun nodo de producto integra >1 hoja. Con esa arquitectura no existe responsabilidad de armonizar. La mera ausencia de una capa NO alcanza (p. ej. un join de dos hojas la desmiente).
    Devuelve las bases (vacio = no se puede justificar)."""
    if ev.harmonization or not ev.views:
        return []
    for v in ev.views.values():
        m = v.get("metrics") or {}
        if m.get("joins") or m.get("unions"):
            return []
    for n in ev.product_nodes:
        eff = ev.effective_providers(n)
        if len(eff.get(K_LOCAL_LEAF, ())) + len(eff.get(K_BOUNDARY, ())) > 1:
            return []
    return ["ARCH.no_harmonization_candidate_in_closure", "ARCH.no_view_in_closure_integrates_several_inputs_joins_0_unions_0", "ARCH.no_product_node_integrates_more_than_one_leaf"]


def _propagation_met_materialized(ev: ProfileEvidence, policy: str) -> tuple[list[str], list[dict]]:
    """LEVEL_RESOLUTION integrada con ``leaf-materialization/v1`` — misma condicion estructural que ``_propagation_met_by_replicated_ingestion`` (el producto compone SOLO contratos ya resueltos y
    a lo sumo UNA hoja local), pero la hoja local debe ser una RÉPLICA TECNICAMENTE ACREDITADA: ``materialization_kind = REPLICA`` (un unico flujo EXACT/resuelto con mapeos de identidad
    verificados), clase de borde derivada ``GOVERNED_DERIVED`` con cadena completa, procedencia ESTABLISHED y la clase de fuente explicita GOBERNADA (gobierno = ``source_contract_classes``; la replica
    no lo otorga). Una hoja UNKNOWN (p. ej. una sombra Delta sin CSN), TRANSFORMED o multi-productor NO aporta evidencia positiva. Un wrapper con filtro de estado CDC no es una transformacion semantica,
    pero tampoco una exposicion integra: se informa como calificador (``filtered`` / ``filter_natures``). Devuelve (bases, calificadores por hoja)."""
    if not ev.product_nodes:
        return [], []
    bases: list[str] = []
    quals: list[dict] = []
    for n in ev.product_nodes:
        eff = ev.effective_providers(n)
        if not set(eff) <= {K_VIEW, K_HARM, K_LOCAL_LEAF} or len(eff.get(K_LOCAL_LEAF, ())) > 1:
            return [], []
        for lf in eff.get(K_LOCAL_LEAF, ()):
            rec = ev.local_leaves.get(lf) or {}
            m = ev.materialization_of(lf)
            if not (m is not None and rec.get("provenance_state") == "ESTABLISHED" and m["materialization_kind"] == LM.KIND_REPLICA and not m["flags"]["multi_producer"]
                    and m["derived_boundary_class"] == LM.DBC_GOVERNED and m["chain_complete"] is True and ev.classify_source(lf, policy) == GOVERNED_SAP_CONTRACT):
                return [], []
            bases += [f"ce:leaf_provenance/{lf}", f"ce:physical_source_boundaries/{lf}"]
            mp = m["mapping_profile"]
            quals.append({"leaf": lf, "materialization_kind": m["materialization_kind"], "derived_boundary_class": m["derived_boundary_class"], "filtered": m["flags"]["filtered"],
                          "filter_natures": sorted({p["wrapper"]["filter_nature"] for p in m["producers"] if p.get("wrapper") and p["wrapper"].get("filter_nature")}),
                          "resolved_via": sorted({p["resolved_via"] for p in m["producers"] if p.get("resolved_via")}), "implicit_identity": mp["implicit_identity"],
                          "identity_normalized": mp["identity_normalized"], "column_evidence": mp["column_evidence"]})
    return ([*sorted(set(bases)), "PRO.product_consumes_only_a_technically_accredited_replica_of_a_governed_contract"], quals) if bases else ([], [])


def _propagation_met_by_replicated_ingestion(ev: ProfileEvidence, policy: str) -> list[str]:
    """EVIDENCIA POSITIVA de «el producto compone contratos ya resueltos» cuando lee una tabla LOCAL replicada: cada nodo de producto consume SOLO vistas/candidatos/tablas locales (ninguna hoja de frontera
    directa), a lo sumo UNA hoja local, y esa hoja es una RÉPLICA PURA: procedencia COMPROBADA (ESTABLISHED, cadena completa), producida UNICAMENTE por Replication Flow y de clase de contrato gobernado.
    Una tabla escrita ademas por un Data Flow (transformada/cargada por dos vías) no es una réplica pura. Sin esa procedencia: nada."""
    if not ev.product_nodes:
        return []
    bases: list[str] = []
    for n in ev.product_nodes:
        eff = ev.effective_providers(n)
        if not set(eff) <= {K_VIEW, K_HARM, K_LOCAL_LEAF} or len(eff.get(K_LOCAL_LEAF, ())) > 1:
            return []
        for lf in eff.get(K_LOCAL_LEAF, ()):
            rec = ev.local_leaves.get(lf) or {}
            kinds = {p.get("kind") for p in rec.get("producer_flows") or ()}
            if not (rec.get("provenance_state") == "ESTABLISHED" and rec.get("chain_complete") is True and rec.get("basis") == "REPLICATION_FLOW" and kinds == {"REPLICATION_FLOW"}
                    and ev.classify_source(lf, policy) == GOVERNED_SAP_CONTRACT):
                return []                                           # una tabla que ADEMAS escribe un Data Flow (u otro productor) ya no es una replica pura: no hay evidencia positiva de «ingesta replicada»
            bases.append(f"ce:leaf_provenance/{lf}")
    return [*sorted(set(bases)), "PRO.product_consumes_only_a_replicated_governed_ingestion_contract_with_established_provenance"] if bases else []


def dimension_resolution(ev: ProfileEvidence, policy: str, strengths_: list[dict], reasons: list[dict], missing: list[dict], materialized: bool = False) -> list[dict]:
    """Estado por dimension para la resolucion exploratoria del nivel, con su evidencia y causa. Solo lee lo ya calculado por el motor (fortalezas, razones, causas de ausencia) y evidencia canonica."""
    have = {s["rule_id"] for s in strengths_}
    by_dim = {m["dimension"]: m for m in missing}
    out = []
    for dim, ids in (("SOURCE_INGESTION", ("SRC-STR-01",)), ("HARMONIZATION", ("HAR-STR-01", "HAR-STR-02")), ("PROPAGATION", ("PRO-STR-01",)), ("PUBLISHED_CONTRACT", ("PUB-STR-01",))):
        dim_reasons = [r["rule_id"] for r in reasons if r["dimension"] == dim and r["state"] not in C.REMOVING_STATES]
        item = {"dimension": dim, "state": C.DS_UNRESOLVED, "cause": None, "basis": [], "local_deficiencies": dim_reasons}
        if any(i in have for i in ids):
            item["state"] = C.DS_STRENGTH
            item["basis"] = [i for i in ids if i in have]
        else:
            m = by_dim.get(dim) or {}
            item["cause"] = m.get("cause")
            if m.get("state") == "INSUFFICIENT_EVIDENCE" or m.get("cause") in ("NO_SOURCE_CONTACT_OBSERVED", "NO_PRODUCT_NODE_OBSERVED"):
                item["state"] = C.DS_NOT_EVALUABLE                                                   # falta evidencia: NO es «no aplicable»
            elif dim_reasons:
                item["state"], item["basis"] = C.DS_DEFICIENCY, dim_reasons
            elif dim == "HARMONIZATION" and m.get("cause") == "NO_HARMONIZATION_BOUNDARY_OBSERVED":
                na = _no_integration_scope(ev)
                if na:
                    item["state"], item["basis"] = C.DS_NOT_APPLICABLE, na
            elif dim == "PROPAGATION" and m.get("cause") == "PRODUCT_DOES_NOT_COMPOSE_RESOLVED_CONTRACTS":
                if materialized:
                    met, quals = _propagation_met_materialized(ev, policy)
                else:
                    met, quals = _propagation_met_by_replicated_ingestion(ev, policy), []
                if met:
                    item["state"], item["basis"] = C.DS_MET, met
                    if materialized:
                        item["materialization"] = quals
        out.append(item)
    return out


def resolve_level_exploratory(states: list[dict]) -> tuple[str, str] | None:
    """HIPOTESIS de nivel (NO definitiva) sobre los estados por dimension, solo para los casos que la regla vigente deja en LVL-06. ``None`` = sigue «No determinable».
    · cualquier dimension aplicable no evaluable, sin resolver (sin evidencia positiva) o con deficiencia demostrada ⇒ no se promueve;
    · BUENA (LVL-07): fortalezas en >= 3 dimensiones y el resto cumplida o no aplicable;
    · ADECUADA (LVL-08): todas las dimensiones aplicables con fortaleza o responsabilidad cumplida, con fortalezas en < 3 y al menos una."""
    applicable = [s for s in states if s["state"] != C.DS_NOT_APPLICABLE]
    if not applicable or any(s["state"] in (C.DS_NOT_EVALUABLE, C.DS_UNRESOLVED, C.DS_DEFICIENCY) for s in applicable):
        return None
    n = sum(1 for s in applicable if s["state"] == C.DS_STRENGTH)
    if n >= C.BUENA_MIN_STRENGTH_DIMENSIONS:
        return C.BUENA, C.LEVEL_RULE_BUENA_BY_RESOLUTION
    if n >= 1:
        return C.ADECUADA, C.LEVEL_RULE_ADECUADA_BY_RESOLUTION
    return None


# ───────────────────────────── judgment-rules/v4: RESOLUCION DE NIVEL POR ESTADO DE DIMENSION (politica transversal) ─────────────────────────────
# Principio: un NO DETERMINABLE legitimo es evidencia INSUFICIENTE (o una clase de contrato critica sin decidir). Si la evidencia alcanza en los cuatro contratos, el nivel DEBE salir de los hechos
# verificados. v4 NO agrega razones ni fortalezas (``reasons``/``strengths`` salen de v3 + T5); solo evalua, para los casos que v3 deja en LVL-06, el ESTADO de cada dimension:
#   STRENGTH / RESPONSIBILITY_MET (evidencia positiva, NO fortaleza) / NOT_APPLICABLE (justificacion arquitectonica) / DEFICIENCY_DEMONSTRATED (hecho verificado adverso)
#   NOT_EVALUABLE (evidencia insuficiente) / UNRESOLVED (evidencia suficiente pero ningun predicado la clasifica).
# Nivel: cualquier NOT_EVALUABLE o UNRESOLVED => sigue «No determinable»; si no, ADECUADA (LVL-08) con >= 1 fortaleza. NUNCA BUENA por resolucion (BUENA exige las 4 fortalezas) y NUNCA PARCIAL
# (PARCIAL exige una desviacion MATERIAL; «tabla fisica cruda» sola jamas lo es: ver encabezado del modulo).
def har_unknown_candidates(ev: ProfileEvidence) -> list[str]:
    """Candidatos de armonizacion de caracter UNKNOWN (etiquetas/mapeo/productor no determinables) que ALIMENTAN algun artefacto aguas abajo del cierre: no son deficiencia ni fortaleza; la dimension no
    es evaluable. Un candidato sin consumidores en el cierre no interviene en la armonizacion del producto: su evidencia faltante no impide evaluarlo."""
    return sorted(c["artifact"] for c in ev.harmonization.values() if (c.get("character") or {}).get("value") == "UNKNOWN" and ev.descendants(c["artifact"]))


def har_projection_over_physical_raw(ev: ProfileEvidence, policy: str) -> list[str]:
    """Candidatos de armonizacion cuyas fuentes incluyen tablas FISICAS CRUDAS verificadas (clase explicita): la frontera existe pero proyecta una fuente no gobernada; con evidencia determinable
    (ningun candidato UNKNOWN) esto es un hecho adverso verificado de la dimension, no una laguna."""
    if har_unknown_candidates(ev):
        return []
    return sorted(c["artifact"] for c in ev.harmonization.values() if any(ev.classify_source(s, policy) == PHYSICAL_RAW_BOUNDARY for s in (c.get("sources") or ())))


def published_vocabulary_leak_from_physical_raw(ev: ProfileEvidence, policy: str) -> list[str]:
    """Campos publicados que heredan SIN CAMBIOS (camino casi-identidad, sin armonizacion) el vocabulario de una hoja FISICA CRUDA verificada. Solo la clase explicita ``PHYSICAL_RAW_BOUNDARY``: una
    clase indeterminada/desconocida NO es un hecho verificado (eso es evidencia insuficiente)."""
    out = set()
    for name, r in ev.lineage_fields.items():
        for lf in r.get("frontier_leaves") or ():
            if ev.classify_source(lf["leaf"], policy) == PHYSICAL_RAW_BOUNDARY and _verbatim_vocabulary(lf) and _paths(lf, characters=NEAR_IDENTITY_CHARACTERS, through=False):
                out.add(name)
    return sorted(out)


def _local_leaf_accredited_governed(ev: ProfileEvidence, policy: str, leaf: str) -> bool:
    """Tabla LOCAL cuyo contenido proviene SOLO de contratos gobernados por flujos con mapeo de identidad/renombre (0 expresiones, 0 desconocidos), cadena completa, procedencia ESTABLISHED y clase
    gobernada (varios productores —p. ej. carga inicial + delta— no la convierten en transformada semantica). Evidencia persistida (leaf-materialization/v1), nunca inferida por nombre."""
    rec = ev.local_leaves.get(leaf) or {}
    m = ev.materialization_of(leaf)
    if m is None or rec.get("provenance_state") != "ESTABLISHED" or m["chain_complete"] is not True or m["derived_boundary_class"] != LM.DBC_GOVERNED or m["unknown_reasons"]:
        return False
    mp = m["mapping_profile"]
    return ev.classify_source(leaf, policy) == GOVERNED_SAP_CONTRACT and mp["expression"] == 0 and mp["unknown"] == 0


def propagation_normal_composition(ev: ProfileEvidence, policy: str) -> list[str]:
    """PROPAGACION «cumplida» por composicion NORMAL de insumos resueltos: cada nodo de producto lee vistas/candidatos de armonizacion, contratos de frontera GOBERNADOS (un hecho gobernado
    compuesto con una dimension armonizada es composicion normal: la misma salvedad de PRO-MAT-01) y tablas locales acreditadas como derivadas de contratos gobernados. Ninguna hoja cruda ni de clase
    indeterminada. Es evidencia positiva de responsabilidad cumplida, NO una fortaleza (PRO-STR-01 sigue exigiendo que no haya hoja de frontera directa)."""
    if not ev.product_nodes:
        return []
    bases: list[str] = []
    for n in ev.product_nodes:
        eff = ev.effective_providers(n)
        if not set(eff) <= {K_VIEW, K_HARM, K_LOCAL_LEAF, K_BOUNDARY} or n not in ev.views:
            return []
        if any(ev.classify_source(p, policy) != GOVERNED_SAP_CONTRACT for p in eff.get(K_BOUNDARY, ())):
            return []
        if len(eff.get(K_BOUNDARY, ())) + len(eff.get(K_LOCAL_LEAF, ())) > 1 and not eff.get(K_HARM):
            return []                                                   # integrar >1 hoja SIN ningun candidato de armonizacion es re-integracion en el producto (la forma (ii) de PRO-MAT-01), no composicion normal
        if any(not _local_leaf_accredited_governed(ev, policy, lf) for lf in eff.get(K_LOCAL_LEAF, ())):
            return []
        bases += [f"ce:view_complexity/{n}", *[f"ce:source_contract_classes/{p}" for p in eff.get(K_BOUNDARY, ())], *[f"ce:leaf_provenance/{lf}" for lf in eff.get(K_LOCAL_LEAF, ())]]
    return [*sorted(set(bases)), "PRO.product_composes_only_resolved_inputs_governed_contracts_harmonized_views_or_accredited_replicas"]


def dimension_resolution_v4(ev: ProfileEvidence, policy: str, strengths_: list[dict], reasons: list[dict], missing: list[dict], pub_lineage_complete: bool, materialized: bool = True) -> list[dict]:
    """Estado por dimension de judgment-rules/v4. Solo lee lo ya calculado (fortalezas, razones, causas de ausencia) y evidencia canonica; no inventa evidencia ni agrega razones/fortalezas."""
    have = {s["rule_id"] for s in strengths_}
    by_dim = {m["dimension"]: m for m in missing}
    out = []
    for dim, ids in (("SOURCE_INGESTION", ("SRC-STR-01",)), ("HARMONIZATION", ("HAR-STR-01", "HAR-STR-02")), ("PROPAGATION", ("PRO-STR-01",)), ("PUBLISHED_CONTRACT", ("PUB-STR-01",))):
        dim_reasons = [r["rule_id"] for r in reasons if r["dimension"] == dim and r["state"] not in C.REMOVING_STATES]
        item = {"dimension": dim, "state": C.DS_UNRESOLVED, "cause": None, "basis": [], "local_deficiencies": dim_reasons}
        m = by_dim.get(dim) or {}
        if any(i in have for i in ids):
            item["state"], item["basis"] = C.DS_STRENGTH, [i for i in ids if i in have]
        elif m.get("state") == "INSUFFICIENT_EVIDENCE" or m.get("cause") in ("NO_SOURCE_CONTACT_OBSERVED", "NO_PRODUCT_NODE_OBSERVED"):
            item["state"], item["cause"] = C.DS_NOT_EVALUABLE, m.get("cause")
        elif dim_reasons:
            item["state"], item["cause"], item["basis"] = C.DS_DEFICIENCY, m.get("cause"), dim_reasons
        elif dim == "HARMONIZATION":
            item["cause"] = m.get("cause")
            if har_str_01_reformulated(ev, policy):
                item["state"], item["basis"] = C.DS_MET, ["HAR.one_to_one_over_governed_contract", "HAR.business_vocabulary_preserved_by_provider_labels", "HAR.consumed_by_downstream_artifact"]
            elif har_projection_over_physical_raw(ev, policy):
                item["state"], item["basis"] = C.DS_DEFICIENCY, ["HAR.reusable_boundary_projects_verified_physical_raw_sources"]
            elif m.get("cause") == "NO_HARMONIZATION_BOUNDARY_OBSERVED":
                na = _no_integration_scope(ev)
                if na:
                    item["state"], item["basis"] = C.DS_NOT_APPLICABLE, na
        elif dim == "PROPAGATION":
            item["cause"] = m.get("cause")
            met = _propagation_met_materialized(ev, policy)[0] if materialized else _propagation_met_by_replicated_ingestion(ev, policy)
            met = met or propagation_normal_composition(ev, policy)
            if met:
                item["state"], item["basis"] = C.DS_MET, met
        elif dim == "PUBLISHED_CONTRACT":
            item["cause"] = m.get("cause")
            leak = published_vocabulary_leak_from_physical_raw(ev, policy) if pub_lineage_complete else []
            if leak:
                item["state"], item["basis"] = C.DS_DEFICIENCY, ["PUB.vocabulary_of_a_verified_physical_raw_boundary_reaches_the_consumer_verbatim"]
        elif dim == "SOURCE_INGESTION":
            item["cause"] = m.get("cause")
        out.append(item)
    return out


def resolve_level_v4(states: list[dict]) -> tuple[str, str] | None:
    """(nivel, regla) o ``None`` (sigue «No determinable»). Cualquier dimension no evaluable o sin resolver => None. Con las cuatro resueltas y >= 1 fortaleza => ADECUADA (LVL-08)."""
    applicable = [s for s in states if s["state"] != C.DS_NOT_APPLICABLE]
    if not applicable or any(s["state"] in (C.DS_NOT_EVALUABLE, C.DS_UNRESOLVED) for s in applicable):
        return None
    return (C.ADECUADA, C.LEVEL_RULE_ADECUADA_BY_RESOLUTION) if any(s["state"] == C.DS_STRENGTH for s in applicable) else None


def mixed_auxiliary_physical_reviews(ev: ProfileEvidence, policy: str) -> list[dict]:
    """T5 integrada — componente fisico COMPROBADO de una frontera MIXED que NO participa de la espina del producto (rama auxiliar): evidencia tecnica informativa, SIN razon, sin penalizacion y
    sin efecto en el nivel ni en el cierre. Solo existe para que el alcance efectivo de SRC-LOC-01 (espina) no oculte el hecho."""
    aux = [c for c in local_mixed_physical_components_materialized(ev, policy) if not c["on_spine"]]
    if not aux:
        return []
    return [_review("MIXED_BOUNDARY_AUXILIARY_PHYSICAL_COMPONENT", "SRC-REV-03", "MIXED_BOUNDARY_PHYSICAL_COMPONENT_OFF_SPINE", "OBSERVED", artifacts=[c["leaf"] for c in aux],
                    refs=[r for c in aux for r in (f"ce:leaf_provenance/{c['leaf']}", f"ce:physical_source_boundaries/{c['leaf']}")], ev=ev,
                    extra={"physical_components": [{k: c[k] for k in ("leaf", "physical_entities", "governed_entities")} for c in aux], "interpretation": "TECHNICAL_EVIDENCE_NOT_A_DEFICIENCY"})]


def evaluate_rules(ev: ProfileEvidence, policy: str = "UNDETERMINED", variants: frozenset = frozenset(), ratified: frozenset = frozenset()) -> dict:
    pro = rule_pro_mat_01(ev, policy)
    lek = rule_lek_mat_02(ev, policy)
    src = rule_src_rel_loc(ev, policy, variants)
    h1, h2 = rule_har_rel_01(ev), rule_har_rel_02(ev)
    loc = rule_pro_loc_01(ev, {r["reason_id"].split(":", 1)[1] for r in pro})
    reasons = [*pro, *lek, *src, *h1, *h2, *loc]
    st = strengths(ev, policy, has_pro_mat=bool(pro), variants=variants)
    cg = review_complex_graphical_chain(ev)
    reviews, limitations = reviews_and_limitations(ev, policy, ratified)
    if cg:
        reviews.append(cg)
    if C.VARIANT_T5_LOCAL_PHYSICAL in variants and C.VARIANT_MATERIALIZED_INPUT in variants:
        reviews += mixed_auxiliary_physical_reviews(ev, policy)
    qualifiers = {
        "design_coherence": qualifier_design_coherence(ev, bool(h2), bool(cg)),
        "product_focus": qualifier_product_focus(ev),
        "naming_governance": qualifier_naming(ev, bool(h2)),
    }
    return {"reasons": reasons, "strengths": st, "qualifiers": qualifiers, "review_candidates": reviews, "evidence_limitations": limitations,
            "supporting_evidence": supporting_evidence(ev), "critical_source_undetermined": decision_critical_undetermined_source(ev, policy, ratified)}
