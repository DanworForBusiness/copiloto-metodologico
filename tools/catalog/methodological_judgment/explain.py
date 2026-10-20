"""Explicabilidad de «NO DETERMINABLE» — PROYECCION determinista de un Juicio YA persistido sobre su perfil YA persistido (``nd-explanation/v1``).

NO es un segundo motor ni una heuristica de texto: no decide ni recalcula ningun nivel. Lee (1) los estados que el motor de Juicio ya persistio (``not_determinable_reason``, ``level_rule``,
``dimension_states``, ``not_determinable_detail``, ``contracts_not_evaluable``) y (2) hechos del perfil a traves de ``ProfileEvidence`` y de los MISMOS predicados del motor
(``har_unknown_candidates``, ``decision_critical_undetermined_source``, ``published_contract_conformance``…) para identificar QUE artefactos y QUE evidencia sostienen cada impedimento.
Cada impedimento se CORROBORA contra el motor (el predicado que lo produjo se vuelve a evaluar sobre el perfil); si no se puede corroborar o el detalle no esta persistido, se dice
(``diagnosis_limits``): jamas se inventa precision. Un Juicio determinado no tiene impedimentos (``None``). Sin escrituras, sin red, sin LLM, sin reloj; no agrega campos al Juicio ni a su firma.

Contrato de un impedimento (``blockers[]``): ``blocker_id``, ``dimension`` (None = nivel global), ``state`` (``NOT_EVALUABLE`` | ``UNRESOLVED_NO_POSITIVE_EVIDENCE``, los valores del motor),
``rule_id`` (regla de nivel que dejo el veredicto: LVL-01/05/06), ``condition_id`` (causa estable ya persistida), ``reason_code`` (causa tecnica normalizada si el perfil la trae),
``category`` (una de ``CATEGORIES``), ``artifacts``, ``evidence_refs`` (solo referencias que existen en el perfil), ``facts`` (hechos estructurados), ``required_evidence`` (claves),
``resolution_type``, ``resolution_hint_key`` (clave; el texto vive en el presentador), ``confidence`` (certeza del DIAGNOSTICO, no la confianza global) y ``corroborated`` (bool | None).
Resolver un impedimento NO garantiza un nivel concreto: habilita que el motor resuelva el nivel con el resto del Juicio."""
from __future__ import annotations

from typing import Any, Mapping

from . import contract as C
from .engine import _contracts_not_evaluable
from .profile_access import EXTERNAL_CONTRACT_UNDETERMINED, ProfileEvidence
from .rules import decision_critical_undetermined_source, har_unknown_candidates, published_contract_conformance, resolve_level_v4

EXPLANATION_VERSION = "nd-explanation/v1"
POLICY = "UNDETERMINED"

# Categorias (el diagnostico distingue estas situaciones; nunca las mezcla)
EVIDENCE_NOT_ACQUIRED = "EVIDENCE_NOT_ACQUIRED"                          # el alcance de extraccion no trae lo necesario para evaluar
EVIDENCE_NOT_INTERPRETABLE = "EVIDENCE_NOT_INTERPRETABLE"                # hay artefactos, pero faltan atributos / etiquetas / clasificacion para interpretarlos
SOURCE_CONTRACT_UNDETERMINED = "SOURCE_CONTRACT_UNDETERMINED"            # la identidad o clase del contrato de origen requiere validacion
ARCHITECTURAL_RESPONSIBILITY_UNPROVEN = "ARCHITECTURAL_RESPONSIBILITY_UNPROVEN"   # evidencia suficiente, pero ninguna frontera/funcion necesaria esta demostrada
GOVERNANCE_DECISION_PENDING = "GOVERNANCE_DECISION_PENDING"              # hay evidencia, falta aceptacion explicita
ASSESSMENT_PRECONDITION = "ASSESSMENT_PRECONDITION"                      # el Assessment no esta en condiciones de ser juzgado
UNDETERMINED_CAUSE = "UNDETERMINED_CAUSE"                                # el Juicio persistido no registro una causa verificable
CATEGORIES = (EVIDENCE_NOT_ACQUIRED, EVIDENCE_NOT_INTERPRETABLE, SOURCE_CONTRACT_UNDETERMINED, ARCHITECTURAL_RESPONSIBILITY_UNPROVEN, GOVERNANCE_DECISION_PENDING, ASSESSMENT_PRECONDITION, UNDETERMINED_CAUSE)
TECHNICAL_EVIDENCE, GOVERNANCE_DECISION, METHODOLOGICAL_REVIEW, MODEL_REMEDIATION = "TECHNICAL_EVIDENCE", "GOVERNANCE_DECISION", "METHODOLOGICAL_REVIEW", "MODEL_REMEDIATION"
RESOLUTION_TYPES = (TECHNICAL_EVIDENCE, GOVERNANCE_DECISION, METHODOLOGICAL_REVIEW, MODEL_REMEDIATION)
HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"

