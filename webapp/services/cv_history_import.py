"""cv_history_import.py — IMPORTACIÓN MASIVA CONTROLADA del histórico de CVs de SharePoint al Banco de CCHH. Incremento I3.1: ANÁLISIS COMPLETO y PLAN. NO escribe nada en el Banco ni en SharePoint.

Reutiliza lo que ya existe, sin un framework de migración: inventario (`cv_history_inventory`), agrupación por contenido y alias por sha256 (`cv_history_pilot.build_groups`/`_process_one`), extractor v3 (`cv_extract`),
conciliación (`cv_reconcile`) y el manifiesto protegido (`cv_history_manifest`). Un lote de tipo `import` toma TODOS los contenidos únicos del origen (no una muestra) y se analiza por tandas reanudables.

DOS FASES. (1) ANALIZAR: descarga cada contenido una sola vez (solo lectura), extrae y concilia, y deja el registro por contenido en el área protegida. (2) PLAN: `build_plan` —función PURA— decide qué haría la importación con
cada contenido; se guarda en `import-plan.json` SIN datos personales. La ejecución del plan (crear/vincular perfiles y subir los CV) es el incremento I3.2.

DECISIÓN POR CONTENIDO (sin aprobación documento por documento para los casos confiables; nunca un nombre ficticio: el contrato del Banco exige nombre y apellido y no se toca):
  * CREATE   — alta de un perfil: identidad CORROBORADA por email fuerte (el usuario del email coincide con el nombre propuesto), un único email no genérico, sin banderas de conflicto/borde/varios emails y sin perfil
               existente con ese email ni por nombre. Los contenidos con el mismo email se agrupan en UN perfil (varias versiones/especialidades de la misma persona);
  * ATTACH   — otro contenido del mismo email de un grupo CREATE (su nombre, si lo hay, no contradice al del grupo);
  * LINK     — el email coincide EXACTAMENTE con UN perfil vigente del Banco y la identidad se sostiene (el nombre propuesto comparte una palabra con el perfil, o el email corrobora el nombre del perfil): se adjuntaría
               el CV a ese perfil sin modificar ninguno de sus campos;
  * PENDING  — todo lo demás, con motivos explícitos: sin email, varios emails, email genérico, identidades contradictorias para un mismo email, nombre sin corroboración/declarado/en conflicto, token de borde sin explicar,
               perfil existente dado de baja o duplicado, coincidencia de email no corroborada, posible duplicado por nombre. Quedan para completar; no se fusionan ni se crean solos;
  * DUPLICATE — contenido idéntico a otro (alias por sha256): sus especialidades se suman al principal;
  * ERROR    — no se pudo descargar o el formato no es válido.

EMAIL COMO IDENTIDAD: solo cuando es fuerte y no ambiguo. Un buzón de rol («info», «ventas»…), varios emails en el documento o identidades contradictorias entre los contenidos que lo comparten NO se usan para vincular
ni para agrupar.

PROTECCIÓN DE DATOS: el índice (nombres de archivo) y los registros por contenido viven en el área protegida y se eliminan con el descarte del lote; el plan y sus conteos no llevan datos personales y se conservan."""
from __future__ import annotations

import secrets
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations

from webapp.services import cv_extract, cv_history_inventory as inventory, cv_history_manifest as manifest, cv_history_pilot as pilot, cv_reconcile, profile_repository

CREATE, ATTACH, LINK, PENDING, DUPLICATE, ERROR = "CREATE", "ATTACH", "LINK", "PENDING", "DUPLICATE", "ERROR"
ACTIONS = (CREATE, ATTACH, LINK, PENDING, DUPLICATE, ERROR)

READ_FAILURE, NO_EMAIL, MULTIPLE_EMAILS, GENERIC_EMAIL, EMAIL_CONFLICT = "READ_FAILURE", "NO_EMAIL", "MULTIPLE_EMAILS", "GENERIC_EMAIL", "EMAIL_CONFLICT"
NO_RELIABLE_NAME, NAME_CONFLICT, NAME_UNCORROBORATED, NAME_DECLARED_ONLY, NAME_EDGE_TOKEN, NAME_NOT_EMAIL_CORROBORATED = (
    "NO_RELIABLE_NAME", "NAME_CONFLICT", "NAME_UNCORROBORATED", "NAME_DECLARED_ONLY", "NAME_EDGE_TOKEN", "NAME_NOT_EMAIL_CORROBORATED")
