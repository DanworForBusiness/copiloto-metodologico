"""Contrato EXPLICITO de la seccion NUEVA de ``canonical_evidence`` v4 (v4.A): ``leaf_materialization``.
Vocabularios CERRADOS tomados de la derivacion (``tools.catalog.leaf_materialization``, fuente unica de la logica) + invariantes INTERNOS de forma y coherencia. El contrato NO recalcula ni reinterpreta
la clasificacion (no duplica reglas): la correspondencia entre la evidencia persistida y la derivacion se AUDITA con ``leaf_materialization_evidence`` y en los tests. Es evidencia de PROCEDENCIA, no juicio:
se prohiben claves de veredicto. No contiene el CSN: solo veredictos derivados, pruebas tecnicas minimas y referencias recuperables."""
from __future__ import annotations

import re
from typing import Any

from .evidence_errors import _expect, _is_int, _keys
from .legacy_bridge import leaf_materialization as LM          # derivacion unica (acceso por el bridge, R-I3)
from .legacy_bridge import leaf_materialization_evidence as LME
from .evidence_contract_v3 import _no_verdicts

V4_SECTIONS = (LME.SECTION_KEY,)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_BOUNDARY_VOCAB = frozenset({LM.BC_PHYSICAL_TABLE, LM.BC_GOVERNED_CDS, LM.BC_GOVERNED_SAPI, LM.BC_STANDARD_VIEW_ON_PHYSICAL, LM.BC_BW_BRIDGE, LM.BC_UNCLASSIFIED})
_PRODUCER_KEYS = {"kind", "name", "target_match", "load_types", "load_type", "materialization_kind", "unknown_reasons", "mapping_profile", "filtered", "write_mode", "upsert", "components",
                  "resolved_via", "shadow_entity", "wrapper", "destination", "normalized_pairs"}
_MAPPING_KEYS = {"identity", "identity_normalized", "rename", "rename_case_only", "skip", "expression", "unknown", "implicit_identity", "column_evidence"}
_WRAPPER_KEYS = {"entity", "shadow_entity", "reads_shadow", "projection", "transformations", "filtered", "filter_columns", "filter_nature", "column_identity"}
_COLUMN_EVIDENCE = frozenset({LM.COLUMN_EVIDENCE_VERIFIED, LM.COLUMN_EVIDENCE_CONTRADICTED, LM.COLUMN_EVIDENCE_NOT_PROVIDED})
_TRANSFORMATIONS = frozenset({"RENAME", "EXPRESSION", "TYPE_CHANGE", "JOIN", "AGGREGATION", "DISTINCT", "LIMIT", "SET_OPERATION"})
_FILTER_NATURES = frozenset({"CDC_ROW_STATE", "ROW_FILTER"})
_COLUMN_IDENTITY = frozenset({"VERIFIED_DEFINITION", "VERIFIED_RF_TARGET_NAMES", "NOT_VERIFIABLE"})
_PROJECTIONS = frozenset({"COLUMNS_ONLY", "NOT_PURE"})
_WRITE_MODES_RF = frozenset({"truncate", "no_truncate", "unspecified"})


def _str_list(v: Any, path: str, vocab: frozenset | None = None) -> None:
    _expect(isinstance(v, list) and all(isinstance(x, str) for x in v) and v == sorted(set(v)), path, "debe ser una lista de textos ordenada y sin duplicados")
    if vocab is not None:
        _expect(set(v) <= vocab, path, f"valor fuera del vocabulario: {sorted(set(v) - vocab)}")


def _validate_ref(r: Any, path: str, *, with_kind: bool) -> None:
    _keys(r, {"source_file", "pointer", "fragment_sha256", "file_sha256", "pool_signature"}, path, closed=True, optional={"kind"} if with_kind else set())
    _expect(isinstance(r["source_file"], str) and r["source_file"] and "\\" not in r["source_file"] and not r["source_file"].startswith("/") and ".." not in r["source_file"].split("/"), path,
            "source_file debe ser una ruta relativa al pool con separador /")
    _expect(isinstance(r["pointer"], str) and r["pointer"].startswith("/"), path, "pointer debe ser un puntero JSON")
    _expect(isinstance(r["fragment_sha256"], str) and bool(_HEX64.match(r["fragment_sha256"])), path, "fragment_sha256 invalido")
    _expect(isinstance(r["file_sha256"], str) and bool(_HEX64.match(r["file_sha256"])), path, "file_sha256 invalido")
    _expect(isinstance(r["pool_signature"], str) and r["pool_signature"], path, "pool_signature obligatoria: la referencia se ancla a un pool concreto")
    if with_kind:
        _expect(r.get("kind") in LME.REF_KINDS, path, "kind de referencia fuera del vocabulario")