# causa persistida -> (categoria, tipo de resolucion, evidencia requerida [claves])
_SPEC: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "NO_SOURCE_CONTACT_OBSERVED": (EVIDENCE_NOT_ACQUIRED, TECHNICAL_EVIDENCE, ("SOURCE_CONTACT_EVIDENCE",)),
    "SOURCE_CONTRACT_CLASS_UNDETERMINED": (SOURCE_CONTRACT_UNDETERMINED, METHODOLOGICAL_REVIEW, ("SOURCE_CONTRACT_CLASS",)),
    "SOURCE_CONTACT_NOT_ALL_GOVERNED": (ARCHITECTURAL_RESPONSIBILITY_UNPROVEN, METHODOLOGICAL_REVIEW, ("GOVERNED_ACQUISITION_OF_EVERY_SOURCE_CONTACT",)),
    "HARMONIZATION_MAPPING_NOT_OBSERVABLE": (EVIDENCE_NOT_ACQUIRED, TECHNICAL_EVIDENCE, ("HARMONIZATION_ATTRIBUTE_MAPPING",)),
    "HARMONIZATION_CANDIDATE_CHARACTER_NOT_DETERMINABLE": (EVIDENCE_NOT_INTERPRETABLE, TECHNICAL_EVIDENCE, ("FIELD_LABEL_ORIGIN_OF_THE_CANDIDATE",)),
    "NO_HARMONIZATION_BOUNDARY_OBSERVED": (ARCHITECTURAL_RESPONSIBILITY_UNPROVEN, METHODOLOGICAL_REVIEW, ("REUSABLE_HARMONIZATION_BOUNDARY_OR_JUSTIFIED_NOT_APPLICABLE",)),
    "HARMONIZATION_NOT_A_LEGITIMATE_REUSABLE_BOUNDARY": (ARCHITECTURAL_RESPONSIBILITY_UNPROVEN, METHODOLOGICAL_REVIEW, ("REUSABLE_HARMONIZATION_BOUNDARY",)),
    "NO_PRODUCT_NODE_OBSERVED": (EVIDENCE_NOT_INTERPRETABLE, TECHNICAL_EVIDENCE, ("PRODUCT_POSITION_NODE",)),
    "PRODUCT_DOES_NOT_COMPOSE_RESOLVED_CONTRACTS": (ARCHITECTURAL_RESPONSIBILITY_UNPROVEN, METHODOLOGICAL_REVIEW, ("PRODUCT_COMPOSES_ONLY_RESOLVED_CONTRACTS",)),
    "PUBLISHED_LINEAGE_INCOMPLETE": (EVIDENCE_NOT_INTERPRETABLE, TECHNICAL_EVIDENCE, ("PUBLISHED_FIELD_LINEAGE",)),
    "UNRESOLVED_VOCABULARY_REACHES_THE_CONSUMER": (ARCHITECTURAL_RESPONSIBILITY_UNPROVEN, METHODOLOGICAL_REVIEW, ("PUBLISHED_CONTRACT_WITHOUT_UNRESOLVED_VOCABULARY",)),
    "NO_AFFIRMATIVE_PUBLISHED_FAMILY": (ARCHITECTURAL_RESPONSIBILITY_UNPROVEN, METHODOLOGICAL_REVIEW, ("PUBLISHED_CONTRACT_AFFIRMATIVE_FAMILY",)),
    "PUBLISHED_CONTRACT_SUBSTANCE_NOT_ESTABLISHED": (ARCHITECTURAL_RESPONSIBILITY_UNPROVEN, METHODOLOGICAL_REVIEW, ("PUBLISHED_CONTRACT_CONSUMPTION_SUBSTANCE",)),
    "NO_AFFIRMATIVE_EVIDENCE": (ARCHITECTURAL_RESPONSIBILITY_UNPROVEN, METHODOLOGICAL_REVIEW, ("AFFIRMATIVE_EVIDENCE_OF_THE_DIMENSION",)),
    "NO_STRENGTH_ANCHOR": (ARCHITECTURAL_RESPONSIBILITY_UNPROVEN, METHODOLOGICAL_REVIEW, ("AT_LEAST_ONE_AFFIRMATIVE_STRENGTH",)),
    "EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL": (SOURCE_CONTRACT_UNDETERMINED, GOVERNANCE_DECISION, ("SOURCE_CONTRACT_CLASS_DECISION",)),
    "CONTRACT_NOT_EVALUABLE": (EVIDENCE_NOT_ACQUIRED, TECHNICAL_EVIDENCE, ("ASSESSED_AREA_EVIDENCE",)),
    "ASSESSMENT_NOT_JUDGEABLE": (ASSESSMENT_PRECONDITION, TECHNICAL_EVIDENCE, ("ASSESSED_ASSESSMENT",)),
    "CANONICAL_EVIDENCE_V3_REQUIRED": (ASSESSMENT_PRECONDITION, TECHNICAL_EVIDENCE, ("CANONICAL_EVIDENCE_V3",)),
}
_DETAIL_STATE = {"INSUFFICIENT_EVIDENCE": C.DS_NOT_EVALUABLE, "NO_STRENGTH": C.DS_UNRESOLVED}      # ``not_determinable_detail`` (v3, sin ``dimension_states``) -> estados del motor
_BLOCKING = (C.DS_NOT_EVALUABLE, C.DS_UNRESOLVED)


