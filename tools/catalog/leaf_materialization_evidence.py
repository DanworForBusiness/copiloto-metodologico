"""Evidencia PERSISTIDA de la materializacion de hojas locales (``canonical-evidence/v4``, seccion ``leaf_materialization``): armado, lectura historica y auditoria. MODULO NEUTRO (hermano de
``leaf_materialization``; no importa el paquete de Juicio ni el de Assessment): lo importan el contrato (Assessment) y el acceso de solo lectura (Juicio).

Principios (v4.A):
  * la LOGICA vive solo en ``leaf_materialization.derive_all`` (fuente unica): aqui no hay reglas de clasificacion, solo se PERSISTE su resultado y las pruebas tecnicas minimas que lo sustentan;
  * NO se persiste el CSN: solo el veredicto derivado y, por cada veredicto que dependio del CSN, una REFERENCIA RECUPERABLE (archivo del pool relativo + puntero + hash del fragmento + hash del
    archivo + firma del pool). El hash solo NO reemplaza a la referencia: si la fuente ya no se puede recuperar, ``audit_section`` lo informa (``SOURCE_NOT_RECOVERABLE``), nunca lo da por bueno;
  * un perfil historico SIN la seccion se lee como evidencia NO EVALUADA (``SECTION_NOT_PERSISTED``), jamas como ausencia de problemas;
  * es evidencia, no juicio: ningun campo habla de calidad ni de nivel."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping

from . import leaf_materialization as LM

SECTION_KEY = "leaf_materialization"
EVIDENCE_VERSION = "leaf-materialization-evidence/v1"
PERSISTED_SECTIONS = ("leaf_provenance", "processing_flow_properties", "physical_source_boundaries")      # evidencia ya persistida que alimenta la derivacion
DERIVATION_MODULE = "tools.catalog.leaf_materialization"

EVALUATED, NOT_EVALUATED = "EVALUATED", "NOT_EVALUATED"
EVALUATION_STATES = (EVALUATED, NOT_EVALUATED)
NE_SOURCE_SECTIONS_MISSING = "SOURCE_SECTIONS_MISSING"

# grado de verificabilidad de cada clasificacion
VER_FROM_PROFILE, VER_BY_CSN, VER_UNVERIFIABLE, VER_NOT_EVALUATED = "VERIFIED_FROM_PROFILE", "VERIFIED_BY_CSN_REFERENCES", "UNVERIFIABLE", "NOT_EVALUATED"
VERIFIABILITY_GRADES = (VER_FROM_PROFILE, VER_BY_CSN, VER_UNVERIFIABLE, VER_NOT_EVALUATED)
_NOT_EVALUATED_CODES = frozenset({LM.U_NO_PRODUCER, LM.U_DECLARED_NOT_OBSERVED, LM.U_SECTION_MISSING, LM.U_EVIDENCE_NOT_AVAILABLE, LM.U_LEAF_NOT_FOUND, LM.U_SECTION_NOT_PERSISTED})

# limitaciones de la seccion (informadas, nunca silenciadas)
LIM_CSN_NOT_PROVIDED = "CSN_EVIDENCE_NOT_PROVIDED"           # el pool no aporto definiciones tecnicas: lo que depende de ellas queda UNKNOWN
LIM_REFERENCE_UNAVAILABLE = "SOURCE_REFERENCE_UNAVAILABLE"   # un veredicto usa evidencia tecnica cuya referencia recuperable no pudo registrarse
LIMITATION_CODES = frozenset({LIM_CSN_NOT_PROVIDED, LIM_REFERENCE_UNAVAILABLE})

# estados de auditoria de una referencia
AUD_VERIFIED, AUD_NOT_RECOVERABLE, AUD_POINTER_NOT_FOUND, AUD_FRAGMENT_MISMATCH = "VERIFIED", "SOURCE_NOT_RECOVERABLE", "POINTER_NOT_FOUND", "FRAGMENT_MISMATCH"
REF_KINDS = ("entity_definition", "rf_task_columns")
LEAF_KEYS = ("leaf", "version", "materialization_kind", "unknown_reasons", "unknown_detail", "flags", "derived_boundary_class", "boundary_reasons", "composition", "terminal_boundaries",
             "chain_complete", "chain_gaps", "producers", "mapping_profile", "evidence_refs", "verifiability", "technical_references")


def sha256_json(obj: Any) -> str:
    """Hash estable de un fragmento JSON (claves ordenadas, sin espacios)."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _map(x: Any) -> Mapping[str, Any]:
    return x if isinstance(x, Mapping) else {}


