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
    python -m webapp.services.assessment_backfill --regenerate-stale-evidence evidence_closure_contract_v2 --client Biogenesis --dry-run
    python -m webapp.services.assessment_backfill --regenerate-stale-evidence evidence_closure_contract_v2 --client Biogenesis --apply

  Elegible = registro gobernado STALE por VERSIÓN únicamente (``input_pool_signature`` igual a la del pool local) cuya diferencia de versiones es EXACTAMENTE la de
  la migración (``assessment_service.EVIDENCE_MIGRATIONS``). STALE por cambio de entrada (INPUT_CHANGED / VERSION_AND_INPUT), STALE con otras diferencias de versión
  (version_only_untargeted), FAILED, INCOMPATIBLE_SCHEMA, NOT_YET_ASSESSED y NOT_APPLICABLE se REPORTAN aparte y NO se migran. Comparte con el enriquecimiento el MISMO
  núcleo seguro (regenerar en memoria → validar → postcondiciones → ``commit_regenerated`` → durabilidad remota exacta entre invocaciones → regla de reinicio); las
  postcondiciones miran el CONTENIDO de ``canonical_evidence`` y la identidad canónica del cierre (CLOSURE_DRIFT es falla dura). No inventa alcance de adquisición.
  ``evidence_closure_contract_v2`` (evidence release) usa la política ``TARGETED_CLOSURE_EXPANSION``: el cierre de una raíz puede CRECER únicamente si cada miembro
  agregado es un target de flujo que Modeling ya leía en el mismo pool y que la clasificación previa excluía (hoja, leída por un miembro del cierre viejo, no un flujo);
  el cierre viejo debe ser subconjunto del nuevo. Cualquier otra diferencia es CLOSURE_DRIFT (cero escrituras). El dry-run informa los miembros agregados y su base.

  SECUENCIA DE MIGRACIONES (sin encadenamiento automático): el registro tiene CUATRO migraciones explícitas y cada una cubre EXACTAMENTE una diferencia de versiones.
    * ``evidence_g1_g3_v1``: perfil ANTERIOR a G1/G3 (sin ``canonical_evidence``). Sigue registrada para no varar perfiles históricos que existan o se restauren en ese contrato.
      El motor de este build solo emite el contrato VIGENTE (no puede fabricar el intermedio G1/G3): con este build lleva el perfil al contrato vigente en UN paso explícito.
    * ``evidence_closure_contract_v2``: perfil en el contrato G1/G3 (el vigente en ``11ce071``) -> evidence release.
    * ``evidence_field_lineage_v3``: perfil en el contrato del evidence release v2 (canonical_evidence v2 + assessability v3) -> evidence release de linaje de campo
      (canonical_evidence v3 + assessability v4: procedencia por campo publicado hasta las hojas frontera + clase de contrato de fuente por hoja). SOLO agrega evidencia: exige
      ``CLOSURE_EQUAL`` (el cierre semantico no puede cambiar). Un perfil con cambio de entrada (INPUT_CHANGED / VERSION_AND_INPUT) nunca se absorbe.
    * ``evidence_confidence_v3``: perfil en ``confidence-cuts/v2`` -> ``confidence-cuts/v3`` (el factor ``resolved_provenance`` pasa a medir el ORIGEN DE LOS DATOS: una hoja de identidad UNKNOWN
      con productor y clase de contrato resuelta cuenta como resuelta). Regenera con el MISMO motor sobre el pool LOCAL (sin Datasphere ni reacquisition) y exige ``CLOSURE_EQUAL`` y que TODO el
      perfil salvo ``summary.confidence`` / ``versions.confidence`` quede idéntico (``NON_CONFIDENCE_CONTENT_CHANGED`` es falla dura, cero escrituras). Operador (en la consola del servidor):
          python -m webapp.services.assessment_backfill --regenerate-stale-evidence evidence_confidence_v3 --dry-run
          python -m webapp.services.assessment_backfill --regenerate-stale-evidence evidence_confidence_v3 --apply
  Un perfil cuya diferencia es la de OTRA migración se informa como ``other_migration_applicable`` (con ``applicable_migration``): NO se migra por la equivocada, NO se salta
  ni se encadena. Un perfil ya CURRENT se omite en ambas (idempotentes).

MIGRACIÓN DE JUICIO (cambio de ``RULESET_VERSION``; p. ej. ``judgment-rules/v2``: segunda vía de reconocimiento del contrato publicado + explicación de LVL-06):
    python -m webapp.services.assessment_backfill --backfill-judgment --dry-run
    python -m webapp.services.assessment_backfill --backfill-judgment --apply
  El Juicio es estado DERIVADO del ``profile_json`` ya persistido: este modo lo re-deriva con el motor vigente y reescribe SOLO el Juicio del MISMO sobre (perfil, firmas y versiones de Assessment
  intactos; sin reassess, sin Datasphere, sin reacquisition). Un Juicio de ruleset anterior se clasifica ``judgment_version_stale``; idempotente por ``output_signature``.

  ADOPCION de ``judgment-rules/v3`` (= v2 + resolucion de LVL-05 por RATIFICACION de gobierno INYECTADA; ver ``judgment_decisions``): el operador toma UNA instantanea de SOLO LECTURA del registro
  ``governed-decisions/`` (``--decisions registry``, defecto; fail-closed si no se puede leer; ``--decisions none`` = Juicio v3 sin decisiones), la inyecta al motor y persiste en el Juicio la ENTRADA
  consumida (``decision_inputs``: event_id, technical_id, evidence_ref). ``--dry-run`` informa ``plan.digest`` (ligado a la instantanea), conteos exactos y ``level_changes``, sin escribir. ``--apply`` es
  ASEGURADO (como ``SECURED_MIGRATIONS``): exige ``--confirm-migration judgment_rules_v3``, ``--approval`` firmada del ``plan.digest``, ``--operator``, ``--backup-dir`` y remoto confirmado (o
  ``--allow-local-only``); respalda, firma el diario y aplica por ola/cliente deteniendose ante la primera falla (exit 4/5). Un Juicio v3 que consumio una decision ya revocada/obsoleta
  (``judgment_decision_stale``) NO se reescribe en silencio: se MARCA ``judgment_review: PENDING_REEVALUATION``; ``--reevaluate-decision-stale`` lo re-deriva explicitamente.
    python -m webapp.services.assessment_backfill --backfill-judgment --dry-run
    python -m webapp.services.assessment_backfill --backfill-judgment --apply --confirm-migration judgment_rules_v3 --approval aprobacion.json --operator <id> --backup-dir <dir vacio fuera del almacen>

REEVALUACIÓN GENÉRICA OFFLINE DE EXISTENTES (modo explícito y SEPARADO; reconstruye COMPLETO el assessment gobernado, CURRENT o STALE):
    python3 -m webapp.services.assessment_backfill --reassess-existing --client Kensing --dry-run
    python3 -m webapp.services.assessment_backfill --reassess-existing --client Kensing --apply     (sin --client: todos los existentes)

  Elegible = registro gobernado CURRENT o STALE (cualquier causa de STALE). Ejecuta el MISMO ``assessment_service.generate(entry, force=True)`` (perfil + evidencia + Juicio +
  clasificación de dominio) sobre el pool LOCAL ya persistido, en la MISMA identidad/ruta gobernada, y la vigencia se recalcula canónicamente. NO inicia sesión en Datasphere,
  NO usa el CLI de DSP, NO publica, NO inventaría, NO crea otra identidad ni fuerza CURRENT. Secuencial; una falla individual restaura el sobre ANTERIOR de esa raíz y no corta el lote.
  ⚠ CONFÍA en el pool local persistido: NO afirma que Datasphere esté al día (si el pool quedó viejo, el assessment será CURRENT sobre un pool viejo). Para refrescar el pool hay que
  reexportar fuera de este operador. ``--reassess-current`` (solo CURRENT), ``--regenerate-stale-evidence`` (migraciones puntuales), ``--backfill-judgment`` y
  ``--backfill-domain-classification`` conservan su semántica.

  DÓNDE CORRERLO: en la CONSOLA DEL SERVIDOR (DigitalOcean), donde ``governed-assessments/`` ya está restaurado y el pool del cliente está presente.
  NO desde una máquina de desarrollo y SIN restore() propio (el modo avisa si el estado gobernado parece ausente).
  APLICACION ASEGURADA (``evidence_leaf_materialization_v4``; ``SECURED_MIGRATIONS``): ``--apply`` es FAIL-CLOSED. Flujo: (1) ``--dry-run`` imprime ``plan.digest`` y los clientes; (2) un APROBADOR (distinto del operador) emite
  una aprobacion FIRMADA de ese digest (``python -m webapp.services.migration_approval issue ...``; sus claves publicas, y solo esas, se configuran en ``DANWOR_MIGRATION_APPROVER_KEYS``; APP_OWNER NO autoriza); (3)
      python -m webapp.services.assessment_backfill --regenerate-stale-evidence evidence_leaf_materialization_v4 --apply --confirm-migration evidence_leaf_materialization_v4 \
          --approval aprobacion.json --operator <id> --backup-dir <dir vacio fuera del almacen> [--waves "Pampa,Loma,Illolay;Kensing;Biogenesis"] [--allow-local-only]
  Sin aprobacion valida del plan EXACTO (digest recalculado ahora) => ``outcome=DENIED`` y exit 4 (jamas un dry-run "exitoso"). Antes de escribir se verifica el respaldo (``governed_backup``; sin el, exit 4); luego olas por
  cliente con confirmacion remota por BYTES; la primera falla/abstencion/pendiente remoto DETIENE las olas siguientes (exit 5). Restauracion por objeto: ``--restore-backup <dir> [--object <clave>] --dry-run|--apply --confirm-restore <sello>``.
  REINICIO: el proceso web conserva el sobre viejo en memoria; el refresco es un reinicio CONTROLADO, y solo cuando TODO lo regenerado está en
  ``remote``. Si algún registro quedó ``local`` (PUT a Spaces sin confirmar) el resultado dice ``RESTART_BLOCKED_PENDING_REMOTE``: reiniciar antes
  dejaría que ``restore()`` baje el objeto remoto viejo sobre el archivo local nuevo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from tools.catalog import leaf_materialization_evidence as lme
from webapp.services import assessment_service as svc
from webapp.services import catalog_reads
from webapp.services import durable_backup as dbk
from webapp.services import governed_backup as gb
from webapp.services import migration_approval as ma


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
ABSTAINED = "ABSTAINED"      # solo ``evidence_leaf_materialization_v4``: la migracion se ABSTIENE (falta el pool, difiere su firma, referencias irresolubles, cierre/metodologia distintos): perfil anterior intacto
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
                       commit: Callable[[Any], str], apply: bool, abstain_codes: frozenset = frozenset()) -> None:
    """Regenera en MEMORIA el assessment de ``entry`` (el motor canónico; no escribe), calcula el delta viejo→nuevo con el MISMO perfil que se escribiría y,
    solo con ``apply``, lo escribe por el camino seguro. Muta ``rec``; nunca lanza (una falla individual no corta el lote); ante cualquier falla el registro
    previo queda intacto."""
    try:
        candidate = regenerate_call(entry)                                        # motor canónico en MEMORIA; valida; no escribe
    except svc.EnrichmentError as exc:
        rec.update(action=ABSTAINED if exc.code in abstain_codes else FAILED_ACTION, reason=exc.code)
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


def _retry_pending_local(out: dict, retry_remote: Callable[[str, str], bool], action: str = REGENERATED) -> None:
    """Reintento EXPLÍCITO del PUT remoto de lo que quedó solo en disco (misma API del write-through). ``action`` permite reusarlo
    desde el reassess (acción ``REASSESSED``) sin cambiar los llamadores existentes (default ``REGENERATED``)."""
    for rec in out["records"]:
        if rec.get("action") == action and rec["durable"] == svc.DURABLE_LOCAL:
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
    out["complete"] = apply and counts["failed"] == 0 and counts["pending_remote"] == 0 and counts.get("abstained", 0) == 0


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
        if status.state != svc.CURRENT or status.profile is None or status.pending_migration:     # un CURRENT pendiente de migracion NO se enriquece: primero migra (revalida el Juicio)
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

# ═══════════════════════════ reevaluación EXPLÍCITA de assessments gobernados CURRENT ═══════════════════════════
REASSESSED, WOULD_REASSESS, SKIPPED_NOT_CURRENT = "REASSESSED", "WOULD_REASSESS", "skipped_not_current"


