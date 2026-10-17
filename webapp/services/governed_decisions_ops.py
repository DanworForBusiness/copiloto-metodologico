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
    for env in _iter_client_envelopes_strict(client):
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
        "consumers": sorted({c for c in consumers if c}),           # trazabilidad, NO amplía alcance
    }


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
        return (event.get("event"), gd.canonical_json(event.get("subject")), (event.get("evidence_ref") or {}).get("digest"), event.get("rationale"))
    return (event.get("event"), gd.canonical_json(event.get("subject")), event.get("revokes_event_id"), event.get("rationale"))


def _idempotency(hist: list[dict], request_id: str, intended_fp: tuple) -> dict | None:
    """Si existe un evento con este `request_id`: idéntico contenido lógico → lo devuelve (idempotente); distinto → conflicto."""
    for e in hist:
        if e.get("request_id") == request_id:
            if _logical_fingerprint(e) == intended_fp:
                return e
            raise IdempotencyConflict(f"request_id {request_id!r} ya usado con contenido distinto")
    return None


def _idempotency_revocation(hist: list[dict], request_id: str, subject: dict, rationale: str) -> dict | None:
    """Idempotencia A para REVOKED: el `revokes_event_id` lo fija el backend en la 1ª aplicación, así que un reintento se
    identifica por `request_id` + (es REVOKED, mismo subject, misma rationale) y HEREDA el target del evento existente. Mismo
    `request_id` reusado para otro contenido (otra rationale) o para un RATIFIED → IdempotencyConflict (fail-closed)."""
    subj = gd.canonical_json(subject)
    for e in hist:
        if e.get("request_id") == request_id:
            if (e.get("event") == gd.EVENT_REVOKED and gd.canonical_json(e.get("subject")) == subj
                    and e.get("rationale") == rationale):
                return e
            raise IdempotencyConflict(f"request_id {request_id!r} ya usado con contenido distinto")
    return None


# ─────────────────────────── operaciones ───────────────────────────

def ratify(*, client: str, identity: dict, rationale: str, actor: str, now: datetime, event_id: str, request_id: str) -> dict:
    """Ratifica el contrato de una CDS/API. Resuelve evidencia (fail-closed), aplica idempotencia A, construye el evento B1
    (con digest per-CDS + actor/event_id/decided_at del backend) y lo persiste+confirma vía B2. Re-ratificar un contrato
    vigente se PERMITE (request_id nuevo); un reintento idéntico (mismo request_id) es idempotente."""
    gd._require_exact_text(rationale, "rationale")                  # motivo obligatorio
    gd._require_exact_text(request_id, "request_id")
    resolved = resolve_cds_contract(client, identity)              # insuficiente/contradictoria → lanza
    subject = _subject(client, identity)
    decided_at = _decided_at(now)
    intended_fp = (gd.EVENT_RATIFIED, gd.canonical_json(subject), resolved["digest"], rationale)
    hist = store.load_events(subject["client"], subject["technical_id"])
    existing = _idempotency(hist, request_id, intended_fp)
    if existing is not None:
        return {"outcome": "IDEMPOTENT", "event": existing, "consumers": resolved["consumers"]}
    event = gd.build_ratification(event_id=event_id, subject=subject, rationale=rationale, actor=actor,
                                  decided_at=decided_at, evidence_ref_override=resolved["evidence_ref"], request_id=request_id)
    out = store.append_event(event)                                # create-only + confirmación por bytes (jamás éxito sin confirmar)
    return {"outcome": "RATIFIED", "event": event, "stored": out, "consumers": resolved["consumers"]}


def revoke(*, client: str, identity: dict, rationale: str, actor: str, now: datetime, event_id: str, request_id: str) -> dict:
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
    existing = _idempotency_revocation(hist, request_id, subject, rationale)
    if existing is not None:
        return {"outcome": "IDEMPOTENT", "event": existing, "revoked_event_id": existing.get("revokes_event_id")}
    status = gd.derive_status(hist, None, now)                     # el digest no hace falta para la DECISIÓN
    if status["decision"] != gd.DECISION_RATIFIED or not status.get("ratified_event_id"):
        raise RevocationNotAllowed("no hay ratificación vigente que revocar (huérfana/repetida/no-vigente)")
    target = status["ratified_event_id"]
    event = gd.build_revocation(event_id=event_id, subject=subject, revokes_event_id=target, rationale=rationale,
                                actor=actor, decided_at=decided_at, request_id=request_id)
    out = store.append_event(event)
    return {"outcome": "REVOKED", "event": event, "stored": out, "revoked_event_id": target}


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
    recon["consumers"] = consumers                               # trazabilidad (no amplía alcance)
    recon["evidence_digest"] = digest
    return recon
