"""assessment_backfill.py — ALTA ÚNICA (offline) de assessments para entradas HISTÓRICAS del Catálogo.

Existe solo porque el Assessment se introdujo DESPUÉS de que ya hubiera entradas en el Catálogo: esas entradas no pasaron por el puente
Publicar → Assessment y figuran "Sin assessment". Es una acción de migración/bootstrap, NO comportamiento de request: ningún GET
(detalle del flujo, Portfolio) la invoca ni genera assessments.

Persistencia: ``generate`` escribe el sobre canónico en el almacén DURABLE del assessment gobernado (``governed-assessments``, registrado en
``PERSIST_PATHS``): se corre UNA vez y los assessments sobreviven a reinicios y deploys. Un assessment generado que solo quedó en memoria
(``durable == "memory"``) se informa como FALLA, no como éxito. ``durability`` cuenta remote / local / memory de lo generado.

Garantías (cubiertas por tests):
  * usa SOLO la evidencia local ya presente (el pool CSN del Catálogo): cero conexión a Datasphere, cero credenciales, cero extracción
    de linaje. Genera con ``assessment_service.generate`` (la misma API canónica del POST del informe);
  * omite lo que no está en ``NOT_YET_ASSESSED`` (ya vigente, desactualizado, no aplica, etc.: no se pisa ni se regenera);
  * una falla individual no corta el lote; se informa ``generated / skipped / failed`` con el motivo de cada uno.

Uso (con el MISMO ``DANWOR_CATALOG_DIR`` / ``DANWOR_GOVERNED_ASSESSMENTS_DIR`` y las credenciales Spaces del servidor que se quiere
poblar; sin Spaces queda en el disco local):
    python -m webapp.services.assessment_backfill [--client "Biogenesis"] [--dry-run]

ENRIQUECIMIENTO CONTROLADO (modo explícito y SEPARADO; el comando de arriba sigue generando solo NOT_YET_ASSESSED):
    python -m webapp.services.assessment_backfill --enrich-current-missing view_type --client Biogenesis --dry-run   # previsualiza (corre el motor en memoria, no escribe)
    python -m webapp.services.assessment_backfill --enrich-current-missing view_type --client Biogenesis --apply     # reemplaza los registros gobernados

  Elegible = registro gobernado CURRENT, perfil legible y SIN el hecho requerido en su ``fact_index`` (``view_type`` → ``am.view_type_composition``).
  NO es un parche: regenera el assessment COMPLETO con el motor vigente sobre la evidencia local (sin Datasphere, sin credenciales), por lo que otros
  valores visibles pueden cambiar (el informe viejo→nuevo se imprime siempre). Solo reemplaza el registro de la MISMA llave y solo si todo valida;
  ante cualquier fallo el registro previo (memoria, disco y remoto) queda intacto. Idempotente: un CURRENT que ya trae el hecho se omite.

MIGRACIÓN CONTROLADA DE EVIDENCIA (modo explícito y SEPARADO; para assessments STALE por cambio de versión de evidencia):
    python -m webapp.services.assessment_backfill --regenerate-stale-evidence evidence_g1_g3_v1 --client Biogenesis --dry-run
    python -m webapp.services.assessment_backfill --regenerate-stale-evidence evidence_g1_g3_v1 --client Biogenesis --apply

  Elegible = registro gobernado STALE por VERSIÓN únicamente (``input_pool_signature`` igual a la del pool local) cuya diferencia de versiones es EXACTAMENTE la de
  la migración (``assessment_service.EVIDENCE_MIGRATIONS``). STALE por cambio de entrada (INPUT_CHANGED / VERSION_AND_INPUT), STALE con otras diferencias de versión
  (version_only_untargeted), FAILED, INCOMPATIBLE_SCHEMA, NOT_YET_ASSESSED y NOT_APPLICABLE se REPORTAN aparte y NO se migran. Comparte con el enriquecimiento el MISMO
  núcleo seguro (regenerar en memoria → validar → postcondiciones → ``commit_regenerated`` → durabilidad remota exacta entre invocaciones → regla de reinicio); las
  postcondiciones miran el CONTENIDO de ``canonical_evidence`` y la identidad canónica del cierre (CLOSURE_DRIFT es falla dura). No inventa alcance de adquisición.

  DÓNDE CORRERLO: en la CONSOLA DEL SERVIDOR (DigitalOcean), donde ``governed-assessments/`` ya está restaurado y el pool del cliente está presente.
  NO desde una máquina de desarrollo y SIN restore() propio (el modo avisa si el estado gobernado parece ausente).
  REINICIO: el proceso web conserva el sobre viejo en memoria; el refresco es un reinicio CONTROLADO, y solo cuando TODO lo regenerado está en
  ``remote``. Si algún registro quedó ``local`` (PUT a Spaces sin confirmar) el resultado dice ``RESTART_BLOCKED_PENDING_REMOTE``: reiniciar antes
  dejaría que ``restore()`` baje el objeto remoto viejo sobre el archivo local nuevo.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Iterable

from webapp.services import assessment_service as svc
from webapp.services import catalog_reads


def backfill(entries: Iterable[Any], *, dry_run: bool = False,
             status_for: Callable[[Any], svc.AssessmentStatus] = svc.status_for_entry,
             generate: Callable[[Any], svc.AssessmentStatus] = svc.generate) -> dict:
    out: dict = {"generated": [], "skipped": [], "failed": [], "dry_run": dry_run}
    for entry in entries:
        ref = {"entry_id": getattr(entry, "entry_id", ""), "client": str(getattr(entry, "cliente", "")),
               "domain": str(getattr(entry, "dominio", ""))}
        try:
            status = status_for(entry)
            if status.state != svc.NOT_YET_ASSESSED:
                out["skipped"].append({**ref, "state": status.state, "reason": status.reason})
                continue
            if dry_run:
                out["skipped"].append({**ref, "state": status.state, "reason": "dry-run"})
                continue
            result = generate(entry)
        except Exception as exc:                                        # noqa: BLE001  una entrada rota no corta el lote
            out["failed"].append({**ref, "state": svc.FAILED, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        row = {**ref, "state": result.state, "reason": result.reason}
        durable = getattr(result, "durable", None)
        row["durable"] = durable
        if result.state == svc.CURRENT and durable == svc.DURABLE_MEMORY:      # generado pero NO persistido: no se informa como éxito
            out["failed"].append({**row, "state": svc.FAILED, "reason": "generado pero no persistido (solo memoria)"})
            continue
        bucket = "generated" if result.state == svc.CURRENT else "skipped" if result.state == svc.NOT_APPLICABLE else "failed"
        out[bucket].append(row)
    out["counts"] = {k: len(out[k]) for k in ("generated", "skipped", "failed")}
    out["durability"] = {k: sum(1 for r in out["generated"] if r.get("durable") == k)
                         for k in (svc.DURABLE_REMOTE, svc.DURABLE_LOCAL, svc.DURABLE_MEMORY)}
    return out


# ═══════════════════════════ enriquecimiento de assessments gobernados CURRENT ═══════════════════════════
# Registro chico y explícito (no es un framework de migraciones): nombre del enriquecimiento → hechos canónicos que el perfil debe traer.
ENRICHMENTS: dict[str, tuple[str, ...]] = {"view_type": ("am.view_type_composition",)}

WOULD_REGENERATE, REGENERATED, SKIP, FAILED_ACTION = "WOULD_REGENERATE", "REGENERATED", "SKIP", "FAILED"
SKIPPED_ALREADY_ENRICHED, SKIPPED_NON_CURRENT, SKIPPED_NOT_YET_ASSESSED = "skipped_already_enriched", "skipped_non_current", "skipped_not_yet_assessed"
RESTART_SAFE, RESTART_BLOCKED, RESTART_NOT_NEEDED, RESTART_DRY_RUN = "RESTART_SAFE", "RESTART_BLOCKED_PENDING_REMOTE", "NO_RESTART_NEEDED", "NOT_APPLICABLE_DRY_RUN"


def compare_profiles(old: dict | None, new: dict | None, required: Iterable[str]) -> dict:
    """Informe CONCISO viejo → nuevo (no vuelca perfiles): cobertura estricta, confianza, casos a revisar, preguntas abiertas, estado y cantidad de grupos
    de hallazgo por área (incluida Ingesta) y presencia de los hechos requeridos. Solo lee campos del JSON canónico."""
    def pick(doc: dict | None) -> dict:
        if not doc:
            return {"coverage_strict": None, "confidence": None, "review_occurrences": None, "open_questions": None, "areas": {}}
        summ = doc.get("summary") or {}
        return {"coverage_strict": (summ.get("coverage") or {}).get("strict"), "confidence": (summ.get("confidence") or {}).get("level"),
                "review_occurrences": sum(g.get("occurrence_count", 0) for g in doc.get("review_required") or []),
                "open_questions": (summ.get("context_required") or {}).get("open_questions"),
                "areas": {a["id"]: {"state": a.get("assessability"), "findings": len(a.get("finding_groups") or [])} for a in doc.get("areas") or []}}
    o, n = pick(old), pick(new)
    pair = lambda k: {"old": o[k], "new": n[k], "changed": o[k] != n[k]}          # noqa: E731
    areas = {aid: {"state": {"old": o["areas"].get(aid, {}).get("state"), "new": n["areas"].get(aid, {}).get("state")},
                   "findings": {"old": o["areas"].get(aid, {}).get("findings"), "new": n["areas"].get(aid, {}).get("findings")}}
             for aid in sorted(set(o["areas"]) | set(n["areas"]))}
    for a in areas.values():
        a["changed"] = a["state"]["old"] != a["state"]["new"] or a["findings"]["old"] != a["findings"]["new"]
    enrichment = {f: {"old": "absent" if f in svc.missing_facts(old, [f]) else "present", "new": "absent" if f in svc.missing_facts(new, [f]) else "present"}
                  for f in required}
    return {"coverage_strict": pair("coverage_strict"), "confidence": pair("confidence"), "review_occurrences": pair("review_occurrences"),
            "open_questions": pair("open_questions"), "areas": areas, "enrichment": enrichment,
            "changed": any(o[k] != n[k] for k in ("coverage_strict", "confidence", "review_occurrences", "open_questions")) or any(a["changed"] for a in areas.values())}


def _governed_state_present() -> bool:
    """¿Hay estado gobernado en el disco de ESTE entorno? (no restaura nada: solo mira). Sin él, la corrida no está en el entorno correcto."""
    root = svc.governed_dir()
    return bool(root is not None and root.is_dir() and any(root.rglob("*.json")))



# ═══════════════════════════ NÚCLEO COMPARTIDO de los modos de regeneración controlada ═══════════════════════════
# Enriquecimiento de CURRENT y migración de evidencia de STALE version-only comparten: regenerar en memoria + delta + escritura segura + reintento
# remoto + verificación de durabilidad entre invocaciones + regla de reinicio. Solo difieren la ELEGIBILIDAD, las postcondiciones (en el servicio) y el delta.
def _regenerate_record(rec: dict, entry: Any, status: svc.AssessmentStatus, *, regenerate_call: Callable[[Any], Any], compare: Callable[[dict | None, Any], dict],
                       commit: Callable[[Any], str], apply: bool) -> None:
    """Regenera en MEMORIA el assessment de ``entry`` (el motor canónico; no escribe), calcula el delta viejo→nuevo con el MISMO perfil que se escribiría y,
    solo con ``apply``, lo escribe por el camino seguro. Muta ``rec``; nunca lanza (una falla individual no corta el lote); ante cualquier falla el registro
    previo queda intacto."""
    try:
        candidate = regenerate_call(entry)                                        # motor canónico en MEMORIA; valida; no escribe
    except svc.EnrichmentError as exc:
        rec.update(action=FAILED_ACTION, reason=exc.code)
        return
    except Exception as exc:                                                      # noqa: BLE001 — jamás se escribe nada; el registro previo queda
        rec.update(action=FAILED_ACTION, reason=f"REGENERATION_ERROR:{type(exc).__name__}")
        return
    rec["delta"] = compare(status.profile, candidate)                             # el MISMO perfil que se escribe: no se recalcula
    if not apply:
        rec["action"] = WOULD_REGENERATE
        return
    try:
        durable = commit(candidate)
    except svc.EnrichmentError as exc:                                            # p. ej. el registro cambió durante la corrida: NO se pisa
        rec.update(action=FAILED_ACTION, reason=exc.code)
        return
    except Exception as exc:                                                      # noqa: BLE001
        rec.update(action=FAILED_ACTION, reason=f"PERSISTENCE_ERROR:{type(exc).__name__}")
        return
    if durable == svc.DURABLE_MEMORY:                                             # no se pudo escribir: el registro previo sigue siendo el vigente
        rec.update(action=FAILED_ACTION, reason="NOT_PERSISTED_MEMORY_ONLY (registro previo conservado)", durable=durable)
        return
    rec.update(action=REGENERATED, durable=durable)
    rec["_candidate"] = candidate


def _retry_pending_local(out: dict, retry_remote: Callable[[str, str], bool]) -> None:
    """Reintento EXPLÍCITO del PUT remoto de lo que quedó solo en disco (misma API del write-through)."""
    for rec in out["records"]:
        if rec.get("action") == REGENERATED and rec["durable"] == svc.DURABLE_LOCAL:
            try:
                if retry_remote(rec["client"], rec["root"]):
                    rec.update(durable=svc.DURABLE_REMOTE, remote_retry="CONFIRMED")
                else:
                    rec["remote_retry"] = "NOT_CONFIRMED"
            except Exception:                                                     # noqa: BLE001
                rec["remote_retry"] = "NOT_CONFIRMED"


def _verify_done_records(out: dict, done_reason: str, apply: bool, retry_remote: Callable[[str, str], bool], verify_remote: Callable[[str, str], str]) -> None:
    """Durabilidad de lo YA hecho: un registro local-pero-no-remoto de una corrida anterior NO deja de bloquear el reinicio por omitirse acá.
    Se verifica por bytes contra Spaces (lectura pura); en apply, lo pendiente recibe el mismo reintento explícito (push_path) y se re-verifica."""
    for rec in out["records"]:
        if rec["reason"] != done_reason:
            continue
        try:
            state = verify_remote(rec["client"], rec["root"])
        except Exception:                                                         # noqa: BLE001
            state = svc.REMOTE_UNVERIFIABLE
        if apply and state == svc.REMOTE_PENDING:
            try:
                if retry_remote(rec["client"], rec["root"]):
                    state = verify_remote(rec["client"], rec["root"])
                    rec["remote_retry"] = "CONFIRMED" if state == svc.REMOTE_CONFIRMED else "NOT_CONFIRMED"
                else:
                    rec["remote_retry"] = "NOT_CONFIRMED"
            except Exception:                                                     # noqa: BLE001
                rec["remote_retry"] = "NOT_CONFIRMED"
        rec["remote_state"] = state


def _environment_warnings(out: dict, entries: list, states: list[str], apply: bool, governed_state_present: Callable[[], bool], remote_enabled: Callable[[], bool]) -> None:
    """Este modo NO restaura nada: si el estado gobernado parece ausente, no se finge que no hay nada que hacer."""
    if not entries:
        out["environment_warnings"].append("NO_CATALOG_ENTRIES: no hay entradas del Catálogo para el alcance pedido (¿cliente mal escrito o Catálogo vacío?)")
    elif states and all(st == svc.NOT_YET_ASSESSED for st in states):
        out["environment_warnings"].append("ENVIRONMENT_NO_GOVERNED_STATE: todas las entradas figuran NOT_YET_ASSESSED; corré esto en la consola del servidor "
                                           "donde governed-assessments/ ya está restaurado (este modo no restaura)")
    elif not governed_state_present():
        out["environment_warnings"].append("ENVIRONMENT_NO_GOVERNED_STATE: no hay archivos en el almacén gobernado de este entorno")
    if apply and not remote_enabled():
        out["environment_warnings"].append("REMOTE_STORE_DISABLED: sin credenciales de Spaces nada puede quedar en remote; el reinicio queda bloqueado")


def _restart_contract(out: dict, counts: dict, apply: bool, remote_enabled: Callable[[], bool], done_confirmed_key: str) -> None:
    """Regla de reinicio: SOLO si todo lo hecho está confirmado en remote (se mira lo regenerado ahora O ya hecho antes, no solo esta corrida)."""
    confirmed_any = counts["regenerated_remote"] + counts[done_confirmed_key] > 0
    if counts["pending_remote"] or (apply and counts["regenerated_remote"] and not remote_enabled()):
        out["restart"] = RESTART_BLOCKED
    elif not apply and counts["would_regenerate"]:
        out["restart"] = RESTART_DRY_RUN                                         # aún hay algo por regenerar: hablar de reinicio sería prematuro
    elif confirmed_any:
        out["restart"] = RESTART_SAFE
    else:
        out["restart"] = RESTART_DRY_RUN if not apply else RESTART_NOT_NEEDED
    out["restart_safe"] = out["restart"] == RESTART_SAFE
    out["complete"] = apply and counts["failed"] == 0 and counts["pending_remote"] == 0


def _record_for(seen: dict, out: dict, status: svc.AssessmentStatus, eid: str, client: str, **extra: Any) -> tuple[dict, bool]:
    """Registro por (cliente, AM terminal): varias entradas del Catálogo → el MISMO assessment gobernado: una sola vez."""
    key = (client.lower(), status.terminal_am) if status.terminal_am else (client.lower(), f"entry:{eid}")
    if key in seen:
        seen[key]["entry_ids"].append(eid)
        return seen[key], False
    rec: dict = {"client": status.client or client, "root": status.terminal_am, "entry_ids": [eid], "state": status.state, "action": SKIP,
                 "reason": None, "delta": None, "durable": None, **extra}
    seen[key] = rec
    out["records"].append(rec)
    return rec, True


def enrich_current(entries: Iterable[Any], enrichment: str, *, apply: bool = False,
                   status_for: Callable[[Any], svc.AssessmentStatus] = svc.status_for_entry,
                   regenerate: Callable[[Any, Iterable[str]], Any] = svc.regenerate_in_memory,
                   commit: Callable[[Any], str] = svc.commit_regenerated,
                   retry_remote: Callable[[str, str], bool] = svc.retry_remote_persistence,
                   verify_remote: Callable[[str, str], str] = svc.verify_remote_durability,
                   governed_state_present: Callable[[], bool] = _governed_state_present,
                   remote_enabled: Callable[[], bool] = lambda: svc.remote_store.enabled()) -> dict:
    """Preview (``apply=False``: corre el motor en memoria y NO escribe) o aplicación (``apply=True``) del enriquecimiento ``enrichment`` sobre los
    assessments gobernados CURRENT que no traen el hecho requerido. Deduplica por (cliente, AM terminal). Una falla individual no corta el lote."""
    required = ENRICHMENTS[enrichment]
    entries = list(entries)
    out: dict = {"mode": "enrich-current-missing", "enrichment": enrichment, "required_facts": list(required), "apply": apply, "dry_run": not apply,
                 "records": [], "environment_warnings": []}
    seen: dict[tuple[str, str], dict] = {}
    states: list[str] = []
    for entry in entries:
        eid, client = str(getattr(entry, "entry_id", "")), str(getattr(entry, "cliente", ""))
        try:
            status = status_for(entry)
        except Exception as exc:                                                  # noqa: BLE001 — una entrada rota no corta el lote
            out["records"].append({"client": client, "root": None, "entry_ids": [eid], "state": None, "action": FAILED_ACTION,
                                   "reason": f"STATUS_ERROR:{type(exc).__name__}", "missing": [], "delta": None, "durable": None})
            continue
        states.append(status.state)
        rec, new = _record_for(seen, out, status, eid, client, missing=[])
        if not new:
            continue
        if status.state == svc.NOT_YET_ASSESSED:
            rec["reason"] = SKIPPED_NOT_YET_ASSESSED                              # lo genera el backfill normal; este modo NUNCA genera faltantes
            continue
        if status.state != svc.CURRENT or status.profile is None:
            rec["reason"] = SKIPPED_NON_CURRENT
            continue
        rec["missing"] = svc.missing_facts(status.profile, required)
        if not rec["missing"]:
            rec["reason"] = SKIPPED_ALREADY_ENRICHED
            continue
        rec["eligible"] = True
        _regenerate_record(rec, entry, status, regenerate_call=lambda e: regenerate(e, required),
                           compare=lambda old, cand: compare_profiles(old, cand.profile, required), commit=commit, apply=apply)
    if apply:
        _retry_pending_local(out, retry_remote)
    _verify_done_records(out, SKIPPED_ALREADY_ENRICHED, apply, retry_remote, verify_remote)
    for rec in out["records"]:
        rec.pop("_candidate", None)
    recs = out["records"]
    count = lambda pred: sum(1 for r in recs if pred(r))                           # noqa: E731
    out["counts"] = {
        "eligible": count(lambda r: r.get("eligible")),
        "would_regenerate": count(lambda r: r["action"] == WOULD_REGENERATE),
        "regenerated_remote": count(lambda r: r["action"] == REGENERATED and r["durable"] == svc.DURABLE_REMOTE),
        "regenerated_local": count(lambda r: r["action"] == REGENERATED and r["durable"] == svc.DURABLE_LOCAL),
        SKIPPED_ALREADY_ENRICHED: count(lambda r: r["reason"] == SKIPPED_ALREADY_ENRICHED),
        SKIPPED_NON_CURRENT: count(lambda r: r["reason"] == SKIPPED_NON_CURRENT),
        SKIPPED_NOT_YET_ASSESSED: count(lambda r: r["reason"] == SKIPPED_NOT_YET_ASSESSED),
        "failed": count(lambda r: r["action"] == FAILED_ACTION),
        "already_enriched_remote_confirmed": count(lambda r: r.get("remote_state") == svc.REMOTE_CONFIRMED),
        "pending_remote": count(lambda r: (r["action"] == REGENERATED and r["durable"] != svc.DURABLE_REMOTE)
                                or (r["reason"] == SKIPPED_ALREADY_ENRICHED and r.get("remote_state") != svc.REMOTE_CONFIRMED)),
    }
    _environment_warnings(out, entries, states, apply, governed_state_present, remote_enabled)
    _restart_contract(out, out["counts"], apply, remote_enabled, "already_enriched_remote_confirmed")
    return out


# ═══════════════════════════ MIGRACIÓN CONTROLADA de evidencia: STALE version-only ═══════════════════════════
# Modo EXPLÍCITO y separado (``--regenerate-stale-evidence <migración>``). Registro chico: ``svc.EVIDENCE_MIGRATIONS``. Elegible SOLO si el assessment
# gobernado es STALE por VERSIÓN únicamente (misma firma del pool: el insumo técnico es idéntico) y la diferencia de versiones es EXACTAMENTE la de la
# migración. STALE por cambio de entrada (INPUT_CHANGED / VERSION_AND_INPUT) o con diferencias fuera de la migración se REPORTA aparte y NO se migra:
# eso es una reevaluación contra evidencia técnica distinta (modo posterior). INCOMPATIBLE_SCHEMA queda fuera.
SKIPPED_ALREADY_CURRENT, SKIPPED_FAILED_STATE = "skipped_already_current", "skipped_failed_state"
SKIPPED_INCOMPATIBLE, SKIPPED_NOT_APPLICABLE = "skipped_incompatible", "skipped_not_applicable"
VERSION_ONLY_UNTARGETED, INPUT_CHANGED_STALE, VERSION_AND_INPUT_CHANGED = "version_only_untargeted", "input_changed_stale", "version_and_input_changed"
_STALE_ONLY_REPORTED = (VERSION_ONLY_UNTARGETED, INPUT_CHANGED_STALE, VERSION_AND_INPUT_CHANGED)


def _provenance_summary(profile: dict | None) -> dict:
    """Conteo de la procedencia de hojas del ``canonical_evidence`` (CONTENIDO, no nombres de hecho): assessed / not assessed / established / declared / not observable."""
    leaves = ((profile or {}).get("canonical_evidence") or {}).get("leaf_provenance") or []
    c = {"leaves": len(leaves), "assessed": 0, "not_assessed": 0, "established": 0, "declared": 0, "not_observable": 0}
    for l in leaves:
        c["assessed" if l.get("assessability") == "ASSESSED" else "not_assessed"] += 1
        key = {"ESTABLISHED": "established", "DECLARED": "declared", "NOT_OBSERVABLE": "not_observable"}.get(l.get("provenance_state"))
        if key:
            c[key] += 1
    return c


def _old_repeated_review(profile: dict | None) -> set[tuple[str, str, str]]:
    """(proveedor, consumidor, campo) que el perfil VIEJO ya mostraba como revisión de derivación repetida (id histórico o vigente)."""
    out: set[tuple[str, str, str]] = set()
    for g in (profile or {}).get("review_required") or []:
        if presenter_canonical(g.get("id", "")) == "REPEATED_DERIVATION_IN_LINEAGE":
            for o in g.get("occurrences") or []:
                v = o.get("values") or {}
                for f in v.get("fields") or []:
                    out.add((str(v.get("provider")), str(v.get("consumer")), str(f)))
    return out


def presenter_canonical(signal_id: str) -> str:
    from webapp.services import assessment_presenter as presenter
    return presenter.canonical_signal_id(signal_id)


def compare_evidence_migration(old: dict | None, new_cand: Any, migration: str, old_versions: dict | None, versions: dict) -> dict:
    """Informe CONCISO viejo → nuevo de la migración de evidencia (no vuelca perfiles). Lee el CONTENIDO de ``canonical_evidence``, no la membresía de hechos."""
    new = new_cand.profile
    m = svc.EVIDENCE_MIGRATIONS[migration]
    base = compare_profiles(old, new, ())
    def cov(doc: dict | None, k: str):
        return (((doc or {}).get("summary") or {}).get("coverage") or {}).get(k)
    old_ce, new_ce = (old or {}).get("canonical_evidence"), new.get("canonical_evidence")
    sections = ("leaf_provenance", "derivation_semantics", "assembly_topology", "expression_repetition")
    presence = lambda ce, sec: "present" if isinstance(ce, dict) and ce.get(sec) not in (None, [], {}) else "absent"          # noqa: E731
    reviewed = _old_repeated_review(old)
    reps = [{"field": r["field"], "provider": r["provider"], "consumer": r["consumer"], "old_review_present": (r["provider"], r["consumer"], r["field"]) in reviewed,
             "relation": r["relation"], "relation_reason": r["relation_reason"], "exposed_by_root": r["exposed_by_root"], "exposed_by_root_basis": r["exposure_basis"]}
            for r in ((new_ce or {}).get("derivation_semantics") or {}).get("repeated_derivations") or []]
    oc, nc = svc.closure_identity(old), svc.closure_identity(new)
    drift = {"drift": oc != nc, "terminal_am_same": (oc or (None,))[0] == (nc or (None,))[0],
             "closure_artifacts_old": len(oc[3]) if oc else None, "closure_artifacts_new": len(nc[3]) if nc else None}
    pair = lambda a, b: {"old": a, "new": b, "changed": a != b}                    # noqa: E731
    delta = {"versions": {k: {"old": (old_versions or {}).get(k), "new": versions.get(k)} for k in m.target_keys},
             "pool_signature_same": True, "coverage_strict": base["coverage_strict"], "coverage_inclusive": pair(cov(old, "inclusive"), cov(new, "inclusive")),
             "confidence": base["confidence"], "review_occurrences": base["review_occurrences"], "open_questions": base["open_questions"], "areas": base["areas"],
             "canonical_evidence": {"old": "present" if isinstance(old_ce, dict) else "absent", "new": "present" if isinstance(new_ce, dict) else "absent",
                                    "sections": {s: {"old": presence(old_ce, s), "new": presence(new_ce, s)} for s in sections}},
             "leaf_provenance": {"old": _provenance_summary(old), "new": _provenance_summary(new)},
             "repeated_derivations": reps, "closure_drift": drift}
    delta["changed"] = base["changed"] or pair(cov(old, "inclusive"), cov(new, "inclusive"))["changed"]
    return delta


def _classify_migration(status: svc.AssessmentStatus, migration: str) -> str | None:
    """Motivo de OMISIÓN/REPORTE aparte, o None si es elegible (STALE VERSION_ONLY cubierto exactamente por ``migration``)."""
    st = status.state
    if st == svc.NOT_YET_ASSESSED:
        return SKIPPED_NOT_YET_ASSESSED
    if st == svc.NOT_APPLICABLE:
        return SKIPPED_NOT_APPLICABLE
    if st == svc.FAILED:
        return SKIPPED_FAILED_STATE
    if st == svc.INCOMPATIBLE_SCHEMA:
        return SKIPPED_INCOMPATIBLE
    if st == svc.CURRENT:
        return SKIPPED_ALREADY_CURRENT
    if status.profile is None:
        return SKIPPED_FAILED_STATE
    if status.stale_reason == svc.STALE_INPUT_CHANGED:
        return INPUT_CHANGED_STALE
    if status.stale_reason == svc.STALE_VERSION_AND_INPUT:
        return VERSION_AND_INPUT_CHANGED
    if status.stale_reason == svc.STALE_VERSION_ONLY and status.stale_targeted == migration:
        return None
    return VERSION_ONLY_UNTARGETED


def migrate_stale_evidence(entries: Iterable[Any], migration: str, *, apply: bool = False,
                           status_for: Callable[[Any], svc.AssessmentStatus] = svc.status_for_entry,
                           regenerate: Callable[[Any, str], Any] = svc.regenerate_stale_evidence,
                           commit: Callable[[Any], str] = svc.commit_stale_migration,
                           retry_remote: Callable[[str, str], bool] = svc.retry_remote_persistence,
                           verify_remote: Callable[[str, str], str] = svc.verify_remote_durability,
                           governed_state_present: Callable[[], bool] = _governed_state_present,
                           remote_enabled: Callable[[], bool] = lambda: svc.remote_store.enabled()) -> dict:
    """Preview (``apply=False``: corre el motor en memoria y NO escribe) o aplicación de la migración de evidencia ``migration`` sobre los assessments gobernados
    STALE VERSION_ONLY. Sin Datasphere, OAuth, SharePoint ni ``restore()``. Deduplica por (cliente, AM terminal). Una falla individual no corta el lote."""
    target = svc.EVIDENCE_MIGRATIONS[migration]
    versions = svc.current_assessment_versions()
    entries = list(entries)
    out: dict = {"mode": "regenerate-stale-evidence", "migration": migration, "target_keys": list(target.target_keys),
                 "target_versions": {k: versions.get(k) for k in target.target_keys}, "apply": apply, "dry_run": not apply,
                 "records": [], "environment_warnings": []}
    seen: dict[tuple[str, str], dict] = {}
    states: list[str] = []
    for entry in entries:
        eid, client = str(getattr(entry, "entry_id", "")), str(getattr(entry, "cliente", ""))
        try:
            status = status_for(entry)
        except Exception as exc:                                                  # noqa: BLE001 — una entrada rota no corta el lote
            out["records"].append({"client": client, "root": None, "entry_ids": [eid], "state": None, "action": FAILED_ACTION,
                                   "reason": f"STATUS_ERROR:{type(exc).__name__}", "delta": None, "durable": None})
            continue
        states.append(status.state)
        rec, new = _record_for(seen, out, status, eid, client, stale_reason=status.stale_reason, version_diff=list(status.stale_version_diff))
        if not new:
            continue
        reason = _classify_migration(status, migration)
        if reason is not None:
            rec["reason"] = reason
            continue
        rec["eligible"] = True
        old_versions = {k: None for k in target.target_keys}
        try:
            env = svc._load_envelope(status.client, status.terminal_am)
            old_versions = (env or {}).get("assessment_versions") or {}
        except Exception:                                                         # noqa: BLE001
            pass
        _regenerate_record(rec, entry, status, regenerate_call=lambda e: regenerate(e, migration),
                           compare=lambda old, cand: compare_evidence_migration(old, cand, migration, old_versions, versions), commit=commit, apply=apply)
        if rec["action"] == FAILED_ACTION and rec["reason"] in ("PREVIOUS_NOT_TARGETED_VERSION_ONLY_STALE", "PREVIOUS_CHANGED_DURING_RUN"):
            try:                                                                  # alguien lo regeneró por la UI durante la ventana: si ahora es CURRENT, se tolera
                if status_for(entry).state == svc.CURRENT:
                    rec.update(action=SKIP, reason=SKIPPED_ALREADY_CURRENT, delta=None, eligible=False)
            except Exception:                                                     # noqa: BLE001
                pass
    if apply:
        _retry_pending_local(out, retry_remote)
    _verify_done_records(out, SKIPPED_ALREADY_CURRENT, apply, retry_remote, verify_remote)
    for rec in out["records"]:
        rec.pop("_candidate", None)
    recs = out["records"]
    count = lambda pred: sum(1 for r in recs if pred(r))                           # noqa: E731
    out["counts"] = {
        "eligible": count(lambda r: r.get("eligible")),
        "version_only_stale": count(lambda r: r.get("eligible")),
        VERSION_ONLY_UNTARGETED: count(lambda r: r["reason"] == VERSION_ONLY_UNTARGETED),
        INPUT_CHANGED_STALE: count(lambda r: r["reason"] == INPUT_CHANGED_STALE),
        VERSION_AND_INPUT_CHANGED: count(lambda r: r["reason"] == VERSION_AND_INPUT_CHANGED),
        "current": count(lambda r: r["reason"] == SKIPPED_ALREADY_CURRENT),
        "not_yet_assessed": count(lambda r: r["reason"] == SKIPPED_NOT_YET_ASSESSED),
        "failed_state": count(lambda r: r["reason"] == SKIPPED_FAILED_STATE),
        "incompatible": count(lambda r: r["reason"] == SKIPPED_INCOMPATIBLE),
        "not_applicable": count(lambda r: r["reason"] == SKIPPED_NOT_APPLICABLE),
        "would_regenerate": count(lambda r: r["action"] == WOULD_REGENERATE),
        "regenerated_remote": count(lambda r: r["action"] == REGENERATED and r["durable"] == svc.DURABLE_REMOTE),
        "regenerated_local": count(lambda r: r["action"] == REGENERATED and r["durable"] == svc.DURABLE_LOCAL),
        "failed": count(lambda r: r["action"] == FAILED_ACTION),
        "already_current_remote_confirmed": count(lambda r: r["reason"] == SKIPPED_ALREADY_CURRENT and r.get("remote_state") == svc.REMOTE_CONFIRMED),
        "pending_remote": count(lambda r: (r["action"] == REGENERATED and r["durable"] != svc.DURABLE_REMOTE)
                                or (r["reason"] == SKIPPED_ALREADY_CURRENT and r.get("remote_state") != svc.REMOTE_CONFIRMED)),
        "remaining_stale_not_migrated": count(lambda r: r["reason"] in _STALE_ONLY_REPORTED),
    }
    _environment_warnings(out, entries, states, apply, governed_state_present, remote_enabled)
    _restart_contract(out, out["counts"], apply, remote_enabled, "already_current_remote_confirmed")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--client", default="", help="limitar a un cliente (por defecto: todas las entradas del Catálogo)")
    ap.add_argument("--dry-run", action="store_true", help="solo informa qué se generaría (en los modos de regeneración: corre el motor en memoria y muestra viejo→nuevo)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--enrich-current-missing", choices=sorted(ENRICHMENTS), metavar="NOMBRE",
                      help="modo EXPLÍCITO: regenera los assessments gobernados CURRENT a los que les falta el hecho requerido (" + ", ".join(sorted(ENRICHMENTS)) + "). "
                           "Exige --dry-run o --apply")
    mode.add_argument("--regenerate-stale-evidence", choices=sorted(svc.EVIDENCE_MIGRATIONS), metavar="MIGRACION",
                      help="modo EXPLÍCITO: migra los assessments gobernados STALE por VERSIÓN únicamente (misma firma del pool) a la evidencia vigente ("
                           + ", ".join(sorted(svc.EVIDENCE_MIGRATIONS)) + "). Exige --dry-run o --apply")
    ap.add_argument("--apply", action="store_true", help="solo con un modo de regeneración: escribe los reemplazos (excluyente con --dry-run)")
    args = ap.parse_args(argv)
    regen_mode = args.enrich_current_missing or args.regenerate_stale_evidence
    if regen_mode:
        if args.dry_run == args.apply:                                           # ninguno o ambos: usage, CERO escrituras
            ap.error("los modos de regeneración exigen EXACTAMENTE uno de --dry-run o --apply")
    elif args.apply:
        ap.error("--apply solo existe con --enrich-current-missing o --regenerate-stale-evidence (el backfill normal no lo usa)")
    _mod, entries = catalog_reads._catalog_entries()
    wanted = args.client.strip().lower()
    entries = [e for e in (entries or []) if not wanted or str(getattr(e, "cliente", "")).strip().lower() == wanted]
    if args.enrich_current_missing:
        report = enrich_current(entries, args.enrich_current_missing, apply=args.apply)
        code = 1 if report["counts"]["failed"] else (2 if report["environment_warnings"] else 0)
    elif args.regenerate_stale_evidence:
        report = migrate_stale_evidence(entries, args.regenerate_stale_evidence, apply=args.apply)
        code = 1 if report["counts"]["failed"] else (2 if report["environment_warnings"] else 0)
    else:
        report = backfill(entries, dry_run=args.dry_run)
        code = 1 if report["failed"] else 0
    sys.stdout.buffer.write((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))   # UTF-8 aun con stdout redirigido (cp1252)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