def _reassess_core(entries: Iterable[Any], *, apply: bool, mode: str, eligible_states: tuple, would_action: str, done_action: str, skip_reason: str,
                    status_for: Callable[[Any], svc.AssessmentStatus], generate: Callable[..., svc.AssessmentStatus],
                    governed_path: Callable[[str, str], Any], retry_remote: Callable[[str, str], bool],
                    governed_state_present: Callable[[], bool], remote_enabled: Callable[[], bool], preserve_on_failure: bool = False,
                    load_envelope: Callable[[str, str], Any] = svc._load_envelope, store_envelope: Callable[[dict], str] = svc._store_envelope) -> dict:
    """NÚCLEO compartido de ``--reassess-current`` y ``--reassess-existing``: reevaluación EXPLÍCITA de los assessments gobernados de los estados
    elegibles del alcance pedido (por ``--client``). Re-ejecuta el MISMO motor canónico de generación (``svc.generate(entry, force=True)``) sobre el contexto
    técnico ACTUAL del pool LOCAL y reemplaza la MISMA identidad gobernada (mismo cliente, mismo AM terminal, misma ruta). NO migra, NO parchea JSON, NO
    borra, NO duplica y NO reexporta de Datasphere (recuperar provenance depende de que el pool local ya traiga los flujos productores). Deduplica por
    (cliente, AM terminal), secuencial. Una falla individual no corta el lote. ``preserve_on_failure`` (modo existente): si la regeneración falla o no
    queda persistida, el sobre gobernado ANTERIOR se restaura tal cual (``generate`` escribiría un sobre FAILED sobre el registro bueno)."""
    entries = list(entries)
    out: dict = {"mode": mode, "apply": apply, "dry_run": not apply, "records": [], "environment_warnings": []}
    seen: dict[tuple[str, str], dict] = {}
    states: list[str] = []
    for entry in entries:
        eid, client = str(getattr(entry, "entry_id", "")), str(getattr(entry, "cliente", ""))
        try:
            status = status_for(entry)
        except Exception as exc:                                                  # noqa: BLE001 — una entrada rota no corta el lote
            out["records"].append({"client": client, "root": None, "entry_ids": [eid], "state": None, "action": FAILED_ACTION,
                                   "reason": f"STATUS_ERROR:{type(exc).__name__}", "governed_path": None, "durable": None})
            continue
        states.append(status.state)
        gp = str(governed_path(status.client or client, status.terminal_am)) if status.terminal_am else None
        rec, new = _record_for(seen, out, status, eid, client, governed_path=gp, **({"state_before": status.state} if mode == "reassess-existing" else {}))
        if not new:
            continue
        if status.state not in eligible_states:
            rec["reason"] = skip_reason                                           # SOLO se reevalúan los estados elegibles del modo (explícito)
            continue
        rec["eligible"] = True
        if not apply:
            rec["action"] = would_action                                        # dry-run: candidato, CERO escrituras
            continue
        before = None
        if preserve_on_failure and status.terminal_am:
            try:
                before = load_envelope(status.client or client, status.terminal_am)      # sobre gobernado ANTERIOR (para restaurarlo si la regeneración falla)
            except Exception:                                                             # noqa: BLE001
                before = None

        def _restore_previous(rec=rec, before=before, status=status, client=client) -> None:
            if before is None:
                return
            try:
                if load_envelope(status.client or client, status.terminal_am) != before:
                    store_envelope(before)
                    rec["previous_envelope_restored"] = True
            except Exception:                                                             # noqa: BLE001 — no se oculta: queda registrado en el reporte
                rec["previous_envelope_restored"] = False

        try:
            result = generate(entry, force=True)                                 # MISMO motor canónico; assess sobre el pool; reemplazo de la MISMA identidad
        except Exception as exc:                                                  # noqa: BLE001
            rec.update(action=FAILED_ACTION, reason=f"REASSESS_ERROR:{type(exc).__name__}")
            _restore_previous()
            continue
        durable = getattr(result, "durable", None)
        rec["identity_unchanged"] = (gp is not None and str(governed_path(status.client or client, status.terminal_am)) == gp)
        if result.state == svc.CURRENT and durable and durable != svc.DURABLE_MEMORY:
            rec.update(action=done_action, durable=durable)
        elif result.state == svc.CURRENT and durable == svc.DURABLE_MEMORY:
            rec.update(action=FAILED_ACTION, reason="NOT_PERSISTED_MEMORY_ONLY (registro previo conservado)", durable=durable)
            _restore_previous()
        else:
            rec.update(action=FAILED_ACTION, reason=result.error_code or result.reason or result.state)
            _restore_previous()
    if apply:
        _retry_pending_local(out, retry_remote, action=done_action)
    recs = out["records"]
    count = lambda pred: sum(1 for r in recs if pred(r))                          # noqa: E731
    out["counts"] = {
        "eligible": count(lambda r: r.get("eligible")),
        "would_regenerate": count(lambda r: r["action"] == would_action),        # clave que consume _restart_contract (dry-run)
        "would_reassess": count(lambda r: r["action"] == would_action),
        "reassessed": count(lambda r: r["action"] == done_action),
        "regenerated_remote": count(lambda r: r["action"] == done_action and r["durable"] == svc.DURABLE_REMOTE),
        "regenerated_local": count(lambda r: r["action"] == done_action and r["durable"] == svc.DURABLE_LOCAL),
        "reassessed_remote_confirmed": 0,                                         # el reassess no tiene categoría "ya hecho antes"
        skip_reason: count(lambda r: r["reason"] == skip_reason),
        "failed": count(lambda r: r["action"] == FAILED_ACTION),
        "pending_remote": count(lambda r: r["action"] == done_action and r["durable"] != svc.DURABLE_REMOTE),
    }
    if mode == "reassess-existing":                                              # contrato del operador genérico: current / stale / skipped
        out["counts"].update(current=count(lambda r: r["state"] == svc.CURRENT), stale=count(lambda r: r["state"] == svc.STALE),
                             skipped=count(lambda r: r["reason"] == skip_reason))
    _environment_warnings(out, entries, states, apply, governed_state_present, remote_enabled)
    _restart_contract(out, out["counts"], apply, remote_enabled, "reassessed_remote_confirmed")
    return out


def reassess_selected(entries: Iterable[Any], *, apply: bool = False,
                      status_for: Callable[[Any], svc.AssessmentStatus] = svc.status_for_entry,
                      generate: Callable[..., svc.AssessmentStatus] = svc.generate,
                      governed_path: Callable[[str, str], Any] = svc.governed_path,
                      retry_remote: Callable[[str, str], bool] = svc.retry_remote_persistence,
                      governed_state_present: Callable[[], bool] = _governed_state_present,
                      remote_enabled: Callable[[], bool] = lambda: svc.remote_store.enabled()) -> dict:
    """``--reassess-current``: reevalúa SOLO los assessments gobernados CURRENT (los STALE tienen su propio camino). Ver ``_reassess_core``."""
    return _reassess_core(entries, apply=apply, mode="reassess-current", eligible_states=(svc.CURRENT,), would_action=WOULD_REASSESS, done_action=REASSESSED,
                          skip_reason=SKIPPED_NOT_CURRENT, status_for=status_for, generate=generate, governed_path=governed_path, retry_remote=retry_remote,
                          governed_state_present=governed_state_present, remote_enabled=remote_enabled)


# ═══════════════════════════ reevaluación GENÉRICA OFFLINE de assessments gobernados EXISTENTES (CURRENT + STALE) ════════════════
# ``--reassess-existing``: reconstruye COMPLETO el assessment gobernado (perfil + evidencia + Juicio + clasificación de dominio) contra el pool LOCAL ya
# persistido, vía el MISMO ``assessment_service.generate(entry, force=True)``, sobre la MISMA identidad gobernada, y la vigencia se recalcula de forma canónica
# (jamás se fuerza CURRENT a mano). Cubre el caso real «assessment gobernado STALE cuyo pool local ya es el vigente» que ``--reassess-current`` (solo CURRENT)
# y ``--regenerate-stale-evidence`` (solo migraciones puntuales) no cubren.
# SIN: CLI de DSP, adquisición remota, publicación, inventario, identidad nueva ni un segundo motor de assessment.
# ⚠ ALCANCE: esto CONFÍA en el pool local persistido; NO afirma que Datasphere esté al día (para eso hay que reexportar/refrescar el pool fuera de este operador).
WOULD_REASSESS_EXISTING, REASSESSED_EXISTING, SKIPPED_NOT_EXISTING = "WOULD_REASSESS_EXISTING", "REASSESSED_EXISTING", "skipped_not_existing"


def reassess_existing(entries: Iterable[Any], *, apply: bool = False,
                      status_for: Callable[[Any], svc.AssessmentStatus] = svc.status_for_entry,
                      generate: Callable[..., svc.AssessmentStatus] = svc.generate,
                      governed_path: Callable[[str, str], Any] = svc.governed_path,
                      retry_remote: Callable[[str, str], bool] = svc.retry_remote_persistence,
                      governed_state_present: Callable[[], bool] = _governed_state_present,
                      remote_enabled: Callable[[], bool] = lambda: svc.remote_store.enabled(),
                      load_envelope: Callable[[str, str], Any] = svc._load_envelope, store_envelope: Callable[[dict], str] = svc._store_envelope) -> dict:
    """``--reassess-existing``: reevalúa los assessments gobernados EXISTENTES (CURRENT y STALE; NO los inexistentes/FAILED/no aplica) con
    ``generate(entry, force=True)``. Secuencial, deduplicado por (cliente, AM terminal); una falla individual no corta el lote ni destruye el sobre previo
    (``generate`` solo reemplaza tras un assessment válido; ante falla o persistencia solo-en-memoria el registro anterior sigue vigente). ``apply=False``: CERO escrituras."""
    return _reassess_core(entries, apply=apply, mode="reassess-existing", eligible_states=(svc.CURRENT, svc.STALE), would_action=WOULD_REASSESS_EXISTING,
                          done_action=REASSESSED_EXISTING, skip_reason=SKIPPED_NOT_EXISTING, status_for=status_for, generate=generate, governed_path=governed_path,
                          retry_remote=retry_remote, governed_state_present=governed_state_present, remote_enabled=remote_enabled,
                          preserve_on_failure=True, load_envelope=load_envelope, store_envelope=store_envelope)


VERSION_ONLY_UNTARGETED, INPUT_CHANGED_STALE, VERSION_AND_INPUT_CHANGED = "version_only_untargeted", "input_changed_stale", "version_and_input_changed"
OTHER_MIGRATION_APPLICABLE = "other_migration_applicable"       # STALE VERSION_ONLY cubierto exactamente por OTRA migracion del registro: se reporta, NO se encadena
_STALE_ONLY_REPORTED = (VERSION_ONLY_UNTARGETED, INPUT_CHANGED_STALE, VERSION_AND_INPUT_CHANGED, OTHER_MIGRATION_APPLICABLE)
# Causas de ABSTENCION de ``evidence_leaf_materialization_v4`` (no son fallas del operador: la condicion de elegibilidad no se cumple y el perfil anterior se conserva).
ABSTENTION_CODES = frozenset({*svc.ERROR_MESSAGES, "PREVIOUS_NOT_TARGETED_VERSION_ONLY_STALE", "PREVIOUS_NOT_READABLE", "POOL_SIGNATURE_MISMATCH", "ENGINE_NOT_ASSESSED", "CLOSURE_DRIFT",
                              "CLOSURE_NOT_VERIFIABLE", "TERMINAL_AM_CHANGED", "NON_MATERIALIZATION_CONTENT_CHANGED", "CANDIDATE_VERSIONS_NOT_CURRENT", "CANDIDATE_TARGET_VERSIONS_MISMATCH",
                              "MATERIALIZATION_SECTION_MISSING", "MATERIALIZATION_NOT_EVALUATED", "MATERIALIZATION_CSN_NOT_PROVIDED", "MATERIALIZATION_POOL_SIGNATURE_MISMATCH",
                              "MATERIALIZATION_REFERENCES_INCOMPLETE", "MATERIALIZATION_SOURCE_NOT_RECOVERABLE", "MATERIALIZATION_REFERENCE_UNRESOLVED", "JUDGMENT_METHODOLOGY_DIVERGED"})
