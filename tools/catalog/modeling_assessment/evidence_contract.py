"""Contrato EXPLICITO de la seccion ``canonical_evidence`` del documento publico (Gate 6). Sin JSON Schema ni dependencias: validacion Python con
vocabularios CERRADOS tomados de las constantes del paquete (``provenance.py`` / ``derivation.py`` / ``readers.py``) e invariantes internos.

Contrato condicionado por VERSION (el documento publico ``assessment/v1`` no tiene JSON Schema; este es su unico contrato para esta seccion):
  1. un perfil historico SIN ``versions.canonical_evidence`` puede omitir la seccion y sigue siendo legible/valido;
  2. un perfil que declara una version SOPORTADA (``v1`` o ``v2``) DEBE traer la seccion (ASSESSED / NOT_APPLICABLE) y debe validar contra el contrato de ESA version
     (los perfiles gobernados ``v1`` siguen siendo validos mientras no se migren; ``v2`` agrega las secciones del evidence release y exige ``versions.closure``);
  3. ``canonical_evidence`` presente sin una version compatible declarada es INVALIDA (ni se ignora ni se acepta por laxitud);
  4. una version declarada fuera de las soportadas es INVALIDA (no se interpreta un contrato desconocido).
Es evidencia, no juicio: ningun invariante habla de calidad.
"""
from __future__ import annotations

from typing import Any

from .derivation import (
    AMBIGUITY_CODES, DERIVATION_EXTRACTOR_VERSION, EXPOSURE_BASIS_SAME_NAME, INPUT_STATES, OPERATION_CLASSES, RELATION_REASONS, RELATIONS,
)
from .canonical_sections import V2_SECTIONS, V3_SECTIONS, build_v2_sections, build_v3_sections  # noqa: F401  (se re-exportan: ``profile`` solo importa de este modulo)
from .evidence_errors import CanonicalEvidenceError, _expect, _fail, _is_int, _keys  # noqa: F401  (CanonicalEvidenceError se re-exporta)
from .facts_closure import CLOSURE_RULESET  # noqa: F401  (se re-exporta)
from .provenance import (
    BASIS_DATA_FLOW, BASIS_DECLARED_DATASET, BASIS_NONE, BASIS_REPLICATION_FLOW, BASIS_TABLE_FUNCTION, PROV_DECLARED, PROV_ESTABLISHED, PROV_NOT_OBSERVABLE,
    UP_CYCLE, UP_DATASPHERE_VIEW, UP_LOCAL_TABLE, UP_OUTSIDE_POOL, UP_REMOTE_TABLE,
)
from .readers import (
    PRODUCER_FAMILIES, SCOPE_FAMILY_NOT_ACQUIRED, SCOPE_NO_FILES, SCOPE_NOT_DECLARED, SCOPE_UNINTERPRETED_PRODUCERS, SCOPE_UNREADABLE_FILES,
)

CANONICAL_EVIDENCE_VERSION_V1 = "canonical-evidence/v1"
CANONICAL_EVIDENCE_VERSION_V2 = "canonical-evidence/v2"      # v2 (evidence release): closure / physical boundaries / published contract / naming / harmonization / expressions / reconvergence / complexity / processing
CANONICAL_EVIDENCE_VERSION = "canonical-evidence/v3"        # v3 (field-lineage evidence release): + published_field_lineage (procedencia por campo publicado hasta las hojas frontera) + source_contract_classes (clase explicita de contrato de fuente por hoja)
SUPPORTED_VERSIONS = (CANONICAL_EVIDENCE_VERSION_V1, CANONICAL_EVIDENCE_VERSION_V2, CANONICAL_EVIDENCE_VERSION)

