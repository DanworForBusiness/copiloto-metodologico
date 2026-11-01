"""connectivity_capture_service.py — CAPTURA GOBERNADA (opt-in) del inventario de una extracción F2.1 y su reconciliación (F2.2c).

Flujo:
  1) Extracción F2.1 ejecutada por el DUEÑO: al terminar (COMPLETE/PARTIAL) el servicio F2.1 entrega los documentos leídos a `hold`, que los
     normaliza a `connectivity-observation/v1` (allowlist estricta + `validate`: sin credenciales) y DESCARTA los documentos crudos. La
     observación queda PENDIENTE solo en memoria, para ese dueño y por `PENDING_TTL_S`; si nadie la captura, expira. Nada se escribe.
  2) Captura (opt-in, owner-only): `capture` exige un tenant ACTIVO del mismo cliente con el host de la extracción VINCULADO (registro
     gobernado); asocia la observación a ese tenant y al environment gobernado de cada Space (`observation.bind_to_registry`) y la persiste
     en su prefijo aislado (`connectivity_capture_store`). Idempotente por `request_id`.
  3) Reconciliación (lectura): `reconciliation_for` compara la captura más reciente contra la anterior del mismo tenant (o, si es la primera,
     contra el Catálogo publicado del cliente, de procedencia limitada) con el motor F2.2a y el alcance gobernado VIGENTE; las ausencias solo
     dan prioridad de revisión (`absence_history`), nunca bajas. No se publica nada en el Catálogo.
"""
from __future__ import annotations

import hashlib
import threading
import time

from tools.catalog.connectivity_governance import observation as obs
from tools.catalog.connectivity_governance import reconciliation as rc
from tools.catalog.connectivity_governance import registry as reg
from webapp.services import connectivity_capture_store as cstore
from webapp.services import connectivity_registry_store as rstore

PENDING_TTL_S = 30 * 60
MAX_PENDING = 20
_PENDING: dict = {}
_LOCK = threading.Lock()

REASON_TEXT = {
    "EXPIRED": "La extracción ya no está disponible para capturar (expiró o el servidor se reinició). Ejecutala de nuevo.",
    "NOT_YOURS": "Esa extracción no está disponible para tu usuario.",
    "TENANT_NOT_ACTIVE": "El tenant elegido no está registrado y activo para este cliente.",
    "HOST_NOT_BOUND": "El host de la extracción no está vinculado a ese tenant. Vinculalo en «Tenants y ambientes» antes de capturar.",
}


