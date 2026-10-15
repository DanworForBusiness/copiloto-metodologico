"""migration_approval.py — autorizacion FAIL-CLOSED para aplicar migraciones sensibles de evidencia (hoy ``evidence_leaf_materialization_v4``).

Mecanismo MINIMO compatible con la arquitectura actual (no es el registro general de decisiones metodologicas v4.B): un artefacto de aprobacion ``migration-approval/v1`` FIRMADO (Ed25519) por un
aprobador, vinculado a un ``plan_digest`` concreto. Sin aprobacion valida => DENEGADO. Reglas (todas deben cumplirse; cualquier duda deniega):
  * identidad: el aprobador debe figurar en las claves publicas configuradas EXPLICITAMENTE (``DANWOR_MIGRATION_APPROVER_KEYS`` = JSON ``{"<id>": "<clave publica Ed25519 base64>"}``; vacio => nadie
    puede aprobar). ``APP_OWNER`` (o cualquier rol de la aplicacion) NO autoriza nada: no se consulta. La firma impide que el operador fabrique la aprobacion;
  * dos personas: ``operator`` (quien ejecuta, ``--operator``) debe ser el operador AUTORIZADO nombrado en la aprobacion y distinto del aprobador;
  * alcance: la migracion y la lista EXPLICITA de clientes de la aprobacion deben cubrir todo el plan (un cliente fuera de alcance deniega);
  * plan: ``plan_digest`` de la aprobacion == el del plan recalculado ahora (cualquier cambio entre el plan aprobado y la aplicacion => ``PLAN_CHANGED``);
  * vencimiento: ``expires_at`` obligatorio, futuro, y la vigencia (``expires_at - issued_at``) no puede superar ``MAX_TTL``; ``issued_at`` no puede ser futuro.
La aprobacion la emite el aprobador en SU maquina con ``issue_approval`` (clave privada que el servidor nunca ve). Stdlib + ``cryptography`` (ya dependencia via python-jose).

MODOS DE FUNCIONES (``duties_mode`` firmado en la aprobacion; ver ``_handoffs/V4_DUTIES_MODES_DESIGN.md``):
  * ``SEPARATED_DUTIES`` (por defecto; campo ausente = este modo; comportamiento SIN cambios): aprobador y operador deben ser identidades distintas. La identidad del operador es la que el aprobador designa
    (``--operator``); el CLI no la autentica (``operator_assurance = DESIGNATED_BY_APPROVER``);
  * ``OWNER_MANAGED`` (explicito y opt-in; administracion individual): aprobador == operador == UNA sola identidad, aceptado SOLO si (1) el servidor la autoriza formalmente en
    ``DANWOR_MIGRATION_OWNER_MANAGED_IDS`` (JSON, lista de ids; ausente/invalida => el modo no existe), (2) la aprobacion firmada declara ``OWNER_MANAGED`` con ``approver == authorized_operator == --operator``
    y (3) trae una ``operator_attestation`` Ed25519 de esa MISMA identidad, atada al ``plan_digest`` y a la firma de ESTA aprobacion, de vigencia <= ``MAX_ATTESTATION_TTL``, que acredita por firma (no por
    variables de entorno ni por acceso a consola) a quien ejecuta (``operator_assurance = ATTESTED_SIGNATURE``). Se conservan firma, ``plan_digest``, vencimiento y alcance. ``APP_OWNER`` no se consulta."""
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

ENV_OWNER_MANAGED_IDS = "DANWOR_MIGRATION_OWNER_MANAGED_IDS"
SEPARATED_DUTIES, OWNER_MANAGED = "SEPARATED_DUTIES", "OWNER_MANAGED"
DUTIES_MODES = (SEPARATED_DUTIES, OWNER_MANAGED)
ATTESTATION_SCHEMA = "migration-operator-attestation/v1"
MAX_ATTESTATION_TTL = timedelta(minutes=60)
ASSURANCE_DESIGNATED, ASSURANCE_ATTESTED = "DESIGNATED_BY_APPROVER", "ATTESTED_SIGNATURE"
BAD_DUTIES_MODE, OWNER_NOT_ENABLED, OWNER_ID_NOT_AUTHORIZED = "APPROVAL_BAD_DUTIES_MODE", "OWNER_MANAGED_NOT_ENABLED", "OWNER_MANAGED_IDENTITY_NOT_AUTHORIZED"
OWNER_SINGLE_IDENTITY = "OWNER_MANAGED_REQUIRES_SINGLE_IDENTITY"
ATT_MISSING, ATT_INVALID, ATT_OTHER_APPROVAL = "OPERATOR_ATTESTATION_MISSING", "OPERATOR_ATTESTATION_INVALID", "OPERATOR_ATTESTATION_FOR_OTHER_APPROVAL"
ATT_IDENTITY, ATT_EXPIRED, ATT_TTL, ATT_FUTURE = ("OPERATOR_ATTESTATION_IDENTITY_MISMATCH", "OPERATOR_ATTESTATION_EXPIRED", "OPERATOR_ATTESTATION_TTL_TOO_LONG",
                                                  "OPERATOR_ATTESTATION_ISSUED_IN_FUTURE")