def _validate_mapping(mp: Any, path: str, csn_provided: bool) -> None:
    _keys(mp, _MAPPING_KEYS, path, closed=True)
    _expect(all(_is_int(mp[k]) and mp[k] >= 0 for k in ("identity", "identity_normalized", "rename", "rename_case_only", "skip", "expression", "unknown")), path, "contadores invalidos")
    _expect(isinstance(mp["implicit_identity"], bool) and mp["column_evidence"] in _COLUMN_EVIDENCE, path, "implicit_identity/column_evidence invalidos")
    _expect(mp["identity_normalized"] <= mp["rename_case_only"], path, "identity_normalized no puede superar rename_case_only")
    if mp["column_evidence"] != LM.COLUMN_EVIDENCE_NOT_PROVIDED or mp["identity_normalized"]:
        _expect(csn_provided, path, "evidencia de columnas / identidad normalizada exige evidencia tecnica del pool declarada")


def _validate_wrapper(w: Any, path: str) -> None:
    _keys(w, _WRAPPER_KEYS, path, closed=True)
    _expect(w["reads_shadow"] in (True, False, None) and w["projection"] in (*_PROJECTIONS, None) and w["column_identity"] in _COLUMN_IDENTITY and isinstance(w["filtered"], bool), path, "campos invalidos")
    _str_list(w["transformations"], path + ".transformations", _TRANSFORMATIONS)
    _expect(isinstance(w["filter_columns"], list) and all(isinstance(x, str) for x in w["filter_columns"]) and w["filter_nature"] in (*_FILTER_NATURES, None), path, "filtro invalido")
    _expect(w["filtered"] == (w["filter_nature"] is not None), path, "filtered si y solo si hay filter_nature")


def _validate_producer(p: Any, path: str, leaf: str, csn_provided: bool) -> None:
    _keys(p, _PRODUCER_KEYS, path, closed=True)
    _expect(p["kind"] in (LM.DATA_FLOW, LM.REPLICATION_FLOW, None) and (p["name"] is None or (isinstance(p["name"], str) and p["name"])), path, "kind/name invalidos")
    _expect(p["target_match"] in (LM.EXACT, "DELTA_SHADOW_SUFFIX", None), path, "target_match fuera del vocabulario")
    _expect(p["materialization_kind"] in LM.MATERIALIZATION_KINDS and (p["materialization_kind"] == LM.KIND_UNKNOWN) == bool(p["unknown_reasons"]), path, "kind / causas incoherentes")
    _str_list(p["unknown_reasons"], path + ".unknown_reasons", LM.UNKNOWN_REASON_CODES)
    _expect(isinstance(p["load_types"], list) and all(isinstance(x, str) for x in p["load_types"]) and (p["load_type"] is None or isinstance(p["load_type"], str)), path, "tipos de carga invalidos")
    _expect(p["filtered"] in (True, False, None) and p["upsert"] in (True, False, None) and (p["write_mode"] is None or isinstance(p["write_mode"], str)), path, "filtrado/escritura invalidos")
    _expect(isinstance(p["components"], list) and all(isinstance(x, str) for x in p["components"]), path, "components invalido")
    _validate_mapping(p["mapping_profile"], path + ".mapping_profile", csn_provided)
    _expect(isinstance(p["normalized_pairs"], list) and all(isinstance(x, list) and len(x) == 2 and all(isinstance(y, str) for y in x) for x in p["normalized_pairs"]), path, "normalized_pairs invalido")
    _expect(len(p["normalized_pairs"]) == p["mapping_profile"]["identity_normalized"], path, "normalized_pairs debe tener un par por cada identity_normalized")
    if p["resolved_via"] is None:
        _expect(p["shadow_entity"] is None and p["wrapper"] is None, path, "sin resolucion Delta no hay entidad sombra ni wrapper")
        _expect(p["destination"] in (leaf, None), path, "sin resolucion Delta el destino es la propia hoja (o ninguno si no se establecio la relacion)")
    else:
        _expect(p["resolved_via"] == LM.RESOLVED_VIA_DELTA_LINK and csn_provided, path, "una relacion Delta resuelta exige el vinculo CSN y evidencia tecnica del pool declarada")
        _expect(isinstance(p["shadow_entity"], str) and p["shadow_entity"] and p["destination"] == p["shadow_entity"] and p["target_match"] == "DELTA_SHADOW_SUFFIX", path, "destino/entidad sombra incoherentes")
        _validate_wrapper(p["wrapper"], path + ".wrapper")
        _expect(p["wrapper"]["entity"] == leaf and p["wrapper"]["shadow_entity"] == p["shadow_entity"], path, "el wrapper debe describir a la hoja y a su sombra")


