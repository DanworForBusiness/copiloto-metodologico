"""
client_modeling_inventory.py — "Inventario técnico disponible" de un cliente (Portfolio, Composición del modelado). SOLO LECTURA.

Responde UNA pregunta: ¿qué artefactos técnicos únicos hay en la evidencia (pool de crudos) que el Copiloto tiene disponible para el cliente?
NO es el tenant completo de Datasphere ni "todos los objetos del cliente": solo lo que se extrajo y está en el pool.

Identidad de un artefacto (única, sin duplicar): cliente (alcance del pool) + familia de artefacto + nombre técnico. El pool de crudos NO informa el
espacio de Datasphere (la identidad con espacio requiere evidencia que hoy el pool no trae), por eso la base es CLIENT_FAMILY_NAME; jamás se usa el
nombre de negocio. Una vista que aparece en varios archivos, dominios o cierres cuenta UNA vez.

  * Vistas: pertenencia y tipo con las MISMAS funciones que "Abrir SQL" y el Assessment (`catalog_view_source.view_record` → clasificador único
    `tools/catalog/dsp_view_type.py`; carpeta `views` cuando el pool informa familias, marcador del exportador en pools planos).
  * Flows y Task Chains: un nombre técnico por archivo de su carpeta-tipo. Un 0 se muestra SOLO si hay evidencia de que la familia se adquirió
    (``effectiveFeatures`` de un ``lineage-manifest.json`` del exportador dentro del pool); sin esa evidencia y sin archivos → None (no disponible,
    jamás 0). Una familia con archivos es evidencia suficiente de su conteo. En pools planos las familias no se distinguen: None.

Lo abre el Portfolio: jamás conecta a Datasphere ni ejecuta el motor; solo lee el pool (con memo por huella de archivos).
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from tools.catalog import dsp_view_type as vt
from webapp.services import assessment_service as asvc
from webapp.services import catalog_view_source as cvs

log = logging.getLogger("danwor.client_modeling_inventory")

IDENTITY_BASIS = "CLIENT_FAMILY_NAME"
MANIFEST_NAME = "lineage-manifest.json"
# clave del snapshot → (carpeta-tipo normalizada del pool, feature del exportador)
FLOW_FAMILIES = {
    "replication_flows": ("replication flows", "replication-flows"),
    "data_flows": ("data flows", "data-flows"),
    "transformation_flows": ("transformation flows", "transformation-flows"),
    "task_chains": ("task chains", "task-chains"),
}
FLOW_ORDER = tuple(FLOW_FAMILIES)

EXHAUSTIVE = "EXHAUSTIVE"        # todas las vistas salen de la carpeta `views`: una vista sin marcador también se observa (UNKNOWN es observable)
PARTIAL = "PARTIAL"              # hay vistas reconocidas solo por marcador estructural: una vista sin marcador no es detectable
EVIDENCE_OBSERVED, EVIDENCE_ACQUIRED_ZERO, EVIDENCE_UNAVAILABLE = "OBSERVED", "ACQUIRED_ZERO", "UNAVAILABLE"

_lock = threading.RLock()
_memo: dict[tuple[str, str], tuple[tuple, dict]] = {}


def unavailable() -> dict:
    """Inventario sin evidencia utilizable (no hay pool o el cliente no figura en él): todo no disponible, nunca 0."""
    return {
        "available": False, "identity_basis": IDENTITY_BASIS,
        "views": {"total": None, "graphical": None, "sql": None, "unknown": None, "unknown_detection": None,
                  "membership": {"family_directory": None, "structural_marker": None}, "view_type_basis": {"stored": None, "derived": None}},
        **{k: None for k in FLOW_ORDER},
        "flow_evidence": {k: EVIDENCE_UNAVAILABLE for k in FLOW_ORDER},
    }


def _acquired_features(manifest_paths: list[Path]) -> set[str]:
    out: set[str] = set()
    for p in manifest_paths:
        data = cvs.load_json(p)
        feats = data.get("effectiveFeatures") if isinstance(data, dict) else None
        if isinstance(feats, list):
            out |= {str(f) for f in feats}
    return out


def _compute(root: Path, files: list[Path]) -> dict:
    views: dict[str, tuple[str, str, str]] = {}                    # nombre técnico → (tipo, procedencia del tipo, base de pertenencia)
    flows: dict[str, set[str]] = {k: set() for k in FLOW_ORDER}
    dir_to_key = {fam: key for key, (fam, _f) in FLOW_FAMILIES.items()}
    manifests = [p for p in files if p.name == MANIFEST_NAME]
    for path in sorted(p for p in files if p.name != MANIFEST_NAME):
        family = cvs.family_of_dir(path.parent.name) if path.parent != root else None
        if family in dir_to_key:
            flows[dir_to_key[family]].add(path.stem)
            continue
        data = cvs.load_json(path)
        if data is None:
            continue
        for name in (data.get("definitions") or {}):
            rec = cvs.view_record(data, name, family)
            if rec is None:
                continue
            prev = views.get(name)
            if prev is None or (prev[2] != cvs.MEMBERSHIP_FAMILY and rec[2] == cvs.MEMBERSHIP_FAMILY):   # la carpeta-tipo gana sobre el marcador
                views[name] = rec
    acquired = _acquired_features(manifests)
    n = {t: sum(1 for v in views.values() if v[0] == t) for t in vt.VIEW_TYPES}
    struct = sum(1 for v in views.values() if v[2] == cvs.MEMBERSHIP_STRUCTURAL)
    family_n = len(views) - struct
    views_acquired = "views" in acquired
    total = len(views) if (views or views_acquired) else None        # sin vistas ni evidencia de adquisición: "ninguna identificada" NO es 0
    block = {
        "total": total,
        "graphical": n[vt.GRAPHICAL] if total is not None else None,
        "sql": n[vt.SQL] if total is not None else None,
        "unknown": n[vt.UNKNOWN] if total is not None else None,
        "unknown_detection": (None if not total else (EXHAUSTIVE if struct == 0 else PARTIAL)),
        "membership": {"family_directory": family_n if total is not None else None, "structural_marker": struct if total is not None else None},
        "view_type_basis": {"stored": sum(1 for v in views.values() if v[1] == vt.BASIS_STORED) if total is not None else None,
                            "derived": sum(1 for v in views.values() if v[1] == vt.BASIS_DERIVED) if total is not None else None},
    }
    out: dict = {"available": True, "identity_basis": IDENTITY_BASIS, "views": block, "flow_evidence": {}}
    for key in FLOW_ORDER:
        feature = FLOW_FAMILIES[key][1]
        count = len(flows[key])
        if count:
            out[key], out["flow_evidence"][key] = count, EVIDENCE_OBSERVED
        elif feature in acquired:
            out[key], out["flow_evidence"][key] = 0, EVIDENCE_ACQUIRED_ZERO     # adquirida y sin artefactos: el único caso de 0
        else:
            out[key], out["flow_evidence"][key] = None, EVIDENCE_UNAVAILABLE   # sin archivos ni evidencia de adquisición: NO se infiere cero
    return out


def inventory_for(client: str) -> dict:
    """Inventario técnico disponible del cliente. Determinista; con memo por huella (ruta, tamaño, mtime) del pool del cliente."""
    try:
        found = asvc.pool_client_files(client)
        if found is None:
            return unavailable()
        root, files = found
        fp = tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files)
        key = (str(root), client.strip().lower())
        with _lock:
            cached = _memo.get(key)
            if cached is not None and cached[0] == fp:
                return json.loads(json.dumps(cached[1]))
        result = _compute(root, files)
        with _lock:
            _memo[key] = (fp, result)
        return json.loads(json.dumps(result))
    except Exception:                                              # noqa: BLE001 — jamás rompe el Portfolio
        log.exception("client_modeling_inventory: no se pudo leer el inventario de %s", client)
        return unavailable()