@dataclass(frozen=True)
class Authorization:
    granted: bool
    reason: str
    approver: str | None = None
    operator: str | None = None
    expires_at: str | None = None
    document: Any = field(default=None, compare=False, repr=False)      # la aprobacion verificada (solo si granted): se archiva con el respaldo; NO sale en ``as_dict``
    duties_mode: str | None = None                                       # SEPARATED_DUTIES | OWNER_MANAGED (registro auditable)
    duties_collapsed: bool = False                                       # True solo en OWNER_MANAGED: aprobador y operador son la MISMA identidad (explicito, no oculto)
    operator_assurance: str | None = None                                # DESIGNATED_BY_APPROVER | ATTESTED_SIGNATURE
    attestation: dict | None = field(default=None, compare=False)        # resumen de la acreditacion (identidad, vigencia, hash de su firma); nunca la firma completa

    def as_dict(self) -> dict:
        return {"granted": self.granted, "reason": self.reason, "approver": self.approver, "operator": self.operator, "expires_at": self.expires_at, "duties_mode": self.duties_mode,
                "duties_collapsed": self.duties_collapsed, "operator_assurance": self.operator_assurance, "attestation": self.attestation}


DENIED = lambda reason, **kw: Authorization(False, reason, **kw)             # noqa: E731


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


_UNSIGNED_APPROVAL_FIELDS = ("signature", "operator_attestation")        # la acreditacion se agrega DESPUES de firmar la aprobacion y se verifica por separado


def signed_payload(approval: Mapping[str, Any]) -> dict:
    return {k: v for k, v in approval.items() if k not in _UNSIGNED_APPROVAL_FIELDS}


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
                   issued_at: datetime | None = None, ttl: timedelta = timedelta(hours=4), duties_mode: str = SEPARATED_DUTIES) -> dict:
    """Emite una aprobacion FIRMADA. Se ejecuta en la maquina del APROBADOR (la clave privada nunca llega al servidor). ``duties_mode=OWNER_MANAGED`` (opt-in) exige UNA sola identidad
    (``operator == approver``) y queda declarado en el documento firmado; ``SEPARATED_DUTIES`` (por defecto) no agrega campo alguno (documento identico al de siempre)."""
    if ttl <= timedelta(0) or ttl > MAX_TTL:
        raise ValueError(f"ttl invalido: debe estar entre 0 y {MAX_TTL}")
    if duties_mode not in DUTIES_MODES:
        raise ValueError(f"duties_mode invalido: {DUTIES_MODES}")
    if duties_mode == OWNER_MANAGED and operator != approver:
        raise ValueError("OWNER_MANAGED exige una sola identidad: --operator debe ser igual a --approver (no se crean identidades nominales distintas para la misma persona)")
    issued = issued_at or datetime.now(timezone.utc)
    doc = {"schema": APPROVAL_SCHEMA, "migration": migration, "plan_digest": plan_digest, "scope": {"clients": sorted(set(clients))}, "approver": approver, "authorized_operator": operator,
           "issued_at": _iso(issued), "expires_at": _iso(issued + ttl)}
    if duties_mode == OWNER_MANAGED:
        doc["duties_mode"] = OWNER_MANAGED
    doc["signature"] = base64.b64encode(private_key.sign(canonical(doc))).decode("ascii")
    return doc


def attest_operator(*, approval: Mapping[str, Any], identity: str, private_key: Ed25519PrivateKey, issued_at: datetime | None = None,
                    ttl: timedelta = timedelta(minutes=30)) -> dict:
    """Acredita por FIRMA a quien va a ejecutar (``OWNER_MANAGED``): firma, con la clave de ``identity``, ``{identity, migration, plan_digest, sha256(firma de la aprobacion), vigencia, purpose}``.
    Devuelve una COPIA de la aprobacion con ``operator_attestation`` agregado (el archivo que se pasa a ``--approval``). Se ejecuta en la maquina del titular de la clave, poco antes del apply."""
    if ttl <= timedelta(0) or ttl > MAX_ATTESTATION_TTL:
        raise ValueError(f"ttl de la acreditacion invalido: debe estar entre 0 y {MAX_ATTESTATION_TTL}")
    issued = issued_at or datetime.now(timezone.utc)
    att = {"schema": ATTESTATION_SCHEMA, "identity": identity, "migration": approval.get("migration"), "plan_digest": approval.get("plan_digest"),
           "approval_signature_sha256": hashlib.sha256(str(approval.get("signature", "")).encode("utf-8")).hexdigest(), "purpose": "execute",
           "issued_at": _iso(issued), "expires_at": _iso(issued + ttl)}
    att["signature"] = base64.b64encode(private_key.sign(canonical(att))).decode("ascii")
    return {**dict(approval), "operator_attestation": att}


