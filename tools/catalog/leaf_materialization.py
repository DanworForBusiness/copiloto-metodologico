"""Materializacion OBSERVADA de las hojas locales (``leaf-materialization/v1``): derivacion PURA, de solo lectura, sobre la evidencia canonica YA persistida.

Responde, por hoja local con procedencia, a "que tipo de tabla es respecto de lo que la alimenta" usando SOLO hechos ya extraidos:

  * ``leaf_provenance``              productores (flujo, tipo, ``target_match``, tipos de carga) y base de la procedencia
  * ``processing_flow_properties``   por flujo: tareas/nodos destino con ``attribute_mappings``, ``component_counts`` (Data Flow), modo de escritura, filtros
  * ``physical_source_boundaries``   bordes terminales de la cadena (clase de borde), ``chain_complete``, ``chain_gaps``, flujos recorridos

Modulo NEUTRO (``tools/catalog``, sin importar el paquete de Juicio ni el de Assessment): lo consume de solo lectura ``ProfileEvidence.materialization`` y lo podra importar la extraccion v4
(fuente unica de la derivacion). NO extrae, NO persiste, NO juzga, NO decide calidad, NO toca el perfil, la firma, la cobertura, la confianza ni el Juicio (ninguna regla consume este modulo). NUNCA infiere por nombres
(prefijos, sufijos, semejanza): las relaciones salen solo de los productores y las definiciones de flujo persistidas. Ante evidencia ausente, incompleta, ambigua o contradictoria el
resultado es ``UNKNOWN`` con CODIGO de causa; la ausencia de evidencia nunca se lee como buena. NUNCA lanza por un perfil historico (v1/v2): devuelve ``UNKNOWN``.

``materialization_kind`` (valor unico, describe lo que hacen los PRODUCTORES de la hoja, no la cadena aguas arriba de su fuente):
  REPLICA      todos los productores EXACT son Replication Flow y todo mapeo observado de la hoja es identidad o ``Skip Mapping`` (sin mapeos explicitos = identidad por defecto de la plataforma)
  COPY         el productor es un Data Flow cuyos unicos componentes son ``table.consumer`` + ``table.producer`` (1 y 1) y todo mapeo de la hoja es identidad o ``Skip Mapping``
  TRANSFORMED  se demuestra una transformacion: mapeo de renombre o de expresion, o cualquier componente de Data Flow distinto de consumer/producer (projection, join, agregacion, ...)
  UNKNOWN      productor ausente / no interpretable, ``target_match`` distinto de EXACT, mapeo no clasificable, forma de flujo no interpretable, evidencia contradictoria, multiples productores
               no comparables o secciones ausentes (siempre con ``unknown_reasons``)
Atributos SEPARADOS (nunca inferidos del nombre del artefacto): ``flags.multi_producer`` (>= 2 flujos distintos EXACT), ``flags.filtered`` (filtros de origen / de tarea observados),
``flags.writes`` (modo de escritura observado del destino).

``derived_boundary_class`` (de las clases de los bordes terminales ya persistidos): GOVERNED_DERIVED | PHYSICAL_DERIVED | MIXED (con ``composition``) | UNDETERMINED
(borde ``UNCLASSIFIED``, cadena incompleta, ``chain_gaps``, sin bordes o sin registro de borde para la hoja)."""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

# Clases de borde de la evidencia persistida (``physical_source_boundaries``). Se repiten como LITERALES a proposito: este modulo es NEUTRO (hermano de ``business_classification`` /
# ``domain_classification``) y lo pueden importar tanto el paquete de Juicio como el de Assessment sin que ninguno importe al otro; un test fija la igualdad con la fuente de verdad.
BC_PHYSICAL_TABLE = "PHYSICAL_TABLE"
BC_GOVERNED_CDS = "GOVERNED_CDS_CONTRACT"
BC_GOVERNED_SAPI = "GOVERNED_SAPI_EXTRACTOR"
BC_STANDARD_VIEW_ON_PHYSICAL = "STANDARD_VIEW_ON_PHYSICAL_TABLE"
BC_BW_BRIDGE = "BW_BRIDGE"
BC_UNCLASSIFIED = "UNCLASSIFIED"

