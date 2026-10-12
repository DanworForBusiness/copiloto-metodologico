"""Proyección de PRESENTACIÓN de «Fuente / Extracción» e «Ingesta» del Assessment (``assessment_presenter`` la compone en las tarjetas de área).

Qué hace: lee la evidencia canónica YA persistida en el perfil (``canonical_evidence``: ``source_contract_classes``, ``leaf_provenance``,
``processing_flow_properties``) y los ``rule_results``/``surfaces`` del mismo perfil, y los dispone en las tablas/notas que ya consumen las tarjetas de área
(``facts.groups`` / ``facts.notes``) y en la división de reglas. Qué NO hace: no calcula evidencia nueva, no toca el perfil, la cobertura, la confianza ni el Juicio, ni
reinterpreta hechos (no infiere FULL/DELTA, ni federado/replicado, ni la ausencia de familias de flujo que la extracción no declaró). Un perfil sin esa evidencia
(anterior a ``canonical-evidence/v3`` o ad-hoc sin flujos) devuelve ``None`` y la presentación vigente queda exactamente como estaba.

Responsabilidades (no se mezclan ni se duplican entre los dos colapsables):
  · Fuente / Extracción → de dónde viene el dato: mecanismo de adquisición observado, origen, contrato de la fuente y estado de la evidencia, por hoja de frontera.
  · Ingesta → cómo entra y se carga: desglose de hojas de frontera por procedencia y propiedades de carga exportadas (modalidad, delta, captura de cambios, persistencia).
"""
from __future__ import annotations

from collections import Counter
from typing import Any


CONTRACT_CLASS_TEXT = {
    "GOVERNED_SAP_CDS_CONTRACT": "CDS gobernado SAP",
    "GOVERNED_SAP_ODP_SAPI_CONTRACT": "Extractor ODP/SAPI gobernado",
    "SAP_STANDARD_VIEW_ON_PHYSICAL_TABLE": "Vista estándar SAP sobre tabla física",
    "PHYSICAL_ABAP_TABLE_BOUNDARY": "Tabla física ABAP",
    "EXTERNAL_CONTRACT_UNDETERMINED": "Contrato externo no determinado",
    "MIXED_BOUNDARY_CLASSES": "Clases de contrato mixtas",
    "PLATFORM_PROVIDED": "Provisto por la plataforma",
    "UNKNOWN": "Sin clase de contrato resuelta",
}
FLOW_KIND_TEXT = {"REPLICATION_FLOW": "Replication Flow", "DATA_FLOW": "Data Flow", "TRANSFORMATION_FLOW": "Transformation Flow"}
REMOTE_CLASSIFICATION_POINTER = "Ver «Por estado de la fuente»"
UNOBSERVED_TEXT = {"schedule": "calendario de ejecución", "replication_state": "estado de replicación", "federation_state": "estado de federación",
                   "execution_mode": "modo de ejecución"}
RULE_POPULATION_NOUN = {"DIRECT_RAW_TABLE_ACCESS": ("hojas de frontera", "evaluadas"), "DUPLICATED_REMOTE_IMPORT": ("hojas de frontera", "evaluadas"),
                        "LAYER_BOUNDARY_REVIEW": ("hojas de frontera", "evaluadas"), "ROLE_LAYER_DIVERGENCE": ("artefactos", "evaluados")}
SPLIT_AREAS = ("SOURCE_EXTRACTION", "INGESTION")           # reglas sin hallazgos plegadas SOLO en estas dos áreas (las demás conservan su tabla)
ABSENCE, NOT_APPLICABLE = "ASSESSED_ABSENCE", "NOT_APPLICABLE"
_V3 = "canonical-evidence/v3"


def _ce(doc: dict) -> dict | None:
    ce = doc.get("canonical_evidence") if isinstance(doc, dict) else None
    return ce if isinstance(ce, dict) and str(ce.get("version", "")).startswith("canonical-evidence/") else None


def _leaves(doc: dict) -> list[dict] | None:
    """Hojas de frontera con su clase de contrato (v3); ``None`` si el perfil no trae la evidencia."""
    ce = _ce(doc)
    leaves = ((ce or {}).get("source_contract_classes") or {}).get("leaves")
    return leaves if isinstance(leaves, list) and leaves else None


def _provenance(doc: dict) -> dict[str, dict]:
    return {x["leaf"]: x for x in ((_ce(doc) or {}).get("leaf_provenance") or []) if isinstance(x, dict) and "leaf" in x}


def _origin_text(leaf: dict) -> str:
    parts = []
    for s in leaf.get("source_systems") or []:
        text = f"{s.get('connection_id')} ({s.get('connection_type')})" if s.get("connection_type") else str(s.get("connection_id"))
        parts.append(text + (f" · {s['container']}" if s.get("container") else ""))
    if not parts and leaf.get("remote_connection"):
        parts.append(str(leaf["remote_connection"]))
    return "; ".join(sorted(set(parts))) or "—"