def verifiability_of(rec: Mapping[str, Any]) -> str:
    """Grado de verificabilidad de un registro derivado: ``NOT_EVALUATED`` (no hay productor/evidencia que evaluar), ``UNVERIFIABLE`` (UNKNOWN con causa de evidencia), ``VERIFIED_BY_CSN_REFERENCES``
    (el veredicto depende de referencias tecnicas recuperables) o ``VERIFIED_FROM_PROFILE`` (solo de la evidencia ya persistida)."""
    if rec.get("materialization_kind") == LM.KIND_UNKNOWN:
        return VER_NOT_EVALUATED if set(rec.get("unknown_reasons") or ()) <= _NOT_EVALUATED_CODES else VER_UNVERIFIABLE
    for p in rec.get("producers") or ():
        mp = _map(p.get("mapping_profile"))
        if p.get("resolved_via") or mp.get("column_evidence") == LM.COLUMN_EVIDENCE_VERIFIED or mp.get("identity_normalized"):
            return VER_BY_CSN
    return VER_FROM_PROFILE


def _ref(index: Mapping[str, Any] | None, key: Any, pool_signature: str | None, fragment_sha256: str | None) -> dict[str, Any] | None:
    """Referencia recuperable de una evidencia tecnica; ``None`` si el recolector no pudo registrarla."""
    r = (index or {}).get(key)
    if not isinstance(r, Mapping):
        return None
    return {"source_file": r["source_file"], "pointer": r["pointer"], "fragment_sha256": fragment_sha256 or r.get("fragment_sha256"), "file_sha256": r.get("file_sha256"), "pool_signature": pool_signature}


def _technical_references(rec: Mapping[str, Any], csn: Mapping[str, Any], refs: Mapping[str, Any], pool_signature: str | None, limitations: set[str]) -> dict[str, Any]:
    """Pruebas tecnicas MINIMAS de los veredictos que dependieron del CSN. Solo registra lo que la derivacion ya uso (``resolved_via``, ``column_evidence``, ``normalized_pairs``)."""
    leaf = rec["leaf"]
    entity_defs, rf_cols = _map(csn.get("entity_definitions")), _map(csn.get("rf_task_columns"))
    ent_idx, rf_idx = _map(refs.get("entities")), _map(refs.get("rf_tasks"))
    delta, cols, case = [], [], []
    for p in rec.get("producers") or ():
        dest = p.get("destination")
        if p.get("resolved_via") and p.get("shadow_entity"):
            x_ref = _ref(ent_idx, leaf, pool_signature, sha256_json(entity_defs[leaf]) if leaf in entity_defs else None)
            d = p["shadow_entity"]
            d_ref = _ref(ent_idx, d, pool_signature, sha256_json(entity_defs[d]) if d in entity_defs else None) if d in entity_defs else None
            if x_ref is None:
                limitations.add(LIM_REFERENCE_UNAVAILABLE)
            delta.append({"declared_by": "@DataWarehouse.delta.deltaFromEntities", "entity": leaf, "shadow_entity": d, "producer": p.get("name"), "entity_definition": x_ref, "shadow_definition": d_ref})
        mp = _map(p.get("mapping_profile"))
        if mp.get("column_evidence") in (LM.COLUMN_EVIDENCE_VERIFIED, LM.COLUMN_EVIDENCE_CONTRADICTED) and p.get("kind") == LM.REPLICATION_FLOW and dest is not None:
            c = _map(rf_cols.get((p.get("name"), dest)))
            frag = {"source_columns": list(c.get("source_columns") or ()), "target_columns": list(c.get("target_columns") or ())}
            t_ref = _ref(rf_idx, (p.get("name"), dest), pool_signature, sha256_json(frag))
            if t_ref is None:
                limitations.add(LIM_REFERENCE_UNAVAILABLE)
            cols.append({"flow": p.get("name"), "destination": dest, "column_evidence": mp["column_evidence"], "n_source_columns": len(frag["source_columns"]), "n_target_columns": len(frag["target_columns"]),
                         "source_columns_sha256": sha256_json(frag["source_columns"]), "target_columns_sha256": sha256_json(frag["target_columns"]), "task_reference": t_ref})
        for t_name, s_name in p.get("normalized_pairs") or ():
            case.append({"flow": p.get("name"), "destination": dest, "target": t_name, "source": s_name})
    return {"delta_links": delta, "rf_columns": cols, "case_equivalence": case}