LEAF_MATERIALIZATION_VERSION = "leaf-materialization/v1"

# ── clases de materializacion ──
KIND_REPLICA, KIND_COPY, KIND_TRANSFORMED, KIND_UNKNOWN = "REPLICA", "COPY", "TRANSFORMED", "UNKNOWN"
MATERIALIZATION_KINDS = (KIND_REPLICA, KIND_COPY, KIND_TRANSFORMED, KIND_UNKNOWN)

# ── clase de borde derivada ──
DBC_GOVERNED, DBC_PHYSICAL, DBC_MIXED, DBC_UNDETERMINED = "GOVERNED_DERIVED", "PHYSICAL_DERIVED", "MIXED", "UNDETERMINED"
DERIVED_BOUNDARY_CLASSES = (DBC_GOVERNED, DBC_PHYSICAL, DBC_MIXED, DBC_UNDETERMINED)
# Mismo criterio que ``ProfileEvidence.classify_source``: CDS / ODP-SAPI / vista estandar sobre tabla fisica / puente BW = contacto gobernado; tabla ABAP fisica = frontera fisica.
GOVERNED_BOUNDARY_CLASSES = frozenset({BC_GOVERNED_CDS, BC_GOVERNED_SAPI, BC_STANDARD_VIEW_ON_PHYSICAL, BC_BW_BRIDGE})
PHYSICAL_BOUNDARY_CLASSES = frozenset({BC_PHYSICAL_TABLE})

# ── codigos de causa de UNKNOWN (kind) ──
U_EVIDENCE_NOT_AVAILABLE = "EVIDENCE_NOT_AVAILABLE"
U_SECTION_MISSING = "SECTION_MISSING"
U_LEAF_NOT_FOUND = "LEAF_NOT_FOUND"
U_NO_PRODUCER = "NO_PRODUCER_OBSERVED"
U_DECLARED_NOT_OBSERVED = "PROVENANCE_DECLARED_NOT_OBSERVED"
U_TARGET_MATCH_NOT_EXACT = "PRODUCER_TARGET_MATCH_NOT_EXACT"
U_FLOW_KIND_UNSUPPORTED = "PRODUCER_FLOW_KIND_UNSUPPORTED"
U_FLOW_NOT_IN_EVIDENCE = "PRODUCER_FLOW_NOT_IN_PROCESSING_EVIDENCE"
U_FLOW_AMBIGUOUS = "PRODUCER_FLOW_AMBIGUOUS"
U_TARGET_NOT_FOUND = "FLOW_TARGET_NOT_FOUND"
U_TARGET_AMBIGUOUS = "FLOW_TARGET_AMBIGUOUS"
U_MAPPING_NOT_OBSERVED = "MAPPING_NOT_OBSERVED"
U_MAPPING_NOT_CLASSIFIABLE = "MAPPING_NOT_CLASSIFIABLE"
U_COMPONENTS_NOT_OBSERVED = "COMPONENTS_NOT_OBSERVED"
U_SHAPE_NOT_INTERPRETABLE = "FLOW_SHAPE_NOT_INTERPRETABLE"
U_CONTRADICTORY = "CONTRADICTORY_EVIDENCE"
U_MULTI_PRODUCER_NOT_COMPARABLE = "MULTI_PRODUCER_NOT_COMPARABLE"
UNKNOWN_REASON_CODES = frozenset({U_EVIDENCE_NOT_AVAILABLE, U_SECTION_MISSING, U_LEAF_NOT_FOUND, U_NO_PRODUCER, U_DECLARED_NOT_OBSERVED, U_TARGET_MATCH_NOT_EXACT, U_FLOW_KIND_UNSUPPORTED,
                                  U_FLOW_NOT_IN_EVIDENCE, U_FLOW_AMBIGUOUS, U_TARGET_NOT_FOUND, U_TARGET_AMBIGUOUS, U_MAPPING_NOT_OBSERVED, U_MAPPING_NOT_CLASSIFIABLE,
                                  U_COMPONENTS_NOT_OBSERVED, U_SHAPE_NOT_INTERPRETABLE, U_CONTRADICTORY, U_MULTI_PRODUCER_NOT_COMPARABLE})

