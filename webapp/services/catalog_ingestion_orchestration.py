"""
catalog_ingestion_orchestration.py — «Orquestación de ingesta» de una tabla local de capa Ingesta (Catálogo → detalle de flujo).

Responde, con evidencia comprobada y sin inventar relaciones: ¿qué objeto técnico origina la tabla, qué Replication Flow la carga (y en qué destino físico:
la tabla base o su `_Delta`), y qué Task Chains superiores lo ejecutan?

Fuentes (todas de SOLO LECTURA):
  · RELACIONES = manifiestos del CLI (`<catálogo>/.lineage_runs/<cliente>/**/lineage-manifest.json`): `PRODUCES` (RF → tabla), `REPLICATED_BY` (objeto de origen → RF),
    `USED_BY` (conexión → RF), `ORCHESTRATES` (Task Chain → RF o Task Chain → Task Chain). Ninguna relación se deduce por nombres.
  · DETALLE y ORÁCULO = los JSON técnicos del pool (`replication-flows/<RF>.json`, `task-chains/<TC>.json`): destino físico declarado (`targetObject.name`), tipo de carga,
    sistema/contenedor de origen y etiqueta de la cadena. El detalle nunca agrega ni quita una relación del manifiesto.
  · La captura delta (`X` con su compañera `X_Delta`) la aporta quien llama, desde la evidencia canónica CSN (`catalog_delta_evidence`); acá solo se la consume.

Estados (la ausencia de evidencia NUNCA se presenta como un hecho sobre Datasphere):
  ESTABLISHED       al menos un Replication Flow produce la tabla según el manifiesto
  NO_PRODUCER_FOUND hay manifiesto que cubre la tabla y ningún RF la produce
  NOT_EVALUATED     no hay manifiesto del CLI que cubra la tabla
Task Chains por productor: CONFIRMED (aristas `ORCHESTRATES`) · NOT_FOUND_IN_EXPORT (el manifiesto escaneó las cadenas y ninguna ejecuta el RF) · NOT_EVALUATED (sin
señal de que se hayan escaneado: nunca se dice «sin cadena»).
"""
from __future__ import annotations

import html
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from webapp.services import assessment_service as asvc

log = logging.getLogger("danwor.catalog_ingestion_orchestration")

ESTABLISHED, NO_PRODUCER_FOUND, NOT_EVALUATED = "ESTABLISHED", "NO_PRODUCER_FOUND", "NOT_EVALUATED"
CONFIRMED, NOT_FOUND_IN_EXPORT = "CONFIRMED", "NOT_FOUND_IN_EXPORT"
BASE, DELTA_SHADOW, OTHER, UNKNOWN = "BASE", "DELTA_SHADOW", "OTHER", "UNKNOWN"
MAX_CHAIN_DEPTH = 8

_PRODUCES, _REPLICATED_BY, _USED_BY, _ORCHESTRATES = "PRODUCES", "REPLICATED_BY", "USED_BY", "ORCHESTRATES"


@dataclass(frozen=True)
class Chain:
    name: str                       # Task Chain DIRECTA del RF
    label: str | None               # @EndUserText.label del JSON técnico (si está en el pool)
    path: tuple[str, ...]           # [cadena superior, …, cadena directa]; len > 1 solo con TC→TC comprobada


@dataclass(frozen=True)
class Producer:
    flow: str
    load_type: str | None = None
    delta_trigger: str | None = None
    physical_target: str | None = None          # tal cual lo declara el RF (tabla base o `X_Delta`); None si el JSON del RF no está en el pool
    target_kind: str = UNKNOWN                  # BASE | DELTA_SHADOW | OTHER | UNKNOWN
    source_objects: tuple[str, ...] = ()        # objeto(s) del sistema de ORIGEN que el manifiesto declara para este RF
    source_task_certain: bool = True            # False: varias fuentes declaradas y no se pudo fijar cuál alimenta ESTA tabla
    connection: str | None = None
    connection_type: str | None = None
    container: str | None = None
    chains: tuple[Chain, ...] = ()
    chains_state: str = UNKNOWN


@dataclass(frozen=True)
class IngestionOrchestration:
    entity: str
    state: str
    producers: tuple[Producer, ...] = ()
    note: str | None = None