def _names(items) -> list[str]:
    return sorted({str(x) for x in items or () if x})


def _refs(ev: ProfileEvidence | None, refs: list[str]) -> list[str]:
    """Solo referencias de evidencia que EXISTEN en el perfil (misma comprobacion que usa el motor)."""
    if ev is None:
        return []
    out = []
    for r in refs:
        try:
            if ev.ref_exists(r):
                out.append(r)
        except Exception:                                                    # noqa: BLE001 — una referencia mal formada no rompe la explicacion
            continue
    return out


def _derived_fields(ev: ProfileEvidence, artifact: str) -> list[dict]:
    fields = ev.section("derivation_semantics").get("fields") or []
    return [{"field": f.get("field"), "operations": list(f.get("operations") or [])} for f in fields if isinstance(f, Mapping) and f.get("artifact") == artifact]


# ───────────────────────────── hechos por causa (solo lectura del perfil + predicados del motor) ─────────────────────────────
def _facts_harmonization_character(ev: ProfileEvidence, limits: list[dict]) -> tuple[list[str], list[str], dict, bool | None]:
    cands = har_unknown_candidates(ev)
    out, products = [], set(ev.product_nodes)
    for a in cands:
        c = ev.harmonization[a]
        ch = c.get("character") or {}
        lo = c.get("label_origin_counts") or {}
        unknown = int(lo.get("UNKNOWN_PROVIDER_LABEL") or 0)
        derived = _derived_fields(ev, a)
        consumers = _names(ev.consumers.get(a))
        out.append({"artifact": a, "reason_unknown": ch.get("reason_unknown"), "unknown_label_field_count": unknown, "field_count": c.get("target_field_count"),
                    "derived_fields": derived, "derived_field_count": c.get("derived_field_count"), "transformation_classes": list(c.get("transformation_classes") or []),
                    "sources": _names(c.get("sources")), "consumed_by": consumers, "feeds_product": bool(products & set(consumers)),
                    "unknown_labels_equal_derived_fields": bool(unknown) and unknown == len(derived)})
        if unknown:
            limits.append({"key": "UNKNOWN_LABEL_FIELDS_NOT_PERSISTED", "artifact": a, "detail": f"el perfil persiste cuántos campos tienen etiqueta de origen indeterminado ({unknown}), no cuáles"})
    refs = _refs(ev, [f"ce:harmonization_contract/{a}" for a in cands])
    return cands, refs, {"candidates": out}, bool(cands)