class CaptureRejected(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _purge(now: float) -> None:
    for k in [k for k, v in _PENDING.items() if now - v["held_at"] > PENDING_TTL_S]:
        del _PENDING[k]


def hold(operation_id: str, *, owner: str, client: str, host: str, inventory: dict, listings: dict, details: dict, now=time.monotonic) -> dict | None:
    """Normaliza y retiene la observación de UNA extracción (sin tenant gobernado todavía). None si no es capturable o no pasa el saneamiento."""
    if not inventory or inventory.get("status") not in ("COMPLETE", "PARTIAL") or not owner:
        return None
    try:
        host_n = reg.normalize_host(host)
        doc = obs.from_cli_documents(client=client, tenant_id=None, snapshot_id=inventory["snapshot_id"], captured_at=inventory["captured_at"],
                                     status=inventory["status"], exhaustiveness=inventory["exhaustiveness"], listings=listings, details=details,
                                     operation_id=operation_id)
    except Exception:  # noqa: BLE001 — fail-closed: lo que no se normaliza no se ofrece para capturar
        return None
    if obs.validate(doc):
        return None
    t = now()
    with _LOCK:
        _purge(t)
        while len(_PENDING) >= MAX_PENDING:
            del _PENDING[min(_PENDING, key=lambda k: _PENDING[k]["held_at"])]
        _PENDING[operation_id] = {"owner": owner.lower(), "client": client, "host": host_n, "observation": doc, "held_at": t}
    return {"operation_id": operation_id, "host": host_n, "spaces": doc["coverage"]["requested_spaces"],
            "connections": doc["coverage"]["discovered_connections"]}


def options(client: str, host: str) -> dict:
    """Tenants activos del cliente y el sugerido por host vinculado (coincidencia exacta normalizada). Nunca de otros clientes."""
    try:
        state = rstore.load_state()
    except Exception:  # noqa: BLE001
        return {"tenants": [], "suggested": None, "registry_available": False}
    tenants = [{"tenant_id": t["tenant_id"], "display_name": t["display_name"] or ""} for t in reg.tenants_of(state, client) if t["active"]]
    return {"tenants": tenants, "suggested": reg.tenant_for_host(state, host, client=client), "registry_available": True}


def capture(operation_id: str, *, actor: str, tenant_id: str, rationale: str, request_id: str, now_iso: str) -> dict:
    capture_id = "cc-" + hashlib.sha256(f"{actor}|{request_id}".encode("utf-8")).hexdigest()[:32]
    with _LOCK:
        _purge(time.monotonic())
        pending = _PENDING.get(operation_id)
    if pending is None:
        raise CaptureRejected("EXPIRED")
    if pending["owner"] != actor.lower():
        raise CaptureRejected("NOT_YOURS")
    state = rstore.load_state()
    tenant = state["tenants"].get(tenant_id)
    if tenant is None or tenant["client"] != pending["client"] or not tenant["active"]:
        raise CaptureRejected("TENANT_NOT_ACTIVE")
    if reg.tenant_for_host(state, pending["host"], client=pending["client"]) != tenant_id:
        raise CaptureRejected("HOST_NOT_BOUND")
    envs = reg.space_environments(state, pending["client"], tenant_id)
    bound = obs.bind_to_registry(pending["observation"], tenant_id=tenant_id, space_environments=envs)
    env = {"schema": cstore.SCHEMA, "capture_id": capture_id, "client": pending["client"], "tenant_id": tenant_id, "host": pending["host"],
           "captured_by": actor, "captured_at": now_iso, "rationale": rationale,
           "provenance": {"kind": "cli_extraction", "operation_id": operation_id, "snapshot_id": bound["provenance"]["snapshot_id"],
                          "observed_at": bound["provenance"]["captured_at"]},
           "space_environments": {s: envs.get(s) for s in bound["coverage"]["requested_spaces"]}, "observation": bound}
    out = cstore.append_capture(env, authorized_owner=True)
    with _LOCK:
        _PENDING.pop(operation_id, None)                    # capturada: no queda nada pendiente en memoria
    return out


# ───────────────────────────── lectura: capturas + reconciliación ─────────────────────────────
def _published_baseline(client: str):
    from webapp.services import catalog_reads
    _mod, entries = catalog_reads._catalog_architecture_connections()
    return obs.from_published_catalog(entries or [], client=client)


def captures_view(state: dict, client: str, tenant_id: str) -> dict:
    """Capturas del tenant + reconciliación de la más reciente (F2.2a) con el alcance gobernado VIGENTE. Fail-closed por tenant."""
    try:
        caps = cstore.load_captures(client, tenant_id)
    except cstore.HistoryIntegrityError:
        return {"error": "Hay capturas inconsistentes para este tenant: no se reconcilia un estado dudoso.", "captures": [], "reconciliation": None}
    except Exception:  # noqa: BLE001
        return {"error": "No se pudieron leer las capturas.", "captures": [], "reconciliation": None}
    rows = [{"capture_id": c["capture_id"], "captured_at": c["captured_at"], "captured_by": c["captured_by"], "rationale": c["rationale"],
             "status": c["observation"]["coverage"]["status"], "exhaustiveness": c["observation"]["coverage"]["exhaustiveness"],
             "spaces": c["observation"]["coverage"]["requested_spaces"], "inaccessible": c["observation"]["coverage"]["inaccessible_spaces"],
             "connections": c["observation"]["coverage"]["discovered_connections"], "read": c["observation"]["coverage"]["read_connections"],
             "space_environments": c["space_environments"]} for c in reversed(caps)]
    if not caps:
        return {"error": None, "captures": [], "reconciliation": None}
    current = caps[-1]["observation"]
    if len(caps) > 1:
        baseline, base_label = caps[-2]["observation"], "captura anterior"
    else:
        baseline, base_label = _published_baseline(client), "Catálogo publicado (procedencia limitada)"
    result = rc.reconcile(current, baseline, reg.governed_scope(state, client, tenant_id))
    history = [c["observation"] for c in caps]
    for item in result["items"]:
        if item["outcome"] == rc.ONLY_IN_BASELINE:
            item["absence"] = rc.absence_history((item["key"]["space"], item["key"]["connection_name"]), history)
    notable = [i for i in result["items"] if i["outcome"] != rc.MATCHED or i.get("technical") == rc.CHANGED
               or i.get("parameters") == rc.CHANGED or i.get("descriptive") == rc.CHANGED]
    limits = [f"{'Captura' if side == 'current' else 'Base'}: {LIMIT_TEXT.get(code, code)}"
              for side, _sep, code in (l.partition(":") for l in result["limits"])]
    return {"error": None, "captures": rows,
            "reconciliation": {**result, "limits": limits, "baseline_label": base_label, "notable": notable}}


LIMIT_TEXT = {
    "PUBLISHED_CATALOG_WITHOUT_EVIDENCE_DATE_OR_COVERAGE": "es el Catálogo publicado, sin fecha de evidencia ni cobertura declarada",
    "EXHAUSTIVENESS_UNVERIFIABLE": "el listado no permite probar que estén todas las conexiones",
    "EXECUTION_PARTIAL": "la ejecución fue parcial",
    "EXECUTION_FAILED": "la ejecución falló",
    "SPACE_DISCOVERY_NOT_PERFORMED": "solo se observaron los Spaces pedidos (no se descubrieron otros)",
    "TENANT_NOT_GOVERNED": "sin tenant gobernado",
}
