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
_SECRET_KEY = re.compile(r"(password|passwd|secret|token|credential|private.?key|assertion|user(name|id)?$)", re.I)   # también oauth2UserId, SAML assertions…
_URL_KEY = re.compile(r"(endpoint|url|uri)$", re.I)          # p. ej. oauth2TokenEndpoint: su valor es una URL pública, no un token


def _sensitive(key, value) -> bool:
    """Propiedad que se ELIMINA al sanear. Excepción: un endpoint/URL cuyo valor es una URL simple se conserva reducida a esquema/host/ruta."""
    if not _SECRET_KEY.search(str(key)):
        return False
    return not (_URL_KEY.search(str(key)) and isinstance(value, str) and _URL.match(value.strip()))


class UpdateRejected(ValueError):
    pass


# Archivos del pool escritos por el «+» (rutas relativas al pool). La regeneración desde SharePoint los pasa como `skip_rels` a
# `fetch_csn_folder` (mecanismo existente): ni se sobrescriben ni se podan; así una regeneración no deshace una aprobación en silencio.
LEDGER = ".connectivity_publications.json"


def canonical_roots() -> tuple[Path, Path]:
    """Raíces del Catálogo para el flujo de conexiones: las rutas CANÓNICAS del Hub (`<BASE_DIR>/catalog_local` y su pool `.csn_cache`), las mismas
    que persiste Spaces. Sin variables de entorno: no son requisito operativo de este flujo."""
    import config
    root = (Path(config.BASE_DIR) / "catalog_local").resolve()
    return root, root / ".csn_cache"


def protected_rels(pool) -> frozenset:
    path = Path(pool) / LEDGER
    try:
        return frozenset(json.loads(path.read_text(encoding="utf-8")).get("rels", []))
    except (OSError, ValueError, AttributeError):
        return frozenset()