def _facts_harmonization_mapping(ev: ProfileEvidence, limits: list[dict]) -> tuple[list[str], list[str], dict, bool | None]:
    cands = sorted(a for a, c in ev.harmonization.items() if c.get("mapping_determinable") is False)
    out = [{"artifact": a, "reason_unknown": ((ev.harmonization[a].get("character") or {}).get("reason_unknown")), "implementation": ev.harmonization[a].get("implementation"),
            "producer_flows": _names(ev.harmonization[a].get("producer_flows"))} for a in cands]
    return cands, _refs(ev, [f"ce:harmonization_contract/{a}" for a in cands]), {"candidates": out}, bool(cands)


def _facts_source_class(ev: ProfileEvidence, limits: list[dict]) -> tuple[list[str], list[str], dict, bool | None]:
    leaves = [l for l in ev.source_contact_leaves if ev.classify_source(l, POLICY) == EXTERNAL_CONTRACT_UNDETERMINED]
    critical = decision_critical_undetermined_source(ev, POLICY)
    rows = []
    for l in leaves:
        rec = ev.leaf_class_records.get(l) or {}
        rows.append({"leaf": l, "remote_connection": rec.get("remote_connection"), "remote_entities": _names(rec.get("remote_entities")), "decision_critical": l in critical,
                     "exposed_published_fields": len(critical.get(l, ()))})
    refs = _refs(ev, [f"ce:source_contract_classes/{l}" for l in leaves])
    return leaves, refs, {"leaves": rows, "ratification_would_apply": any(r["decision_critical"] for r in rows)}, bool(leaves)


def _facts_critical_source(ev: ProfileEvidence, limits: list[dict]) -> tuple[list[str], list[str], dict, bool | None]:
    critical = decision_critical_undetermined_source(ev, POLICY)
    rows = []
    for l in sorted(critical):
        rec = ev.leaf_class_records.get(l) or {}
        rows.append({"leaf": l, "remote_connection": rec.get("remote_connection"), "remote_entities": _names(rec.get("remote_entities")), "exposed_published_fields": _names(critical[l])})
    return sorted(critical), _refs(ev, [f"ce:source_contract_classes/{l}" for l in critical]), {"leaves": rows}, bool(critical)


def _facts_lineage(ev: ProfileEvidence, limits: list[dict]) -> tuple[list[str], list[str], dict, bool | None]:
    fields = list(ev.lineage_incomplete_fields)
    return [], _refs(ev, ["ce:published_field_lineage"]), {"incomplete_field_count": len(fields), "incomplete_fields": fields[:20]}, bool(fields)


def _facts_product(ev: ProfileEvidence, limits: list[dict]) -> tuple[list[str], list[str], dict, bool | None]:
    nodes = list(ev.product_nodes)
    rows = [{"node": n, "providers_by_kind": {k: _names(v) for k, v in sorted(ev.effective_providers(n).items())}} for n in nodes]
    return nodes, _refs(ev, [f"ce:view_complexity/{n}" for n in nodes]), {"product_nodes": rows}, None


def _facts_har_candidates(ev: ProfileEvidence, limits: list[dict]) -> tuple[list[str], list[str], dict, bool | None]:
    """Armonizacion con evidencia suficiente pero ninguna frontera reutilizable legitima: que candidatos hay y que caracter tienen (hechos del perfil, sin juicio)."""
    rows = [{"artifact": a, "character": (c.get("character") or {}).get("value"), "sources": _names(c.get("sources")), "consumed_by": _names(ev.consumers.get(a))} for a, c in sorted(ev.harmonization.items())]
    return [r["artifact"] for r in rows], _refs(ev, [f"ce:harmonization_contract/{r['artifact']}" for r in rows]), {"candidates": rows}, None


