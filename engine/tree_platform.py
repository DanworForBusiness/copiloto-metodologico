# engine/tree_platform.py
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Optional, Literal


NodeType = Literal["single_choice"]  # por ahora mantenemos tu PoC tal cual


@dataclass(frozen=True)
class TreeLinks:
    module_stem: str = ""   # ej: "03_Integracion_y_Estrategia_de_Datos"
    topic_id: str = ""      # ej: "extraccion"


@dataclass(frozen=True)
class CompiledNode:
    id: str
    type: NodeType
    question: str
    options: list[str]
    next_map: dict[str, str]   # answer -> next_id


@dataclass(frozen=True)
class CompiledLeaf:
    id: str
    leaf: dict[str, Any]       # mantenemos tu estructura libre (decision/summary/etc)


@dataclass(frozen=True)
class CompiledTree:
    id: str
    title: str
    version: str
    tags: list[str]
    links: TreeLinks
    start: str
    nodes: dict[str, CompiledNode]
    leaves: dict[str, CompiledLeaf]
    source_path: str = ""

    def get(self, key: str, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __getitem__(self, key: str):
        if key == "id":
            return self.id
        if key == "title":
            return self.title
        if key == "version":
            return self.version
        if key == "tags":
            return self.tags
        if key == "start":
            return self.start
        if key == "nodes":
            # si tu app espera dict crudo, devolvemos un dict compatible
            return {k: {"type": v.type, "question": v.question, "options": v.options, "next": v.next_map} for k, v in self.nodes.items()}
        if key == "leaves":
            return {k: v.leaf for k, v in self.leaves.items()}
        if key == "links":
            return asdict(self.links)
        if key == "_path":
            return self.source_path
        raise KeyError(key)

@dataclass
class DecisionEvent:
    ts: str
    tree_id: str
    node_id: str
    question: str
    answer_raw: Any
    answer_normalized: Any
    next_id: Optional[str]
    rule_applied: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compile_tree(spec: dict[str, Any]) -> CompiledTree:
    """
    Compile step "soft":
    - Normaliza campos
    - Construye nodos/leaves compilados
    - Hace sanity checks mínimos para no explotar en runtime
    """
    tree_id = str(spec.get("id", "")).strip()
    if not tree_id:
        raise ValueError("Tree spec missing 'id'.")

    start = str(spec.get("start", "")).strip()
    if not start:
        raise ValueError(f"Tree '{tree_id}' missing 'start'.")

    title = str(spec.get("title", tree_id))
    version = str(spec.get("version", "0.0"))
    tags = list(spec.get("tags", []) or [])

    links_raw = spec.get("links", {}) or {}

    # module_stem legacy OR module nuevo
    module_val = links_raw.get("module_stem") or links_raw.get("module") or ""
    module_val = str(module_val).strip()

    # topic_id legacy OR topics nuevo
    topics_val = links_raw.get("topics", None)
    if isinstance(topics_val, list) and topics_val:
        topic_val = str(topics_val[0]).strip()
    else:
        topic_val = str(links_raw.get("topic_id") or "").strip()

    links = TreeLinks(
        module_stem=module_val,
        topic_id=topic_val,
    )

    nodes_raw = spec.get("nodes", {}) or {}
    leaves_raw = spec.get("leaves", {}) or {}
    if not isinstance(nodes_raw, dict) or not isinstance(leaves_raw, dict):
        raise ValueError(f"Tree '{tree_id}' nodes/leaves must be dicts.")

    nodes: dict[str, CompiledNode] = {}
    for node_id, n in nodes_raw.items():
        if not isinstance(n, dict):
            continue
        nid = str(node_id)
        ntype = str(n.get("type", "single_choice"))
        if ntype != "single_choice":
            # mantenemos PoC: sólo single_choice
            ntype = "single_choice"

        question = str(n.get("question", f"Pregunta {nid}"))
        options = list(n.get("options", []) or [])
        next_map = dict(n.get("next", {}) or {})

        # sanity: opciones deben ser strings
        options = [str(o) for o in options]
        next_map = {str(k): str(v) for k, v in next_map.items()}

        nodes[nid] = CompiledNode(
            id=nid,
            type="single_choice",
            question=question,
            options=options,
            next_map=next_map,
        )

    leaves: dict[str, CompiledLeaf] = {}
    for leaf_id, leaf_obj in leaves_raw.items():
        lid = str(leaf_id)
        leaf_dict = leaf_obj if isinstance(leaf_obj, dict) else {"decision": str(leaf_obj)}
        leaves[lid] = CompiledLeaf(id=lid, leaf=leaf_dict)

    # sanity mínimo: start existe
    if start not in nodes and start not in leaves:
        raise ValueError(f"Tree '{tree_id}' start '{start}' not found in nodes/leaves.")

    # sanity mínimo: next targets existen
    for node in nodes.values():
        for ans, nxt in node.next_map.items():
            if nxt not in nodes and nxt not in leaves:
                # no hardening: lo marcamos como "dangling" y se resolverá en runtime como leaf error
                # (si preferís: levantar error acá)
                pass

    return CompiledTree(
        id=tree_id,
        title=title,
        version=version,
        tags=tags,
        links=links,
        start=start,
        nodes=nodes,
        leaves=leaves,
        source_path=str(spec.get("_path", "")),
    )


def start_run_state(tree: CompiledTree) -> dict[str, Any]:
    """
    Estado serializable (para Streamlit session_state).
    Mantiene compatibilidad con route[] (strings) y agrega trail[] (eventos estructurados).
    """
    return {
        "tree_id": tree.id,
        "current": tree.start,
        "route": [],   # compat: ["q1: ECC", ...]
        "trail": [],   # nuevo: list[dict] de DecisionEvent
        "started_at": _utc_now_iso(),
        "ended_at": None,
    }


def append_event(state: dict[str, Any], ev: DecisionEvent) -> None:
    state.setdefault("trail", [])
    state["trail"].append(asdict(ev))


def export_run_json(tree: CompiledTree, state: dict[str, Any], leaf_id: Optional[str]) -> dict[str, Any]:
    leaf_payload = tree.leaves.get(leaf_id).leaf if leaf_id and leaf_id in tree.leaves else None
    return {
        "export_version": "0.1",
        "tree": {
            "id": tree.id,
            "title": tree.title,
            "version": tree.version,
            "tags": tree.tags,
            "links": asdict(tree.links),
            "source_path": tree.source_path,
        },
        "run": {
            "current": state.get("current"),
            "leaf_id": leaf_id,
            "route": list(state.get("route", [])),
            "trail": list(state.get("trail", [])),
            "started_at": state.get("started_at"),
            "ended_at": state.get("ended_at"),
        },
        "leaf": leaf_payload,
    }