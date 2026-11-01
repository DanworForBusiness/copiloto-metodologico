"""sap_connection_types.py — contrato `sap-connection-types/v1` (catálogo de REFERENCIA de tipos de conexión de SAP Datasphere).

Transforma el export crudo del catálogo nativo de tipos de conexión de un tenant (`{"connectionTypes": [...]}`) en la versión
SANEADA que consume el Hub (`content_packs/datasphere_core/sap_connection_types.json`). El crudo es solo un INSUMO EXTERNO: no se
versiona ni se sirve como estático.

Reglas del contrato:
  * Se preservan, en el MISMO orden: cada tipo y su `typeId`, `categories`, `sources`, `capabilities` e `iconLocation` (idéntico,
    para la futura biblioteca de imágenes).
  * Requisitos técnicos en TRES estados: `true` (requerido) · `false` (no requerido) · `null` (no declarado en el snapshot).
  * `featureFlag` ⇒ disponibilidad CONDICIONAL (`availability.conditional_on_feature_toggle = true`), sin el identificador del flag.
    Sin flag ⇒ `null` (no declarado): nunca se afirma que el tipo esté habilitado en todos los tenants.
  * Se excluye todo lo particular del tenant u operacional: agentes (`dpAgents`), IPs, instancias/configuración de Open Connectors,
    URLs de integración de partners, identificadores de flags.
  * ALLOWLIST ESTRICTA: un atributo que no esté clasificado acá rechaza la transformación completa (nada nuevo se publica sin revisar).

Uso:
  python -m tools.catalog.sap_connection_types <crudo.json> [--out <saneado.json>]   genera (por defecto, el del content pack)
  python -m tools.catalog.sap_connection_types --check [<saneado.json>]                valida el saneado versionado
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA = "sap-connection-types/v1"
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "content_packs" / "datasphere_core" / "sap_connection_types.json"

# ── Clasificación de atributos del crudo (allowlist) ──────────────────────────────────────────────────────────────────────────────
REQUIRED = ("typeId", "iconLocation", "categories", "sources", "capabilities")
LIST_FIELDS = ("categories", "sources", "capabilities")
REQUIREMENTS = {                                   # crudo → salida (tres estados)
    "dpAgentRequired": "dp_agent_required",
    "disReplicationRequired": "dis_replication_required",
    "ipWhitelistingRequired": "ip_allowlisting_required",
    "advancedMode": "advanced_mode",
    "generic": "generic_framework",
}
TYPE_TECHNICAL = {"adapter", "partner", "partnerAPIVersion", "ccmTypeId", "ccmTypeVersion", "CSNInjection"}
CONDITIONAL = {"featureFlag"}                      # se conserva SOLO la semántica (disponibilidad condicional), nunca el valor
TENANT_SPECIFIC = {                                # se descartan siempre
    "dpAgents", "ipAddresses", "ocnConfigured", "ocnInstances", "iFrameUrlConnectionCreation", "iFramePostMessageOrigin",
}
KNOWN = {*REQUIRED, *REQUIREMENTS, *TYPE_TECHNICAL, *CONDITIONAL, *TENANT_SPECIFIC}

# Nombres visibles SOLO con evidencia: captura del diálogo «Create Connection» de SAP Datasphere (mismo orden que el export).
# Sin evidencia ⇒ `display_name = null` (la UI muestra el `typeId`). Nunca se inventa un nombre comercial.
DISPLAY_NAMES = {
    "ABAP": "SAP ABAP", "ADL": "Microsoft Azure Data Lake Storage Gen2", "AdverityDatatap": "Adverity", "PRECOG": "Precog",
    "ATHENA": "Amazon Athena", "AZURESQL": "Microsoft Azure SQL Database", "BIGQUERY": "Google BigQuery",
    "CDI": "Cloud Data Integration", "GCS": "Google Cloud Storage", "GENERICHTTP": "Generic HTTP", "GENERICJDBC": "Generic JDBC",
    "HANA": "SAP HANA",
}
DISPLAY_NAME_SOURCE = "sap_ui_capture"

SOURCE = {
    "kind": "sap_datasphere_connection_type_catalog",
    "nature": "reference_snapshot",
    "note": "Snapshot de referencia del catálogo de tipos de conexión de un tenant de SAP Datasphere. La disponibilidad real "
            "depende del tenant, su licencia y su configuración.",
}

# ── Salida: claves permitidas (estabilidad del contrato) ──────────────────────────────────────────────────────────────────────────
DOC_KEYS = {"schema", "source", "count", "connection_types"}
TYPE_KEYS = {"type_id", "display_name", "display_name_source", "icon_location", "categories", "sources", "capabilities",
             "requirements", "availability", "adapter_id", "partner", "generic_framework_type", "default_parameters"}
OPTIONAL_TYPE_KEYS = {"partner", "generic_framework_type", "default_parameters"}
_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://", re.I)
_FORBIDDEN_MARKERS = ("dpAgents", "agentName", "ipAddresses", "ocnInstances", "ocnConfigured", "featureFlag", "iFrame")


class ContractError(ValueError):
    """El crudo no cumple el contrato (atributo no clasificado, campo obligatorio ausente, tipo inválido o duplicado)."""


def _tri_state(value, field: str, type_id: str):
    if value is None or isinstance(value, bool):
        return value
    raise ContractError(f"{type_id}: '{field}' debe ser booleano")


def _str_list(value, field: str, type_id: str) -> list:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ContractError(f"{type_id}: '{field}' debe ser una lista de textos")
    return list(value)


def _sanitize_type(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ContractError("cada tipo de conexión debe ser un objeto")
    type_id = raw.get("typeId")
    if not isinstance(type_id, str) or not type_id.strip():
        raise ContractError("tipo sin 'typeId'")
    unknown = sorted(set(raw) - KNOWN)
    if unknown:
        raise ContractError(f"{type_id}: atributos no clasificados {unknown}")
    missing = [f for f in REQUIRED if f not in raw]
    if missing:
        raise ContractError(f"{type_id}: faltan {missing}")
    if not isinstance(raw["iconLocation"], str) or not raw["iconLocation"]:
        raise ContractError(f"{type_id}: 'iconLocation' debe ser un texto")

    out = {
        "type_id": type_id,
        "display_name": DISPLAY_NAMES.get(type_id),
        "display_name_source": DISPLAY_NAME_SOURCE if type_id in DISPLAY_NAMES else None,
        "icon_location": raw["iconLocation"],
    }
    for f in LIST_FIELDS:
        out[f] = _str_list(raw[f], f, type_id)
    out["requirements"] = {dst: _tri_state(raw.get(src), src, type_id) for src, dst in REQUIREMENTS.items()}
    flag = raw.get("featureFlag")
    if flag is not None and (not isinstance(flag, str) or not flag):
        raise ContractError(f"{type_id}: 'featureFlag' inválido")
    out["availability"] = {"conditional_on_feature_toggle": True if flag else None}
    adapter = raw.get("adapter")
    if adapter is not None and not (isinstance(adapter, dict) and isinstance(adapter.get("id"), str) and set(adapter) <= {"id"}):
        raise ContractError(f"{type_id}: 'adapter' inválido")
    out["adapter_id"] = adapter["id"] if adapter else None
    if "partner" in raw or "partnerAPIVersion" in raw:
        out["partner"] = {"id": raw.get("partner"), "api_version": raw.get("partnerAPIVersion")}
    if "ccmTypeId" in raw or "ccmTypeVersion" in raw:
        out["generic_framework_type"] = {"ccm_type_id": raw.get("ccmTypeId"), "ccm_type_version": raw.get("ccmTypeVersion")}
    if "CSNInjection" in raw:
        csn = raw["CSNInjection"]
        params = (((csn or {}).get("dataflow") or {}).get("parameters") or []) if isinstance(csn, dict) else None
        if not isinstance(params, list) or not all(isinstance(p, dict) and isinstance(p.get("name"), str) for p in params):
            raise ContractError(f"{type_id}: 'CSNInjection' inválido")
        out["default_parameters"] = sorted({p["name"] for p in params})   # solo los NOMBRES; nunca valores de configuración
    return out


def sanitize(raw: dict) -> dict:
    """Crudo → documento `sap-connection-types/v1`. Determinístico (misma entrada ⇒ misma salida). Lanza `ContractError`."""
    if not isinstance(raw, dict) or set(raw) != {"connectionTypes"} or not isinstance(raw["connectionTypes"], list):
        raise ContractError("el crudo debe ser {'connectionTypes': [...]}")
    types = [_sanitize_type(t) for t in raw["connectionTypes"]]
    if not types:
        raise ContractError("sin tipos de conexión")
    ids = [t["type_id"] for t in types]
    dups = sorted({i for i in ids if ids.count(i) > 1})
    if dups:
        raise ContractError(f"typeId duplicados: {dups}")
    return {"schema": SCHEMA, "source": dict(SOURCE), "count": len(types), "connection_types": types}


def validate(doc: dict, raw: dict | None = None) -> list[str]:
    """Problemas del documento saneado (lista vacía = válido). Con `raw`, además verifica la preservación contra el crudo."""
    problems: list[str] = []
    if not isinstance(doc, dict) or set(doc) != DOC_KEYS:
        return [f"claves de primer nivel distintas de {sorted(DOC_KEYS)}"]
    if doc["schema"] != SCHEMA:
        problems.append(f"schema distinto de {SCHEMA}")
    if doc["source"] != SOURCE:
        problems.append("metadato 'source' alterado")
    types = doc["connection_types"]
    if not isinstance(types, list) or doc["count"] != len(types):
        problems.append("'count' no coincide con la cantidad de tipos")
        return problems
    ids = [t.get("type_id") for t in types]
    if len(set(ids)) != len(ids):
        problems.append("type_id duplicados")
    for t in types:
        tid = t.get("type_id")
        keys = set(t)
        extra, missing = keys - TYPE_KEYS, (TYPE_KEYS - OPTIONAL_TYPE_KEYS) - keys
        if extra or missing:
            problems.append(f"{tid}: claves fuera del contrato {sorted(extra)} / faltantes {sorted(missing)}")
            continue
        if set(t["requirements"]) != set(REQUIREMENTS.values()) or any(v not in (True, False, None) for v in t["requirements"].values()):
            problems.append(f"{tid}: requisitos fuera de los tres estados")
        if set(t["availability"]) != {"conditional_on_feature_toggle"} or t["availability"]["conditional_on_feature_toggle"] not in (True, None):
            problems.append(f"{tid}: disponibilidad fuera del contrato")
        if (t["display_name"] is None) != (t["display_name_source"] is None) or t["display_name"] not in (None, DISPLAY_NAMES.get(tid)):
            problems.append(f"{tid}: display_name sin evidencia")
    text = json.dumps(doc, ensure_ascii=False)
    if _IP_RE.search(text):
        problems.append("contiene direcciones IP")
    if _URL_RE.search(text):
        problems.append("contiene URLs")
    for marker in _FORBIDDEN_MARKERS:
        if marker in text:
            problems.append(f"contiene un marcador de tenant: {marker}")
    if raw is not None:
        src = raw.get("connectionTypes") or []
        if [t.get("typeId") for t in src] != ids:
            problems.append("typeId o su orden difieren del crudo")
        else:
            for s, t in zip(src, types):
                for f in LIST_FIELDS:
                    if s.get(f) != t.get(f):
                        problems.append(f"{t['type_id']}: '{f}' difiere del crudo")
                if s.get("iconLocation") != t.get("icon_location"):
                    problems.append(f"{t['type_id']}: iconLocation difiere del crudo")
                if any(s.get(a) != t["requirements"][b] for a, b in REQUIREMENTS.items()):
                    problems.append(f"{t['type_id']}: requisitos difieren del crudo")
                if bool(s.get("featureFlag")) != bool(t["availability"]["conditional_on_feature_toggle"]):
                    problems.append(f"{t['type_id']}: disponibilidad condicional difiere del crudo")
        agents = {a.get("agentName") for s in src for a in (s.get("dpAgents") or []) if isinstance(a, dict)}
        if any(a and a in text for a in agents):
            problems.append("contiene nombres de agentes del tenant")
    return problems


def render(doc: dict) -> str:
    """Serialización estable (orden de claves fijo, UTF-8, salto final): regenerar sin cambios no produce diff."""
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Contrato sap-connection-types/v1: genera o valida el catálogo saneado.")
    ap.add_argument("raw", nargs="?", help="export crudo (insumo externo; no se versiona)")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="destino del JSON saneado")
    ap.add_argument("--check", nargs="?", const=str(DEFAULT_OUT), help="solo validar un JSON saneado")
    args = ap.parse_args(argv)
    if args.check:
        problems = validate(json.loads(Path(args.check).read_text(encoding="utf-8")))
        print("OK" if not problems else "\n".join(problems))
        return 0 if not problems else 1
    if not args.raw:
        ap.error("indicá el export crudo o --check")
    raw = json.loads(Path(args.raw).read_text(encoding="utf-8"))
    try:
        doc = sanitize(raw)
    except ContractError as e:
        print(f"RECHAZADO: {e}")
        return 2
    problems = validate(doc, raw)
    if problems:
        print("\n".join(problems))
        return 1
    Path(args.out).write_text(render(doc), encoding="utf-8")
    print(f"OK: {doc['count']} tipos → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
