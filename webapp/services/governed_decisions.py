"""
governed_decisions.py — Biblioteca PURA del registro de decisiones de gobierno `governed-decision/v1` (v4.B, incremento B1).

Plano SEPARADO (ver `_handoffs/ADR_GOVERNED_DECISION_V1.md` y `V4B_DECISIONS_TABLE.md`): decisiones humanas de gobierno
arquitectónico (en v1 solo `CONTRACT_RATIFICATION`: "el contrato de esta CDS/API está gobernado"), distinto de la autorización
de migraciones (`migration-approval/v1`, Ed25519), de la evidencia técnica (`canonical-evidence/v4`) y de la política de
Judgment (`judgment-rules/vN`). Este módulo NO integra con Judgment (D8) ni toca evidencia/Assessments.

B1 = SOLO lógica: validar esquema/eventos, serializar canónicamente, calcular el digest contractual (D7) y derivar el estado
(tres dimensiones independientes, precisión 2). **Sin I/O**: no lee archivos/red/Spaces, no usa reloj ni azar; el almacén
(B2), el CLI/servicio con auth real (B3) y la UI (B4) van en incrementos posteriores con su propio GO. El `event_id`, el
`decided_at` y el `now` se reciben por parámetro para que todo sea determinista.

Autoridad (D1): la ratificación/revocación la ejecuta el APP_OWNER autenticado server-side (B3); este módulo NO autentica,
solo da forma/validación. Append-only (D5): revocar es un evento nuevo; nunca edita/borra el `RATIFIED` original.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

SCHEMA = "governed-decision/v1"

# D3 — contrato extensible; en v1 SOLO este kind. Otros kind se rechazan explícitamente.
KIND_CONTRACT_RATIFICATION = "CONTRACT_RATIFICATION"
SUPPORTED_KINDS = frozenset({KIND_CONTRACT_RATIFICATION})

EVENT_RATIFIED = "RATIFIED"
EVENT_REVOKED = "REVOKED"
SUPPORTED_EVENTS = frozenset({EVENT_RATIFIED, EVENT_REVOKED})

DIGEST_ALGO = "sha256"
# D7 — el digest contractual cubre SOLO estas dos subsecciones de `canonical_evidence` (ver ADR §3.3).
CONTRACT_DIGEST_FIELDS = ("published_contract", "source_contract_classes.leaves")

REVIEW_DEFAULT_MONTHS = 12
REVIEW_MAX_MONTHS = 24

# Dimensiones del estado derivado (precisión 2): coexisten, no se colapsan.
DECISION_NONE, DECISION_RATIFIED, DECISION_REVOKED = "NONE", "RATIFIED", "REVOKED"
EVIDENCE_MATCH, EVIDENCE_STALE, EVIDENCE_UNKNOWN, EVIDENCE_NA = "MATCH", "STALE", "UNKNOWN", "NA"
REVIEW_CURRENT, REVIEW_DUE, REVIEW_NA = "CURRENT", "DUE", "NA"
# `principal`: etiqueta única SOLO para presentación, derivada sin perder las dimensiones.
# Prioridad: REVOKED > EVIDENCE_STALE > EVIDENCE_UNKNOWN > REVIEW_DUE > RATIFIED > NONE.
# EVIDENCE_UNKNOWN = ratificación vigente con evidencia NO verificable (no hay digest actual); NO es revocación ni vencimiento.
PRINCIPAL_NONE, PRINCIPAL_RATIFIED, PRINCIPAL_REVIEW_DUE = "NONE", "RATIFIED", "REVIEW_DUE"
PRINCIPAL_EVIDENCE_UNKNOWN, PRINCIPAL_EVIDENCE_STALE, PRINCIPAL_REVOKED = "EVIDENCE_UNKNOWN", "EVIDENCE_STALE", "REVOKED"

_WILDCARDS = ("*", "%", "?")


class GovernedDecisionError(ValueError):
    """Base de los errores de este plano (siempre ValueError: fail-closed)."""


class UnsupportedKind(GovernedDecisionError):
    """`kind` no implementado en v1 (D3)."""


class SchemaError(GovernedDecisionError):
    """El objeto no es un `governed-decision/v1` (o pertenece a otro plano)."""


class ValidationError(GovernedDecisionError):
    """Evento con forma inválida."""


# ─────────────────────────── serialización canónica + digest (D7) ───────────────────────────

def canonical_json(obj: Any) -> str:
    """JSON determinista: claves ordenadas, separadores estables, UTF-8 literal. Única forma de serializar para digerir/comparar."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _stable_key(rec: Any, *fields: str) -> tuple:
    """Clave de orden canónico por campos de identidad ESTABLES; fallback determinista (bucket 1 + json) si no es dict/faltan."""
    if isinstance(rec, dict):
        return (0,) + tuple(str(rec.get(f) or "") for f in fields)
    return (1, canonical_json(rec))


