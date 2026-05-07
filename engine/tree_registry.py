# engine/tree_registry.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
import yaml

from .tree_platform import CompiledTree, compile_tree


@dataclass
class TreeRegistry:
    trees: dict[str, CompiledTree]
    by_tag: dict[str, list[str]]
    by_module_stem: dict[str, list[str]]
    by_topic_id: dict[str, list[str]]

    def get(self, tree_id: str) -> Optional[CompiledTree]:
        return self.trees.get(tree_id)

    def list(
        self,
        tags: Optional[list[str]] = None,
        module_stem: str = "",
        topic_id: str = "",
    ) -> list[CompiledTree]:
        ids = set(self.trees.keys())

        if tags:
            # AND semantics: debe cumplir todos los tags
            for t in tags:
                ids &= set(self.by_tag.get(t, []))

        if module_stem:
            ids &= set(self.by_module_stem.get(module_stem, []))

        if topic_id:
            ids &= set(self.by_topic_id.get(topic_id, []))

        return [self.trees[i] for i in sorted(ids)]

    @property
    def all(self) -> list[CompiledTree]:
        return [self.trees[i] for i in sorted(self.trees.keys())]


def build_registry_from_folder(trees_dir: Path) -> TreeRegistry:
    trees: dict[str, CompiledTree] = {}
    by_tag: dict[str, list[str]] = {}
    by_module: dict[str, list[str]] = {}
    by_topic: dict[str, list[str]] = {}

    if not trees_dir.exists():
        return TreeRegistry(trees, by_tag, by_module, by_topic)

    for p in sorted(trees_dir.glob("*.yaml")):
        try:
            spec = yaml.safe_load(p.read_text(encoding="utf-8"))
            if not isinstance(spec, dict):
                continue
            spec["_path"] = str(p)

            # compile step
            ct = compile_tree(spec)

            # dedupe simple
            if ct.id in trees:
                # si repetís id, el último gana; si preferís, levantar error
                pass

            trees[ct.id] = ct

            # índices
            for t in ct.tags:
                by_tag.setdefault(t, []).append(ct.id)

            if ct.links.module_stem:
                by_module.setdefault(ct.links.module_stem, []).append(ct.id)

            # indexar topics (nuevo: links.topics lista) + fallback legacy (ct.links.topic_id)
            links_raw = spec.get("links", {}) or {}
            topics_val = links_raw.get("topics")
            if isinstance(topics_val, list) and topics_val:
                for topic in topics_val:
                    by_topic.setdefault(str(topic), []).append(ct.id)
            elif ct.links.topic_id:
                by_topic.setdefault(ct.links.topic_id, []).append(ct.id)
                
        except Exception as e:
            # DEBUG TEMP: no tragarse errores de compilación
            print(f"[TREE REGISTRY] Error de compilacion: {p.name}: {e}")
            continue

    return TreeRegistry(trees, by_tag, by_module, by_topic)