# ── codigos de causa de UNDETERMINED (clase de borde) ──
B_NO_SECTION = "BOUNDARY_SECTION_MISSING"
B_NO_RECORD = "BOUNDARY_RECORD_MISSING"
B_NO_BOUNDARIES = "NO_TERMINAL_BOUNDARIES"
B_CHAIN_GAPS = "CHAIN_GAPS"
B_CHAIN_INCOMPLETE = "CHAIN_INCOMPLETE"
B_UNCLASSIFIED = "UNCLASSIFIED_BOUNDARY"

DATA_FLOW, REPLICATION_FLOW = "DATA_FLOW", "REPLICATION_FLOW"
EXACT = "EXACT"
_DF_CONSUMER, _DF_PRODUCER = "com.sap.database.table.consumer", "com.sap.database.table.producer"
_SKIP_MAPPING = "Skip Mapping"
_QUOTED_NAME = re.compile(r'^"([^"]+)"$')
_BASIS_DECLARED = frozenset({"DECLARED_DATASET", "TABLE_FUNCTION"})
_RANK = {KIND_REPLICA: 0, KIND_COPY: 1, KIND_TRANSFORMED: 2}


# ───────────────────────────── helpers defensivos (nunca lanzan, nunca mutan) ─────────────────────────────
def _map(x: Any) -> Mapping[str, Any]:
    return x if isinstance(x, Mapping) else {}


def _list(x: Any) -> list:
    return list(x) if isinstance(x, (list, tuple)) else []


def _dicts(x: Any) -> list[Mapping[str, Any]]:
    return [i for i in _list(x) if isinstance(i, Mapping)]