def _validate_technical_references(tr: Any, path: str, rec: dict, limitations: set[str], csn_provided: bool) -> None:
    _keys(tr, {"delta_links", "rf_columns", "case_equivalence"}, path, closed=True)
    resolved = sorted(((p["name"], p["shadow_entity"]) for p in rec["producers"] if p["resolved_via"]), key=repr)
    _expect(sorted(((d.get("producer"), d.get("shadow_entity")) for d in tr["delta_links"]), key=repr) == resolved, path, "delta_links debe corresponder uno a uno a los productores resueltos")
    for i, d in enumerate(tr["delta_links"]):
        dp = f"{path}.delta_links[{i}]"
        _keys(d, {"declared_by", "entity", "shadow_entity", "producer", "entity_definition", "shadow_definition"}, dp, closed=True)
        _expect(d["declared_by"] == "@DataWarehouse.delta.deltaFromEntities" and d["entity"] == rec["leaf"], dp, "vinculo mal declarado")
        if d["entity_definition"] is None:
            _expect(LME.LIM_REFERENCE_UNAVAILABLE in limitations, dp, "referencia ausente sin la limitacion SOURCE_REFERENCE_UNAVAILABLE")
        else:
            _validate_ref(d["entity_definition"], dp + ".entity_definition", with_kind=False)
        if d["shadow_definition"] is not None:
            _validate_ref(d["shadow_definition"], dp + ".shadow_definition", with_kind=False)
    for i, c in enumerate(tr["rf_columns"]):
        cp = f"{path}.rf_columns[{i}]"
        _keys(c, {"flow", "destination", "column_evidence", "n_source_columns", "n_target_columns", "source_columns_sha256", "target_columns_sha256", "task_reference"}, cp, closed=True)
        _expect(csn_provided and c["column_evidence"] in (LM.COLUMN_EVIDENCE_VERIFIED, LM.COLUMN_EVIDENCE_CONTRADICTED) and _is_int(c["n_source_columns"]) and _is_int(c["n_target_columns"]), cp, "evidencia de columnas invalida")
        _expect(all(isinstance(c[k], str) and _HEX64.match(c[k]) for k in ("source_columns_sha256", "target_columns_sha256")), cp, "hash de columnas invalido")
        if c["task_reference"] is None:
            _expect(LME.LIM_REFERENCE_UNAVAILABLE in limitations, cp, "referencia ausente sin la limitacion SOURCE_REFERENCE_UNAVAILABLE")
        else:
            _validate_ref(c["task_reference"], cp + ".task_reference", with_kind=False)
    _expect(sorted(((c["flow"], c["destination"], c["column_evidence"]) for c in tr["rf_columns"]), key=repr) == sorted(
        ((p["name"], p["destination"], p["mapping_profile"]["column_evidence"]) for p in rec["producers"]
         if p["kind"] == LM.REPLICATION_FLOW and p["mapping_profile"]["column_evidence"] != LM.COLUMN_EVIDENCE_NOT_PROVIDED), key=repr), path, "rf_columns debe corresponder a los productores con evidencia de columnas")
    pairs = sorted(((c["flow"], c["destination"], c["target"], c["source"]) for c in tr["case_equivalence"]), key=repr)
    _expect(pairs == sorted(((p["name"], p["destination"], t, s) for p in rec["producers"] for t, s in p["normalized_pairs"]), key=repr), path, "case_equivalence debe corresponder a normalized_pairs")


