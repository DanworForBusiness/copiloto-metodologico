"""governed_update_requests.py — solicitudes de actualización del Assessment gobernado (governed-update-request/v1), PR-1.

Circuito: un Assessment AD HOC detecta una mejora frente al Assessment GOBERNADO vigente del mismo cliente + modelo terminal → la persona elige
«Solicitar actualización» → se registra la SOLICITUD (este módulo) y se notifica a Arquitectura (`NOTIFICATIONS_ARQ_RECIPIENTS`) → quien revisa abre
el enlace autenticado y, si puede aprobar (APP_OWNER o CONTENT_OWNER), calcula un DRY-RUN gobernado de solo lectura.

Qué NO hace (PR-1): no aprueba, no ejecuta ni escribe Assessments gobernados, ni Spaces de cliente. La aprobación (`hub-human-approval/v1`) y la
ejecución asegurada son de PR-2. El dry-run usa las MISMAS funciones de preview de la vía asegurada (``migrate_stale_evidence(apply=False)`` /
``plan_selective_regeneration``), que garantizan cero escrituras; además se verifica por bytes que el sobre no cambió.

Registro AUTORITATIVO (no es telemetría): un JSON por solicitud en ``config.governed_update_requests_dir()`` (persistido como los demás registros:
``remote_store.write_through``). La telemetría (``hub-activity/v2``) solo guarda identificadores que apuntan acá.

Controles:
  · NO depende del resultado ad hoc (vive 24 h y se pierde al reiniciar): la solicitud guarda un RESUMEN (niveles, contratos, Space, ids).
  · Deduplicación: una sola solicitud ABIERTA y VIGENTE por cliente + modelo; pedirla de nuevo devuelve la existente.
  · Obsolescencia: se guarda el sha256 del sobre gobernado al solicitar; si el sobre cambió, la solicitud queda OBSOLETA (no se revisa ni se aprueba).
  · Procedencia: el Space del sobre (si existe) es la ÚNICA evidencia que la verifica; el Space del ledger de publicación (``published_space``) es
    evidencia AUXILIAR y nunca autoriza por sí sola. Sin verificación inequívoca la solicitud se registra igual, pero queda marcada.
  · Nada sensible: sin perfiles, evidencia, credenciales ni tokens; solo identificadores, niveles, conteos y estados.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from core import remote_store
from webapp.services import assessment_service as svc
from webapp.services import governed_evidence

log = logging.getLogger("danwor.governed_update_requests")

SCHEMA = "governed-update-request/v1"
OPEN, OBSOLETE = "OPEN", "OBSOLETE"
NOTIFY_EVENT = "copilot.governed_update.requested"
BASE_PATH = "/copilot/governed-updates"
REVIEW_DEDUP_S = 600                                   # una revisión por persona y solicitud cada 10 min (la recarga no cuenta como otra revisión)
PREVIEW_HISTORY = 5

# Procedencia (Space): VERIFIED solo con el Space REGISTRADO en el sobre gobernado. El del ledger de publicación es AUXILIAR.
PROV_VERIFIED, PROV_AUXILIARY, PROV_CONFLICT, PROV_UNVERIFIED = "VERIFIED", "AUXILIARY_ONLY", "CONFLICT", "UNVERIFIED"
PROVENANCE_TEXT = {
    PROV_VERIFIED: "Verificada: el Space de esta evaluación coincide con el registrado en el Assessment gobernado.",
    PROV_AUXILIARY: "No verificada: el Assessment gobernado no registra Space; el registro de publicación del Catálogo coincide, "
                    "pero es evidencia auxiliar y no autoriza la actualización por sí sola.",
    PROV_CONFLICT: "En conflicto: el Space de esta evaluación difiere del registrado para el Assessment gobernado.",
    PROV_UNVERIFIED: "No verificada: no hay Space registrado para el Assessment gobernado con el que comparar.",
}
PREVIEW_READY, PREVIEW_NO_CHANGE, PREVIEW_ABSTAINED, PREVIEW_FAILED, PREVIEW_NOT_FOUND, PREVIEW_OBSOLETE = (
    "READY", "NO_CHANGE", "ABSTAINED", "FAILED", "ENTRY_NOT_FOUND", "OBSOLETE")

_lock = threading.Lock()
_previewing: set[str] = set()
_RID_RE = re.compile(r"^[0-9a-f]{32}$")
_JUDGMENT_RE = re.compile(r"(?P<from>[A-Z_]+)/(?P<from_rule>[^/:>]*?)->(?P<to>[A-Z_]+)/(?P<to_rule>[^/:>\s]*)")      # migración: ANTES->DESPUÉS
_JUDGMENT_TO_RE = re.compile(r":(?P<to>[A-Z_]+)/(?P<to_rule>[^/:>\s]*)$")                                                # selectiva: solo DESPUÉS


class NoOpportunity(Exception):
    """No hay diferencia relevante con el Assessment gobernado (o no hay sobre): no se registra nada."""


class RegistryUnavailable(Exception):
    """El registro todavía no es una copia fiel del durable (restauración desde Spaces en curso, fallida o incompleta tras un redeploy): no se lee ni
    se crea nada sobre un estado parcial (una solicitud ya existente no se vería y se DUPLICARÍA). La web responde 503 + Retry-After."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


