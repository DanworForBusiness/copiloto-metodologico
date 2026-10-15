"""migration_approval.py — autorizacion FAIL-CLOSED para aplicar migraciones sensibles de evidencia (hoy ``evidence_leaf_materialization_v4``).

Mecanismo MINIMO compatible con la arquitectura actual (no es el registro general de decisiones metodologicas v4.B): un artefacto de aprobacion ``migration-approval/v1`` FIRMADO (Ed25519) por un
aprobador, vinculado a un ``plan_digest`` concreto. Sin aprobacion valida => DENEGADO. Reglas (todas deben cumplirse; cualquier duda deniega):
  * identidad: el aprobador debe figurar en las claves publicas configuradas EXPLICITAMENTE (``DANWOR_MIGRATION_APPROVER_KEYS`` = JSON ``{"<id>": "<clave publica Ed25519 base64>"}``; vacio => nadie
    puede aprobar). ``APP_OWNER`` (o cualquier rol de la aplicacion) NO autoriza nada: no se consulta. La firma impide que el operador fabrique la aprobacion;
  * dos personas: ``operator`` (quien ejecuta, ``--operator``) debe ser el operador AUTORIZADO nombrado en la aprobacion y distinto del aprobador;
  * alcance: la migracion y la lista EXPLICITA de clientes de la aprobacion deben cubrir todo el plan (un cliente fuera de alcance deniega);
  * plan: ``plan_digest`` de la aprobacion == el del plan recalculado ahora (cualquier cambio entre el plan aprobado y la aplicacion => ``PLAN_CHANGED``);
  * vencimiento: ``expires_at`` obligatorio, futuro, y la vigencia (``expires_at - issued_at``) no puede superar ``MAX_TTL``; ``issued_at`` no puede ser futuro.
La aprobacion la emite el aprobador en SU maquina con ``issue_approval`` (clave privada que el servidor nunca ve). Stdlib + ``cryptography`` (ya dependencia via python-jose)."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

APPROVAL_SCHEMA = "migration-approval/v1"
ENV_APPROVER_KEYS = "DANWOR_MIGRATION_APPROVER_KEYS"
MAX_TTL = timedelta(hours=24)
CLOCK_SKEW = timedelta(minutes=2)

# causas estables de denegacion
NO_APPROVAL, APPROVAL_UNREADABLE, BAD_SCHEMA = "NO_APPROVAL_PROVIDED", "APPROVAL_UNREADABLE", "APPROVAL_BAD_SCHEMA"
NO_APPROVERS, UNKNOWN_APPROVER, BAD_SIGNATURE = "NO_APPROVERS_CONFIGURED", "APPROVER_NOT_AUTHORIZED", "APPROVAL_SIGNATURE_INVALID"
WRONG_MIGRATION, SCOPE_NOT_COVERED, PLAN_CHANGED = "APPROVAL_FOR_OTHER_MIGRATION", "CLIENT_OUT_OF_APPROVED_SCOPE", "PLAN_CHANGED"
NO_OPERATOR, OPERATOR_MISMATCH, SAME_PERSON = "OPERATOR_IDENTITY_REQUIRED", "OPERATOR_NOT_AUTHORIZED_BY_APPROVAL", "APPROVER_IS_OPERATOR"
EXPIRED, NO_EXPIRY, TTL_TOO_LONG, ISSUED_IN_FUTURE = "APPROVAL_EXPIRED", "APPROVAL_WITHOUT_EXPIRY", "APPROVAL_TTL_TOO_LONG", "APPROVAL_ISSUED_IN_FUTURE"
EMPTY_PLAN = "EMPTY_PLAN"


@dataclass(frozen=True)
class Authorization:
    granted: bool
    reason: str
    approver: str | None = None
    operator: str | None = None
    expires_at: str | None = None
    document: Any = field(default=None, compare=False, repr=False)      # la aprobacion verificada (solo si granted): se archiva con el respaldo; NO sale en ``as_dict``

    def as_dict(self) -> dict:
        return {"granted": self.granted, "reason": self.reason, "approver": self.approver, "operator": self.operator, "expires_at": self.expires_at}


DENIED = lambda reason, **kw: Authorization(False, reason, **kw)             # noqa: E731


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def environment_fingerprint(governed_dir: Path | None) -> str:
    """Huella del almacen gobernado de ESTE entorno (ruta resuelta): el plan, y por lo tanto la aprobacion, quedan atados al entorno donde se calculo."""
    return hashlib.sha256(str(Path(governed_dir).resolve() if governed_dir else "").encode("utf-8")).hexdigest()


def compute_plan_digest(migration: str, environment: str, items: Iterable[Mapping[str, Any]]) -> str:
    """sha256 del plan: migracion + entorno + items ordenados (cliente, AM, firma previa, firma del pool, firma del perfil candidato, accion de Juicio). Cualquier cambio de un item o de la lista cambia el digest."""
    norm = sorted(({k: it.get(k) for k in ("client", "root", "previous_artifact_signature", "pool_signature", "candidate_artifact_signature", "judgment_action")} for it in items),
                  key=lambda d: (str(d["client"]), str(d["root"])))
    return hashlib.sha256(canonical({"migration": migration, "environment": environment, "items": norm})).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: Any) -> datetime | None:
    try:
        return datetime.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def public_key_b64(private_key: Ed25519PrivateKey) -> str:
    from cryptography.hazmat.primitives import serialization
    return base64.b64encode(private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode("ascii")


def issue_approval(*, migration: str, plan_digest: str, clients: Iterable[str], approver: str, operator: str, private_key: Ed25519PrivateKey,
                   issued_at: datetime | None = None, ttl: timedelta = timedelta(hours=4)) -> dict:
    """Emite una aprobacion FIRMADA. Se ejecuta en la maquina del APROBADOR (la clave privada nunca llega al servidor)."""
    if ttl <= timedelta(0) or ttl > MAX_TTL:
        raise ValueError(f"ttl invalido: debe estar entre 0 y {MAX_TTL}")
    issued = issued_at or datetime.now(timezone.utc)
    doc = {"schema": APPROVAL_SCHEMA, "migration": migration, "plan_digest": plan_digest, "scope": {"clients": sorted(set(clients))}, "approver": approver, "authorized_operator": operator,
           "issued_at": _iso(issued), "expires_at": _iso(issued + ttl)}
    doc["signature"] = base64.b64encode(private_key.sign(canonical(doc))).decode("ascii")
    return doc


def load_approver_keys(raw: str | None = None) -> dict[str, Ed25519PublicKey]:
    """Claves publicas de los aprobadores, SOLO de la configuracion explicita. Ausente / invalida => vacio (fail-closed: nadie puede aprobar)."""
    raw = os.environ.get(ENV_APPROVER_KEYS, "") if raw is None else raw
    keys: dict[str, Ed25519PublicKey] = {}
    try:
        for ident, b64 in (json.loads(raw) if raw.strip() else {}).items():
            keys[str(ident)] = Ed25519PublicKey.from_public_bytes(base64.b64decode(b64))
    except (ValueError, TypeError, AttributeError):
        return {}
    return keys


def verify_approval(approval: Any, *, migration: str, plan_digest: str, plan_clients: Iterable[str], operator: str | None, now: datetime | None = None,
                    approver_keys: Mapping[str, Ed25519PublicKey] | None = None) -> Authorization:
    """Verifica TODAS las condiciones (ver el docstring del modulo). Devuelve ``Authorization``; cualquier fallo => ``granted=False`` con causa estable. Nunca lanza."""
    now = now or datetime.now(timezone.utc)
    keys = load_approver_keys() if approver_keys is None else dict(approver_keys)
    if not isinstance(approval, Mapping):
        return DENIED(NO_APPROVAL)
    if not keys:
        return DENIED(NO_APPROVERS)
    if approval.get("schema") != APPROVAL_SCHEMA:
        return DENIED(BAD_SCHEMA)
    approver = approval.get("approver")
    if not isinstance(approver, str) or approver not in keys:
        return DENIED(UNKNOWN_APPROVER, approver=approver if isinstance(approver, str) else None)
    try:
        sig = base64.b64decode(str(approval.get("signature", "")))
        keys[approver].verify(sig, canonical({k: v for k, v in approval.items() if k != "signature"}))
    except (InvalidSignature, ValueError, TypeError):
        return DENIED(BAD_SIGNATURE, approver=approver)
    ctx = {"approver": approver, "operator": operator, "expires_at": approval.get("expires_at")}
    if not operator or not str(operator).strip():
        return DENIED(NO_OPERATOR, **ctx)
    if approval.get("authorized_operator") != operator:
        return DENIED(OPERATOR_MISMATCH, **ctx)
    if operator == approver:
        return DENIED(SAME_PERSON, **ctx)                                           # aprobador distinto de quien ejecuta
    if approval.get("migration") != migration:
        return DENIED(WRONG_MIGRATION, **ctx)
    exp, iss = _parse(approval.get("expires_at")), _parse(approval.get("issued_at"))
    if exp is None or iss is None:
        return DENIED(NO_EXPIRY, **ctx)
    if iss > now + CLOCK_SKEW:
        return DENIED(ISSUED_IN_FUTURE, **ctx)
    if exp - iss > MAX_TTL:
        return DENIED(TTL_TOO_LONG, **ctx)
    if exp <= now:
        return DENIED(EXPIRED, **ctx)
    scope = (approval.get("scope") or {}).get("clients")
    clients = sorted(set(plan_clients))
    if not clients:
        return DENIED(EMPTY_PLAN, **ctx)
    if not isinstance(scope, list) or not set(clients) <= set(map(str, scope)):
        return DENIED(SCOPE_NOT_COVERED, **ctx)
    if approval.get("plan_digest") != plan_digest:
        return DENIED(PLAN_CHANGED, **ctx)                                           # el plan de ahora NO es el aprobado
    return Authorization(True, "AUTHORIZED", approver=approver, operator=operator, expires_at=approval.get("expires_at"), document=dict(approval))


def authorizer_from_file(path: str | Path | None, operator: str | None, *, now: Callable[[], datetime] | None = None,
                         approver_keys: Mapping[str, Ed25519PublicKey] | None = None) -> Callable[[str, dict], Authorization]:
    """``authorizer(migration, plan) -> Authorization`` a partir de un archivo de aprobacion. Sin archivo, ilegible o invalido => DENEGADO (fail-closed)."""
    def authorize(migration: str, plan: dict) -> Authorization:
        if not path:
            return DENIED(NO_APPROVAL)
        try:
            approval = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return DENIED(APPROVAL_UNREADABLE)
        return verify_approval(approval, migration=migration, plan_digest=plan["digest"], plan_clients=plan["clients"], operator=operator,
                               now=now() if now else None, approver_keys=approver_keys)
    return authorize


def deny_all(migration: str, plan: dict) -> Authorization:
    """Autorizador por defecto de una migracion sensible: DENIEGA siempre."""
    return DENIED(NO_APPROVAL)


# ═══════════════════════════ CLI del APROBADOR (se ejecuta en SU maquina, no en el servidor) ═══════════════════════════
def _private_from_file(path: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(Path(path).read_text(encoding="ascii").strip()))


def main(argv: list[str] | None = None) -> int:
    """``keygen --out k.key`` (imprime la clave PUBLICA para ``DANWOR_MIGRATION_APPROVER_KEYS``) | ``issue --key k.key --migration M --plan-digest D --client C... --approver A --operator O --out aprobacion.json``."""
    import argparse
    from cryptography.hazmat.primitives import serialization
    ap = argparse.ArgumentParser(description="Aprobaciones firmadas de migraciones (lado del aprobador)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    kg = sub.add_parser("keygen")
    kg.add_argument("--out", required=True)
    iss = sub.add_parser("issue")
    iss.add_argument("--key", required=True)
    iss.add_argument("--migration", required=True)
    iss.add_argument("--plan-digest", required=True)
    iss.add_argument("--client", action="append", required=True)
    iss.add_argument("--approver", required=True)
    iss.add_argument("--operator", required=True)
    iss.add_argument("--ttl-hours", type=float, default=4.0)
    iss.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "keygen":
        if Path(args.out).exists():
            ap.error("--out ya existe: no se sobrescribe una clave")
        key = Ed25519PrivateKey.generate()
        Path(args.out).write_text(base64.b64encode(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode("ascii"), encoding="ascii")
        print(public_key_b64(key))
        return 0
    doc = issue_approval(migration=args.migration, plan_digest=args.plan_digest, clients=args.client, approver=args.approver, operator=args.operator,
                         private_key=_private_from_file(args.key), ttl=timedelta(hours=args.ttl_hours))
    Path(args.out).write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
    print(f"aprobacion emitida: vence {doc['expires_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