def load_owner_managed_ids(raw: str | None = None) -> frozenset:
    """Identidades que el servidor autoriza FORMALMENTE para ``OWNER_MANAGED`` (``DANWOR_MIGRATION_OWNER_MANAGED_IDS`` = JSON lista de ids). Ausente / vacia / invalida => vacio: el modo NO existe (fail-closed)."""
    raw = os.environ.get(ENV_OWNER_MANAGED_IDS, "") if raw is None else raw
    try:
        data = json.loads(raw) if raw.strip() else []
    except (ValueError, AttributeError):
        return frozenset()
    if not isinstance(data, list) or not all(isinstance(x, str) and x.strip() for x in data):
        return frozenset()
    return frozenset(data)


def verify_attestation(att: Any, approval: Mapping[str, Any], keys: Mapping[str, Ed25519PublicKey], now: datetime) -> tuple[str | None, dict | None]:
    """(causa de denegacion | None, resumen). La acreditacion debe: existir, tener el esquema correcto, ser de la identidad aprobadora, estar firmada por su clave registrada, referirse a ESTA aprobacion y
    a ESTE plan, no estar vencida ni emitida en el futuro y durar <= ``MAX_ATTESTATION_TTL``."""
    if att is None:
        return ATT_MISSING, None
    if not isinstance(att, Mapping) or att.get("schema") != ATTESTATION_SCHEMA:
        return ATT_INVALID, None
    ident = att.get("identity")
    if ident != approval.get("approver"):
        return ATT_IDENTITY, None
    key = keys.get(ident) if isinstance(ident, str) else None
    try:
        if key is None:
            return ATT_INVALID, None
        key.verify(base64.b64decode(str(att.get("signature", ""))), canonical({k: v for k, v in att.items() if k != "signature"}))
    except (InvalidSignature, ValueError, TypeError):
        return ATT_INVALID, None
    if (att.get("migration") != approval.get("migration") or att.get("plan_digest") != approval.get("plan_digest") or att.get("purpose") != "execute"
            or att.get("approval_signature_sha256") != hashlib.sha256(str(approval.get("signature", "")).encode("utf-8")).hexdigest()):
        return ATT_OTHER_APPROVAL, None
    exp, iss = _parse(att.get("expires_at")), _parse(att.get("issued_at"))
    if exp is None or iss is None:
        return ATT_INVALID, None
    if iss > now + CLOCK_SKEW:
        return ATT_FUTURE, None
    if exp - iss > MAX_ATTESTATION_TTL:
        return ATT_TTL, None
    if exp <= now:
        return ATT_EXPIRED, None
    return None, {"identity": ident, "issued_at": att.get("issued_at"), "expires_at": att.get("expires_at"), "signature_sha256": hashlib.sha256(str(att.get("signature")).encode("utf-8")).hexdigest()}


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
                    approver_keys: Mapping[str, Ed25519PublicKey] | None = None, owner_managed_ids: Iterable[str] | None = None) -> Authorization:
    """Verifica TODAS las condiciones (ver el docstring del modulo). Devuelve ``Authorization``; cualquier fallo => ``granted=False`` con causa estable. Nunca lanza.
    ``owner_managed_ids``: identidades autorizadas formalmente para OWNER_MANAGED (None => ``DANWOR_MIGRATION_OWNER_MANAGED_IDS``; vacio => el modo no existe)."""
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
        keys[approver].verify(sig, canonical(signed_payload(approval)))
    except (InvalidSignature, ValueError, TypeError):
        return DENIED(BAD_SIGNATURE, approver=approver)
    ctx = {"approver": approver, "operator": operator, "expires_at": approval.get("expires_at")}
    if not operator or not str(operator).strip():
        return DENIED(NO_OPERATOR, **ctx)
    mode = approval.get("duties_mode", SEPARATED_DUTIES)
    if mode not in DUTIES_MODES:
        return DENIED(BAD_DUTIES_MODE, **ctx)
    ctx["duties_mode"] = mode
    if approval.get("authorized_operator") != operator:
        return DENIED(OPERATOR_MISMATCH, **ctx)
    if mode == SEPARATED_DUTIES:
        if operator == approver:
            return DENIED(SAME_PERSON, **ctx)                                       # aprobador distinto de quien ejecuta (modo por defecto, sin cambios)
    else:                                                                           # OWNER_MANAGED: opt-in formal del servidor + UNA sola identidad (la acreditacion se exige al final)
        owner_ids = load_owner_managed_ids() if owner_managed_ids is None else frozenset(owner_managed_ids)
        if not owner_ids:
            return DENIED(OWNER_NOT_ENABLED, **ctx)
        if approver not in owner_ids:
            return DENIED(OWNER_ID_NOT_AUTHORIZED, **ctx)
        if not (operator == approver == approval.get("authorized_operator")):
            return DENIED(OWNER_SINGLE_IDENTITY, **ctx)
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
    summary, assurance = None, ASSURANCE_DESIGNATED
    if mode == OWNER_MANAGED:                                                       # acreditacion de quien ejecuta, por FIRMA y atada a esta aprobacion/plan (ultima condicion)
        reason, summary = verify_attestation(approval.get("operator_attestation"), approval, keys, now)
        if reason:
            return DENIED(reason, **ctx)
        assurance = ASSURANCE_ATTESTED
    return Authorization(True, "AUTHORIZED", approver=approver, operator=operator, expires_at=approval.get("expires_at"), document=dict(approval), duties_mode=mode,
                         duties_collapsed=(mode == OWNER_MANAGED), operator_assurance=assurance, attestation=summary)


