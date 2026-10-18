"""Motor PURO de Juicio metodologico v1: ``evaluate_judgment(profile, dispositions=None)``.

Sin escrituras (ni disco, ni base, ni perfil), sin red, sin LLM, sin reloj en la salida semantica, sin veredicto legacy, sin puntajes ni pesos. Misma entrada => misma salida.
El perfil se lee como SOLO LECTURA (``ProfileEvidence``) y no se muta.

Logica de decision (cualitativa y existencial, en este orden):
  1. >=1 razon MATERIAL efectiva                     -> DETERMINED / PARCIAL
  2. un contrato critico no evaluable                -> NOT_DETERMINABLE
  3. fuente externa indeterminada DECISION-CRITICA   -> NOT_DETERMINABLE   (clase no resuelta que decide leak vs reuso en el publicado)
  4. >=1 razon RELEVANT efectiva                     -> DETERMINED / ADECUADA
  5. fortaleza afirmativa en los 4 contratos         -> DETERMINED / BUENA
  6. si no                                           -> NOT_DETERMINABLE
PLENA nunca se emite (``PLENA_ASSIGNMENT = DISABLED_UNTIL_CALIBRATED``). El paso 3 nunca degrada un MATERIAL ya establecido (va despues del 1):
si otra regla ratificada determina PARCIAL de forma independiente, la fuente indeterminada no lo convierte en NOT_DETERMINABLE.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Iterable, Mapping

from tools.catalog.methodological_judgment.profile_access import ProfileEvidence

from . import contract as C
from .cta import build_cta_candidates
from .rules import decision_critical_undetermined_source, dimension_resolution, evaluate_rules, published_contract_conformance, resolve_level_exploratory

_STRENGTH_BY_DIM = {
    C.SOURCE_INGESTION: ("SRC-STR-01",),
    C.HARMONIZATION: ("HAR-STR-01", "HAR-STR-02"),
    C.PROPAGATION: ("PRO-STR-01",),
    C.PUBLISHED_CONTRACT: ("PUB-STR-01",),
}
_CLASS_RANK = {C.MATERIAL_DEVIATION: 0, C.RELEVANT_WEAKNESS: 1, C.LOCAL_WEAKNESS: 2, C.QUALIFIER_ONLY: 3, C.POSITIVE_EVIDENCE: 4}


# ───────────────────────────── dispositions ─────────────────────────────
def _validate_disposition(d: Mapping[str, Any], target: Mapping[str, Any] | None, kind: str | None) -> str | None:
    """Devuelve un codigo de rechazo o None. Una disposicion invalida se IGNORA (queda registrada como REJECTED) y nunca altera el juicio."""
    if target is None:
        return "UNKNOWN_TARGET"
    dec = d.get("decision")
    if dec not in C.DISPOSITION_DECISIONS:
        return "UNKNOWN_DECISION"
    if not str(d.get("actor") or "").strip() or not str(d.get("rationale") or "").strip():
        return "ACTOR_AND_RATIONALE_REQUIRED"
    if kind == "review":
        return "REVIEW_DISPOSITIONS_NOT_SUPPORTED_IN_V1" if dec not in (C.NEEDS_CONTEXT, C.ACCEPTED_AS_DESIGNED, C.NOT_APPLICABLE) else None
    cls = target["classification"]
    if dec == C.GOVERNED_EXCEPTION:
        ex = d.get("exception")
        if not isinstance(ex, Mapping) or not all(str(ex.get(k) or "").strip() for k in ("policy_ref", "approver", "version")) or not ex.get("scope"):
            return "INVALID_EXCEPTION_OBJECT"
        if not d.get("evidence_refs") or ex.get("approver") == d.get("actor"):
            return "INVALID_EXCEPTION_OBJECT"
        if cls not in (C.MATERIAL_DEVIATION, C.RELEVANT_WEAKNESS):
            return "CLASS_NOT_ALLOWED"
    elif dec == C.NOT_APPLICABLE:
        if not str(d.get("justification") or "").strip() or not d.get("evidence_refs"):
            return "JUSTIFICATION_AND_EVIDENCE_REQUIRED"
    elif dec == C.CONFIRMED_DEVIATION:
        if target["disposition"]["max_class_if_confirmed"] != C.MATERIAL_DEVIATION or cls != C.MATERIAL_DEVIATION:
            return "CLASS_NOT_ALLOWED"
    elif dec == C.CONFIRMED_WEAKNESS:
        if cls != C.RELEVANT_WEAKNESS:
            return "CLASS_NOT_ALLOWED"                                  # nunca baja un MATERIAL a RELEVANT: eso seria aritmetica de severidad
    if not target["disposition"]["allowed"] and dec not in (C.NEEDS_CONTEXT, C.ACCEPTED_AS_DESIGNED):
        return "DISPOSITION_NOT_ALLOWED_FOR_RULE"
    return None


def _apply_dispositions(reasons: list[dict], reviews: list[dict], dispositions: Iterable[Mapping[str, Any]] | None) -> list[dict]:
    by_reason = {r["reason_id"]: r for r in reasons}
    by_review = {r["review_id"]: r for r in reviews}
    applied: list[dict] = []
    for d in dispositions or ():
        tid = d.get("target")
        rtarget, vtarget = by_reason.get(tid), by_review.get(tid)
        target, kind = (rtarget, "reason") if rtarget is not None else ((vtarget, "review") if vtarget is not None else (None, None))
        code = _validate_disposition(d, target, kind)
        entry = {"target": tid, "decision": d.get("decision"), "actor": d.get("actor"), "status": "APPLIED" if code is None else "REJECTED"}
        if code is not None:
            entry["reject_code"] = code
        else:
            if kind == "reason":
                target["state"] = d["decision"]
                if d["decision"] == C.GOVERNED_EXCEPTION:
                    ex = d["exception"]
                    entry["exception"] = {"policy_ref": ex["policy_ref"], "approver": ex["approver"], "scope": sorted(ex["scope"]), "version": ex["version"]}
                target["disposition"]["resolved_by"] = d["decision"]
            else:
                target["state"] = d["decision"]
        applied.append(entry)
    applied.sort(key=lambda e: (str(e["target"]), str(e["decision"]), str(e["actor"])))
    return applied


# ───────────────────────────── decision ─────────────────────────────
def _contracts_not_evaluable(ev: ProfileEvidence) -> list[str]:
    out = []
    for dim, areas in C.CONTRACT_AREAS.items():
        if not all(ev.area_state(a) in C.EVALUABLE_AREA_STATES for a in areas):
            out.append(dim)
    return out


def _decide(reasons: list[dict], strengths: list[dict], not_evaluable: list[str], ignore_exceptions: bool = False,
            critical_undetermined: bool = False) -> tuple[str, str | None, str, str | None]:
    """(status, level, level_rule, nd_reason). Funcion de la EXISTENCIA de razones clasificadas; sin conteos ni pesos."""
    removing = () if ignore_exceptions else C.REMOVING_STATES
    live = [r for r in reasons if r["state"] not in removing]
    if any(r["classification"] == C.MATERIAL_DEVIATION for r in live):
        return C.DETERMINED, C.PARCIAL, "LVL-02", None
    if not_evaluable:
        return C.NOT_DETERMINABLE, None, "LVL-01", "CONTRACT_NOT_EVALUABLE"
    if critical_undetermined:
        # La calidad del contrato publicado para campos que heredan vocabulario verbatim de una fuente de clase indeterminada no puede
        # afirmarse hasta que esa clase se resuelva (decide leak vs reuso). No se adivina por tecnologia: NOT_DETERMINABLE decision-critico.
        return C.NOT_DETERMINABLE, None, "LVL-05", "EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL"
    if any(r["classification"] == C.RELEVANT_WEAKNESS for r in live):
        return C.DETERMINED, C.ADECUADA, "LVL-03", None
    have = {s["rule_id"] for s in strengths}
    if all(any(rid in have for rid in ids) for ids in _STRENGTH_BY_DIM.values()):
        return C.DETERMINED, C.BUENA, "LVL-04", None
    return C.NOT_DETERMINABLE, None, "LVL-06", "INSUFFICIENT_AFFIRMATIVE_EVIDENCE"


def _closure(status: str, level: str | None, reasons: list[dict], limitations: list[dict]) -> str:
    """FINAL iff nada sin resolver puede cambiar el nivel efectivo; PROVISIONAL si al menos un item de disposicion/contexto abierto podria cambiarlo.
    Las revisiones y calificadores abiertos (naming, mantenibilidad, foco, predicados no evaluados) NO fuerzan PROVISIONAL."""
    if status != C.DETERMINED:
        return C.PROVISIONAL
    if level == C.BUENA:
        return C.PROVISIONAL if any(l["can_change_level"] for l in limitations) else C.FINAL
    cls = C.MATERIAL_DEVIATION if level == C.PARCIAL else C.RELEVANT_WEAKNESS
    sustaining = [r for r in reasons if r["classification"] == cls and r["state"] not in C.REMOVING_STATES]
    terminal = [r for r in sustaining if r["state"] in (C.CONFIRMED_DEVIATION, C.CONFIRMED_WEAKNESS, C.ACCEPTED_AS_DESIGNED)]
    return C.FINAL if terminal else C.PROVISIONAL


# Estados de una dimensión SIN fortaleza en LVL-06: ``NO_STRENGTH`` = lo observable se evaluó y no hay evidencia afirmativa suficiente (ausencia de fortaleza, no una deficiencia);
# ``INSUFFICIENT_EVIDENCE`` = falta evidencia para poder afirmar (clase de fuente sin determinar, linaje publicado incompleto, mapeo no observable).
NO_STRENGTH, INSUFFICIENT_EVIDENCE = "NO_STRENGTH", "INSUFFICIENT_EVIDENCE"


def _missing_strength_dimensions(ev: ProfileEvidence, strengths: list[dict], policy: str) -> list[dict]:
    """Dimensiones sin fortaleza afirmativa, cada una con su ``state`` y su ``cause`` estable. Solo lee hechos canónicos ya usados por las reglas; no inventa evidencia."""
    have = {s["rule_id"] for s in strengths}
    out = []
    for dim, ids in _STRENGTH_BY_DIM.items():
        if any(i in have for i in ids):
            continue
        state, cause, unmet = NO_STRENGTH, "NO_AFFIRMATIVE_EVIDENCE", []
        if dim == C.SOURCE_INGESTION:
            classes = {ev.classify_source(l, policy) for l in ev.source_contact_leaves}
            if not ev.source_contact_leaves:
                cause = "NO_SOURCE_CONTACT_OBSERVED"
            elif "EXTERNAL_CONTRACT_UNDETERMINED" in classes:
                state, cause = INSUFFICIENT_EVIDENCE, "SOURCE_CONTRACT_CLASS_UNDETERMINED"
            else:
                cause = "SOURCE_CONTACT_NOT_ALL_GOVERNED"
        elif dim == C.HARMONIZATION:
            if not ev.harmonization:
                cause = "NO_HARMONIZATION_BOUNDARY_OBSERVED"
            elif any(c.get("mapping_determinable") is False for c in ev.harmonization.values()):
                state, cause = INSUFFICIENT_EVIDENCE, "HARMONIZATION_MAPPING_NOT_OBSERVABLE"
            else:
                cause = "HARMONIZATION_NOT_A_LEGITIMATE_REUSABLE_BOUNDARY"
        elif dim == C.PROPAGATION:
            cause = "NO_PRODUCT_NODE_OBSERVED" if not ev.product_nodes else "PRODUCT_DOES_NOT_COMPOSE_RESOLVED_CONTRACTS"
        elif dim == C.PUBLISHED_CONTRACT:
            conds = published_contract_conformance(ev, policy)["conditions"]
            unmet = sorted(k for k, v in conds.items() if not v)
            if not conds["lineage_complete"]:
                state, cause = INSUFFICIENT_EVIDENCE, "PUBLISHED_LINEAGE_INCOMPLETE"
            elif not conds["no_vocabulary_leak"]:
                cause = "UNRESOLVED_VOCABULARY_REACHES_THE_CONSUMER"
            elif not conds["field_level_affirmative_family"]:
                cause = "NO_AFFIRMATIVE_PUBLISHED_FAMILY"
            else:
                cause = "PUBLISHED_CONTRACT_SUBSTANCE_NOT_ESTABLISHED"
        item = {"dimension": dim, "state": state, "cause": cause}
        if unmet:
            item["unmet_conditions"] = unmet
        out.append(item)
    return out


def _what_would_change(status: str, level: str | None, reasons: list[dict], strengths: list[dict], not_evaluable: list[str],
                       critical_undetermined: bool = False, missing_dims: list[dict] | None = None) -> list[dict]:
    out: list[dict] = []
    if status == C.DETERMINED and level in (C.PARCIAL, C.ADECUADA):
        cls = C.MATERIAL_DEVIATION if level == C.PARCIAL else C.RELEVANT_WEAKNESS
        sustaining = [r for r in reasons if r["classification"] == cls and r["state"] not in C.REMOVING_STATES]
        remaining = [r for r in reasons if r not in sustaining]
        st2, lv2, _, nd = _decide(remaining, strengths, not_evaluable, critical_undetermined=critical_undetermined)
        out.append({"change_key": "ALL_SUSTAINING_REASONS_REMOVED_BY_VALID_EXCEPTION_OR_NOT_APPLICABLE", "reason_ids": sorted(r["reason_id"] for r in sustaining),
                    "resulting_status": st2, "resulting_level": lv2, "note_key": "removal_requires_governed_exception_or_justified_not_applicable_never_intent_alone"})
        out.append({"change_key": "ACCEPTED_AS_DESIGNED_DOES_NOT_CHANGE_LEVEL", "reason_ids": sorted(r["reason_id"] for r in sustaining), "resulting_status": status, "resulting_level": level})
    elif status == C.DETERMINED and level == C.BUENA:
        out.append({"change_key": "NEW_EVIDENCE_OF_A_RELEVANT_OR_MATERIAL_REASON_WOULD_LOWER_THE_LEVEL", "reason_ids": [], "resulting_status": C.DETERMINED, "resulting_level": None})
    elif critical_undetermined and not not_evaluable:
        out.append({"change_key": "RESOLVE_THE_UNDETERMINED_EXTERNAL_SOURCE_CONTRACT_CLASSIFICATION", "reason_ids": [],
                    "note_key": "classifying_the_source_as_raw_or_governed_removes_the_decision_critical_gap_technology_alone_does_not",
                    "resulting_status": None, "resulting_level": None})
    else:
        entry = {"change_key": "ESTABLISH_EVIDENCE_FOR_UNEVALUABLE_CONTRACTS_OR_AFFIRMATIVE_STRENGTHS", "reason_ids": [], "contracts_not_evaluable": sorted(not_evaluable),
                 "resulting_status": None, "resulting_level": None}
        if missing_dims:
            entry["missing_strength_dimensions"] = [d["dimension"] for d in missing_dims]
        out.append(entry)
    return out


def _unjudgeable(ev: ProfileEvidence, code: str, ruleset: str = C.RULESET_V2) -> dict:
    out = {
        "judgment_schema_version": C.JUDGMENT_SCHEMA_VERSION, "ruleset_version": ruleset, "status": C.NOT_DETERMINABLE, "level": None, "closure": C.PROVISIONAL,
        "plena_assignment": C.PLENA_ASSIGNMENT, "plena_state": C.PLENA_STATE, "not_determinable_reason": code,
        "source_assessment": _source_assessment(ev), "reasons": [], "strengths": [], "qualifiers": {}, "review_candidates": [], "evidence_limitations": [],
        "dispositions_applied": [], "level_without_exceptions": None, "level_rule": None, "supporting_evidence": {}, "cta_candidates": [],
        "what_would_change_the_judgment": [{"change_key": "PROVIDE_A_CURRENT_ASSESSMENT_WITH_CANONICAL_EVIDENCE_V3", "reason_ids": [], "resulting_status": None, "resulting_level": None}],
        "what_would_improve_the_solution": [],
    }
    out["output_signature"] = C.signature(out)
    return out


def _source_assessment(ev: ProfileEvidence) -> dict:
    return {"terminal_analytic_model": ev.am, "input_pool_signature": ev.scope.get("pool_signature"), "assessment_state": ev.assessment_state,
            "assessment_versions": dict(sorted((ev.versions or {}).items())), "profile_signature": C.signature(ev.profile)}


# ───────────────────────────── ratificaciones de gobierno (B5, SIMULACION opt-in) ─────────────────────────────
_UNDETERMINED_CLASS = "EXTERNAL_CONTRACT_UNDETERMINED"


def _resolve_ratifications(ev: ProfileEvidence, ratified_contracts: Iterable[Mapping[str, Any]] | None, policy: str) -> tuple[frozenset, list[dict]]:
    """Valida CONTRA EL PERFIL cada ratificacion ya resuelta por el llamador (identidad + vigencia + digest MATCH se comprueban FUERA del motor: este solo lee la evidencia).
    Una ratificacion solo resuelve la indeterminacion contractual decision-critica (LVL-05) de una hoja ``REMOTE_IMPORTED`` cuya clase explicita es ``EXTERNAL_CONTRACT_UNDETERMINED`` y cuya identidad
    (remote_connection, remote_entity) coincide EXACTAMENTE con la del evento. Una hoja de clase fisica cruda / gobernada / desconocida / de plataforma NO es elegible: la ratificacion humana no
    reclasifica evidencia positiva. Devuelve (hojas resueltas, registros auditables APPLIED/REJECTED/NO_EFFECT)."""
    critical = decision_critical_undetermined_source(ev, policy)
    records: list[dict] = []
    applied: set[str] = set()
    for item in sorted((i for i in (ratified_contracts or ()) if isinstance(i, Mapping)), key=lambda i: (str(i.get("leaf")), str(i.get("event_id")))):
        leaf, ident, event_id, digest, eref = item.get("leaf"), item.get("identity"), item.get("event_id"), item.get("digest"), item.get("evidence_ref")
        # Trazabilidad al EVENTO concreto (no solo al subject CDS/API): event_id + la referencia de evidencia con la que se ratifico (algo, campos cubiertos y digest) + technical_id del contrato.
        rec = {"target": f"source:{leaf}", "decision": C.DISPOSITION_GOVERNED_CONTRACT_RATIFIED, "scope": C.RATIFICATION_SCOPE, "leaf": leaf, "identity": ident,
               "technical_id": item.get("technical_id"), "event_id": event_id, "digest": digest, "evidence_ref": dict(eref) if isinstance(eref, Mapping) else eref}
        code = None
        if not (isinstance(leaf, str) and leaf and isinstance(ident, Mapping) and isinstance(event_id, str) and event_id.strip() and isinstance(digest, str) and len(digest) == 64
                and isinstance(eref, Mapping) and eref.get("algo") == "sha256" and eref.get("digest") == digest and eref.get("fields")):
            code = "INVALID_RATIFICATION"
        else:
            lr = ev.leaf_class_records.get(leaf)
            if lr is None:
                code = "UNKNOWN_LEAF"
            elif lr.get("leaf_kind") != "REMOTE_IMPORTED" or lr.get("contract_class") != _UNDETERMINED_CLASS or ev.classify_source(leaf, policy) != _UNDETERMINED_CLASS:
                code = "SOURCE_NOT_ELIGIBLE"
            else:
                ents = [e for e in (lr.get("remote_entities") or ()) if isinstance(e, str) and e.strip()]
                if len(ents) != 1 or ident.get("remote_entity") != ents[0] or ident.get("remote_connection") != lr.get("remote_connection"):
                    code = "IDENTITY_MISMATCH"
                elif leaf in applied:
                    code = "DUPLICATE_LEAF"
        if code is not None:
            rec.update(status="REJECTED", reject_code=code)
        elif leaf not in critical:
            rec.update(status="NO_EFFECT", reject_code="NO_DECISION_CRITICAL_EXPOSURE")      # valida, pero esta hoja no era la causa de LVL-05: nada que resolver
        else:
            rec.update(status="APPLIED")
            applied.add(leaf)
        records.append(rec)
    return frozenset(applied), records


_INPUT_KEYS = ("leaf", "identity", "technical_id", "event_id", "digest", "evidence_ref")


def decision_inputs(ratified_contracts: Iterable[Mapping[str, Any]] | None) -> dict:
    """Entrada de decisiones de gobierno de un Juicio v3: cada ratificacion INYECTADA (no solo la aplicada) con sus identificadores (hoja, identidad, technical_id, event_id) y su referencia de evidencia
    (algo/campos/digest), normalizada y ordenada, mas su firma. Es el insumo EXACTO para reproducir el Juicio (``replay``) y para detectar invalidacion (un evento revocado/obsoleto ya no se inyecta)."""
    items = []
    for i in ratified_contracts or ():
        if isinstance(i, Mapping):
            items.append({k: copy.deepcopy(i.get(k)) for k in _INPUT_KEYS})
    items.sort(key=lambda r: (str(r["leaf"]), str(r["event_id"])))
    return {"version": C.DECISION_INPUTS_VERSION, "policy": C.DISPOSITION_GOVERNED_CONTRACT_RATIFIED, "scope": C.RATIFICATION_SCOPE, "ratified_contracts": items,
            "signature": C.signature({"policy": C.DISPOSITION_GOVERNED_CONTRACT_RATIFIED, "ratified_contracts": items})}


# ───────────────────────────── API ─────────────────────────────
def _materialization_digest(ev: ProfileEvidence) -> str:
    """Huella de la materializacion que el motor CONSUMIO (la evidencia CSN explicita cambia el resultado y por tanto la firma de salida)."""
    slim = {leaf: [m["materialization_kind"], m["derived_boundary_class"], m["flags"], m["unknown_reasons"], sorted(p["resolved_via"] or "" for p in m["producers"]), m["mapping_profile"]]
            for leaf, m in sorted(ev.materialization.items())}
    return hashlib.sha256(json.dumps(slim, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _materialization_options(ev: ProfileEvidence, materialization_csn: Mapping[str, Any] | None) -> dict:
    """``ruleset_options.materialization``: de DONDE salio la materializacion que el motor consumio. En un perfil v4 con la seccion persistida es la EVIDENCIA PERSISTIDA (fuente unica; un CSN de
    ejecucion se ignora y no se declara); en un perfil historico es la derivacion experimental sobre la evidencia persistida, con el CSN explicito del llamador si lo hubo."""
    view = ev.persisted_materialization
    persisted = bool(view["persisted"] and view["state"] == "EVALUATED")
    return {"version": "leaf-materialization/v1", "source": "PERSISTED_SECTION" if persisted else "DERIVED_ON_READ",
            "csn_evidence_provided": bool(view["csn_provided"]) if persisted else bool(materialization_csn), "digest": _materialization_digest(ev)}


def evaluate_judgment(profile: Mapping[str, Any], dispositions: Iterable[Mapping[str, Any]] | None = None, *, external_policy: str = "UNDETERMINED",
                      variants: Iterable[str] = (), materialization_csn: Mapping[str, Any] | None = None,
                      ratified_contracts: Iterable[Mapping[str, Any]] | None = None, ruleset: str = C.RULESET_V2) -> dict:
    """Juicio determinista sobre un perfil de Assessment YA persistido/generado (solo lectura). ``external_policy`` existe SOLO para pruebas de sensibilidad
    (``UNDETERMINED`` es la politica vigente); no forma parte del contrato de produccion."""
    variants = frozenset(variants or ())
    if not variants <= C.VARIANTS:
        raise ValueError(f"variantes desconocidas: {sorted(variants - C.VARIANTS)}")
    if ruleset not in C.RULESETS:
        raise ValueError(f"ruleset desconocido: {ruleset!r} (permitidos: {list(C.RULESETS)})")
    v3 = ruleset == C.RULESET_V3
    if v3 and variants:
        raise ValueError("judgment-rules/v3 no admite variantes experimentales (solo la politica de ratificacion de gobierno, que es parte del ruleset)")
    ratifying = v3 or C.VARIANT_GOVERNED_CONTRACT_RATIFIED in variants
    if ratified_contracts is not None and not ratifying:
        raise ValueError("ratified_contracts exige la variante GOVERNED_CONTRACT_RATIFIED (opt-in explicito) o ruleset judgment-rules/v3")
    materialized = C.VARIANT_MATERIALIZED_INPUT in variants
    ev = ProfileEvidence(profile, materialization_csn if materialized else None)      # el CSN es una entrada EXPLICITA: sin el modificador no se usa; sin CSN las relaciones no verificables quedan UNKNOWN
    if ev.assessment_state != "ASSESSED":
        return _unjudgeable(ev, "ASSESSMENT_NOT_JUDGEABLE", ruleset)
    if not ev.has_canonical_evidence_v3:
        return _unjudgeable(ev, "CANONICAL_EVIDENCE_V3_REQUIRED", ruleset)
    ratified_leaves, ratification_records = _resolve_ratifications(ev, ratified_contracts, external_policy) if ratifying else (frozenset(), [])
    res = evaluate_rules(ev, external_policy, variants, ratified_leaves)
    reasons = copy.deepcopy(res["reasons"])
    reviews = copy.deepcopy(res["review_candidates"])
    strengths = res["strengths"]
    limitations = res["evidence_limitations"]
    not_eval = _contracts_not_evaluable(ev)
    # Fuente externa indeterminada DECISION-CRITICA: hoja EXTERNAL_CONTRACT_UNDETERMINED cuyo vocabulario llega verbatim al publicado (ver
    # rules.decision_critical_undetermined_source). No degrada un MATERIAL ya establecido (el paso 1 de _decide gana).
    critical = bool(res.get("critical_source_undetermined"))
    applied = _apply_dispositions(reasons, reviews, dispositions) + ratification_records

    status, level, level_rule, nd_reason = _decide(reasons, strengths, not_eval, critical_undetermined=critical)
    _, level_no_exc, _, _ = _decide(reasons, strengths, not_eval, ignore_exceptions=True, critical_undetermined=critical)
    dim_states = None
    if C.VARIANT_LEVEL_RESOLUTION in variants and level_rule == "LVL-06":              # EXPLORATORIA: solo reinterpreta los LVL-06; el resto de la logica de decision queda intacta
        pre_missing = _missing_strength_dimensions(ev, strengths, external_policy)
        dim_states = dimension_resolution(ev, external_policy, strengths, reasons, pre_missing, materialized)
        resolved = resolve_level_exploratory(dim_states)
        if resolved is not None:
            status, (level, level_rule), nd_reason = C.DETERMINED, resolved, None
            level_no_exc = level
    closure = _closure(status, level, reasons, limitations)
    reasons.sort(key=lambda r: (_CLASS_RANK[r["classification"]], r["rule_id"], r["reason_id"]))
    reviews.sort(key=lambda r: (r["rule_id"], r["review_id"]))
    cta = build_cta_candidates(reasons, reviews, res["qualifiers"], limitations, res["supporting_evidence"])
    missing_dims = _missing_strength_dimensions(ev, strengths, external_policy) if level_rule == "LVL-06" else None
    out = {
        "judgment_schema_version": C.JUDGMENT_SCHEMA_VERSION, "ruleset_version": ruleset, "status": status, "level": level, "closure": closure,
        "plena_assignment": C.PLENA_ASSIGNMENT, "plena_state": C.PLENA_STATE, "level_rule": level_rule,
        "source_assessment": _source_assessment(ev),
        "reasons": reasons, "strengths": strengths, "qualifiers": res["qualifiers"], "review_candidates": reviews, "evidence_limitations": limitations,
        "supporting_evidence": res["supporting_evidence"], "dispositions_applied": applied, "level_without_exceptions": level_no_exc,
        "cta_candidates": cta,
        "what_would_change_the_judgment": _what_would_change(status, level, reasons, strengths, not_eval, critical, missing_dims),
        "what_would_improve_the_solution": [{"cta_id": c["cta_id"], "priority_class": c["priority_class"], "linked_reason_ids": c["linked_reason_ids"], "applicability_state": c["applicability_state"]}
                                            for c in cta if c["applicability_state"] != "DISABLED_PENDING_EVIDENCE"],
        "contracts_not_evaluable": sorted(not_eval),
    }
    if v3:
        out["decision_inputs"] = decision_inputs(ratified_contracts)       # ENTRADA verificable del Juicio (entra en la firma de salida): que decisiones se inyectaron, por evento y evidencia
    if nd_reason:
        out["not_determinable_reason"] = nd_reason
    if missing_dims is not None:                           # LVL-06: distingue ausencia de fortalezas (evidencia evaluada) de insuficiencia de evidencia; aditivo, no cambia el nivel
        out["not_determinable_detail"] = {"level_rule": "LVL-06", "missing_strength_dimensions": missing_dims,
                                          "evidence_sufficient": not any(d["state"] == INSUFFICIENT_EVIDENCE for d in missing_dims)}
    if dim_states is not None:
        out["dimension_states"] = dim_states
    if external_policy != "UNDETERMINED" or variants:
        out["ruleset_options"] = {**({"external_policy": external_policy} if external_policy != "UNDETERMINED" else {}), **({"variants": sorted(variants)} if variants else {}),
                                  **({"materialization": _materialization_options(ev, materialization_csn)}
                                     if materialized and variants & {C.VARIANT_T5_LOCAL_PHYSICAL, C.VARIANT_LEVEL_RESOLUTION} else {}),
                                  **({"ratified_contracts": {"scope": C.RATIFICATION_SCOPE, "resolved_leaves": sorted(ratified_leaves), "promotes_strengths": False}} if ratifying else {})}
    out["output_signature"] = C.signature(out)
    return out