EXISTING_NAME_CONFLICT, EXISTING_DELETED, MULTIPLE_EXISTING_MATCH, EMAIL_MATCH_UNCORROBORATED, POSSIBLE_DUPLICATE, EMAIL_NAME_MISMATCH, NOT_ANALYZED = (
    "EXISTING_NAME_CONFLICT", "EXISTING_DELETED", "MULTIPLE_EXISTING_MATCH", "EMAIL_MATCH_UNCORROBORATED", "POSSIBLE_DUPLICATE", "EMAIL_NAME_MISMATCH", "NOT_ANALYZED")

# Banderas del nombre que impiden el alta automática (`NAME_ORDER_UNCERTAIN` y `NAME_SPLIT_AMBIGUOUS` no la impiden: el email ya corrobora; quedan como ADVERTENCIA para revisar).
_NAME_BLOCKERS = {cv_extract.NAME_CONFLICT, cv_extract.NAME_PARTIAL_MATCH, cv_extract.NAME_UNCORROBORATED, cv_extract.NAME_EDGE_TOKEN_UNEXPLAINED, cv_extract.NAME_MISSING, cv_extract.NAME_SINGLE_SOURCE}
_WARNING_FLAGS = (cv_extract.NAME_ORDER_UNCERTAIN, cv_extract.NAME_SPLIT_AMBIGUOUS)
BATCH_MAX_DOCUMENTS = 200
# Un candidato de nombre «reclama» una identidad cuando tiene un ancla (texto y archivo que coinciden, o línea con etiqueta) aunque no esté corroborado por el email: sirve para detectar CONTRADICCIONES,
# no para dar de alta. Un candidato estructural suelto (la primera frase del documento) no cuenta.
_CLAIMING_STATES = {"UNANCHORED_AGREEMENT", "DECLARED", "CORROBORATED"}


