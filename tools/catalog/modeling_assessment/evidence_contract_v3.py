"""Contrato EXPLICITO de las secciones NUEVAS de ``canonical_evidence`` v3 (evidence release de linaje de campo): ``published_field_lineage`` y ``source_contract_classes``.
Vocabularios CERRADOS tomados de los modulos que producen cada hecho + invariantes internos. Es evidencia de PROCEDENCIA, no juicio: ningun invariante habla de calidad, y se
prohiben explicitamente claves de veredicto (``quality``, ``score``, ``restored``, ``deviation``...) en estas secciones.
"""
from __future__ import annotations

from typing import Any

from .evidence_errors import _expect, _is_int, _keys
from .facts_field_lineage import (
    CLASSIFICATION_BASES, COMPLETENESS_STATES, LEAF_KINDS, PATH_BASIS, PATH_CHARACTERS, SCC_PLATFORM, SCC_UNKNOWN, SOURCE_CONTRACT_CLASSES, UNRESOLVED_REASONS, DELTA_BASIS,
    SCC_ABAP_TABLE,
)
from .field_lineage import AMBIGUITY_CODES, FIELD_LINEAGE_EXTRACTOR_VERSION, MAPPING_TYPES
from .facts_contract_semantics import DATATYPE_FAMILIES, FIELD_ROLES

V3_SECTIONS = ("published_field_lineage", "source_contract_classes")
_BANNED_PROVENANCE_KEYS = frozenset({"quality", "score", "restored", "restoration", "deviation", "weakness", "verdict", "level", "semantic_quality", "methodological_deviation"})