def _canonicalize_published_contract(pc: Any) -> Any:
    """Canonicaliza SOLO las listas de `published_contract` que son CONJUNTOS por clave estable (precisión 4):
      - `associations.associations`  → conjunto por (field, target)      [no semántico]
      - `fields.fields`              → conjunto por technical_name        [no semántico]
    NO reordena listas de orden semántico/ya ordenadas (`measure_type_composition.signatures` y `foreign_key_fields` ya
    vienen ordenadas por la derivación; el resto son dicts). Devuelve copia superficial canonicalizada; None/forma inesperada
    pasa tal cual. El resto (claves de dict) lo ordena `canonical_json`."""
    if not isinstance(pc, dict):
        return pc
    out = dict(pc)
    assoc = pc.get("associations")
    if isinstance(assoc, dict) and isinstance(assoc.get("associations"), list):
        a2 = dict(assoc)
        a2["associations"] = sorted(assoc["associations"], key=lambda r: _stable_key(r, "field", "target"))
        out["associations"] = a2
    flds = pc.get("fields")
    if isinstance(flds, dict) and isinstance(flds.get("fields"), list):
        f2 = dict(flds)
        f2["fields"] = sorted(flds["fields"], key=lambda r: _stable_key(r, "technical_name"))
        out["fields"] = f2
    return out


def extract_contract_evidence(canonical_evidence: dict | None) -> dict:
    """Subárbol contractual EXACTO que se ratifica (ADR §3.3). INCLUYE `published_contract` (o None explícito si la raíz no es
    un Analytical Model) y `source_contract_classes.leaves`. EXCLUYE `definition`, `by_class`, `published_field_lineage`,
    `leaf_materialization` (v4) y todo el resto del perfil. Puro: no muta la entrada. Canonicaliza los conjuntos por clave
    estable (hojas por `leaf`; asociaciones por (field,target); campos por technical_name) para que el digest sea invariante
    al ORDEN de enumeración (no a cambios de contenido)."""
    ce = canonical_evidence or {}
    published = _canonicalize_published_contract(ce.get("published_contract", None))   # None legítimo → null explícito
    scc = ce.get("source_contract_classes") or {}
    leaves = list(scc.get("leaves") or ())
    leaves_sorted = sorted(leaves, key=lambda r: _stable_key(r, "leaf"))
    return {"published_contract": published, "source_contract_classes_leaves": leaves_sorted}


def contract_evidence_digest(canonical_evidence: dict | None) -> str:
    """Digest hex sha256 del subárbol contractual canónico. INVARIANTE a la migración v3→v4 (solo agrega `leaf_materialization`)."""
    subtree = extract_contract_evidence(canonical_evidence)
    return hashlib.sha256(canonical_json(subtree).encode("utf-8")).hexdigest()


def evidence_ref(canonical_evidence: dict | None) -> dict:
    """`evidence_ref` para incrustar en un evento RATIFIED: algoritmo, campos cubiertos y digest. NO incluye el perfil."""
    return {"algo": DIGEST_ALGO, "fields": list(CONTRACT_DIGEST_FIELDS), "digest": contract_evidence_digest(canonical_evidence)}


# ─────────────────────────── utilidades de tiempo (deterministas) ───────────────────────────

