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
  REPLICA      todos los productores EXACT son Replication Flow y todo mapeo observado de la hoja es identidad o ``Skip Mapping`` (columna destino SIN alimentar; sin mapeos explicitos =
               identidad por defecto: ``attributeMappings`` es una lista de ANULACIONES, contrato verificado contra el CSN crudo de los Replication Flow disponibles; ``implicit_identity`` lo conserva)
  COPY         el productor es un Data Flow cuyos unicos componentes son ``table.consumer`` + ``table.producer`` (1 y 1) y todo mapeo de la hoja es identidad o ``Skip Mapping``
  TRANSFORMED  se demuestra una transformacion TECNICA (no necesariamente armonizacion semantica; no acredita fortalezas HAR): mapeo de renombre o de expresion, o cualquier componente de
               Data Flow distinto de consumer/producer (projection, join, agregacion, ...)
  UNKNOWN      productor ausente / no interpretable, ``target_match`` distinto de EXACT, mapeo no clasificable, forma de flujo no interpretable, evidencia contradictoria, multiples productores
               no comparables o secciones ausentes (siempre con ``unknown_reasons``)
Evidencia CSN OPCIONAL (``csn``; sin ella el resultado depende solo del perfil): ``delta_links`` (vinculos EXPLICITOS ``X.@DataWarehouse.delta.deltaFromEntities`` -> ``D``) es la unica via para
resolver un productor ``DELTA_SHADOW_SUFFIX`` (el sufijo es solo una pista); ``rf_task_columns`` verifica por tarea la identidad implicita y la equivalencia de un renombre SOLO de mayusculas
(identidad normalizada solo con columnas exactas, sin colision y origen CDS; sin casefold universal).
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
U_IMPLICIT_IDENTITY_CONTRADICTED = "IMPLICIT_IDENTITY_CONTRADICTED"
U_DELTA_WRAPPER_NOT_VERIFIABLE = "DELTA_WRAPPER_NOT_VERIFIABLE"
UNKNOWN_REASON_CODES = frozenset({U_EVIDENCE_NOT_AVAILABLE, U_SECTION_MISSING, U_LEAF_NOT_FOUND, U_NO_PRODUCER, U_DECLARED_NOT_OBSERVED, U_TARGET_MATCH_NOT_EXACT, U_FLOW_KIND_UNSUPPORTED,
                                  U_FLOW_NOT_IN_EVIDENCE, U_FLOW_AMBIGUOUS, U_TARGET_NOT_FOUND, U_TARGET_AMBIGUOUS, U_MAPPING_NOT_OBSERVED, U_MAPPING_NOT_CLASSIFIABLE,
                                  U_COMPONENTS_NOT_OBSERVED, U_SHAPE_NOT_INTERPRETABLE, U_CONTRADICTORY, U_MULTI_PRODUCER_NOT_COMPARABLE, U_IMPLICIT_IDENTITY_CONTRADICTED, U_DELTA_WRAPPER_NOT_VERIFIABLE})

# ── codigos de causa de UNDETERMINED (clase de borde) ──
B_NO_SECTION = "BOUNDARY_SECTION_MISSING"
B_NO_RECORD = "BOUNDARY_RECORD_MISSING"
B_NO_BOUNDARIES = "NO_TERMINAL_BOUNDARIES"
B_CHAIN_GAPS = "CHAIN_GAPS"
B_CHAIN_INCOMPLETE = "CHAIN_INCOMPLETE"
B_UNCLASSIFIED = "UNCLASSIFIED_BOUNDARY"