def _persist_record(rec: Mapping[str, Any], csn: Mapping[str, Any], refs: Mapping[str, Any], pool_signature: str | None, limitations: set[str]) -> dict[str, Any]:
    out = json.loads(json.dumps(rec, default=str))                       # copia profunda JSON-serializable del registro derivado (la derivacion no se muta)
    out["verifiability"] = verifiability_of(rec)
    out["technical_references"] = _technical_references(rec, csn, refs, pool_signature, limitations)
    return out


def collect_references(leaves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Referencias unicas (ordenadas) de las pruebas tecnicas persistidas."""
    seen: dict[str, dict[str, Any]] = {}
    for rec in leaves:
        tr = rec["technical_references"]
        for d in tr["delta_links"]:
            for k in ("entity_definition", "shadow_definition"):
                if d.get(k):
                    seen.setdefault(json.dumps([d[k]["source_file"], d[k]["pointer"]]), {"kind": "entity_definition", **d[k]})
        for c in tr["rf_columns"]:
            if c.get("task_reference"):
                seen.setdefault(json.dumps([c["task_reference"]["source_file"], c["task_reference"]["pointer"]]), {"kind": "rf_task_columns", **c["task_reference"]})
    return [seen[k] for k in sorted(seen)]


def build_section(ce: Mapping[str, Any], csn: Mapping[str, Any] | None = None, references: Mapping[str, Any] | None = None, *, pool_signature: str | None = None) -> dict[str, Any]:
    """Seccion ``leaf_materialization`` de ``canonical-evidence/v4`` para ``ce`` (v1-v3 ya armado). ``csn``/``references``: evidencia tecnica del pool (ver ``collect_*``) o ``None`` (sin pool:
    lo que depende de ella queda UNKNOWN y la seccion lo informa como limitacion). Determinista; no muta ``ce``."""
    csn = csn or {}
    refs = references or {}
    derived = LM.derive_all(ce, csn or None)
    limitations: set[str] = set()
    provided = bool(csn)
    if not derived and not _sections_present(ce):
        state, reason, leaves = NOT_EVALUATED, NE_SOURCE_SECTIONS_MISSING, []
    else:
        state, reason = EVALUATED, None
        leaves = [_persist_record(r, csn, refs, pool_signature, limitations) for _, r in sorted(derived.items())]
    if not provided:
        limitations.add(LIM_CSN_NOT_PROVIDED)
    refs_list = collect_references(leaves)
    counts = {"leaves": len(leaves), "by_kind": _count(r["materialization_kind"] for r in leaves), "by_boundary_class": _count(r["derived_boundary_class"] for r in leaves),
              "by_verifiability": _count(r["verifiability"] for r in leaves), "unknown_reasons": _count(c for r in leaves for c in r["unknown_reasons"])}
    return {"version": LM.LEAF_MATERIALIZATION_VERSION, "evidence_version": EVIDENCE_VERSION, "evaluation_state": state, "not_evaluated_reason": reason,
            "derivation": {"module": DERIVATION_MODULE, "version": LM.LEAF_MATERIALIZATION_VERSION},
            "derivation_inputs": {"persisted_sections": list(PERSISTED_SECTIONS), "csn": {"provided": provided, "pool_signature": pool_signature if provided else None,
                                                                                         "limitations": sorted(limitations), "references": refs_list}},
            "leaves": leaves, "counts": counts,
            "definition": "derived materialization of local leaves (replica / copy / transformed / unknown) and boundary class, with the minimal technical proofs and recoverable source references that "
                          "support each verdict; evidence of provenance, not a quality judgment; the full CSN is never persisted"}


def _sections_present(ce: Mapping[str, Any]) -> bool:
    return not LM._missing_sections(ce)


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return dict(sorted(out.items()))


# ───────────────────────────── lectura (incluye perfiles historicos) ─────────────────────────────
def not_evaluated_record(leaf: str, code: str = LM.U_SECTION_NOT_PERSISTED) -> dict[str, Any]:
    """Registro de una hoja SIN evidencia persistida: UNKNOWN / NO EVALUADA con causa. Nunca «sin problemas»."""
    rec = LM._unknown(leaf, code, boundary_code=LM.B_NO_SECTION)
    rec["verifiability"] = VER_NOT_EVALUATED
    rec["technical_references"] = {"delta_links": [], "rf_columns": [], "case_equivalence": []}
    return rec


def read_section(ce: Any) -> dict[str, Any]:
    """Vista de solo lectura de la seccion persistida. Perfil sin la seccion (v1-v3 historicos) o con forma no interpretable => ``state = NOT_EVALUATED`` (``SECTION_NOT_PERSISTED``), sin hojas."""
    sec = _map(ce).get(SECTION_KEY) if isinstance(ce, Mapping) else None
    if isinstance(sec, Mapping) and isinstance(sec.get("leaves"), list):
        csn = _map(_map(sec.get("derivation_inputs")).get("csn"))
        return {"persisted": True, "state": sec.get("evaluation_state"), "reason": sec.get("not_evaluated_reason"), "version": sec.get("version"),
                "csn_provided": bool(csn.get("provided")), "limitations": list(csn.get("limitations") or ()),
                "leaves": {r["leaf"]: r for r in sec["leaves"] if isinstance(r, Mapping) and isinstance(r.get("leaf"), str)}}
    return {"persisted": False, "state": NOT_EVALUATED, "reason": LM.U_SECTION_NOT_PERSISTED, "version": None, "csn_provided": False, "limitations": [], "leaves": {}}


def record_of(view: Mapping[str, Any], leaf: str) -> dict[str, Any]:
    """Registro persistido de ``leaf`` o, si no existe, su representacion NO EVALUADA."""
    rec = _map(view.get("leaves")).get(leaf)
    if isinstance(rec, Mapping):
        return dict(rec)
    return not_evaluated_record(leaf, LM.U_SECTION_NOT_PERSISTED if not view.get("persisted") else LM.U_LEAF_NOT_FOUND)


# ───────────────────────────── auditoria de referencias ─────────────────────────────
def _resolve_pointer(doc: Any, pointer: str) -> Any:
    node = doc
    for part in [p for p in pointer.split("/") if p]:
        if isinstance(node, Mapping) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            raise KeyError(part)
    return node


def audit_reference(ref: Mapping[str, Any], resolver: Callable[[str], Any]) -> str:
    """Estado de auditoria de UNA referencia: recupera el archivo por ``resolver`` (devuelve el JSON ya cargado o ``None``), resuelve el puntero y compara el hash del fragmento."""
    doc = resolver(ref["source_file"])
    if doc is None:
        return AUD_NOT_RECOVERABLE
    try:
        node = _resolve_pointer(doc, ref["pointer"])
    except KeyError:
        return AUD_POINTER_NOT_FOUND
    if ref.get("kind") == "rf_task_columns":
        flow = ref["pointer"].strip("/").split("/")[1]
        task = node if isinstance(node, Mapping) else {}
        cols = LM.rf_task_columns({flow: {"contents": {"replicationTasks": [task]}}})
        frag = next(iter(cols.values()), {"source_columns": [], "target_columns": []})
        frag = {"source_columns": frag["source_columns"], "target_columns": frag["target_columns"]}
    else:
        frag = node
    return AUD_VERIFIED if sha256_json(frag) == ref.get("fragment_sha256") else AUD_FRAGMENT_MISMATCH


def audit_section(section: Mapping[str, Any], resolver: Callable[[str], Any]) -> dict[str, Any]:
    """Audita las referencias de la seccion contra la fuente. Si una fuente ya no se puede recuperar se informa (``SOURCE_NOT_RECOVERABLE``): el hash no se acepta como sustituto."""
    refs = _map(_map(section.get("derivation_inputs")).get("csn")).get("references") or []
    results = [{"source_file": r["source_file"], "pointer": r["pointer"], "kind": r.get("kind"), "status": audit_reference(r, resolver)} for r in refs]
    counts = _count(r["status"] for r in results)
    return {"references": results, "counts": counts, "all_verified": bool(results) and set(counts) == {AUD_VERIFIED}, "has_unrecoverable": AUD_NOT_RECOVERABLE in counts}