# ═══════════════════ creación del lote ═══════════════════
def start_import(token: str, share_url: str, *, actor: str, authorization: str, clock=time.monotonic, sleep=time.sleep) -> dict:
    """Crea el lote de importación: inventario de SOLO LECTURA, agrupación por contenido y un índice con TODOS los contenidos compatibles. No descarga ningún documento y no escribe en el Banco ni en SharePoint. Un
    inventario incompleto se rechaza: importar sobre un origen a medio recorrer dejaría contenidos fuera sin que nadie lo note."""
    if (authorization or "").strip() != pilot.AUTHORIZATION_PHRASE:
        raise pilot.PilotInputError("Falta la confirmación explícita del owner para procesar datos personales.")
    items: list[dict] = []
    report = inventory.run_inventory(token, share_url, clock=clock, sleep=sleep, items_out=items)
    if not report.get("ok"):
        raise pilot.PilotInputError("No se pudo resolver el link a una carpeta con tus permisos actuales.")
    if not report.get("complete"):
        raise pilot.PilotInputError("El inventario quedó incompleto (accesos denegados, límite de tiempo o de elementos). Revisá el inventario antes de importar.")
    groups = pilot.build_groups(items)
    if not groups:
        raise pilot.PilotInputError("No hay documentos compatibles para importar.")
    run_id = manifest.new_run_id(suffix=secrets.token_hex(2))
    until = (datetime.now(timezone.utc) + timedelta(days=manifest.STAGING_RETENTION_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest.create_run(run_id, {
        "staging": {"state": "PRESENT", "retention_days": manifest.STAGING_RETENTION_DAYS, "retention_until": until, "auto_delete": False,
                    "policy": "El índice y los registros por contenido (nombre, email y celular extraídos) se conservan en un área temporal protegida hasta la fecha de vigencia como máximo y se eliminan por acción explícita del owner; el plan y la auditoría (sin datos personales) se conservan."},
        "kind": pilot.KIND_IMPORT, "state": "INDEXING", "created_by": actor, "source": report["root"],
        "authorization": {"phrase": pilot.AUTHORIZATION_PHRASE, "confirmed_by": actor, "confirmed_at": manifest.now_iso()},
        "params": {"contents": len(groups)}, "population": pilot._population(items, groups, report),
        "counters": {"selected": len(groups), "extracted": 0, "failed": 0, "alias": 0}})
    manifest.put_index(run_id, {"groups": groups})
    return manifest.update_run(run_id, lambda d: d.update({"state": "CREATED"}))


def analyze(run_id: str, token: str, *, max_documents: int | None = None, **kw) -> dict:
    """Una tanda de ANÁLISIS (descarga de solo lectura + extracción + conciliación). Reanudable e idempotente; `max_documents` acota la tanda (1–`BATCH_MAX_DOCUMENTS`)."""
    if manifest.get_run(run_id).get("kind") != pilot.KIND_IMPORT:
        raise pilot.PilotInputError("No es un lote de importación.")
    if max_documents is not None:
        max_documents = max(1, min(int(max_documents), BATCH_MAX_DOCUMENTS))
    return pilot.process_run(run_id, token, max_documents=max_documents, **kw)


def generate_plan(run_id: str, *, actor: str) -> dict:
    """Genera (o regenera) el PLAN con el estado actual del Banco. Exige el análisis completo. Solo escribe en el manifiesto: ni el Banco ni SharePoint."""
    run = manifest.get_run(run_id)
    if run.get("kind") != pilot.KIND_IMPORT:
        raise pilot.PilotInputError("No es un lote de importación.")
    if (run.get("staging") or {}).get("state") != "PRESENT":
        raise pilot.PilotInputError("Los datos personales del lote ya no están disponibles: no se puede generar un plan.")
    pending = pilot._pending(run_id)
    if pending:
        raise pilot.PilotInputError(f"Faltan {pending} contenidos por analizar: terminá el análisis antes de generar el plan.")
    try:
        profiles = profile_repository.list()
    except profile_repository.ProfileStorageError:
        raise pilot.PilotInputError("No se pudo leer el Banco para conciliar.") from None
    plan = build_plan(list(pilot._import_groups(run_id).values()), profiles)
    versions = Counter(str((g.get("extraction") or {}).get("extractor_version", 1)) for g in pilot._import_groups(run_id).values() if g["state"] == pilot.EXTRACTED and not (g.get("extraction") or {}).get("failure"))
    doc = manifest.put_plan(run_id, {"generated_at": manifest.now_iso(), "generated_by": actor, "extractor_versions": dict(sorted(versions.items())), "banco_profiles": len(profiles),
                                     "summary": plan["summary"], "decisions": plan["decisions"], "clusters": plan["clusters"]})
    manifest.update_run(run_id, lambda d: d.update({"plan": {"generated_at": doc["generated_at"], "revision": doc["revision"], "summary": plan["summary"]}}))
    return doc


# ═══════════════════ decisión (PURA) ═══════════════════
def _words(first, last) -> frozenset:
    return frozenset(w for w in cv_extract.fold(f"{first or ''} {last or ''}").split() if len(w) >= 2)


def _shares(a: frozenset, b: frozenset) -> bool:
    return bool(a & b)


def _name_reason(extraction: dict, proposal: dict | None) -> str:
    blockers = set(extraction.get("blocking") or []) | set(extraction.get("flags") or [])
    if cv_extract.NAME_EDGE_TOKEN_UNEXPLAINED in blockers:
        return NAME_EDGE_TOKEN
    state = extraction.get("name_state")
    if state == "CONFLICT":
        return NAME_CONFLICT
    if state in ("UNCORROBORATED", "PARTIAL"):
        return NAME_UNCORROBORATED
    if state == "DECLARED":
        return NAME_DECLARED_ONLY
    if state == "CORROBORATED" and proposal and proposal.get("last_name"):
        return NAME_NOT_EMAIL_CORROBORATED
    return NO_RELIABLE_NAME


def _facts(group: dict) -> dict:
    e = group["extraction"]
    proposal = pilot._proposal(e)
    if proposal and not (proposal.get("first_name") and proposal.get("last_name")):
        proposal = None                                                                       # el Banco exige nombre Y apellido: una propuesta incompleta no sirve para un alta
    email = cv_reconcile.norm_email(e.get("email")) or None
    emails = (e.get("candidates") or {}).get("emails") or []
    blockers = set(e.get("blocking") or []) & _NAME_BLOCKERS
    strong = bool(proposal and proposal["state"] == "CORROBORATED" and e.get("name_corroborated_by_email") and not blockers)
    alternatives = (e.get("name_decision") or {}).get("alternatives") or []
    words = _words(proposal["first_name"], proposal["last_name"]) if proposal else frozenset()
    claims = [words] if proposal else [_words(a["tokens"], "") for a in alternatives if a.get("state") in _CLAIMING_STATES]
    return {"key": group["content_key"], "group": group, "email": email, "n_emails": len(emails), "proposal": proposal, "strong": strong, "claims": claims,
            "words": words, "warnings": sorted(set(e.get("flags") or []) & set(_WARNING_FLAGS)),
            "rule": (e.get("name_decision") or {}).get("rule"), "name_reason": _name_reason(e, proposal)}


def _contradicts(doc: dict, target: frozenset) -> bool:
    """El documento reclama una identidad (propuesta, o candidato anclado) y NINGUNA comparte una palabra con `target`."""
    return bool(doc["claims"]) and not any(_shares(c, target) for c in doc["claims"])


def _leader(members: list[dict]) -> dict:
    """El documento que da el nombre del perfil: la evidencia más fuerte (R1: texto y archivo coinciden), luego el nombre más completo y, para desempatar, el `content_key` (determinístico)."""
    return sorted(members, key=lambda m: (m["rule"] != "R1", -len(m["words"]), m["key"]))[0]


def build_plan(groups: list[dict], profiles: list[dict]) -> dict:
    """Decide la acción de cada contenido. PURA y determinística: depende solo de los registros por contenido y de la lista de perfiles recibida (no del orden de entrada). Devuelve
    `{decisions: {content_key: {...}}, clusters: {cluster_id: {...}}, summary: {...}}`; nada de esto lleva datos personales (solo códigos, `content_key` y el `profile_id` de perfiles existentes)."""
    decisions: dict[str, dict] = {}

    def decide(key: str, action: str, reasons=(), **extra):
        decisions[key] = {"action": action, "reasons": list(reasons), **extra}

    by_email: dict[str, list[dict]] = defaultdict(list)
    for p in profiles:
        email = cv_reconcile.norm_email(p.get("email"))
        if email:
            by_email[email].append(p)

    docs: list[dict] = []
    for g in sorted(groups, key=lambda g: g["content_key"]):
        key = g["content_key"]
        if g["state"] == pilot.FAILED:
            decide(key, ERROR, [g.get("failure") or "UNKNOWN"])
        elif g["state"] == pilot.ALIAS:
            decide(key, DUPLICATE, [], alias_of=g.get("alias_of"))
        elif g["state"] != pilot.EXTRACTED or not g.get("extraction"):
            decide(key, ERROR, [NOT_ANALYZED])
        elif g["extraction"].get("failure"):
            decide(key, PENDING, [READ_FAILURE, g["extraction"]["failure"]])
        else:
            docs.append(_facts(g))

    # Índices para el chequeo de posible duplicado por nombre: las señales STRONG/PROBABLE solo existen con el mismo conjunto de palabras del nombre o el mismo celular, así que alcanza con comparar contra esos perfiles
    # (el resultado es idéntico a comparar contra todos, pero no recorre ~miles de pares con similitud de texto).
    by_tokens: dict[frozenset, list[dict]] = defaultdict(list)
    by_phone: dict[str, list[dict]] = defaultdict(list)
    for p in profiles:
        by_tokens[cv_reconcile.token_key(p.get("first_name"), p.get("last_name"))].append(p)
        tail = cv_reconcile.phone_tail(p.get("phone"))
        if tail:
            by_phone[tail].append(p)

    clusters_by_email: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        if d["email"] is None:
            decide(d["key"], PENDING, [NO_EMAIL if d["n_emails"] == 0 else MULTIPLE_EMAILS] + ([] if d["strong"] else [d["name_reason"]]))
        elif cv_extract.is_role_mailbox(d["email"]):
            decide(d["key"], PENDING, [GENERIC_EMAIL] + ([] if d["strong"] else [d["name_reason"]]))
        else:
            clusters_by_email[d["email"]].append(d)

    clusters: dict[str, dict] = {}
    planned_creates: list[tuple[dict, list[dict]]] = []
    for email in sorted(clusters_by_email, key=lambda e: min(m["key"] for m in clusters_by_email[e])):
        members = sorted(clusters_by_email[email], key=lambda m: m["key"])
        strong = [m for m in members if m["strong"]]
        existing = by_email.get(email, [])
        live = [p for p in existing if not p.get("deleted_at")]
        if any(not _shares(a["words"], b["words"]) for a, b in combinations(strong, 2)):
            for m in members:
                decide(m["key"], PENDING, [EMAIL_CONFLICT])                                   # identidades contradictorias para un mismo email: ni se vincula ni se agrupa
        elif existing and not live:
            for m in members:
                decide(m["key"], PENDING, [EXISTING_DELETED])
        elif len(live) > 1:
            for m in members:
                decide(m["key"], PENDING, [MULTIPLE_EXISTING_MATCH], candidates=sorted(p["profile_id"] for p in live))
        elif live:
            profile = live[0]
            pwords = _words(profile.get("first_name"), profile.get("last_name"))
            email_corroborates_profile = cv_extract.email_strong([w for w in (profile.get("first_name") or "").split() + (profile.get("last_name") or "").split()], [email])
            for m in members:
                if _contradicts(m, pwords):
                    decide(m["key"], PENDING, [EXISTING_NAME_CONFLICT], candidates=[profile["profile_id"]])
                elif m["claims"] or email_corroborates_profile:
                    decide(m["key"], LINK, [], link_profile_id=profile["profile_id"])
                else:
                    decide(m["key"], PENDING, [EMAIL_MATCH_UNCORROBORATED], candidates=[profile["profile_id"]])
        elif not strong:
            for m in members:
                decide(m["key"], PENDING, [m["name_reason"]])
        else:
            leader = _leader(strong)
            person = {"first_name": leader["proposal"]["first_name"], "last_name": leader["proposal"]["last_name"], "email": email,
                      "phone": leader["group"]["extraction"].get("phone"), "phone_digits": leader["group"]["extraction"].get("phone_digits")}
            near = {id(p): p for p in by_tokens.get(cv_reconcile.token_key(person["first_name"], person["last_name"]), []) + by_phone.get(cv_reconcile.phone_tail(person["phone"] or person["phone_digits"]), [])}
            dups = [m_["profile_id"] for m_ in cv_reconcile.reconcile_with_profiles(person, list(near.values())) if m_["strength"] in (cv_reconcile.STRONG, cv_reconcile.PROBABLE)]
            if dups:
                for m in members:
                    decide(m["key"], PENDING, [POSSIBLE_DUPLICATE], candidates=sorted(dups))
            else:
                planned_creates.append((leader, members))

    for number, (leader, members) in enumerate(sorted(planned_creates, key=lambda lm: lm[0]["key"]), start=1):
        cid = f"c{number:04d}"
        attached = []
        for m in members:
            if m is leader:
                decide(m["key"], CREATE, [], cluster=cid, warnings=leader["warnings"])
            elif _contradicts(m, leader["words"]):
                decide(m["key"], PENDING, [EMAIL_NAME_MISMATCH])
            else:
                decide(m["key"], ATTACH, [], cluster=cid)
                attached.append(m["key"])
        clusters[cid] = {"leader": leader["key"], "members": [leader["key"]] + attached, "warnings": leader["warnings"]}

    return {"decisions": dict(sorted(decisions.items())), "clusters": clusters, "summary": _summary(groups, decisions, clusters)}


def _summary(groups: list[dict], decisions: dict, clusters: dict) -> dict:
    actions = Counter(d["action"] for d in decisions.values())
    primary = Counter(d["reasons"][0] for d in decisions.values() if d["action"] in (PENDING, ERROR) and d["reasons"])
    secondary = Counter(r for d in decisions.values() if d["action"] == PENDING for r in d["reasons"])
    specialties: dict[str, Counter] = defaultdict(Counter)
    for g in groups:
        action = decisions[g["content_key"]]["action"]
        for s in g["specialties"]:
            specialties[s]["documents"] += 1
            specialties[s][action] += 1
    links = {d["link_profile_id"] for d in decisions.values() if d["action"] == LINK}
    return {
        "contents": len(groups),
        "actions": {a: actions.get(a, 0) for a in ACTIONS},
        "profiles_to_create": len(clusters),
        "documents_to_upload": actions.get(CREATE, 0) + actions.get(ATTACH, 0) + actions.get(LINK, 0),
        "existing_profiles_to_link": len(links),
        "created_with_warnings": sum(1 for c in clusters.values() if c["warnings"]),
        "warnings": dict(sorted(Counter(w for c in clusters.values() for w in c["warnings"]).items())),
        "pending_by_reason": dict(sorted(primary.items())),
        "pending_reasons_any": dict(sorted(secondary.items())),
        "by_specialty": [{"specialty": s, "documents": c["documents"], **{a.lower(): c.get(a, 0) for a in ACTIONS}} for s, c in sorted(specialties.items())],
    }
