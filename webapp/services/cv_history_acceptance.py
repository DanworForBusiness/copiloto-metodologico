"""cv_history_acceptance.py — ACEPTACIÓN CONTROLADA de un borrador de «Históricos sin validación» (Entrega C): el owner revisa un borrador guardado y, con una confirmación explícita, lo incorpora al Banco por el circuito ACTUAL de resolución de
excepciones. No es un segundo mecanismo de importación: es una orquestación fina sobre `cv_history_import_exec.complete_identity` (crear un perfil nuevo) y `link_existing` (vincular el CV a un perfil existente), que hacen todo el trabajo
(validación del Banco, aviso de duplicado, perfil con id determinístico, subida idempotente del CV, libro).

DOS PASOS, y ninguno es automático:
  1. `preflight` (SOLO LECTURA): con el borrador guardado y el Banco VIGENTE calcula qué se podría hacer —crear o vincular—, las coincidencias, los avisos y las confirmaciones que hay que dar. Entrega una HUELLA. Cargar o precargar un borrador
     nunca acepta nada.
  2. `confirm`: el owner elige UNA operación (crear, o vincular a UN perfil de los ofrecidos) y envía la huella y sus confirmaciones. Bajo el candado común del libro (`manifest.ledger_lock`, el mismo de la tanda de importación y de las acciones
     manuales) se recalcula todo: si cambió algo relevante, NO se escribe nada y se pide revisar de nuevo.

REGLAS DE IDENTIDAD SIN CAMBIOS: se mantiene `human_capital_service.find_duplicate` (aviso por email exacto, o por nombre si no hay email; el owner puede crear igual con confirmación explícita) y se COMPLEMENTA con las coincidencias de
`cv_reconcile` (fuertes y probables) sin tope: se muestran todas y exigen una confirmación humana; nunca se vincula solo. Una coincidencia solo por celular no es identidad. Vincular NO modifica el perfil existente (los datos del borrador no se
aplican a él) y las observaciones del borrador no se copian al perfil.

HUELLA (`fingerprint`): hash de la revisión y el contenido del borrador, el contenido del documento (sha256 del staging), el estado y la revisión del libro, el aviso de duplicado (perfil y revisión) y TODO el universo de coincidencias fuertes y
probables (perfil, revisión, fuerza, señales, baja) —no solo las cinco visibles—. Si aparece un candidato relevante nuevo o cambia (o se da de baja) uno existente, la huella cambia y la confirmación se rechaza.

IDEMPOTENCIA ESTRICTA: lo aceptado queda en el libro (`accepted`, sin datos personales: quién, cuándo, operación, perfil, revisión del borrador, hash de los datos confirmados). Repetir la MISMA decisión (operación + perfil objetivo + hash de los datos)
es un reintento: reanuda lo pendiente o, si ya terminó, devuelve éxito idempotente. Cualquier otra decisión sobre un contenido ya aceptado es un CONFLICTO, nunca un éxito aparente.

TRAZABILIDAD Y RETENCIÓN: el estado «Aceptado» se deriva SOLO del libro (no hay otra fuente de verdad y el borrador no se toca: queda inmutable y conservado). El libro identifica qué borrador (revisión) se aceptó y a qué perfil quedó asociado el CV, base de
una futura política de retención y purga de borradores aceptados (ver `cv_history_drafts.accepted_drafts`). Sin borrado automático. NO hay deshacer en esta entrega: la pantalla lo advierte antes de confirmar."""
from __future__ import annotations

import hashlib
import json
import logging

from webapp.services import cv_history_import_exec as ex, cv_history_manifest as manifest, cv_history_recoverability as rec, cv_reconcile, human_capital_service, profile_repository
from webapp.services.cv_history_candidate_index import CandidateIndex

_log = logging.getLogger(__name__)

