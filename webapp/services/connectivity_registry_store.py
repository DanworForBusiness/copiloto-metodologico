"""connectivity_registry_store.py — almacén DURABLE append-only del registro `connectivity-registry/v1` (F2.2b).

Reutiliza el almacén gobernado EXISTENTE en Spaces (`core/remote_store`: `put/get/list_governed_decision_*`, namespace `governed-decisions/`, fuera de
`PERSIST_PATHS`/`mirror_up`, create-only) con un prefijo PROPIO `governed-decisions/_connectivity-registry/<cliente>/`: plano separado de las
ratificaciones del Judgment (que viven en `governed-decisions/<cliente>/<hash>/`), sin infraestructura ni variables nuevas.
Mismo flujo que `governed_decisions_store` (B2): validación ⇒ idempotencia por bytes ⇒ PUT create-only ⇒ confirmación por bytes; lectura con replay
determinista y fail-closed ante objetos corruptos. Además, antes de escribir se reconstruye el estado y se RECHAZA un evento que el replay no aplicaría
(conflicto de identidad, Space ya asignado, revocación inválida, otro cliente): al almacén no entran conflictos.

Autorización: escribir exige que el llamador acredite al DUEÑO (APP_OWNER) — este módulo no lee sesiones; el `actor` del evento lo pone la capa web.
Fuera de alcance (F2.2c/d): inventarios extraídos, UI, rutas y publicación.
"""
from __future__ import annotations

import json
import re

from core import remote_store
from tools.catalog.connectivity_governance import registry as reg

NAMESPACE = remote_store.GOVERNED_DECISIONS_NAMESPACE
PREFIX = f"{NAMESPACE}/_connectivity-registry/"
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class RegistryStoreError(RuntimeError):
    """Base de los errores del almacén (fail-closed)."""


class NotAuthorized(RegistryStoreError):
    """Solo el dueño registra tenants, hosts y asignaciones."""


class RegistryConflict(RegistryStoreError):
    """El evento contradice el registro (identidad, asignación vigente, otro cliente) o reusa un `event_id` con otro contenido."""


class StorageUnconfirmed(RegistryStoreError):
    """No se pudo confirmar por bytes tras el PUT: nunca se informa éxito."""


class HistoryIntegrityError(RegistryStoreError):
    """Objeto listado ausente, no-JSON, fuera de contrato o con cliente cruzado: no se reconstruye un estado dudoso."""


def _slug(value: str) -> str:
    return _SAFE.sub("_", str(value or "").strip()) or "_"


def client_prefix(client: str) -> str:
    return f"{PREFIX}{_slug(client)}/"


def event_key(event: dict) -> str:
    stamp = reg._utc(event["decided_at"]).strftime("%Y%m%dT%H%M%SZ")
    return f"{client_prefix(event['client'])}{stamp}-{_slug(event['event_id'])}.json"


def load_events() -> list:
    """TODOS los eventos del registro (todos los clientes: la unicidad de tenant y host es global). Fail-closed ante objetos inválidos."""
    events = []
    for key in remote_store.list_governed_decision_keys(PREFIX):
        raw = remote_store.get_governed_decision_object(key)
        if raw is None:
            raise HistoryIntegrityError(f"clave listada sin objeto: {key}")
        try:
            event = json.loads(raw.decode("utf-8"))
            reg.validate_event(event)
        except Exception as exc:  # noqa: BLE001
            raise HistoryIntegrityError(f"objeto inválido en el registro: {key}") from exc
        if not key.startswith(client_prefix(event["client"])):
            raise HistoryIntegrityError(f"el cliente del evento no coincide con la ruta: {key}")
        events.append(event)
    return events


def load_state() -> dict:
    """Estado del registro por replay determinista (`registry.replay`). Solo lectura."""
    return reg.replay(load_events())


def append_event(event: dict, *, authorized_owner: bool) -> dict:
    """Persiste UN evento del registro. Flujo fail-closed:
      1) autorización de dueño; 2) validación del contrato; 3) idempotencia por bytes en la misma clave;
      4) replay del registro + el evento: si no se aplicaría ⇒ `RegistryConflict` (no se escribe); 5) PUT create-only + confirmación por bytes."""
    if not authorized_owner:
        raise NotAuthorized("solo el dueño puede modificar el registro de conectividad")
    reg.validate_event(event)
    data = reg.canonical_json(event).encode("utf-8")
    key = event_key(event)
    existing = remote_store.get_governed_decision_object(key)
    if existing is not None:
        if existing == data:
            return {"stored": False, "idempotent": True, "key": key}
        raise RegistryConflict(f"clave existente con contenido distinto: {key}")
    state = reg.replay(load_events() + [event])
    reasons = [r["reason"] for r in state["rejected"] if r["event_id"] == event["event_id"]]
    if reasons:
        raise RegistryConflict(f"el evento contradice el registro: {reasons[0]}")
    try:
        remote_store.put_governed_decision_object(key, data)
    except remote_store.GovernedDecisionObjectExists:
        if remote_store.get_governed_decision_object(key) == data:
            return {"stored": False, "idempotent": True, "key": key}
        raise RegistryConflict(f"clave escrita concurrentemente con otro contenido: {key}")
    if remote_store.get_governed_decision_object(key) != data:
        raise StorageUnconfirmed(f"el objeto no se confirmó por bytes tras el PUT: {key}")
    return {"stored": True, "idempotent": False, "key": key}