def _strip(value):
    """Documento del CLI sin credenciales (claves sensibles fuera, recursivo). Lo que no se puede garantizar limpio no se publica."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if not _sensitive(k, v)}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    embedded = _embedded_json(value)
    if embedded is not None:                                 # JSON serializado dentro de un texto (p. ej. `configuration`): se sanea por dentro
        return json.dumps(_strip(embedded), ensure_ascii=False)
    if isinstance(value, str) and _URL.match(value.strip()):
        return _reduce_url(value)
    return value


_URL = re.compile(r"^(jdbc:)?[A-Za-z][\w+.-]*://\S+$", re.I)            # URL simple (también JDBC); un texto que solo contiene URLs no lo es


def _embedded_json(value):
    if isinstance(value, str) and value.strip()[:1] in ("{", "["):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return None


def _reduce_url(value: str) -> str:
    """URL sin usuario/contraseña/parámetros (mismo recorte que el contrato F2.2a), conservando el esquema http/https."""
    endpoint = obs.url_endpoint(value) or ""
    scheme = value.strip().split("://", 1)[0].lower()
    return f"{scheme}://{endpoint}" if endpoint and scheme in ("http", "https") else endpoint


def unsafe_properties(value, path: str = "") -> list:
    """Rutas (SOLO nombres de propiedad, nunca valores) que impiden garantizar el saneamiento del documento ORIGINAL: una propiedad que `_strip`
    conserva y cuyo nombre o valor de texto contiene un marcador de credenciales. Las propiedades sensibles se eliminan (no bloquean) y las URLs
    se reducen a host/ruta sin usuario, contraseña ni parámetros (una palabra como «token» en la RUTA de un endpoint OAuth no es un secreto).
    Lista vacía = documento publicable."""
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            if _sensitive(k, v):
                continue                                                  # se elimina al sanear
            here = f"{path}.{k}" if path else str(k)
            if obs._FORBIDDEN.search(str(k)) and not (isinstance(v, str) and _URL.match(v.strip())):
                out.append(here)                                          # (un endpoint con «token» en el nombre y una URL como valor no bloquea)
            else:
                out += unsafe_properties(v, here)
    elif isinstance(value, list):
        for v in value:
            out += unsafe_properties(v, f"{path}[]")
    elif _embedded_json(value) is not None:
        out += unsafe_properties(_embedded_json(value), path)
    elif isinstance(value, str) and not _URL.match(value.strip()) and obs._FORBIDDEN.search(value):
        out.append(path or "(valor)")
    return sorted(set(out))


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
    baseline = _published(client)
    result = rc.reconcile(current, baseline, rc.GovernedScope(None, {s: "—" for s in requested}))
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
        unsafe = unsafe_properties(details[key]) if key in docs else []
        safe = key in docs and not unsafe
        rows.append({"id": f"{key[0]}::{key[1]}", "space": key[0], "name": key[1], "class": cls, "label": LABEL[cls], "changed": changed,
                     "type_id": ((cur_index.get(key) or {}).get("fields", {}).get("type_id") or {}).get("value"),
                     "f1": None if v is None else {"conformance": v.conformance.value, "operational": v.operational.value,
                                                   "evidence": v.evidence.value, "labels": list(v.labels)},
                     "selectable": cls in SELECTABLE and safe, "sanitized": safe, "unsafe": unsafe[:5]})
    rows.sort(key=lambda r: (list(LABEL).index(r["class"]), r["space"], r["name"]))
    cov = current["coverage"]
    limits = [f"{'Extracción' if side == 'current' else 'Catálogo publicado'}: {_limit_text(code)}"
              for side, _s, code in (l.partition(":") for l in result["limits"]) if code != "TENANT_NOT_GOVERNED"]
    if cov["inaccessible_spaces"]:
        limits.append("Spaces inaccesibles: " + ", ".join(cov["inaccessible_spaces"]) + " — sus conexiones quedan sin evidencia, nunca como bajas.")
    limits.append("Sin contrato de homologación: el assessment F1 informa divergencias, nunca «no conforme».")
    view = {"operation_id": operation_id, "client": client, "rows": rows, "limits": limits,
            "considerations": _considerations(client, cov, len(baseline["connections"]), rows, set(result["limits"])),
            "counts": {c: sum(1 for r in rows if r["class"] == c) for c in LABEL}}
    t = now()
    with _LOCK:
        for k in [k for k, p in _PENDING.items() if t - p["held_at"] > TTL_S]:
            del _PENDING[k]
        while len(_PENDING) >= MAX_PENDING:
            del _PENDING[min(_PENDING, key=lambda k: _PENDING[k]["held_at"])]
        _PENDING[operation_id] = {"executor": executor.lower(), "client": client, "docs": docs, "items": items, "view": view, "held_at": t}
    return view


def _considerations(client: str, cov: dict, published: int, rows: list, codes: set) -> list:
    """PRESENTACIÓN de «Alcance y consideraciones», agrupada por concepto y con los datos REALES del resultado (los códigos y la procedencia
    originales siguen en `limits` y en la reconciliación). Solo los conceptos que aplican."""
    groups = []
    ref = (f"La referencia son las {published} conexiones publicadas en el Hub para {client}" if published
           else f"{client} no tiene conexiones publicadas en el Hub: todo lo leído se compara contra un Catálogo vacío")
    groups.append({"title": "Comparación con el Catálogo existente", "items": [
        ref + ", no necesariamente el estado más reciente de SAP Datasphere: el Catálogo no registra fecha de evidencia ni cobertura."]})
    requested, inaccessible = cov["requested_spaces"], cov["inaccessible_spaces"]
    items = [f"Spaces consultados: {', '.join(requested)}" + (f" (sin acceso: {', '.join(inaccessible)})." if inaccessible else "."),
             f"Conexiones recuperadas: {cov['read_connections']} con detalle leído de {cov['discovered_connections']} listadas."]
    if inaccessible:
        items.append("Las conexiones de los Spaces sin acceso quedan sin evidencia; nunca se consideran bajas.")
    if cov["status"] == "PARTIAL":
        items.append("La extracción terminó con errores parciales: revisá la cobertura antes de aprobar.")
    if "current:EXHAUSTIVENESS_UNVERIFIABLE" in codes:
        items.append("SAP no permite probar que el listado esté completo: una conexión publicada que no aparece no se da de baja.")
    groups.append({"title": "Cobertura de la extracción", "items": items})
    if any(r["f1"] for r in rows):
        divergent = sum(1 for r in rows if r["f1"] and r["f1"]["conformance"] == "DIVERGENTE")
        items = ["No hay evidencia de homologación registrada para este cliente: el assessment no puede afirmar que conexiones de distintos "
                 "ambientes sean equivalentes, y esa ausencia no implica una no conformidad."]
        if divergent:
            items.append(f"{divergent} conexión(es) muestran diferencias entre ambientes que conviene revisar.")
        groups.append({"title": "Equivalencia entre ambientes", "items": items})
    return groups


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
    p = pending(operation_id, actor)
    if p is None:
        raise UpdateRejected("La propuesta ya no está disponible (venció o es de otra ejecución). Volvé a extraer.")
    if not rationale.strip():
        raise UpdateRejected("El motivo es obligatorio.")
    rows = {r["id"]: r for r in p["view"]["rows"]}
    chosen = [rows[s] for s in dict.fromkeys(selected) if s in rows]
    if not chosen or any(not r["selectable"] for r in chosen) or len(chosen) != len(set(selected)):
        raise UpdateRejected("Solo se pueden publicar conexiones nuevas o modificadas, con evidencia leída y saneada.")
    catalog_root, pool = canonical_roots()
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
            pool_rels = sorted({p.relative_to(pool).as_posix() for p, _prev in undo})
            _write(pool / LEDGER, json.dumps({"rels": sorted(protected_rels(pool) | set(pool_rels))}, indent=2).encode("utf-8"), undo, durability)
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
        from tools.catalog import catalog_service as _cs
        from webapp.services import catalog_governance
        catalog_governance.lift_retirement([_cs.CatalogConnectionEntry(path="", cliente=p["client"], space=r["space"], connection=r["name"],
                                                                       type_id="").entry_id for r in chosen])   # volver a importar levanta el retiro
    except Exception:  # noqa: BLE001 — best-effort, como al republicar un modelo
        pass
    try:
        from webapp import main as hub_main
        hub_main._invalidate_catalog_cache()
    except Exception:  # noqa: BLE001
        pass
    with _LOCK:
        _PENDING.pop(operation_id, None)
    return {"published": [{"space": r["space"], "name": r["name"], "class": r["label"]} for r in chosen], "files": written,
            "durability": "remote" if durability and all(d == "remote" for d in durability) else "local"}