OPS = ("create", "link")
ACK_IRREVERSIBLE, ACK_NO_EMAIL, ACK_MATCHES, ACK_DUPLICATE, ACK_WEAK_LINK = "irreversible", "no_email", "matches", "duplicate", "weak_link"
ACK_LABELS = {ACK_IRREVERSIBLE: "Entiendo que esta aceptación no se puede deshacer desde esta pantalla.",
              ACK_NO_EMAIL: "El borrador no tiene email: el perfil se crea sin email y la detección de duplicados queda solo por nombre.",
              ACK_MATCHES: "Vi las posibles coincidencias del Banco y aun así quiero crear un perfil nuevo.",
              ACK_DUPLICATE: "Ya existe un perfil con este email o nombre: quiero crear uno nuevo igual.",
              ACK_WEAK_LINK: "El perfil elegido no es una coincidencia fuerte ni probable: confirmo que el CV le pertenece."}
SIGNAL_LABEL = {"EMAIL_EXACT": "mismo email", "PHONE_MATCH": "mismo celular", "NAME_EXACT": "mismo nombre y apellido", "NAME_SWAPPED": "nombre y apellido invertidos", "NAME_TOKENS_EQUAL": "mismas palabras en el nombre",
                "INITIAL_LAST": "misma inicial y apellido", "NAME_SIMILAR": "nombre parecido"}
STRENGTH_LABEL = {"STRONG": "Coincidencia fuerte", "PROBABLE": "Coincidencia probable", "WEAK": "Coincidencia débil"}
RELEVANT = (cv_reconcile.STRONG, cv_reconcile.PROBABLE)
MAX_WEAK_SHOWN = 5
SCHEMA = 1


def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha(value) -> str:
    return hashlib.sha256(_canon(value).encode("utf-8")).hexdigest()


def _revision(profile: dict) -> int:
    return int(profile.get("revision") or 1)


def confirmed_hash(op: str, profile_id: str, fields: dict | None) -> str:
    """Hash de lo que el owner confirma: la operación, el perfil objetivo y —solo al crear— los datos de identidad. Vincular no aplica datos al perfil, así que no los incluye. No incluye la revisión del perfil objetivo: un
    reintento de la misma decisión sigue siendo el mismo aunque alguien edite luego ese perfil."""
    return _sha({"op": op, "profile_id": profile_id, "fields": fields})


# ═══════════════════ universo de coincidencias (sin tope) ═══════════════════
def _variants(fields: dict, extraction: dict) -> list[dict]:
    """La persona del borrador y, aparte, cada OTRO email que trae el documento (el borrador elige uno; los demás también pueden identificar a alguien del Banco)."""
    person = {"first_name": fields.get("first_name") or None, "last_name": fields.get("last_name") or None, "email": fields.get("email") or None, "phone": fields.get("phone") or None}
    own = cv_reconcile.norm_email(fields.get("email"))
    return [person] + [{"first_name": None, "last_name": None, "email": e, "phone": None} for e in rec._usable_emails(extraction) if e != own]


def universe(variants: list[dict], profiles: list[dict]) -> dict[str, dict]:
    """TODAS las coincidencias (cualquier fuerza) de las variantes con los perfiles —incluidos los dados de baja—, sin el tope de 5 de `reconcile_with_profiles`. Usa el índice temporal solo para acotar candidatos (equivalencia exacta
    verificada); las señales y la fuerza las calcula `cv_reconcile`."""
    index = CandidateIndex(profiles)
    found: dict[str, dict] = {}
    for person in variants:
        for profile in index.candidates(person):
            signals = cv_reconcile.signals_between(person, profile)
            level = cv_reconcile.strength(signals)
            if not level:
                continue
            slot = found.setdefault(profile["profile_id"], {"profile": profile, "strength": level, "signals": set()})
            slot["signals"] |= set(signals)
            if rec._RANK[level] < rec._RANK[slot["strength"]]:
                slot["strength"] = level
    return found