DATA_FLOW, REPLICATION_FLOW = "DATA_FLOW", "REPLICATION_FLOW"
DELTA_SHADOW_SUFFIX_MATCH = "DELTA_SHADOW_SUFFIX"                      # ``target_match`` persistido: PISTA por nombre; solo un vinculo CSN EXPLICITO la convierte en relacion
RESOLVED_VIA_DELTA_LINK = "CSN_DELTA_LINK"
_DELTA_ANNOTATION = "@DataWarehouse.delta"
# Evidencia de columnas del CSN crudo de un Replication Flow (estado por tarea)
COLUMN_EVIDENCE_VERIFIED, COLUMN_EVIDENCE_CONTRADICTED, COLUMN_EVIDENCE_NOT_PROVIDED = "VERIFIED", "CONTRADICTED", "NOT_PROVIDED"
# Premisa de IDENTIFICADORES (RATIFICADA por el owner con la documentacion oficial de SAP: los identificadores ABAP CDS son insensibles a mayusculas; NO se extiende a identificadores SQL HANA entre comillas). Un renombre que solo cambia mayusculas
# se trata como identidad normalizada UNICAMENTE si el origen de la tarea es una CDS gobernada, hay evidencia de columnas sin colision y el origen/destino existen EXACTAMENTE.
CASE_INSENSITIVE_SOURCE_BOUNDARY_CLASSES = frozenset({BC_GOVERNED_CDS})
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
    return {"identity": 0, "identity_normalized": 0, "rename": 0, "rename_case_only": 0, "skip": 0, "expression": 0, "unknown": 0, "implicit_identity": False,
            "column_evidence": COLUMN_EVIDENCE_NOT_PROVIDED}