def _facts_no_boundary(ev: ProfileEvidence, limits: list[dict]) -> tuple[list[str], list[str], dict, bool | None]:
    """Sin candidato de armonizacion: que integra varias entradas (vistas con joins / uniones; nodos de producto con mas de una hoja). Es lo que impide justificar «no aplica»."""
    integrating = sorted(n for n, v in ev.views.items() if (v.get("metrics") or {}).get("joins") or (v.get("metrics") or {}).get("unions"))
    nodes = []
    for n in ev.product_nodes:
        eff = ev.effective_providers(n)
        leaves = sorted({x for k, xs in eff.items() if "LEAF" in k or k == "BOUNDARY_LEAF" for x in xs})
        if len(leaves) > 1:
            nodes.append({"node": n, "leaves": leaves})
    return sorted({*integrating, *(x["node"] for x in nodes)}), [], {"views_with_joins_or_unions": integrating, "product_nodes_integrating_several_leaves": nodes}, None


def _facts_published(ev: ProfileEvidence, limits: list[dict]) -> tuple[list[str], list[str], dict, bool | None]:
    return ([ev.am] if ev.am else []), _refs(ev, ["ce:published_contract/fields", "ce:published_field_lineage"]), {}, None


def _facts_none(ev: ProfileEvidence, limits: list[dict]) -> tuple[list[str], list[str], dict, bool | None]:
    return [], [], {}, None


_FACTS = {"HARMONIZATION_NOT_A_LEGITIMATE_REUSABLE_BOUNDARY": _facts_har_candidates, "NO_HARMONIZATION_BOUNDARY_OBSERVED": _facts_no_boundary,
          "UNRESOLVED_VOCABULARY_REACHES_THE_CONSUMER": _facts_published, "NO_AFFIRMATIVE_PUBLISHED_FAMILY": _facts_published, "PUBLISHED_CONTRACT_SUBSTANCE_NOT_ESTABLISHED": _facts_published,
"HARMONIZATION_CANDIDATE_CHARACTER_NOT_DETERMINABLE": _facts_harmonization_character, "HARMONIZATION_MAPPING_NOT_OBSERVABLE": _facts_harmonization_mapping,
          "SOURCE_CONTRACT_CLASS_UNDETERMINED": _facts_source_class, "EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL": _facts_critical_source, "PUBLISHED_LINEAGE_INCOMPLETE": _facts_lineage,
          "PRODUCT_DOES_NOT_COMPOSE_RESOLVED_CONTRACTS": _facts_product}


def _resolution_type(cause: str, facts: Mapping[str, Any]) -> str:
    """Tipo de resolucion. La clase de fuente sin determinar solo es una DECISION DE GOBIERNO si la ratificacion tiene efecto (la fuente es decision-critica); si no, hoy no hay una decision
    que la resuelva (la ratificacion no tiene efecto fuera de LVL-05) y es una revision metodologica."""
    if cause == "SOURCE_CONTRACT_CLASS_UNDETERMINED":
        return GOVERNANCE_DECISION if facts.get("ratification_would_apply") else METHODOLOGICAL_REVIEW
    return _SPEC[cause][1]


def _hint_key(cause: str, facts: Mapping[str, Any]) -> str:
    if cause == "SOURCE_CONTRACT_CLASS_UNDETERMINED" and not facts.get("ratification_would_apply"):
        return "SOURCE_CLASS_RATIFICATION_HAS_NO_EFFECT_OUTSIDE_LVL05"
    return cause