def _match_view(pid: str, m: dict) -> dict:
    p = m["profile"]
    return {"profile_id": pid, "name": f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip(), "email": p.get("email"), "revision": _revision(p), "strength": m["strength"], "signals": sorted(m["signals"]),
            "deleted": bool(p.get("deleted_at")), "relevant": m["strength"] in RELEVANT, "selectable": not p.get("deleted_at"), "phone_only": set(m["signals"]) <= {cv_reconcile.PHONE_MATCH}}      # solo celular: no es identidad


# ═══════════════════ paso 1: revisión (solo lectura) ═══════════════════
def preflight(run_id: str, key: str) -> dict:
    """Qué se puede hacer con el borrador guardado de UNA excepción, contra el Banco de este momento. No escribe nada. Lanza `ManifestNotFoundError` si el lote no existe."""
    manifest.get_run(run_id)
    ledger = manifest.get_ledger(run_id, key)
    out: dict = {"run_id": run_id, "content_key": key, "blockers": [], "ledger_state": (ledger or {}).get("state"), "accepted": (ledger or {}).get("accepted"), "draft": None, "create": None, "link": None, "matches": [],
                 "relevant_total": 0, "fingerprint": None}
    out["already_accepted"] = bool(ledger and ledger.get("state") == ex.DONE and ledger.get("accepted"))
    try:
        _run, _plan, _decision, group = ex._manual_unit(run_id, key)
    except ex.ExecutionError as exc:
        group = manifest.get_group(run_id, key)
        out["blockers"].append(str(exc))
    draft = manifest.get_draft(run_id, key)
    if not draft:
        out["blockers"].append("Guardá un borrador (al menos nombre y apellido) para poder revisarlo y aceptarlo.")
    if not group or not draft:
        return out
    fields = {name: (draft.get("fields") or {}).get(name) or "" for name in ("first_name", "last_name", "email", "phone", "notes")}
    out["draft"] = {"revision": int(draft.get("revision") or 0), "fields": fields, "updated_by": draft.get("updated_by"), "updated_at": draft.get("updated_at")}

    profiles = profile_repository.list()
    found = universe(_variants(fields, group.get("extraction") or {}), profiles)
    views = sorted((_match_view(pid, m) for pid, m in found.items()), key=lambda v: (rec._RANK[v["strength"]], -len(v["signals"]), v["name"], v["profile_id"]))
    relevant = [v for v in views if v["relevant"]]
    weak = [v for v in views if not v["relevant"]][:MAX_WEAK_SHOWN]
    out["matches"], out["relevant_total"] = relevant + weak, len(relevant)

    pid = ex.manual_profile_id(run_id, key)
    create = {"allowed": True, "blockers": [], "profile_id": pid, "fields": None, "confirmed_sha256": None, "email_missing": not fields["email"], "duplicate": None, "acks": []}
    shaped = None
    try:
        shaped = ex._manual_profile_fields(group, fields["first_name"], fields["last_name"], fields["email"], fields["phone"])
    except ValueError as exc:
        create.update(allowed=False, blockers=[str(exc)])
    if shaped is not None:
        identity = {k: shaped.get(k) for k in ("first_name", "last_name", "email", "phone")}
        create.update(fields=identity, confirmed_sha256=confirmed_hash("create", pid, identity))
        existing = profile_repository.get(pid)
        if existing is not None:
            if not profile_repository._business_fields_match(existing, shaped):
                create.update(allowed=False, blockers=["El perfil de esta aceptación ya fue creado con otros datos: no se puede volver a crear con estos."])
        else:
            duplicate = human_capital_service.find_duplicate(shaped, profiles)                            # la regla de siempre, sin cambios
            if duplicate is not None:
                create["duplicate"] = {"profile_id": duplicate["profile_id"], "name": f"{duplicate.get('first_name')} {duplicate.get('last_name')}", "email": duplicate.get("email"), "revision": _revision(duplicate)}
        create["acks"] = [ACK_IRREVERSIBLE] + ([ACK_NO_EMAIL] if create["email_missing"] else []) + ([ACK_MATCHES] if relevant else []) + ([ACK_DUPLICATE] if create["duplicate"] else [])
    out["create"] = create
    out["link"] = {"allowed": bool(out["matches"]), "acks": [ACK_IRREVERSIBLE]}
    out["fingerprint"] = _sha({"v": SCHEMA, "run": run_id, "key": key, "draft_revision": out["draft"]["revision"], "draft": _sha(fields), "staging": group.get("sha256"),
                               "ledger": [(ledger or {}).get("state"), (ledger or {}).get("revision")], "duplicate": [create["duplicate"]["profile_id"], create["duplicate"]["revision"]] if create["duplicate"] else None,
                               "universe": sorted([v["profile_id"], v["revision"], v["strength"], v["signals"], v["deleted"]] for v in relevant)})
    return out