@dataclass
class _Manifest:
    objects: set = field(default_factory=set)
    edges: set = field(default_factory=set)         # (relation, source_type, source_name, target_type, target_name)
    scanned_chains: bool = False


_lock = threading.Lock()
_memo: dict[tuple, tuple[tuple, list]] = {}


def _load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _parse_manifest(data: dict) -> _Manifest:
    m = _Manifest()
    for o in data.get("objects") or []:
        if isinstance(o, dict) and o.get("technical_name"):
            m.objects.add((str(o.get("object_type") or ""), str(o["technical_name"])))
    for e in data.get("edges") or []:
        if isinstance(e, dict) and e.get("relation") and e.get("source_name") and e.get("target_name"):
            m.edges.add((str(e["relation"]), str(e.get("source_type") or ""), str(e["source_name"]), str(e.get("target_type") or ""), str(e["target_name"])))
    # ¿se escanearon las Task Chains? `effectiveFeatures` lo prueba (manifiestos recientes); en los anteriores, solo una arista `ORCHESTRATES` en ese manifiesto.
    feats = data.get("effectiveFeatures")
    m.scanned_chains = ("task-chains" in feats) if isinstance(feats, list) else any(e[0] == _ORCHESTRATES for e in m.edges)
    return m


def _manifests(client: str) -> list[_Manifest]:
    """Manifiestos del cliente, memoizados por huella de los archivos. `.lineage_runs` puede traer copias duplicadas del mismo modelo (p. ej. `Pampa/` y `Pampa/DSDEV/`): se leen todas."""
    base = asvc.catalog_dir() / ".lineage_runs"
    wanted = (client or "").strip().lower()
    files: list[Path] = []
    try:
        if base.is_dir():
            for sub in sorted(p for p in base.iterdir() if p.is_dir() and p.name.lower() == wanted):
                files.extend(sorted(sub.rglob("lineage-manifest.json")))
    except OSError:
        return []
    fp = tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files)
    key = (str(base), wanted)
    with _lock:
        cached = _memo.get(key)
        if cached is not None and cached[0] == fp:
            return cached[1]
    out = []
    for p in files:
        data = _load_json(p)
        if data is not None and str(data.get("client") or client).strip().lower() == wanted:
            out.append(_parse_manifest(data))
    with _lock:
        _memo[key] = (fp, out)
    return out