PROVENANCE_STATES = frozenset({PROV_ESTABLISHED, PROV_DECLARED, PROV_NOT_OBSERVABLE})
PROVENANCE_BASES = frozenset({BASIS_REPLICATION_FLOW, BASIS_DATA_FLOW, BASIS_DECLARED_DATASET, BASIS_TABLE_FUNCTION, BASIS_NONE})
SCOPE_LIMITATIONS = frozenset({SCOPE_NOT_DECLARED, SCOPE_FAMILY_NOT_ACQUIRED, SCOPE_UNINTERPRETED_PRODUCERS, SCOPE_UNREADABLE_FILES, SCOPE_NO_FILES})
LEAF_CAUSE_CODES = frozenset({"SUPPLEMENTARY_EVIDENCE_NOT_PROVIDED", "EVIDENCE_NOT_YET_PARSED", "CONTRADICTORY_EVIDENCE"})
TARGET_MATCHES = frozenset({"EXACT", "DELTA_SHADOW_SUFFIX"})
MATCH_AMBIGUITIES = frozenset({"MULTIPLE_DELTA_CANDIDATES"})
UPSTREAM_KINDS = frozenset({UP_REMOTE_TABLE, UP_LOCAL_TABLE, UP_DATASPHERE_VIEW, UP_OUTSIDE_POOL, UP_CYCLE})
FLOW_KINDS = frozenset({"REPLICATION_FLOW", "DATA_FLOW"})
FIELD_ASSESSABILITY = frozenset({"ASSESSED", "PARTIALLY_ASSESSED"})
REPETITION_KINDS = frozenset({"SIBLING", "LINEAGE_RELATED", "MIXED"})
EXPECTED_SECTIONS = ("leaf_provenance", "derivation_semantics", "expression_repetition", "assembly_topology")      # secciones de v1 (y de v2: v2 agrega V2_SECTIONS)


# ───────────────────────────── procedencia de hojas (G1) ─────────────────────────────
def _validate_scope(scope: Any, path: str) -> None:
    _keys(scope, {"declared", "producer_families_acquired", "conclusive", "limitation"}, path, closed=True)
    _expect(isinstance(scope["declared"], bool) and isinstance(scope["conclusive"], bool), path, "declared/conclusive deben ser booleanos")
    fam = scope["producer_families_acquired"]
    _expect(isinstance(fam, list) and set(fam) <= PRODUCER_FAMILIES and fam == sorted(set(fam)), path, "producer_families_acquired fuera del vocabulario")
    _expect(scope["limitation"] is None or scope["limitation"] in SCOPE_LIMITATIONS, path, "limitation fuera del vocabulario")
    _expect(scope["conclusive"] == (scope["limitation"] is None), path, "conclusive debe ser equivalente a limitation == null")
    if scope["conclusive"]:
        _expect(scope["declared"] and set(fam) == PRODUCER_FAMILIES, path, "un alcance concluyente exige alcance declarado con las tres familias de productores")
    if not scope["declared"]:
        _expect(fam == [], path, "sin alcance declarado no hay familias adquiridas")


