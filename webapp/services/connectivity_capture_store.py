"""connectivity_capture_store.py — almacén DURABLE de CAPTURAS de inventario (`connectivity-capture/v1`, F2.2c).

Una captura = una observación `connectivity-observation/v1` de una extracción F2.1, ASOCIADA al tenant gobernado y al environment gobernado de
cada Space (`connectivity-registry/v1`), con actor, motivo y procedencia. Reutiliza el almacén gobernado existente en Spaces (`core/remote_store`,
namespace `governed-decisions/`, fuera de `PERSIST_PATHS`/`mirror_up`, create-only) con un prefijo PROPIO aislado por cliente y tenant:
`governed-decisions/_connectivity-captures/<cliente>/<tenant>/`. Nada se publica en el Catálogo: la captura es evidencia, no una decisión.
Mismo flujo que el registro: dueño ⇒ validación (contrato + saneamiento de la observación) ⇒ idempotencia por `capture_id` ⇒ PUT create-only ⇒
confirmación por bytes. Lectura fail-closed ante objetos corruptos o fuera de su ruta.
"""
from __future__ import annotations

import json
import re

from core import remote_store
from tools.catalog.connectivity_governance import observation as obs
from tools.catalog.connectivity_governance import registry as reg

SCHEMA = "connectivity-capture/v1"
PREFIX = f"{remote_store.GOVERNED_DECISIONS_NAMESPACE}/_connectivity-captures/"
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_KEYS = {"schema", "capture_id", "client", "tenant_id", "host", "captured_by", "captured_at", "rationale", "provenance",
         "space_environments", "observation"}


class CaptureStoreError(RuntimeError):
    """Base (fail-closed)."""


class NotAuthorized(CaptureStoreError):
    pass


class CaptureConflict(CaptureStoreError):
    """`capture_id` reutilizado con otro contenido."""


class StorageUnconfirmed(CaptureStoreError):
    pass


class HistoryIntegrityError(CaptureStoreError):
    pass


def _slug(value: str) -> str:
    return _SAFE.sub("_", str(value or "").strip()) or "_"


def tenant_prefix(client: str, tenant_id: str) -> str:
    return f"{PREFIX}{_slug(client)}/{_slug(tenant_id)}/"


def capture_key(env: dict) -> str:
    stamp = reg._utc(env["captured_at"]).strftime("%Y%m%dT%H%M%SZ")
    return f"{tenant_prefix(env['client'], env['tenant_id'])}{stamp}-{_slug(env['capture_id'])}.json"


def validate(env) -> None:
    if not isinstance(env, dict) or env.get("schema") != SCHEMA or set(env) != _KEYS:
        raise ValueError(f"no es una captura {SCHEMA}")
    for f in ("capture_id", "client", "tenant_id", "host", "captured_by", "rationale"):
        reg._text(env[f], f)
    reg._utc(env["captured_at"])
    if env["host"] != reg.normalize_host(env["host"]):
        raise ValueError("host no normalizado")
    o = env["observation"]
    problems = obs.validate(o)
    if problems:
        raise ValueError(f"observación inválida: {problems[0]}")
    p = o["provenance"]
    if p.get("tenant_id") != env["tenant_id"] or not p.get("tenant_governed") or p.get("client") != env["client"]:
        raise ValueError("la observación no está asociada al tenant/cliente de la captura")
    if any(c["key"]["tenant_id"] != env["tenant_id"] for c in o["connections"]):
        raise ValueError("conexión de otro tenant en la captura")


def _intent(env: dict) -> str:
    return reg.canonical_json({k: v for k, v in env.items() if k != "captured_at"})


def load_captures(client: str, tenant_id: str) -> list:
    """Capturas de UN tenant de UN cliente, de la más vieja a la más nueva. Fail-closed."""
    out = []
    prefix = tenant_prefix(client, tenant_id)
    for key in remote_store.list_governed_decision_keys(prefix):
        raw = remote_store.get_governed_decision_object(key)
        if raw is None:
            raise HistoryIntegrityError(f"clave listada sin objeto: {key}")
        try:
            env = json.loads(raw.decode("utf-8"))
            validate(env)
        except Exception as exc:  # noqa: BLE001
            raise HistoryIntegrityError(f"captura inválida: {key}") from exc
        if env["client"] != client or env["tenant_id"] != tenant_id or capture_key(env) != key:
            raise HistoryIntegrityError(f"la captura no coincide con su ruta: {key}")
        out.append(env)
    return sorted(out, key=lambda e: (reg._utc(e["captured_at"]), e["capture_id"]))


def append_capture(env: dict, *, authorized_owner: bool) -> dict:
    if not authorized_owner:
        raise NotAuthorized("solo el dueño captura inventarios")
    validate(env)
    prior = next((e for e in load_captures(env["client"], env["tenant_id"]) if e["capture_id"] == env["capture_id"]), None)
    if prior is not None:
        if _intent(prior) != _intent(env):
            raise CaptureConflict("capture_id existente con contenido distinto")
        return {"stored": False, "idempotent": True, "key": capture_key(prior), "capture": prior}
    key, data = capture_key(env), reg.canonical_json(env).encode("utf-8")
    try:
        remote_store.put_governed_decision_object(key, data)
    except remote_store.GovernedDecisionObjectExists:
        if remote_store.get_governed_decision_object(key) != data:
            raise CaptureConflict(f"clave escrita concurrentemente con otro contenido: {key}")
        return {"stored": False, "idempotent": True, "key": key, "capture": env}
    if remote_store.get_governed_decision_object(key) != data:
        raise StorageUnconfirmed(f"la captura no se confirmó por bytes: {key}")
    return {"stored": True, "idempotent": False, "key": key, "capture": env}