def _flow_json(client: str) -> tuple[dict[str, dict], dict[str, dict]]:
    """(RF por nombre, Task Chain por nombre) leídos de los JSON técnicos del pool del cliente. Sin pool → vacíos (el detalle simplemente no se muestra)."""
    found = asvc.pool_client_files(client)
    if found is None:
        return {}, {}
    _root, files = found
    fp = tuple(sorted((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files if p.parent.name in ("replication-flows", "task-chains")))
    key = ("flows", str(_root), (client or "").lower())
    with _lock:
        cached = _memo.get(key)
        if cached is not None and cached[0] == fp:
            return cached[1]
    rfs: dict[str, dict] = {}
    tcs: dict[str, dict] = {}
    for p in files:
        if p.parent.name not in ("replication-flows", "task-chains"):
            continue
        data = _load_json(p)
        for name, body in ((data or {}).get("replicationflows") or {}).items():
            if isinstance(body, dict):
                rfs.setdefault(name, body)
        for name, body in ((data or {}).get("taskchains") or {}).items():
            if isinstance(body, dict):
                tcs.setdefault(name, body)
    with _lock:
        _memo[key] = (fp, (rfs, tcs))
    return rfs, tcs


def _chain_paths(direct: str, parents: dict[str, set]) -> list[tuple[str, ...]]:
    """Caminos [raíz, …, directa] siguiendo SOLO aristas TC→TC comprobadas; con guarda de ciclos y de profundidad."""
    out: list[tuple[str, ...]] = []

    def up(path: tuple[str, ...]) -> None:
        ups = [p for p in sorted(parents.get(path[0], ())) if p not in path]
        if not ups or len(path) >= MAX_CHAIN_DEPTH:
            out.append(path)
            return
        for p in ups:
            up((p, *path))

    up((direct,))
    return sorted(set(out))


def orchestration_for(client: str, entity: str, delta: dict | None = None) -> IngestionOrchestration:
    """Orquestación de la tabla local ``entity``. ``delta`` = `catalog_delta_evidence.delta_capture(...)` ({state, companions}). Jamás lanza: ante una falla, NOT_EVALUATED."""
    try:
        return _orchestration(client, entity, delta or {})
    except Exception:                                         # noqa: BLE001 — presentación: nunca rompe el detalle del flujo
        log.exception("catalog_ingestion_orchestration: falló %s/%s", client, entity)
        return IngestionOrchestration(entity, NOT_EVALUATED, note="No se pudo evaluar la orquestación de esta tabla.")


def _orchestration(client: str, entity: str, delta: dict) -> IngestionOrchestration:
    mans = _manifests(client)
    if not mans:
        return IngestionOrchestration(entity, NOT_EVALUATED, note="No hay manifiesto de linaje del CLI para este cliente: la orquestación no se evaluó.")
    covering = [m for m in mans if ("local-tables", entity) in m.objects or any(e[4] == entity for e in m.edges)]
    if not covering:
        return IngestionOrchestration(entity, NOT_EVALUATED, note="Ningún manifiesto de linaje del CLI cubre esta tabla: la orquestación no se evaluó.")
    flows = sorted({e[2] for m in covering for e in m.edges if e[0] == _PRODUCES and e[1] == "replication-flows" and e[4] == entity})
    if not flows:
        return IngestionOrchestration(entity, NO_PRODUCER_FOUND, note="Evidencia insuficiente: ningún Replication Flow del export produce esta tabla (no se evaluaron Data Flows ni Transformation Flows).")
    rf_json, tc_json = _flow_json(client)
    all_edges = {e for m in mans for e in m.edges}
    parents: dict[str, set] = {}
    for rel, st, sn, tt, tn in all_edges:
        if rel == _ORCHESTRATES and st == "task-chains" and tt == "task-chains":
            parents.setdefault(tn, set()).add(sn)
    companions = [str(c) for c in (delta.get("companions") or [])] if delta.get("state") == "ON" else []
    producers = []
    for rf in flows:
        sources = tuple(sorted({e[2] for e in all_edges if e[0] == _REPLICATED_BY and e[4] == rf}))
        conns = sorted({e[2] for e in all_edges if e[0] == _USED_BY and e[4] == rf})
        chains_direct = sorted({e[2] for e in all_edges if e[0] == _ORCHESTRATES and e[1] == "task-chains" and e[3] == "replication-flows" and e[4] == rf})
        scanned = any(m.scanned_chains for m in mans if any(e[4] == rf or e[2] == rf for e in m.edges))
        chains = []
        for tc in chains_direct:
            label = ((tc_json.get(tc) or {}).get("@EndUserText.label")) or None
            for path in _chain_paths(tc, parents):
                chains.append(Chain(tc, str(label) if label else None, path))
        body = (rf_json.get(rf) or {}).get("contents") or {}
        tasks = [t for t in (body.get("replicationTasks") or []) if isinstance(t, dict)]
        wanted = {entity, *companions}
        mine = [t for t in tasks if str(((t.get("targetObject") or {}).get("name")) or "") in wanted]
        target = load = None
        kind, certain = UNKNOWN, True
        src_objects = sources
        if mine:
            t = mine[0]
            target = str((t.get("targetObject") or {}).get("name"))
            load = t.get("loadType")
            kind = BASE if target == entity else DELTA_SHADOW
            obj = (t.get("sourceObject") or {}).get("name")
            if obj:
                src_objects = (str(obj),)                    # la tarea que carga ESTA tabla fija su objeto de origen (no se asocian las demás fuentes del flujo)
        elif tasks:
            kind, certain = OTHER, False
            src_objects = sources
        else:
            certain = len(sources) <= 1
        sysd = (body.get("sourceSystem") or [{}])[0] if isinstance(body.get("sourceSystem"), list) and body.get("sourceSystem") else {}
        producers.append(Producer(
            flow=rf, load_type=str(load) if load else None, delta_trigger=(body.get("deltaLoadTrigger") or None), physical_target=target, target_kind=kind,
            source_objects=src_objects, source_task_certain=certain,
            connection=(sysd.get("connectionId") or (conns[0] if len(conns) == 1 else None)), connection_type=sysd.get("connectionType"), container=sysd.get("container"),
            chains=tuple(chains), chains_state=CONFIRMED if chains else (NOT_FOUND_IN_EXPORT if scanned else NOT_EVALUATED)))
    return IngestionOrchestration(entity, ESTABLISHED, tuple(producers))


# ───────────────────────────── presentación (HTML del colapsable) ─────────────────────────────
_E = lambda s: html.escape(str(s), quote=True)        # noqa: E731
_C = lambda s: f"<code>{_E(s)}</code>"                # noqa: E731


def _row(label: str, cell: str) -> str:
    return f"<tr><td>{_E(label)}</td><td>{cell}</td></tr>"


def _producer_rows(p: Producer, entity: str, cds_link: Callable[[str], str]) -> list[str]:
    rows = []
    objs = []
    for o in p.source_objects:
        link = cds_link(o)
        objs.append(_C(o) + (f" {link}" if link else ""))
    bits = []
    if p.connection:
        bits.append(_E(p.connection) + (f" · {_E(p.connection_type)}" if p.connection_type else ""))
    if p.container:
        bits.append(_E(p.container))
    origin = ", ".join(objs) if objs else "No declarado en el manifiesto"
    if not p.source_task_certain:
        origin += " <em>(el flujo declara varias fuentes; no se fijó cuál alimenta esta tabla)</em>"
    rows.append(_row("Origen técnico", origin + (f" · {' · '.join(bits)}" if bits else "")))
    flow = _C(p.flow) + (f" · carga {_E(p.load_type)}" if p.load_type else "") + (f" · delta {_E(p.delta_trigger)}" if p.delta_trigger else "")
    rows.append(_row("Replication Flow", flow))
    if p.target_kind == BASE:
        dest = f"{_C(p.physical_target)} · tabla de esta ingesta"
    elif p.target_kind == DELTA_SHADOW:
        dest = f"{_C(p.physical_target)} · tabla de captura de cambios; {_C(entity)} es su proyección activa (no es la misma tabla)"
    elif p.target_kind == OTHER:
        dest = "El JSON del flujo no declara un destino que coincida con esta tabla ni con su compañera delta conocida"
    else:
        dest = "No disponible: el JSON del Replication Flow no está en el pool"
    rows.append(_row("Destino físico", dest))
    if p.chains_state == CONFIRMED:
        parts = []
        for c in p.chains:
            txt = " → ".join(_C(n) for n in c.path)
            parts.append(txt + (f" · {_E(c.label)}" if c.label else ""))
        rows.append(_row("Task Chain", "<br>".join(parts)))
    elif p.chains_state == NOT_FOUND_IN_EXPORT:
        rows.append(_row("Task Chain", f"Sin Task Chain comprobada: ninguna cadena del export ejecuta {_C(p.flow)}. El Replication Flow es el orquestador conocido."))
    else:
        rows.append(_row("Task Chain", "Orquestación no evaluada: el export de este modelo no incluyó Task Chains."))
    return rows


# ───────────────────────────── pipeline visual ─────────────────────────────
def pipeline_model(orch: IngestionOrchestration) -> dict | None:
    """Modelo del diagrama (nodos + aristas REALES) derivado SOLO del contrato ya comprobado; None si no hay productor comprobado (no se dibuja nada inventado).

    Tipos de nodo por metadata del manifest (Task Chain, Replication Flow, tabla local), nunca por prefijo. Aristas: `data` (RF → tabla; flujo de datos), `orch` (Task Chain → RF y TC → TC;
    ORQUESTACIÓN, no flujo de datos), `proj` (tabla técnica `X_Delta` → `X`: proyección activa, solo con el hecho CSN que la prueba) y `unverified` (el manifest dice RF → tabla pero el JSON
    del RF no confirma el destino físico: se dibuja punteada, nunca como un destino físico comprobado)."""
    if orch.state != ESTABLISHED or not orch.producers:
        return None
    nodes: dict[str, dict] = {}
    kinds: dict[tuple[str, str], str] = {}

    def node(key: str, label: str, icon: str, role: str, title: str, variant: str | None = None) -> dict:
        return nodes.setdefault(key, {"name": key, "label": label, "title": title, "layer": "0", "upstreams": [], "assoc_upstreams": [], "slug": None, "num": 0,
                                      "icon": icon, "icon_variant": variant, "role": role})

    def link(up: str, tgt: str, kind: str) -> None:
        if up not in nodes[tgt]["upstreams"]:
            nodes[tgt]["upstreams"].append(up)
        if kind != "data":
            kinds[(up, tgt)] = kind

    tkey = f"tbl:{orch.entity}"
    node(tkey, orch.entity, "table", "Tabla local", f"Tabla local de ingesta {orch.entity}")
    parts = []
    for p in orch.producers:
        origin = ", ".join(p.source_objects) or "origen no declarado"
        rf_title = (f"Replication Flow {p.flow} · origen técnico {origin}" + (f" · conexión {p.connection}" if p.connection else "")
                    + (f" · delta {p.delta_trigger}" if p.delta_trigger else ""))
        rfk = f"rf:{p.flow}"
        node(rfk, p.flow, "replication_flow", "Replication Flow" + (f" · {p.load_type}" if p.load_type else ""), rf_title)
        if p.target_kind == DELTA_SHADOW and p.physical_target:
            phys = f"tbl:{p.physical_target}"
            node(phys, p.physical_target, "table", "Tabla técnica de cambios", f"Tabla física de captura de cambios {p.physical_target}: destino del Replication Flow", variant="delta")
            link(rfk, phys, "data")
            link(phys, tkey, "proj")
            nodes[tkey]["role"] = "Tabla local · proyección activa"
            nodes[tkey]["title"] = f"{orch.entity} es la proyección activa de {p.physical_target} (hecho CSN); no es la misma tabla"
            parts.append(f"{p.flow} carga {p.physical_target}, tabla técnica de la que {orch.entity} es la proyección activa")
        elif p.target_kind == BASE:
            link(rfk, tkey, "data")
            nodes[tkey]["role"] = "Tabla local · destino físico"
            parts.append(f"{p.flow} carga {orch.entity}")
        else:
            link(rfk, tkey, "unverified")
            parts.append(f"{p.flow} produce {orch.entity} según el manifest (destino físico no verificado)")
        for c in p.chains:
            prev_k = None
            for name in c.path:
                k = f"tc:{name}"
                node(k, name, "task_chain", "Task Chain", f"Task Chain {name}" + (f" · {c.label}" if c.label and name == c.name else ""))
                if prev_k is not None:
                    link(prev_k, k, "orch")
                prev_k = k
            link(prev_k, rfk, "orch")
            parts.append(f"{' → '.join(c.path)} orquesta {p.flow}")
    return {"nodes": list(nodes.values()), "edge_kind": kinds, "aria": f"Pipeline de ingesta de {orch.entity}: " + "; ".join(parts) + "."}


_LEGEND = {
    "data": ("Flujo de datos", ""),
    "orch": ("Orquestación (Task Chain; no mueve datos)", "orch"),
    "proj": ("Proyección (no es una carga): tabla técnica — tabla base", "proj"),
    "unverified": ("Relación del manifest, destino físico no verificado", "unverified"),
}


def _legend_html(kinds: set) -> str:
    items = []
    for k in ("data", "orch", "proj", "unverified"):
        if k == "data" or k in kinds:
            label, cls = _LEGEND[k]
            edge_cls = "ln-edge" + (" ln-edge--" + cls if cls else "")
            items.append(f'<li><svg viewBox="0 0 30 8" width="30" height="8" aria-hidden="true" focusable="false"><path class="{edge_cls}" d="M1,4 L29,4"/></svg>{_E(label)}</li>')
    return f'<ul class="orch-legend">{"".join(items)}</ul>'


def _pipeline_notes(orch: IngestionOrchestration, cds_link: Callable[[str], str]) -> str:
    """Notas VISIBLES junto al diagrama: origen técnico (no es un nodo) y el estado de la orquestación cuando no hay Task Chain comprobada."""
    out = []
    for p in orch.producers:
        objs = ", ".join(_C(o) + (f" {cds_link(o)}" if cds_link(o) else "") for o in p.source_objects)
        bits = [b for b in (p.connection, p.connection_type, p.container) if b]
        if objs:
            tail = (f" · {_E(' · '.join(bits))}" if bits else "") + ("" if p.source_task_certain else " <em>(el flujo declara varias fuentes; no se fijó cuál alimenta esta tabla)</em>")
            out.append(f"<p>Origen técnico: {objs}{tail}</p>")
        if p.chains_state == NOT_FOUND_IN_EXPORT:
            out.append(f"<p>Sin Task Chain comprobada: ninguna cadena del export ejecuta {_C(p.flow)}. El Replication Flow es el orquestador conocido.</p>")
        elif p.chains_state != CONFIRMED:
            out.append("<p>Orquestación no evaluada: el export de este modelo no incluyó Task Chains.</p>")
    return f'<div class="orch-notes">{"".join(out)}</div>'


def render_html(orch: IngestionOrchestration, anchor: str, cds_link: Callable[[str], str] = lambda _o: "", diagram: Callable[..., str] | None = None) -> str:
    """Disclosure del Hub (`details.dashboard-section`, colapsado). Con `diagram` (el renderer de linaje compartido) y un productor comprobado, la representación principal es el
    PIPELINE visual y la tabla técnica completa queda como «Detalle técnico» (sin pérdida de contenido); sin `diagram` (o sin productor) se muestra la tabla/estado neutral de siempre."""
    model = pipeline_model(orch) if diagram is not None else None
    if model is not None:
        svg = diagram(model["nodes"], False, {n["name"] for n in model["nodes"]}, None, edge_kind=model["edge_kind"], aria=model["aria"])
        if svg:
            count = f" ({len(orch.producers)})" if len(orch.producers) > 1 else ""
            return (f'<details class="dashboard-section orch-pipeline" id="{_E(anchor)}">'
                    f'<summary class="dashboard-section__head"><span class="dashboard-section__caret"></span><span class="dashboard-section__title">Orquestación de ingesta{count}</span></summary>'
                    f'<div class="dashboard-section__body">{svg}{_legend_html(set(model["edge_kind"].values()))}{_pipeline_notes(orch, cds_link)}'
                    f'<details class="content-collapsible orch-text"><summary class="content-collapsible__summary">Detalle técnico</summary><div class="content-collapsible__body">'
                    f'<div class="matrix-scroll">{_table_html(orch, cds_link)}</div></div></details></div></details>')
    return _table_disclosure(orch, anchor, cds_link)


def _table_html(orch: IngestionOrchestration, cds_link: Callable[[str], str]) -> str:
    head = "<thead><tr><th>Eslabón</th><th>Detalle</th></tr></thead>"
    if orch.state == ESTABLISHED:
        blocks = []
        for i, p in enumerate(orch.producers):
            rows = _producer_rows(p, orch.entity, cds_link)
            if len(orch.producers) > 1:
                rows.insert(0, f'<tr><td colspan="2"><strong>Productor {i + 1} de {len(orch.producers)}</strong></td></tr>')
            blocks.append("".join(rows))
        body = "".join(blocks)
    else:
        body = _row("Estado", _E(orch.note or ""))
    return f"<table>{head}<tbody>{body}</tbody></table>"


def _table_disclosure(orch: IngestionOrchestration, anchor: str, cds_link: Callable[[str], str]) -> str:
    count = f" ({len(orch.producers)})" if orch.state == ESTABLISHED and len(orch.producers) > 1 else ""
    return (f'<details class="dashboard-section" id="{_E(anchor)}">'
            f'<summary class="dashboard-section__head"><span class="dashboard-section__caret"></span><span class="dashboard-section__title">Orquestación de ingesta{count}</span></summary>'
            f'<div class="dashboard-section__body"><div class="matrix-scroll">{_table_html(orch, cds_link)}</div></div></details>')


def inject_after_fields(section_html: str, block: str) -> str:
    """Inserta ``block`` justo DESPUÉS del colapsable «Campos (N)» de la sección (ya renderizado por `collapse_field_tables`); sin «Campos» lo agrega al final. El resto del HTML queda byte a byte igual."""
    marker = 'dashboard-section__title">Campos ('
    at = section_html.find(marker)
    if at < 0:
        return section_html + block
    end = section_html.find("</details>", at)
    return section_html + block if end < 0 else section_html[:end + len("</details>")] + block + section_html[end + len("</details>"):]