def parse_utc(value: Any) -> datetime:
    """ISO-8601 → datetime UTC aware. Acepta sufijo 'Z'. Falla cerrado ante formato inválido."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("timestamp vacío o no-str")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError(f"timestamp ISO-8601 inválido: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _add_months(dt: datetime, months: int) -> datetime:
    """Suma `months` meses con recorte de día (p. ej. 31-ene +1m → 28/29-feb). Determinista."""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    # recorte de día al último válido del mes destino
    for day in range(dt.day, 0, -1):
        try:
            return dt.replace(year=year, month=month, day=day)
        except ValueError:
            continue
    raise ValidationError("fecha inválida al sumar meses")  # inalcanzable (day llega a 1)


def default_review_by(decided_at: Any) -> str:
    """`review_by` por defecto = +12 meses (ISO UTC)."""
    return _add_months(parse_utc(decided_at), REVIEW_DEFAULT_MONTHS).isoformat()


def max_review_by(decided_at: Any) -> datetime:
    """Tope de `review_by` = +24 meses."""
    return _add_months(parse_utc(decided_at), REVIEW_MAX_MONTHS)


# ─────────────────────────── validación de forma (fail-closed) ───────────────────────────

def _require_exact_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} debe ser str")
    v = value.strip()
    if not v:
        raise ValidationError(f"{field} vacío")
    if any(w in v for w in _WILDCARDS):
        raise ValidationError(f"{field} no admite comodines ({v!r}) — alcance exacto (D4)")
    return v


def _validate_subject(subject: Any) -> None:
    """D4 — alcance exacto: CLIENT + identidad técnica de la CDS/API; sin comodines ni listas abiertas."""
    if not isinstance(subject, dict):
        raise ValidationError("subject debe ser objeto {client, technical_id}")
    extra = set(subject) - {"client", "technical_id"}
    if extra:
        raise ValidationError(f"subject con claves no permitidas: {sorted(extra)}")
    _require_exact_text(subject.get("client"), "subject.client")
    _require_exact_text(subject.get("technical_id"), "subject.technical_id")


def _validate_evidence_ref(ref: Any) -> None:
    if not isinstance(ref, dict):
        raise ValidationError("evidence_ref debe ser objeto")
    if ref.get("algo") != DIGEST_ALGO:
        raise ValidationError(f"evidence_ref.algo debe ser {DIGEST_ALGO!r}")
    digest = ref.get("digest")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValidationError("evidence_ref.digest debe ser sha256 hex (64)")
    if list(ref.get("fields") or ()) != list(CONTRACT_DIGEST_FIELDS):
        raise ValidationError("evidence_ref.fields debe ser exactamente los campos contractuales (D7)")


def assert_is_governed_decision(obj: Any) -> None:
    """Rechaza objetos de OTRO plano (invariante 1). En particular una `migration-approval/v1` nunca es una decisión de gobierno."""
    if not isinstance(obj, dict):
        raise SchemaError("no es un objeto")
    if obj.get("schema") != SCHEMA:
        raise SchemaError(f"schema {obj.get('schema')!r} no es {SCHEMA!r} (plano equivocado)")
    if "plan_digest" in obj or "approval" in obj:
        raise SchemaError("el objeto parece una autorización de migración, no una decisión de gobierno")


def validate_event(event: Any) -> None:
    """Valida un evento `RATIFIED`/`REVOKED` completo. Lanza (fail-closed) ante cualquier inconsistencia; no devuelve nada."""
    assert_is_governed_decision(event)
    kind = event.get("kind")
    if kind not in SUPPORTED_KINDS:
        raise UnsupportedKind(f"kind {kind!r} no implementado en v1 (D3)")
    etype = event.get("event")
    if etype not in SUPPORTED_EVENTS:
        raise ValidationError(f"event {etype!r} no soportado")
    _require_exact_text(event.get("event_id"), "event_id")
    _validate_subject(event.get("subject"))
    _require_exact_text(event.get("actor"), "actor")               # identidad autenticada (la valida el backend en B3)
    decided_at = event.get("decided_at")
    parse_utc(decided_at)                                          # formato
    if not isinstance(event.get("rationale"), str) or not event["rationale"].strip():
        raise ValidationError("rationale obligatorio (D5 para revocación; también en ratificación)")

    if etype == EVENT_RATIFIED:
        _validate_evidence_ref(event.get("evidence_ref"))
        review_by = event.get("review_by")
        rb = parse_utc(review_by)
        da = parse_utc(decided_at)
        if rb < da:
            raise ValidationError("review_by anterior a decided_at")
        if rb > max_review_by(decided_at):
            raise ValidationError(f"review_by excede el máximo (+{REVIEW_MAX_MONTHS} meses)")
    elif etype == EVENT_REVOKED:
        _require_exact_text(event.get("revokes_event_id"), "revokes_event_id")
        if "evidence_ref" in event or "review_by" in event:
            raise ValidationError("un REVOKED no lleva evidence_ref ni review_by")


# ─────────────────────────── constructores puros (sin I/O/reloj/azar) ───────────────────────────

def build_ratification(*, event_id: str, subject: dict, canonical_evidence: dict | None, rationale: str, actor: str,
                       decided_at: str, review_by: str | None = None, traceability: dict | None = None) -> dict:
    """Arma (y valida) un evento RATIFIED. `event_id`/`decided_at` los provee el llamador (server-side, determinista acá).
    `review_by` por defecto = +12 meses. `traceability` (modelos/Data Products consumidores) es contexto, NO amplía alcance (D4)."""
    event = {
        "schema": SCHEMA,
        "event_id": event_id,
        "event": EVENT_RATIFIED,
        "kind": KIND_CONTRACT_RATIFICATION,
        "subject": dict(subject) if isinstance(subject, dict) else subject,   # tal cual lo da el llamador → validate_event rechaza claves extra/comodines
        "evidence_ref": evidence_ref(canonical_evidence),
        "rationale": rationale,
        "actor": actor,
        "decided_at": decided_at,
        "review_by": review_by or default_review_by(decided_at),
    }
    if traceability is not None:
        event["traceability"] = traceability
    validate_event(event)
    return event


def build_revocation(*, event_id: str, subject: dict, revokes_event_id: str, rationale: str, actor: str,
                     decided_at: str) -> dict:
    """Arma (y valida) un evento REVOKED (D5): motivo obligatorio; apunta al RATIFIED que revoca; nunca edita el original."""
    event = {
        "schema": SCHEMA,
        "event_id": event_id,
        "event": EVENT_REVOKED,
        "kind": KIND_CONTRACT_RATIFICATION,
        "subject": dict(subject) if isinstance(subject, dict) else subject,   # idem: validación fail-closed en validate_event
        "revokes_event_id": revokes_event_id,
        "rationale": rationale,
        "actor": actor,
        "decided_at": decided_at,
    }
    validate_event(event)
    return event


# ─────────────────────────── estado derivado (tres dimensiones + principal) ───────────────────────────

def _sorted_events(events: list[dict]) -> list[dict]:
    """Orden determinista por (decided_at, event_id)."""
    return sorted(events, key=lambda e: (parse_utc(e.get("decided_at")), str(e.get("event_id") or "")))


def derive_status(events: list[dict], current_contract_digest: str | None, now: datetime) -> dict:
    """Estado derivado de UN subject (eventos ya filtrados por subject), calculado en lectura — NUNCA se almacena (D7).

    Devuelve tres dimensiones que COEXISTEN + un `principal` solo para presentación:
      decision: NONE | RATIFIED | REVOKED
      evidence: MATCH | STALE | UNKNOWN | NA   (NA si no hay ratificación vigente; UNKNOWN si no se dispone del digest vigente)
      review:   CURRENT | DUE | NA             (DUE NO es caducidad: la ratificación sigue vigente)
      principal: REVOKED > EVIDENCE_STALE > REVIEW_DUE > RATIFIED > NONE
    `now` debe ser datetime aware (UTC)."""
    for e in events:
        validate_event(e)
    if now.tzinfo is None:
        raise ValidationError("now debe ser datetime aware (UTC)")

    ordered = _sorted_events(events)
    decision = DECISION_NONE
    active: dict | None = None            # última ratificación vigente (None si revocada o inexistente)
    for e in ordered:
        if e["event"] == EVENT_RATIFIED:
            decision, active = DECISION_RATIFIED, e
        elif e["event"] == EVENT_REVOKED:
            decision, active = DECISION_REVOKED, None

    evidence = EVIDENCE_NA
    review = REVIEW_NA
    effective_review_by = None
    ratified_event_id = None
    if decision == DECISION_RATIFIED and active is not None:
        ratified_event_id = active["event_id"]
        effective_review_by = active["review_by"]
        if current_contract_digest is None:
            evidence = EVIDENCE_UNKNOWN
        elif current_contract_digest == active["evidence_ref"]["digest"]:
            evidence = EVIDENCE_MATCH
        else:
            evidence = EVIDENCE_STALE
        review = REVIEW_DUE if now > parse_utc(active["review_by"]) else REVIEW_CURRENT

    if decision == DECISION_REVOKED:
        principal = PRINCIPAL_REVOKED
    elif decision == DECISION_NONE:
        principal = PRINCIPAL_NONE
    elif evidence == EVIDENCE_STALE:
        principal = PRINCIPAL_EVIDENCE_STALE
    elif evidence == EVIDENCE_UNKNOWN:
        principal = PRINCIPAL_EVIDENCE_UNKNOWN          # ratificación vigente con evidencia no verificable (≠ revocación/vencimiento)
    elif review == REVIEW_DUE:
        principal = PRINCIPAL_REVIEW_DUE
    else:
        principal = PRINCIPAL_RATIFIED

    return {
        "decision": decision,
        "evidence": evidence,
        "review": review,
        "principal": principal,
        "effective_review_by": effective_review_by,
        "ratified_event_id": ratified_event_id,
    }
