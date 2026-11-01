"""sap_connection_catalog.py — presentación de «Disponibilidad SAP» (Catálogo de conectividad del módulo 01).

Fuente ÚNICA: el snapshot saneado del content pack (`<pack>/sap_connection_types.json`, contrato `sap-connection-types/v1`, ver
`tools/catalog/sap_connection_types.py`). Se valida con el MISMO contrato antes de usarlo: si no valida, la vista queda vacía
(fail-closed; nunca se muestra un archivo alterado). El crudo del tenant no se lee nunca desde acá.

Solo PRESENTACIÓN: etiquetas legibles para códigos de SAP con respaldo en la UI de SAP Datasphere («Create Connection»: sus filtros
coinciden 1:1 con los conteos del snapshot). El código original se muestra siempre junto a la etiqueta; no se agregan nombres
comerciales ni capacidades que el snapshot no declare.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache

log = logging.getLogger("danwor.sap_connection_catalog")

FILE_NAME = "sap_connection_types.json"

SOURCE_LABELS = {"SAP": "SAP", "NonSAP": "Non-SAP", "Partner": "Partner Tools"}
CATEGORY_LABELS = {"CloudApp": "Cloud", "OnPremise": "On-Premise"}
CAPABILITY_LABELS = {
    "DataflowSource": "Data Flows",
    "ReplicationflowSource": "Replication Flows (origen)",
    "ReplicationflowTarget": "Replication Flows (destino)",
    "HanaSdi": "Remote Tables",
    "PartnerSchema": "Remote Tables (partner)",
    "ModelTransfer": "Model Import",
    "ApiTasks": "API Tasks",
}
# Atributos técnicos del tipo, en TRES estados (true / false / no declarado). `requirement` = requisito («Requerido / No requerido»);
# `attribute` = característica del tipo («Sí / No»). La etiqueta acompaña SIEMPRE al código original del snapshot.
REQUIREMENT_LABELS = {
    "dp_agent_required": ("DP Agent", "dpAgentRequired", "requirement"),
    "ip_allowlisting_required": ("IP allowlisting", "ipWhitelistingRequired", "requirement"),
    "dis_replication_required": ("DIS replication", "disReplicationRequired", "requirement"),
    "advanced_mode": ("Modo avanzado", "advancedMode", "attribute"),
    "generic_framework": ("Framework genérico", "generic", "attribute"),
}
STATE_LABELS = {
    "requirement": {True: "Requerido", False: "No requerido", None: "No declarado"},
    "attribute": {True: "Sí", False: "No", None: "No declarado"},
}
NOTICE = ("Snapshot de referencia del catálogo de tipos de conexión de un tenant de SAP Datasphere. No garantiza disponibilidad en "
          "todos los tenants: depende del tenant, su licencia y su configuración.")
CONDITIONAL_TEXT = "Disponibilidad condicional: en el tenant de referencia este tipo depende de una configuración habilitada."


def _state_key(value) -> str:
    return {True: "true", False: "false"}.get(value, "null")


def _view_type(t: dict) -> dict:
    caps = t["capabilities"]
    requirements = [
        {"key": key, "label": label, "code": code, "kind": kind, "state": _state_key(t["requirements"][key]),
         "state_label": STATE_LABELS[kind][t["requirements"][key]]}
        for key, (label, code, kind) in REQUIREMENT_LABELS.items()
    ]
    conditional = t["availability"]["conditional_on_feature_toggle"] is True
    search_text = " ".join(x for x in (t["display_name"], t["type_id"]) if x)
    return {
        "type_id": t["type_id"],
        "title": t["display_name"] or t["type_id"],
        "display_name": t["display_name"],
        "icon_location": t["icon_location"],                      # se conserva tal cual para la futura biblioteca de imágenes
        "sources": [{"code": s, "label": SOURCE_LABELS.get(s, s)} for s in t["sources"]],
        "categories": [{"code": c, "label": CATEGORY_LABELS.get(c, c)} for c in t["categories"]],
        "capabilities": [{"code": c, "label": CAPABILITY_LABELS.get(c, c)} for c in caps],
        "requirements": requirements,
        "declared_requirements": [r for r in requirements if r["state"] != "null"],
        "conditional": conditional,
        "adapter_id": t.get("adapter_id"),
        "partner": t.get("partner"),
        "generic_framework_type": t.get("generic_framework_type"),
        "default_parameters": t.get("default_parameters") or [],
        # Atributos para los filtros en el cliente (códigos del snapshot; nunca texto libre del tenant).
        "data_search": search_text,
        "data_sources": " ".join(t["sources"]),
        "data_categories": " ".join(t["categories"]),
        "data_capabilities": " ".join(caps),
        "data_requirements": " ".join(f"{r['key']}:{r['state']}" for r in requirements)
                             + (" availability:conditional" if conditional else ""),
    }


def _options(types: list[dict], field: str, labels: dict) -> list[dict]:
    present = {code for t in types for code in t[field]}
    ordered = [code for code in labels if code in present] + sorted(present - set(labels))
    return [{"value": code, "label": labels.get(code, code),
             "count": sum(1 for t in types if code in t[field])} for code in ordered]


def _requirement_options(types: list[dict]) -> list[dict]:
    out = []
    for key, (label, code, kind) in REQUIREMENT_LABELS.items():
        for value in (True, False):
            n = sum(1 for t in types if t["requirements"][key] is value)
            if n:
                out.append({"value": f"{key}:{_state_key(value)}", "label": f"{label}: {STATE_LABELS[kind][value].lower()}", "count": n})
    n = sum(1 for t in types if t["availability"]["conditional_on_feature_toggle"] is True)
    if n:
        out.append({"value": "availability:conditional", "label": "Disponibilidad condicional", "count": n})
    return out


@lru_cache(maxsize=8)
def catalog(pack: str) -> dict:
    """Vista de «Disponibilidad SAP» para el pack. Vacía si el pack no tiene snapshot o si el snapshot no cumple el contrato."""
    empty = {"types": [], "total": 0, "notice": NOTICE, "conditional_text": CONDITIONAL_TEXT,
             "filters": {"sources": [], "categories": [], "capabilities": [], "requirements": []}}
    try:
        import config
        from tools.catalog.sap_connection_types import validate
        path = config.CONTENT_PACKS_DIR / pack / FILE_NAME
        if not path.is_file():
            return empty
        doc = json.loads(path.read_text(encoding="utf-8"))
        problems = validate(doc)
        if problems:
            log.warning("sap_connection_types inválido en el pack %s: %d problema(s)", pack, len(problems))
            return empty
    except Exception as exc:                                       # noqa: BLE001 — lectura de referencia: nunca rompe el módulo
        log.warning("sap_connection_types no disponible: %s", type(exc).__name__)
        return empty
    types = doc["connection_types"]
    return {
        "types": [_view_type(t) for t in types],
        "total": len(types),
        "notice": NOTICE,
        "conditional_text": CONDITIONAL_TEXT,
        "filters": {
            "sources": _options(types, "sources", SOURCE_LABELS),
            "categories": _options(types, "categories", CATEGORY_LABELS),
            "capabilities": _options(types, "capabilities", CAPABILITY_LABELS),
            "requirements": _requirement_options(types),
        },
    }