CONFIRMATION_REQUIRED = frozenset({svc.MATERIALIZATION_MIGRATION})        # ``--apply`` de estas migraciones exige ``--confirm-migration <nombre>`` ademas de la autorizacion firmada
SECURED_MIGRATIONS = CONFIRMATION_REQUIRED                                  # solo se aplican por ``secured_migrate`` (autorizacion fail-closed + respaldo + olas); ``migrate_stale_evidence(apply=True)`` directo se NIEGA
_PLAN_KEYS = ("previous_artifact_signature", "pool_signature", "candidate_artifact_signature", "judgment_action")


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


def _published_summary(ce: dict | None) -> dict | None:
    pc = (ce or {}).get("published_contract")
    if not pc:
        return None
    ag = (pc.get("associations") or {}).get("aggregates") or {}
    comp = (pc.get("fields") or {}).get("measure_type_composition") or {}
    return {"associations": ag.get("total"), "by_category": ag.get("by_category"), "measure_signatures": comp.get("signature_count"), "measure_families": comp.get("family_count")}


def _count_by(items: list, key: str) -> list:
    out: dict = {}
    for i in items:
        out[i.get(key)] = out.get(i.get(key), 0) + 1
    return [{"value": k, "count": v} for k, v in sorted(out.items(), key=lambda kv: str(kv[0]))]


def compare_evidence_migration(old: dict | None, new_cand: Any, migration: str, old_versions: dict | None, versions: dict) -> dict:
    """Informe CONCISO viejo → nuevo de la migración de evidencia (no vuelca perfiles). Lee el CONTENIDO de ``canonical_evidence``, no la membresía de hechos.
    Informa SIN interpretar calidad: versiones, cierre (miembros agregados y su base), cobertura/confianza/áreas/revisiones/preguntas, y el resumen de cada
    sección nueva de ``canonical_evidence`` v2."""
    new = new_cand.profile
    m = svc.EVIDENCE_MIGRATIONS[migration]
    base = compare_profiles(old, new, ())

    def cov(doc: dict | None, k: str):
        return (((doc or {}).get("summary") or {}).get("coverage") or {}).get(k)
    old_ce, new_ce = (old or {}).get("canonical_evidence"), new.get("canonical_evidence")
    v2_sections = ("closure", "physical_source_boundaries", "published_contract", "naming_evidence", "harmonization_contract", "expression_occurrences",
                   "branch_reconvergence", "view_complexity", "processing_flow_properties")
    presence = lambda ce, sec: "present" if isinstance(ce, dict) and ce.get(sec) not in (None, [], {}) else "absent"          # noqa: E731
    oc, nc = svc.closure_identity(old), svc.closure_identity(new)
    added, removed = svc.closure_delta(oc, nc)
    members = {x["artifact"]: x for x in (((new_ce or {}).get("closure") or {}).get("plane_context") or {}).get("flow_produced_members") or []}
    closure = {"policy": m.closure_policy, "old": len(oc[3]) if oc else None, "new": len(nc[3]) if nc else None,
               "terminal_am_same": (oc or (None,))[0] == (nc or (None,))[0], "root_identity_same": (oc or ())[:3] == (nc or ())[:3],
               "added": [{"artifact": a, "basis": (members.get(a) or {}).get("membership_basis"), "semantic_consumers": (members.get(a) or {}).get("semantic_consumers"),
                          "producer_flows": (members.get(a) or {}).get("producer_flows"), "excluded_before": (members.get(a) or {}).get("excluded_before")} for a in added],
               "removed": list(removed), "drift": bool(removed) or (m.closure_policy == svc.CLOSURE_EQUAL and bool(added))}
    rr = (((new_ce or {}).get("closure") or {}).get("reference_resolution") or {})
    pair = lambda a, b: {"old": a, "new": b, "changed": a != b}                    # noqa: E731
    delta = {"versions": {k: {"old": (old_versions or {}).get(k), "new": versions.get(k)} for k in m.target_keys},
             "pool_signature_same": True, "coverage_strict": base["coverage_strict"], "coverage_inclusive": pair(cov(old, "inclusive"), cov(new, "inclusive")),
             "required_surfaces": pair((((old or {}).get("summary") or {}).get("coverage") or {}).get("required_surfaces"),
                                      (((new or {}).get("summary") or {}).get("coverage") or {}).get("required_surfaces")),
             "confidence": base["confidence"], "review_occurrences": base["review_occurrences"], "open_questions": base["open_questions"], "areas": base["areas"],
             "closure": closure,
             "canonical_evidence": {"old": (old_ce or {}).get("version") if isinstance(old_ce, dict) else "absent", "new": (new_ce or {}).get("version") if isinstance(new_ce, dict) else "absent",
                                    "sections_added": [s for s in v2_sections if presence(old_ce, s) == "absent" and presence(new_ce, s) == "present"]},
             "closure_reference_resolution": {"members_total": rr.get("members_total"), "counts": rr.get("counts")},
             "leaf_provenance": {"old": _provenance_summary(old), "new": _provenance_summary(new)},
             "physical_source_boundaries": [{"leaf": l["leaf"], "boundaries": [{"boundary_entity": b["boundary_entity"], "boundary_class": b["boundary_class"]} for b in l["boundaries"]],
                                             "chain_complete": l["chain_complete"]} for l in ((new_ce or {}).get("physical_source_boundaries") or {}).get("leaves") or []],
             "published_contract": _published_summary(new_ce),
             "harmonization_contract": _count_by([{"value": a["character"]["value"]} for a in ((new_ce or {}).get("harmonization_contract") or {}).get("artifacts") or []], "value"),
             "expression_occurrences": {"counts": ((new_ce or {}).get("expression_occurrences") or {}).get("counts")},
             "branch_reconvergence": {"pairs": (((new_ce or {}).get("branch_reconvergence") or {}).get("counts") or {}).get("pairs")},
             "view_complexity": {"views": ((new_ce or {}).get("view_complexity") or {}).get("count"),
                                 "by_view_type": _count_by(((new_ce or {}).get("view_complexity") or {}).get("views") or [], "view_type")},
             "processing_flow_properties": {"flows": [{"flow_kind": f["flow_kind"], "flow_name": f["flow_name"], "observed": f["observed"]}
                                                     for f in ((new_ce or {}).get("processing_flow_properties") or {}).get("flows") or []]}}
    sec = (new_ce or {}).get(lme.SECTION_KEY) if isinstance(new_ce, dict) else None
    if isinstance(sec, dict):                                              # v4: resumen de la seccion nueva (sin volcarla) y decision de revalidacion del Juicio
        csn = (sec.get("derivation_inputs") or {}).get("csn") or {}
        delta["leaf_materialization"] = {"state": sec.get("evaluation_state"), "counts": sec.get("counts"), "csn_provided": csn.get("provided"),
                                         "references": len(csn.get("references") or ()), "limitations": list(csn.get("limitations") or ())}
    if getattr(new_cand, "notes", None):
        delta["judgment"] = dict(new_cand.notes)
    delta["changed"] = base["changed"] or delta["coverage_inclusive"]["changed"] or bool(added)
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
        if status.pending_migration == migration and status.profile is not None:
            return None                                                           # CURRENT por compatibilidad de transicion: pendiente de ESTA migracion (elegible)
        return SKIPPED_ALREADY_CURRENT
    if status.profile is None:
        return SKIPPED_FAILED_STATE
    if status.stale_reason == svc.STALE_INPUT_CHANGED:
        return INPUT_CHANGED_STALE
    if status.stale_reason == svc.STALE_VERSION_AND_INPUT:
        return VERSION_AND_INPUT_CHANGED
    if status.stale_reason == svc.STALE_VERSION_ONLY and status.stale_targeted == migration:
        return None
    if status.stale_reason == svc.STALE_VERSION_ONLY and status.stale_targeted is not None:
        return OTHER_MIGRATION_APPLICABLE                                         # sin salto ni encadenamiento automatico: se indica cual aplica
    return VERSION_ONLY_UNTARGETED


class ApplyRequiresAuthorization(Exception):
    """``--apply`` de una migracion SENSIBLE sin pasar por ``secured_migrate`` (autorizacion + respaldo + olas): se NIEGA con excepcion, jamas se degrada a dry-run."""


def _plan_item(candidate: Any) -> dict:
    """Identidad del reemplazo que se escribiria (lo que el aprobador firma): llave, firma previa, firma del pool, firma del perfil candidato y accion de Juicio."""
    env = candidate.envelope or {}
    return {"client": candidate.client, "root": candidate.terminal_am, "previous_artifact_signature": candidate.previous_signature,
            "pool_signature": env.get("input_pool_signature"), "candidate_artifact_signature": env.get("assessment_artifact_signature"),
            "judgment_action": (getattr(candidate, "notes", None) or {}).get("judgment_action")}


def build_plan(migration: str, items: list[dict]) -> dict:
    items = sorted(items, key=lambda i: (str(i["client"]), str(i["root"])))
    return {"migration": migration, "environment": ma.environment_fingerprint(svc.governed_dir()), "items": items,
            "clients": sorted({str(i["client"]) for i in items}), "digest": ma.compute_plan_digest(migration, ma.environment_fingerprint(svc.governed_dir()), items)}


def _migration_counts(recs: list[dict]) -> dict:
    count = lambda pred: sum(1 for r in recs if pred(r))                           # noqa: E731
    return {
        "eligible": count(lambda r: r.get("eligible")),
        "version_only_stale": count(lambda r: r.get("eligible")),
        VERSION_ONLY_UNTARGETED: count(lambda r: r["reason"] == VERSION_ONLY_UNTARGETED),
        INPUT_CHANGED_STALE: count(lambda r: r["reason"] == INPUT_CHANGED_STALE),
        VERSION_AND_INPUT_CHANGED: count(lambda r: r["reason"] == VERSION_AND_INPUT_CHANGED),
        OTHER_MIGRATION_APPLICABLE: count(lambda r: r["reason"] == OTHER_MIGRATION_APPLICABLE),
        "current": count(lambda r: r["reason"] == SKIPPED_ALREADY_CURRENT),
        "pending_migration_compat": count(lambda r: r.get("eligible") and r.get("state") == svc.CURRENT and r["action"] != REGENERATED),      # CURRENT por compatibilidad de transicion, aun sin migrar (criterio de retiro: 0)
        "not_yet_assessed": count(lambda r: r["reason"] == SKIPPED_NOT_YET_ASSESSED),
        "failed_state": count(lambda r: r["reason"] == SKIPPED_FAILED_STATE),
        "incompatible": count(lambda r: r["reason"] == SKIPPED_INCOMPATIBLE),
        "not_applicable": count(lambda r: r["reason"] == SKIPPED_NOT_APPLICABLE),
        "would_regenerate": count(lambda r: r["action"] == WOULD_REGENERATE),
        "regenerated_remote": count(lambda r: r["action"] == REGENERATED and r["durable"] == svc.DURABLE_REMOTE),
        "regenerated_local": count(lambda r: r["action"] == REGENERATED and r["durable"] == svc.DURABLE_LOCAL),
        "failed": count(lambda r: r["action"] == FAILED_ACTION),
        "abstained": count(lambda r: r["action"] == ABSTAINED),
        "already_current_remote_confirmed": count(lambda r: r["reason"] == SKIPPED_ALREADY_CURRENT and r.get("remote_state") == svc.REMOTE_CONFIRMED),
        "pending_remote": count(lambda r: (r["action"] == REGENERATED and r["durable"] != svc.DURABLE_REMOTE)
                                or (r["reason"] == SKIPPED_ALREADY_CURRENT and r.get("remote_state") != svc.REMOTE_CONFIRMED)),
        "remaining_stale_not_migrated": count(lambda r: r["reason"] in _STALE_ONLY_REPORTED),
    }


def _confirm_regenerated_bytes(out: dict, retry_remote: Callable[[str, str], bool], verify_remote: Callable[[str, str], str]) -> None:
    """Confirmacion REMOTA POR BYTES de lo recien escrito: ``push_path`` devolviendo True NO alcanza; el objeto remoto debe ser byte a byte el archivo local. Si no coincide
    (tras un reintento) el registro pasa a ``local`` (= pendiente: bloquea el reinicio y detiene las olas)."""
    for rec in out["records"]:
        if rec.get("action") != REGENERATED or rec.get("durable") != svc.DURABLE_REMOTE:
            continue
        try:
            state = verify_remote(rec["client"], rec["root"])
            if state != svc.REMOTE_CONFIRMED and retry_remote(rec["client"], rec["root"]):
                state = verify_remote(rec["client"], rec["root"])
        except Exception:                                                         # noqa: BLE001
            state = svc.REMOTE_UNVERIFIABLE
        rec["remote_state"] = state
        if state != svc.REMOTE_CONFIRMED:
            rec["durable"] = svc.DURABLE_LOCAL
            rec["remote_retry"] = "NOT_CONFIRMED"