# ═══════════════════ paso 2: confirmación ═══════════════════
def _decision(pre: dict, op: str, target_profile_id: str) -> dict | None:
    """La decisión pedida, en la forma que se compara con la aceptada (operación + perfil objetivo + hash de los datos). None si no se puede construir."""
    if op == "create" and pre["create"] and pre["create"]["fields"]:
        return {"op": "create", "profile_id": pre["create"]["profile_id"], "confirmed_sha256": pre["create"]["confirmed_sha256"]}
    if op == "link" and target_profile_id:
        return {"op": "link", "profile_id": target_profile_id, "confirmed_sha256": confirmed_hash("link", target_profile_id, None)}
    return None


def _result_from(res: dict, op: str, *, idempotent: bool = False) -> dict:
    if res.get("conflict"):
        return {"ok": False, "conflict": res["conflict"]}
    if res.get("stale"):
        return {"ok": False, "stale": True, "error": res.get("error")}
    if res.get("advisory"):
        return {"ok": False, "stale": True, "error": "Apareció un perfil con el mismo email o nombre: volvé a revisar antes de confirmar."}
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error") or "No se pudo completar la aceptación."}
    return {"ok": True, "accepted": True, "outcome": res.get("outcome"), "profile_id": res.get("profile_id"), "op": op, "idempotent": idempotent}


