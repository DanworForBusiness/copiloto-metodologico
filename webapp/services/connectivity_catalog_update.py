"""connectivity_catalog_update.py — del «+» del Catálogo de conexiones (F2.1) a una ACTUALIZACIÓN real del Catálogo, con assessment y aprobación.

Flujo (todo con piezas existentes):
  1) `prepare`: al terminar una extracción F2.1 (COMPLETE/PARTIAL) se comparan los documentos leídos con las conexiones PUBLICADAS del cliente
     (observación + reconciliación F2.2a: `from_cli_documents` vs `from_published_catalog`) y se evalúan con el motor F1 (`engine.evaluate_all`).
     Clasificación: NUEVA · MODIFICADA · SIN_CAMBIOS · EVIDENCIA_INSUFICIENTE (+ NO_OBSERVADA: publicada y no vista; nunca se da de baja).
     La propuesta queda en memoria para quien ejecutó (TTL), con los documentos SANEADOS (sin credenciales); los crudos no sobreviven al run.
  2) `approve` (SOLO el dueño): las conexiones elegidas (solo NUEVA/MODIFICADA) se escriben en el pool canónico del Catálogo
     (`<pool>/<Cliente>/Conexiones/conn_space_*.json`, mismo layout que los exports) y se regeneran con el generador existente
     (`generate_space_catalog`), que conserva el bloque `manual:` de cada ficha. Escritura con el candado de escritor único del Catálogo,
     `remote_store.write_through` (local atómico + Spaces) y deshacer ante falla. Nada se borra: el listado se fusiona por nombre (sin duplicados).
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

from tools.catalog.connectivity_governance import engine
from tools.catalog.connectivity_governance import observation as obs
from tools.catalog.connectivity_governance import reconciliation as rc
from tools.catalog.connectivity_governance.inventory import UNASSIGNED_ENVIRONMENT
from tools.catalog.connectivity_governance.snapshot import from_snapshot

TTL_S = 30 * 60
MAX_PENDING = 20
_PENDING: dict = {}
_LOCK = threading.Lock()

NEW, CHANGED, UNCHANGED, INSUFFICIENT, NOT_OBSERVED = "NUEVA", "MODIFICADA", "SIN_CAMBIOS", "EVIDENCIA_INSUFICIENTE", "NO_OBSERVADA"
LABEL = {NEW: "Nueva", CHANGED: "Modificada", UNCHANGED: "Sin cambios", INSUFFICIENT: "Evidencia insuficiente",
         NOT_OBSERVED: "Publicada, no observada"}
SELECTABLE = (NEW, CHANGED)
_GROUP_TEXT = {"type": "tipo", "adapter": "adaptador", "endpoint": "destino", "connectivity": "conectividad", "capabilities": "capacidades",
               "security": "seguridad", "parameters": "parámetros", "descriptive": "descripción"}
_SECRET_KEY = re.compile(r"(password|passwd|secret|token|credential|private.?key|^user(name)?$)", re.I)


class UpdateRejected(ValueError):
    pass


def _strip(value):
    """Documento del CLI sin credenciales (claves sensibles fuera, recursivo). Lo que no se puede garantizar limpio no se publica."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if not _SECRET_KEY.search(str(k))}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    if isinstance(value, str) and "://" in value:
        return obs.url_endpoint(value) or ""                 # URL sin usuario/contraseña/parámetros (mismo recorte que el contrato F2.2a)
    return value


def _clean(doc) -> bool:
    return not obs._FORBIDDEN.search(re.sub(r'"(credential_mode|auth_type)"', "", json.dumps(doc, ensure_ascii=False)))


def _published(client: str) -> dict:
    from webapp.services import catalog_reads
    _mod, entries = catalog_reads._catalog_architecture_connections()
    return obs.from_published_catalog(entries or [], client=client)


def _classify(item: dict) -> tuple[str, list]:
    o = item["outcome"]
    if o == rc.ONLY_IN_CURRENT:
        return NEW, []
    if o == rc.ONLY_IN_BASELINE:
        return NOT_OBSERVED, []
    if o != rc.MATCHED or item["detail_state"]["current"] != obs.DETAIL_READ:
        return INSUFFICIENT, []
    changed = [g for g, v in item["groups"].items() if v["result"] == rc.CHANGED and g in _GROUP_TEXT]
    if changed:
        return CHANGED, [_GROUP_TEXT[g] for g in changed]
    if item.get("technical") == rc.NOT_COMPARABLE:
        return INSUFFICIENT, []
    return UNCHANGED, []