def _blocker(dim: str | None, state: str, rule: str | None, cause: str, ev: ProfileEvidence | None, limits: list[dict], *, unmet: list[str] | None = None, persisted: bool = True,
             extra_ids: dict[str, Any] | None = None) -> dict:
    spec = _SPEC.get(cause)
    artifacts: list[str] = []
    refs: list[str] = []
    facts: dict[str, Any] = {}
    corroborated: bool | None = None
    if ev is not None and cause in _FACTS:
        try:
            artifacts, refs, facts, corroborated = _FACTS[cause](ev, limits)
        except Exception as exc:                                              # noqa: BLE001 — el detalle es complementario: su falla se declara, no rompe el diagnostico
            limits.append({"key": "DETAIL_NOT_RECOVERABLE", "dimension": dim, "detail": type(exc).__name__})
    elif ev is None:
        limits.append({"key": "PROFILE_NOT_READABLE", "dimension": dim, "detail": "sin perfil legible: solo se informa lo registrado en el Juicio"})
    if unmet:
        facts["unmet_conditions"] = sorted(unmet)
    if extra_ids:
        facts.update(extra_ids)
    if spec is None:
        category, rtype, required = UNDETERMINED_CAUSE, METHODOLOGICAL_REVIEW, ()
        limits.append({"key": "CAUSE_NOT_RECOGNIZED", "dimension": dim, "detail": cause})
    else:
        category, required = spec[0], list(spec[2])
        rtype = _resolution_type(cause, facts)
    detailed = bool(artifacts or refs or facts)
    confidence = LOW if (not persisted or spec is None) else (HIGH if (detailed and corroborated is not False) else MEDIUM)
    reason_code = None
    cands = facts.get("candidates") or []
    if cands and cands[0].get("reason_unknown"):
        reason_code = cands[0]["reason_unknown"]
    return {"blocker_id": f"{dim or 'GLOBAL'}:{cause}", "dimension": dim, "state": state, "rule_id": rule, "condition_id": cause, "reason_code": reason_code, "category": category,
            "artifacts": artifacts, "evidence_refs": refs, "facts": facts, "required_evidence": required, "resolution_type": rtype, "resolution_hint_key": _hint_key(cause, facts),
            "confidence": confidence, "corroborated": corroborated}


