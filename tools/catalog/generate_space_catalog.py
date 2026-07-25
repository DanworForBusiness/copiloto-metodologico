
"""
generate_space_catalog.py — Catálogo operativo de espacios y conexiones Datasphere
================================================================================
PILOTO / LOCALHOST — NO productivo. Lee exports JSON obtenidos por CLI de
Datasphere y emite markdown para consultar arquitectura/conectividad por cliente:

Entrada esperada, por cliente:
  <Cliente>/Espacios/<SPACE>.json
  <Cliente>/Conexiones/conn_space_<SPACE>.json
  <Cliente>/Conexiones/conn_space_<SPACE>_<CONN_ID>.json

También tolera que `--all` apunte directamente a la carpeta del cliente.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SPACE_CATALOG_DIR = "arquitectura"
CATALOG_MANIFEST = ".catalog_manifest.json"
SPACE_SOURCE_EXTENSIONS = {".json"}


@dataclass
class SpaceRecord:
    cliente: str
    name: str
    label: str = ""
    source_language: str = ""
    assigned_storage: str = ""
    assigned_ram: str = ""
    enable_data_lake: Any = None
    allow_consumption: Any = None
    auditing: Any = None
    definitions_count: int = 0
    members_count: int = 0
    dbusers_count: int = 0
    hdicontainers_count: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectionRecord:
    cliente: str
    space: str
    name: str
    business_name: str = ""
    type_id: str = ""
    creator: str = ""
    creation_date: str = ""
    status: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClientCatalog:
    cliente: str
    spaces: dict[str, SpaceRecord] = field(default_factory=dict)
    connections: dict[str, list[ConnectionRecord]] = field(default_factory=dict)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _space_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _space_generator_signature() -> str:
    try:
        return _space_sha256(Path(__file__))
    except Exception:
        return "unknown"


def _catalog_manifest_path(out_root: Path) -> Path:
    return Path(out_root) / CATALOG_MANIFEST


def _load_catalog_manifest(out_root: Path) -> dict[str, Any]:
    path = _catalog_manifest_path(out_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_catalog_manifest(out_root: Path, manifest: dict[str, Any]) -> None:
    path = _catalog_manifest_path(out_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _client_manifest_key(root: Path, inbound_root: Path, cliente: str) -> str:
    try:
        rel = root.relative_to(inbound_root).as_posix()
    except ValueError:
        rel = ""
    return rel if rel and rel != "." else cliente.replace(" ", "_")


def _client_source_signature(root: Path, inbound_root: Path) -> dict[str, Any]:
    files = []
    for folder_name in ("Espacios", "Conexiones"):
        folder = root / folder_name
        if not folder.is_dir():
            continue
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            if path.suffix.lower() not in SPACE_SOURCE_EXTENSIONS:
                continue
            stat = path.stat()
            # Sin mtime: la descarga de SharePoint usa un temp nuevo cada vez → el
            # mtime siempre cambia. Identidad estable = sha256 (contenido) + ruta.
            files.append({
                "path": path.relative_to(inbound_root).as_posix(),
                "size": stat.st_size,
                "sha256": _space_sha256(path),
            })
    return {"files": files}


def _client_outputs_exist(out_root: Path, catalog: ClientCatalog) -> bool:
    base = Path(out_root) / SPACE_CATALOG_DIR / _slug(catalog.cliente)
    if not (base / "arquitectura_conectividad.md").exists():
        return False
    for space_name in sorted(set(catalog.spaces) | set(catalog.connections)):
        if space_name in catalog.spaces and not (base / "espacios" / f"{_slug(space_name)}.md").exists():
            return False
        for conn in catalog.connections.get(space_name, []):
            if not (base / "conexiones" / f"{_slug(space_name)}__{_slug(conn.name)}.md").exists():
                return False
    return True


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", text.strip())
    return cleaned.strip("_") or "sin_nombre"


def _md(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Sí" if value else "No"
    text = str(value).replace("|", "\\|").strip()
    return text or "—"


def _code(value: Any) -> str:
    if value is None or value == "":
        return "—"
    text = str(value).strip().replace("`", "")
    return f"`{text}`" if text else "—"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "si", "sí", "s", "y"}
    return bool(value)


def _short_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True).replace("|", "\\|")
    return _md(value)


_CAPABILITY_ALIASES: dict[str, tuple[str, ...]] = {
    "dataflows": (
        "dataflows",
        "dataFlows",
        "capabilityDataflowSource",
        "capabilityDataFlowSource",
        "capabilityDataflowTarget",
        "capabilityDataFlowTarget",
    ),
    "replicationflows": (
        "replicationflows",
        "replicationFlows",
        "capabilityReplicationflowSource",
        "capabilityReplicationFlowSource",
        "capabilityReplicationflowTarget",
        "capabilityReplicationFlowTarget",
    ),
    "remoteTables": (
        "remoteTables",
        "remotetables",
        "remoteTable",
        "remoteTablesEnabled",
        "capabilityRemoteTables",
        "capabilityRemoteTableSource",
        "capabilityRemoteTableTarget",
    ),
    "streaming": ("streaming",),
}


def _feature_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _collect_scalar_features(value: Any, out: dict[str, Any]) -> None:
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if isinstance(item, dict):
            _collect_scalar_features(item, out)
        elif not isinstance(item, list):
            out.setdefault(str(key), item)


def _find_feature_value(value: Any, aliases: tuple[str, ...]) -> Any:
    alias_keys = {_feature_key(alias) for alias in aliases}
    if isinstance(value, dict):
        for key, item in value.items():
            if _feature_key(key) in alias_keys:
                return item
        for item in value.values():
            found = _find_feature_value(item, aliases)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_feature_value(item, aliases)
            if found is not None:
                return found
    return None


def _find_feature_value_matching(value: Any, aliases: tuple[str, ...], predicate) -> Any:
    alias_keys = {_feature_key(alias) for alias in aliases}
    if isinstance(value, dict):
        for key, item in value.items():
            if _feature_key(key) in alias_keys and predicate(item):
                return item
        for item in value.values():
            found = _find_feature_value_matching(item, aliases, predicate)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_feature_value_matching(item, aliases, predicate)
            if found is not None:
                return found
    return None


def _normalize_connection_features(connection_features: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    _collect_scalar_features(connection_features, merged)

    data_access = str(
        merged.get("dataAccess")
        or _find_feature_value(connection_features, ("dataAccess",))
        or _find_feature_value(detail, ("dataAccess",))
        or ""
    ).lower()

    for canonical, aliases in _CAPABILITY_ALIASES.items():
        value = _find_feature_value(connection_features, aliases)
        if value is None:
            value = _find_feature_value(detail, aliases)
        if value is not None:
            merged[canonical] = _truthy(value)

    if "federation" in data_access:
        merged.setdefault("remoteTables", True)
    if "replication" in data_access:
        merged.setdefault("dataflows", True)
        merged.setdefault("replicationflows", True)

    return merged


def _frontmatter(auto: dict[str, Any], manual: dict[str, Any] | None = None) -> str:
    body = yaml.safe_dump(
        {"auto": auto, "manual": manual or {}},
        allow_unicode=True,
        sort_keys=False,
    )
    return (
        "---\n"
        "# AUTO — generado por el script desde JSON Datasphere (NO editar a mano)\n"
        "# MANUAL — lo completa el consultor / senior\n"
        f"{body}---\n"
    )


def _read_manual(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    match = re.match(r"^---\n(.*?)\n---", path.read_text(encoding="utf-8", errors="ignore"), re.S)
    if not match:
        return {}
    try:
        fm = yaml.safe_load(match.group(1)) or {}
    except Exception:
        return {}
    return fm.get("manual") or {}


def _space_definition(payload: Any, fallback_name: str) -> tuple[str, dict[str, Any], int]:
    if isinstance(payload, dict) and len(payload) == 1:
        name, body = next(iter(payload.items()))
        if isinstance(body, dict):
            defs = body.get("definitions") or {}
            return str(name), body.get("spaceDefinition") or {}, len(defs) if isinstance(defs, dict) else 0
    if isinstance(payload, dict):
        defs = payload.get("definitions") or {}
        return fallback_name, payload.get("spaceDefinition") or payload, len(defs) if isinstance(defs, dict) else 0
    return fallback_name, {}, 0


def _space_from_file(cliente: str, path: Path) -> SpaceRecord:
    payload = _load_json(path)
    name, sd, definitions_count = _space_definition(payload, path.stem)
    return SpaceRecord(
        cliente=cliente,
        name=name,
        label=str(sd.get("label") or name),
        source_language=str(sd.get("sourceLanguage") or ""),
        assigned_storage=str(sd.get("assignedStorage") if sd.get("assignedStorage") is not None else ""),
        assigned_ram=str(sd.get("assignedRam") if sd.get("assignedRam") is not None else ""),
        enable_data_lake=sd.get("enableDataLake"),
        allow_consumption=sd.get("allowConsumption"),
        auditing=sd.get("auditing"),
        definitions_count=definitions_count,
        members_count=len(sd.get("members") or []),
        dbusers_count=len(sd.get("dbusers") or []),
        hdicontainers_count=len(sd.get("hdicontainers") or []),
        raw=sd,
    )


def _connection_summary(cliente: str, space: str, item: dict[str, Any]) -> ConnectionRecord:
    return ConnectionRecord(
        cliente=cliente,
        space=space,
        name=str(item.get("name") or ""),
        business_name=str(item.get("businessName") or ""),
        type_id=str(item.get("typeId") or ""),
        creator=str(item.get("creator") or ""),
        creation_date=str(item.get("creation_date") or ""),
        status=str(item.get("realtimeReplicationStatus") or item.get("disReplicationStatus") or ""),
    )


def _connection_detail(cliente: str, path: Path) -> ConnectionRecord | None:
    payload = _load_json(path)
    if not isinstance(payload, dict) or isinstance(payload, list):
        return None
    name = str(payload.get("name") or path.stem)
    space = str(payload.get("space_name") or payload.get("space_id") or "")
    if not space:
        stem = path.stem.removeprefix("conn_space_")
        space = stem.split("_", 1)[0]
    return ConnectionRecord(
        cliente=cliente,
        space=space,
        name=name,
        business_name=str(payload.get("businessName") or name),
        type_id=str(payload.get("typeId") or ""),
        creator=str(payload.get("creator") or ""),
        creation_date=str(payload.get("creation_date") or ""),
        status=str(payload.get("realtimeReplicationStatus") or payload.get("disReplicationStatus") or ""),
        detail=payload,
    )


def _connection_info(detail: dict[str, Any]) -> dict[str, Any]:
    objects = (((detail.get("content") or {}).get("objects") or {}))
    if not isinstance(objects, dict) or not objects:
        return {}
    first = next(iter(objects.values()))
    return (((first or {}).get("configuration") or {}).get("ConnectionProperties") or {}).get("ConnectionInfo") or {}


def _connection_features(detail: dict[str, Any]) -> dict[str, Any]:
    objects = (((detail.get("content") or {}).get("objects") or {}))
    connection_features: dict[str, Any] = {}
    if isinstance(objects, dict) and objects:
        first = next(iter(objects.values()))
        raw_features = (((first or {}).get("configuration") or {}).get("ConnectionFeatures") or {})
        if isinstance(raw_features, dict):
            connection_features = raw_features
    return _normalize_connection_features(connection_features, detail)


def _connection_authentication(detail: dict[str, Any]) -> dict[str, Any]:
    auth_type = _find_feature_value_matching(
        detail,
        ("auth_type",),
        lambda item: item not in (None, "") and not isinstance(item, (dict, list)),
    )
    if auth_type in (None, ""):
        return {}
    return {"auth_type": auth_type}


def _connection_database(detail: dict[str, Any]) -> dict[str, Any]:
    database = _find_feature_value_matching(detail, ("database",), lambda item: isinstance(item, dict))
    return database if isinstance(database, dict) else {}


def _connection_gateway(detail: dict[str, Any]) -> dict[str, Any]:
    gateway = _find_feature_value_matching(detail, ("gateway",), lambda item: isinstance(item, dict))
    return gateway if isinstance(gateway, dict) else {}


def _discover_client_roots(inbound_root: Path) -> list[tuple[str, Path]]:
    inbound_root = Path(inbound_root)
    if (inbound_root / "Espacios").is_dir() or (inbound_root / "Conexiones").is_dir():
        return [(inbound_root.name.replace("_", " "), inbound_root)]
    roots = []
    for child in sorted(p for p in inbound_root.iterdir() if p.is_dir()):
        if (child / "Espacios").is_dir() or (child / "Conexiones").is_dir():
            roots.append((child.name.replace("_", " "), child))
    return roots


def load_spaces_and_connections(inbound_root: Path) -> dict[str, ClientCatalog]:
    catalogs: dict[str, ClientCatalog] = {}
    for cliente, root in _discover_client_roots(Path(inbound_root)):
        catalog = ClientCatalog(cliente=cliente)
        spaces_dir = root / "Espacios"
        conn_dir = root / "Conexiones"

        if spaces_dir.is_dir():
            for path in sorted(spaces_dir.glob("*.json")):
                rec = _space_from_file(cliente, path)
                catalog.spaces[rec.name] = rec

        details: dict[tuple[str, str], ConnectionRecord] = {}
        summaries: dict[str, list[ConnectionRecord]] = {}
        if conn_dir.is_dir():
            for path in sorted(conn_dir.glob("conn_space_*.json")):
                try:
                    payload = _load_json(path)
                except Exception:
                    continue
                if isinstance(payload, list):
                    space = path.stem.removeprefix("conn_space_")
                    summaries.setdefault(space, [])
                    for item in payload:
                        if isinstance(item, dict):
                            summaries[space].append(_connection_summary(cliente, space, item))
                elif isinstance(payload, dict):
                    rec = _connection_detail(cliente, path)
                    if rec:
                        details[(rec.space, rec.name)] = rec

        for space, rows in summaries.items():
            merged = []
            for row in rows:
                detail = details.get((space, row.name)) or details.get((row.space, row.name))
                if detail:
                    detail.business_name = detail.business_name or row.business_name
                    detail.creator = detail.creator or row.creator
                    detail.creation_date = detail.creation_date or row.creation_date
                    detail.status = detail.status or row.status
                    merged.append(detail)
                else:
                    merged.append(row)
            catalog.connections[space] = merged

        for (space, _name), detail in details.items():
            if not any(c.name == detail.name for c in catalog.connections.get(space, [])):
                catalog.connections.setdefault(space, []).append(detail)

        if catalog.spaces or catalog.connections:
            catalogs[cliente] = catalog
    return catalogs


def _space_findings(space: SpaceRecord, conns: list[ConnectionRecord]) -> list[tuple[str, str, str]]:
    findings: list[tuple[str, str, str]] = []
    if not space.source_language:
        findings.append(("medio", "Source Language no informado", "Definir explícitamente el idioma del espacio antes de modelar."))
    if str(space.assigned_storage or "0") in {"", "0", "0.0"} and str(space.assigned_ram or "0") in {"", "0", "0.0"}:
        findings.append(("nota", "Quota no dimensionada en el export", "Validar sizing real del tenant y distribución por ambiente/dominio."))
    for conn in conns:
        label = f"{conn.name} {conn.business_name}".upper()
        if "NO USAR" in label:
            findings.append(("alto", f"Conexión marcada como NO USAR: {conn.name}", "Depurar o aislar conexiones no operativas para evitar consumo accidental."))
        if (conn.detail.get("agentStatus") or "").upper() not in {"", "CONNECTED"}:
            findings.append(("medio", f"Agente no conectado: {conn.name}", f"Estado reportado: {conn.detail.get('agentStatus')}"))
    if not findings:
        findings.append(("nota", "Sin alertas automáticas relevantes", "Revisión técnica inicial sin hallazgos críticos desde el JSON."))
    return findings


def render_space(space: SpaceRecord, conns: list[ConnectionRecord], manual: dict[str, Any] | None = None) -> str:
    auto = {
        "tipo": "space",
        "cliente": space.cliente,
        "space": space.name,
        "source_language": space.source_language or None,
        "assigned_storage": space.assigned_storage or None,
        "assigned_ram": space.assigned_ram or None,
        "connections": [c.name for c in conns],
    }
    rows = [
        ("Nombre técnico", _code(space.name)),
        ("Label", _md(space.label)),
        ("Source Language", _code(space.source_language)),
        ("Storage asignado", _md(space.assigned_storage)),
        ("RAM asignada", _md(space.assigned_ram)),
        ("HDL habilitado", _md(space.enable_data_lake)),
        ("Consumo externo habilitado", _md(space.allow_consumption)),
        ("Auditoría", _short_value(space.auditing)),
        ("Miembros", _md(space.members_count)),
        ("Schema users", _md(space.dbusers_count)),
        ("HDI containers", _md(space.hdicontainers_count)),
        ("Objetos detectados", _md(space.definitions_count)),
    ]
    md = [
        _frontmatter(auto, manual),
        f"# {{dw-black}}Espacio · {{dw-gold}}{space.name}",
        "",
        "> Configuración operativa del espacio Datasphere y sus conexiones asociadas.",
        "",
        "| Propiedad | Valor |",
        "|---|---|",
    ]
    md += [f"| {k} | {v} |" for k, v in rows]
    md += ["", "## {dw-black}Conexiones del {dw-gold}Espacio", ""]
    if conns:
        md += ["| Conexión | Tipo | Ubicación | Agente | Estado | Capacidades |", "|---|---|---|---|---|---|"]
        for c in sorted(conns, key=lambda x: x.name.lower()):
            features = _connection_features(c.detail)
            caps = []
            for key, label in (("dataflows", "Data Flow fuente"), ("replicationflows", "Replication Flow fuente"), ("remoteTables", "Tablas remotas"), ("streaming", "Streaming")):
                if _truthy(features.get(key)):
                    caps.append(label)
            md.append(
                f"| {_code(c.name)} | {_code(c.type_id)} | {_md(c.detail.get('location'))} | "
                f"{_code(c.detail.get('agentName'))} | {_md(c.detail.get('agentStatus') or c.status)} | {_md(', '.join(caps))} |"
            )
    else:
        md.append("Sin conexiones detectadas para este espacio.")
    md += ["", "## {dw-black}Lectura {dw-gold}Danwor", ""]
    for sev, title, evidence in _space_findings(space, conns):
        icon = {"alto": "\U0001F534", "medio": "\U0001F7E1", "nota": "\U0001F535"}.get(sev, "•")
        md.append(f"- {icon} **{title}**  ")
        md.append(f"  _Evidencia:_ {evidence}")
    md.append("")
    return "\n".join(md)


def render_connection(conn: ConnectionRecord, manual: dict[str, Any] | None = None) -> str:
    detail = conn.detail or {}
    info = _connection_info(detail)
    features = _connection_features(detail)
    authentication = _connection_authentication(detail)
    database = _connection_database(detail)
    gateway = _connection_gateway(detail)
    auto = {
        "tipo": "connection",
        "cliente": conn.cliente,
        "space": conn.space,
        "connection": conn.name,
        "type_id": conn.type_id or None,
        "remote_source": detail.get("remoteSourceName") or None,
    }
    rows = [
        ("Espacio", _code(conn.space)),
        ("Nombre técnico", _code(conn.name)),
        ("Nombre funcional", _md(conn.business_name)),
        ("Tipo", _code(conn.type_id)),
        ("Adapter", _code(detail.get("adapter"))),
        ("Ubicación", _md(detail.get("location"))),
        ("Modo credencial", _md(detail.get("credentialMode"))),
        ("Remote source", _code(detail.get("remoteSourceName"))),
        ("Agente", _code(detail.get("agentName"))),
        ("Grupo de agente", _code(detail.get("agentGroupName"))),
        ("Estado agente", _md(detail.get("agentStatus"))),
        ("Estado replicación realtime", _md(detail.get("realtimeReplicationStatus") or conn.status)),
    ]
    md = [
        _frontmatter(auto, manual),
        f"# {{dw-black}}Conexión · {{dw-gold}}{conn.name}",
        "",
        "> Detalle técnico de conexión Datasphere exportado por CLI.",
        "",
        "| Propiedad | Valor |",
        "|---|---|",
    ]
    md += [f"| {k} | {v} |" for k, v in rows]
    if info or gateway:
        md += ["", "## {dw-black}Connection {dw-gold}Info", "", "| Parámetro | Valor |", "|---|---|"]
        for key in ("connectionType", "host", "port", "serverGroup", "systemnumber", "client", "sysid", "langcode", "odpversion", "poolsize", "connlimit", "gateway", "gwhost", "gwserv"):
            if key in info:
                md.append(f"| {key} | {_short_value(info.get(key))} |")
        if gateway and "gateway" not in info:
            md.append(f"| gateway | {_short_value(gateway)} |")
    if database:
        md += ["", "## {dw-black}Database", "", "| Parámetro | Valor |", "|---|---|"]
        preferred = ("pds_always_on", "pds_server_name", "pds_port_number", "pds_database_name", "version")
        emitted = set()
        for key in preferred:
            if key in database:
                md.append(f"| {key} | {_short_value(database.get(key))} |")
                emitted.add(key)
        for key in sorted(k for k in database if k not in emitted):
            md.append(f"| {key} | {_short_value(database.get(key))} |")
    if authentication:
        md += ["", "## {dw-black}Authentication", "", "| Parámetro | Valor |", "|---|---|"]
        for key in sorted(authentication):
            md.append(f"| {key} | {_md(authentication.get(key))} |")
    if features:
        md += ["", "## {dw-black}Capacidades {dw-gold}Declaradas", "", "| Feature | Valor |", "|---|---|"]
        for key in sorted(features):
            md.append(f"| {key} | {_md(features.get(key))} |")
    md.append("")
    return "\n".join(md)


def render_client_index(catalog: ClientCatalog, manual: dict[str, Any] | None = None) -> str:
    auto = {
        "tipo": "spaces_index",
        "cliente": catalog.cliente,
        "spaces": sorted(catalog.spaces),
        "connections": sum(len(v) for v in catalog.connections.values()),
    }
    md = [
        _frontmatter(auto, manual),
        f"# {{dw-black}}Arquitectura y Conectividad · {{dw-gold}}{catalog.cliente}",
        "",
        "> Inventario operativo de espacios Datasphere y conexiones contra sistemas origen.",
        "",
        "| Espacio | Source Language | Storage | RAM | Conexiones | Objetos |",
        "|---|---|---|---|---:|---:|",
    ]
    for name in sorted(catalog.spaces):
        space = catalog.spaces[name]
        md.append(
            f"| {_code(name)} | {_code(space.source_language)} | {_md(space.assigned_storage)} | "
            f"{_md(space.assigned_ram)} | {len(catalog.connections.get(name, []))} | {space.definitions_count} |"
        )
    for name in sorted(set(catalog.connections) - set(catalog.spaces)):
        md.append(f"| {_code(name)} | — | — | — | {len(catalog.connections.get(name, []))} | — |")
    md.append("")
    return "\n".join(md)


def write_client_catalog(catalog: ClientCatalog, out_root: Path) -> str:
    base = Path(out_root) / SPACE_CATALOG_DIR / _slug(catalog.cliente)
    spaces_dir = base / "espacios"
    conns_dir = base / "conexiones"
    spaces_dir.mkdir(parents=True, exist_ok=True)
    conns_dir.mkdir(parents=True, exist_ok=True)

    index_path = base / "arquitectura_conectividad.md"
    index_path.write_text(render_client_index(catalog, _read_manual(index_path)), encoding="utf-8")

    space_count = 0
    conn_count = 0
    all_spaces = sorted(set(catalog.spaces) | set(catalog.connections))
    for space_name in all_spaces:
        space = catalog.spaces.get(space_name)
        conns = catalog.connections.get(space_name, [])
        if space:
            path = spaces_dir / f"{_slug(space_name)}.md"
            path.write_text(render_space(space, conns, _read_manual(path)), encoding="utf-8")
            space_count += 1
        for conn in conns:
            path = conns_dir / f"{_slug(space_name)}__{_slug(conn.name)}.md"
            path.write_text(render_connection(conn, _read_manual(path)), encoding="utf-8")
            conn_count += 1
    return f"[OK] {catalog.cliente}: {space_count} espacio(s), {conn_count} conexión(es)"


def generate_all(inbound_root, out_root) -> str:
    inbound_root = Path(inbound_root)
    out_root = Path(out_root)
    client_roots = _discover_client_roots(inbound_root)
    if not client_roots:
        return "Catálogo de espacios/conexiones: sin carpetas Espacios/Conexiones."

    catalogs = load_spaces_and_connections(inbound_root)
    if not catalogs:
        return "Catálogo de espacios/conexiones: sin datos válidos para generar."

    generator_sig = _space_generator_signature()
    manifest = _load_catalog_manifest(out_root)
    previous_sig = manifest.get("spaces_generator_sha256")
    force_all = previous_sig != generator_sig
    entries = manifest.get("spaces_connections", {}) if not force_all else {}

    root_by_cliente = {cliente: root for cliente, root in client_roots}
    lines: list[str] = []
    generated = 0
    skipped = 0
    next_entries: dict[str, Any] = {}

    for cliente, catalog in sorted(catalogs.items(), key=lambda item: item[0].lower()):
        root = root_by_cliente.get(cliente)
        if root is None:
            continue
        key = _client_manifest_key(root, inbound_root, cliente)
        signature = _client_source_signature(root, inbound_root)
        previous = entries.get(key, {})
        if (not force_all
                and previous.get("signature") == signature
                and _client_outputs_exist(out_root, catalog)):
            skipped += 1
            next_entries[key] = previous
            continue

        lines.append(write_client_catalog(catalog, out_root))
        generated += 1
        next_entries[key] = {
            "cliente": cliente,
            "signature": signature,
            "spaces": sorted(catalog.spaces),
            "connections": sum(len(v) for v in catalog.connections.values()),
        }

    next_manifest = dict(manifest)
    next_manifest.update({
        "spaces_generator_sha256": generator_sig,
        "spaces_connections": next_entries,
    })
    _save_catalog_manifest(out_root, next_manifest)

    if not lines:
        lines.append("[OK] Sin cambios en espacios/conexiones; se conserva el catálogo generado.")
    if force_all and previous_sig:
        lines.insert(0, "[info] Cambió el generador de espacios/conexiones; se regeneró el catálogo operativo.")

    summary = f"Catálogo de espacios/conexiones regenerado ({generated} cliente(s), {skipped} sin cambios):"
    return summary + "\n" + "\n".join(lines)

def main() -> None:
    ap = argparse.ArgumentParser(description="Generador del catálogo operativo de espacios y conexiones Datasphere")
    ap.add_argument("--all", dest="inbound", required=True, help="Raíz de entrada o carpeta de cliente")
    ap.add_argument("--out", dest="out", required=True, help="Carpeta de salida")
    args = ap.parse_args()
    print(generate_all(args.inbound, args.out))


if __name__ == "__main__":
    main()