def _mechanism_and_evidence(leaf: dict, prov: dict[str, dict]) -> tuple[str, str]:
    kind = leaf.get("leaf_kind")
    p = prov.get(leaf["leaf"]) or {}
    if kind == "LOCAL_FLOW_PRODUCED":
        kinds = sorted({FLOW_KIND_TEXT.get(f.get("kind"), str(f.get("kind"))) for f in p.get("producer_flows") or []})
        return (" + ".join(kinds) if kinds else "Flujo productor"), "Observada"
    if kind == "REMOTE_IMPORTED":
        return "Tabla remota importada", "Observada"
    if kind == "LOCAL_DECLARED_SOURCE":
        basis = {"DECLARED_DATASET": "Dataset externo declarado", "TABLE_FUNCTION": "Table function declarada"}.get(p.get("basis"), "Origen declarado")
        return basis, "Declarada"
    if kind == "LOCAL_PROVENANCE_NOT_ASSESSED":
        return "Sin productor identificado", "No evaluable"
    if kind == "PLATFORM_OBJECT":
        return "Objeto de la plataforma", "Provista por la plataforma"
    return str(kind), "No evaluable"


def _has_state_table(doc: dict) -> bool:
    """«Por estado de la fuente» se muestra (hay artefactos remotos y el hecho trae filas)."""
    h = _highlight(doc, "SOURCE_EXTRACTION", "by_source_state") or {}
    return bool(h.get("value")) and federation_gate(doc)["has_remote"]