def _validate_leaf(rec: Any, path: str) -> None:
    _keys(rec, {"leaf", "assessability", "cause_code", "implementation", "identity", "provenance_state", "basis", "producer_flows", "origin", "upstream",
                "chain_complete", "declaration", "match_ambiguity", "scope"}, path, closed=True)
    _expect(isinstance(rec["leaf"], str) and rec["leaf"], path, "leaf vacio")
    st, basis, ass = rec["provenance_state"], rec["basis"], rec["assessability"]
    _expect(st is None or st in PROVENANCE_STATES, path, f"provenance_state invalido {st!r}")
    _expect(basis in PROVENANCE_BASES, path, f"basis invalido {basis!r}")
    _expect(ass in ("ASSESSED", "NOT_ASSESSED"), path, f"assessability invalida {ass!r}")
    _expect((st is None) == (ass == "NOT_ASSESSED"), path, "provenance_state nulo si y solo si NOT_ASSESSED")
    _expect((ass == "ASSESSED") == (rec["cause_code"] is None), path, "cause_code solo para NOT_ASSESSED")
    _expect(rec["cause_code"] is None or rec["cause_code"] in LEAF_CAUSE_CODES, path, "cause_code fuera del vocabulario")
    _validate_scope(rec["scope"], path + ".scope")
    _expect(rec["match_ambiguity"] is None or rec["match_ambiguity"] in MATCH_AMBIGUITIES, path, "match_ambiguity invalida")
    _expect(isinstance(rec["producer_flows"], list) and isinstance(rec["upstream"], list), path, "producer_flows/upstream deben ser listas")
    for i, p in enumerate(rec["producer_flows"]):
        _keys(p, {"kind", "name", "role", "target_match", "load_types"}, f"{path}.producer_flows[{i}]", closed=True)
        _expect(p["kind"] in FLOW_KINDS and p["role"] == "target" and p["target_match"] in TARGET_MATCHES, f"{path}.producer_flows[{i}]", "kind/role/target_match invalidos")
        _expect(isinstance(p["load_types"], list) and all(isinstance(x, str) for x in p["load_types"]), f"{path}.producer_flows[{i}]", "load_types invalido")
    for i, u in enumerate(rec["upstream"]):
        _keys(u, {"entity", "kind", "provenance_state", "basis", "remote_entity", "chain_complete"}, f"{path}.upstream[{i}]", closed=True)
        _expect(u["kind"] in UPSTREAM_KINDS and isinstance(u["chain_complete"], bool), f"{path}.upstream[{i}]", "kind/chain_complete invalidos")
        _expect(u["provenance_state"] is None or u["provenance_state"] in PROVENANCE_STATES, f"{path}.upstream[{i}]", "provenance_state invalido")
    if st == PROV_NOT_OBSERVABLE:                                      # invariante central: la ausencia solo es un resultado EVALUADO con alcance concluyente
        _expect(rec["scope"]["conclusive"], path, "NOT_OBSERVABLE exige alcance concluyente")
        _expect(basis == BASIS_NONE and not rec["producer_flows"] and rec["declaration"] is None, path, "NOT_OBSERVABLE no admite productor ni declaracion")
    elif st == PROV_ESTABLISHED:
        _expect(basis in (BASIS_REPLICATION_FLOW, BASIS_DATA_FLOW) and rec["producer_flows"], path, "ESTABLISHED exige un productor RF/DF con la hoja como destino")
        if basis == BASIS_REPLICATION_FLOW:
            o = rec["origin"]
            _keys(o, {"systems", "source_objects"}, path + ".origin", closed=True)
            _expect(o["systems"] and o["source_objects"], path + ".origin", "un RF establece sistema y objeto fuente")
            for sy in o["systems"]:
                _keys(sy, {"connection_id", "connection_type", "container"}, path + ".origin.systems", closed=True)
        else:
            _expect(rec["upstream"] and isinstance(rec["chain_complete"], bool), path, "DF exige upstream y chain_complete")
    elif st == PROV_DECLARED:
        _expect(basis in (BASIS_DECLARED_DATASET, BASIS_TABLE_FUNCTION) and rec["declaration"] is not None and not rec["producer_flows"], path,
                "DECLARED exige la declaracion del artefacto (y nunca un productor)")
        _keys(rec["declaration"], {"kind", "data_source_schema", "data_source_entity"}, path + ".declaration", closed=True)
        _expect(rec["declaration"]["kind"] == basis, path + ".declaration", "kind debe coincidir con basis")
    else:                                                              # sin determinar
        _expect(basis == BASIS_NONE, path, "una hoja sin determinar no declara basis")
        if rec["match_ambiguity"]:
            _expect(rec["cause_code"] == "CONTRADICTORY_EVIDENCE", path, "la ambiguedad de coincidencia es evidencia contradictoria, no ausencia")
    if rec["match_ambiguity"]:
        _expect(st is None, path, "con ambiguedad de coincidencia la procedencia NO se establece")


# ───────────────────────────── derivaciones (G3) ─────────────────────────────
def _validate_field(f: Any, path: str) -> None:
    _keys(f, {"artifact", "field", "branch", "operations", "functions", "inputs", "exact_fp", "shape_fp", "resolvable", "ambiguity", "reads_provider_same_field",
              "exposed_by_root", "exposure_basis"}, path, closed=True)
    _expect(_is_int(f["branch"]) and f["branch"] >= 0, path, "branch invalido")
    _expect(isinstance(f["operations"], list) and f["operations"] and set(f["operations"]) <= set(OPERATION_CLASSES), path, "operations fuera de la taxonomia")
    _expect(isinstance(f["exact_fp"], str) and isinstance(f["shape_fp"], str) and f["exact_fp"] and f["shape_fp"], path, "huellas invalidas")
    _expect(isinstance(f["ambiguity"], list) and set(f["ambiguity"]) <= AMBIGUITY_CODES, path, "ambiguity fuera del vocabulario")
    _expect(f["resolvable"] == (not f["ambiguity"]), path, "resolvable debe ser equivalente a no tener ambiguedad")
    _expect(f["reads_provider_same_field"] in (True, False, None), path, "reads_provider_same_field invalido")
    for i, inp in enumerate(f["inputs"]):
        _keys(inp, {"provider", "field", "state"}, f"{path}.inputs[{i}]", closed=True)
        _expect(inp["state"] in INPUT_STATES, f"{path}.inputs[{i}]", "state fuera del vocabulario")
    _validate_exposure(f, path)


def _validate_exposure(rec: dict, path: str) -> None:
    """Todo ``exposed_by_root`` lleva su base: coincidencia de NOMBRE con el contrato de la raiz, NO linaje de campo probado."""
    _expect(isinstance(rec["exposed_by_root"], bool), path, "exposed_by_root debe ser booleano")
    _expect(rec.get("exposure_basis") == EXPOSURE_BASIS_SAME_NAME, path, "exposed_by_root exige exposure_basis == SAME_NAME_IN_ROOT_CONTRACT")