def confirm(run_id: str, key: str, token: str, *, actor: str, op: str, target_profile_id: str = "", target_revision=None, fingerprint: str = "", draft_revision=None, acks=()) -> dict:
    """Acepta el borrador de UNA excepción con la operación que el owner eligió. Devuelve `{ok, accepted, outcome, profile_id, ...}`, o `{ok: False, stale | conflict | missing_acks | error}`. NO escribe nada si algo cambió, si faltan
    confirmaciones o si la decisión choca con una aceptación previa. Lanza `ManifestBusyError` si otro proceso retiene el candado del contenido."""
    if op not in OPS:
        return {"ok": False, "error": "Elegí si querés crear un perfil nuevo o vincular a uno existente."}
    target_profile_id = (target_profile_id or "").strip()
    given = set(acks or ())
    with manifest.ledger_lock(run_id, key):                                                                 # MISMA exclusión que la tanda de importación y las acciones manuales
        pre = preflight(run_id, key)
        ledger = manifest.get_ledger(run_id, key)
        requested = _decision(pre, op, target_profile_id)
        resume = False
        if ex._accepted_committed(ledger):                                                                  # ya hay una aceptación con efectos: manda el libro
            prior = ledger["accepted"]
            if requested is None or not ex._same_decision(prior, requested):
                _log.info("cv_history_acceptance: run=%s key=%s op=%s resultado=CONFLICT", run_id, key, op)
                return {"ok": False, "conflict": {"reason": "ALREADY_ACCEPTED_DIFFERENTLY", "op": prior.get("op"), "profile_id": prior.get("profile_id"), "draft_revision": prior.get("draft_revision"), "by": prior.get("by"),
                                                  "at": prior.get("at"), "state": ledger.get("state")}}
            if ledger.get("state") == ex.DONE:
                _log.info("cv_history_acceptance: run=%s key=%s op=%s resultado=IDEMPOTENT", run_id, key, op)
                return {"ok": True, "accepted": True, "idempotent": True, "outcome": ex.DONE, "profile_id": prior.get("profile_id"), "op": op}
            resume = True                                                                                   # misma decisión con efectos a medio terminar: se reanuda (el Banco ya incluye lo creado, así que la huella cambió con razón)
        if not resume:
            if pre["blockers"]:
                return {"ok": False, "error": " ".join(pre["blockers"])}
            if requested is None:
                return {"ok": False, "error": "Elegí un perfil de los ofrecidos para vincular." if op == "link" else (" ".join(pre["create"]["blockers"]) or "No se puede crear con estos datos.")}
            if str(draft_revision or "").strip() != str(pre["draft"]["revision"]) or not fingerprint or fingerprint != pre["fingerprint"]:
                _log.info("cv_history_acceptance: run=%s key=%s op=%s resultado=STALE", run_id, key, op)
                return {"ok": False, "stale": True, "error": "La información cambió desde la revisión (el borrador, el Banco o este contenido). No se guardó nada: revisá de nuevo antes de confirmar.", "preflight": pre}
            required = list(pre["create"]["acks"]) if op == "create" else list(pre["link"]["acks"])
            if op == "create" and not pre["create"]["allowed"]:
                return {"ok": False, "error": " ".join(pre["create"]["blockers"])}
            if op == "link":
                offered = {m["profile_id"]: m for m in pre["matches"]}
                target = offered.get(target_profile_id)
                if target is None or not target["selectable"]:
                    return {"ok": False, "stale": True, "error": "El perfil elegido ya no es una opción (cambió o fue dado de baja). No se guardó nada: revisá de nuevo.", "preflight": pre}
                if str(target_revision or "").strip() != str(target["revision"]):
                    return {"ok": False, "stale": True, "error": "El perfil elegido cambió desde la revisión. No se guardó nada: revisá de nuevo.", "preflight": pre}
                if not target["relevant"]:
                    required.append(ACK_WEAK_LINK)
            missing = [a for a in required if a not in given]
            if missing:
                return {"ok": False, "missing_acks": missing, "error": "Faltan confirmaciones: " + " ".join(ACK_LABELS[a] for a in missing)}
        draft = pre["draft"]
        prior_record = (ledger or {}).get("accepted") if resume else None
        accepted = prior_record or {"schema": SCHEMA, "by": actor, "at": manifest.now_iso(), **requested, "draft_revision": draft["revision"], "fingerprint": pre["fingerprint"], "acks": sorted(given),
                                    "duplicate_confirmed": ACK_DUPLICATE in given, "target_revision": int(target_revision) if (op == "link" and str(target_revision or "").isdigit()) else None}
        try:
            if op == "create":
                f = draft["fields"]
                res = ex.complete_identity(run_id, key, token, actor=actor, first_name=f["first_name"], last_name=f["last_name"], email=f["email"], phone=f["phone"], duplicate_confirmed=ACK_DUPLICATE in given or resume, accepted=accepted)
            else:
                res = ex.link_existing(run_id, key, token, actor=actor, profile_id=target_profile_id, accepted=accepted, expected_target_revision=None if resume else accepted["target_revision"])
        except ex.ExecutionError as exc:
            return {"ok": False, "error": str(exc)}
        out = _result_from(res, op)
        _log.info("cv_history_acceptance: run=%s key=%s op=%s resultado=%s", run_id, key, op, ("OK:" + str(out.get("outcome"))) if out["ok"] else ("CONFLICT" if out.get("conflict") else "STALE" if out.get("stale") else "ERROR"))
        return out