def _validate_leaf(rec: Any, path: str, csn_provided: bool, limitations: set[str]) -> None:
    _keys(rec, set(LME.LEAF_KEYS), path, closed=True)
    _expect(isinstance(rec["leaf"], str) and rec["leaf"] and rec["version"] == LM.LEAF_MATERIALIZATION_VERSION, path, "leaf/version invalidos")
    _expect(rec["materialization_kind"] in LM.MATERIALIZATION_KINDS and (rec["materialization_kind"] == LM.KIND_UNKNOWN) == bool(rec["unknown_reasons"]), path, "materialization_kind / unknown_reasons incoherentes")
    _str_list(rec["unknown_reasons"], path + ".unknown_reasons", LM.UNKNOWN_REASON_CODES)
    _expect(isinstance(rec["unknown_detail"], dict) and set(rec["unknown_detail"]) <= set(rec["unknown_reasons"]), path, "unknown_detail solo puede detallar causas declaradas")
    _keys(rec["flags"], {"multi_producer", "filtered", "writes"}, path + ".flags", closed=True)
    _expect(isinstance(rec["flags"]["multi_producer"], bool) and rec["flags"]["filtered"] in (True, False, None) and isinstance(rec["flags"]["writes"], list), path + ".flags", "flags invalidos")
    _expect(rec["derived_boundary_class"] in LM.DERIVED_BOUNDARY_CLASSES, path, "derived_boundary_class fuera del vocabulario")
    _str_list(rec["boundary_reasons"], path + ".boundary_reasons", LM.BOUNDARY_REASON_CODES)
    _expect((rec["derived_boundary_class"] == LM.DBC_UNDETERMINED) == bool(rec["boundary_reasons"]), path, "UNDETERMINED si y solo si hay causas de borde")
    _expect(isinstance(rec["composition"], dict) and all(k in _BOUNDARY_VOCAB and _is_int(v) and v > 0 for k, v in rec["composition"].items()), path, "composition invalida")
    _expect(isinstance(rec["terminal_boundaries"], list), path, "terminal_boundaries debe ser lista")
    for j, b in enumerate(rec["terminal_boundaries"]):
        _keys(b, {"boundary_entity", "remote_connection", "remote_entity", "boundary_class", "basis", "via_flows"}, f"{path}.terminal_boundaries[{j}]", closed=True)
        _expect(b["boundary_class"] in _BOUNDARY_VOCAB, f"{path}.terminal_boundaries[{j}]", "boundary_class fuera del vocabulario")
    _expect(sum(rec["composition"].values()) == len(rec["terminal_boundaries"]), path, "composition inconsistente con terminal_boundaries")
    _expect(rec["chain_complete"] in (True, False, None) and isinstance(rec["chain_gaps"], list), path, "cadena invalida")
    if rec["derived_boundary_class"] in (LM.DBC_GOVERNED, LM.DBC_PHYSICAL, LM.DBC_MIXED):
        _expect(rec["chain_complete"] is True and not rec["chain_gaps"], path, "una clase de borde determinada exige cadena completa y sin huecos")
    _validate_mapping(rec["mapping_profile"], path + ".mapping_profile", csn_provided)
    _expect(isinstance(rec["producers"], list), path, "producers debe ser lista")
    for j, p in enumerate(rec["producers"]):
        _validate_producer(p, f"{path}.producers[{j}]", rec["leaf"], csn_provided)
    _expect(isinstance(rec["evidence_refs"], list) and all(isinstance(x, str) for x in rec["evidence_refs"]), path, "evidence_refs invalido")
    _expect(rec["verifiability"] in LME.VERIFIABILITY_GRADES and rec["verifiability"] == LME.verifiability_of(rec), path, "verifiability incoherente con el registro")
    _validate_technical_references(rec["technical_references"], path + ".technical_references", rec, limitations, csn_provided)


