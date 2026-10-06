"""Playbook GOBERNADO de candidatos de CTA (llamado a la accion) — ``cta-playbook/v1``. Determinista: cada candidato nace de una razon/revision/limitacion del motor.
No hay texto libre ni recomendaciones de arquitectura fuera del catalogo: un futuro LLM solo puede SELECCIONAR / PRIORIZAR / EXPLICAR estos candidatos."""
from __future__ import annotations

from typing import Any, Mapping

from . import contract as C

# rule_id (causa) -> (cta_id, action_family, priority_class, action_keys)
PLAYBOOK: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "PRO-MAT-01": ("CTA-PRO-RESOLVE-MIXED-UNIVERSE", "PROPAGATION_RESOLVE_MIXED_UNIVERSE", "MATERIAL",
                   ("extend_or_reuse_harmonization_contract", "move_reusable_transformations_to_harmonization", "keep_only_product_specific_composition_in_propagation",
                    "evaluate_governed_source_contract")),
    "PRO-LOC-01": ("CTA-PRO-RESOLVE-MIXED-UNIVERSE", "PROPAGATION_RESOLVE_MIXED_UNIVERSE", "LOCAL", ("keep_only_product_specific_composition_in_propagation",)),
    "LEK-MAT-02": ("CTA-LEK-RESTORE-RESPONSIBILITY", "RESTORE_LAYER_RESPONSIBILITY", "MATERIAL",
                   ("resolve_responsibility_at_expected_boundary", "normalize_vocabulary_toward_published_contract", "reduce_distributed_logic_in_intermediate_artifacts",
                    "enrich_business_identity_of_published_measures")),
    "SRC-REL-01": ("CTA-SRC-GOVERNED-SOURCE", "EVALUATE_GOVERNED_SOURCE_CONTRACT", "RELEVANT", ("evaluate_governed_cds_or_odp_instead_of_physical_table", "record_governed_exception_if_legitimate")),
    "SRC-LOC-01": ("CTA-SRC-GOVERNED-SOURCE", "EVALUATE_GOVERNED_SOURCE_CONTRACT", "LOCAL", ("evaluate_governed_cds_or_odp_instead_of_physical_table",)),
    "HAR-REL-01": ("CTA-HAR-HARMONIZE-REUSABLE-SEMANTICS", "CREATE_OR_REUSE_SEMANTIC_HARMONIZATION", "RELEVANT",
                   ("normalize_or_enrich_vocabulary_in_harmonization", "publish_reusable_business_labels", "remove_projections_without_semantic_responsibility")),
    "HAR-REL-02": ("CTA-HAR-CONSOLIDATE-FRAGMENTED-LAYERS", "CONSOLIDATE_FRAGMENTED_LAYERS", "RELEVANT",
                   ("consolidate_redundant_intermediate_views", "make_layer_roles_explicit", "reduce_unnecessary_reconvergence")),
    "SRC-REV-01": ("CTA-SRC-DOCUMENT-EXTERNAL-CONTRACT", "CLARIFY_ACQUISITION_STRATEGY", "REVIEW", ("establish_class_of_external_object",)),
    "SRC-REV-02": ("CTA-SRC-DECLARE-REPLICATION-MODE", "CLARIFY_ACQUISITION_STRATEGY", "REVIEW", ("declare_replication_or_federation_mode", "declare_load_strategy")),
    "MNT-QUAL-01": ("CTA-MNT-REVIEW-GRAPH-DECOMPOSITION", "REVIEW_GRAPHICAL_DECOMPOSITION", "MAINTAINABILITY",
                    ("review_graph_decomposition", "consolidate_redundant_intermediate_views", "evaluate_sql_where_it_improves_traceability", "preserve_graphical_where_clearer")),
    "PRO-REV-01": ("CTA-PRO-PREDICATE-OWNERSHIP-REVIEW", "REVIEW_FIXED_PREDICATE_OWNERSHIP", "REVIEW", ("review_predicate_ownership", "evaluate_governed_access_control")),
    "GOV-REV-01": ("CTA-GOV-DECLARE-NAMING-CONVENTION", "DECLARE_NAMING_CONVENTION", "REVIEW", ("declare_local_convention_via_governed_exception", "align_naming_where_role_ambiguity_justifies")),
    "FOC-REV-01": ("CTA-FOC-CONFIRM-PRODUCT-INTENT", "CONFIRM_PRODUCT_INTENT", "REVIEW", ("confirm_product_intent",)),
    "EVD-LIM-01": ("CTA-EVD-CLOSE-EVIDENCE-GAP", "CLOSE_EVIDENCE_GAPS", "EVIDENCE", ("acquire_missing_processing_evidence", "complete_missing_mapping_evidence")),
    "PUB-SUP-01": ("CTA-PUB-ENRICH-SEMANTICS", "ENRICH_PUBLISHED_SEMANTICS", "REVIEW", ("enrich_business_labels", "associate_reusable_business_dimensions", "reduce_source_vocabulary_leakage")),
}
CTA_IDS = frozenset(v[0] for v in PLAYBOOK.values())
_PRIORITY_RANK = {p: i for i, p in enumerate(C.CTA_PRIORITIES)}
_STATE_RANK = {"APPLICABLE": 0, "REVIEW": 1, "DISABLED_PENDING_EVIDENCE": 2}