def _no_verdicts(node: Any, path: str) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            _expect(k not in _BANNED_PROVENANCE_KEYS, path, f"clave de veredicto prohibida en evidencia de procedencia: {k!r}")
            _no_verdicts(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _no_verdicts(v, f"{path}[{i}]")


# ───────────────────────────── source_contract_classes ─────────────────────────────
def validate_source_contract_classes(sc: Any) -> dict[str, str]:
    """Valida la seccion y devuelve ``{hoja: clase}`` para cruzar con el linaje."""
    p = ".source_contract_classes"
    _keys(sc, {"leaves", "by_class", "definition"}, p, closed=True)
    out: dict[str, str] = {}
    counts: dict[str, int] = {}
    for i, r in enumerate(sc["leaves"]):
        rp = f"{p}.leaves[{i}]"
        _keys(r, {"leaf", "leaf_kind", "contract_class", "classification_basis", "classification_bases", "boundary_classes", "source_systems", "remote_entities", "remote_connection",
                  "definition"}, rp, closed=True)
        _expect(isinstance(r["leaf"], str) and r["leaf"] and r["leaf"] not in out, rp, "hoja vacia o duplicada")
        _expect(r["leaf_kind"] in LEAF_KINDS and r["contract_class"] in SOURCE_CONTRACT_CLASSES, rp, "tipo de hoja / clase fuera del vocabulario")
        _expect(r["classification_basis"] in CLASSIFICATION_BASES and set(r["classification_bases"]) <= set(CLASSIFICATION_BASES), rp, "base de clasificacion fuera del vocabulario")
        _expect(isinstance(r["boundary_classes"], list) and isinstance(r["remote_entities"], list) and isinstance(r["source_systems"], list), rp, "listas invalidas")
        for s in r["source_systems"]:
            _keys(s, {"connection_id", "connection_type", "container"}, rp + ".source_systems", closed=True)
        _expect((r["contract_class"] == SCC_PLATFORM) == (r["leaf_kind"] == "PLATFORM_OBJECT"), rp, "PLATFORM_PROVIDED si y solo si la hoja es un objeto de plataforma")
        if r["leaf_kind"] == "REMOTE_IMPORTED":
            _expect(len(r["remote_entities"]) == 1, rp, "una hoja remota importada tiene exactamente una entidad remota")
        if r["contract_class"] == SCC_UNKNOWN:
            _expect(r["classification_basis"] in {"NO_ACQUIRED_PRODUCER", "PROVENANCE_NOT_ASSESSED", "PROVENANCE_CHAIN_INCOMPLETE", "UNKNOWN"}, rp, "UNKNOWN exige una base que explique por que no se clasifica")
        if r["contract_class"] == SCC_ABAP_TABLE:
            _expect(r["classification_basis"] in {"REMOTE_ENTITY_NAMESPACE", "RF_SOURCE_CONTAINER", "MULTIPLE_CONVERGING_BASES"}, rp, "tabla ABAP fisica exige evidencia explicita (namespace / contenedor)")
        out[r["leaf"]] = r["contract_class"]
        counts[r["contract_class"]] = counts.get(r["contract_class"], 0) + 1
    _expect({c["contract_class"]: c["count"] for c in sc["by_class"]} == counts and len(sc["by_class"]) == len(counts), p, "by_class inconsistente con las hojas")
    _no_verdicts(sc, p)
    return out


# ───────────────────────────── published_field_lineage ─────────────────────────────
def validate_published_field_lineage(pl: Any, leaf_classes: dict[str, str], published: Any) -> None:
    if pl is None:
        _expect(published is None, ".published_field_lineage", "una raiz AM con contrato publicado exige el linaje de campo publicado")
        return
    p = ".published_field_lineage"
    _keys(pl, {"extractor_version", "root", "edge_basis", "fields", "counts", "harmonization_candidates_in_closure", "definition"}, p, closed=True)
    _expect(pl["extractor_version"] == FIELD_LINEAGE_EXTRACTOR_VERSION and pl["edge_basis"] == PATH_BASIS, p, "version del extractor / base de aristas no soportada")
    counts = {s: 0 for s in COMPLETENESS_STATES}
    seen: set[str] = set()
    for i, r in enumerate(pl["fields"]):
        fp = f"{p}.fields[{i}]"
        _keys(r, {"technical_name", "label", "role", "datatype_family", "immediate_providers", "completeness", "paths", "unresolved_reasons", "ambiguity_codes",
                  "harmonization_boundaries_crossed", "frontier_leaves", "lineage_paths"}, fp, closed=True)
        _expect(r["technical_name"] not in seen, fp, "campo duplicado")
        seen.add(r["technical_name"])
        _expect(r["role"] in FIELD_ROLES and r["datatype_family"] in DATATYPE_FAMILIES, fp, "rol / familia de tipo fuera del vocabulario")
        _expect(r["completeness"] in COMPLETENESS_STATES, fp, "completeness fuera del vocabulario")
        counts[r["completeness"]] += 1
        pa = r["paths"]
        _keys(pa, {"total", "to_frontier_leaf", "no_field_input", "unresolved", "ambiguous"}, fp + ".paths", closed=True)
        _expect(all(_is_int(v) and v >= 0 for v in pa.values()) and pa["total"] == pa["to_frontier_leaf"] + pa["no_field_input"] + pa["unresolved"] + pa["ambiguous"], fp, "conteos de caminos inconsistentes")
        reach = pa["to_frontier_leaf"] + pa["no_field_input"]
        if r["completeness"] == "COMPLETE":
            _expect(pa["unresolved"] == 0 and pa["ambiguous"] == 0 and reach > 0, fp, "COMPLETE exige que todo camino llegue a una hoja o a un origen sin insumo de campo")
        elif r["completeness"] == "AMBIGUOUS":
            _expect(pa["ambiguous"] > 0, fp, "AMBIGUOUS exige una ambiguedad explicita")
        elif r["completeness"] == "PARTIAL":
            _expect(pa["unresolved"] > 0 and reach > 0 and pa["ambiguous"] == 0, fp, "PARTIAL exige caminos que llegan y caminos sin resolver (sin ambiguedad)")
        else:
            _expect(pa["unresolved"] > 0 and reach == 0 and pa["ambiguous"] == 0, fp, "UNRESOLVED exige que ningun camino llegue")
        _expect(set(r["unresolved_reasons"]) <= set(UNRESOLVED_REASONS) and set(r["ambiguity_codes"]) <= AMBIGUITY_CODES, fp, "razones/codigos fuera del vocabulario")
        _expect((pa["unresolved"] > 0) == bool(r["unresolved_reasons"]) and (pa["ambiguous"] > 0) == bool(r["ambiguity_codes"]), fp, "razones inconsistentes con los conteos")
        for j, o in enumerate(r["immediate_providers"]):
            _keys(o, {"artifact", "field", "mapping_type"}, f"{fp}.immediate_providers[{j}]", closed=True, optional={"ambiguity"})
            _expect(o["mapping_type"] in MAPPING_TYPES, fp, "mapping_type fuera del vocabulario")
            if o["artifact"] is None:
                _expect("ambiguity" in o and (o["ambiguity"] is None or o["ambiguity"] in AMBIGUITY_CODES), fp, "un origen sin proveedor declara su ambiguedad (o None si es un origen sin insumo de campo)")
            else:
                _expect("ambiguity" not in o and o["field"], fp, "un proveedor resuelto no lleva ambiguedad")
        leaf_paths = 0
        for j, lf in enumerate(r["frontier_leaves"]):
            lp = f"{fp}.frontier_leaves[{j}]"
            _keys(lf, {"leaf", "leaf_field", "source_contract_class", "paths", "path_characters", "paths_through_harmonization"}, lp, closed=True)
            _expect(lf["leaf"] in leaf_classes and leaf_classes[lf["leaf"]] == lf["source_contract_class"], lp, "la hoja debe existir en source_contract_classes con la misma clase")
            _expect(set(lf["path_characters"]) <= set(PATH_CHARACTERS) and sum(lf["path_characters"].values()) == lf["paths"] and lf["paths"] >= 1, lp, "caracter de caminos inconsistente")
            _expect(0 <= lf["paths_through_harmonization"] <= lf["paths"], lp, "paths_through_harmonization fuera de rango")
            leaf_paths += lf["paths"]
        _expect(leaf_paths == pa["to_frontier_leaf"], fp, "los caminos por hoja no suman to_frontier_leaf")
        _expect(set(r["harmonization_boundaries_crossed"]) <= set(pl["harmonization_candidates_in_closure"]), fp, "una frontera de armonizacion cruzada debe ser un candidato del cierre")
        _expect(isinstance(r["lineage_paths"], list), fp, "lineage_paths debe ser lista")
        for j, lp_ in enumerate(r["lineage_paths"]):
            pp = f"{fp}.lineage_paths[{j}]"
            _keys(lp_, {"nodes", "edges", "character", "crosses_harmonization"}, pp, closed=True)
            _expect(len(lp_["nodes"]) == len(lp_["edges"]) + 1 and lp_["character"] in PATH_CHARACTERS and isinstance(lp_["crosses_harmonization"], bool), pp, "camino de muestra invalido")
            for n in lp_["nodes"]:
                _keys(n, {"artifact", "field"}, pp, closed=True, optional={"layer", "harmonization_candidate", "frontier_leaf"})
            for ed in lp_["edges"]:
                _keys(ed, {"mapping_type"}, pp, closed=True, optional={"normalization"})
                _expect(ed["mapping_type"] in MAPPING_TYPES and ed.get("normalization") in (None, DELTA_BASIS), pp, "arista fuera del vocabulario")
            _expect(lp_["nodes"][-1].get("frontier_leaf") is True and lp_["nodes"][0]["artifact"] == pl["root"], pp, "el camino de muestra va del AM a una hoja frontera")
    _expect(pl["counts"] == {"fields": len(pl["fields"]), **counts}, p, "counts inconsistentes")
    if published is not None and published.get("fields") is not None:
        _expect([f["technical_name"] for f in published["fields"]["fields"]] == [r["technical_name"] for r in pl["fields"]], p, "el linaje debe cubrir exactamente los campos publicados, en el mismo orden")
    _no_verdicts(pl, p)


def validate_v3_sections(ce: dict) -> None:
    classes = validate_source_contract_classes(ce["source_contract_classes"])
    validate_published_field_lineage(ce["published_field_lineage"], classes, ce["published_contract"])