def _validate_derivation(d: Any) -> None:
    if d is None:
        return
    p = ".derivation_semantics"
    _keys(d, {"assessability", "repeated_derivations", "relation_counts"}, p, optional={"extractor_version", "artifacts_with_query_evidence",
          "artifacts_without_query_evidence", "fields", "counts", "exposure_basis"})
    _expect(d["assessability"] in FIELD_ASSESSABILITY or d["assessability"] is None, p, "assessability invalida")
    fields = d.get("fields")
    if d["assessability"] is not None:
        _keys(d, {"extractor_version", "artifacts_with_query_evidence", "artifacts_without_query_evidence", "fields", "counts", "exposure_basis"}, p)
        _expect(d["extractor_version"] == DERIVATION_EXTRACTOR_VERSION, p, "extractor_version no soportada")
        _expect(d["exposure_basis"] == EXPOSURE_BASIS_SAME_NAME, p, "exposure_basis de bloque invalida")
        for i, f in enumerate(fields):
            _validate_field(f, f"{p}.fields[{i}]")
        _expect(d["counts"]["derived_fields"] == len(fields) and d["counts"]["unresolvable"] == sum(1 for f in fields if not f["resolvable"]), p,
                "counts inconsistentes con fields")
    reps = d["repeated_derivations"]
    _expect(isinstance(reps, list), p, "repeated_derivations debe ser lista")
    by_field: dict[tuple[str, str], list[dict]] = {}
    for f in fields or []:
        by_field.setdefault((f["artifact"], f["field"]), []).append(f)
    for i, r in enumerate(reps):
        rp = f"{p}.repeated_derivations[{i}]"
        _keys(r, {"field", "provider", "consumer", "lineage_distance", "adjacent", "relation", "relation_reason", "exposed_by_root", "exposure_basis"}, rp, closed=True)
        _expect(r["relation"] in RELATIONS, rp, f"relation invalida {r['relation']!r}")
        _expect(r["relation_reason"] in RELATION_REASONS[r["relation"]], rp, "relation_reason incoherente con la relacion (un refinamiento no puede ser una duplicacion)")
        _expect(_is_int(r["lineage_distance"]) and r["lineage_distance"] >= 1 and r["adjacent"] == (r["lineage_distance"] == 1), rp, "distancia/adjacent incoherentes")
        _validate_exposure(r, rp)
        cons = by_field.get((r["consumer"], r["field"]))
        if cons and r["relation"] == "CHAINED_REFINEMENT":                # coherencia cruzada: el consumidor LEE el campo derivado
            _expect(all(c["reads_provider_same_field"] is True for c in cons), rp, "CHAINED_REFINEMENT exige que el consumidor lea el campo derivado del proveedor")
        if cons and r["relation"] == "DUPLICATED_TRANSFORMATION":
            _expect(all(c["reads_provider_same_field"] is False for c in cons), rp, "DUPLICATED_TRANSFORMATION exige que el consumidor NO lea el campo derivado")
    rc = d["relation_counts"]
    _expect(isinstance(rc, dict) and set(rc) == set(RELATIONS), p, "relation_counts debe traer exactamente las 4 relaciones")
    _expect(all(rc[k] == sum(1 for r in reps if r["relation"] == k) for k in RELATIONS), p, "relation_counts inconsistente con repeated_derivations")


# ───────────────────────────── descriptivos (G3/G4) ─────────────────────────────
def _validate_repetition(r: Any) -> None:
    if r is None:
        return
    p = ".expression_repetition"
    _keys(r, {"count", "groups", "definition"}, p, closed=True)
    _expect(r["count"] == len(r["groups"]), p, "count inconsistente")
    for i, g in enumerate(r["groups"]):
        gp = f"{p}.groups[{i}]"
        _keys(g, {"exact_fp", "shape_fp", "operations", "members", "artifact_pairs", "lineage_related_pairs", "sibling_pairs", "kind"}, gp, closed=True)
        _expect(g["kind"] in REPETITION_KINDS and set(g["operations"]) <= set(OPERATION_CLASSES), gp, "kind/operations fuera del vocabulario")
        _expect(len({m["artifact"] for m in g["members"]}) >= 2, gp, "un grupo exige >= 2 artefactos distintos")
        _expect(g["lineage_related_pairs"] + g["sibling_pairs"] == g["artifact_pairs"], gp, "pares inconsistentes")