def authorizer_from_file(path: str | Path | None, operator: str | None, *, now: Callable[[], datetime] | None = None,
                         approver_keys: Mapping[str, Ed25519PublicKey] | None = None,
                         owner_managed_ids: Iterable[str] | None = None) -> Callable[[str, dict], Authorization]:
    """``authorizer(migration, plan) -> Authorization`` a partir de un archivo de aprobacion. Sin archivo, ilegible o invalido => DENEGADO (fail-closed)."""
    def authorize(migration: str, plan: dict) -> Authorization:
        if not path:
            return DENIED(NO_APPROVAL)
        try:
            approval = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return DENIED(APPROVAL_UNREADABLE)
        return verify_approval(approval, migration=migration, plan_digest=plan["digest"], plan_clients=plan["clients"], operator=operator,
                               now=now() if now else None, approver_keys=approver_keys, owner_managed_ids=owner_managed_ids)
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
    iss.add_argument("--duties-mode", choices=DUTIES_MODES, default=SEPARATED_DUTIES, help="SEPARATED_DUTIES (por defecto) | OWNER_MANAGED (opt-in: --operator debe ser igual a --approver y el servidor debe autorizarlo en "
                     + ENV_OWNER_MANAGED_IDS + ")")
    iss.add_argument("--out", required=True)
    att = sub.add_parser("attest", help="OWNER_MANAGED: acredita por firma a quien ejecuta (agrega operator_attestation a la aprobacion; correr poco antes del apply)")
    att.add_argument("--key", required=True)
    att.add_argument("--approval", required=True)
    att.add_argument("--identity", required=True)
    att.add_argument("--ttl-minutes", type=float, default=30.0)
    att.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "keygen":
        if Path(args.out).exists():
            ap.error("--out ya existe: no se sobrescribe una clave")
        key = Ed25519PrivateKey.generate()
        Path(args.out).write_text(base64.b64encode(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode("ascii"), encoding="ascii")
        print(public_key_b64(key))
        return 0
    if args.cmd == "attest":
        try:
            approval = json.loads(Path(args.approval).read_text(encoding="utf-8"))
            doc = attest_operator(approval=approval, identity=args.identity, private_key=_private_from_file(args.key), ttl=timedelta(minutes=args.ttl_minutes))
        except (OSError, ValueError) as exc:
            ap.error(str(exc))
        Path(args.out).write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
        print(f"acreditacion emitida: vence {doc['operator_attestation']['expires_at']}")
        return 0
    try:
        doc = issue_approval(migration=args.migration, plan_digest=args.plan_digest, clients=args.client, approver=args.approver, operator=args.operator,
                             private_key=_private_from_file(args.key), ttl=timedelta(hours=args.ttl_hours), duties_mode=args.duties_mode)
    except ValueError as exc:
        ap.error(str(exc))
    Path(args.out).write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
    print(f"aprobacion emitida: vence {doc['expires_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
