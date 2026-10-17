"""
governed_decisions_store.py — Almacén DURABLE append-only de eventos `governed-decision/v1` (v4.B, incremento B2).

Persiste cada evento RATIFIED/REVOKED (validado por la biblioteca pura B1 `governed_decisions`) como UN objeto create-only en
el namespace EXCLUSIVO `governed-decisions/` de Spaces, **fuera de `PERSIST_PATHS`/`mirror_up`** (ver `core/remote_store`:
`put/get/list_governed_decision_object/keys`). Reconstruye el historial por replay DETERMINISTA (por contenido, no por el
orden de Spaces), validando y rechazando eventos inválidos/duplicados/contradictorios (fail-closed).

Límites (v4.B / B2): NO actualiza ni borra (no existen esas rutas); NO ratifica/revoca desde UI/API/CLI (eso es B3 con auth
real del APP_OWNER); NO usa el contenido del registro para modificar Assessments, `canonical-evidence/v4` ni Judgment (D8).
El digest de evidencia vigente y el `now` los provee el llamador: este módulo NO lee perfiles/evidencia.

GARANTÍAS Y LÍMITES (importante, no sobre-afirmar):
  * B2 GARANTIZA: integridad de escritura CONFIRMADA por bytes (nunca informa éxito sin confirmar) e inmutabilidad LÓGICA en
    operación normal (create-only; no existen rutas de update/delete; revocar = evento nuevo).
  * B2 NO garantiza: **exclusividad atómica** entre escritores concurrentes ni **inmutabilidad física** del bucket.
  * Concurrencia: `put_governed_decision_object` es **check-then-PUT** — DigitalOcean Spaces NO documenta `If-None-Match` en
    PutObject, así que **NO hay create-only atómico**. check-then-PUT **NO impide** una sobrescritura concurrente: si dos
    escritores ven la clave ausente en su check, el segundo PUT crudo sobrescribe (ver `test_create_only_is_check_then_put_
    not_atomic`). Lo que SÍ acota el riesgo en la práctica: la clave es ÚNICA por evento (`decided_at` + `event_id`), con
    `event_id` de **unicidad robusta generado en la capa funcional de B3** (no acá) ⇒ dos eventos distintos nunca comparten
    clave; un reintento idéntico es idempotente; un reuso de `event_id` con contenido distinto se rechaza en operación
    secuencial (create-only) y, de colarse por una carrera, el **replay lo detecta fail-closed**.
  * Inmutabilidad física: la app no borra/actualiza, pero la clave de Spaces es de bucket (no por prefijo); Object Lock/
    versionado NO están verificados y NO se asumen (misma limitación que el namespace de respaldos).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from core import remote_store
from webapp.services import governed_decisions as gd

NAMESPACE = remote_store.GOVERNED_DECISIONS_NAMESPACE
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class GovernedDecisionStoreError(RuntimeError):
    """Base de los errores del almacén (fail-closed)."""


class StorageUnconfirmed(GovernedDecisionStoreError):
    """El objeto no se pudo confirmar por bytes tras el PUT ⇒ NUNCA se informa éxito."""


class DecisionConflict(GovernedDecisionStoreError):
    """Colisión de clave con bytes distintos (reuso de `event_id` con otro contenido): create-only, no se sobrescribe."""


class HistoryIntegrityError(GovernedDecisionStoreError):
    """El historial persistido es inválido/contradictorio (evento corrupto, subject cruzado, id duplicado divergente,
    revocación sin su RATIFIED): fail-closed, no se reconstruye un estado dudoso."""


# ─────────────────────────── claves deterministas ───────────────────────────

def _slug(value: str) -> str:
    s = _SAFE.sub("_", str(value or "").strip())
    return s or "_"


def _subject_hash(subject: dict) -> str:
    """Hash estable del subject EXACTO (client + technical_id) → agrupa un contrato sin depender del slug (que es cosmético)."""
    payload = gd.canonical_json({"client": subject.get("client"), "technical_id": subject.get("technical_id")})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def subject_prefix(subject: dict) -> str:
    """Prefijo de TODOS los eventos de un contrato: `governed-decisions/<client_slug>/<subject_hash>/`."""
    return f"{NAMESPACE}/{_slug(subject.get('client'))}/{_subject_hash(subject)}/"


def client_prefix(client: str) -> str:
    """Prefijo de los eventos de un cliente (best-effort por slug; cada evento lleva el client exacto)."""
    return f"{NAMESPACE}/{_slug(client)}/"


def _decided_compact(decided_at: str) -> str:
    return gd.parse_utc(decided_at).strftime("%Y%m%dT%H%M%SZ")


def event_key(event: dict) -> str:
    """Clave determinista y ÚNICA por evento: `<subject_prefix><decided_at>-<event_id>.json`. `event_id` único ⇒ sin colisión entre eventos distintos."""
    return f"{subject_prefix(event['subject'])}{_decided_compact(event['decided_at'])}-{_slug(event['event_id'])}.json"


# ─────────────────────────── escritura (create-only + confirmación) ───────────────────────────

def append_event(event: dict) -> dict:
    """Persiste UN evento ya construido/validable. Flujo fail-closed:
      1) valida (B1) — inválido ⇒ lanza, NO escribe;
      2) si la clave existe con bytes idénticos ⇒ idempotente (no reescribe);
      3) si existe con bytes distintos ⇒ `DecisionConflict` (nunca sobrescribe);
      4) PUT create-only + **confirmación por bytes** (GET posterior == lo escrito); si no confirma ⇒ `StorageUnconfirmed`.
    Devuelve `{"stored": bool, "idempotent": bool, "key": str}`."""
    gd.validate_event(event)
    data = gd.canonical_json(event).encode("utf-8")
    key = event_key(event)

    existing = remote_store.get_governed_decision_object(key)
    if existing is not None:
        if existing == data:
            return {"stored": False, "idempotent": True, "key": key}
        raise DecisionConflict(f"clave existente con contenido distinto (¿reuso de event_id?): {key}")

    try:
        remote_store.put_governed_decision_object(key, data)
    except remote_store.GovernedDecisionObjectExists:
        # carrera entre el GET y el PUT: alguien escribió la misma clave en el medio
        other = remote_store.get_governed_decision_object(key)
        if other == data:
            return {"stored": False, "idempotent": True, "key": key}
        raise DecisionConflict(f"clave escrita concurrentemente con otro contenido: {key}")

    confirm = remote_store.get_governed_decision_object(key)
    if confirm != data:
        raise StorageUnconfirmed(f"el objeto no se confirmó por bytes tras el PUT: {key}")   # jamás éxito sin confirmación
    return {"stored": True, "idempotent": False, "key": key}


# ─────────────────────────── lectura + reconstrucción ───────────────────────────

def list_subject_keys(client: str, technical_id: str) -> list[str]:
    """Claves de todos los eventos de un contrato (orden de Spaces; el replay NO depende de este orden)."""
    return remote_store.list_governed_decision_keys(subject_prefix({"client": client, "technical_id": technical_id}))


def list_client_keys(client: str) -> list[str]:
    """Claves de todos los eventos de un cliente."""
    return remote_store.list_governed_decision_keys(client_prefix(client))


def load_events(client: str, technical_id: str) -> list[dict]:
    """Baja y VALIDA cada evento del contrato. Fail-closed ante objeto ausente/no-JSON/esquema inválido/subject cruzado."""
    events: list[dict] = []
    for key in list_subject_keys(client, technical_id):
        raw = remote_store.get_governed_decision_object(key)
        if raw is None:
            raise HistoryIntegrityError(f"clave listada sin objeto: {key}")
        try:
            event = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise HistoryIntegrityError(f"objeto no-JSON: {key}") from exc
        gd.validate_event(event)
        subj = event.get("subject") or {}
        if subj.get("client") != client or subj.get("technical_id") != technical_id:
            raise HistoryIntegrityError(f"el subject del evento no coincide con la ruta: {key}")
        events.append(event)
    return events


def _dedupe_and_check(events: list[dict]) -> list[dict]:
    """Deduplica eventos idénticos por `event_id` y rechaza contradicciones (fail-closed)."""
    by_id: dict[str, dict] = {}
    for e in events:
        eid = e["event_id"]
        if eid in by_id:
            if gd.canonical_json(by_id[eid]) != gd.canonical_json(e):
                raise HistoryIntegrityError(f"event_id duplicado con contenido distinto: {eid}")
            continue                                               # duplicado idéntico → idempotente
        by_id[eid] = e
    unique = list(by_id.values())
    ratified = {e["event_id"] for e in unique if e["event"] == gd.EVENT_RATIFIED}
    for e in unique:
        if e["event"] == gd.EVENT_REVOKED and e.get("revokes_event_id") not in ratified:
            raise HistoryIntegrityError(f"revocación referencia un RATIFIED inexistente: {e['event_id']}")
    return unique


def reconstruct(client: str, technical_id: str, current_contract_digest: str | None, now: datetime) -> dict:
    """Historial + estado derivado de UN contrato, por replay DETERMINISTA (por contenido). `current_contract_digest` y `now`
    los provee el llamador (este módulo NO lee evidencia/Assessments). Rechaza historiales contradictorios (fail-closed).
    NO modifica nada (solo lectura)."""
    events = _dedupe_and_check(load_events(client, technical_id))
    status = gd.derive_status(events, current_contract_digest, now)
    ordered = sorted(events, key=lambda e: (gd.parse_utc(e["decided_at"]), str(e.get("event_id") or "")))
    return {"subject": {"client": client, "technical_id": technical_id}, "events": ordered, "status": status}