def migrate_stale_evidence(entries: Iterable[Any], migration: str, *, apply: bool = False,
                           status_for: Callable[[Any], svc.AssessmentStatus] = svc.status_for_entry,
                           regenerate: Callable[[Any, str], Any] = svc.regenerate_stale_evidence,
                           commit: Callable[[Any], str] = svc.commit_stale_migration,
                           retry_remote: Callable[[str, str], bool] = svc.retry_remote_persistence,
                           verify_remote: Callable[[str, str], str] = svc.verify_remote_durability,
                           governed_state_present: Callable[[], bool] = _governed_state_present,
                           remote_enabled: Callable[[], bool] = lambda: svc.remote_store.enabled(),
                           plan_guard: Mapping[tuple[str, str], dict] | None = None,
                           journal: Callable[[dict], None] | None = None) -> dict:
    """Preview (``apply=False``: corre el motor en memoria y NO escribe; el reporte trae ``plan`` con su ``digest``) o aplicacion de la migracion de evidencia ``migration``
    sobre los assessments gobernados STALE VERSION_ONLY. Sin Datasphere, OAuth, SharePoint ni ``restore()``. Deduplica por (cliente, AM terminal). Una falla individual no corta el lote.
    Una migracion SENSIBLE (``SECURED_MIGRATIONS``) con ``apply=True`` exige ``plan_guard`` (solo lo provee ``secured_migrate``, tras autorizar y respaldar); sin el, ``ApplyRequiresAuthorization``.
    ``plan_guard``: ``{(cliente, AM): item aprobado}``; antes de escribir cada candidato se compara su identidad con el item aprobado y si difiere NO se escribe (``PLAN_CHANGED``).
    ``journal``: recibe los eventos ``COMMITTED``/``FAILED`` por objeto."""
    if apply and migration in SECURED_MIGRATIONS and plan_guard is None:
        raise ApplyRequiresAuthorization(migration)
    target = svc.EVIDENCE_MIGRATIONS[migration]
    versions = svc.current_assessment_versions()
    entries = list(entries)
    out: dict = {"mode": "regenerate-stale-evidence", "migration": migration, "target_keys": list(target.target_keys),
                 "target_versions": {k: versions.get(k) for k in target.target_keys}, "apply": apply, "dry_run": not apply,
                 "records": [], "environment_warnings": []}
    abstain = ABSTENTION_CODES if migration == svc.MATERIALIZATION_MIGRATION else frozenset()
    seen: dict[tuple[str, str], dict] = {}
    states: list[str] = []
    plan_items: list[dict] = []

    def guarded_commit(candidate: Any) -> str:
        item = _plan_item(candidate)
        if plan_guard is not None:
            approved = plan_guard.get((str(item["client"]), str(item["root"])))
            if approved is None or any(approved.get(k) != item.get(k) for k in _PLAN_KEYS):
                raise svc.EnrichmentError("PLAN_CHANGED")                         # el candidato de ahora NO es el aprobado: no se escribe
        key = _store_key(candidate)
        try:
            durable = commit(candidate)
        except Exception as exc:                                                  # noqa: BLE001
            if journal:
                journal({"event": "FAILED", "key": key, "error": getattr(exc, "code", type(exc).__name__)})
            raise
        if journal and durable != svc.DURABLE_MEMORY:
            p = svc.governed_path(candidate.client, candidate.terminal_am)
            journal({"event": "COMMITTED", "key": key, "sha256": hashlib.sha256(p.read_bytes()).hexdigest() if p is not None and p.is_file() else None, "durable": durable})
        return durable

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
            if reason == OTHER_MIGRATION_APPLICABLE:
                rec["applicable_migration"] = status.stale_targeted
            continue
        rec["eligible"] = True
        old_versions = {k: None for k in target.target_keys}
        try:
            env = svc._load_envelope(status.client, status.terminal_am)
            old_versions = (env or {}).get("assessment_versions") or {}
        except Exception:                                                         # noqa: BLE001
            pass

        def regen(e, _rec=rec):
            cand = regenerate(e, migration)
            if cand is not None:
                item = _plan_item(cand)
                _rec["_plan_item"] = item
            return cand
        _regenerate_record(rec, entry, status, regenerate_call=regen,
                           compare=lambda old, cand: compare_evidence_migration(old, cand, migration, old_versions, versions), commit=guarded_commit, apply=apply, abstain_codes=abstain)
        if rec.get("_plan_item") is not None:
            plan_items.append(rec.pop("_plan_item"))
        if rec["action"] == FAILED_ACTION and rec["reason"] in ("PREVIOUS_NOT_TARGETED_VERSION_ONLY_STALE", "PREVIOUS_CHANGED_DURING_RUN"):
            try:                                                                  # alguien lo regeneró por la UI durante la ventana: si ahora es CURRENT, se tolera
                again = status_for(entry)
                if again.state == svc.CURRENT and not again.pending_migration:
                    rec.update(action=SKIP, reason=SKIPPED_ALREADY_CURRENT, delta=None, eligible=False)
            except Exception:                                                     # noqa: BLE001
                pass
    if apply:
        _retry_pending_local(out, retry_remote)
        if plan_guard is not None:                                                # migracion SENSIBLE: lo escrito se confirma por bytes (las demas conservan su contrato)
            _confirm_regenerated_bytes(out, retry_remote, verify_remote)
    _verify_done_records(out, SKIPPED_ALREADY_CURRENT, apply, retry_remote, verify_remote)
    for rec in out["records"]:
        rec.pop("_candidate", None)
        rec.pop("_plan_item", None)
    out["plan"] = build_plan(migration, plan_items)
    out["counts"] = _migration_counts(out["records"])
    _environment_warnings(out, entries, states, apply, governed_state_present, remote_enabled)
    _restart_contract(out, out["counts"], apply, remote_enabled, "already_current_remote_confirmed")
    return out


def _key_of(rec: dict) -> str:
    p, d = svc.governed_path(rec["client"], rec["root"]), svc.governed_dir()
    return p.resolve().relative_to(d.resolve()).as_posix() if p is not None and d is not None else f"{rec['client']}/{rec['root']}"


def _store_key(candidate: Any) -> str:
    p, d = svc.governed_path(candidate.client, candidate.terminal_am), svc.governed_dir()
    return p.resolve().relative_to(d.resolve()).as_posix() if p is not None and d is not None else f"{candidate.client}/{candidate.terminal_am}"


# ═══════════════════════════ APLICACION ASEGURADA de las migraciones sensibles (fail-closed) ═══════════════════════════
# dry-run (plan + digest) → autorizacion firmada vinculada al digest → respaldo verificado → olas por cliente con confirmacion remota por bytes → detener ante la primera falla/abstencion/pendiente.
EXIT_DENIED, EXIT_WAVE_STOPPED = 4, 5
DENIED, BACKUP_NOT_GUARANTEED, REMOTE_REQUIRED, STOPPED_AT_WAVE, APPLIED, RECOVERY_ONLY, WAVES_INVALID = (
    "DENIED", "BACKUP_NOT_GUARANTEED", "REMOTE_REQUIRED", "STOPPED_AT_WAVE", "APPLIED", "RECOVERY_ONLY", "WAVES_INVALID")


def _refusal(plan_report: dict, outcome: str, **extra: Any) -> dict:
    """Salida de un ``--apply`` NO ejecutado: JAMAS parece un dry-run exitoso (``apply`` verdadero, ``dry_run`` falso, ``outcome`` explicito, cero registros escritos, exit 4)."""
    plan = plan_report["plan"]
    return {"mode": "regenerate-stale-evidence", "migration": plan["migration"], "apply": True, "dry_run": False, "secured": True, "outcome": outcome, "applied": False, "writes": 0,
            "plan": {"digest": plan["digest"], "clients": plan["clients"], "items": len(plan["items"])}, "records": [], "counts": {"failed": 0, "abstained": 0, "refused": 1},
            "environment_warnings": [f"APPLY_NOT_PERFORMED: {outcome}"], "restart": RESTART_BLOCKED, "restart_safe": False, "complete": False, **extra}


def _default_waves(plan: dict) -> list[list[str]]:
    """Canarios primero: un cliente por ola, de menos a mas sobres (empate: nombre)."""
    n = {c: sum(1 for i in plan["items"] if i["client"] == c) for c in plan["clients"]}
    return [[c] for c in sorted(n, key=lambda c: (n[c], c))]


def parse_waves(spec: str, plan: dict) -> list[list[str]]:
    """``"A,B;C"`` = ola 1 {A, B}, ola 2 {C}. Debe cubrir EXACTAMENTE los clientes del plan (sin repetir)."""
    waves = [[c.strip() for c in grp.split(",") if c.strip()] for grp in spec.split(";") if grp.strip()]
    flat = [c for w in waves for c in w]
    if len(flat) != len(set(flat)) or set(flat) != set(plan["clients"]):
        raise ValueError(f"--waves debe cubrir exactamente los clientes del plan sin repetirlos: {plan['clients']}")
    return waves


