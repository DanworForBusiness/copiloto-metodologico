"""Contrato `connectivity-registry/v1` (F2.2b): registro GOBERNADO cliente → tenant → Space → environment.

PURO: construcción/validación de eventos y replay determinista; sin I/O (la persistencia es `webapp/services/connectivity_registry_store`, con el
almacén gobernado existente en Spaces). Mismo modelo que `governed-decision/v1`: append-only, cada hecho es un EVENTO con actor, fecha, motivo y
procedencia; nada se actualiza ni se borra (un cambio es un evento nuevo que referencia al anterior).

Dimensiones SEPARADAS (no se derivan unas de otras):
  * IDENTIDAD TÉCNICA del tenant: `tenant_id` opaco y estable, declarado por el responsable. NO depende del host, del nombre comercial ni del ambiente.
  * ACCESO: `HOST_BOUND` vincula un host al tenant (puede haber varios; un host pertenece a UN solo tenant). Cambiar de host no cambia la identidad.
    `HOST_UNBOUND` (F2.2c.1) desvincula referenciando el `HOST_BOUND` VIGENTE: corrige un vínculo equivocado o libera el host, sin borrar historia.
  * CORRECCIÓN DE IDENTIDAD (F2.2c.1): el `tenant_id` es INMUTABLE y nunca se reutiliza; un registro equivocado se DESACTIVA (`TENANT_DEACTIVATED`),
    solo si no le quedan hosts ni asignaciones vigentes. Un tenant desactivado no admite eventos nuevos y no entra en el alcance gobernado.
  * AMBIENTE: `SPACE_ASSIGNED` asigna (tenant, Space) → environment del vocabulario cerrado. Nunca se infiere de nombres de Space ni de URLs.
  * Fuera de este registro: el DESTINO FÍSICO de cada conexión (host/SID/mandante, que sale de las observaciones) y la FAMILIA LÓGICA SAP
    (`connectivity-homologation/v1`, F0). El registro no los guarda ni los deduce.

Reglas de conflicto (fail-closed en el replay; los eventos rechazados quedan listados con su motivo, nunca se aplican):
  tenant_id único global y de UN cliente · host único global · un Space tiene a lo sumo UNA asignación vigente por tenant; reasignar exige
  `supersedes_event_id` = la vigente · revocar exige `revokes_event_id` = una asignación vigente · desvincular exige `unbinds_event_id` = un
  vínculo vigente · desactivar exige tenant activo y sin vínculos ni asignaciones vigentes · un cliente nunca opera sobre tenants de otro ·
  `event_id` repetido: idéntico ⇒ idempotente, distinto ⇒ conflicto.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

SCHEMA = "connectivity-registry/v1"

TENANT_REGISTERED = "TENANT_REGISTERED"
HOST_BOUND = "HOST_BOUND"
SPACE_ASSIGNED = "SPACE_ASSIGNED"
ASSIGNMENT_REVOKED = "ASSIGNMENT_REVOKED"
HOST_UNBOUND = "HOST_UNBOUND"
TENANT_DEACTIVATED = "TENANT_DEACTIVATED"
KINDS = (TENANT_REGISTERED, HOST_BOUND, SPACE_ASSIGNED, ASSIGNMENT_REVOKED, HOST_UNBOUND, TENANT_DEACTIVATED)

ENVIRONMENTS = ("DEV", "QAS", "PRD")                 # vocabulario CERRADO; se asigna, nunca se infiere
PROVENANCE_KINDS = ("owner_declaration", "evidence_review")

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?)+$")
_SPACE = re.compile(r"^[A-Za-z0-9_.\-]{1,100}$")


class RegistryError(ValueError):
    """Evento fuera del contrato (forma, vocabulario o identidad inválida)."""


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise RegistryError(f"fecha inválida: {value}") from exc
    if dt.tzinfo is None:
        raise RegistryError("la fecha debe incluir zona horaria (UTC)")
    return dt.astimezone(timezone.utc)


def normalize_host(host: str) -> str:
    """Host EXACTO de acceso (minúsculas, sin punto final). No acepta URLs, puertos ni rutas: no se interpreta nada del texto."""
    h = str(host or "").strip().lower().rstrip(".")
    if not _HOST.match(h):
        raise RegistryError(f"host inválido (solo nombre de host, sin esquema/puerto/ruta): {host!r}")
    return h


def _text(value, field: str, pattern=None) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise RegistryError(f"'{field}' debe ser un texto exacto, sin espacios en los extremos")
    if pattern is not None and not pattern.match(value):
        raise RegistryError(f"'{field}' con formato inválido: {value!r}")
    return value


def validate_event(event) -> None:
    """Forma y vocabulario de UN evento (no mira el historial: eso es `replay`)."""
    if not isinstance(event, dict) or event.get("schema") != SCHEMA:
        raise RegistryError(f"no es un evento {SCHEMA}")
    kind = event.get("kind")
    if kind not in KINDS:
        raise RegistryError(f"kind fuera del contrato: {kind}")
    common = {"schema", "event_id", "kind", "client", "actor", "decided_at", "rationale", "provenance"}
    specific = {TENANT_REGISTERED: {"tenant_id", "display_name"}, HOST_BOUND: {"tenant_id", "host"},
                SPACE_ASSIGNED: {"tenant_id", "space", "environment", "supersedes_event_id"},
                ASSIGNMENT_REVOKED: {"tenant_id", "revokes_event_id"}, HOST_UNBOUND: {"tenant_id", "unbinds_event_id"},
                TENANT_DEACTIVATED: {"tenant_id"}}[kind]
    if set(event) != common | specific:
        raise RegistryError(f"campos fuera del contrato para {kind}: {sorted(set(event) ^ (common | specific))}")
    _text(event["event_id"], "event_id", _ID)
    _text(event["client"], "client")
    _text(event["actor"], "actor")
    _text(event["rationale"], "rationale")
    _utc(event["decided_at"])
    prov = event["provenance"]
    if not isinstance(prov, dict) or prov.get("kind") not in PROVENANCE_KINDS or set(prov) - {"kind", "evidence_ref"}:
        raise RegistryError("provenance fuera del contrato")
    _text(event["tenant_id"], "tenant_id", _ID)
    if kind == TENANT_REGISTERED and event["display_name"] is not None:
        _text(event["display_name"], "display_name")
    if kind == HOST_BOUND and event["host"] != normalize_host(event["host"]):
        raise RegistryError("host no normalizado")
    if kind == SPACE_ASSIGNED:
        _text(event["space"], "space", _SPACE)
        if event["environment"] not in ENVIRONMENTS:
            raise RegistryError(f"environment fuera del vocabulario {ENVIRONMENTS}: {event['environment']!r}")
        if event["supersedes_event_id"] is not None:
            _text(event["supersedes_event_id"], "supersedes_event_id", _ID)
    if kind == ASSIGNMENT_REVOKED:
        _text(event["revokes_event_id"], "revokes_event_id", _ID)
    if kind == HOST_UNBOUND:
        _text(event["unbinds_event_id"], "unbinds_event_id", _ID)


def _event(kind: str, *, event_id, client, actor, decided_at, rationale, provenance, **specific) -> dict:
    ev = {"schema": SCHEMA, "event_id": event_id, "kind": kind, "client": client, "actor": actor, "decided_at": decided_at,
          "rationale": rationale, "provenance": provenance or {"kind": "owner_declaration"}, **specific}
    validate_event(ev)
    return ev


def register_tenant(*, tenant_id, display_name=None, **common) -> dict:
    return _event(TENANT_REGISTERED, tenant_id=tenant_id, display_name=display_name, **common)


def bind_host(*, tenant_id, host, **common) -> dict:
    return _event(HOST_BOUND, tenant_id=tenant_id, host=normalize_host(host), **common)


def assign_space(*, tenant_id, space, environment, supersedes_event_id=None, **common) -> dict:
    return _event(SPACE_ASSIGNED, tenant_id=tenant_id, space=space, environment=environment, supersedes_event_id=supersedes_event_id, **common)


def revoke_assignment(*, tenant_id, revokes_event_id, **common) -> dict:
    return _event(ASSIGNMENT_REVOKED, tenant_id=tenant_id, revokes_event_id=revokes_event_id, **common)


def unbind_host(*, tenant_id, unbinds_event_id, **common) -> dict:
    return _event(HOST_UNBOUND, tenant_id=tenant_id, unbinds_event_id=unbinds_event_id, **common)


def deactivate_tenant(*, tenant_id, **common) -> dict:
    return _event(TENANT_DEACTIVATED, tenant_id=tenant_id, **common)


# ───────────────────────────── replay ─────────────────────────────
def replay(events: list) -> dict:
    """Estado derivado de TODOS los eventos (de uno o varios clientes), por replay DETERMINISTA (orden: decided_at, event_id — nunca el orden
    de llegada). Devuelve `{"tenants": {...}, "hosts": {...}, "rejected": [...]}`; un evento rechazado NO se aplica."""
    rejected, by_id, unique = [], {}, []
    for ev in events:
        try:
            validate_event(ev)
        except RegistryError as exc:
            rejected.append({"event_id": (ev or {}).get("event_id") if isinstance(ev, dict) else None, "reason": f"INVALID: {exc}"})
            continue
        prev = by_id.get(ev["event_id"])
        if prev is not None:
            if canonical_json(prev) != canonical_json(ev):
                rejected.append({"event_id": ev["event_id"], "reason": "DUPLICATE_ID_DIFFERENT_CONTENT", "client": ev["client"], "kind": ev["kind"]})
            continue                                                       # idéntico ⇒ idempotente
        by_id[ev["event_id"]] = ev
        unique.append(ev)
    tenants: dict = {}
    hosts: dict = {}
    for ev in sorted(unique, key=lambda e: (_utc(e["decided_at"]), e["event_id"])):
        reason = _apply(ev, tenants, hosts)
        if reason:
            rejected.append({"event_id": ev["event_id"], "reason": reason, "client": ev["client"], "kind": ev["kind"]})
    return {"schema": SCHEMA, "tenants": tenants, "hosts": hosts, "rejected": rejected}


def _log(tenant: dict, ev: dict, **extra) -> None:
    """Historial del tenant: TODOS sus eventos aplicados, en orden de replay (forma uniforme; `space`/`host` en None si no aplican)."""
    tenant["history"].append({"event_id": ev["event_id"], "kind": ev["kind"], "space": None, "environment": None, "host": None,
                              "actor": ev["actor"], "decided_at": ev["decided_at"], "rationale": ev["rationale"],
                              "provenance": ev["provenance"], **extra})


def _apply(ev: dict, tenants: dict, hosts: dict) -> str | None:
    kind, tid, client = ev["kind"], ev["tenant_id"], ev["client"]
    if kind == TENANT_REGISTERED:
        if tid in tenants:                                                 # el id es inmutable y no se reutiliza (tampoco si está desactivado)
            return "TENANT_ALREADY_REGISTERED" if tenants[tid]["client"] == client else "TENANT_BELONGS_TO_ANOTHER_CLIENT"
        tenants[tid] = {"tenant_id": tid, "client": client, "display_name": ev["display_name"], "hosts": [], "spaces": {},
                        "history": [], "registered_by": ev["actor"], "registered_at": ev["decided_at"], "event_id": ev["event_id"],
                        "active": True, "deactivation": None}
        _log(tenants[tid], ev)
        return None
    tenant = tenants.get(tid)
    if tenant is None:
        return "TENANT_NOT_REGISTERED"
    if tenant["client"] != client:
        return "TENANT_BELONGS_TO_ANOTHER_CLIENT"                         # aislamiento entre clientes
    if not tenant["active"]:
        return "TENANT_DEACTIVATED"                                        # un registro desactivado no admite eventos nuevos
    if kind == HOST_BOUND:
        owner = hosts.get(ev["host"])
        if owner is not None:
            return None if owner == tid else "HOST_BOUND_TO_ANOTHER_TENANT"
        hosts[ev["host"]] = tid
        tenant["hosts"].append({"host": ev["host"], "event_id": ev["event_id"], "bound_at": ev["decided_at"], "bound_by": ev["actor"]})
        _log(tenant, ev, host=ev["host"])
        return None
    if kind == HOST_UNBOUND:
        binding = next((h for h in tenant["hosts"] if h["event_id"] == ev["unbinds_event_id"]), None)
        if binding is None:
            return "UNBINDS_NON_ACTIVE_BINDING"
        tenant["hosts"].remove(binding)
        del hosts[binding["host"]]                                         # el host queda libre; el historial conserva ambos eventos
        _log(tenant, ev, host=binding["host"], unbinds_event_id=ev["unbinds_event_id"])
        return None
    if kind == TENANT_DEACTIVATED:
        if tenant["hosts"] or tenant["spaces"]:
            return "TENANT_HAS_ACTIVE_BINDINGS"                            # primero se desvinculan hosts y se revocan asignaciones
        tenant["active"] = False
        tenant["deactivation"] = {"event_id": ev["event_id"], "actor": ev["actor"], "decided_at": ev["decided_at"], "rationale": ev["rationale"]}
        _log(tenant, ev)
        return None
    if kind == SPACE_ASSIGNED:
        current = tenant["spaces"].get(ev["space"])
        if current is None and ev["supersedes_event_id"] is not None:
            return "SUPERSEDES_WITHOUT_ACTIVE_ASSIGNMENT"
        if current is not None and ev["supersedes_event_id"] != current["event_id"]:
            return "SPACE_ALREADY_ASSIGNED"                                # reasignar exige referenciar la vigente
        tenant["spaces"][ev["space"]] = {"space": ev["space"], "environment": ev["environment"], "event_id": ev["event_id"],
                                         "assigned_by": ev["actor"], "assigned_at": ev["decided_at"], "provenance": ev["provenance"]}
        _log(tenant, ev, space=ev["space"], environment=ev["environment"], supersedes_event_id=ev["supersedes_event_id"],
             previous_environment=current["environment"] if current else None)
        return None
    # ASSIGNMENT_REVOKED
    target = next((s for s in tenant["spaces"].values() if s["event_id"] == ev["revokes_event_id"]), None)
    if target is None:
        return "REVOKES_NON_ACTIVE_ASSIGNMENT"
    del tenant["spaces"][target["space"]]
    _log(tenant, ev, space=target["space"], previous_environment=target["environment"], revokes_event_id=ev["revokes_event_id"])
    return None


# ───────────────────────────── consultas (sin inferencias) ─────────────────────────────
def tenants_of(state: dict, client: str) -> list:
    """Tenants de UN cliente (nunca los de otro)."""
    return sorted((t for t in state["tenants"].values() if t["client"] == client), key=lambda t: t["tenant_id"])


def tenant_for_host(state: dict, host: str, client: str | None = None):
    """`tenant_id` vinculado EXPLÍCITAMENTE a ese host (coincidencia exacta normalizada), o None. Con `client`, solo si el tenant es de ese cliente."""
    try:
        tid = state["hosts"].get(normalize_host(host))
    except RegistryError:
        return None
    if tid is None or (client is not None and state["tenants"][tid]["client"] != client):
        return None
    return tid


def space_environments(state: dict, client: str, tenant_id: str) -> dict:
    """Asignaciones VIGENTES `space → environment` de un tenant de ese cliente ({} si no existe o es de otro cliente)."""
    tenant = state["tenants"].get(tenant_id)
    if tenant is None or tenant["client"] != client:
        return {}
    return {s: v["environment"] for s, v in sorted(tenant["spaces"].items())}


def governed_scope(state: dict, client: str, tenant_id: str):
    """Alcance para `reconciliation.reconcile` (F2.2a): None si el tenant no está registrado para ese cliente."""
    from .reconciliation import GovernedScope
    tenant = state["tenants"].get(tenant_id)
    if tenant is None or tenant["client"] != client or not tenant["active"]:
        return None
    return GovernedScope(tenant_id, space_environments(state, client, tenant_id))
