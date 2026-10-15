"""Recolector de la evidencia TECNICA del pool para ``canonical-evidence/v4`` (``leaf_materialization``). Lado Assessment (lee el pool que el assessment ya lee; NO escribe nada, NO toca produccion).

Reune, SOLO para las hojas locales y los flujos productores del perfil, lo que la derivacion necesita del CSN: definiciones de las hojas y de las entidades fisicas delta que declaran
(``deltaFromEntities``) y columnas origen/destino de las tareas de Replication Flow. Devuelve tambien el INDICE de referencias recuperables (archivo relativo al pool + puntero + hashes) para que
la seccion persista referencias y no el CSN. Sin logica de clasificacion: esa vive en ``tools.catalog.leaf_materialization``."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import legacy_bridge as bridge
from .legacy_bridge import leaf_materialization as LM          # derivacion unica (acceso por el bridge, R-I3)
from .legacy_bridge import leaf_materialization_evidence as LME

sha256_json = LME.sha256_json


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def collect_materialization_evidence(files: Iterable[Path], root: Path, ce: Mapping[str, Any], pool_signature: str | None) -> dict[str, Any]:
    """``{"csn": {...}, "references": {...}, "pool_signature": ...}`` para ``build_v4_sections``. Determinista (archivos ordenados por ruta relativa)."""
    ordered = sorted(files, key=lambda p: p.relative_to(root).as_posix())
    leaf_names = {r["leaf"] for r in (ce.get("leaf_provenance") or ()) if isinstance(r, Mapping) and isinstance(r.get("leaf"), str)}
    flow_names = {p["name"] for r in (ce.get("leaf_provenance") or ()) if isinstance(r, Mapping) for p in (r.get("producer_flows") or ()) if isinstance(p, Mapping) and isinstance(p.get("name"), str)}
    defs: dict[str, Any] = {}
    ent_refs: dict[str, dict[str, Any]] = {}
    rf_cols: dict[tuple[str, str], dict[str, list[str]]] = {}
    rf_refs: dict[tuple[str, str], dict[str, Any]] = {}
    name_to_file: dict[str, Path] = {}
    file_hash: dict[Path, str] = {}

    def fsha(p: Path) -> str:
        if p not in file_hash:
            file_hash[p] = bridge.file_sha256(p)
        return file_hash[p]

    def capture(name: str, definition: Any, p: Path) -> None:
        if name in defs:
            return
        defs[name] = definition
        ent_refs[name] = {"source_file": p.relative_to(root).as_posix(), "pointer": f"/definitions/{name}", "fragment_sha256": sha256_json(definition), "file_sha256": fsha(p)}

    for p in ordered:                                                          # pasada 1: hojas locales y flujos productores
        doc = _load(p)
        if not isinstance(doc, dict):
            continue
        d = doc.get("definitions")
        if isinstance(d, dict):
            for name, definition in d.items():
                name_to_file.setdefault(name, p)
                if name in leaf_names:
                    capture(name, definition, p)
        rfs = doc.get("replicationflows")
        if isinstance(rfs, dict):
            for fname, fd in rfs.items():
                if fname not in flow_names or not isinstance(fd, dict):
                    continue
                cols = LM.rf_task_columns({fname: fd})
                tasks = ((fd.get("contents") or {}).get("replicationTasks") or [])
                for i, task in enumerate(tasks):
                    tgt = ((task.get("targetObject") or {}) if isinstance(task, dict) else {}).get("name")
                    key = (fname, tgt)
                    if key in cols and key not in rf_cols:
                        rf_cols[key] = cols[key]
                        rf_refs[key] = {"source_file": p.relative_to(root).as_posix(), "pointer": f"/replicationflows/{fname}/contents/replicationTasks/{i}", "file_sha256": fsha(p)}
    shadows = {s for links in LM.explicit_delta_links({n: defs[n] for n in leaf_names if n in defs}).values() for s in links}
    for s in sorted(shadows):                                                  # pasada 2: entidades fisicas delta DECLARADAS por las hojas (nunca por sufijo)
        p = name_to_file.get(s)
        if p is not None and s not in defs:
            doc = _load(p)
            if isinstance(doc, dict) and isinstance(doc.get("definitions"), dict) and s in doc["definitions"]:
                capture(s, doc["definitions"][s], p)
    csn = {"entity_definitions": defs, "delta_links": LM.explicit_delta_links(defs), "rf_task_columns": rf_cols}
    return {"csn": csn, "references": {"entities": ent_refs, "rf_tasks": rf_refs}, "pool_signature": pool_signature}