RESTORE_WAIT_S = 15.0
RETRY_AFTER_S = 30


def _ensure_restored() -> None:
    """Toda lectura o creación del registro espera a que termine la restauración de arranque (``remote_store.await_restore``, mismo mecanismo que los
    demás repositorios del Hub) y, a diferencia de ellos, NO sigue sobre un estado incompleto: si no terminó, falló o quedó degradada,
    ``RegistryUnavailable``. Sin persistencia remota (local) no hay nada que esperar."""
    if not remote_store.enabled():
        return
    remote_store.await_restore(timeout=RESTORE_WAIT_S)
    reason = remote_store.sync_blocked_reason()
    if reason is not None:
        raise RegistryUnavailable(reason)


# ── almacenamiento ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _dir() -> Path:
    return config.governed_update_requests_dir()


def _safe(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(part or "")).strip("._") or "_"


def _path(client_dir: str, am: str, rid: str) -> Path:
    return _dir() / _safe(client_dir) / _safe(am) / f"{rid}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write(rec: dict) -> str:
    path = _path(rec["client_dir"], rec["terminal_am"], rec["request_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    return remote_store.write_through(path, json.dumps(rec, ensure_ascii=False, indent=2, sort_keys=True))


def _read(path: Path) -> dict | None:
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and rec.get("schema") == SCHEMA else None


def load(request_id: str) -> dict | None:
    if not _RID_RE.match(request_id or ""):
        return None
    _ensure_restored()
    root = _dir()
    if not root.is_dir():
        return None
    for path in root.glob(f"*/*/{request_id}.json"):
        return _read(path)
    return None


def _requests_for(client_dir: str, am: str) -> list[dict]:
    _ensure_restored()
    folder = _dir() / _safe(client_dir) / _safe(am)
    out = [r for r in (_read(p) for p in folder.glob("*.json")) if r] if folder.is_dir() else []
    return sorted(out, key=lambda r: r.get("requested_at") or "", reverse=True)


# ── estado derivado ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def envelope_sha(client_dir: str, am: str) -> str | None:
    path = svc.governed_path(client_dir, am)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path is not None and path.is_file() else None


def current_status(rec: dict) -> str:
    """OBSOLETE si el Assessment gobernado cambió desde la solicitud (o ya no existe); si no, el estado registrado."""
    if rec.get("status") == OPEN and envelope_sha(rec["client_dir"], rec["terminal_am"]) != (rec.get("governed") or {}).get("sha256"):
        return OBSOLETE
    return str(rec.get("status") or OPEN)


def find_open(client_dir: str, am: str) -> dict | None:
    """La solicitud ABIERTA y VIGENTE de este cliente + modelo, si hay (deduplicación)."""
    for rec in _requests_for(client_dir, am):
        if current_status(rec) == OPEN:
            return rec
    return None


def assess_provenance(candidate_space: str | None, governed_space: str | None, published: str | None) -> dict:
    cand = (candidate_space or "").strip() or None
    gov = (governed_space or "").strip() or None
    pub = (published or "").strip() or None
    if gov:
        status = PROV_VERIFIED if cand and cand == gov else PROV_CONFLICT
    elif pub and cand:
        status = PROV_AUXILIARY if cand == pub else PROV_CONFLICT
    else:
        status = PROV_UNVERIFIED
    return {"status": status, "verified": status == PROV_VERIFIED, "candidate_space": cand, "governed_space": gov,
            "published_space": pub, "text": PROVENANCE_TEXT[status]}


# ── autorización ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def approver_role(user: dict | None) -> str | None:
    """«APP_OWNER» o «CONTENT_OWNER» si ``user`` puede revisar/aprobar actualizaciones gobernadas; None si no. Predicado PROPIO de este circuito:
    no altera el significado de CONTENT_OWNER en Estrategia Comercial."""
    from webapp import access
    from webapp.services import auth_service
    if not user:
        return None
    if access.is_owner_user(user):
        return "APP_OWNER"
    if auth_service.is_content_owner_user(str(user.get("email") or user.get("sub") or "")):
        return "CONTENT_OWNER"
    return None


def user_email(user: dict | None) -> str:
    return str((user or {}).get("sub") or (user or {}).get("email") or "").strip().lower()


def can_view(user: dict | None, rec: dict) -> bool:
    return bool(user) and (approver_role(user) is not None or user_email(user) == rec.get("requested_by"))


# ── solicitud ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _judgment_brief(j: dict | None) -> dict:
    j = j or {}
    return {"status": j.get("status"), "level": j.get("level")}


def create(*, client_dir: str, client_display: str, terminal_am: str, space: str | None, result_id: str, candidate_judgment: dict | None,
           candidate_profile: dict | None, requested_by: str, requested_by_role: str | None = None) -> tuple[dict, bool]:
    """Registra la solicitud (o devuelve la abierta vigente). ``(registro, creada)``. ``NoOpportunity`` si no hay diferencia relevante.
    La oportunidad se RECALCULA en el servidor con el sobre de ahora (nunca se confía en lo que mandó el navegador)."""
    _ensure_restored()                                                     # nunca sobre un registro incompleto: sin restauración completa no se crea
    opp = svc.governed_update_opportunity(client_dir, terminal_am, candidate_judgment, candidate_profile, space)
    if not opp:
        raise NoOpportunity()
    env = svc._load_envelope(client_dir, terminal_am) or {}
    gov_prof = env.get("profile_json")
    if isinstance(gov_prof, str):
        try:
            gov_prof = json.loads(gov_prof)
        except ValueError:
            gov_prof = {}
    gov_scope_space = ((gov_prof or {}).get("scope") or {}).get("space")
    with _lock:
        existing = find_open(client_dir, terminal_am)
        if existing is not None:
            return existing, False
        rid = uuid.uuid4().hex
        rec = {
            "schema": SCHEMA, "request_id": rid, "status": OPEN,
            "client_dir": client_dir, "client": client_display or client_dir, "terminal_am": terminal_am,
            "requested_by": (requested_by or "").strip().lower(), "requested_by_role": requested_by_role, "requested_at": _now(),
            "source": {"result_id": result_id, "space": (space or "").strip() or None, "judgment": _judgment_brief(opp["candidato"]),
                       "source_contracts": opp["source_contracts"]["candidato"],
                       # huella de la evidencia del ad hoc (identidad + equivalencia metodológica), desde el perfil canónico; PR-2 la compara con la
                       # reconstrucción gobernada restringida al origen declarado. Solicitudes previas sin huella NO son elegibles.
                       "fingerprint": governed_evidence.fingerprint(candidate_profile) if isinstance(candidate_profile, dict) else None},
            "governed": {"sha256": envelope_sha(client_dir, terminal_am), "judgment": _judgment_brief(opp["vigente"]),
                         "source_contracts": opp["source_contracts"]["vigente"], "generated_at": env.get("generated_at")},
            "change": {"level_changed": opp["level_changed"], "contracts_changed": opp["contracts_changed"]},
            "provenance": assess_provenance(space, gov_scope_space, svc.published_space(client_dir, terminal_am)),
            "notification": None, "reviews": [], "preview": None, "previews": [],
        }
        rec["durability"] = _write(rec)
    return rec, True


def record_notification(rec: dict, dispatch: str | None) -> None:
    with _lock:
        fresh = load(rec["request_id"]) or rec
        fresh["notification"] = {"event": NOTIFY_EVENT, "key": rec["request_id"], "dispatch": dispatch, "at": _now()}
        _write(fresh)
        rec.update(fresh)


def record_review(rec: dict, user: dict, role: str, *, now: float | None = None) -> bool:
    """Registra que ``user`` (con rol de revisión) revisó la solicitud. False si ya la revisó en los últimos ``REVIEW_DEDUP_S`` (recarga)."""
    email, now = user_email(user), (time.time() if now is None else now)
    with _lock:
        fresh = load(rec["request_id"]) or rec
        for r in fresh.get("reviews") or []:
            if r.get("by") == email and now - float(r.get("ts") or 0) < REVIEW_DEDUP_S:
                return False
        fresh.setdefault("reviews", []).append({"by": email, "role": role, "at": _now(), "ts": now, "status": current_status(fresh)})
        _write(fresh)
        rec.update(fresh)
    return True


# ── dry-run gobernado (SOLO LECTURA) ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def _entries_for(client_dir: str, am: str) -> list[Any]:
    from webapp.services import catalog_reads
    _mod, entries = catalog_reads._catalog_entries()
    entries = [e for e in (entries or []) if str(getattr(e, "cliente", "")).strip().lower() == client_dir.strip().lower()]
    res = svc.resolve_entries(entries) if entries else {}
    return [e for e in entries if res.get(e.entry_id) is not None and res[e.entry_id].terminal_am == am]


def _parse_judgment(action: str | None) -> dict | None:
    nd = lambda v: None if v in (None, "ND", "None", "") else v                    # noqa: E731
    m = _JUDGMENT_RE.search(action or "")
    if m:
        return {"from": nd(m["from"]), "from_rule": nd(m["from_rule"]), "to": nd(m["to"]), "to_rule": nd(m["to_rule"]), "known_from": True}
    m = _JUDGMENT_TO_RE.search(action or "")
    if m:
        return {"from": None, "from_rule": None, "to": nd(m["to"]), "to_rule": nd(m["to_rule"]), "known_from": False}
    return None


class WriteForbidden(RuntimeError):
    """Un dry-run de PR-1 intentó persistir un Assessment gobernado: se corta (nunca debería ocurrir con ``apply=False``)."""


def _write_forbidden(*_a: Any, **_k: Any) -> Any:
    raise WriteForbidden("GOVERNED_WRITE_FORBIDDEN_IN_PREVIEW")


ELIGIBLE, NOT_ELIGIBLE = "ELIGIBLE", "NOT_ELIGIBLE"


def eligibility(rec: dict) -> dict:
    """¿Esta solicitud quedaría ELEGIBLE para aprobación (PR-2)? Solo si está ABIERTA y vigente, su procedencia está VERIFICADA y el último
    dry-run gobernado produjo un plan (READY). Explica cada motivo; no aprueba nada."""
    reasons = []
    if current_status(rec) != OPEN:
        reasons.append("la solicitud está obsoleta")
    if not (rec.get("provenance") or {}).get("verified"):
        reasons.append("la procedencia no está verificada")
    prev = rec.get("preview") or {}
    if not prev:
        reasons.append("falta calcular el dry-run gobernado")
    elif prev.get("state") != PREVIEW_READY:
        reasons.append("el dry-run gobernado no produce un plan aplicable")
    elif prev.get("governed_sha256") != (rec.get("governed") or {}).get("sha256"):
        reasons.append("el dry-run es anterior al Assessment gobernado vigente")
    return {"state": ELIGIBLE if not reasons else NOT_ELIGIBLE, "reasons": reasons}


def compute_preview(rec: dict, spaces: Any = None) -> dict:
    """Dry-run gobernado de UN modelo con la vía asegurada en modo preview. CERO escrituras (se verifica por bytes del sobre).
    ``spaces`` (PR-2): ORIGEN DECLARADO — el motor solo lee las fichas de conexión de esos Spaces (``svc.connection_spaces``) y el resultado suma, por
    separado, la identidad/equivalencia de la evidencia gobernada candidata, la acreditación de procedencia y la integridad operativa (sha del sobre +
    firma de dependencias). Sin ``spaces`` es el dry-run de PR-1 (sin restricción ni procedencia)."""
    from webapp.services import assessment_backfill as ab
    client_dir, am = rec["client_dir"], rec["terminal_am"]
    before = envelope_sha(client_dir, am)
    declared = sorted({str(x).strip() for x in spaces if str(x).strip()}) if spaces is not None else None
    base = {"computed_at": _now(), "governed_sha256": before, "spaces": declared}
    if before != (rec.get("governed") or {}).get("sha256"):
        return {**base, "state": PREVIEW_OBSOLETE, "path": None, "zero_writes": True}
    entries = _entries_for(client_dir, am)
    if not entries:
        return {**base, "state": PREVIEW_NOT_FOUND, "path": None, "zero_writes": envelope_sha(client_dir, am) == before}
    captured: dict = {}

    def _capture(cand: Any) -> Any:
        if cand is not None and getattr(cand, "profile", None) is not None:
            captured["profile"] = cand.profile
        return cand

    status = svc.status_for_entry(entries[0])
    migration = status.pending_migration or (status.stale_targeted if status.state == svc.STALE else None)
    with svc.connection_spaces(declared):                                  # None = sin restricción (PR-1); lista = SOLO esas fichas de conexión
        if migration:                                                      # preview + guardas: si algo intentara persistir, FALLA (nunca escribe)
            report = ab.migrate_stale_evidence(entries[:1], migration, apply=False, commit=_write_forbidden, retry_remote=_write_forbidden,
                                               regenerate=lambda e, m: _capture(svc.regenerate_stale_evidence(e, m)))
            path = f"MIGRATION:{migration}"
        else:
            report = ab.plan_selective_regeneration(entries[:1], [am], client=client_dir,
                                                    regenerate=lambda e, snap=None: _capture(svc.regenerate_current_evidence(e, snap)))
            path = f"SELECTIVE:{ab.SELECTIVE_MIGRATION}"
    plan = report.get("plan") or {}
    items = [i for i in plan.get("items") or [] if i.get("client") == client_dir or str(i.get("root")) == am]
    recs = report.get("records") or []
    abstentions = sorted({str(r.get("reason")) for r in recs if r.get("action") == ab.ABSTAINED})
    failures = sorted({str(r.get("reason")) for r in recs if r.get("action") == ab.FAILED_ACTION})
    judgment = next((_parse_judgment(i.get("judgment_action")) for i in items if _parse_judgment(i.get("judgment_action"))), None)
    if items:
        state = PREVIEW_READY
    elif abstentions:
        state = PREVIEW_ABSTAINED
    elif failures:
        state = PREVIEW_FAILED
    else:
        state = PREVIEW_NO_CHANGE
    out = {**base, "state": state, "path": path, "plan_digest": plan.get("digest") if items else None, "items": len(items),
           "judgment": judgment, "abstentions": abstentions, "failures": failures}
    if declared is not None:
        pool = svc.pool_root()
        profile = captured.get("profile")
        out["fingerprint"] = governed_evidence.fingerprint(profile) if profile else None
        out["provenance"] = governed_evidence.verify_provenance(
            client_dir=client_dir, am=am, declared_spaces=declared, governed_profile=profile or {},
            adhoc_fingerprint=(rec.get("source") or {}).get("fingerprint"), adhoc_space=(rec.get("source") or {}).get("space"),
            connection_records=svc.connection_records_of(client_dir), catalog_dir=svc.catalog_dir(), pool_root=pool)
        try:                                                               # integridad operativa: firma de dependencias del modelo en el pool actual
            out["dependency_signature"] = svc.closure_signature(svc.resolve_entry(entries[0]))
        except Exception:                                                  # noqa: BLE001
            out["dependency_signature"] = None
    out["zero_writes"] = envelope_sha(client_dir, am) == before
    return out


def start_preview(rec: dict, user: dict, role: str, *, run: Any = None, on_done: Any = None, spaces: Any = None) -> str:
    """Arranca el dry-run en un hilo (no depende del request HTTP). ``STARTED`` | ``ALREADY_RUNNING`` | ``OBSOLETE``."""
    rid = rec["request_id"]
    if current_status(rec) != OPEN:
        return "OBSOLETE"
    with _lock:
        if rid in _previewing:
            return "ALREADY_RUNNING"
        _previewing.add(rid)
    runner = run or compute_preview

    def worker() -> None:
        t0 = time.time()
        try:
            try:
                result = runner(rec, spaces=spaces) if spaces is not None else runner(rec)
            except Exception as exc:                                                # noqa: BLE001 — falla visible en la solicitud, nunca silenciosa
                log.warning("governed_update preview failed request=%s exc=%s", rid, type(exc).__name__)
                result = {"computed_at": _now(), "state": PREVIEW_FAILED, "failures": [type(exc).__name__], "zero_writes": None}
            result.update(by=user_email(user), role=role, duration_s=round(time.time() - t0, 1))
            try:
                with _lock:
                    fresh = load(rid) or rec
                    fresh["preview"] = result
                    fresh["previews"] = ([result] + list(fresh.get("previews") or []))[:PREVIEW_HISTORY]
                    _write(fresh)
            except RegistryUnavailable as exc:                             # no se escribe sobre un registro incompleto; el dry-run se puede recalcular
                log.warning("governed_update preview not stored request=%s reason=%s", rid, exc.reason)
                return
            if on_done:
                on_done(fresh, result)
        finally:
            with _lock:
                _previewing.discard(rid)

    threading.Thread(target=worker, name=f"governed-update-preview-{rid[:8]}", daemon=True).start()
    return "STARTED"


def preview_running(request_id: str) -> bool:
    with _lock:
        return request_id in _previewing