def _unique(records: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Registros ESTRICTAMENTE IDENTICOS repetidos (el mismo flujo/productor aparece mas de una vez en la evidencia persistida) cuentan una sola vez; registros distintos con la misma clave siguen siendo ambiguos."""
    seen: dict[str, Mapping[str, Any]] = {}
    for r in records:
        seen.setdefault(json.dumps(r, sort_keys=True, default=str), r)
    return list(seen.values())


def _strs(x: Any) -> list[str]:
    return [i for i in _list(x) if isinstance(i, str)]


def _empty_mapping_profile() -> dict[str, Any]:
    return {"identity": 0, "rename": 0, "rename_case_only": 0, "skip": 0, "expression": 0, "unknown": 0, "implicit_identity": False}


def _add_profiles(profiles: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    out = _empty_mapping_profile()
    for p in profiles:
        for k in ("identity", "rename", "rename_case_only", "skip", "expression", "unknown"):
            out[k] += int(p.get(k) or 0)
        out["implicit_identity"] = out["implicit_identity"] or bool(p.get("implicit_identity"))
    return out


# ───────────────────────────── mapeos ─────────────────────────────
def classify_mappings(attribute_mappings: Any) -> dict[str, Any]:
    """Perfil de los mapeos de atributos de UN destino. Cada mapeo ``{"target": T, "expression": E}`` es:
    ``skip`` (E = ``"Skip Mapping"``) · ``identity`` (E = ``"T"``, mismo identificador exacto) · ``rename`` (E = ``"X"`` con X != T; ``rename_case_only`` cuenta los que solo difieren en
    mayusculas, que NO se tratan como identidad) · ``expression`` (cualquier otra expresion) · ``unknown`` (sin objetivo o sin expresion textual). La lista vacia NO es un mapeo desconocido:
    ``implicit_identity`` = True (la plataforma mapea por nombre cuando no hay anulaciones; la bandera permite a un consumidor distinguirlo de una identidad declarada)."""
    out = _empty_mapping_profile()
    items = _list(attribute_mappings)
    if not items:
        out["implicit_identity"] = True
        return out
    for m in items:
        target = _map(m).get("target")
        expr = _map(m).get("expression")
        if not isinstance(target, str) or not target or not isinstance(expr, str) or not expr.strip():
            out["unknown"] += 1
            continue
        e = expr.strip()
        quoted = _QUOTED_NAME.match(e)
        if quoted and quoted.group(1) == _SKIP_MAPPING:
            out["skip"] += 1
        elif quoted and quoted.group(1) == target:
            out["identity"] += 1
        elif quoted:
            out["rename"] += 1
            if quoted.group(1).lower() == target.lower():
                out["rename_case_only"] += 1
        else:
            out["expression"] += 1
    return out


def _is_transforming(mp: Mapping[str, Any]) -> bool:
    return bool(mp["rename"] or mp["expression"])


# ───────────────────────────── un productor (un flujo) respecto de una hoja ─────────────────────────────
def classify_flow_kind(flow: Any, leaf: str) -> dict[str, Any]:
    """Clasifica lo que UN flujo (registro de ``processing_flow_properties.flows``) hace con la hoja ``leaf``. Devuelve ``materialization_kind``, ``unknown_reasons``, ``mapping_profile``,
    ``filtered``, ``write_mode`` (+ ``upsert``/``load_type``/``components``). La hoja se localiza por IDENTIDAD EXACTA del destino (tarea ``target_object`` / nodo ``role=target``)."""
    flow = _map(flow)
    kind = flow.get("flow_kind")
    props = _map(flow.get("properties"))
    res: dict[str, Any] = {"materialization_kind": KIND_UNKNOWN, "unknown_reasons": [], "mapping_profile": _empty_mapping_profile(), "filtered": None, "write_mode": None,
                           "upsert": None, "load_type": None, "components": []}
    if kind == REPLICATION_FLOW:
        tasks = [t for t in _dicts(props.get("tasks")) if t.get("target_object") == leaf]
        if not tasks:
            res["unknown_reasons"].append(U_TARGET_NOT_FOUND)
            return res
        if len(tasks) > 1:
            res["unknown_reasons"].append(U_TARGET_AMBIGUOUS)
            return res
        t = tasks[0]
        raw_mappings = t.get("attribute_mappings")
        mp = classify_mappings(raw_mappings) if isinstance(raw_mappings, (list, tuple)) else _empty_mapping_profile()
        res["mapping_profile"] = mp
        res["filtered"] = bool(_list(t.get("filters")))
        tr = t.get("truncate")
        res["write_mode"] = "truncate" if tr is True else ("no_truncate" if tr is False else "unspecified")
        lt = t.get("load_type")
        res["load_type"] = lt if isinstance(lt, str) else None
        if _is_transforming(mp):
            res["materialization_kind"] = KIND_TRANSFORMED
        elif not isinstance(raw_mappings, (list, tuple)):
            res["unknown_reasons"].append(U_MAPPING_NOT_OBSERVED)
        elif mp["unknown"]:
            res["unknown_reasons"].append(U_MAPPING_NOT_CLASSIFIABLE)
        else:
            res["materialization_kind"] = KIND_REPLICA
        return res
    if kind == DATA_FLOW:
        nodes = [n for n in _dicts(props.get("nodes")) if n.get("role") == "target" and n.get("entity") == leaf]
        if not nodes:
            res["unknown_reasons"].append(U_TARGET_NOT_FOUND)
            return res
        if len(nodes) > 1:
            res["unknown_reasons"].append(U_TARGET_AMBIGUOUS)
            return res
        n = nodes[0]
        raw_mappings = n.get("attribute_mappings")
        mp = classify_mappings(raw_mappings) if isinstance(raw_mappings, (list, tuple)) else _empty_mapping_profile()
        mapping_observed = isinstance(raw_mappings, (list, tuple)) and bool(raw_mappings)
        mp["implicit_identity"] = False                      # en un Data Flow una lista vacia NO prueba identidad: es evidencia ausente
        res["mapping_profile"] = mp
        sf = flow.get("source_filters")
        res["filtered"] = (any(f.get("where") or f.get("having") for f in _dicts(sf)) if isinstance(sf, (list, tuple)) else None)
        mode = n.get("mode")
        res["write_mode"] = mode if isinstance(mode, str) and mode else "unspecified"
        res["upsert"] = n.get("upsert") if isinstance(n.get("upsert"), bool) else None
        comps = {c["component"]: int(c.get("count") or 0) for c in _dicts(props.get("component_counts")) if isinstance(c.get("component"), str)}
        res["components"] = sorted(comps)
        others = sorted(c for c in comps if c not in (_DF_CONSUMER, _DF_PRODUCER))
        if _is_transforming(mp) or others:                     # la transformacion se DEMUESTRA por un mapeo no identidad o por un componente distinto de consumer/producer
            res["materialization_kind"] = KIND_TRANSFORMED
        elif not comps:
            res["unknown_reasons"].append(U_COMPONENTS_NOT_OBSERVED)
        elif comps.get(_DF_CONSUMER) != 1 or comps.get(_DF_PRODUCER) != 1:
            res["unknown_reasons"].append(U_SHAPE_NOT_INTERPRETABLE)
        elif not mapping_observed:
            res["unknown_reasons"].append(U_MAPPING_NOT_OBSERVED)
        elif mp["unknown"]:
            res["unknown_reasons"].append(U_MAPPING_NOT_CLASSIFIABLE)
        else:
            res["materialization_kind"] = KIND_COPY
        return res
    res["unknown_reasons"].append(U_FLOW_KIND_UNSUPPORTED)
    return res


# ───────────────────────────── clase de borde derivada ─────────────────────────────
def derive_boundary_class(boundaries: Any, chain_gaps: Any = (), chain_complete: Any = True,
                          governed_classes: frozenset[str] = GOVERNED_BOUNDARY_CLASSES, physical_classes: frozenset[str] = PHYSICAL_BOUNDARY_CLASSES) -> tuple[str, dict[str, int], list[str]]:
    """``(clase, composicion, causas)`` a partir de los bordes terminales YA persistidos de una hoja. ``UNDETERMINED`` si no hay bordes, algun borde es ``UNCLASSIFIED`` o de clase desconocida,
    hay ``chain_gaps`` o la cadena no esta completa (``composition`` se informa igual: lo observado hasta el hueco)."""
    bs = _dicts(boundaries)
    composition: dict[str, int] = {}
    for b in bs:
        c = b.get("boundary_class")
        c = c if isinstance(c, str) and c else BC_UNCLASSIFIED
        composition[c] = composition.get(c, 0) + 1
    composition = dict(sorted(composition.items()))
    reasons: list[str] = []
    if not bs:
        reasons.append(B_NO_BOUNDARIES)
    if _list(chain_gaps):
        reasons.append(B_CHAIN_GAPS)
    if chain_complete is not True:
        reasons.append(B_CHAIN_INCOMPLETE)
    if any(c not in governed_classes and c not in physical_classes for c in composition):
        reasons.append(B_UNCLASSIFIED)
    if reasons:
        return DBC_UNDETERMINED, composition, sorted(set(reasons))
    gov = any(c in governed_classes for c in composition)
    phy = any(c in physical_classes for c in composition)
    return (DBC_MIXED if gov and phy else DBC_GOVERNED if gov else DBC_PHYSICAL), composition, []


# ───────────────────────────── derivacion por hoja ─────────────────────────────
def _missing_sections(ce: Mapping[str, Any]) -> list[str]:
    """Secciones requeridas ausentes o de forma no interpretable (perfiles v1/v2 historicos)."""
    pf = ce.get("processing_flow_properties")
    miss = []
    if not isinstance(ce.get("leaf_provenance"), (list, tuple)):
        miss.append("leaf_provenance")
    if not (isinstance(pf, Mapping) and isinstance(pf.get("flows"), (list, tuple))):
        miss.append("processing_flow_properties")
    if not isinstance(ce.get("physical_source_boundaries"), Mapping):
        miss.append("physical_source_boundaries")
    return sorted(miss)


def _skeleton(leaf: str) -> dict[str, Any]:
    return {"leaf": leaf, "version": LEAF_MATERIALIZATION_VERSION, "materialization_kind": KIND_UNKNOWN, "unknown_reasons": [], "unknown_detail": {},
            "flags": {"multi_producer": False, "filtered": None, "writes": []},
            "derived_boundary_class": DBC_UNDETERMINED, "boundary_reasons": [], "composition": {}, "terminal_boundaries": [], "chain_complete": None, "chain_gaps": [],
            "producers": [], "mapping_profile": _empty_mapping_profile(), "evidence_refs": []}


def _unknown(leaf: str, code: str, detail: list[str] | None = None, boundary_code: str | None = None) -> dict[str, Any]:
    out = _skeleton(leaf)
    out["unknown_reasons"] = [code]
    if detail:
        out["unknown_detail"] = {code: sorted(detail)}
    if boundary_code:
        out["boundary_reasons"] = [boundary_code]
    return out


def derive_leaf_materialization(leaf: str, ce: Any) -> dict[str, Any]:
    """Materializacion observada de UNA hoja sobre ``canonical_evidence`` (dict ya persistido; solo lectura). Nunca lanza ni muta; ver el docstring del modulo."""
    if not isinstance(leaf, str) or not isinstance(ce, Mapping) or not ce:
        return _unknown(leaf if isinstance(leaf, str) else "", U_EVIDENCE_NOT_AVAILABLE, boundary_code=B_NO_SECTION)
    missing = _missing_sections(ce)
    if missing:
        return _unknown(leaf, U_SECTION_MISSING, missing, B_NO_SECTION)
    lp = next((r for r in _dicts(ce["leaf_provenance"]) if r.get("leaf") == leaf), None)
    pb_leaf = next((r for r in _dicts(_map(ce["physical_source_boundaries"]).get("leaves")) if r.get("leaf") == leaf), None)
    out = _skeleton(leaf)
    refs = []
    if lp is not None:
        refs.append(f"ce:leaf_provenance/{leaf}")
    if pb_leaf is not None:
        refs.append(f"ce:physical_source_boundaries/{leaf}")
    out["evidence_refs"] = refs

    # clase de borde derivada (independiente del kind)
    if pb_leaf is None:
        out["boundary_reasons"] = [B_NO_RECORD]
    else:
        cls, comp, why = derive_boundary_class(pb_leaf.get("boundaries"), pb_leaf.get("chain_gaps"), pb_leaf.get("chain_complete"))
        out["derived_boundary_class"], out["composition"], out["boundary_reasons"] = cls, comp, why
        out["terminal_boundaries"] = [{k: b.get(k) for k in ("boundary_entity", "remote_connection", "remote_entity", "boundary_class", "basis")} | {"via_flows": sorted(_strs(b.get("via_flows")))}
                                      for b in sorted(_dicts(pb_leaf.get("boundaries")), key=lambda b: (str(b.get("remote_entity")), str(b.get("boundary_entity")), str(b.get("boundary_class"))))]
        out["chain_complete"] = pb_leaf.get("chain_complete") if isinstance(pb_leaf.get("chain_complete"), bool) else None
        out["chain_gaps"] = sorted((dict(g) if isinstance(g, Mapping) else g for g in _list(pb_leaf.get("chain_gaps"))), key=repr)

    reasons: set[str] = set()
    detail: dict[str, set[str]] = {}

    def fail(code: str, what: str = "") -> None:
        reasons.add(code)
        if what:
            detail.setdefault(code, set()).add(what)

    if lp is None:
        fail(U_LEAF_NOT_FOUND, leaf)
        return _finish(out, reasons, detail)
    prods = sorted(_unique(_dicts(lp.get("producer_flows"))), key=lambda p: (str(p.get("kind")), str(p.get("name"))))
    if not prods:
        fail(U_DECLARED_NOT_OBSERVED if lp.get("basis") in _BASIS_DECLARED else U_NO_PRODUCER, str(lp.get("basis")))
        return _finish(out, reasons, detail)

    flows = _dicts(_map(ce["processing_flow_properties"]).get("flows"))
    out["evidence_refs"].append("ce:processing_flow_properties")
    results = []
    for p in prods:
        name = p.get("name") if isinstance(p.get("name"), str) else None
        pkind = p.get("kind") if isinstance(p.get("kind"), str) else None
        match = p.get("target_match") if isinstance(p.get("target_match"), str) else None
        entry: dict[str, Any] = {"kind": pkind, "name": name, "target_match": match, "load_types": sorted(_strs(p.get("load_types"))), "materialization_kind": KIND_UNKNOWN,
                                 "unknown_reasons": [], "mapping_profile": _empty_mapping_profile(), "filtered": None, "write_mode": None, "upsert": None, "components": []}
        if name is None or pkind is None:
            entry["unknown_reasons"].append(U_FLOW_NOT_IN_EVIDENCE)
            fail(U_FLOW_NOT_IN_EVIDENCE, "unparseable_producer")
        elif match != EXACT:
            entry["unknown_reasons"].append(U_TARGET_MATCH_NOT_EXACT)
            fail(U_TARGET_MATCH_NOT_EXACT, f"{name}:{match}")
        else:
            cands = _unique([f for f in flows if f.get("flow_name") == name and f.get("flow_kind") == pkind])
            if not cands:
                entry["unknown_reasons"].append(U_FLOW_NOT_IN_EVIDENCE)
                fail(U_FLOW_NOT_IN_EVIDENCE, str(name))
            elif len(cands) > 1:
                entry["unknown_reasons"].append(U_FLOW_AMBIGUOUS)
                fail(U_FLOW_AMBIGUOUS, str(name))
            else:
                r = classify_flow_kind(cands[0], leaf)
                entry.update({k: r[k] for k in ("materialization_kind", "unknown_reasons", "mapping_profile", "filtered", "write_mode", "upsert", "components")})
                entry["load_type"] = r["load_type"]
                for code in r["unknown_reasons"]:
                    fail(code, str(name))
        results.append(entry)
    out["producers"] = results

    exact = {(e["kind"], e["name"]) for e in results if e["target_match"] == EXACT}
    out["flags"]["multi_producer"] = len(exact) >= 2
    fl = [e["filtered"] for e in results]
    out["flags"]["filtered"] = True if any(x is True for x in fl) else (None if any(x is None for x in fl) else False)
    out["flags"]["writes"] = sorted({e["write_mode"] for e in results if e["write_mode"]})
    out["mapping_profile"] = _add_profiles(e["mapping_profile"] for e in results)

    # coherencia entre fuentes de evidencia: los productores deben figurar entre los flujos que recorren los bordes de la hoja
    if pb_leaf is not None:
        walked = set(_strs(pb_leaf.get("flows")))
        stray = sorted(e["name"] for e in results if e["name"] is not None and e["name"] not in walked)
        if stray:
            fail(U_CONTRADICTORY, "producers_not_in_boundary_flows:" + ",".join(stray))

    if not reasons:
        kinds = [e["materialization_kind"] for e in results]
        top = max(kinds, key=_RANK.__getitem__)
        if out["flags"]["multi_producer"] and top == KIND_REPLICA:
            fail(U_MULTI_PRODUCER_NOT_COMPARABLE, "replica_producers:" + ",".join(sorted(str(e["name"]) for e in results)))   # N replicadores sobre un mismo destino: la identidad conjunta no se verifica
        else:
            out["materialization_kind"] = top
    return _finish(out, reasons, detail)


def _finish(out: dict[str, Any], reasons: set[str], detail: dict[str, set[str]]) -> dict[str, Any]:
    if reasons:
        out["materialization_kind"] = KIND_UNKNOWN
        out["unknown_reasons"] = sorted(reasons)
        out["unknown_detail"] = {k: sorted(v) for k, v in sorted(detail.items())}
    return out


def derive_all(ce: Any) -> dict[str, dict[str, Any]]:
    """``{hoja: derive_leaf_materialization}`` para toda hoja con registro de procedencia o de borde, ordenado por nombre. ``{}`` si no hay evidencia o es historica."""
    if not isinstance(ce, Mapping) or not ce or _missing_sections(ce):
        return {}
    names = {r["leaf"] for r in _dicts(ce.get("leaf_provenance")) if isinstance(r.get("leaf"), str)}
    names |= {r["leaf"] for r in _dicts(_map(ce.get("physical_source_boundaries")).get("leaves")) if isinstance(r.get("leaf"), str)}
    return {leaf: derive_leaf_materialization(leaf, ce) for leaf in sorted(names)}
