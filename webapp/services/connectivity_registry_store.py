"""connectivity_registry_store.py — almacén DURABLE append-only del registro `connectivity-registry/v1` (F2.2b).

Reutiliza el almacén gobernado EXISTENTE en Spaces (`core/remote_store`: `put/get/list_governed_decision_*`, namespace `governed-decisions/`, fuera de
`PERSIST_PATHS`/`mirror_up`, create-only) con un prefijo PROPIO `governed-decisions/_connectivity-registry/<cliente>/`: plano separado de las
ratificaciones del Judgment (que viven en `governed-decisions/<cliente>/<hash>/`), sin infraestructura ni variables nuevas.
Mismo flujo que `governed_decisions_store` (B2): validación ⇒ idempotencia por bytes ⇒ PUT create-only ⇒ confirmación por bytes; lectura con replay
determinista y fail-closed ante objetos corruptos. Además, antes de escribir se reconstruye el estado y se RECHAZA un evento que el replay no aplicaría
(conflicto de identidad, Space ya asignado, revocación inválida, otro cliente): al almacén no entran conflictos.

Autorización: escribir exige que el llamador acredite al DUEÑO (APP_OWNER) — este módulo no lee sesiones; el `actor` del evento lo pone la capa web.
F2.2c.1: relectura posterior (`ConcurrentConflict`) e idempotencia por `event_id`. Fuera de alcance: inventarios extraídos (F2.2c.2) y reconciliación (F2.2d).
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


class ConcurrentConflict(RegistryConflict):
    """F2.2c.1: el evento QUEDÓ guardado, pero al releer el registro el replay NO lo aplica (otra escritura concurrente ganó el orden
    determinista). Estado explícito: no se informa éxito ni se simula consistencia transaccional; el evento queda en el historial como rechazado."""

    def __init__(self, message: str, *, key: str, reason: str):
        super().__init__(message)
        self.key, self.reason = key, reason


def _intent(event: dict) -> str:
    """Contenido de la decisión sin su fecha: un reintento del MISMO pedido (mismo `event_id`) llega con otro `decided_at`."""
    return reg.canonical_json({k: v for k, v in event.items() if k != "decided_at"})


def _confirm_applied(event: dict, key: str, *, stored: bool, idempotent: bool) -> dict:
    """RELECTURA posterior (F2.2c.1): guardar no es aplicar. Se reconstruye el registro desde el almacén y se exige que el evento esté
    presente y aplicado; si el replay lo rechaza (carrera check-then-PUT) ⇒ `ConcurrentConflict` con el motivo."""
    events = load_events()
    if not any(e["event_id"] == event["event_id"] for e in events):
        raise StorageUnconfirmed(f"el evento no aparece al releer el registro: {key}")
    reasons = [r["reason"] for r in reg.replay(events)["rejected"] if r["event_id"] == event["event_id"]]
    if reasons:
        raise ConcurrentConflict(f"el evento quedó guardado pero el registro no lo aplica: {reasons[0]}", key=key, reason=reasons[0])
    return {"stored": stored, "idempotent": idempotent, "applied": True, "key": key}


def append_event(event: dict, *, authorized_owner: bool) -> dict:
    """Persiste UN evento del registro. Flujo fail-closed:
      1) autorización de dueño; 2) validación del contrato; 3) idempotencia: mismo `event_id` con la misma decisión ⇒ no se reescribe
      (con otra decisión ⇒ conflicto); 4) replay del registro + el evento: si no se aplicaría ⇒ `RegistryConflict` (no se escribe);
      5) PUT create-only + confirmación por bytes; 6) relectura: el evento tiene que quedar APLICADO en el estado reconstruido."""
    if not authorized_owner:
        raise NotAuthorized("solo el dueño puede modificar el registro de conectividad")
    reg.validate_event(event)
    data = reg.canonical_json(event).encode("utf-8")
    key = event_key(event)
    events = load_events()
    prior = next((e for e in events if e["event_id"] == event["event_id"]), None)
    if prior is not None:
        if _intent(prior) != _intent(event):
            raise RegistryConflict(f"event_id existente con contenido distinto: {event['event_id']}")
        return _confirm_applied(prior, event_key(prior), stored=False, idempotent=True)
    state = reg.replay(events + [event])
    reasons = [r["reason"] for r in state["rejected"] if r["event_id"] == event["event_id"]]
    if reasons:
        raise RegistryConflict(f"el evento contradice el registro: {reasons[0]}")
    try:
        remote_store.put_governed_decision_object(key, data)
    except remote_store.GovernedDecisionObjectExists:
        if remote_store.get_governed_decision_object(key) != data:
            raise RegistryConflict(f"clave escrita concurrentemente con otro contenido: {key}")
        return _confirm_applied(event, key, stored=False, idempotent=True)
    if remote_store.get_governed_decision_object(key) != data:
        raise StorageUnconfirmed(f"el objeto no se confirmó por bytes tras el PUT: {key}")
    return _confirm_applied(event, key, stored=True, idempotent=False)
