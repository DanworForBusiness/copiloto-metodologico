"""Contrato `connectivity-observation/v1` (F2.2a): una OBSERVACIÓN de conexiones con identidad, procedencia, cobertura y atributos normalizados.

PURO: sin I/O, sin red, sin persistencia. Dos fuentes, una sola normalización:
  * `from_cli_documents`: los documentos del CLI de Datasphere (`spaces connections list` / `get`, la MISMA forma que los exports
    `conn_space_<SPACE>[_<CONN>].json`), parseados con los MISMOS parsers del Catálogo publicado (`generate_space_catalog`), no con una copia.
  * `from_published_catalog`: las entradas del Catálogo publicado (`catalog_service.load_architecture_connections`), que son el resultado de esos
    mismos parsers rendido en Markdown. El Catálogo NO es «el último estado SAP»: su procedencia queda declarada (sin fecha de evidencia ni
    cobertura) y lo que su render no conserva queda DESCONOCIDO, nunca «ausente».

Identidad (connectivity-identity/v1): conexión = (tenant_id, space, connection_name). `type_id` es un ATRIBUTO (un cambio de tipo es un cambio,
no una alta + baja). `tenant_id` puede ser provisional/desconocido y `environment` es GOBERNADO (lo provee el llamador; F2.1 lo deja en null).

Valores (tres estados, por campo):
  DECLARED — la fuente se leyó y declara un valor (normalizado según el tipo del campo).
  ABSENT   — la fuente se leyó y no declara valor (vacío, «—», ausente).
  UNKNOWN  — la fuente no permite saberlo (detalle no leído, campo que el render del Catálogo no conserva). UNKNOWN nunca se compara como cambio.

Grupos: los TÉCNICOS de arquitectura (type, adapter, endpoint, connectivity, capabilities, security) forman el `technical_fingerprint`;
`parameters` (parametrización) y `descriptive` se comparan aparte; `operational` (estado del agente, conexión del agente, replicación) es
TRANSITORIO: se registra con su fecha y NUNCA cuenta como cambio de arquitectura. Fuera del contrato: credenciales, usuarios, secretos, tokens,
creador, ids internos y cuerpos crudos (allowlist estricta).
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from urllib.parse import urlparse

SCHEMA = "connectivity-observation/v1"
IDENTITY_SCHEMA = "connectivity-identity/v1"

DECLARED, ABSENT, UNKNOWN = "DECLARED", "ABSENT", "UNKNOWN"
SOURCE_KINDS = ("cli_extraction", "cli_export_archive", "published_catalog")
STATUSES = ("COMPLETE", "PARTIAL", "FAILED", "UNKNOWN")
EXHAUSTIVENESS = ("VERIFIED", "UNVERIFIABLE", "INCOMPLETE", "UNKNOWN")
DETAIL_READ, DETAIL_NOT_READ = "READ", "NOT_READ"

ARCHITECTURE_GROUPS = ("type", "adapter", "endpoint", "connectivity", "capabilities", "security")
COMPARED_GROUPS = ARCHITECTURE_GROUPS + ("parameters", "descriptive")
GROUPS = COMPARED_GROUPS + ("operational",)

# (campo, grupo, tipo) — allowlist ESTRICTA del contrato. `bool` normaliza «true/false/Sí/No»; `str` conserva el texto recortado.
FIELDS = (
    ("type_id", "type", "str"),
    ("adapter", "adapter", "str"), ("remote_source", "adapter", "str"), ("kind", "adapter", "str"),
    ("connection_type", "endpoint", "str"), ("host", "endpoint", "str"), ("port", "endpoint", "str"), ("sysid", "endpoint", "str"),
    ("systemnumber", "endpoint", "str"), ("client", "endpoint", "str"), ("server_group", "endpoint", "str"), ("dbtype", "endpoint", "str"),
    ("driver_class", "endpoint", "str"), ("url_endpoint", "endpoint", "str"), ("version", "endpoint", "str"),
    ("location", "connectivity", "str"), ("agent_name", "connectivity", "str"), ("agent_group", "connectivity", "str"),
    ("cloud_connector", "connectivity", "bool"), ("cloud_connector_location", "connectivity", "str"),
    ("virtual_host", "connectivity", "str"), ("virtual_port", "connectivity", "str"),
    ("remote_tables", "capabilities", "bool"), ("dataflows", "capabilities", "bool"), ("replicationflows", "capabilities", "bool"),
    ("streaming", "capabilities", "bool"),
    ("credential_mode", "security", "str"), ("auth_type", "security", "str"),
    ("langcode", "parameters", "str"), ("odpversion", "parameters", "str"), ("poolsize", "parameters", "str"), ("connlimit", "parameters", "str"),
    ("business_name", "descriptive", "str"),
    ("agent_status", "operational", "str"), ("agent_connected", "operational", "bool"), ("replication_status", "operational", "str"),
)
FIELD_GROUP = {name: group for name, group, _ in FIELDS}
FIELD_KIND = {name: kind for name, _, kind in FIELDS}
# Lo que el render del Catálogo publicado NO conserva (generate_space_catalog.render_connection): queda UNKNOWN en esa fuente.
CATALOG_UNRENDERED = frozenset({"agent_connected"})

_TRUE = {"true", "1", "yes", "si", "sí", "s", "y", "on", "connected"}
_FALSE = {"false", "0", "no", "n", "off", "disconnected"}
_EMPTY = {"", "—", "-", "none", "null", "?"}


class ObservationError(ValueError):
    """Entrada fuera del contrato (identidad incompleta, enum desconocido, estructura inválida)."""


# ───────────────────────────── normalización de valores ─────────────────────────────
def _value(raw, kind: str) -> dict:
    if raw is None or isinstance(raw, (dict, list)):
        return {"state": ABSENT}
    if kind == "bool":
        if isinstance(raw, bool):
            return {"state": DECLARED, "value": raw}
        text = _fold(str(raw))
        if text in _EMPTY:
            return {"state": ABSENT}
        if text in _TRUE:
            return {"state": DECLARED, "value": True}
        if text in _FALSE:
            return {"state": DECLARED, "value": False}
        return {"state": DECLARED, "value": str(raw).strip()}       # valor no booleano declarado tal cual (se compara literal)
    text = str(raw).strip()
    return {"state": ABSENT} if text.lower() in _EMPTY else {"state": DECLARED, "value": text}


def _fold(text: str) -> str:
    return unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode().strip().lower()


def url_endpoint(raw) -> str | None:
    """Solo `host[:puerto][/ruta]` de una URL (JDBC/OData): sin esquema de credenciales, usuario, contraseña ni parámetros de consulta."""
    if raw in (None, "") or isinstance(raw, (dict, list)):
        return None
    text = str(raw).strip()
    if text.lower().startswith("jdbc:"):
        text = text[5:]
        if "://" not in text and ":" in text:                      # jdbc:sqlserver://… ya trae //; otros dialectos: subprotocolo:host
            text = text.split(":", 1)[1]
    scheme, sep, rest = text.partition("://")
    rest = re.split(r"[;?#]", rest if sep else scheme, maxsplit=1)[0]   # fuera parámetros (`;user=…`, `?password=…`) antes de parsear
    parsed = urlparse("//" + rest)
    try:
        host, port = parsed.hostname, parsed.port
    except ValueError:
        host, port = parsed.hostname, None
    if not host:
        return None
    path = (parsed.path or "").split(";", 1)[0].rstrip("/")
    return f"{host}{':' + str(port) if port else ''}{path}"


def _fields_state(raw_values: dict, unknown: frozenset = frozenset()) -> dict:
    out = {}
    for name, _group, kind in FIELDS:
        out[name] = {"state": UNKNOWN} if name in unknown else _value(raw_values.get(name), kind)
    return out


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def group_fingerprint(fields: dict, group: str) -> str | None:
    """sha256 estable de un grupo sobre (campo, estado, valor). None si algún campo del grupo es UNKNOWN (no comparable)."""
    members = sorted(n for n, g in FIELD_GROUP.items() if g == group)
    if any(fields[n]["state"] == UNKNOWN for n in members):
        return None
    return hashlib.sha256(_canon([[n, fields[n]] for n in members]).encode("utf-8")).hexdigest()


def _connection(key: dict, environment, detail_state: str, fields: dict) -> dict:
    groups = {g: group_fingerprint(fields, g) for g in COMPARED_GROUPS}
    arch = [groups[g] for g in ARCHITECTURE_GROUPS]
    technical = None if any(f is None for f in arch) else hashlib.sha256(_canon(arch).encode("utf-8")).hexdigest()
    return {"key": key, "environment": environment, "detail_state": detail_state, "fields": fields,
            "group_fingerprints": groups, "technical_fingerprint": technical}


def _key(tenant_id, space, name) -> dict:
    if not space or not name:
        raise ObservationError("identidad incompleta: Space y nombre de conexión son obligatorios")
    return {"tenant_id": tenant_id or None, "space": str(space), "connection_name": str(name)}


def _document(provenance: dict, scope: dict, coverage: dict, connections: list) -> dict:
    if provenance.get("source_kind") not in SOURCE_KINDS:
        raise ObservationError(f"source_kind fuera del contrato: {provenance.get('source_kind')}")
    if coverage.get("status") not in STATUSES or coverage.get("exhaustiveness") not in EXHAUSTIVENESS:
        raise ObservationError("estado o exhaustividad fuera del contrato")
    keys = [(c["key"]["space"], c["key"]["connection_name"]) for c in connections]
    if len(keys) != len(set(keys)):
        raise ObservationError("conexiones duplicadas en la observación")
    connections = sorted(connections, key=lambda c: (c["key"]["space"], c["key"]["connection_name"]))
    return {"schema": SCHEMA, "identity_schema": IDENTITY_SCHEMA, "provenance": provenance, "scope": scope,
            "coverage": coverage, "connections": connections}


# ───────────────────────────── fuente: documentos del CLI ─────────────────────────────
def _cli_values(doc: dict) -> dict:
    """Valores del allowlist desde UN documento `get`, con los parsers del Catálogo publicado (`generate_space_catalog`)."""
    from tools.catalog import generate_space_catalog as gsc
    # MISMA semántica que el lector del Catálogo (`catalog_service._parse_connection_md`: `info.update(database)`): la tabla Database del render
    # se fusiona en ConnectionInfo, así que acá también; si no, la `version` de la base aparecería como un falso cambio de endpoint.
    info = {**gsc._connection_info(doc), **gsc._connection_database(doc)}
    features = gsc._connection_features(doc)
    gateway = gsc._connection_gateway(doc)
    auth = gsc._connection_authentication(doc)
    first = next(iter(((doc.get("content") or {}).get("objects") or {}).values()), {}) or {}
    agent_loc = first.get("location") if isinstance(first.get("location"), dict) else {}
    return {
        "type_id": doc.get("typeId"), "adapter": doc.get("adapter"), "remote_source": doc.get("remoteSourceName"), "kind": info.get("kind"),
        "connection_type": info.get("connectionType"), "host": info.get("host"), "port": info.get("port"), "sysid": info.get("sysid"),
        "systemnumber": info.get("systemnumber"), "client": info.get("client"), "server_group": info.get("serverGroup"),
        "dbtype": info.get("dbtype"), "driver_class": info.get("driverClass"), "url_endpoint": url_endpoint(info.get("url")),
        "version": info.get("version"),
        "location": doc.get("location"), "agent_name": doc.get("agentName"), "agent_group": doc.get("agentGroupName"),
        "cloud_connector": gateway.get("cloud_connector"), "cloud_connector_location": gateway.get("cloudConnectorLocation"),
        "virtual_host": gateway.get("virtualHost"), "virtual_port": gateway.get("virtualPort"),
        "remote_tables": features.get("remoteTables"), "dataflows": features.get("dataflows"),
        "replicationflows": features.get("replicationflows"), "streaming": features.get("streaming"),
        "credential_mode": doc.get("credentialMode"), "auth_type": auth.get("auth_type") or info.get("authentication"),
        "langcode": info.get("langcode"), "odpversion": info.get("odpversion"), "poolsize": info.get("poolsize"), "connlimit": info.get("connlimit"),
        "business_name": doc.get("businessName"),
        "agent_status": doc.get("agentStatus"), "agent_connected": agent_loc.get("connected"),
        "replication_status": doc.get("realtimeReplicationStatus") or doc.get("disReplicationStatus"),
    }


def from_cli_documents(*, client: str, tenant_id, snapshot_id: str, captured_at: str, status: str, exhaustiveness: str,
                       listings: dict, details: dict, space_environments: dict | None = None, tenant_governed: bool = False,
                       source_kind: str = "cli_extraction", operation_id: str | None = None, space_discovery: str = "NOT_PERFORMED") -> dict:
    """Observación desde documentos del CLI.

    `listings`: {space: [ítems de `connections list`] | None (Space inaccesible)} — define el alcance pedido.
    `details`:  {(space, nombre): documento `get` | None (lectura fallida)}.
    `space_environments`: mapa GOBERNADO space → environment (sin él, `environment` = None)."""
    envs = space_environments or {}
    connections = []
    seen = set()
    for space, items in sorted(listings.items()):
        for item in items or []:
            name = item.get("name") if isinstance(item, dict) else None
            if not name or (space, name) in seen:
                continue
            seen.add((space, name))
            doc = details.get((space, name))
            if isinstance(doc, dict):
                fields = _fields_state(_cli_values(doc))
                state = DETAIL_READ
            else:                                                    # listada pero sin detalle legible: solo lo que trae el listado
                fields = _fields_state({"type_id": item.get("typeId"), "business_name": item.get("businessName")},
                                       unknown=frozenset(n for n, *_ in FIELDS if n not in ("type_id", "business_name")))
                state = DETAIL_NOT_READ
            connections.append(_connection(_key(tenant_id, space, name), envs.get(space), state, fields))
    requested = sorted(listings)
    coverage = {
        "status": status, "exhaustiveness": exhaustiveness, "requested_spaces": requested,
        "accessible_spaces": sorted(s for s, v in listings.items() if v is not None),
        "inaccessible_spaces": sorted(s for s, v in listings.items() if v is None),
        "discovered_connections": len(connections),
        "read_connections": sum(1 for c in connections if c["detail_state"] == DETAIL_READ),
    }
    provenance = {"source_kind": source_kind, "snapshot_id": snapshot_id, "captured_at": captured_at, "client": client,
                  "tenant_id": tenant_id or None, "tenant_governed": bool(tenant_governed and tenant_id), "operation_id": operation_id}
    return _document(provenance, {"space_discovery": space_discovery}, coverage, connections)


# ───────────────────────────── fuente: Catálogo publicado ─────────────────────────────
def _prop(props: dict, label: str):
    want = _fold(label)
    for k, v in (props or {}).items():
        if _fold(str(k)) == want:
            return v
    return None


def _catalog_values(entry) -> dict:
    """Valores del allowlist desde UNA entrada del Catálogo publicado (lo que `render_connection` escribió con los mismos parsers)."""
    info, feats, gateway, auth, props = entry.info or {}, entry.feature_values or {}, entry.gateway or {}, entry.authentication or {}, entry.props or {}
    return {
        "type_id": entry.type_id, "adapter": entry.adapter or _prop(props, "Adapter"), "remote_source": entry.remote_source, "kind": info.get("kind"),
        "connection_type": info.get("connectionType"), "host": info.get("host"), "port": info.get("port"), "sysid": info.get("sysid"),
        "systemnumber": info.get("systemnumber"), "client": info.get("client"), "server_group": info.get("serverGroup"),
        "dbtype": info.get("dbtype"), "driver_class": info.get("driverClass"), "url_endpoint": url_endpoint(info.get("url")),
        "version": info.get("version"),
        "location": _prop(props, "Ubicación"), "agent_name": _prop(props, "Agente"), "agent_group": _prop(props, "Grupo de agente"),
        "cloud_connector": gateway.get("cloud_connector"), "cloud_connector_location": gateway.get("cloudConnectorLocation"),
        "virtual_host": gateway.get("virtualHost"), "virtual_port": gateway.get("virtualPort"),
        "remote_tables": feats.get("remoteTables"), "dataflows": feats.get("dataflows"), "replicationflows": feats.get("replicationflows"),
        "streaming": feats.get("streaming"),
        "credential_mode": _prop(props, "Modo credencial"), "auth_type": auth.get("auth_type") or info.get("authentication"),
        "langcode": info.get("langcode"), "odpversion": info.get("odpversion"), "poolsize": info.get("poolsize"), "connlimit": info.get("connlimit"),
        "business_name": entry.business_name or _prop(props, "Nombre funcional"),
        "agent_status": entry.agent_status or _prop(props, "Estado agente"), "agent_connected": None,
        "replication_status": _prop(props, "Estado replicación realtime"),
    }


def from_published_catalog(entries, *, client: str, catalog_ref: str = "catalog_local/arquitectura") -> dict:
    """Observación desde el Catálogo publicado de UN cliente. Sin tenant (el Catálogo no lo registra), sin fecha de evidencia y sin cobertura
    declarada: la reconciliación la trata como base de procedencia LIMITADA, nunca como «último estado SAP»."""
    rows = [e for e in entries if (e.cliente or "").strip().lower() == (client or "").strip().lower()]
    connections = [_connection(_key(None, e.space, e.connection), None, DETAIL_READ, _fields_state(_catalog_values(e), CATALOG_UNRENDERED))
                   for e in rows]
    spaces = sorted({e.space for e in rows})
    coverage = {"status": "UNKNOWN", "exhaustiveness": "UNKNOWN", "requested_spaces": spaces, "accessible_spaces": spaces,
                "inaccessible_spaces": [], "discovered_connections": len(connections), "read_connections": len(connections)}
    provenance = {"source_kind": "published_catalog", "snapshot_id": None, "captured_at": None, "client": client, "tenant_id": None,
                  "tenant_governed": False, "operation_id": None, "catalog_ref": catalog_ref}
    return _document(provenance, {"space_discovery": "NOT_PERFORMED"}, coverage, connections)


# ───────────────────────────── validación ─────────────────────────────
_FORBIDDEN = re.compile(r"(password|passwd|secret|token|credential_value|client_secret|\buser(name)?\b)", re.I)


def validate(doc: dict) -> list[str]:
    """Problemas del documento (lista vacía = válido): forma del contrato, allowlist de campos y ausencia de marcadores de credenciales."""
    problems = []
    if doc.get("schema") != SCHEMA:
        return [f"schema distinto de {SCHEMA}"]
    for c in doc.get("connections", []):
        if set(c["fields"]) != set(FIELD_GROUP):
            problems.append(f"{c['key']}: campos fuera del contrato")
        if any(v.get("state") not in (DECLARED, ABSENT, UNKNOWN) for v in c["fields"].values()):
            problems.append(f"{c['key']}: estado de campo inválido")
    text = _canon(doc)
    if _FORBIDDEN.search(re.sub(r'"(credential_mode|auth_type)"', "", text)):
        problems.append("contiene marcadores de credenciales")
    return problems