def secured_migrate(entries: Iterable[Any], migration: str, *, authorizer: Callable[[str, dict], "ma.Authorization"], backup_dir: Any, allow_local_only: bool = False,
                    waves: list[list[str]] | None = None, run_id: str | None = None, **kw: Any) -> dict:
    """Aplica ``migration`` SOLO si: (1) hay autorizacion valida del plan EXACTO recalculado ahora (``plan_digest``), (2) el remoto esta disponible (salvo ``allow_local_only`` explicito),
    (3) el respaldo verificable del conjunto afectado quedo garantizado, y (4) cada ola (cliente) cierra limpia: sin fallas, abstenciones ni pendientes remotos, con confirmacion remota por bytes.
    Cualquier incumplimiento deja ``outcome`` explicito (exit 4/5) y NO escribe nada mas. Sin items en el plan: solo recupera pendientes remotos (``RECOVERY_ONLY``), sin regenerar."""
    entries = list(entries)
    remote_enabled = kw.get("remote_enabled", lambda: svc.remote_store.enabled())
    read_kw = {k: v for k, v in kw.items() if k not in ("commit",)}
    plan_report = migrate_stale_evidence(entries, migration, apply=False, **read_kw)          # fase 1: plan en memoria, CERO escrituras
    plan = plan_report["plan"]
    if not plan["items"]:
        out = migrate_stale_evidence(entries, migration, apply=True, plan_guard={}, **kw)      # nada que regenerar: solo reintento/verificacion remota de lo ya hecho
        out.update(secured=True, outcome=RECOVERY_ONLY, applied=False)
        return out
    auth = authorizer(migration, plan)
    if not auth.granted:
        return _refusal(plan_report, DENIED, authorization=auth.as_dict())
    if not remote_enabled() and not allow_local_only:
        return _refusal(plan_report, REMOTE_REQUIRED, authorization=auth.as_dict())
    governed = svc.governed_dir()
    targets = [{"client": i["client"], "root": i["root"], "path": svc.governed_path(i["client"], i["root"])} for i in plan["items"]]
    try:
        if not backup_dir:
            raise gb.BackupError("BACKUP_DIR_REQUIRED")
        if governed is None or any(t["path"] is None for t in targets):
            raise gb.BackupError("NO_GOVERNED_STORE")
        manifest = gb.create_backup(Path(backup_dir), targets, governed_dir=governed, migration=migration, plan_digest=plan["digest"], environment=plan["environment"],
                                    remote_read=svc.remote_store.read_object_bytes if remote_enabled() else None, require_remote=not allow_local_only)
    except gb.BackupError as exc:
        return _refusal(plan_report, BACKUP_NOT_GUARANTEED, authorization=auth.as_dict(), backup_error=exc.code)
    backup_dir = Path(backup_dir)
    remote_info = None
    if remote_enabled():                                                           # copia DURABLE en Spaces: sellada y confirmada por bytes ANTES de modificar el primer Assessment
        run_id = run_id or dbk.new_run_id(plan["digest"])
        try:
            remote_info = dbk.publish(backup_dir, migration, run_id, approval=auth.document)
        except dbk.DurableBackupError as exc:
            return _refusal(plan_report, BACKUP_NOT_GUARANTEED, authorization=auth.as_dict(), backup_error=exc.code, backup_detail=exc.detail)
    guard = {(str(i["client"]), str(i["root"])): i for i in plan["items"]}
    order = waves or _default_waves(plan)
    sec = {"plan_digest": plan["digest"], "authorization": auth.as_dict(), "backup": {"dir": str(backup_dir), "manifest_sha256": manifest["manifest_sha256"], "objects": len(manifest["objects"]),
                                                                                       "remote": remote_info}, "waves": []}
    out: dict = {"mode": "regenerate-stale-evidence", "migration": migration, "apply": True, "dry_run": False, "secured": True, "records": [], "environment_warnings": [],
                 "target_keys": plan_report["target_keys"], "target_versions": plan_report["target_versions"]}
    seq, jfail = [0], []

    def journal(ev: dict) -> None:
        rec = gb.append_journal(backup_dir, ev)                                    # local: si falla se propaga (no se sigue sin registro)
        if remote_info is not None:
            seq[0] += 1
            try:
                dbk.publish_event(migration, run_id, seq[0], rec)                  # remoto: un objeto inmutable por evento; una falla detiene las olas (restore necesita los COMMITTED)
            except dbk.DurableBackupError as exc:
                jfail.append(exc.code)
    try:                                                                            # el registro de la autorizacion debe quedar PERSISTIDO (local y, si hay remoto, remoto) antes de tocar un Assessment
        journal({"event": "AUTHORIZED", "approver": auth.approver, "operator": auth.operator, "duties_mode": auth.duties_mode, "duties_collapsed": auth.duties_collapsed,
                 "operator_assurance": auth.operator_assurance, "attestation": auth.attestation, "identity_config": auth.identity_config, "plan_digest": plan["digest"],
                 "approval_sha256": hashlib.sha256(ma.canonical(auth.document)).hexdigest() if auth.document is not None else None})      # registro auditable ANTES de la primera escritura
    except Exception:                                                              # noqa: BLE001 — sin detalle (podria contener rutas); cero escrituras sobre Assessments
        return _refusal(plan_report, BACKUP_NOT_GUARANTEED, authorization=auth.as_dict(), backup_error="AUDIT_LOG_FAILED")
    if jfail:
        return _refusal(plan_report, BACKUP_NOT_GUARANTEED, authorization=auth.as_dict(), backup_error="JOURNAL_REMOTE_FAILED")
    stopped = None
    for n, wave in enumerate(order, 1):
        wanted = {c.lower() for c in wave}
        sub = [e for e in entries if str(getattr(e, "cliente", "")).strip().lower() in wanted]
        res = migrate_stale_evidence(sub, migration, apply=True, plan_guard=guard, journal=journal, **kw)
        recs = res["records"]
        out["records"].extend(recs)
        out["environment_warnings"].extend(w for w in res["environment_warnings"] if w not in out["environment_warnings"])
        for r in recs:
            if r.get("action") == REGENERATED:
                journal({"event": "REMOTE_CONFIRMED" if r.get("remote_state") == svc.REMOTE_CONFIRMED else "REMOTE_PENDING", "key": _key_of(r)})
        c = res["counts"]
        clean = not (c["failed"] or c["abstained"] or jfail or (remote_enabled() and c["pending_remote"]))      # sin remoto (solo con allow_local_only) lo local es lo maximo alcanzable
        sec["waves"].append({"wave": n, "clients": wave, "status": "CLEAN" if clean else "STOPPED", "regenerated": c["regenerated_remote"] + c["regenerated_local"],
                             "failed": c["failed"], "abstained": c["abstained"], "pending_remote": c["pending_remote"]})
        if not clean:
            stopped = n
            break
    if not stopped:                                                                # recuperacion: lo ya migrado (por una corrida anterior) se re-verifica por bytes y se reintenta su PUT pendiente
        in_waves = {c.lower() for w in order for c in w}
        rest = [e for e in entries if str(getattr(e, "cliente", "")).strip().lower() not in in_waves]
        if rest:
            res = migrate_stale_evidence(rest, migration, apply=True, plan_guard=guard, journal=journal, **kw)
            out["records"].extend(res["records"])
            out["environment_warnings"].extend(w for w in res["environment_warnings"] if w not in out["environment_warnings"])
            sec["recovery_pass"] = {"records": len(res["records"]), "pending_remote": res["counts"]["pending_remote"], "failed": res["counts"]["failed"]}
    sec["not_attempted_waves"] = [w for w in order[(stopped or len(order)):]]
    sec["journal_remote_failures"] = list(jfail)
    out["secured_apply"] = sec
    out["plan"] = {"digest": plan["digest"], "clients": plan["clients"], "items": len(plan["items"])}
    out["counts"] = _migration_counts(out["records"])
    _restart_contract(out, out["counts"], True, remote_enabled, "already_current_remote_confirmed")
    out["outcome"] = STOPPED_AT_WAVE if stopped else APPLIED
    out["applied"] = True
    out["complete"] = bool(out["complete"]) and not stopped
    if stopped:
        out["restart"], out["restart_safe"] = RESTART_BLOCKED, False
        out["environment_warnings"].append(f"STOPPED_AT_WAVE_{stopped}: las olas siguientes NO se ejecutaron; resolver, regenerar el plan y volver a autorizar")
    return out


# ═══════════════════════════ RESTAURACION por objeto desde un respaldo ═══════════════════════════
def restore_from_backup(backup_dir: Any, *, apply: bool = False, keys: list[str] | None = None, confirm: str = "",
                        authorizer: Callable[[str, dict], "ma.Authorization"] | None = None) -> dict:
    """Restaura POR OBJETO los sobres del respaldo ``backup_dir`` (dry-run por omision). Solo escribe lo que la migracion escribio (sha256 del diario) o lo ya restaurado; cualquier otro
    contenido local/remoto => ``CONFLICT_CONCURRENT_CHANGE`` y no se toca. Exige que el respaldo sea de ESTE entorno y, con ``apply``: ``confirm`` = prefijo del sello del manifiesto Y una
    AUTORIZACION firmada (``authorizer``; migracion ``restore:<migracion>``, plan_digest = sello del manifiesto, alcance = clientes del manifiesto); sin ella => ``RESTORE_DENIED``."""
    backup_dir = Path(backup_dir)
    governed = svc.governed_dir()
    try:
        m = gb.load_manifest(backup_dir)
    except gb.BackupError as exc:
        return {"mode": "restore-backup", "apply": apply, "dry_run": not apply, "outcome": exc.code, "restored": 0, "results": [], "counts": {"failed": 1}}
    base = {"mode": "restore-backup", "apply": apply, "dry_run": not apply, "migration": m.get("migration"), "manifest_sha256": m.get("manifest_sha256"), "results": []}
    if governed is None or m.get("environment") != ma.environment_fingerprint(governed):
        return {**base, "outcome": "ENVIRONMENT_MISMATCH", "counts": {"failed": 1}}
    if apply and (len(confirm) < 12 or not str(m.get("manifest_sha256", "")).startswith(confirm)):
        return {**base, "outcome": "CONFIRMATION_REQUIRED", "counts": {"failed": 1}, "required": "--confirm-restore <primeros 12+ caracteres de manifest_sha256>"}
    if apply:
        auth = (authorizer or ma.deny_all)(f"restore:{m.get('migration')}", {"digest": m.get("manifest_sha256"), "clients": sorted({str(o["client"]) for o in m["objects"]})})
        if not auth.granted:
            return {**base, "outcome": "RESTORE_DENIED", "applied": False, "authorization": auth.as_dict(), "counts": {"failed": 1}}
    remote = svc.remote_store.enabled()
    wanted = keys or [o["key"] for o in m["objects"]]
    for key in wanted:
        base["results"].append(gb.restore_object(backup_dir, key, governed_dir=governed, apply=apply, push_remote=svc.remote_store.push_path if remote else None,
                                                 read_remote=svc.remote_store.read_object_bytes if remote else None))
    if apply:
        svc.reset_memory_cache()                                                      # este proceso no debe seguir sirviendo el sobre migrado desde memoria (el web se reinicia aparte)
    st = [r["status"] for r in base["results"]]
    counts = {s: st.count(s) for s in sorted(set(st))}
    counts["failed"] = sum(counts.get(s, 0) for s in ("BACKUP_OBJECT_CORRUPT", "RESTORE_FAILED", "UNKNOWN_OBJECT"))
    counts["conflicts"] = counts.get("CONFLICT_CONCURRENT_CHANGE", 0)
    counts["pending_remote"] = counts.get("RESTORE_REMOTE_PENDING", 0)
    return {**base, "outcome": "DONE", "counts": counts}


def restore_from_remote_run(migration: str, run_id: str, staging_dir: Any, *, apply: bool = False, keys: list[str] | None = None, confirm: str = "",
                            authorizer: Callable[[str, dict], "ma.Authorization"] | None = None) -> dict:
    """Baja el run SELLADO ``migration-backups/<migracion>/<run_id>/`` de Spaces a ``staging_dir`` (vacio; verifica sello, manifiesto y cada hash) y restaura desde ahi con las MISMAS reglas
    (por objeto, sin pisar cambios, confirmacion + autorizacion firmada). Sirve cuando el respaldo local del contenedor ya no existe."""
    try:
        dbk.fetch(migration, run_id, Path(staging_dir))
    except dbk.DurableBackupError as exc:
        return {"mode": "restore-backup", "apply": apply, "dry_run": not apply, "outcome": f"REMOTE_BACKUP_UNUSABLE:{exc.code}", "results": [], "counts": {"failed": 1}}
    out = restore_from_backup(staging_dir, apply=apply, keys=keys, confirm=confirm, authorizer=authorizer)
    out["remote_run"] = {"migration": migration, "run_id": run_id}
    return out


JUDGMENT_V3_MIGRATION = "judgment_rules_v3"      # nombre de la migracion de ADOPCION del contrato de Juicio v3 (plan firmado + respaldo + diario); el apply por CLI SOLO existe por ``secured_adopt_judgment``


def _lvl(j: dict | None) -> tuple | None:
    return (j.get("status"), j.get("level"), j.get("level_rule")) if isinstance(j, dict) else None