def prepare(operation_id: str, *, executor: str, client: str, inventory: dict, listings: dict, details: dict, now=time.monotonic) -> dict | None:
    """Comparación + assessment F1 de UNA extracción. Retiene la propuesta para `executor`. None si la extracción no es evaluable."""
    if not inventory or inventory.get("status") not in ("COMPLETE", "PARTIAL") or not executor:
        return None
    current = obs.from_cli_documents(client=client, tenant_id=None, snapshot_id=inventory["snapshot_id"], captured_at=inventory["captured_at"],
                                     status=inventory["status"], exhaustiveness=inventory["exhaustiveness"], listings=listings, details=details,
                                     operation_id=operation_id)
    requested = current["coverage"]["requested_spaces"]
    result = rc.reconcile(current, _published(client), rc.GovernedScope(None, {s: "—" for s in requested}))
    docs = {k: _strip(v) for k, v in details.items() if isinstance(v, dict)}
    items = {(s, i.get("name")): _strip(i) for s, rows in listings.items() for i in (rows or []) if isinstance(i, dict)}
    verdicts = {}
    observed = []
    for (space, name), doc in docs.items():
        try:
            observed.append(from_snapshot({**doc, "space_name": doc.get("space_name") or space, "name": doc.get("name") or name},
                                          tenant="extraccion", environment=UNASSIGNED_ENVIRONMENT))
        except Exception:  # noqa: BLE001 — un documento que F1 no puede leer queda sin assessment (evidencia insuficiente)
            continue
    for v in engine.evaluate_all(observed):
        verdicts[(v.connection_key[2], v.connection_key[3])] = v
    cur_index = {(c["key"]["space"], c["key"]["connection_name"]): c for c in current["connections"]}
    rows = []
    for item in result["items"]:
        if item["outcome"] == rc.OUT_OF_SCOPE:
            continue
        key = (item["key"]["space"], item["key"]["connection_name"])
        cls, changed = _classify(item)
        if cls == NEW and cur_index.get(key, {}).get("detail_state") != obs.DETAIL_READ:
            cls = INSUFFICIENT
        v = verdicts.get(key)
        safe = key in docs and _clean(docs[key])
        rows.append({"id": f"{key[0]}::{key[1]}", "space": key[0], "name": key[1], "class": cls, "label": LABEL[cls], "changed": changed,
                     "type_id": ((cur_index.get(key) or {}).get("fields", {}).get("type_id") or {}).get("value"),
                     "f1": None if v is None else {"conformance": v.conformance.value, "operational": v.operational.value,
                                                   "evidence": v.evidence.value, "labels": list(v.labels)},
                     "selectable": cls in SELECTABLE and safe, "sanitized": safe})
    rows.sort(key=lambda r: (list(LABEL).index(r["class"]), r["space"], r["name"]))
    cov = current["coverage"]
    limits = [f"{'Extracción' if side == 'current' else 'Catálogo publicado'}: {_limit_text(code)}"
              for side, _s, code in (l.partition(":") for l in result["limits"]) if code != "TENANT_NOT_GOVERNED"]
    if cov["inaccessible_spaces"]:
        limits.append("Spaces inaccesibles: " + ", ".join(cov["inaccessible_spaces"]) + " — sus conexiones quedan sin evidencia, nunca como bajas.")
    limits.append("Sin contrato de homologación: el assessment F1 informa divergencias, nunca «no conforme».")
    view = {"operation_id": operation_id, "client": client, "rows": rows, "limits": limits,
            "counts": {c: sum(1 for r in rows if r["class"] == c) for c in LABEL}}
    t = now()
    with _LOCK:
        for k in [k for k, p in _PENDING.items() if t - p["held_at"] > TTL_S]:
            del _PENDING[k]
        while len(_PENDING) >= MAX_PENDING:
            del _PENDING[min(_PENDING, key=lambda k: _PENDING[k]["held_at"])]
        _PENDING[operation_id] = {"executor": executor.lower(), "client": client, "docs": docs, "items": items, "view": view, "held_at": t}
    return view


def _limit_text(code: str) -> str:
    from webapp.services.connectivity_capture_service import LIMIT_TEXT
    return LIMIT_TEXT.get(code, code)


def pending(operation_id: str, executor: str) -> dict | None:
    with _LOCK:
        p = _PENDING.get(operation_id)
    if p is None or time.monotonic() - p["held_at"] > TTL_S or p["executor"] != (executor or "").lower():
        return None
    return p


