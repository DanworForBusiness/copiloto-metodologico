"""
catalog_service.py — Consumo del Catálogo de Vistas/Flujos CDS (Fase B, piloto)
==============================================================================
PILOTO / LOCALHOST — NO productivo. Lee los .md de flujo generados por
generate_catalog.py, los indexa y los ofrece al asistente como evidencia
interna, ORDENADOS por mirada Danwor (🟢 plena → 🟡 buena → 🟠 parcial).

Fuente pluggable: hoy una carpeta local (los .md generados); mañana una
biblioteca de SharePoint vía Graph (mismo patrón que sharepoint_cases.py:
token delegado, sin persistir, cache TTL) — solo cambia load_catalog().

Responde el caso de uso: "¿tenemos un modelo de stock?" →
"Sí: Biogenesis 🟢 (referencia), Danone 🟡, Loma Negra 🟠. ¿Los ves por cliente?"
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# yaml.safe_load() siempre usa el SafeLoader puro-Python de PyYAML, incluso
# cuando libyaml (CSafeLoader, en C) está instalado. Para frontmatter grande
# (ej. Data Products con ~150+ campos embebidos) eso es ~8x más lento medido
# (400ms vs 50ms). Mismo resultado, mismo modo seguro — solo el loader cambia.
try:
    _YAML_LOADER = yaml.CSafeLoader
except AttributeError:  # libyaml no instalado en este entorno
    _YAML_LOADER = yaml.SafeLoader


def _yaml_load(text: str):
    return yaml.load(text, Loader=_YAML_LOADER)


_STOPWORDS = {
    "a", "al", "algun", "alguna", "como", "con", "de", "del", "el", "en", "hay",
    "la", "las", "lo", "los", "me", "necesito", "para", "por", "que", "quiero",
    "se", "si", "tenemos", "tienen", "un", "una", "uno", "y",
}
# Sinónimos de dominio (para que "stock" alcance "inventario" y viceversa).
_DOMAIN_SYNONYMS = [
    {"stock", "inventario", "inventarios", "existencias", "almacen", "almacenes"},
    {"venta", "ventas", "facturacion", "sales"},
    {"compra", "compras", "purchasing"},
    {"finanzas", "financiero", "costeo", "costos"},
]
_VERDICT_RANK = {"plena": 0, "buena": 1, "parcial": 2}
_VERDICT_ICON = {"plena": "🟢", "buena": "🟡", "parcial": "🟠"}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _tokens(text: str) -> set[str]:
    base = {t for t in _norm(text).split() if len(t) >= 3 and t not in _STOPWORDS}
    # expandir con sinónimos de dominio
    extra: set[str] = set()
    for group in _DOMAIN_SYNONYMS:
        if base & group:
            extra |= group
    return base | extra


@dataclass
class CatalogEntry:
    path: str
    cliente: str
    dominio: str
    veredicto: str
    apto: str
    artefactos: list = field(default_factory=list)
    intents: list = field(default_factory=list)
    hallazgos: list = field(default_factory=list)
    origenes: list = field(default_factory=list)
    capas: list = field(default_factory=list)
    portabilidad_bdc: dict = field(default_factory=dict)   # dimensión BDC (auto)
    portabilidad_ack: list = field(default_factory=list)   # acknowledgements (manual, senior)
    body: str = ""
    _search: str = ""

    @property
    def icon(self) -> str:
        return _VERDICT_ICON.get(self.veredicto, "⚪")


@dataclass
class CatalogConnectionEntry:
    path: str
    cliente: str
    space: str
    connection: str
    type_id: str
    remote_source: str = ""
    business_name: str = ""
    location: str = ""
    adapter: str = ""
    agent: str = ""
    agent_status: str = ""
    cloud_connector: bool = False
    dp_agent: bool = False
    data_provisioning: str = ""
    features: list[str] = field(default_factory=list)
    props: dict[str, str] = field(default_factory=dict)
    info: dict[str, str] = field(default_factory=dict)
    gateway: dict[str, str] = field(default_factory=dict)
    authentication: dict[str, str] = field(default_factory=dict)
    feature_values: dict[str, str] = field(default_factory=dict)

    @property
    def type_label(self) -> str:
        return connection_type_label(self.type_id)



@dataclass
class CatalogSecurityEntry:
    path: str
    cliente: str
    criterio: str
    dac: str
    family: str = "data"
    source_entity: str = ""
    principal_element: str = ""
    veredicto: str = "parcial"
    apto: str = "Inspiraci?n conceptual con reservas"
    summary: str = ""
    spaces: list[str] = field(default_factory=list)
    governed_fields: list[str] = field(default_factory=list)
    lineage_views: list[dict[str, str]] = field(default_factory=list)
    consumers: int = 0
    role_count: int = 0
    global_role_count: int = 0
    scoped_role_count: int = 0
    roles: list[dict] = field(default_factory=list)
    permission_count: int = 0
    source_file: str = ""
    body: str = ""
    _search: str = ""

    @property
    def icon(self) -> str:
        return _VERDICT_ICON.get(self.veredicto, "?")


@dataclass
class CatalogDataProductEntry:
    path: str
    product_id: str
    title: str
    business_name: str = ""
    data_categories: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    delivery_mode: str = ""
    veredicto: str = "plena"
    apto: str = "Referencia Danwor — reutilizable"
    entities: list[dict] = field(default_factory=list)
    fields: list[dict] = field(default_factory=list)
    lineage_nodes: list[dict] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    portabilidad_bdc: dict = field(default_factory=dict)   # dimensión BDC (auto)
    portabilidad_ack: list = field(default_factory=list)   # acknowledgements (manual, senior)
    body: str = ""
    _search: str = ""

    @property
    def icon(self) -> str:
        return _VERDICT_ICON.get(self.veredicto, "?")

    @property
    def artifact_names(self) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for ent in self.entities:
            if not isinstance(ent, dict):
                continue
            if str(ent.get("kind") or "").strip().lower() != "entity":
                continue
            name = str(ent.get("name") or "").strip()
            if not name:
                continue
            tail = name.split(":", 1)[1] if ":" in name else name
            label = tail.split(".", 1)[1] if "." in tail else tail
            label = label.strip()
            key = label.lower()
            if label and key not in seen:
                seen.add(key)
                out.append(label)
        return out


_RESERVED_DOMAIN_NAMES = {"apis", "cds", "espacios", "conexiones", "seguridad", "data_products", "data products"}


def _is_reserved_domain(value: str) -> bool:
    raw = str(value or "").strip().lower()
    norm = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return raw in _RESERVED_DOMAIN_NAMES or norm in _RESERVED_DOMAIN_NAMES


_TYPE_LABELS = {
    "ABAP": "ABAP",
    "GENERICJDBC": "Generic JDBC",
    "MSSQL": "MSSQL",
    "SAPBW": "SAPBW",
    "SAPS4HANAOP": "SAP S/4HANA OP",
}


def connection_type_label(type_id: str) -> str:
    raw = (type_id or "").strip()
    key = re.sub(r"[^A-Z0-9]+", "", raw.upper())
    return _TYPE_LABELS.get(key, raw or "Sin tipo")


# Inferencia liviana de capa desde el nombre (para el foco por módulo).
_CAPA_NAMES = {"1": "Ingesta", "2": "Armonización", "3": "Propagación", "4": "Consumo"}


def _capa_of_name(name: str) -> str:
    if name and name[0] in "1234":
        return _CAPA_NAMES[name[0]]
    if name.split("_", 1)[0] == "AM":
        return "Consumo"
    return "Otro"


# Categorías ESTÁNDAR de la mirada Danwor (una de tres; NO texto libre). Es la
# Etiqueta canónica que se muestra cuando el senior confirma un veredicto. FUENTE
# ÚNICA compartida con el generador (compliance_criteria.APTO_CANONICAL): ya no es una
# copia que haya que "mantener sincronizada" — es el mismo objeto. El import tolera los
# dos modos de carga de este módulo (top-level vía sys.path, o como paquete).
try:
    from tools.catalog.compliance_criteria import APTO_CANONICAL as _APTO_ESTANDAR
except Exception:  # noqa: BLE001 — fallback si tools/ no está como paquete en el path
    import compliance_criteria as _cc
    _APTO_ESTANDAR = _cc.APTO_CANONICAL


# ── Carga (fuente local; SharePoint se enchufa acá) ───────────────────────
def _parse_md(text: str, path: str) -> CatalogEntry | None:
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return None
    try:
        fm = _yaml_load(m.group(1)) or {}
    except Exception:
        return None
    auto = fm.get("auto") or {}
    manual = fm.get("manual") or {}
    if auto.get("tipo") != "flujo":
        return None
    comp = auto.get("compliance") or {}
    hallazgos = comp.get("hallazgos") or []
    # Veredicto: el override del senior gana sobre el automático (paridad con el
    # catálogo de seguridad). El semáforo auto es un PUNTO DE PARTIDA; el senior
    # confirma UNA de las tres CATEGORÍAS estándar en el bloque `manual`. La
    # categorización NO es texto libre: el `apto` que se muestra es la frase
    # canónica de la categoría (no la nota del senior). `notas_senior` queda como
    # nota interna, no reemplaza la etiqueta estandarizada.
    _manual_verdict = str(manual.get("compliance_veredicto_confirmado")
                          or manual.get("veredicto") or "").strip().lower()
    if _manual_verdict in _APTO_ESTANDAR:
        _veredicto = _manual_verdict
        _apto = _APTO_ESTANDAR[_veredicto]
    else:
        _veredicto = str(comp.get("mirada_danwor") or "?")
        _apto = str(comp.get("apto_para") or "")
    entry = CatalogEntry(
        path=path,
        cliente=str(manual.get("cliente") or ""),
        # El dominio es del CONTENIDO: override manual del senior gana; si no, el auto
        # (inferido por el generador desde los artefactos de consumo).
        dominio=str(manual.get("dominio") or auto.get("dominio") or ""),
        veredicto=_veredicto,
        apto=_apto,
        artefactos=list(auto.get("artefactos") or []),
        intents=list(manual.get("intent_negocio") and [manual["intent_negocio"]] or []),
        hallazgos=[{"severidad": h.get("severidad", ""), "titulo": h.get("titulo", ""),
                    "evidencia": h.get("evidencia", "")} for h in hallazgos],
        origenes=list(auto.get("origenes") or []),
        portabilidad_bdc=dict(auto.get("portabilidad_bdc") or {}),
        portabilidad_ack=list(manual.get("portabilidad_ack") or []),
        body=m.group(2),
    )
    from collections import Counter
    cnt = Counter(_capa_of_name(a) for a in entry.artefactos)
    entry.capas = [f"{c} ({cnt[c]})" for c in ("Ingesta", "Armonización", "Propagación", "Consumo") if cnt.get(c)]
    entry._search = " ".join([
        entry.dominio, entry.cliente, " ".join(entry.artefactos),
        " ".join(entry.intents), " ".join(h["titulo"] for h in entry.hallazgos),
        " ".join(entry.origenes),
    ])
    return entry



def _frontmatter_and_body(text: str) -> tuple[dict, str] | tuple[None, str]:
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return None, text
    try:
        return _yaml_load(m.group(1)) or {}, m.group(2)
    except Exception:
        return None, m.group(2)


def _table_values(body: str, title: str = "") -> dict[str, str]:
    """Extract simple markdown tables: Property/Value, Parameter/Value or Feature/Value.

    When `title` is provided, first slices the section whose heading contains it.
    This avoids a greedy regex picking the next table and losing fields such as
    `gateway`.
    """
    source = body or ""
    if title:
        wanted = _norm(title)
        lines = source.splitlines()
        section: list[str] = []
        collecting = False
        for line in lines:
            if line.lstrip().startswith("## "):
                if collecting:
                    break
                collecting = wanted in _norm(line)
                continue
            if collecting:
                section.append(line)
        source = "\n".join(section)
    rows: dict[str, str] = {}
    for line in source.splitlines():
        if not line.strip().startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        header = _norm(cells[0])
        if header in {"propiedad", "feature", "parametro", "parameter"}:
            continue
        rows[cells[0]] = cells[1]
    return rows


def _clean_cell(value: str) -> str:
    value = re.sub(r"`([^`]*)`", r"\1", value or "")
    value = re.sub(r"<[^>]+>", "", value)
    value = value.strip()
    return "" if value in {"", "\u2014", "-", "None", "none", "null", "?"} else value


def _boolish_text(value: str) -> bool:
    return _norm(str(value)) in {"si", "true", "1", "on", "yes", "connected", "dpagent"}


def _connection_feature_labels(feats: dict[str, str]) -> list[str]:
    """Return UI-facing connection capabilities, not low-level technical parameters."""
    checks = [
        (("dataflows", "capabilityDataflowSource", "capabilityDataFlowSource"), "Data Flows"),
        (("replicationflows", "capabilityReplicationflowSource", "capabilityReplicationFlowSource"), "Replication Flows"),
        (("remoteTables", "remotetables", "capabilityRemoteTables"), "Remote Tables"),
        (("streaming",), "Streaming"),
    ]
    out: list[str] = []
    norm_keys = {_norm(k): v for k, v in feats.items()}
    data_access = _norm(str(feats.get("dataAccess") or norm_keys.get("dataaccess") or ""))

    if "replication" in data_access:
        out.extend(["Data Flows", "Replication Flows"])
    if "federation" in data_access:
        out.append("Remote Tables")

    for keys, label in checks:
        enabled = False
        for key in keys:
            value = feats.get(key)
            if value is None:
                value = norm_keys.get(_norm(key))
            if value is not None and _boolish_text(value):
                enabled = True
                break
        if enabled and label not in out:
            out.append(label)
    return out


def _feature_available(conn: CatalogConnectionEntry, feature: str) -> bool:
    if feature in {"Data Flows", "Replication Flows"}:
        return conn.cloud_connector
    if feature == "Remote Tables":
        provisioning = _norm(conn.data_provisioning)
        return conn.dp_agent or (bool(provisioning) and provisioning != "dpagent")
    return True


def _connection_feature_states(conns: list[CatalogConnectionEntry]) -> list[dict[str, object]]:
    labels: list[str] = []
    for conn in conns:
        for feature in conn.features:
            if feature not in labels:
                labels.append(feature)
    return [
        {"label": feature, "enabled": any(_feature_available(conn, feature) for conn in conns if feature in conn.features)}
        for feature in labels
    ]


def connection_feature_states(conns: list[CatalogConnectionEntry]) -> list[dict[str, object]]:
    return _connection_feature_states(conns)


def _parse_connection_md(path: Path) -> CatalogConnectionEntry | None:
    fm, body = _frontmatter_and_body(path.read_text(encoding="utf-8", errors="ignore"))
    if not fm:
        return None
    auto = fm.get("auto") or {}
    if auto.get("tipo") != "connection":
        return None
    props = {k: _clean_cell(v) for k, v in _table_values(body).items()}
    info = {k: _clean_cell(v) for k, v in _table_values(body, "Connection").items()}
    database = {k: _clean_cell(v) for k, v in _table_values(body, "Database").items()}
    if database:
        info.update(database)
    feats = {k: _clean_cell(v) for k, v in _table_values(body, "Capacidades").items()}
    if not info and not feats:
        # Listed in conn_space_<SPACE>.json, but no conn_space_<SPACE>_<CONNECTION>
        # detail was generated. Keep the architecture catalog operational: omit it.
        return None
    gateway = info.get("gateway", "")
    gateway_map: dict[str, str] = {}
    if gateway:
        try:
            loaded_gateway = json.loads(gateway)
            if isinstance(loaded_gateway, dict):
                gateway_map = {str(k): _clean_cell(str(v)) for k, v in loaded_gateway.items()}
        except Exception:
            gateway_map = {}
    gateway_norm = _norm(gateway)
    gateway_compact = re.sub(r"\s+", "", gateway.lower())
    cloud_connector = (
        _boolish_text(gateway_map.get("cloud_connector", ""))
        or '"cloud_connector":"true"' in gateway_compact
        or "cloud connector true" in gateway_norm
        or "cloudconnector true" in gateway_norm
        or "cloud_connector true" in gateway_norm
    )
    authentication = {k: _clean_cell(v) for k, v in _table_values(body, "Authentication").items()}
    if not authentication:
        authentication = {k: _clean_cell(v) for k, v in _table_values(body, "Autenticaci").items()}
    data_provisioning = feats.get("dataProvisioningOption") or feats.get("data_provisioning_option") or ""
    dp_connected = feats.get("dpAgentConnected") or feats.get("dp_agent_connected") or ""
    dp_agent = bool(props.get("Agente") or feats.get("dpAgent")) or _boolish_text(dp_connected) or _norm(data_provisioning) == "dpagent"
    features = _connection_feature_labels(feats)
    return CatalogConnectionEntry(
        path=str(path),
        cliente=str(auto.get("cliente") or ""),
        space=str(auto.get("space") or props.get("Espacio") or ""),
        connection=str(auto.get("connection") or props.get("Nombre t?cnico") or path.stem),
        type_id=str(auto.get("type_id") or props.get("Tipo") or ""),
        remote_source=str(auto.get("remote_source") or props.get("Remote source") or ""),
        business_name=props.get("Nombre funcional", ""),
        location=props.get("Ubicaci?n", ""),
        adapter=props.get("Adapter", ""),
        agent=props.get("Agente", ""),
        agent_status=props.get("Estado agente", ""),
        cloud_connector=cloud_connector,
        dp_agent=dp_agent,
        data_provisioning=str(data_provisioning),
        features=features,
        props=props,
        info=info,
        gateway=gateway_map,
        authentication=authentication,
        feature_values=feats,
    )


def load_architecture_connections(root: str | Path) -> list[CatalogConnectionEntry]:
    """Carga conexiones generadas bajo catalog_local/arquitectura/*/conexiones/*.md."""
    base = Path(root) / "arquitectura"
    if not base.exists():
        return []
    entries: list[CatalogConnectionEntry] = []
    for p in sorted(base.glob("*/conexiones/*.md")):
        entry = _parse_connection_md(p)
        if entry:
            entries.append(entry)
    return entries


def connection_types(entries: list[CatalogConnectionEntry]) -> list[dict[str, str]]:
    values = sorted({e.type_id for e in entries if e.type_id}, key=lambda x: connection_type_label(x).lower())
    return [{"value": v, "label": connection_type_label(v)} for v in values]


def connections_for_type(entries: list[CatalogConnectionEntry], type_id: str) -> list[CatalogConnectionEntry]:
    wanted = (type_id or "").strip().lower()
    return sorted(
        [e for e in entries if e.type_id.lower() == wanted],
        key=lambda e: (e.cliente.lower(), e.space.lower(), e.connection.lower()),
    )


def connections_for_client(entries: list[CatalogConnectionEntry], cliente: str) -> list[CatalogConnectionEntry]:
    wanted = (cliente or "").strip().lower()
    return sorted(
        [e for e in entries if e.cliente.lower() == wanted],
        key=lambda e: (connection_type_label(e.type_id).lower(), e.space.lower(), e.connection.lower()),
    )


def connection_client_detail(
    entries: list[CatalogConnectionEntry],
    cliente: str,
    type_id: str,
    include_all: bool = False,
) -> dict | None:
    selected = connections_for_type(entries, type_id)
    selected = [e for e in selected if e.cliente.lower() == (cliente or "").strip().lower()]
    if not selected:
        return None

    all_client = connections_for_client(entries, cliente)
    shown = all_client if include_all else selected
    selected_paths = {e.path for e in selected}
    selected_group = connection_client_groups(selected)[0]

    ordered = sorted(
        shown,
        key=lambda e: (e.path not in selected_paths, connection_type_label(e.type_id).lower(), e.space.lower(), e.connection.lower()),
    )
    return {
        "cliente": selected_group["cliente"],
        "type_id": type_id,
        "type_label": connection_type_label(type_id),
        "include_all": bool(include_all),
        "connections": ordered,
        "selected_connections": selected,
        "selected_paths": selected_paths,
        "all_count": len(all_client),
        "count": len(selected),
        "spaces": sorted({e.space for e in ordered if e.space}),
        "verdict": selected_group["verdict"],
        "verdict_label": selected_group["verdict_label"],
        "connection_label": selected_group.get("connection_label") or selected_group.get("type_label") or connection_type_label(type_id),
        "dp_agent": selected_group["dp_agent"],
        "cloud_connector": selected_group["cloud_connector"],
        "feature_states": selected_group["feature_states"],
        "alerts": selected_group["alerts"],
    }


def _connection_functional_label(conn: CatalogConnectionEntry) -> str:
    return conn.business_name or conn.connection or "(sin conexión)"


def _enabled_feature_count(conns: list[CatalogConnectionEntry]) -> int:
    return sum(1 for item in _connection_feature_states(conns) if item.get("enabled"))


def _primary_connection_scenario(scenarios: dict[str, list[CatalogConnectionEntry]]) -> tuple[str, list[CatalogConnectionEntry]]:
    def sort_key(item: tuple[str, list[CatalogConnectionEntry]]) -> tuple[int, int, int, str]:
        label, conns = item
        spaces = {c.space for c in conns if c.space}
        return (-len(spaces), -_enabled_feature_count(conns), -len(conns), label.lower())

    return sorted(scenarios.items(), key=sort_key)[0]


def _connection_group_verdict(
    primary_conns: list[CatalogConnectionEntry],
    feature_states: list[dict[str, object]],
    alerts: list[dict],
) -> str:
    if alerts or any((not c.dp_agent and not c.cloud_connector) for c in primary_conns):
        return "parcial"
    if feature_states and all(bool(item.get("enabled")) for item in feature_states):
        return "plena"
    return "buena"


def _connection_alert(label: str, conns: list[CatalogConnectionEntry]) -> dict:
    feature_states = _connection_feature_states(conns)
    return {
        "label": label,
        "connections": conns,
        "spaces": sorted({c.space for c in conns if c.space}),
        "count": len(conns),
        "dp_agent": any(c.dp_agent for c in conns),
        "cloud_connector": any(c.cloud_connector for c in conns),
        "feature_states": feature_states,
        "reason": "Conexión no sincronizada contra el escenario principal del cliente para este tipo de origen.",
    }


def connection_client_groups(entries: list[CatalogConnectionEntry]) -> list[dict]:
    """Agrupa conexiones por cliente para el tipo seleccionado.

    El endpoint ya filtra por typeId, por eso el tile debe representar el estado
    del cliente frente a ese origen. Si aparecen nombres funcionales distintos
    entre espacios, elegimos un escenario principal y dejamos los desvíos como
    alertas en lugar de crear tiles competidores.
    """
    by_client: dict[str, list[CatalogConnectionEntry]] = {}
    for e in entries:
        by_client.setdefault(e.cliente or "(sin cliente)", []).append(e)

    out = []
    for cliente, conns in sorted(by_client.items(), key=lambda kv: kv[0].lower()):
        scenarios: dict[str, list[CatalogConnectionEntry]] = {}
        for conn in conns:
            scenarios.setdefault(_connection_functional_label(conn), []).append(conn)

        primary_label, primary_conns = _primary_connection_scenario(scenarios)
        alerts = [
            _connection_alert(label, scenario_conns)
            for label, scenario_conns in sorted(scenarios.items(), key=lambda kv: kv[0].lower())
            if label != primary_label
        ]
        spaces = sorted({c.space for c in primary_conns if c.space})
        business_names = {c.business_name for c in primary_conns if c.business_name}
        connection_label = next(iter(business_names)) if len(business_names) == 1 else ""
        if not connection_label and len(primary_conns) == 1:
            connection_label = primary_label
        feature_states = _connection_feature_states(primary_conns)
        verdict = _connection_group_verdict(primary_conns, feature_states, alerts)
        features = [str(item["label"]) for item in feature_states]
        out.append({
            "cliente": cliente,
            "connections": primary_conns,
            "spaces": spaces,
            "connection_label": connection_label,
            "count": len(primary_conns),
            "total_count": len(conns),
            "dp_agent": any(c.dp_agent for c in primary_conns),
            "cloud_connector": any(c.cloud_connector for c in primary_conns),
            "features": features,
            "feature_states": feature_states,
            "alerts": alerts,
            "alert_count": len(alerts),
            "verdict": verdict,
            "verdict_label": verdict,
        })
    return out




def _looks_like_security_view(value: str) -> bool:
    raw = _clean_cell(value)
    if not raw:
        return False
    norm = _norm(raw)
    if norm in {
        "business name", "technical name", "criterio", "contacto", "owner", "estructura",
        "principal element", "identificador", "source entity", "entidad de permisos",
        "regla de acceso", "criterios", "campos gobernados", "email", "user id",
    }:
        return False
    upper = raw.upper()
    return bool(re.match(r"^(?:[0-9][A-Z0-9_]*|[A-Z]+[0-9]*[A-Z0-9_]*_[A-Z0-9_]+)$", upper))


def _clean_security_lineage_views(raw_views: list, body: str = "") -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in raw_views or []:
        if not isinstance(row, dict):
            continue
        space = _clean_cell(str(row.get("space") or row.get("SPACE_ID") or ""))
        view = _clean_cell(str(row.get("view") or row.get("VIEW_NAME") or ""))
        if not _looks_like_security_view(view):
            continue
        key = (space.upper(), view.upper())
        if key in seen:
            continue
        seen.add(key)
        rows.append({"space": space, "view": view})
    if rows:
        return rows
    return _security_lineage_from_body(body)


def _security_lineage_from_body(body: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    in_lineage = False
    table_started = False
    for line in (body or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_lineage = "linaje" in _norm(stripped)
            table_started = False
            continue
        if not in_lineage:
            continue
        if not stripped.startswith("|"):
            if table_started:
                break
            continue
        table_started = True
        if "---" in stripped:
            continue
        cells = [_clean_cell(c.strip()) for c in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        left, right = cells[0], cells[1]
        if _norm(left) in {"espacio", "space"} or _norm(right) in {"vista consumidora", "view", "view name"}:
            continue
        if left and _looks_like_security_view(right):
            rows.append({"space": left, "view": right})
    return rows

def _parse_security_md(path: Path) -> CatalogSecurityEntry | None:
    fm, body = _frontmatter_and_body(path.read_text(encoding="utf-8", errors="ignore"))
    if not fm:
        return None
    auto = fm.get("auto") or {}
    if auto.get("tipo") != "security":
        return None
    manual = fm.get("manual") or {}
    veredicto = str(manual.get("veredicto") or auto.get("veredicto") or "parcial").strip().lower()
    apto = str(manual.get("apto") or auto.get("apto") or "Inspiraci?n conceptual con reservas")
    spaces = auto.get("spaces") or []
    if not isinstance(spaces, list):
        spaces = [str(spaces)]
    governed_fields = auto.get("governed_fields") or []
    if not isinstance(governed_fields, list):
        governed_fields = [str(governed_fields)]
    lineage_views = auto.get("lineage_views") or []
    if not isinstance(lineage_views, list):
        lineage_views = []
    lineage_views = _clean_security_lineage_views(lineage_views, body)
    entry = CatalogSecurityEntry(
        path=str(path),
        cliente=str(auto.get("cliente") or ""),
        criterio=str(auto.get("criterio") or ""),
        dac=str(auto.get("dac") or ""),
        family=str(auto.get("family") or "data"),
        source_entity=str(auto.get("source_entity") or ""),
        principal_element=str(auto.get("principal_element") or ""),
        veredicto=veredicto,
        apto=apto,
        summary=str(manual.get("summary") or auto.get("summary") or ""),
        spaces=[str(s) for s in spaces if str(s or "").strip()],
        governed_fields=[str(f) for f in governed_fields if str(f or "").strip()],
        lineage_views=[
            {"space": str((r or {}).get("space") or ""), "view": str((r or {}).get("view") or "")}
            for r in lineage_views
            if isinstance(r, dict) and (str(r.get("space") or "").strip() or str(r.get("view") or "").strip())
        ],
        consumers=int(auto.get("consumers") or 0),
        role_count=int(auto.get("role_count") or 0),
        global_role_count=int(auto.get("global_role_count") or 0),
        scoped_role_count=int(auto.get("scoped_role_count") or 0),
        roles=[
            {
                "id": str((r or {}).get("id") or ""),
                "category": str((r or {}).get("category") or "scope"),
                "scopes": [str(s) for s in ((r or {}).get("scopes") or []) if str(s).strip()],
                "layer": str((r or {}).get("layer") or ""),
                "anchor": str((r or {}).get("anchor") or ""),
            }
            for r in (auto.get("roles") or []) if isinstance(r, dict) and (r or {}).get("id")
        ],
        permission_count=int(auto.get("permission_count") or 0),
        source_file=str(auto.get("source_file") or ""),
        body=body,
    )
    entry._search = " ".join([
        entry.cliente, entry.criterio, entry.dac, entry.family, entry.source_file, entry.source_entity,
        entry.principal_element, " ".join(entry.governed_fields),
        " ".join(v.get("view", "") for v in entry.lineage_views),
        " ".join(v.get("space", "") for v in entry.lineage_views),
        entry.summary, entry.apto, body,
    ])
    return entry


def load_security_catalog(root: str | Path) -> list[CatalogSecurityEntry]:
    """Carga criterios DAC generados bajo catalog_local/seguridad/*/*.md."""
    base = Path(root) / "seguridad"
    if not base.exists():
        return []
    entries: list[CatalogSecurityEntry] = []
    for p in sorted(base.glob("*/*.md")):
        entry = _parse_security_md(p)
        if entry:
            entries.append(entry)
    return sorted(entries, key=lambda e: (_VERDICT_RANK.get(e.veredicto, 9), e.cliente.lower(), e.criterio.lower()))


SECURITY_FAMILY_LABELS = {
    "roles": "Seguridad de Roles",
    "data": "Seguridad de Datos",
}
SECURITY_FAMILY_ORDER = {"roles": 0, "data": 1}


def security_families(entries: list[CatalogSecurityEntry]) -> list[dict[str, str]]:
    """Opciones funcionales del selector de Seguridad.

    El dropdown no representa criterios t?cnicos generados sino familias de
    catálogo. Se mantiene fijo para que la UX sea estable: Roles primero, Datos
    despu?s. Cada familia mostrar? las tiles disponibles, o vac?o si todavía no
    fue regenerada.
    """
    return [
        {"value": family, "label": SECURITY_FAMILY_LABELS[family]}
        for family in sorted(SECURITY_FAMILY_LABELS, key=lambda f: (SECURITY_FAMILY_ORDER.get(f, 99), f))
    ]


def security_for_family(entries: list[CatalogSecurityEntry], family: str) -> list[CatalogSecurityEntry]:
    wanted = (family or "").strip().lower()
    return sorted(
        [e for e in entries if (e.family or "data").strip().lower() == wanted],
        key=lambda e: (_VERDICT_RANK.get(e.veredicto, 9), e.cliente.lower(), e.criterio.lower(), e.dac.lower()),
    )


def security_criteria(entries: list[CatalogSecurityEntry]) -> list[str]:
    return sorted({e.criterio for e in entries if e.criterio}, key=lambda x: x.lower())


def security_for_criterion(entries: list[CatalogSecurityEntry], criterio: str) -> list[CatalogSecurityEntry]:
    wanted = (criterio or "").strip().lower()
    return sorted(
        [e for e in entries if e.criterio.strip().lower() == wanted],
        key=lambda e: (_VERDICT_RANK.get(e.veredicto, 9), e.cliente.lower(), e.dac.lower()),
    )


def find_security_entry(entries: list[CatalogSecurityEntry], cliente: str, criterio: str, dac: str = "") -> CatalogSecurityEntry | None:
    c = (cliente or "").strip().lower()
    cr = (criterio or "").strip().lower()
    d = (dac or "").strip().lower()
    for entry in entries:
        if entry.cliente.strip().lower() == c and entry.criterio.strip().lower() == cr:
            if not d or entry.dac.strip().lower() == d:
                return entry
    return None


def _parse_data_product_md(path: Path) -> CatalogDataProductEntry | None:
    fm, body = _frontmatter_and_body(path.read_text(encoding="utf-8", errors="ignore"))
    if not fm:
        return None
    auto = fm.get("auto") or {}
    manual = fm.get("manual") or {}
    if auto.get("tipo") != "data_product":
        return None
    veredicto = str(manual.get("veredicto") or auto.get("veredicto") or "plena").strip().lower()
    apto = str(manual.get("apto") or auto.get("apto") or "Referencia Danwor — reutilizable")
    title = str(manual.get("titulo") or auto.get("titulo") or auto.get("product_id") or path.stem)
    business_name = str(manual.get("business_name") or auto.get("business_name") or title)
    entry = CatalogDataProductEntry(
        path=str(path),
        product_id=str(auto.get("product_id") or path.stem),
        title=title,
        business_name=business_name,
        data_categories=list(manual.get("data_categories") or auto.get("data_categories") or []),
        industries=list(manual.get("industries") or auto.get("industries") or []),
        delivery_mode=str(manual.get("delivery_mode") or auto.get("delivery_mode") or ""),
        veredicto=veredicto,
        apto=apto,
        entities=list(auto.get("entidades") or []),
        fields=list(auto.get("campos") or []),
        lineage_nodes=list(auto.get("lineage_nodes") or []),
        files=list(auto.get("archivos") or []),
        portabilidad_bdc=dict(auto.get("portabilidad_bdc") or {}),
        portabilidad_ack=list(manual.get("portabilidad_ack") or []),
        body=body,
    )
    entry._search = " ".join([
        entry.product_id,
        entry.title,
        entry.business_name,
        " ".join(entry.data_categories),
        " ".join(entry.industries),
        entry.delivery_mode,
        " ".join(str(e.get("name", "")) + " " + str(e.get("label", "")) for e in entry.entities if isinstance(e, dict)),
        " ".join(str(n.get("name", "")) + " " + str(n.get("display", "")) + " " + str(n.get("label", "")) for n in entry.lineage_nodes if isinstance(n, dict)),
        " ".join(str(f.get("name", "")) + " " + str(f.get("label", "")) for f in entry.fields if isinstance(f, dict)),
    ])
    return entry


def load_data_products(root: str | Path) -> list[CatalogDataProductEntry]:
    base = Path(root) / "data_products"
    if not base.is_dir():
        return []
    entries: list[CatalogDataProductEntry] = []
    for path in sorted(base.glob("*.md")):
        entry = _parse_data_product_md(path)
        if entry:
            entries.append(entry)
    return sorted(entries, key=lambda e: e.title.lower())


def find_data_product(entries: list[CatalogDataProductEntry], product_id: str) -> CatalogDataProductEntry | None:
    wanted = (product_id or "").strip().lower()
    return next((e for e in entries if e.product_id.lower() == wanted), None)


def load_catalog(root: str | Path) -> list[CatalogEntry]:
    """Carga todos los flujo_*.md bajo `root` (recursivo)."""
    entries: list[CatalogEntry] = []
    for p in sorted(Path(root).rglob("flujo_*.md")):
        entry = _parse_md(p.read_text(encoding="utf-8"), str(p))
        if entry:
            entries.append(entry)
    return entries


# ── Fuente SharePoint (Graph, token delegado) — espejo de sharepoint_cases ─
# No persiste el token, cache TTL por fingerprint, degradación a [] ante fallo.
import hashlib
import json as _json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

_SP_ENABLED = os.getenv("SHAREPOINT_CATALOG_ENABLED", "OFF").upper() in {"ON", "TRUE", "1", "YES"}
_SP_SITE_ID = os.getenv("SHAREPOINT_CATALOG_SITE_ID", "").strip()
_SP_FOLDER = os.getenv("SHAREPOINT_CATALOG_FOLDER", "").strip()   # p. ej. "Catalogo CDS"
_SP_TTL = int(os.getenv("SHAREPOINT_CATALOG_CACHE_TTL", "300"))
_sp_cache: dict = {}
_sp_lock = threading.Lock()


def _graph_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _graph_get_json(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers=_graph_headers(token))
    with urllib.request.urlopen(req, timeout=12) as r:
        return _json.loads(r.read().decode("utf-8"))


def _download_text(url: str) -> str:
    """downloadUrl de Graph es pre-autenticada (GET sin header)."""
    with urllib.request.urlopen(urllib.request.Request(url), timeout=12) as r:
        return r.read().decode("utf-8", errors="ignore")


def _walk_md(site_id: str, folder: str, token: str) -> list:
    """Lista recursiva de flujo_*.md (nombre, downloadUrl) bajo `folder` en el drive del sitio."""
    site = urllib.parse.quote(site_id, safe=",")
    base = f"https://graph.microsoft.com/v1.0/sites/{site}/drive/root"
    start = f"{base}:/{urllib.parse.quote(folder)}:/children" if folder else f"{base}/children"
    out, stack = [], [start]
    while stack:
        url = stack.pop()
        while url:
            payload = _graph_get_json(url, token)
            for item in payload.get("value", []):
                name = item.get("name", "")
                if item.get("folder"):
                    drive = item.get("parentReference", {}).get("driveId", "")
                    stack.append(f"https://graph.microsoft.com/v1.0/drives/{drive}/items/{item.get('id')}/children")
                elif item.get("file") and name.startswith("flujo_") and name.lower().endswith(".md"):
                    out.append((name, item.get("@microsoft.graph.downloadUrl", "")))
            url = payload.get("@odata.nextLink")
    return out


def fetch_catalog_from_sharepoint(token: str, *, force_refresh: bool = False) -> list[CatalogEntry]:
    """Lee los flujo_*.md desde la biblioteca de SharePoint. [] si está apagado,
    sin token, o si Graph falla (nunca rompe la conversación). El cliente/dominio
    salen del frontmatter del .md (no de la ruta), así que basta el contenido."""
    if not (_SP_ENABLED and token and _SP_SITE_ID):
        return []
    fp = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    now = time.monotonic()
    with _sp_lock:
        hit = _sp_cache.get(fp)
        if hit and not force_refresh and (now - hit[0]) < _SP_TTL:
            return hit[1]
    try:
        entries = []
        for name, durl in _walk_md(_SP_SITE_ID, _SP_FOLDER, token):
            if not durl:
                continue
            entry = _parse_md(_download_text(durl), name)
            if entry:
                entries.append(entry)
    except (urllib.error.URLError, Exception):
        return []
    with _sp_lock:
        _sp_cache[fp] = (now, entries)
    return entries


# ── Retrieval ─────────────────────────────────────────────────────────────
def search_catalog(query: str, entries: list[CatalogEntry], top_k: int = 8) -> list[CatalogEntry]:
    q = _tokens(query)
    if not q:
        return []
    scored = []
    for e in entries:
        dom = _tokens(e.dominio)
        cli = _tokens(e.cliente)
        txt = _tokens(e._search)
        score = 6.0 * len(q & dom) + 3.0 * len(q & cli) + 1.0 * len(q & txt)
        if score > 0:
            scored.append((score, e))
    # orden: relevancia primero, y a igualdad, mejor mirada Danwor arriba
    scored.sort(key=lambda se: (-se[0], _VERDICT_RANK.get(se[1].veredicto, 9)))
    return [e for _s, e in scored[:top_k]]


def best_domain_match(query: str, entries: list[CatalogEntry]) -> CatalogEntry | None:
    """Mejor flujo del catálogo para `query`, SOLO si matchea a nivel DOMINIO
    (token de dominio en común, con sinónimos stock↔inventario). Es el umbral
    "fuerte" que usa el asistente para responder con evidencia real (un flujo
    publicado) antes que ofrecer un template. None si ningún dominio matchea."""
    q = _tokens(query)
    if not q:
        return None
    cands = [e for e in entries if q & _tokens(e.dominio)]
    if not cands:
        return None
    ranked = search_catalog(query, cands, top_k=1)   # reusa scoring + orden por veredicto
    return ranked[0] if ranked else None


def domains(entries: list[CatalogEntry]) -> list[str]:
    """Dominios disponibles en el catálogo (para el dropdown determinista).

    Directorios técnicos como Seguridad, Conexiones o Espacios tienen generadores
    dedicados y nunca deben competir como dominios de negocio aunque exista un
    markdown histórico en catalog_local.
    """
    return sorted({e.dominio for e in entries if e.dominio and not _is_reserved_domain(e.dominio)})


def flows_for_domain(entries: list[CatalogEntry], dominio: str) -> list[CatalogEntry]:
    """Flujos de un dominio, ordenados por mirada Danwor (🟢 → 🟡 → 🟠)."""
    if _is_reserved_domain(dominio):
        return []
    d = (dominio or "").strip().lower()
    hits = [e for e in entries if e.dominio.lower() == d and not _is_reserved_domain(e.dominio)]
    return sorted(hits, key=lambda e: _VERDICT_RANK.get(e.veredicto, 9))


def find_flow(entries: list[CatalogEntry], cliente: str, dominio: str) -> CatalogEntry | None:
    """Un flujo puntual por cliente+dominio (para el detalle en el panel central)."""
    c, d = (cliente or "").strip().lower(), (dominio or "").strip().lower()
    return next((e for e in entries if e.cliente.lower() == c and e.dominio.lower() == d), None)


def group_by_client(matches: list[CatalogEntry]) -> dict:
    out: dict[str, list] = {}
    for e in matches:
        out.setdefault(e.cliente or "(sin cliente)", []).append(e)
    return out


# ── Grounding para el asistente ────────────────────────────────────────────
def format_catalog_for_prompt(matches: list[CatalogEntry]) -> str:
    if not matches:
        return ""
    ordered = sorted(matches, key=lambda e: _VERDICT_RANK.get(e.veredicto, 9))
    lines = [
        "CATÁLOGO INTERNO DE FLUJOS CDS IMPLEMENTADOS (evidencia Danwor)",
        "Ofrecé primero los de mayor 'mirada Danwor' (🟢 plena) como referencia; "
        "presentá los 🟠 parcial SOLO como inspiración conceptual, advirtiendo sus reservas. "
        "No inventes flujos: usá solo los de esta lista. Ofrecé verlos por cliente si hay varios.",
        "",
    ]
    for e in ordered:
        reservas = (f" — reservas: {'; '.join(h['titulo'] for h in e.hallazgos[:2])}"
                    if e.veredicto == "parcial" else "")
        lines.append(
            f"- {e.icon} [{e.veredicto}] {e.dominio} · cliente {e.cliente} — {e.apto}{reservas} "
            f"({len(e.artefactos)} artefactos)")
    return "\n".join(lines)


def answer_preview(query: str, matches: list[CatalogEntry]) -> str:
    """Respuesta determinística (sin LLM) — demo del comportamiento esperado."""
    if not matches:
        return "No encontré flujos internos implementados para esa consulta."
    ordered = sorted(matches, key=lambda e: _VERDICT_RANK.get(e.veredicto, 9))
    dom = ordered[0].dominio.lower()
    head = (f"Sí — tenemos {len(ordered)} flujo(s) de **{dom}** implementados:"
            if len(ordered) > 1 else
            f"Sí — tenemos un flujo de **{dom}** implementado:")
    body = [f"  {e.icon} **{e.cliente}** — {e.apto}"
            + (f"  ⚠ {e.hallazgos[0]['titulo']}" if e.veredicto == "parcial" and e.hallazgos else "")
            for e in ordered]
    tail = "¿Querés que profundice en alguno, o verlos por cliente?"
    return "\n".join([head, *body, "", tail])


# ── Demo CLI ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Demo de consumo del catálogo CDS")
    ap.add_argument("--catalog", required=True, help="Carpeta con los flujo_*.md generados")
    ap.add_argument("--q", default="necesito un modelo de stock", help="Consulta")
    args = ap.parse_args()

    entries = load_catalog(args.catalog)
    print(f"[catálogo] {len(entries)} flujo(s) cargado(s): "
          f"{', '.join(f'{e.cliente}/{e.dominio} {e.icon}' for e in entries)}\n")
    matches = search_catalog(args.q, entries)
    print(f"[consulta] \"{args.q}\" → {len(matches)} match(es)\n")
    print("── Grounding que recibe el LLM ──")
    print(format_catalog_for_prompt(matches))
    print("\n── Respuesta preview (determinística) ──")
    print(answer_preview(args.q, matches))