def validate_leaf_materialization(sec: Any, ce: dict) -> None:
    p = "." + LME.SECTION_KEY
    _keys(sec, {"version", "evidence_version", "evaluation_state", "not_evaluated_reason", "derivation", "derivation_inputs", "leaves", "counts", "definition"}, p, closed=True)
    _expect(sec["version"] == LM.LEAF_MATERIALIZATION_VERSION and sec["evidence_version"] == LME.EVIDENCE_VERSION, p, "version de derivacion / de evidencia no soportada")
    _expect(sec["derivation"] == {"module": LME.DERIVATION_MODULE, "version": LM.LEAF_MATERIALIZATION_VERSION}, p + ".derivation", "referencia a la derivacion invalida")
    _expect(sec["evaluation_state"] in LME.EVALUATION_STATES, p, "evaluation_state fuera del vocabulario")
    _expect((sec["evaluation_state"] == LME.EVALUATED) == (sec["not_evaluated_reason"] is None) and sec["not_evaluated_reason"] in (None, LME.NE_SOURCE_SECTIONS_MISSING), p, "not_evaluated_reason incoherente")
    di = sec["derivation_inputs"]
    _keys(di, {"persisted_sections", "csn"}, p + ".derivation_inputs", closed=True)
    _expect(di["persisted_sections"] == list(LME.PERSISTED_SECTIONS), p + ".derivation_inputs", "persisted_sections debe declarar la evidencia de entrada")
    csn = di["csn"]
    _keys(csn, {"provided", "pool_signature", "limitations", "references"}, p + ".derivation_inputs.csn", closed=True)
    provided = csn["provided"]
    _expect(isinstance(provided, bool), p + ".derivation_inputs.csn", "provided debe ser booleano")
    _str_list(csn["limitations"], p + ".derivation_inputs.csn.limitations", LME.LIMITATION_CODES)
    limitations = set(csn["limitations"])
    _expect(provided == (LME.LIM_CSN_NOT_PROVIDED not in limitations), p + ".derivation_inputs.csn", "la limitacion CSN_EVIDENCE_NOT_PROVIDED debe declararse si y solo si no hay evidencia tecnica")
    _expect(isinstance(csn["references"], list), p + ".derivation_inputs.csn", "references debe ser lista")
    if provided:
        _expect(isinstance(csn["pool_signature"], str) and csn["pool_signature"], p + ".derivation_inputs.csn", "con evidencia tecnica declarada la firma del pool es obligatoria")
    else:
        _expect(csn["pool_signature"] is None and csn["references"] == [], p + ".derivation_inputs.csn", "sin evidencia tecnica no hay firma de pool ni referencias")
    for i, r in enumerate(csn["references"]):
        _validate_ref(r, f"{p}.derivation_inputs.csn.references[{i}]", with_kind=True)
        _expect(r["pool_signature"] == csn["pool_signature"], f"{p}.derivation_inputs.csn.references[{i}]", "la referencia debe anclarse a la firma del pool declarada")
    leaves = sec["leaves"]
    _expect(isinstance(leaves, list), p + ".leaves", "debe ser lista")
    names = [r.get("leaf") for r in leaves if isinstance(r, dict)]
    _expect(len(names) == len(leaves) and names == sorted(set(names)), p + ".leaves", "hojas ordenadas, unicas y sin duplicados")
    if sec["evaluation_state"] == LME.NOT_EVALUATED:
        _expect(leaves == [], p + ".leaves", "una seccion NO EVALUADA no trae hojas (evidencia no evaluada, nunca «sin problemas»)")
    else:
        expected = {r["leaf"] for r in ce["leaf_provenance"]} | {r["leaf"] for r in (ce["physical_source_boundaries"] or {}).get("leaves") or []}
        _expect(set(names) == expected, p + ".leaves", "debe cubrir exactamente las hojas con procedencia o con registro de borde")
    for i, rec in enumerate(leaves):
        _validate_leaf(rec, f"{p}.leaves[{i}]", provided, limitations)
    refs_set = {(r["source_file"], r["pointer"]) for r in csn["references"]}
    for rec in leaves:                                   # toda referencia usada por un veredicto figura en la lista de la seccion (auditable)
        tr = rec["technical_references"]
        used = [d[k] for d in tr["delta_links"] for k in ("entity_definition", "shadow_definition") if d.get(k)] + [c["task_reference"] for c in tr["rf_columns"] if c.get("task_reference")]
        _expect(all((u["source_file"], u["pointer"]) in refs_set for u in used), p + ".derivation_inputs.csn.references", "falta una referencia usada por un veredicto")
    counts = sec["counts"]
    _keys(counts, {"leaves", "by_kind", "by_boundary_class", "by_verifiability", "unknown_reasons"}, p + ".counts", closed=True)
    _expect(counts == {"leaves": len(leaves), "by_kind": LME._count(r["materialization_kind"] for r in leaves), "by_boundary_class": LME._count(r["derived_boundary_class"] for r in leaves),
                       "by_verifiability": LME._count(r["verifiability"] for r in leaves), "unknown_reasons": LME._count(c for r in leaves for c in r["unknown_reasons"])}, p + ".counts", "counts inconsistentes con las hojas")
    _no_verdicts(sec, p)


def validate_v4_sections(ce: dict) -> None:
    validate_leaf_materialization(ce[LME.SECTION_KEY], ce)