# ───────────────────────────── API ─────────────────────────────
def explain_determination(judgment: Mapping[str, Any] | None, profile: Mapping[str, Any] | None) -> dict | None:
    """Explicacion estructurada de por que ``judgment`` no llego a un nivel. ``None`` si hay nivel (o no hay Juicio): jamas se inventan impedimentos para un Juicio determinado."""
    if not judgment or (judgment.get("status") == C.DETERMINED and judgment.get("level") is not None):
        return None
    code, rule = judgment.get("not_determinable_reason") or None, judgment.get("level_rule")
    limits: list[dict] = []
    try:
        ev: ProfileEvidence | None = ProfileEvidence(profile) if isinstance(profile, Mapping) else None
    except Exception:                                                         # noqa: BLE001
        ev = None
    blockers: list[dict] = []
    resolved: list[dict] = []
    checks: list[dict] = []
    states = [s for s in judgment.get("dimension_states") or [] if isinstance(s, Mapping)]
    detail = judgment.get("not_determinable_detail") or {}
    missing = [d for d in detail.get("missing_strength_dimensions") or [] if isinstance(d, Mapping)]

    if code in ("ASSESSMENT_NOT_JUDGEABLE", "CANONICAL_EVIDENCE_V3_REQUIRED"):
        blockers.append(_blocker(None, C.DS_NOT_EVALUABLE, rule, code, ev, limits))
    elif code == "CONTRACT_NOT_EVALUABLE":
        for dim in _names(judgment.get("contracts_not_evaluable")):
            areas = {a: ev.area_state(a) for a in C.CONTRACT_AREAS.get(dim, ())} if ev is not None else {}
            blockers.append(_blocker(dim, C.DS_NOT_EVALUABLE, rule, code, ev, limits, extra_ids={"areas": areas, "area_reasons": _area_reasons(ev, dim)}))
        if not blockers:
            blockers.append(_blocker(None, C.DS_NOT_EVALUABLE, rule, code, ev, limits))
            limits.append({"key": "CONTRACTS_NOT_EVALUABLE_NOT_LISTED", "detail": "el Juicio no registró cuáles contratos no pudieron evaluarse"})
        if ev is not None:
            checks.append({"check": "contracts_not_evaluable_recomputed", "ok": sorted(_contracts_not_evaluable(ev)) == _names(judgment.get("contracts_not_evaluable"))})
    elif code == "EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL":
        blockers.append(_blocker("SOURCE_INGESTION", C.DS_NOT_EVALUABLE, rule, code, ev, limits))
    elif code == "INSUFFICIENT_AFFIRMATIVE_EVIDENCE" and states:
        unmet_by_dim = {m.get("dimension"): m.get("unmet_conditions") for m in missing}
        for s in states:
            if s.get("state") in _BLOCKING:
                blockers.append(_blocker(s.get("dimension"), s["state"], rule, s.get("cause") or "NO_AFFIRMATIVE_EVIDENCE", ev, limits, unmet=_names(unmet_by_dim.get(s.get("dimension")))))
            else:
                resolved.append({"dimension": s.get("dimension"), "state": s.get("state"), "basis": _names(s.get("basis"))})
        if not blockers:                                                       # todas resueltas pero ninguna es fortaleza: ancla positiva ausente (LVL-08 exige >= 1)
            blockers.append(_blocker(None, C.DS_UNRESOLVED, rule, "NO_STRENGTH_ANCHOR", ev, limits, extra_ids={"dimension_states": [{"dimension": s.get("dimension"), "state": s.get("state")} for s in states]}))
        checks.append({"check": "verdict_is_a_function_of_the_persisted_dimension_states", "ok": resolve_level_v4([dict(s) for s in states]) is None})
        persisted_cause = {m.get("dimension"): m.get("cause") for m in missing}
        checks.append({"check": "dimension_states_agree_with_not_determinable_detail", "ok": all(persisted_cause.get(b["dimension"]) in (None, b["condition_id"]) for b in blockers if b["dimension"])})
    elif code == "INSUFFICIENT_AFFIRMATIVE_EVIDENCE" and missing:              # Juicio historico (v3): sin ``dimension_states``; el estado se traduce desde ``not_determinable_detail``
        limits.append({"key": "HISTORICAL_JUDGMENT_WITHOUT_DIMENSION_STATES", "detail": "Juicio anterior a judgment-rules/v4: los estados por dimensión se derivan de not_determinable_detail"})
        for m in missing:
            state = _DETAIL_STATE.get(m.get("state"))
            if state is None:
                continue
            blockers.append(_blocker(m.get("dimension"), state, rule, m.get("cause") or "NO_AFFIRMATIVE_EVIDENCE", ev, limits, unmet=_names(m.get("unmet_conditions")), persisted=False))
    if not blockers:                                                           # ninguna causa verificable: se declara, no se inventa
        blockers.append(_blocker(None, C.DS_UNRESOLVED, rule, code or "UNSPECIFIED", ev, limits, persisted=False))
        limits.append({"key": "NO_VERIFIABLE_CAUSE_PERSISTED", "detail": "el Juicio no registró un detalle suficiente para identificar el impedimento"})
    for b in blockers:                                                         # corroboracion: el predicado del motor que produjo la causa, evaluado de nuevo sobre el perfil
        if b["corroborated"] is not None:
            checks.append({"check": f"engine_predicate_confirms:{b['condition_id']}", "ok": bool(b["corroborated"])})
    ok = [c["ok"] for c in checks]
    status = "NOT_CORROBORATED" if ok and not all(ok) else ("CORROBORATED" if ok and all(c["confidence"] != LOW for c in blockers) else "PARTIAL")
    return {"version": EXPLANATION_VERSION, "source": "PROJECTION_OF_PERSISTED_JUDGMENT_AND_PROFILE", "level_rule": rule, "not_determinable_reason": code, "blockers": blockers,
            "resolved_dimensions": resolved, "diagnosis_limits": limits, "corroboration": {"status": status, "checks": checks},
            "categories": sorted({b["category"] for b in blockers}), "blocked_dimensions": [b["dimension"] for b in blockers if b["dimension"]]}


def _area_reasons(ev: ProfileEvidence | None, dim: str) -> dict:
    if ev is None:
        return {}
    out = {}
    for a in C.CONTRACT_AREAS.get(dim, ()):
        area = ev.area(a) or {}
        reason = area.get("reason") if isinstance(area, Mapping) else None
        if isinstance(reason, Mapping) and reason.get("reason_code"):
            out[a] = reason["reason_code"]
    return out