def _add(acc: dict, rule_id: str, *, artifacts=(), refs=(), linked=(), state: str = "APPLICABLE") -> None:
    cta_id, family, prio, keys = PLAYBOOK[rule_id]
    c = acc.setdefault(cta_id, {"cta_id": cta_id, "action_family": family, "priority_class": prio, "action_keys": set(), "cause_rule_ids": set(), "affected_artifacts": set(),
                                "evidence_refs": set(), "linked_reason_ids": set(), "state": state})
    c["action_keys"].update(keys)
    c["cause_rule_ids"].add(rule_id)
    c["affected_artifacts"].update(artifacts)
    c["evidence_refs"].update(refs)
    c["linked_reason_ids"].update(linked)
    if _PRIORITY_RANK[prio] < _PRIORITY_RANK[c["priority_class"]]:
        c["priority_class"] = prio
    if _STATE_RANK[state] < _STATE_RANK[c["state"]]:                       # APPLICABLE > REVIEW > DISABLED_PENDING_EVIDENCE
        c["state"] = state


def build_cta_candidates(reasons: list[Mapping[str, Any]], reviews: list[Mapping[str, Any]], qualifiers: Mapping[str, Any], limitations: list[Mapping[str, Any]],
                         supporting: Mapping[str, Any]) -> list[dict]:
    acc: dict[str, dict] = {}
    for r in reasons:
        if r["rule_id"] in PLAYBOOK and r["state"] not in C.REMOVING_STATES:
            _add(acc, r["rule_id"], artifacts=r["affected_artifacts"], refs=r["evidence_refs"], linked=[r["reason_id"]])
    for rv in reviews:
        rid = rv["rule_id"]
        if rid not in PLAYBOOK:
            continue
        pending = str(rv["state"]).startswith("NOT_EVALUATED")
        _add(acc, rid, artifacts=rv["affected_artifacts"], refs=rv["evidence_refs"], linked=[rv["review_id"]], state="DISABLED_PENDING_EVIDENCE" if pending else "REVIEW")
    naming = qualifiers.get("naming_governance") or {}
    if naming.get("disposition") == C.NEEDS_CONTEXT:
        _add(acc, "GOV-REV-01", state="REVIEW", linked=["naming_governance"])
    focus = qualifiers.get("product_focus") or {}
    if focus.get("value") == "GENERIC":
        _add(acc, "FOC-REV-01", state="REVIEW", linked=["product_focus"])
    if any(l["key"] in ("local_leaf_provenance_not_evaluated", "harmonization_mapping_not_observable") for l in limitations):
        _add(acc, "EVD-LIM-01", artifacts=[a for l in limitations if l["key"] in ("local_leaf_provenance_not_evaluated", "harmonization_mapping_not_observable") for a in l["affected_artifacts"]],
             refs=[r for l in limitations if l["key"] in ("local_leaf_provenance_not_evaluated", "harmonization_mapping_not_observable") for r in l["evidence_refs"]], state="REVIEW",
             linked=[l["limitation_id"] for l in limitations if l["key"] in ("local_leaf_provenance_not_evaluated", "harmonization_mapping_not_observable")])
    if supporting.get("published_measures_without_business_identity"):
        _add(acc, "PUB-SUP-01", artifacts=supporting["published_measures_without_business_identity"], refs=["ce:published_contract/fields"], state="REVIEW", linked=["published_supporting"])
    out = []
    for cta_id in sorted(acc):
        c = acc[cta_id]
        out.append({"cta_id": cta_id, "cause_rule_ids": sorted(c["cause_rule_ids"]), "affected_artifacts": sorted(c["affected_artifacts"]), "priority_class": c["priority_class"],
                    "action_family": c["action_family"], "action_keys": sorted(c["action_keys"]), "evidence_refs": sorted(c["evidence_refs"]),
                    "linked_reason_ids": sorted(c["linked_reason_ids"]), "applicability_state": c["state"]})
    out.sort(key=lambda c: (_PRIORITY_RANK[c["priority_class"]], c["cta_id"]))
    return out
