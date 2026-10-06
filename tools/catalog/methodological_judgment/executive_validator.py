"""Validador del CONTRATO de salida de un futuro Asesor LLM (``executive-output-contract/v1``). NO llama a ningun modelo y esta AISLADO del motor de juicio.

Una narrativa se RECHAZA (no se persiste como confiable) si: cambia/inventa el nivel, referencia artefactos o evidence_refs desconocidos, afirma un estado de fuente/carga sin
evidencia, infiere FULL/DELTA/federado/replicado, sostiene "SQL bueno / grafico malo", propone una CTA fuera del playbook gobernado, trata una pregunta abierta como defecto
confirmado, inventa PLENA o introduce hechos/cifras que no estan en la entrada ejecutiva."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

from . import contract as C

REQUIRED_KEYS = ("executive_summary", "why_this_level", "strengths", "improvement_actions", "questions_for_project", "maintainability_opportunities",
                 "what_would_improve_the_solution", "evidence_limitations")
_LOAD_TERMS = re.compile(r"\b(full|delta|incremental(?:es)?|federad[oa]s?|federated|replicad[oa]s?|replicated|carga completa|carga incremental|full load)\b", re.I)
_SQL_GOOD = re.compile(r"\bsql\b[^.]{0,60}\b(mejor|bueno|buena|superior|preferible|better|good|preferred)\b|\b(mejor|better)\b[^.]{0,30}\bsql\b", re.I)
_GRAPH_BAD = re.compile(r"\bgr[aá]fic[oa]s?\b[^.]{0,60}\b(malo|mala|malas|malos|peor|inferior|evitar|bad|worse|avoid)\b|\b(evitar|avoid)\b[^.]{0,30}\bgr[aá]fic", re.I)
_DEFECT = re.compile(r"\b(defecto|defectuos[oa]|incumplimiento|defect|non-?compliance)\b", re.I)
_GOVERNED_OR_RAW = re.compile(r"gobernad|governed|cruda|\braw\b|f[ií]sic[ao]|physical", re.I)
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w])")
_BACKTICK = re.compile(r"`([^`]+)`")
_PLENA = re.compile(r"\bplena\b", re.I)
_STRUCTURAL_PATH = re.compile(r"\.(cta_id|action_key|priority|level_echo|status_echo|closure_echo|question_id)$|\.(evidence_refs|reason_ids|affected_artifacts|action_keys)\[\d+\]$")
PRIORITIES = C.CTA_PRIORITIES[:4]                                         # MATERIAL | RELEVANT | LOCAL | MAINTAINABILITY


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[Mapping[str, str], ...]

    def codes(self) -> tuple[str, ...]:
        return tuple(sorted({e["code"] for e in self.errors}))


def _strings(o: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(o, str):
        yield path, o
    elif isinstance(o, Mapping):
        for k, v in o.items():
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(o, (list, tuple)):
        for i, v in enumerate(o):
            yield from _strings(v, f"{path}[{i}]")


def _known_numbers(ex: Mapping[str, Any]) -> set[str]:
    return {m.group(0).replace(",", ".") for m in _NUMBER.finditer(C.canonical_json(ex))} | {m.group(0).replace(",", ".") for _, s in _strings(ex) for m in _NUMBER.finditer(s)}


def validate_executive_output(output: Mapping[str, Any], executive_input: Mapping[str, Any]) -> ValidationResult:
    errs: list[dict] = []

    def err(code: str, path: str, detail: str = "") -> None:
        errs.append({"code": code, "path": path, "detail": detail})

    if not isinstance(output, Mapping):
        return ValidationResult(False, ({"code": "SCHEMA", "path": "$", "detail": "output must be an object"},))
    for k in REQUIRED_KEYS:
        if k not in output:
            err("SCHEMA", f"$.{k}", "missing required key")
    if any(k in output for k in ("level", "status", "closure")):
        err("UNAUTHORIZED_LEVEL_FIELD", "$", "the model must not emit its own level/status/closure; only *_echo fields inside executive_summary")
    ij = executive_input.get("immutable_judgment") or {}
    es = output.get("executive_summary") or {}
    if isinstance(es, Mapping):
        for key, exp, code in (("level_echo", ij.get("level"), "LEVEL_MISMATCH"), ("status_echo", ij.get("status"), "STATUS_MISMATCH"), ("closure_echo", ij.get("closure"), "CLOSURE_MISMATCH")):
            if es.get(key) != exp:
                err(code, f"$.executive_summary.{key}", f"expected {exp!r}")
    known_art = set(executive_input.get("known_artifacts") or ())
    known_refs = set(executive_input.get("evidence_refs") or ())
    known_reasons = set(executive_input.get("known_reason_ids") or ())
    known_reviews = set(executive_input.get("known_review_ids") or ())
    known_cta = set(executive_input.get("known_cta_ids") or ())
    open_q = {q["question_id"] for q in executive_input.get("open_questions") or ()}
    known_tokens = known_art | known_refs | known_reasons | known_reviews | known_cta | open_q
    allowed_terms = set((executive_input.get("observed_load_properties") or {}).get("allowed_terms") or ())
    ext_undet = set(executive_input.get("external_undetermined_artifacts") or ())

    for section in ("why_this_level", "strengths", "improvement_actions", "maintainability_opportunities", "what_would_improve_the_solution", "evidence_limitations"):
        for i, item in enumerate(output.get(section) or ()):
            p = f"$.{section}[{i}]"
            if not isinstance(item, Mapping):
                err("SCHEMA", p, "item must be an object")
                continue
            for a in item.get("affected_artifacts") or ():
                if a not in known_art:
                    err("UNKNOWN_ARTIFACT", p, str(a))
            for r in item.get("evidence_refs") or ():
                if r not in known_refs:
                    err("UNKNOWN_EVIDENCE_REF", p, str(r))
            for rid in item.get("reason_ids") or ():
                if rid not in known_reasons:
                    err("OPEN_QUESTION_AS_DEFECT" if (rid in open_q or rid in known_reviews) else "UNKNOWN_REASON_ID", p, str(rid))
            if section in ("why_this_level", "strengths", "improvement_actions") and not item.get("evidence_refs"):
                err("UNSUPPORTED_FACT", p, "every factual statement must carry at least one evidence_ref")
    for section in ("improvement_actions", "maintainability_opportunities", "what_would_improve_the_solution"):
        for i, a in enumerate(output.get(section) or ()):
            if isinstance(a, Mapping) and a.get("cta_id") not in known_cta:
                err("CTA_OUTSIDE_PLAYBOOK", f"$.{section}[{i}]", str(a.get("cta_id")))
    for i, a in enumerate(output.get("improvement_actions") or ()):
        if isinstance(a, Mapping) and a.get("priority") not in PRIORITIES:
            err("PRIORITY_INVALID", f"$.improvement_actions[{i}]", str(a.get("priority")))
    known_nums = _known_numbers(executive_input)
    for path, text in _strings(output):
        if _STRUCTURAL_PATH.search(path):
            continue
        for m in _LOAD_TERMS.finditer(text):
            if m.group(0).lower() not in allowed_terms:
                err("UNSUPPORTED_LOAD_STATE", path, m.group(0))
        if _SQL_GOOD.search(text) or _GRAPH_BAD.search(text):
            err("SQL_GRAPHICAL_DOGMA", path, text[:80])
        if _PLENA.search(text):
            err("PLENA_INVENTED", path, text[:80])
        for tok in _BACKTICK.findall(text):
            if tok not in known_tokens:
                err("UNKNOWN_ARTIFACT", path, tok)
        if _DEFECT.search(text) and any(q in text for q in open_q):
            err("OPEN_QUESTION_AS_DEFECT", path, text[:80])
        if _GOVERNED_OR_RAW.search(text) and any(a in text for a in ext_undet):
            err("UNSUPPORTED_SOURCE_STATE", path, "external source contract is undetermined")
        for n in _NUMBER.findall(text):
            if n.replace(",", ".") not in known_nums and not re.match(r"^\s*\d+[.)]\s", text):
                err("UNSUPPORTED_NUMBER", path, n)
    return ValidationResult(valid=not errs, errors=tuple(errs))
