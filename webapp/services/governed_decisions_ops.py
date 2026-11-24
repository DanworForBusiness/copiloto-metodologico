"""
governed_decisions_ops.py — Capa FUNCIONAL de decisiones de gobierno (v4.B, incremento B3).

Orquesta: resolución de la evidencia contractual per-CDS (Alt 1: cross-scan de los Assessments gobernados del cliente),
ratificación, revocación y consulta técnica. La AUTORIZACIÓN (sesión del APP_OWNER + same-origin) la hace la capa de ruta
(`webapp/routers/governed_decisions.py`); acá `actor`, `event_id`, `decided_at` llegan YA impuestos por el backend y NUNCA
desde el navegador. Idempotencia A por `request_id` validado. Persistencia vía B2 (`governed_decisions_store`).

Límites: SOLO LEE la evidencia (`canonical_evidence` de los sobres gobernados) para calcular el digest; NO modifica Assessments,
NO toca `canonical-evidence/v4` ni Judgment (D8). Fail-closed ante evidencia ambigua/contradictoria/insuficiente.
Concurrencia conocida (documentada): la resolución de vigencia y la idempotencia son read-check-write; NO eliminan carreras
entre procesos (mismo límite que el check-then-PUT de B2). El APP_OWNER es un único actor humano.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from webapp.services import assessment_service as _assess          # SOLO LECTURA de sobres/evidencia (nunca escribe/ni Judgment)
from webapp.services import governed_decisions as gd
from webapp.services import governed_decisions_store as store


class GovernedOpsError(RuntimeError):
    """Base de los errores de operación (fail-closed)."""


class ContractEvidenceInsufficient(GovernedOpsError):
    """No se halló evidencia de contrato CDS/API para (cliente, identidad): identidad inexistente/insuficiente."""


class ContractEvidenceUnverifiable(GovernedOpsError):
    """Existe al menos un Assessment gobernado del cliente que NO se puede leer/validar (ilegible, estructura desconocida,
    identidad/firma alterada, o fallo de listado): no se puede GARANTIZAR consistencia completa ⇒ fail-closed (NO se trata como
    ausencia). Distinto de `ContractEvidenceInsufficient` (ausencia real) y de un perfil válido sin coincidencia (se ignora)."""


class ContractEvidenceAmbiguous(GovernedOpsError):
    """La evidencia del contrato difiere REALMENTE entre Assessments consumidores (clase/base/leaf_kind): contradictoria."""


class IdempotencyConflict(GovernedOpsError):
    """Mismo `request_id` con contenido lógico distinto (A): conflicto explícito."""


class ApplicabilityRejected(GovernedOpsError):
    """`applies_to` no acreditado: modelo inexistente en los Assessments gobernados del cliente (MODEL_NOT_FOUND) o modelo que NO consume la identidad
    ratificada (CONSUMPTION_NOT_ACCREDITED). Fail-closed: no se escribe nada."""

    def __init__(self, code: str, model: str):
        super().__init__(f"{code}: {model}")
        self.code, self.model = code, model


class RevocationNotAllowed(GovernedOpsError):
    """Revocación huérfana, repetida o dirigida a una ratificación que ya no es vigente."""


# ─────────────────────────── lectura ESTRICTA de sobres gobernados del cliente (exclusiva de B3) ───────────────────────────

def _client_slug(client: str) -> str:
    """Slug del directorio del cliente, IDÉNTICO a `assessment_service.governed_path` (reusa `_SAFE`)."""
    return _assess._SAFE.sub("_", client).strip(".") or "_"


def _iter_client_envelopes_strict(client: str):
    """Enumera y lee los sobres gobernados DEL CLIENTE de forma FAIL-CLOSED, EQUIVALENTE al inventario real
    (`assessment_service.iter_governed_envelopes`): recorre TODOS los directorios de cliente bajo
    `governed_dir()/GOVERNED_LAYOUT_VERSION` (igual que el loader global) y selecciona los sobres cuyo CAMPO `client` coincide
    `casefold`-insensible (misma semántica que el resolve previo: `env["client"].lower() == client.lower()`), NO por el nombre
    del directorio — así no omite perfiles cuando el `client` pedido difiere en mayúsculas o en caracteres no alfanuméricos del
    string con que se persistió (en un FS sensible a mayúsculas, p. ej. Linux de producción).

    Reusa el contrato de integridad del lector individual `_read_governed` (identidad + FIRMA de artefacto) — es MÁS estricto que
    el loader global (que sólo valida `dict` + `envelope_version`), nunca menos: ante un sobre de ESTE cliente ilegible, con
    estructura desconocida, o con identidad/firma alterada → LANZA `ContractEvidenceUnverifiable` (no lo omite en silencio). La
    corrupción en el directorio de OTRO cliente (nombre de dir que NO coincide con el slug de este cliente) se SALTA, igual que
    el loader real, para no bloquear esta operación. NO modifica el loader global ni el comportamiento de Assessment (D8).

    Distingue los tres casos exigidos: ausencia real (sin almacén / sin sobres de este cliente → no produce nada) · perfil válido
    sin match de identidad CDS (se entrega y el llamador no lo matchea) · perfil existente ilegible/inválido (lanza)."""
    d = _assess.governed_dir()
    if d is None:
        return                                                   # sin almacén durable gobernado → ausencia real
    base = d / _assess.GOVERNED_LAYOUT_VERSION
    if not base.is_dir():
        return                                                   # no hay assessments gobernados → ausencia real
    target_slug = _client_slug(client).casefold()
    try:
        client_dirs = sorted(p for p in base.iterdir() if p.is_dir())
    except OSError as e:
        raise ContractEvidenceUnverifiable(f"no se pudo listar el almacén de Assessments gobernados: {e}")
    for cdir in client_dirs:
        # Un sobre de ESTE cliente vive siempre bajo el slug de su propio `client`; comparamos casefold para tolerar mayúsculas.
        is_target = cdir.name.strip(".").casefold() == target_slug
        try:
            files = sorted(p for p in cdir.glob("*.json") if not p.name.startswith(".tmp"))
        except OSError as e:
            if is_target:
                raise ContractEvidenceUnverifiable(f"no se pudo listar los Assessments gobernados de {client!r}: {e}")
            continue                                             # dir de otro cliente ilegible: el loader real también lo saltaría
        for f in files:
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                if is_target:
                    raise ContractEvidenceUnverifiable(f"Assessment gobernado ilegible/corrupto ({f.name}): {e}")
                continue                                         # corrupción en dir de otro cliente → se omite (equivale al loader real)
            if not isinstance(raw, dict) or raw.get("envelope_version") != _assess.ENVELOPE_VERSION:
                if is_target:
                    raise ContractEvidenceUnverifiable(f"Assessment gobernado con estructura desconocida ({f.name})")
                continue
            if str(raw.get("client", "")).lower() != client.lower():
                continue                                         # otro cliente (dir ajeno o colisión de slug): no es evidencia de éste
            env = _assess._read_governed(f, client, raw.get("terminal_am"))   # identidad + firma (fail-closed → None)
            if env is None:
                raise ContractEvidenceUnverifiable(f"Assessment gobernado inválido/alterado ({f.name})")
            yield env


# ─────────────────────────── resolución de evidencia per-CDS (Alt 1: cross-scan) ───────────────────────────

def resolve_cds_contract(client: str, identity: dict) -> dict:
    """Cross-scan ESTRICTO de los Assessments gobernados del cliente (`_iter_client_envelopes_strict`): junta el registro de la
    hoja REMOTE_IMPORTED cuya identidad (remote_connection, remote_entity) coincide, exige CONSISTENCIA del slice contractual y
    devuelve el slice canónico + digest + evidence_ref + consumidores (trazabilidad). Fail-closed: sobre ilegible/alterado →
    `ContractEvidenceUnverifiable`; sin coincidencias → `ContractEvidenceInsufficient`; slices divergentes → `ContractEvidenceAmbiguous`.
    SOLO LECTURA."""
    conn = identity.get("remote_connection") or None
    ent = identity.get("remote_entity")
    if not isinstance(ent, str) or not ent.strip():
        raise ContractEvidenceInsufficient("remote_entity ausente")
    slices: list[dict] = []
    consumers: list[str] = []
    models: set[str] = set()
    for env in _iter_client_envelopes_strict(client):
        if env.get("terminal_am"):
            models.add(str(env["terminal_am"]))
        ce = (env.get("profile_json") or {}).get("canonical_evidence") or {}
        leaves = ((ce.get("source_contract_classes") or {}).get("leaves") or [])
        for lf in leaves:
            if not isinstance(lf, dict) or lf.get("leaf_kind") != gd.LEAF_KIND_REMOTE_IMPORTED:
                continue
            ents = [e for e in (lf.get("remote_entities") or []) if isinstance(e, str)]
            if ents == [ent] and (lf.get("remote_connection") or None) == conn:
                slices.append(gd.extract_cds_contract_evidence(lf))      # valida identidad/clase; excluye contexto
                consumers.append(env.get("terminal_am"))
    if not slices:
        raise ContractEvidenceInsufficient(f"sin evidencia de contrato REMOTE_IMPORTED para {ent!r} en el cliente {client!r}")
    canon = {gd.canonical_json(s) for s in slices}
    if len(canon) != 1:
        raise ContractEvidenceAmbiguous(f"evidencia de contrato contradictoria para {ent!r} entre consumidores: {sorted(canon)}")
    slice0 = slices[0]
    digest = hashlib.sha256(gd.canonical_json(slice0).encode("utf-8")).hexdigest()
    return {
        "slice": slice0,
        "digest": digest,
        "evidence_ref": {"algo": gd.DIGEST_ALGO, "fields": list(gd.CDS_DIGEST_FIELDS), "digest": digest},
        "consumers": sorted({c for c in consumers if c}),           # trazabilidad, NO amplía alcance (el alcance explícito es `applies_to`)
        "client_models": sorted(models),                            # modelos con Assessment gobernado legible del cliente (acreditación de `applies_to`)
    }


def accredit_applies_to(resolved: dict, applies_to: Any) -> list[str] | None:
    """Normaliza y ACREDITA `applies_to` contra la resolución estricta del contrato: cada modelo debe existir (Assessment gobernado del cliente) y CONSUMIR
    la identidad ratificada (hoja REMOTE_IMPORTED con la misma identidad en su perfil). ``None``/vacío ⇒ sin alcance (semántica histórica)."""
    if applies_to is None:
        return None
    vals = [str(a).strip() for a in applies_to if str(a).strip()] if isinstance(applies_to, (list, tuple, set, frozenset)) else None
    if not vals:
        raise gd.ValidationError("applies_to debe ser una lista NO vacía de modelos terminales")
    for a in vals:
        gd._require_exact_text(a, "applies_to[]")
        if a not in resolved["client_models"]:
            raise ApplicabilityRejected("MODEL_NOT_FOUND", a)
        if a not in resolved["consumers"]:
            raise ApplicabilityRejected("CONSUMPTION_NOT_ACCREDITED", a)
    if len(set(vals)) != len(vals):
        raise gd.ValidationError("applies_to no admite duplicados")
    return sorted(vals)


def _subject(client: str, identity: dict) -> dict:
    return {"client": client, "technical_id": gd.cds_technical_id(identity)}


def _decided_at(now: datetime) -> str:
    """ISO-8601 en UTC (nunca la zona local del servidor); fail-closed si `now` es naive."""
    if now.tzinfo is None:
        raise GovernedOpsError("now debe ser datetime aware (UTC)")
    return now.astimezone(timezone.utc).isoformat()


def _logical_fingerprint(event: dict) -> tuple:
    """Contenido LÓGICO de un evento (sin los campos impuestos por backend event_id/decided_at/actor): para idempotencia A."""
    if event.get("event") == gd.EVENT_RATIFIED:
        fp = (event.get("event"), gd.canonical_json(event.get("subject")), (event.get("evidence_ref") or {}).get("digest"), event.get("rationale"))
        return fp + (tuple(event["applies_to"]),) if event.get("applies_to") else fp    # el alcance es contenido lógico (huella histórica intacta sin él)
    return (event.get("event"), gd.canonical_json(event.get("subject")), event.get("revokes_event_id"), event.get("rationale"))


def _idempotency(hist: list[dict], request_id: str, intended_fp: tuple) -> dict | None:
    """Si existe un evento con este `request_id`: idéntico contenido lógico → lo devuelve (idempotente); distinto → conflicto."""
    for e in hist:
        if e.get("request_id") == request_id:
            if _logical_fingerprint(e) == intended_fp:
                return e
            raise IdempotencyConflict(f"request_id {request_id!r} ya usado con contenido distinto")
    return None


def _revoked_scope(e: dict, hist: list[dict]) -> tuple | None:
    """Alcance de la ratificación que revoca un REVOKED (``None`` = histórica sin alcance)."""
    t = next((x for x in hist if x.get("event_id") == e.get("revokes_event_id") and x.get("event") == gd.EVENT_RATIFIED), None)
    return tuple(t["applies_to"]) if t and t.get("applies_to") else None


def _idempotency_revocation(hist: list[dict], request_id: str, subject: dict, rationale: str, scope: tuple | None = None) -> dict | None:
    """Idempotencia A para REVOKED: el `revokes_event_id` lo fija el backend en la 1ª aplicación, así que un reintento se
    identifica por `request_id` + (es REVOKED, mismo subject, misma rationale) y HEREDA el target del evento existente. Mismo
    `request_id` reusado para otro contenido (otra rationale) o para un RATIFIED → IdempotencyConflict (fail-closed)."""
    subj = gd.canonical_json(subject)
    for e in hist:
        if e.get("request_id") == request_id:
            if (e.get("event") == gd.EVENT_REVOKED and gd.canonical_json(e.get("subject")) == subj
                    and e.get("rationale") == rationale and _revoked_scope(e, hist) == scope):
                return e
            raise IdempotencyConflict(f"request_id {request_id!r} ya usado con contenido distinto")
    return None


# ─────────────────────────── operaciones ───────────────────────────

def ratify(*, client: str, identity: dict, rationale: str, actor: str, now: datetime, event_id: str, request_id: str,
           applies_to: Any = None) -> dict:
    """Ratifica el contrato de una CDS/API. Resuelve evidencia (fail-closed), aplica idempotencia A, construye el evento B1
    (con digest per-CDS + actor/event_id/decided_at del backend) y lo persiste+confirma vía B2. Re-ratificar un contrato
    vigente se PERMITE (request_id nuevo); un reintento idéntico (mismo request_id) es idempotente."""
    gd._require_exact_text(rationale, "rationale")                  # motivo obligatorio
    gd._require_exact_text(request_id, "request_id")
    resolved = resolve_cds_contract(client, identity)              # insuficiente/contradictoria → lanza
    scope = accredit_applies_to(resolved, applies_to)              # v7: alcance de consumo acreditado (modelo existente que consume la identidad) o rechazo
    subject = _subject(client, identity)
    decided_at = _decided_at(now)
    intended_fp = (gd.EVENT_RATIFIED, gd.canonical_json(subject), resolved["digest"], rationale) + ((tuple(scope),) if scope else ())
    hist = store.load_events(subject["client"], subject["technical_id"])
    existing = _idempotency(hist, request_id, intended_fp)
    if existing is not None:
        return {"outcome": "IDEMPOTENT", "event": existing, "consumers": resolved["consumers"]}
    event = gd.build_ratification(event_id=event_id, subject=subject, rationale=rationale, actor=actor,
                                  decided_at=decided_at, evidence_ref_override=resolved["evidence_ref"], request_id=request_id, applies_to=scope)
    out = store.append_event(event)                                # create-only + confirmación por bytes (jamás éxito sin confirmar)
    return {"outcome": "RATIFIED", "event": event, "stored": out, "consumers": resolved["consumers"]}


def _scoped_target(hist: list[dict], scope: tuple, now: datetime) -> str | None:
    """Ratificación con alcance EXACTAMENTE ``scope`` que sigue vigente en al menos uno de sus carriles (la más reciente)."""
    live = {gd.derive_status(hist, None, now, lane=m).get("ratified_event_id") for m in scope}
    cands = [e for e in hist if e.get("event") == gd.EVENT_RATIFIED and tuple(e.get("applies_to") or ()) == scope and e.get("event_id") in live]
    return max(cands, key=lambda e: (gd.parse_utc(e["decided_at"]), str(e["event_id"])))["event_id"] if cands else None


def revoke(*, client: str, identity: dict, rationale: str, actor: str, now: datetime, event_id: str, request_id: str,
           applies_to: Any = None) -> dict:
    """Revoca la ratificación VIGENTE de una CDS/API. Rechaza huérfana/repetida/no-vigente. Construye REVOKED (apunta a la
    vigente) sin alterar el original; persiste+confirma vía B2. NO requiere resolver la evidencia actual (opera sobre el historial)."""
    gd._require_exact_text(rationale, "rationale")                  # motivo obligatorio (D5)
    gd._require_exact_text(request_id, "request_id")
    subject = _subject(client, identity)
    decided_at = _decided_at(now)
    hist = store.load_events(subject["client"], subject["technical_id"])
    # Idempotencia A ANTES del gate de vigencia (A aplica a REVOKED igual que a RATIFIED): un reintento idéntico de una
    # revocación ya aplicada debe devolver IDEMPOTENT, NO RevocationNotAllowed (tras la 1ª revocación el estado ya es REVOKED,
    # así que derivar vigencia primero rechazaría el reintento legítimo). El `revokes_event_id` lo fija el backend en la 1ª
    # aplicación; el reintento lo HEREDA del evento existente (no se re-deriva). Distinto contenido (otra razón, o reuso del
    # id para un RATIFIED) → IdempotencyConflict.
    # v7: la revocación es DIRIGIDA a un carril. Sin `applies_to` ⇒ SOLO la ratificación histórica (sin alcance) vigente; con `applies_to` ⇒ la
    # ratificación vigente cuyo alcance es EXACTAMENTE ese (nunca se infiere ni se amplía: revocar un carril no toca el histórico ni otros consumidores).
    scope = tuple(sorted({str(a).strip() for a in applies_to if str(a).strip()})) if applies_to else None
    existing = _idempotency_revocation(hist, request_id, subject, rationale, scope)
    if existing is not None:
        return {"outcome": "IDEMPOTENT", "event": existing, "revoked_event_id": existing.get("revokes_event_id")}
    if scope is None:
        status = gd.derive_status(hist, None, now)                 # carril histórico; el digest no hace falta para la DECISIÓN
        if status["decision"] != gd.DECISION_RATIFIED or not status.get("ratified_event_id"):
            raise RevocationNotAllowed("no hay ratificación vigente que revocar (huérfana/repetida/no-vigente)")
        target = status["ratified_event_id"]
    else:
        target = _scoped_target(hist, scope, now)
        if target is None:
            raise RevocationNotAllowed(f"no hay ratificación vigente con alcance exacto {list(scope)} que revocar")
    event = gd.build_revocation(event_id=event_id, subject=subject, revokes_event_id=target, rationale=rationale,
                                actor=actor, decided_at=decided_at, request_id=request_id)
    out = store.append_event(event)
    return {"outcome": "REVOKED", "event": event, "stored": out, "revoked_event_id": target, "applies_to": list(scope) if scope else None}


def status(client: str, identity: dict, now: datetime) -> dict:
    """Consulta técnica (SOLO LECTURA): estado + historial del contrato por replay determinista. Mantiene independientes las
    dimensiones decisión/evidencia/revisión; NO interpreta una ratificación como cumplimiento metodológico. El digest vigente
    se resuelve best-effort (si la evidencia es insuficiente/ambigua ⇒ evidencia UNKNOWN, no se rompe la consulta)."""
    subject = _subject(client, identity)
    digest = None
    consumers: list[str] = []
    try:
        resolved = resolve_cds_contract(client, identity)
        digest, consumers = resolved["digest"], resolved["consumers"]
    except (ContractEvidenceInsufficient, ContractEvidenceAmbiguous, ContractEvidenceUnverifiable):
        digest = None                                             # evidencia ausente/ambigua/ilegible → UNKNOWN (la consulta no se rompe)
    recon = store.reconstruct(subject["client"], subject["technical_id"], digest, now)
    recon["consumers"] = consumers                               # trazabilidad (no amplía alcance); `scoped_status` = carriles por consumidor declarado
    recon["summary_status"] = gd.summary_status(recon["events"], digest, now)   # presentación (ficha): sin acotadas == `status`
    recon["evidence_digest"] = digest
    return recon