def acquisition_rows(doc: dict) -> list[list[Any]] | None:
    """Filas de «Adquisición de las hojas de frontera»: ``[Mecanismo, Origen, Contrato de la fuente, Hojas, Evidencia]`` (una por combinación observada)."""
    leaves = _leaves(doc)
    if leaves is None:
        return None
    prov = _provenance(doc)
    pointer = REMOTE_CLASSIFICATION_POINTER if _has_state_table(doc) else None
    counts: Counter = Counter()
    for leaf in leaves:
        mech, ev = _mechanism_and_evidence(leaf, prov)
        contract = CONTRACT_CLASS_TEXT.get(leaf.get("contract_class"), str(leaf.get("contract_class")))
        if pointer and leaf.get("leaf_kind") == "REMOTE_IMPORTED":      # la clasificación de contratos remotos ya vive en «Por estado de la fuente»: no se repite aquí
            contract = pointer
        counts[(mech, _origin_text(leaf), contract, ev)] += 1
    return [[m, o, c, n, e] for (m, o, c, e), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _highlight(doc: dict, area_id: str, hid: str) -> dict | None:
    for a in doc.get("areas") or []:
        if a.get("id") == area_id:
            return next((h for h in a.get("highlights") or [] if h.get("id") == hid), None)
    return None


def _surface_state(doc: dict, surface_id: str) -> str | None:
    return next((s.get("assessability") for s in doc.get("surfaces") or [] if s.get("surface_id") == surface_id), None)


def federation_gate(doc: dict) -> dict:
    """Qué indicadores de federación se muestran: solo con artefactos remotos Y evidencia evaluada (un 0 de una superficie no evaluada no es un dato)."""
    remote = (_highlight(doc, "SOURCE_EXTRACTION", "remote_artifacts") or {}).get("value")
    has_remote = isinstance(remote, (int, float)) and remote > 0
    return {"known": remote is not None, "has_remote": has_remote,
            "hide": set() if has_remote else {"remote_artifacts", "by_source_state", "connections", "sources_with_filter"},
            "hide_unassessed": {hid for hid, surf in (("connections", "source.connection_consistency"), ("sources_with_filter", "source.query_filter"))
                                if has_remote and _surface_state(doc, surf) != "ASSESSED"}}


def source_facts(doc: dict) -> dict | None:
    """Extras de Fuente / Extracción: tabla de adquisición, nota de alcance y los ids de highlights de federación a ocultar. ``None`` si el perfil no trae la evidencia."""
    rows = acquisition_rows(doc)
    if rows is None:
        return None
    gate = federation_gate(doc)
    notes = []
    empty = None
    if gate["known"] and not gate["has_remote"]:                        # estado vacío del Hub (`.dashboard-empty`): mismo mensaje, ya no como texto corrido
        empty = {"label": "Sin origen remoto",
                 "text": "El alcance evaluado no contiene artefactos con origen remoto (tablas importadas desde otra conexión); las superficies de contrato remoto "
                         "no aplican a este modelo."}
    return {"empty_state": empty, "table": {"title": "Adquisición de las hojas de frontera", "columns": ["Mecanismo", "Origen", "Contrato de la fuente", "Hojas", "Evidencia"], "rows": rows},
            "notes": notes, "hide": gate["hide"] | gate["hide_unassessed"]}


def _persistence(flow: dict, prov: dict[str, dict]) -> str:
    kinds = set()
    for leaf in flow.get("closure_targets") or []:
        p = prov.get(leaf) or {}
        match = next((f.get("target_match") for f in p.get("producer_flows") or [] if f.get("name") == flow.get("flow_name")), None)
        impl = p.get("implementation")
        if match == "DELTA_SHADOW_SUFFIX":
            kinds.add("Tabla local con captura de delta")
        elif impl == "LOCAL_TABLE":
            kinds.add("Tabla local")
        elif impl == "STRUCTURE_ONLY":
            kinds.add("Estructura local sin tipo de repositorio declarado")
    return " + ".join(sorted(kinds)) or "—"


def _detail(flow: dict) -> str:
    o, bits = flow.get("observed") or {}, []
    if o.get("delta_load_trigger"):
        bits.append(f"disparador del delta {o['delta_load_trigger']}")
    if o.get("delta_check_intervals"):
        bits.append("intervalo de verificación " + "/".join(f"{i} s" for i in o["delta_check_intervals"]))
    if True in (o.get("delta_disabled") or []):
        bits.append("delta deshabilitado")
    if o.get("tasks_with_change_capture_properties"):
        bits.append("captura de cambios declarada")
    tasks = (flow.get("properties") or {}).get("tasks") or []
    if o.get("tasks_with_filters"):
        bits.append(f"filtros declarados en {o['tasks_with_filters']} tarea(s)")
    elif tasks:
        bits.append("sin filtros declarados")
    return "; ".join(bits) or "—"


def load_facts(doc: dict) -> dict | None:
    """Extras de Ingesta: tabla «Cómo se carga» (solo propiedades exportadas) y notas de alcance. ``None`` si el perfil no trae flujos productores."""
    ce = _ce(doc)
    pfp = (ce or {}).get("processing_flow_properties") or {}
    flows = pfp.get("flows") or []
    if not flows:
        return None
    prov = _provenance(doc)
    counts: Counter = Counter()
    for fl in flows:
        o = fl.get("observed") or {}
        loads = ", ".join(o.get("load_types") or []) or "tipo de carga no exportado"
        counts[(f"{FLOW_KIND_TEXT.get(fl.get('flow_kind'), fl.get('flow_kind'))} · {loads}", _persistence(fl, prov), _detail(fl))] += 1
    rows = [[c, n, p, d] for (c, p, d), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    scopes = [x.get("scope") or {} for x in prov.values()]
    conclusive = bool(scopes) and all(s.get("conclusive") for s in scopes)
    notes = ["La evidencia de carga se limita a los flujos que producen hojas de frontera."
             + ("" if conclusive else " No se evaluó la presencia de Data Flows, Transformation Flows ni Task Chains fuera de ese alcance: la extracción no declara "
                                      "qué familias de objetos incluyó.")]
    missing = [UNOBSERVED_TEXT[u["property"]] for u in pfp.get("unobserved_properties") or [] if u.get("reason") == "NOT_IN_EXPORT" and u.get("property") in UNOBSERVED_TEXT]
    if missing:
        notes.append("No presente en el export de los flujos: " + ", ".join(missing) + ".")
    return {"table": {"title": "Cómo se carga", "columns": ["Carga observada", "Flujos", "Persistencia", "Detalle exportado"], "rows": rows}, "notes": notes}


def ingestion_hidden(doc: dict) -> set[str]:
    """Indicadores de federación de Ingesta (entidades remotas duplicadas) sin artefactos remotos: no aportan información (un 0 por construcción)."""
    g = federation_gate(doc)
    return {"duplicated_remote_entities_in_closure"} if g["known"] and not g["has_remote"] else set()


def split_rules(doc: dict, area_id: str, rules: list[dict]) -> tuple[list[dict], list[dict]] | None:
    """(reglas con hallazgos o con ausencia NO establecida, controles sin hallazgos) para las áreas en ``SPLIT_AREAS``; ``None`` en las demás.

    Los controles sin hallazgos siguen visibles (disclosure) con su población evaluada: «lo miré y no apareció» no se esconde ni compite con los hallazgos."""
    if area_id not in SPLIT_AREAS:
        return None
    units = {s["surface_id"]: s.get("unit_counts") or {} for s in doc.get("surfaces") or []}
    opened, clean = [], []
    for r in rules:
        item = dict(r)
        uc = units.get(f"rule.{str(r.get('rule', '')).lower()}") or {}
        total = sum(v for v in uc.values() if isinstance(v, int))
        noun, part = RULE_POPULATION_NOUN.get(r.get("rule"), ("unidades", "evaluadas"))
        item["population"] = f"{uc.get('assessed', 0)} de {total} {noun} {part}" if total and r.get("result") != NOT_APPLICABLE else "—"
        (clean if r.get("result") in (ABSENCE, NOT_APPLICABLE) else opened).append(item)
    return opened, clean
