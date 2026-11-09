"""
review_resolution_ops.py — Operaciones gobernadas de resolución de revisiones (Iteración II, kind `REVIEW_RESOLUTION`).

Capa FUNCIONAL que une: identidad + huella (`review_evidence`), contrato puro (`governed_decisions`), almacén append-only
reutilizado (`governed_decisions_store`) y derivación de estado (`review_resolutions`). NO entra al motor del Juicio ni modifica
scoring/evidencia técnica (D8). El I/O ocurre SOLO a través del `store` inyectado (las pruebas pasan un almacén simulado y
NUNCA escriben en Spaces real).

Reglas duras:
  * Nunca se resuelve "por defecto": si la huella material vigente no es verificable (sidecar ausente/desfasado o sin entrada),
    se rechaza (`FootprintUnavailable`).
  * AUTORIZACIÓN EXPLÍCITA (frontera): toda operación exige un `AuthContext` (no hay default autorizado). El `actor` se DERIVA del
    contexto autenticado — un llamador no confiable no puede fijarlo por fuera de la identidad. La política es inyectable y
    extensible a consultores; en Iteración III la sesión/same-origin HTTP poblarán el contexto sin cambiar estas invariantes.
  * ESTADO REAL del subject: resolver/revocar/reconciliar VALIDAN el estado vigente del subject antes de aceptar (revoca/supera
    eventos existentes; base vigente; reconciliación que cubre todas las ramas). Es DETECCIÓN, no exclusión atómica (Spaces no la
    ofrece): entre la lectura y el append otro escritor podría intervenir ⇒ la bifurcación se detecta al derivar (CONTESTED).
  * `log_activity` (si se pasa `audit`) es COMPLEMENTARIO: el evento gobernado es la fuente autoritativa.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tools.catalog.modeling_assessment import review_evidence as rev
from webapp.services import governed_decisions as gd
from webapp.services import governed_decisions_store as store_mod
from webapp.services import review_resolutions as rr


class ReviewResolutionError(RuntimeError):
    """Base de los errores de resolución de revisiones (fail-closed)."""


class FootprintUnavailable(ReviewResolutionError):
    """No hay huella material VERIFICABLE para la revisión ⇒ no se resuelve por defecto."""


class AuthorizationDenied(ReviewResolutionError):
    """La política de autorización rechazó la escritura (o faltó el contexto de autorización explícito)."""


class SubjectStateError(ReviewResolutionError):
    """La operación no es coherente con el estado REAL del subject (revoca/supera algo inexistente o no vigente, base
    desfasada, o reconciliación que no cubre todas las ramas vigentes)."""


# ─────────────────────────── autorización EXPLÍCITA (frontera) ───────────────────────────

@dataclass(frozen=True)
class AuthContext:
    """Contexto de autorización EXPLÍCITO y obligatorio. `actor` es la identidad AUTENTICADA (en Iteración III proviene de la
    sesión server-side, NO del cuerpo del request); `is_owner`/`same_origin` los resuelve la capa HTTP. El contrato ya exige
    pasarlo y deriva el actor de él: un llamador no confiable no puede establecer el actor arbitrariamente."""
    actor: str
    is_owner: bool
    same_origin: bool


def authorize_resolution(auth: AuthContext) -> None:
    """Política MVP (fail-closed): se exige un `AuthContext` explícito, con actor autenticado, owner y same-origin. Inyectable:
    una política futura puede habilitar consultores por klass/disposición SIN cambiar la firma ni las invariantes."""
    if not isinstance(auth, AuthContext):
        raise AuthorizationDenied("se requiere un AuthContext explícito: no hay autorización por defecto")
    if not isinstance(auth.actor, str) or not auth.actor.strip():
        raise AuthorizationDenied("actor autenticado ausente")
    if not auth.is_owner or not auth.same_origin:
        raise AuthorizationDenied("MVP owner-only + same-origin")


# ─────────────────────────── validación del estado real del subject ───────────────────────────

def _subject_events(client: str, technical_id: str, store) -> list[dict]:
    return store.load_events(client, technical_id)


def _live_head_ids(events: list[dict]) -> list[str]:
    live, _ = rr._live_ratifications(events)
    return [e["event_id"] for e in live]


def _validate_resolution_preconditions(events: list[dict], *, base_event_id, supersedes_event_ids) -> None:
    """Valida la operación contra el estado REAL del subject (detección, no exclusión atómica)."""
    all_ids = {e["event_id"] for e in events}
    live = set(_live_head_ids(events))
    if supersedes_event_ids is not None:                 # reconciliación
        missing = [s for s in supersedes_event_ids if s not in all_ids]
        if missing:
            raise SubjectStateError(f"la reconciliación referencia eventos inexistentes del subject: {missing}")
        uncovered = sorted(live - set(supersedes_event_ids))
        if uncovered:
            raise SubjectStateError(f"reconciliación incompleta: ramas vigentes no referenciadas: {uncovered}")
    elif base_event_id is not None and base_event_id not in live:
        raise SubjectStateError(f"base_event_id desfasado: {base_event_id!r} no es cabeza vigente {sorted(live)}")


# ─────────────────────────── escritura (resolver / reconciliar / revocar) ───────────────────────────

def resolve_review(*, client: str, identity: dict, disposition: str, rationale: str, decided_at: str, event_id: str,
                   sidecar, profile, auth: AuthContext, review_by: str | None = None, base_event_id: str | None = None,
                   supersedes_event_ids: list | None = None, request_id: str | None = None, traceability: dict | None = None,
                   authorize=authorize_resolution, store=store_mod, audit=None) -> dict:
    """Registra una resolución (RATIFIED/REVIEW_RESOLUTION). Exige huella vigente verificable (`FootprintUnavailable` si no),
    autorización explícita (`auth`, actor derivado de ella) y coherencia con el estado real del subject. `base_event_id` = cabeza
    vista (concurrencia); `supersedes_event_ids` = reconciliación (append-only). `event_id`/`decided_at` los impone el backend."""
    authorize(auth)
    technical_id = rev.review_technical_id(identity)
    subject = {"client": client, "technical_id": technical_id}
    digest = rev.current_footprint_digest(technical_id, sidecar, profile)
    if digest is None:
        raise FootprintUnavailable("huella material no verificable (sidecar ausente/desfasado o sin entrada): no se resuelve por defecto")
    _validate_resolution_preconditions(_subject_events(client, technical_id, store), base_event_id=base_event_id,
                                       supersedes_event_ids=supersedes_event_ids)
    event = gd.build_review_resolution(
        event_id=event_id, subject=subject, klass=identity.get("klass"), disposition=disposition, digest=digest,
        rationale=rationale, actor=auth.actor, decided_at=decided_at, review_by=review_by, base_event_id=base_event_id,
        supersedes_event_ids=supersedes_event_ids, traceability=traceability, request_id=request_id)
    result = store.append_event(event)
    if audit is not None:
        audit(event, result)                       # complementario; NO sustituye al evento gobernado
    return {"event": event, "store": result, "subject": subject}


def reconcile_review(*, client: str, identity: dict, disposition: str, rationale: str, decided_at: str, event_id: str,
                     sidecar, profile, auth: AuthContext, supersedes_event_ids: list, review_by: str | None = None,
                     request_id: str | None = None, authorize=authorize_resolution, store=store_mod, audit=None) -> dict:
    """Reconcilia una bifurcación (CONTESTED): resolución nueva que SUPERA todas las ramas vigentes (append-only). Valida que
    `supersedes_event_ids` existan y cubran TODAS las cabezas vigentes del subject."""
    if not supersedes_event_ids:
        raise ReviewResolutionError("reconciliación sin supersedes_event_ids")
    return resolve_review(client=client, identity=identity, disposition=disposition, rationale=rationale, decided_at=decided_at,
                          event_id=event_id, sidecar=sidecar, profile=profile, auth=auth, review_by=review_by,
                          supersedes_event_ids=list(supersedes_event_ids), request_id=request_id, authorize=authorize,
                          store=store, audit=audit)


def revoke_review(*, client: str, technical_id: str, revokes_event_id: str, rationale: str, decided_at: str, event_id: str,
                  auth: AuthContext, base_event_id: str | None = None, request_id: str | None = None,
                  authorize=authorize_resolution, store=store_mod, audit=None) -> dict:
    """Revoca una resolución (REVOKED/REVIEW_RESOLUTION, append-only: nunca edita el original). Valida que `revokes_event_id`
    sea una ratificación VIGENTE del subject (estado real) antes de aceptar."""
    authorize(auth)
    subject = {"client": client, "technical_id": technical_id}
    live = set(_live_head_ids(_subject_events(client, technical_id, store)))
    if revokes_event_id not in live:
        raise SubjectStateError(f"revoca una ratificación no vigente del subject: {revokes_event_id!r} (vigentes {sorted(live)})")
    event = gd.build_review_revocation(event_id=event_id, subject=subject, revokes_event_id=revokes_event_id,
                                       rationale=rationale, actor=auth.actor, decided_at=decided_at, base_event_id=base_event_id,
                                       request_id=request_id)
    result = store.append_event(event)
    if audit is not None:
        audit(event, result)
    return {"event": event, "store": result, "subject": subject}


# ─────────────────────────── lectura (estado derivado + cabezas vigentes) ───────────────────────────

def review_status(*, client: str, technical_id: str, sidecar, profile, now: datetime, presence: str | None = None,
                  store=store_mod) -> dict:
    """Estado derivado de UN subject de revisión (5 planos + CONTESTED). La huella vigente sale del sidecar ligado al perfil;
    si no liga ⇒ evidence=UNKNOWN."""
    events = store.load_events(client, technical_id)
    digest = rev.current_footprint_digest(technical_id, sidecar, profile)
    return rr.derive_review_status(events, current_footprint_digest=digest, now=now, presence=presence)


def subject_live_heads(*, client: str, technical_id: str, store=store_mod) -> list[str]:
    """IDs de las ratificaciones VIGENTES del subject (para que el llamador fije `base_event_id` / detecte bifurcación)."""
    return _live_head_ids(store.load_events(client, technical_id))