def backfill_judgment(*, apply: bool = False, client: str = "", snapshot: Any = None, reevaluate_decision_stale: bool = False) -> dict:
    """Materializa el Juicio metodológico DERIVADO en los sobres gobernados que lo tengan ausente o con un contrato de
    Juicio anterior, SIN reassess ni adquisición: se deriva del ``profile_json`` YA persistido con el motor canónico y se
    reescribe la MISMA identidad gobernada (perfil, firmas del artefacto y versiones de Assessment intactos). Idempotente
    por ``output_signature``: un segundo ``--apply`` no hace trabajo. ``apply=False`` solo informa.

    ``snapshot`` (judgment-rules/v3): instantánea VERIFICABLE de decisiones de gobierno (``judgment_decisions.Snapshot``) que el operador INYECTA al motor; ``None`` = sin decisiones.
    Un Juicio v3 que consumió una decisión ya revocada/obsoleta (``judgment_decision_stale``) NO se reescribe en silencio: queda MARCADO ``PENDING_REEVALUATION`` (clave hermana
    ``judgment_review``); solo ``reevaluate_decision_stale=True`` lo reemplaza. ``plan`` lista (para firmar) cada sobre que cambiaría y la instantánea que lo justifica."""
    from webapp.services import judgment_decisions as jd
    wanted = client.strip().lower()
    records, failed, pending, plan_items = [], 0, 0, []
    snap = snapshot if snapshot is not None else jd.empty_snapshot()
    for env, path in svc.iter_governed_envelopes():
        if wanted and str(env.get("client", "")).strip().lower() != wanted:
            continue
        cls = svc.classify_judgment_decisions(env, snap)
        action, fresh = cls["action"], cls["fresh"]
        persisted = env.get("judgment")
        rec = {"client": env.get("client"), "terminal_am": env.get("terminal_am"), "action": action,
               "assessment_status": env.get("status"),
               "from_version": list(svc.judgment_versions_of(persisted)) if persisted else None,
               "to_version": list(svc.judgment_versions_of(fresh)) if fresh else None,
               "from_level": list(_lvl(persisted) or ()) or None, "to_level": list(_lvl(fresh) or ()) or None,
               "decision_inputs": ({"signature": (fresh.get("decision_inputs") or {}).get("signature"), "consumed_event_ids": [i["event_id"] for i in jd.consumed(fresh)]} if fresh else None)}
        if cls["lost"]:
            rec["lost_decisions"] = cls["lost"]
        rewrite = action in (svc.JDG_MISSING, svc.JDG_VERSION_STALE, svc.JDG_CONTENT_STALE) or (action == svc.JDG_DECISION_STALE and reevaluate_decision_stale)
        mark = action == svc.JDG_DECISION_STALE and not reevaluate_decision_stale
        if rewrite or mark:
            plan_items.append({"client": str(env.get("client")), "root": str(env.get("terminal_am")), "operation": "REPLACE_JUDGMENT" if rewrite else "MARK_PENDING_REEVALUATION",
                               "previous_ruleset": (persisted or {}).get("ruleset_version"), "previous_output_signature": (persisted or {}).get("output_signature"),
                               "new_ruleset": (fresh or {}).get("ruleset_version"), "new_output_signature": (fresh or {}).get("output_signature") if rewrite else None,
                               "new_level": list(_lvl(fresh) or ()) if rewrite else None, "decision_inputs_signature": ((fresh or {}).get("decision_inputs") or {}).get("signature") if rewrite else None,
                               "lost_event_ids": sorted(l["event_id"] for l in cls["lost"]) if mark else None, "snapshot_digest": snap.digest,
                               # claves que ``ma.compute_plan_digest`` SI firma: la aprobacion queda atada al Juicio previo, al Juicio/entrada resultante y a la INSTANTANEA del registro
                               "previous_artifact_signature": (persisted or {}).get("output_signature"), "pool_signature": snap.digest,
                               "candidate_artifact_signature": (fresh or {}).get("output_signature") if rewrite else "MARK:" + ",".join(sorted(l["event_id"] for l in cls["lost"])),
                               "judgment_action": f"{'REPLACE_JUDGMENT' if rewrite else 'MARK_PENDING_REEVALUATION'}:{((fresh or {}).get('decision_inputs') or {}).get('signature') if rewrite else ''}"})
        if (rewrite or mark) and apply:
            try:
                if rewrite:
                    durable = svc.apply_judgment_backfill(env, fresh)
                    rec["action"] = "updated"
                else:
                    durable = svc.apply_judgment_review_marker(env, jd.review_marker(persisted, cls["lost"], snap))
                    rec["action"] = "marked_pending_reevaluation"
                rec["durable"] = durable
                if durable != svc.DURABLE_REMOTE:
                    pending += 1
            except Exception as exc:                                    # noqa: BLE001 — una falla NO corrompe el sobre previo
                svc.log.exception("backfill de Juicio: fallo al escribir %s", path)
                rec["action"] = "failed"
                rec["error"] = type(exc).__name__
                failed += 1
        elif rewrite:
            rec["action"] = "would_update"
        elif mark:
            rec["action"] = "would_mark_pending_reevaluation"
        records.append(rec)

    def count(pred):
        return sum(1 for r in records if pred(r))
    changed = lambda r: r["action"] in ("would_update", "updated") and r.get("from_level") is not None and r.get("from_level") != r.get("to_level")   # noqa: E731
    counts = {
        "eligible": len(records),
        "already_current": count(lambda r: r["action"] == svc.JDG_CURRENT),
        "no_profile": count(lambda r: r["action"] == svc.JDG_NO_PROFILE),
        "judgment_missing": count(lambda r: r.get("from_version") is None and r["action"] in ("would_update", "updated")),
        "judgment_version_stale": count(lambda r: r.get("from_version") is not None
                                        and tuple(r.get("to_version") or ()) != tuple(r.get("from_version") or ())
                                        and r["action"] in ("would_update", "updated")),
        "decision_stale": count(lambda r: r.get("lost_decisions")),
        "would_update": count(lambda r: r["action"] == "would_update"),
        "would_mark_pending_reevaluation": count(lambda r: r["action"] == "would_mark_pending_reevaluation"),
        "updated": count(lambda r: r["action"] == "updated"),
        "marked_pending_reevaluation": count(lambda r: r["action"] == "marked_pending_reevaluation"),
        "level_changes": count(changed),
        "decision_resolutions": count(lambda r: r["action"] in ("would_update", "updated") and (r.get("decision_inputs") or {}).get("consumed_event_ids")),
        "failed": failed,
        "pending_remote": pending,
    }
    # restart-safe: tras un apply exitoso, un segundo apply ve todo ``already_current`` (cero trabajo)
    counts["complete"] = (failed == 0 and (not apply or (counts["would_update"] == 0 and counts["would_mark_pending_reevaluation"] == 0)))
    return {"mode": "backfill-judgment", "apply": apply, "dry_run": not apply, "records": records, "counts": counts,
            "decision_snapshot": snap.summary(), "plan": build_plan(JUDGMENT_V3_MIGRATION, plan_items),
            "level_changes": [{"client": r["client"], "terminal_am": r["terminal_am"], "from": r["from_level"], "to": r["to_level"],
                               "event_ids": (r.get("decision_inputs") or {}).get("consumed_event_ids")} for r in records if changed(r)],
            "environment_warnings": ["sin almacén durable (solo memoria): el backfill no persiste"] if svc.governed_dir() is None else []}


def secured_adopt_judgment(*, authorizer: Callable[[str, dict], "ma.Authorization"], backup_dir: Any, client: str = "", snapshot: Any = None, allow_local_only: bool = False,
                           waves: list[list[str]] | None = None, run_id: str | None = None, reevaluate_decision_stale: bool = False,
                           remote_enabled: Callable[[], bool] | None = None) -> dict:
    """ADOPCION controlada de ``judgment-rules/v3`` (``--backfill-judgment --apply``): SOLO si hay (1) autorizacion firmada del plan EXACTO recalculado ahora (incluye el digest de la
    instantanea de decisiones), (2) remoto disponible (salvo ``allow_local_only``), (3) respaldo verificable previo (local + copia durable sellada en Spaces) y (4) cada ola (cliente) cierra
    limpia con el sobre reescrito CONFIRMADO. Cualquier incumplimiento => ``outcome`` explicito, exit 4/5 y NO se escribe nada mas. Un sobre cuya derivacion difiere de la planificada (deriva del
    registro o del perfil entre el plan y la escritura) NO se escribe. Diario por objeto compatible con ``--restore-backup`` (COMMITTED con sha256)."""
    from webapp.services import judgment_decisions as jd
    remote_enabled = remote_enabled or (lambda: svc.remote_store.enabled())
    snap = snapshot if snapshot is not None else jd.empty_snapshot()
    plan_report = backfill_judgment(apply=False, client=client, snapshot=snap, reevaluate_decision_stale=reevaluate_decision_stale)
    plan = plan_report["plan"]
    base = {"mode": "backfill-judgment", "migration": JUDGMENT_V3_MIGRATION, "apply": True, "dry_run": False, "secured": True, "decision_snapshot": snap.summary(),
            "plan": {"digest": plan["digest"], "clients": plan["clients"], "items": len(plan["items"])}}

    def refuse(outcome: str, **extra: Any) -> dict:
        return {**base, "outcome": outcome, "applied": False, "writes": 0, "records": [], "counts": {"failed": 0, "refused": 1}, "environment_warnings": [f"APPLY_NOT_PERFORMED: {outcome}"],
                "complete": False, **extra}
    if not plan["items"]:
        return {**base, "outcome": "NOTHING_TO_DO", "applied": False, "writes": 0, "records": [], "counts": plan_report["counts"], "complete": plan_report["counts"]["complete"]}
    auth = authorizer(JUDGMENT_V3_MIGRATION, plan)
    if not auth.granted:
        return refuse(DENIED, authorization=auth.as_dict())
    if not remote_enabled() and not allow_local_only:
        return refuse(REMOTE_REQUIRED, authorization=auth.as_dict())
    governed = svc.governed_dir()
    targets = [{"client": i["client"], "root": i["root"], "path": svc.governed_path(i["client"], i["root"])} for i in plan["items"]]
    try:
        if not backup_dir:
            raise gb.BackupError("BACKUP_DIR_REQUIRED")
        if governed is None or any(t["path"] is None for t in targets):
            raise gb.BackupError("NO_GOVERNED_STORE")
        manifest = gb.create_backup(Path(backup_dir), targets, governed_dir=governed, migration=JUDGMENT_V3_MIGRATION, plan_digest=plan["digest"], environment=plan["environment"],
                                    remote_read=svc.remote_store.read_object_bytes if remote_enabled() else None, require_remote=not allow_local_only)
    except gb.BackupError as exc:
        return refuse(BACKUP_NOT_GUARANTEED, authorization=auth.as_dict(), backup_error=exc.code)
    backup_dir = Path(backup_dir)
    remote_info = None
    if remote_enabled():
        run_id = run_id or dbk.new_run_id(plan["digest"])
        try:
            remote_info = dbk.publish(backup_dir, JUDGMENT_V3_MIGRATION, run_id, approval=auth.document)
        except dbk.DurableBackupError as exc:
            return refuse(BACKUP_NOT_GUARANTEED, authorization=auth.as_dict(), backup_error=exc.code, backup_detail=exc.detail)
    seq, jfail = [0], []

    def journal(ev: dict) -> None:
        rec = gb.append_journal(backup_dir, ev)
        if remote_info is not None:
            seq[0] += 1
            try:
                dbk.publish_event(JUDGMENT_V3_MIGRATION, run_id, seq[0], rec)
            except dbk.DurableBackupError as exc:
                jfail.append(exc.code)
    try:
        journal({"event": "AUTHORIZED", "approver": auth.approver, "operator": auth.operator, "duties_mode": auth.duties_mode, "duties_collapsed": auth.duties_collapsed,
                 "operator_assurance": auth.operator_assurance, "attestation": auth.attestation, "identity_config": auth.identity_config, "plan_digest": plan["digest"],
                 "decision_snapshot_digest": snap.digest,
                 "approval_sha256": hashlib.sha256(ma.canonical(auth.document)).hexdigest() if auth.document is not None else None})
    except Exception:                                                              # noqa: BLE001
        return refuse(BACKUP_NOT_GUARANTEED, authorization=auth.as_dict(), backup_error="AUDIT_LOG_FAILED")
    if jfail:
        return refuse(BACKUP_NOT_GUARANTEED, authorization=auth.as_dict(), backup_error="JOURNAL_REMOTE_FAILED")
    guard = {(i["client"], i["root"]): i for i in plan["items"]}
    order = waves or _default_waves(plan)
    out_records, wave_log, stopped = [], [], None
    for n, wave in enumerate(order, 1):
        wanted = {c.lower() for c in wave}
        failed = pending = done = 0
        for env, path in svc.iter_governed_envelopes():
            key = (str(env.get("client")), str(env.get("terminal_am")))
            if str(env.get("client", "")).strip().lower() not in wanted or key not in guard:
                continue
            item = guard[key]
            cls = svc.classify_judgment_decisions(env, snap)
            rec = {"client": key[0], "terminal_am": key[1], "operation": item["operation"]}
            try:
                if item["operation"] == "REPLACE_JUDGMENT":
                    drift = (cls["fresh"] or {}).get("output_signature") != item["new_output_signature"]
                else:
                    drift = cls["action"] != svc.JDG_DECISION_STALE
                if drift:
                    raise RuntimeError("PLAN_DRIFT")
                if item["operation"] == "REPLACE_JUDGMENT":
                    durable = svc.apply_judgment_backfill(env, cls["fresh"])
                else:
                    durable = svc.apply_judgment_review_marker(env, jd.review_marker(env.get("judgment"), cls["lost"], snap))
                p = svc.governed_path(*key)
                okey = _key_of({"client": key[0], "root": key[1]})
                journal({"event": "COMMITTED", "key": okey, "sha256": hashlib.sha256(p.read_bytes()).hexdigest() if p is not None and p.is_file() else None, "durable": durable})
                rec.update(action="applied", durable=durable)
                done += 1
                if durable == svc.DURABLE_REMOTE:
                    journal({"event": "REMOTE_CONFIRMED", "key": okey})
                elif remote_enabled():
                    pending += 1
                    journal({"event": "REMOTE_PENDING", "key": okey})
            except Exception as exc:                                               # noqa: BLE001
                failed += 1
                rec.update(action="failed", error=str(exc.args[0]) if isinstance(exc, RuntimeError) and exc.args else type(exc).__name__)
                journal({"event": "FAILED", "key": _key_of({"client": key[0], "root": key[1]}), "error": rec["error"]})
            out_records.append(rec)
        clean = not (failed or pending or jfail)
        wave_log.append({"wave": n, "clients": wave, "status": "CLEAN" if clean else "STOPPED", "applied": done, "failed": failed, "pending_remote": pending})
        if not clean:
            stopped = n
            break
    counts = {"applied": sum(1 for r in out_records if r["action"] == "applied"), "failed": sum(1 for r in out_records if r["action"] == "failed"),
              "pending_remote": sum(w["pending_remote"] for w in wave_log)}
    out = {**base, "outcome": STOPPED_AT_WAVE if stopped else APPLIED, "applied": True, "records": out_records, "counts": counts,
           "secured_apply": {"plan_digest": plan["digest"], "authorization": auth.as_dict(), "backup": {"dir": str(backup_dir), "manifest_sha256": manifest["manifest_sha256"],
                                                                                                         "objects": len(manifest["objects"]), "remote": remote_info},
                             "waves": wave_log, "not_attempted_waves": order[(stopped or len(order)):], "journal_remote_failures": list(jfail)},
           "complete": not stopped and not counts["failed"] and not counts["pending_remote"], "environment_warnings": []}
    if stopped:
        out["environment_warnings"].append(f"STOPPED_AT_WAVE_{stopped}: las olas siguientes NO se ejecutaron; resolver, regenerar el plan y volver a autorizar")
    return out