# ───────────────────────────── publicación aprobada ─────────────────────────────
def _write(path: Path, data: bytes, undo: list, durability: list) -> None:
    from core import remote_store
    undo.append((path, path.read_bytes() if path.exists() else None))
    durability.append(remote_store.write_through(path, data))


def approve(operation_id: str, *, actor: str, selected: list, rationale: str) -> dict:
    """Escribe en el Catálogo SOLO las conexiones elegidas (NUEVA/MODIFICADA y saneadas). Sin bajas, sin duplicados, `manual:` intacto."""
    from tools.catalog import generate_space_catalog as gsc
    from tools.catalog import publication_txn as txn
    from webapp.services.datasphere_publication_service import _canonical_roots
    p = pending(operation_id, actor)
    if p is None:
        raise UpdateRejected("La propuesta ya no está disponible (venció o es de otra ejecución). Volvé a extraer.")
    if not rationale.strip():
        raise UpdateRejected("El motivo es obligatorio.")
    rows = {r["id"]: r for r in p["view"]["rows"]}
    chosen = [rows[s] for s in dict.fromkeys(selected) if s in rows]
    if not chosen or any(not r["selectable"] for r in chosen) or len(chosen) != len(set(selected)):
        raise UpdateRejected("Solo se pueden publicar conexiones nuevas o modificadas, con evidencia leída y saneada.")
    catalog_root, pool = _canonical_roots()
    undo, durability, written = [], [], []
    with txn.writer_lock():
        client_dir = txn.canonical_client_dir(p["client"].replace(" ", "_"), txn.existing_client_dirs(catalog_root, pool))
        client_root = pool / client_dir
        conn_dir = gsc._find_subdir(client_root, "Conexiones") or (client_root / "Conexiones")
        try:
            for space in sorted({r["space"] for r in chosen}):
                listing_path = conn_dir / f"conn_space_{space}.json"
                listing = json.loads(listing_path.read_text(encoding="utf-8")) if listing_path.exists() else []
                listing = [i for i in listing if isinstance(i, dict)]
                for r in (r for r in chosen if r["space"] == space):
                    item = p["items"].get((space, r["name"])) or {"name": r["name"]}
                    listing = [i for i in listing if i.get("name") != r["name"]] + [item]          # fusión por nombre: sin duplicados ni bajas
                    _write(conn_dir / f"conn_space_{space}_{r['name']}.json",
                           json.dumps(p["docs"][(space, r["name"])], ensure_ascii=False, indent=2).encode("utf-8"), undo, durability)
                _write(listing_path, json.dumps(listing, ensure_ascii=False, indent=2).encode("utf-8"), undo, durability)
            catalog = gsc.load_spaces_and_connections(client_root).get(client_dir.replace("_", " "))
            if catalog is None:
                raise RuntimeError("el generador no reconoce el pool del cliente")
            base_rel = Path(gsc.SPACE_CATALOG_DIR) / gsc._slug(catalog.cliente)
            with tempfile.TemporaryDirectory(prefix="dw-conn-pub-") as tmp:
                tmp_base = Path(tmp) / base_rel
                if (catalog_root / base_rel).is_dir():
                    shutil.copytree(catalog_root / base_rel, tmp_base)                           # el generador lee el `manual:` vigente de cada ficha
                gsc.write_client_catalog(catalog, Path(tmp))
                targets = [base_rel / "arquitectura_conectividad.md"]
                for space in {r["space"] for r in chosen}:
                    targets.append(base_rel / "espacios" / f"{gsc._slug(space)}.md")
                targets += [base_rel / "conexiones" / f"{gsc._slug(r['space'])}__{gsc._slug(r['name'])}.md" for r in chosen]
                for rel in targets:
                    src = Path(tmp) / rel
                    if src.exists():
                        _write(catalog_root / rel, src.read_bytes(), undo, durability)
                        written.append(rel.as_posix())
        except Exception:
            for path, prev in reversed(undo):                                                       # deshacer: el Catálogo queda como estaba
                try:
                    if prev is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.write_bytes(prev)
                except OSError:
                    pass
            raise
    try:
        from webapp import main as hub_main
        hub_main._invalidate_catalog_cache()
    except Exception:  # noqa: BLE001
        pass
    with _LOCK:
        _PENDING.pop(operation_id, None)
    return {"published": [{"space": r["space"], "name": r["name"], "class": r["label"]} for r in chosen], "files": written,
            "durability": "remote" if durability and all(d == "remote" for d in durability) else "local"}