def _validate_topology(t: Any) -> None:
    if t is None:
        return
    p = ".assembly_topology"
    _keys(t, {"count", "artifacts", "definition"}, p, closed=True)
    _expect(t["count"] == len(t["artifacts"]), p, "count inconsistente")
    for i, a in enumerate(t["artifacts"]):
        _keys(a, {"artifact", "direct_providers", "boundary_providers", "intermediate_providers", "reusable_intermediate_providers", "lineage_consumers",
                  "lineage_distance_to_root", "reaches_root_by_strict_lineage", "closure_leaves_reached", "closure_leaves_total", "share_of_closure_leaves"},
              f"{p}.artifacts[{i}]", closed=True)
        _expect(a["direct_providers"] >= 2 and a["boundary_providers"] + a["intermediate_providers"] == a["direct_providers"], f"{p}.artifacts[{i}]", "proveedores inconsistentes")


def validate_canonical_evidence(ce: Any) -> None:
    """Valida la seccion ``canonical_evidence`` contra el contrato de SU version (vocabularios cerrados + invariantes). Lanza ``CanonicalEvidenceError``."""
    _expect(isinstance(ce, dict), "", "debe ser un objeto")
    version = ce.get("version")
    _expect(version in SUPPORTED_VERSIONS, ".version", "version no soportada")
    if version == CANONICAL_EVIDENCE_VERSION:
        _keys(ce, {"version", *EXPECTED_SECTIONS, *V2_SECTIONS, *V3_SECTIONS}, "", closed=True)
    elif version == CANONICAL_EVIDENCE_VERSION_V2:
        _keys(ce, {"version", *EXPECTED_SECTIONS, *V2_SECTIONS}, "", closed=True)
    else:
        _keys(ce, {"version", *EXPECTED_SECTIONS}, "", closed=True)
    _expect(isinstance(ce["leaf_provenance"], list), ".leaf_provenance", "debe ser lista")
    seen = set()
    for i, rec in enumerate(ce["leaf_provenance"]):
        _validate_leaf(rec, f".leaf_provenance[{i}]")
        _expect(rec["leaf"] not in seen, f".leaf_provenance[{i}]", "hoja duplicada")
        seen.add(rec["leaf"])
    _validate_derivation(ce["derivation_semantics"])
    _validate_repetition(ce["expression_repetition"])
    _validate_topology(ce["assembly_topology"])
    if version in (CANONICAL_EVIDENCE_VERSION_V2, CANONICAL_EVIDENCE_VERSION):
        from .evidence_contract_v2 import validate_v2_sections
        validate_v2_sections(ce)
    if version == CANONICAL_EVIDENCE_VERSION:
        from .evidence_contract_v3 import validate_v3_sections
        validate_v3_sections(ce)


def validate_document_evidence(doc: dict) -> None:
    """Contrato condicionado por version (ver el docstring del modulo). ``doc`` ya paso la validacion de forma de ``validate_assessment``."""
    versions = doc.get("versions") or {}
    declared = versions.get("canonical_evidence")
    present = "canonical_evidence" in doc and doc["canonical_evidence"] is not None
    if declared is None:
        if present:
            _fail("", "presente sin versions.canonical_evidence compatible declarada")
        return                                                      # perfil historico: la seccion es opcional
    if declared not in SUPPORTED_VERSIONS:
        _fail("", f"version declarada {declared!r} no soportada (se esperaba una de {list(SUPPORTED_VERSIONS)})")
    if not present:
        if doc.get("assessment_state") in ("ASSESSED", "NOT_APPLICABLE"):
            _fail("", "el perfil declara versions.canonical_evidence pero no trae la seccion")
        return                                                      # INDETERMINATE: no hay secciones que validar
    ce = doc["canonical_evidence"]
    _expect(ce.get("version") == declared, ".version", "la version de la seccion no coincide con versions.canonical_evidence")
    validate_canonical_evidence(ce)
    if declared in (CANONICAL_EVIDENCE_VERSION_V2, CANONICAL_EVIDENCE_VERSION):
        _expect(versions.get("closure") == CLOSURE_RULESET, ".closure", f"{declared} exige versions.closure == " + CLOSURE_RULESET)
        pc = ce["closure"]["plane_context"]
        size = (doc.get("scope") or {}).get("closure_size")
        if size is not None and pc is not None:                                         # el cierre es el cierre previo mas lo agregado (y no se quita nada)
            _expect(size == pc["loader_file_flag_closure_size"] + len(pc["added_vs_loader_file_flag"]), ".closure.plane_context", "closure_size incoherente con la diferencia vs la politica previa")