def _add_profiles(profiles: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    out = _empty_mapping_profile()
    states: list[Any] = []
    for p in profiles:
        for k in ("identity", "identity_normalized", "rename", "rename_case_only", "skip", "expression", "unknown"):
            out[k] += int(p.get(k) or 0)
        out["implicit_identity"] = out["implicit_identity"] or bool(p.get("implicit_identity"))
        states.append(p.get("column_evidence"))
    if states and COLUMN_EVIDENCE_CONTRADICTED in states:
        out["column_evidence"] = COLUMN_EVIDENCE_CONTRADICTED
    elif states and all(s == COLUMN_EVIDENCE_VERIFIED for s in states):
        out["column_evidence"] = COLUMN_EVIDENCE_VERIFIED
    return out


# ───────────────────────────── mapeos ─────────────────────────────
def classify_mappings(attribute_mappings: Any, case_equivalent: frozenset = frozenset()) -> dict[str, Any]:
    """Perfil de los mapeos de atributos de UN destino. Cada mapeo ``{"target": T, "expression": E}`` es:
    ``skip`` (E = ``"Skip Mapping"``) · ``identity`` (E = ``"T"``, mismo identificador exacto) · ``rename`` (E = ``"X"`` con X != T; ``rename_case_only`` cuenta los que solo difieren en
    mayusculas; ``rename`` solo cuenta los NO verificados) · ``identity_normalized`` (renombre solo-mayusculas cuyo par ``(target, origen)`` figura en ``case_equivalent``: equivalencia
    COMPROBADA por el llamador, nunca un casefold universal; ``rename_case_only`` sigue contando TODOS los solo-mayusculas) · ``expression`` (cualquier otra expresion) · ``unknown`` (sin objetivo o sin expresion textual). La lista vacia NO es un mapeo desconocido:
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
            case_only = quoted.group(1).lower() == target.lower()
            out["rename_case_only"] += int(case_only)
            if case_only and (target, quoted.group(1)) in case_equivalent:
                out["identity_normalized"] += 1
            else:
                out["rename"] += 1
        else:
            out["expression"] += 1
    return out


def _is_transforming(mp: Mapping[str, Any]) -> bool:
    return bool(mp["rename"] or mp["expression"])


# ───────────────────────────── evidencia CSN OPCIONAL (vinculos delta explicitos, columnas de Replication Flow) ─────────────────────────────
def explicit_delta_links(definitions: Any) -> dict[str, tuple[str, ...]]:
    """``{X: (D, ...)}`` a partir de las ``definitions`` de un CSN YA CARGADO: solo lo que la entidad ``X`` DECLARA en ``@DataWarehouse.delta.deltaFromEntities`` (vinculo EXPLICITO). Nunca por
    sufijo, similitud de nombres ni convencion. No verifica el acuerdo estructural ``X`` lee ``D`` (eso lo hace la extraccion, ``technical_delta_shadow_entities``): es un insumo, no un veredicto."""
    out: dict[str, tuple[str, ...]] = {}
    for x, xd in _map(definitions).items():
        dfe = _map(_map(xd).get(_DELTA_ANNOTATION)).get("deltaFromEntities")
        ds = sorted({d for d in _list(dfe) if isinstance(d, str) and d and d != x})
        if isinstance(x, str) and ds:
            out[x] = tuple(ds)
    return out


_WRAPPER_SELECT_KEYS = frozenset({"from", "columns", "where", "orderBy", "groupBy", "having", "distinct", "limit"})
_TYPE_KEYS = ("type", "length", "precision", "scale")


def _from_refs(frm: Any) -> list[str]:
    """Nombres de entidad referenciados por un ``from`` CSN (ref simple o arbol de joins)."""
    if not isinstance(frm, Mapping):
        return []
    out = [r if isinstance(r, str) else _map(r).get("id") for r in _list(frm.get("ref"))[:1]]
    for arg in _list(frm.get("args")) if "join" in frm else []:
        out += _from_refs(arg)
    return [r for r in out if isinstance(r, str)]


def _where_columns(node: Any) -> set[str]:
    cols: set[str] = set()
    if isinstance(node, Mapping):
        ref = node.get("ref")
        if isinstance(ref, (list, tuple)) and ref and isinstance(ref[-1], str):
            cols.add(ref[-1])
        for v in node.values():
            cols |= _where_columns(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            cols |= _where_columns(v)
    return cols


def verify_delta_wrapper(leaf: str, shadow: str, definitions: Any, cdc_columns: Iterable[str] = (), shadow_columns: Any = None) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """Verifica, SOLO con el CSN ya cargado, que el wrapper logico ``leaf`` (X) es una PROYECCION de su companera delta ``shadow`` (D) y que no hay transformacion relevante.
    Devuelve ``(info, razones)``; ``razones`` = ``[(codigo, detalle)]`` (vacia = verificado). ``info``: ``reads_shadow``, ``projection`` (``COLUMNS_ONLY`` | ``NOT_PURE``),
    ``transformations`` (``RENAME`` | ``EXPRESSION`` | ``TYPE_CHANGE`` | ``JOIN`` | ``AGGREGATION`` | ``DISTINCT`` | ``LIMIT`` | ``SET_OPERATION``), ``filtered`` / ``filter_columns`` /
    ``filter_nature`` (``CDC_ROW_STATE`` si el filtro solo mira columnas CDC declaradas; ``ROW_FILTER`` en otro caso: un filtro de filas NO es una transformacion de valores) y
    ``column_identity`` (``VERIFIED_DEFINITION`` con tipos contra la definicion de D | ``VERIFIED_RF_TARGET_NAMES`` solo nombres contra el destino del RF | ``NOT_VERIFIABLE``).
    Nada se deduce por sufijo ni nombre: X debe LEER a D en su ``query.SELECT.from``. Sin definicion de X o sin ``SELECT`` => no verificable."""
    info: dict[str, Any] = {"entity": leaf, "shadow_entity": shadow, "reads_shadow": None, "projection": None, "transformations": [], "filtered": False, "filter_columns": [],
                            "filter_nature": None, "column_identity": "NOT_VERIFIABLE"}
    reasons: list[tuple[str, str]] = []
    defs = _map(definitions)
    xd = defs.get(leaf)
    if not isinstance(xd, Mapping):
        return info, [(U_DELTA_WRAPPER_NOT_VERIFIABLE, "wrapper_definition_missing")]
    q = _map(xd.get("query"))
    sel = q.get("SELECT")
    if not isinstance(sel, Mapping):
        if "SET" in q:
            info["transformations"].append("SET_OPERATION")
            info["projection"] = "NOT_PURE"
            return info, []
        return info, [(U_DELTA_WRAPPER_NOT_VERIFIABLE, "wrapper_has_no_select")]
    unknown_keys = sorted(k for k in sel if k not in _WRAPPER_SELECT_KEYS)
    if unknown_keys:
        return info, [(U_DELTA_WRAPPER_NOT_VERIFIABLE, "wrapper_select_unrecognized:" + ",".join(map(str, unknown_keys)))]
    frm = sel.get("from")
    refs = _from_refs(frm)
    if shadow not in refs:
        info["reads_shadow"] = False
        return info, [(U_CONTRADICTORY, f"wrapper_does_not_read_shadow:{leaf}<-{shadow}")]       # X declara a D pero no la lee: evidencia contradictoria
    info["reads_shadow"] = True
    if isinstance(frm, Mapping) and "join" in frm:
        info["transformations"].append("JOIN")
    elif not (isinstance(frm, Mapping) and set(frm) <= {"ref", "as"} and len(_list(frm.get("ref"))) == 1):
        return info, [(U_DELTA_WRAPPER_NOT_VERIFIABLE, "wrapper_from_not_interpretable")]
    sources: list[str] = []
    for c in (sel.get("columns") if isinstance(sel.get("columns"), (list, tuple)) else ["*"]):
        if c == "*":
            continue
        ref = _map(c).get("ref") if isinstance(c, Mapping) else None
        if isinstance(c, Mapping) and set(c) <= {"ref", "as"} and isinstance(ref, (list, tuple)) and ref and all(isinstance(r, str) for r in ref) and len(ref) <= 2:
            sources.append(ref[-1])
            if isinstance(c.get("as"), str) and c["as"] != ref[-1]:
                info["transformations"].append("RENAME")
        else:
            info["transformations"].append("EXPRESSION")
    for key, label in (("groupBy", "AGGREGATION"), ("having", "AGGREGATION"), ("distinct", "DISTINCT"), ("limit", "LIMIT")):
        if sel.get(key):
            info["transformations"].append(label)
    if "where" in sel:
        wc = sorted(_where_columns(sel.get("where")))
        info["filtered"], info["filter_columns"] = True, wc
        info["filter_nature"] = "CDC_ROW_STATE" if wc and set(wc) <= set(cdc_columns) else "ROW_FILTER"
    info["transformations"] = sorted(set(info["transformations"]))
    info["projection"] = "COLUMNS_ONLY" if not info["transformations"] else "NOT_PURE"
    # identidad de columnas "cuando la definicion lo permita"
    dd = _map(defs.get(shadow))
    d_els = _map(dd.get("elements"))
    x_els = _map(xd.get("elements"))
    if d_els and sources:
        missing = sorted(s for s in set(sources) if s not in d_els)
        if missing:
            reasons.append((U_CONTRADICTORY, "wrapper_columns_not_in_shadow:" + ",".join(missing)))
        else:
            info["column_identity"] = "VERIFIED_DEFINITION"
            if any(_map(x_els.get(s)) and any(_map(x_els[s]).get(k) != _map(d_els[s]).get(k) for k in _TYPE_KEYS) for s in set(sources)):
                info["transformations"] = sorted(set(info["transformations"]) | {"TYPE_CHANGE"})
                info["projection"] = "NOT_PURE"
    elif shadow_columns is not None and sources:
        have = set(_strs(shadow_columns))
        if have:
            missing = sorted(s for s in set(sources) if s not in have)
            if missing:
                reasons.append((U_CONTRADICTORY, "wrapper_columns_not_in_shadow:" + ",".join(missing)))
            else:
                info["column_identity"] = "VERIFIED_RF_TARGET_NAMES"
    return info, reasons


def _column_names(obj: Any) -> list[str]:
    return [c["name"] for c in _dicts(_map(_map(obj).get("definition")).get("columns")) if isinstance(c.get("name"), str)]


def rf_task_columns(replication_flows: Any) -> dict[tuple[str, str], dict[str, list[str]]]:
    """``{(flujo, destino): {"source_columns": [...], "target_columns": [...]}}`` a partir del bloque ``replicationflows`` de un CSN YA CARGADO (columnas del objeto origen y del objeto destino de cada
    tarea). Las tareas sin definiciones de columnas, o con la misma clave y contenido distinto (ambiguas), se omiten: ausencia de evidencia, no evidencia."""
    out: dict[tuple[str, str], dict[str, list[str]]] = {}
    dropped: set[tuple[str, str]] = set()
    for fname, fd in _map(replication_flows).items():
        for task in _dicts(_map(_map(fd).get("contents")).get("replicationTasks")):
            tgt_name = _map(task.get("targetObject")).get("name")
            src, tgt = _column_names(task.get("sourceObject")), _column_names(task.get("targetObject"))
            if not isinstance(fname, str) or not isinstance(tgt_name, str) or not src or not tgt:
                continue
            key, val = (fname, tgt_name), {"source_columns": src, "target_columns": tgt}
            if key in out and out[key] != val:
                dropped.add(key)
            out.setdefault(key, val)
    return {k: v for k, v in out.items() if k not in dropped}


def _has_columns(columns: Any) -> bool:
    return bool(_strs(_map(columns).get("source_columns"))) and bool(_strs(_map(columns).get("target_columns")))


def _case_only_pairs(mappings: Any) -> list[tuple[str, str]]:
    out = []
    for m in _dicts(mappings):
        target, expr = m.get("target"), m.get("expression")
        q = _QUOTED_NAME.match(expr.strip()) if isinstance(expr, str) else None
        if isinstance(target, str) and q and q.group(1) not in (_SKIP_MAPPING, target) and q.group(1).lower() == target.lower():
            out.append((target, q.group(1)))
    return out


def _task_column_contradictions(task: Mapping[str, Any], columns: Mapping[str, Any]) -> list[str]:
    """Contradicciones entre la lista de mapeos de la tarea y las columnas crudas. Contrato verificado: ``attributeMappings`` es una lista de ANULACIONES; toda columna destino debe estar
    (i) mapeada explicitamente (``Skip Mapping`` = columna destino SIN alimentar), (ii) alimentada por la columna origen HOMONIMA (nombre exacto) o (iii) ser una columna CDC declarada por las
    propiedades de captura de cambios del propio destino; y el origen de todo mapeo ``"X"`` debe existir exactamente."""
    src, tgt = set(_strs(columns.get("source_columns"))), set(_strs(columns.get("target_columns")))
    maps = _dicts(task.get("attribute_mappings"))
    mapped_targets = {m["target"] for m in maps if isinstance(m.get("target"), str)}
    cdc = {v for v in _map(task.get("change_capture_properties")).values() if isinstance(v, str)}
    sources = []
    for m in maps:
        e = m.get("expression")
        q = _QUOTED_NAME.match(e.strip()) if isinstance(e, str) else None
        if q and q.group(1) != _SKIP_MAPPING:
            sources.append(q.group(1))
    return sorted([f"target_column_without_origin:{c}" for c in tgt - mapped_targets - src - cdc] + [f"mapped_source_missing:{s}" for s in set(sources) if s not in src])


def _verified_case_pairs(pairs: list[tuple[str, str]], columns: Mapping[str, Any]) -> frozenset:
    """Pares (destino, origen) solo-mayusculas con equivalencia COMPROBADA en las columnas crudas: ambos existen EXACTAMENTE y ninguna otra columna del mismo objeto se distingue solo por
    mayusculas (colision). Se evalua PAR A PAR; no se aplica casefold a nada mas."""
    src, tgt = _strs(columns.get("source_columns")), _strs(columns.get("target_columns"))
    ok = set()
    for t_name, s_name in pairs:
        collides = any(c != s_name and c.lower() == s_name.lower() for c in src) or any(c != t_name and c.lower() == t_name.lower() for c in tgt)
        if s_name in src and t_name in tgt and not collides:
            ok.add((t_name, s_name))
    return frozenset(ok)


# ───────────────────────────── un productor (un flujo) respecto de una hoja ─────────────────────────────
def classify_flow_kind(flow: Any, leaf: str, target: str | None = None, columns: Any = None, case_premise: bool = False) -> dict[str, Any]:
    """Clasifica lo que UN flujo (registro de ``processing_flow_properties.flows``) hace con la hoja ``leaf``. Devuelve ``materialization_kind``, ``unknown_reasons``, ``mapping_profile``,
    ``filtered``, ``write_mode`` (+ ``upsert``/``load_type``/``components``/``detail``). La hoja se localiza por IDENTIDAD EXACTA del destino (tarea ``target_object`` / nodo ``role=target``);
    ``target`` (por defecto ``leaf``) permite pasar la entidad fisica delta ``D`` ya resuelta por un vinculo CSN explicito. ``columns`` = columnas crudas de la tarea (opcional) y
    ``case_premise`` = el origen es una CDS gobernada (premisa de identificadores): solo con ambos un renombre solo-mayusculas verificado cuenta como identidad normalizada."""
    target = leaf if target is None else target
    flow = _map(flow)
    kind = flow.get("flow_kind")
    props = _map(flow.get("properties"))
    res: dict[str, Any] = {"materialization_kind": KIND_UNKNOWN, "unknown_reasons": [], "mapping_profile": _empty_mapping_profile(), "filtered": None, "write_mode": None,
                           "upsert": None, "load_type": None, "components": [], "detail": {}}
    if kind == REPLICATION_FLOW:
        tasks = [t for t in _dicts(props.get("tasks")) if t.get("target_object") == target]
        if not tasks:
            res["unknown_reasons"].append(U_TARGET_NOT_FOUND)
            return res
        if len(tasks) > 1:
            res["unknown_reasons"].append(U_TARGET_AMBIGUOUS)
            return res
        t = tasks[0]
        raw_mappings = t.get("attribute_mappings")
        contradictions: list[str] = []
        verified: frozenset = frozenset()
        have_cols = isinstance(raw_mappings, (list, tuple)) and _has_columns(columns)
        if have_cols:
            contradictions = _task_column_contradictions(t, columns)
            if case_premise:
                verified = _verified_case_pairs(_case_only_pairs(raw_mappings), columns)
        mp = classify_mappings(raw_mappings, verified) if isinstance(raw_mappings, (list, tuple)) else _empty_mapping_profile()
        if have_cols:
            mp["column_evidence"] = COLUMN_EVIDENCE_CONTRADICTED if contradictions else COLUMN_EVIDENCE_VERIFIED
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
        elif contradictions:                                   # las columnas crudas CONTRADICEN la lectura "anulaciones + identidad por nombre": no se certifica la replica
            res["unknown_reasons"].append(U_IMPLICIT_IDENTITY_CONTRADICTED)
            res["detail"][U_IMPLICIT_IDENTITY_CONTRADICTED] = contradictions[:8]
        elif mp["unknown"]:
            res["unknown_reasons"].append(U_MAPPING_NOT_CLASSIFIABLE)
        else:
            res["materialization_kind"] = KIND_REPLICA
        return res
    if kind == DATA_FLOW:
        nodes = [n for n in _dicts(props.get("nodes")) if n.get("role") == "target" and n.get("entity") == target]
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


def _cds_source_premise(pb_leaf: Mapping[str, Any] | None, flow_name: str) -> bool:
    """El origen de la tarea es (solo) una CDS gobernada: todo borde terminal alcanzado POR ese flujo es de una clase de ``CASE_INSENSITIVE_SOURCE_BOUNDARY_CLASSES`` (y hay al menos uno)."""
    bs = [b for b in _dicts(_map(pb_leaf).get("boundaries")) if flow_name in _strs(b.get("via_flows"))]
    return bool(bs) and all(b.get("boundary_class") in CASE_INSENSITIVE_SOURCE_BOUNDARY_CLASSES for b in bs)


def derive_leaf_materialization(leaf: str, ce: Any, csn: Any = None) -> dict[str, Any]:
    """Materializacion observada de UNA hoja sobre ``canonical_evidence`` (dict ya persistido; solo lectura). Nunca lanza ni muta; ver el docstring del modulo.

    ``csn`` (OPCIONAL, ya cargado por el llamador; sin el, el resultado depende solo del perfil persistido): ``{"entity_definitions": {nombre: definicion CSN}, "delta_links": {X: (D, ...)},
    "rf_task_columns": {(flujo, destino): {...}}}`` (ver ``explicit_delta_links`` / ``rf_task_columns`` / ``verify_delta_wrapper``). Un productor con ``target_match = DELTA_SHADOW_SUFFIX`` (pista por
    nombre) se resuelve SOLO si (1) la hoja ``X`` DECLARA a ``D`` (``delta_links``, o derivado de ``entity_definitions``), (2) la tarea escribe EXACTAMENTE en ``D`` y (3) ``verify_delta_wrapper``
    comprueba con el CSN que ``X`` LEE a ``D`` como proyeccion: sin la definicion de ``X`` => ``DELTA_WRAPPER_NOT_VERIFIABLE``; ``X`` no lee ``D`` => ``CONTRADICTORY_EVIDENCE``; una transformacion
    demostrada en ``X`` => ``TRANSFORMED``; un filtro de filas => ``filtered`` (no cambia el tipo). ``rf_task_columns`` verifica por tarea la identidad implicita y la equivalencia solo-mayusculas."""
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
    csn = _map(csn)
    entity_defs = _map(csn.get("entity_definitions"))
    leaf_links = tuple(d for d in _list(_map(csn.get("delta_links")).get(leaf)) if isinstance(d, str)) or explicit_delta_links({leaf: entity_defs[leaf]} if leaf in entity_defs else {}).get(leaf, ())
    col_evidence = _map(csn.get("rf_task_columns"))
    results = []
    for p in prods:
        name = p.get("name") if isinstance(p.get("name"), str) else None
        pkind = p.get("kind") if isinstance(p.get("kind"), str) else None
        match = p.get("target_match") if isinstance(p.get("target_match"), str) else None
        entry: dict[str, Any] = {"kind": pkind, "name": name, "target_match": match, "load_types": sorted(_strs(p.get("load_types"))), "materialization_kind": KIND_UNKNOWN,
                                 "unknown_reasons": [], "mapping_profile": _empty_mapping_profile(), "filtered": None, "write_mode": None, "upsert": None, "components": [],
                                 "resolved_via": None, "shadow_entity": None, "wrapper": None}
        if name is None or pkind is None:
            entry["unknown_reasons"].append(U_FLOW_NOT_IN_EVIDENCE)
            fail(U_FLOW_NOT_IN_EVIDENCE, "unparseable_producer")
        elif match != EXACT and not (match == DELTA_SHADOW_SUFFIX_MATCH and leaf_links and pkind == REPLICATION_FLOW):
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
                target = leaf
                if match != EXACT:                                  # pista por nombre: solo un vinculo CSN explicito X -> D la convierte en relacion
                    covered = sorted({t["target_object"] for t in _dicts(_map(cands[0].get("properties")).get("tasks")) if t.get("target_object") in leaf_links})
                    if len(covered) != 1:
                        code = U_CONTRADICTORY if not covered else U_TARGET_AMBIGUOUS
                        entry["unknown_reasons"].append(code)
                        fail(code, f"delta_link_does_not_cover_producer_target:{name}" if not covered else f"delta_link_covers_several_targets:{name}")
                        results.append(entry)
                        continue
                    target = covered[0]
                    entry["resolved_via"], entry["shadow_entity"] = RESOLVED_VIA_DELTA_LINK, target
                    task = next(t for t in _dicts(_map(cands[0].get("properties")).get("tasks")) if t.get("target_object") == target)
                    cdc = {v for v in _map(task.get("change_capture_properties")).values() if isinstance(v, str)}
                    winfo, wreasons = verify_delta_wrapper(leaf, target, entity_defs, cdc, _map(col_evidence.get((name, target))).get("target_columns"))
                    entry["wrapper"] = winfo
                    if wreasons:                                   # el vinculo declarado NO basta: X debe leer a D como proyeccion verificable
                        for code, why in wreasons:
                            entry["unknown_reasons"].append(code)
                            fail(code, f"{name}:{why}")
                        results.append(entry)
                        continue
                cols = col_evidence.get((name, target)) if pkind == REPLICATION_FLOW else None
                r = classify_flow_kind(cands[0], leaf, target=target, columns=cols, case_premise=pkind == REPLICATION_FLOW and _cds_source_premise(pb_leaf, name))
                entry.update({k: r[k] for k in ("materialization_kind", "unknown_reasons", "mapping_profile", "filtered", "write_mode", "upsert", "components")})
                entry["load_type"] = r["load_type"]
                w = entry["wrapper"]
                if w is not None and not r["unknown_reasons"]:
                    if w["filtered"]:
                        entry["filtered"] = True
                    if w["transformations"] and entry["materialization_kind"] == KIND_REPLICA:
                        entry["materialization_kind"] = KIND_TRANSFORMED          # transformacion TECNICA demostrada en el wrapper (naturaleza en wrapper.transformations)
                for code in r["unknown_reasons"]:
                    extras = r["detail"].get(code)
                    if extras:
                        for extra in extras:
                            fail(code, f"{name}:{extra}")
                    else:
                        fail(code, str(name))
        results.append(entry)
    out["producers"] = results

    exact = {(e["kind"], e["name"]) for e in results if e["target_match"] == EXACT or e["resolved_via"]}
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


def derive_all(ce: Any, csn: Any = None) -> dict[str, dict[str, Any]]:
    """``{hoja: derive_leaf_materialization}`` para toda hoja con registro de procedencia o de borde, ordenado por nombre. ``{}`` si no hay evidencia o es historica. ``csn``: ver ``derive_leaf_materialization``."""
    if not isinstance(ce, Mapping) or not ce or _missing_sections(ce):
        return {}
    names = {r["leaf"] for r in _dicts(ce.get("leaf_provenance")) if isinstance(r.get("leaf"), str)}
    names |= {r["leaf"] for r in _dicts(_map(ce.get("physical_source_boundaries")).get("leaves")) if isinstance(r.get("leaf"), str)}
    return {leaf: derive_leaf_materialization(leaf, ce, csn) for leaf in sorted(names)}