def revalidate_judgments(*, apply: bool = False, client: str = "") -> dict:
    """``--revalidate-judgment``: REVALIDA (no recalifica) los Juicios v2 persistidos cuya firma de perfil quedo desactualizada tras un cambio de evidencia del perfil (p. ej. la migracion
    ``evidence_leaf_materialization_v4``). Para cada sobre se deriva el Juicio con el MISMO motor v2 y se compara su parte METODOLOGICA (nivel, estado, cierre, fortalezas, razones, revisiones,
    limitaciones, calificadores, CTA...; todo salvo ``source_assessment`` y ``output_signature``) con la persistida: identica => se reconoce la nueva firma del perfil (``judgment_signature_rebound``);
    cualquier diferencia => ``judgment_methodology_diverged``: NO se reescribe y se informa que claves difieren (decision humana). No crea Juicios ausentes ni migra contratos viejos (eso es
    ``--backfill-judgment``). Solo reescribe el Juicio del MISMO sobre (perfil, firmas y versiones intactos). Idempotente. ``apply=False`` no escribe."""
    wanted = client.strip().lower()
    records, failed, pending = [], 0, 0
    for env, path in svc.iter_governed_envelopes():
        if wanted and str(env.get("client", "")).strip().lower() != wanted:
            continue
        action, fresh = svc.classify_judgment_revalidation(env)
        prev = env.get("judgment") or {}
        rec = {"client": env.get("client"), "terminal_am": env.get("terminal_am"), "action": action,
               "previous": {"status": prev.get("status"), "level": prev.get("level"), "level_rule": prev.get("level_rule")} if prev else None,
               "revalidated": {"status": (fresh or {}).get("status"), "level": (fresh or {}).get("level"), "level_rule": (fresh or {}).get("level_rule")} if fresh else None}
        if action == svc.JDG_METHODOLOGY_DIVERGED:
            rec["methodology_diff_keys"] = svc.judgment_methodology_diff(prev, svc._derive_judgment(env.get("profile_json")))
        if action == svc.JDG_SIGNATURE_REBOUND and apply:
            try:
                durable = svc.apply_judgment_backfill(env, fresh)
                rec.update(action="rebound", durable=durable)
                if durable != svc.DURABLE_REMOTE:
                    pending += 1
            except Exception as exc:                                    # noqa: BLE001 — una falla NO corrompe el sobre previo
                svc.log.exception("revalidacion de Juicio: fallo al escribir %s", path)
                rec.update(action="failed", error=type(exc).__name__)
                failed += 1
        elif action == svc.JDG_SIGNATURE_REBOUND:
            rec["action"] = "would_rebind"
        records.append(rec)
    count = lambda pred: sum(1 for r in records if pred(r))              # noqa: E731
    counts = {"examined": len(records), "already_current": count(lambda r: r["action"] == svc.JDG_CURRENT), "would_rebind": count(lambda r: r["action"] == "would_rebind"),
              "rebound": count(lambda r: r["action"] == "rebound"), "methodology_diverged": count(lambda r: r["action"] == svc.JDG_METHODOLOGY_DIVERGED),
              "judgment_missing": count(lambda r: r["action"] == svc.JDG_MISSING), "judgment_version_stale": count(lambda r: r["action"] == svc.JDG_VERSION_STALE),
              "no_profile": count(lambda r: r["action"] == svc.JDG_NO_PROFILE), "failed": failed, "pending_remote": pending}
    counts["complete"] = failed == 0 and counts["methodology_diverged"] == 0 and (not apply or counts["would_rebind"] == 0)
    return {"mode": "revalidate-judgment", "apply": apply, "dry_run": not apply, "records": records, "counts": counts,
            "environment_warnings": ["sin almacén durable (solo memoria): la revalidación no persiste"] if svc.governed_dir() is None else []}


def backfill_domain_classification(entries: Iterable[Any], *, apply: bool = False) -> dict:
    """Materializa la CLASIFICACIÓN DE DOMINIO (sugerencia) en los sobres gobernados que la tengan ausente o desactualizada, SIN reassess ni
    adquisición: se deriva del contexto local del Catálogo y se reescribe la MISMA identidad (perfil, Juicio y firmas intactos). Es la ruta
    operativa de backfill (más barata y segura que ``--reassess-current``: no re-ejecuta el motor). Idempotente por ``input_signature``."""
    entries = list(entries)
    resolutions = svc.resolve_entries(entries)
    records, failed, pending = [], 0, 0
    for e in entries:
        res = resolutions[e.entry_id]
        if res.kind != "OK":
            continue
        env = svc._load_envelope(res.client, res.terminal_am)
        if env is None:
            continue
        action, fresh = svc.classify_domain_classification_backfill(env, e)
        rec = {"client": res.client, "terminal_am": res.terminal_am, "domain": str(getattr(e, "dominio", "")), "action": action,
               "status": (fresh or {}).get("classification_status"), "suggested_domain": (fresh or {}).get("suggested_domain")}
        needs = action in (svc.DC_MISSING, svc.DC_STALE)
        if needs and apply:
            try:
                durable = svc.apply_domain_classification_backfill(env, fresh)
                rec["durable"], rec["action"] = durable, "updated"
                if durable != svc.DURABLE_REMOTE:
                    pending += 1
            except Exception as exc:                                    # noqa: BLE001 — una falla NO corrompe el sobre previo
                svc.log.exception("backfill de clasificación de dominio: fallo al escribir %s/%s", res.client, res.terminal_am)
                rec["action"], rec["error"] = "failed", type(exc).__name__
                failed += 1
        elif needs:
            rec["action"] = "would_update"
        records.append(rec)
    count = lambda pred: sum(1 for r in records if pred(r))             # noqa: E731
    counts = {"eligible": len(records), "already_current": count(lambda r: r["action"] == svc.DC_CURRENT), "no_root": count(lambda r: r["action"] == svc.DC_NO_ROOT),
              "would_update": count(lambda r: r["action"] == "would_update"), "updated": count(lambda r: r["action"] == "updated"),
              "failed": failed, "pending_remote": pending}
    counts["complete"] = (failed == 0 and (not apply or counts["would_update"] == 0))
    return {"mode": "backfill-domain-classification", "apply": apply, "dry_run": not apply, "records": records, "counts": counts,
            "environment_warnings": ["sin almacén durable (solo memoria): el backfill no persiste"] if svc.governed_dir() is None else []}


def backfill_business_classification(entries: Iterable[Any], *, apply: bool = False) -> dict:
    """Materializa la clasificación de NEGOCIO v2 (línea de negocio + dominio) en los sobres gobernados que la tengan ausente o desactualizada, SIN reassess ni
    adquisición: se deriva del contexto local del Catálogo y se reescribe la MISMA identidad (perfil, Juicio, firmas y la clasificación de dominio v1 intactos; esta
    operación NO materializa la v1). Independiente de ``--backfill-domain-classification``. Idempotente por ``contract_version`` + ``input_signature``; restart-safe; una
    falla individual no revierte a las demás raíces. ``counts``: eligible / classified_high / suggested_medium / undetermined / conflicts / unchanged / failed."""
    entries = list(entries)
    resolutions = svc.resolve_entries(entries)
    records, failed, pending = [], 0, 0
    for e in entries:
        res = resolutions[e.entry_id]
        if res.kind != "OK":
            continue
        env = svc._load_envelope(res.client, res.terminal_am)
        if env is None:
            continue
        action, fresh = svc.classify_business_classification_backfill(env, e)
        rec = {"client": res.client, "terminal_am": res.terminal_am, "domain": str(getattr(e, "dominio", "")), "action": action,
               "business_status": (fresh or {}).get("status"), "business_confidence": (fresh or {}).get("confidence"),
               "line_of_business": ((fresh or {}).get("line_of_business") or {}).get("id"), "business_reason": (fresh or {}).get("reason_code")}
        needs = action in (svc.BC_MISSING, svc.BC_STALE)
        if needs and apply:
            try:
                durable = svc.apply_business_classification_backfill(env, fresh)
                rec["durable"], rec["action"] = durable, "updated"
                if durable != svc.DURABLE_REMOTE:
                    pending += 1
            except Exception as exc:                                    # noqa: BLE001 — una falla NO corrompe el sobre previo ni revierte las demás raíces
                svc.log.exception("backfill de clasificación de negocio: fallo al escribir %s/%s", res.client, res.terminal_am)
                rec["action"], rec["error"] = "failed", type(exc).__name__
                failed += 1
        elif needs:
            rec["action"] = "would_update"
        records.append(rec)
    count = lambda pred: sum(1 for r in records if pred(r))             # noqa: E731
    counts = {"eligible": len(records), "unchanged": count(lambda r: r["action"] == svc.BC_CURRENT), "no_root": count(lambda r: r["action"] == svc.DC_NO_ROOT),
              "would_update": count(lambda r: r["action"] == "would_update"), "updated": count(lambda r: r["action"] == "updated"),
              "classified_high": count(lambda r: r.get("business_status") == "CLASSIFIED"), "suggested_medium": count(lambda r: r.get("business_status") == "SUGGESTED"),
              "undetermined": count(lambda r: r.get("business_status") == "UNDETERMINED"),
              "conflicts": count(lambda r: r.get("business_reason") == "CROSS_LINE_CONFLICT"),
              "failed": failed, "pending_remote": pending}
    counts["complete"] = (failed == 0 and (not apply or counts["would_update"] == 0))
    return {"mode": "backfill-business-classification", "apply": apply, "dry_run": not apply, "records": records, "counts": counts,
            "environment_warnings": ["sin almacén durable (solo memoria): el backfill no persiste"] if svc.governed_dir() is None else []}


def _cli_waves(ap: argparse.ArgumentParser, args: Any, entries: list) -> list[list[str]] | None:
    if not args.waves:
        return None
    plan = migrate_stale_evidence(entries, args.regenerate_stale_evidence, apply=False)["plan"]
    try:
        return parse_waves(args.waves, plan)
    except ValueError as exc:
        ap.error(str(exc))


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
    mode.add_argument("--reassess-current", action="store_true",
                      help="modo EXPLÍCITO: reevalúa (fuerza) los assessments gobernados CURRENT del --client con el MISMO motor canónico de generación, "
                           "sobre el contexto técnico ACTUAL del pool, reemplazando la MISMA identidad. Exige --client y --dry-run o --apply")
    mode.add_argument("--reassess-existing", action="store_true",
                      help="modo EXPLÍCITO y GENÉRICO: reconstruye COMPLETO (perfil + evidencia + Juicio + clasificación de dominio) los assessments gobernados EXISTENTES "
                           "(CURRENT y STALE) con el MISMO motor canónico (generate force=True) sobre el pool LOCAL ya persistido, en la MISMA identidad gobernada; la vigencia "
                           "se recalcula canónicamente. SIN Datasphere/DSP/publicación. Confía en el pool local: NO afirma que Datasphere esté al día. "
                           "Opcional --client (sin --client = todos los existentes). Exige --dry-run o --apply")
    mode.add_argument("--backfill-judgment", action="store_true",
                      help="modo EXPLÍCITO: materializa el Juicio metodológico DERIVADO (judgment-rules/v3) en los sobres gobernados que lo tengan ausente o con un contrato de "
                           "Juicio anterior, derivándolo del perfil YA persistido (SIN reassess ni adquisición) e INYECTANDO una instantánea verificable del registro de decisiones "
                           "de gobierno (--decisions). Idempotente. Exige --dry-run o --apply; --apply es ASEGURADO (aprobación firmada + respaldo + --confirm-migration " + JUDGMENT_V3_MIGRATION + ")")
    ap.add_argument("--decisions", choices=("registry", "none"), default="registry",
                    help="con --backfill-judgment: 'registry' (defecto) toma UNA instantánea de SOLO LECTURA del registro governed-decisions/ (fail-closed si no se puede leer); 'none' = Juicio v3 SIN decisiones")
    ap.add_argument("--reevaluate-decision-stale", action="store_true",
                    help="con --backfill-judgment: reemplaza (re-deriva) los Juicios marcados judgment_decision_stale; por defecto SOLO se marcan pendientes de reevaluación sin reescribir el Juicio")
    mode.add_argument("--revalidate-judgment", action="store_true",
                      help="modo EXPLÍCITO: REVALIDA (no recalifica) los Juicios v2 persistidos cuya firma de perfil quedó desactualizada: si la parte metodológica del Juicio derivado por el motor v2 es "
                           "IDÉNTICA a la persistida reconoce la nueva firma del perfil; si difiere en algo NO escribe. No crea ni migra Juicios (eso es --backfill-judgment). Exige --dry-run o --apply")
    mode.add_argument("--backfill-business-classification", action="store_true",
                      help="modo EXPLÍCITO: materializa la clasificación de NEGOCIO v2 (línea de negocio + dominio) en los sobres gobernados que la tengan ausente o desactualizada, "
                           "derivándola del Catálogo local (SIN reassess ni adquisición; no toca perfil, Juicio ni la clasificación de dominio v1). Idempotente. Exige --dry-run o --apply")
    mode.add_argument("--backfill-domain-classification", action="store_true",
                      help="modo EXPLÍCITO: materializa la CLASIFICACIÓN DE DOMINIO (sugerencia, hermana del Juicio) en los sobres gobernados que la tengan ausente "
                           "o desactualizada, derivándola del Catálogo local (SIN reassess ni adquisición; no toca perfil ni Juicio). Idempotente. Exige --dry-run o --apply")
    ap.add_argument("--apply", action="store_true", help="solo con un modo de regeneración: escribe los reemplazos (excluyente con --dry-run)")
    ap.add_argument("--approval", default="", metavar="ARCHIVO", help="aprobacion FIRMADA (migration-approval/v1) del plan_digest que muestra el dry-run; sin ella --apply de una migracion sensible se DENIEGA (exit 4)")
    ap.add_argument("--operator", default="", metavar="ID", help="identidad de quien ejecuta; debe ser el operador autorizado en la aprobacion y distinto del aprobador")
    ap.add_argument("--backup-dir", default="", metavar="DIR", help="directorio VACIO, fuera del almacen gobernado, donde se guarda el respaldo verificable previo al --apply")
    ap.add_argument("--waves", default="", metavar="A,B;C", help="olas de clientes (por defecto un cliente por ola, de menos a mas sobres); debe cubrir los clientes del plan")
    ap.add_argument("--allow-local-only", action="store_true", help="permite aplicar SIN Spaces habilitado (por defecto el --apply de una migracion sensible exige remoto confirmado por bytes)")
    ap.add_argument("--restore-backup", default="", metavar="DIR", help="modo de RESTAURACION por objeto desde un respaldo local (--dry-run para simular; --apply exige --confirm-restore Y una --approval firmada de 'restore:<migracion>'); no pisa cambios concurrentes")
    ap.add_argument("--restore-remote-run", default="", metavar="MIGRACION/RUN_ID", help="igual que --restore-backup pero desde el respaldo DURABLE sellado en Spaces; requiere --backup-dir (directorio vacio de staging)")
    ap.add_argument("--list-remote-backups", default="", metavar="MIGRACION", help="lista (solo lectura) los runs de respaldo durable de la migracion y si estan sellados")
    ap.add_argument("--object", action="append", default=[], metavar="CLAVE", help="con --restore-backup: restaura solo este objeto (repetible); por omision todos")
    ap.add_argument("--confirm-restore", default="", metavar="SELLO", help="con --restore-backup --apply: primeros 12+ caracteres de manifest_sha256")
    ap.add_argument("--confirm-migration", default="", metavar="MIGRACION",
                    help="confirmación EXPLÍCITA que exige --apply en las migraciones sensibles (" + ", ".join(sorted(CONFIRMATION_REQUIRED)) + "): debe repetir el nombre de la migración")
    args = ap.parse_args(argv)
    if args.list_remote_backups:
        try:
            report = {"mode": "list-remote-backups", "migration": args.list_remote_backups, "runs": dbk.list_runs(args.list_remote_backups)}
        except svc.remote_store.RemoteReadError as exc:
            report = {"mode": "list-remote-backups", "migration": args.list_remote_backups, "runs": [], "error": str(exc)}
        sys.stdout.buffer.write((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return 1 if report.get("error") else 0
    if args.restore_backup or args.restore_remote_run:
        if args.restore_backup and args.restore_remote_run:
            ap.error("--restore-backup y --restore-remote-run son excluyentes")
        if args.dry_run == args.apply:
            ap.error("la restauracion exige EXACTAMENTE uno de --dry-run o --apply")
        auth = ma.authorizer_from_file(args.approval or None, args.operator or None)
        if args.restore_remote_run:
            mig, _, rid = args.restore_remote_run.partition("/")
            if not rid or not args.backup_dir:
                ap.error("--restore-remote-run exige MIGRACION/RUN_ID y --backup-dir (directorio vacio de staging)")
            report = restore_from_remote_run(mig, rid, args.backup_dir, apply=args.apply, keys=args.object or None, confirm=args.confirm_restore, authorizer=auth)
        else:
            report = restore_from_backup(args.restore_backup, apply=args.apply, keys=args.object or None, confirm=args.confirm_restore, authorizer=auth)
        sys.stdout.buffer.write((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        c = report["counts"]
        if report["outcome"] in ("RESTORE_DENIED", "CONFIRMATION_REQUIRED"):
            return EXIT_DENIED
        return 1 if report["outcome"] != "DONE" or c.get("failed") else (3 if c.get("conflicts") else (2 if c.get("pending_remote") else 0))
    regen_mode = (args.enrich_current_missing or args.regenerate_stale_evidence or args.reassess_current or args.reassess_existing or args.backfill_judgment or args.revalidate_judgment
                  or args.backfill_domain_classification or args.backfill_business_classification)
    if regen_mode:
        if args.dry_run == args.apply:                                           # ninguno o ambos: usage, CERO escrituras
            ap.error("los modos de regeneración exigen EXACTAMENTE uno de --dry-run o --apply")
    elif args.apply:
        ap.error("--apply solo existe con un modo de regeneración (el backfill normal no lo usa)")
    if args.apply and args.regenerate_stale_evidence in CONFIRMATION_REQUIRED and args.confirm_migration != args.regenerate_stale_evidence:
        ap.error(f"--apply de {args.regenerate_stale_evidence} exige --confirm-migration {args.regenerate_stale_evidence} (control explícito; cero escrituras)")
    if args.backfill_judgment and args.apply and args.confirm_migration != JUDGMENT_V3_MIGRATION:
        ap.error(f"--apply de --backfill-judgment exige --confirm-migration {JUDGMENT_V3_MIGRATION} (control explícito; cero escrituras)")
    if args.confirm_migration and args.confirm_migration != (JUDGMENT_V3_MIGRATION if args.backfill_judgment else args.regenerate_stale_evidence):
        ap.error("--confirm-migration solo confirma la migración indicada (--regenerate-stale-evidence, o " + JUDGMENT_V3_MIGRATION + " con --backfill-judgment)")
    if (args.decisions != "registry" or args.reevaluate_decision_stale) and not args.backfill_judgment:
        ap.error("--decisions/--reevaluate-decision-stale solo existen con --backfill-judgment")
    if args.reassess_current and not args.client.strip():                        # la reevaluación masiva DEBE ser explícita por cliente
        ap.error("--reassess-current exige --client (no reevalúa todos los clientes automáticamente)")
    _mod, entries = catalog_reads._catalog_entries()
    wanted = args.client.strip().lower()
    entries = [e for e in (entries or []) if not wanted or str(getattr(e, "cliente", "")).strip().lower() == wanted]
    if args.enrich_current_missing:
        report = enrich_current(entries, args.enrich_current_missing, apply=args.apply)
        code = 1 if report["counts"]["failed"] else (2 if report["environment_warnings"] else 0)
    elif args.regenerate_stale_evidence:
        if args.apply and args.regenerate_stale_evidence in SECURED_MIGRATIONS:
            report = secured_migrate(entries, args.regenerate_stale_evidence, authorizer=ma.authorizer_from_file(args.approval or None, args.operator or None),
                                     backup_dir=args.backup_dir or None, allow_local_only=args.allow_local_only, waves=_cli_waves(ap, args, entries))
            code = (EXIT_DENIED if report.get("outcome") in (DENIED, BACKUP_NOT_GUARANTEED, REMOTE_REQUIRED) else EXIT_WAVE_STOPPED if report.get("outcome") == STOPPED_AT_WAVE
                    else 1 if report["counts"]["failed"] else (3 if report["counts"].get("abstained") else (2 if report["environment_warnings"] else 0)))
        else:
            report = migrate_stale_evidence(entries, args.regenerate_stale_evidence, apply=args.apply)
            code = 1 if report["counts"]["failed"] else (3 if report["counts"].get("abstained") else (2 if report["environment_warnings"] else 0))
    elif args.reassess_current:
        report = reassess_selected(entries, apply=args.apply)
        code = 1 if report["counts"]["failed"] else (2 if report["environment_warnings"] else 0)
    elif args.reassess_existing:
        report = reassess_existing(entries, apply=args.apply)
        code = 1 if report["counts"]["failed"] else (2 if report["environment_warnings"] else 0)
    elif args.backfill_judgment:
        from webapp.services import judgment_decisions as jd
        try:
            if args.decisions == "registry":                                      # UNA instantánea SOLO LECTURA del registro; si no se puede leer/validar NO se continúa
                wanted_c = args.client.strip().lower()
                scope = [str(e.get("client") or "") for e, _p in svc.iter_governed_envelopes() if not wanted_c or str(e.get("client", "")).strip().lower() == wanted_c]
                snap = jd.take_snapshot(scope)
            else:
                snap = jd.empty_snapshot()
        except jd.DecisionSnapshotError as exc:
            sys.stdout.buffer.write((json.dumps({"mode": "backfill-judgment", "outcome": "DECISION_SNAPSHOT_FAILED", "error": str(exc), "writes": 0}, ensure_ascii=False, indent=2) + chr(10)).encode("utf-8"))
            return 1
        if args.apply:
            report = secured_adopt_judgment(authorizer=ma.authorizer_from_file(args.approval or None, args.operator or None), backup_dir=args.backup_dir or None, client=args.client,
                                            snapshot=snap, allow_local_only=args.allow_local_only, reevaluate_decision_stale=args.reevaluate_decision_stale)
            code = (EXIT_DENIED if report.get("outcome") in (DENIED, BACKUP_NOT_GUARANTEED, REMOTE_REQUIRED) else EXIT_WAVE_STOPPED if report.get("outcome") == STOPPED_AT_WAVE
                    else 1 if report["counts"].get("failed") else (2 if report.get("environment_warnings") else 0))
        else:
            report = backfill_judgment(apply=False, client=args.client, snapshot=snap, reevaluate_decision_stale=args.reevaluate_decision_stale)
            code = 1 if report["counts"]["failed"] else (2 if report["environment_warnings"] else 0)
    elif args.revalidate_judgment:
        report = revalidate_judgments(apply=args.apply, client=args.client)
        code = 1 if report["counts"]["failed"] else (3 if report["counts"].get("methodology_diverged") else (2 if report["environment_warnings"] else 0))
    elif args.backfill_domain_classification:
        report = backfill_domain_classification(entries, apply=args.apply)
        code = 1 if report["counts"]["failed"] else (2 if report["environment_warnings"] else 0)
    elif args.backfill_business_classification:
        report = backfill_business_classification(entries, apply=args.apply)
        code = 1 if report["counts"]["failed"] else (2 if report["environment_warnings"] else 0)
    else:
        report = backfill(entries, dry_run=args.dry_run)
        code = 1 if report["failed"] else 0
    sys.stdout.buffer.write((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))   # UTF-8 aun con stdout redirigido (cp1252)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
