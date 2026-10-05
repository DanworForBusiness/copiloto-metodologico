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
        key = (client.lower(), status.terminal_am) if status.terminal_am else (client.lower(), f"entry:{eid}")
        if key in seen:                                                           # varias entradas del Catálogo → el MISMO assessment gobernado: una sola vez
            seen[key]["entry_ids"].append(eid)
            continue
        rec: dict = {"client": status.client or client, "root": status.terminal_am, "entry_ids": [eid], "state": status.state, "action": SKIP,
                     "reason": None, "missing": [], "delta": None, "durable": None}
        seen[key] = rec
        out["records"].append(rec)
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
        try:
            candidate = regenerate(entry, required)                               # motor canónico en MEMORIA; valida; no escribe
        except svc.EnrichmentError as exc:
            rec.update(action=FAILED_ACTION, reason=exc.code)
            continue
        except Exception as exc:                                                  # noqa: BLE001 — jamás se escribe nada; el registro previo queda
            rec.update(action=FAILED_ACTION, reason=f"REGENERATION_ERROR:{type(exc).__name__}")
            continue
        rec["delta"] = compare_profiles(status.profile, candidate.profile, required)   # el MISMO perfil que se escribe: no se recalcula
        if not apply:
            rec["action"] = WOULD_REGENERATE
            continue
        try:
            durable = commit(candidate)
        except Exception as exc:                                                  # noqa: BLE001
            rec.update(action=FAILED_ACTION, reason=f"PERSISTENCE_ERROR:{type(exc).__name__}")
            continue
        if durable == svc.DURABLE_MEMORY:                                         # no se pudo escribir: el registro previo sigue siendo el vigente
            rec.update(action=FAILED_ACTION, reason="NOT_PERSISTED_MEMORY_ONLY (registro previo conservado)", durable=durable)
            continue
        rec.update(action=REGENERATED, durable=durable)
        rec["_candidate"] = candidate
    if apply:                                                                     # reintento EXPLÍCITO del PUT remoto de lo que quedó solo en disco (misma API del write-through)
        for rec in out["records"]:
            if rec.get("action") == REGENERATED and rec["durable"] == svc.DURABLE_LOCAL:
                try:
                    if retry_remote(rec["client"], rec["root"]):
                        rec.update(durable=svc.DURABLE_REMOTE, remote_retry="CONFIRMED")
                    else:
                        rec["remote_retry"] = "NOT_CONFIRMED"
                except Exception:                                                 # noqa: BLE001
                    rec["remote_retry"] = "NOT_CONFIRMED"
    # ── durabilidad de lo YA enriquecido: un registro local-pero-no-remoto de una corrida anterior NO deja de bloquear el reinicio por omitirse acá.
    #    Se verifica por bytes contra Spaces (lectura pura); en apply, lo pendiente recibe el mismo reintento explícito (push_path) y se re-verifica.
    for rec in out["records"]:
        if rec["reason"] != SKIPPED_ALREADY_ENRICHED:
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
    # ── entorno: este modo NO restaura nada; si el estado gobernado parece ausente, no se finge que no hay nada que hacer ──
    if not entries:
        out["environment_warnings"].append("NO_CATALOG_ENTRIES: no hay entradas del Catálogo para el alcance pedido (¿cliente mal escrito o Catálogo vacío?)")
    elif states and all(st == svc.NOT_YET_ASSESSED for st in states):
        out["environment_warnings"].append("ENVIRONMENT_NO_GOVERNED_STATE: todas las entradas figuran NOT_YET_ASSESSED; corré esto en la consola del servidor "
                                           "donde governed-assessments/ ya está restaurado (este modo no restaura)")
    elif not governed_state_present():
        out["environment_warnings"].append("ENVIRONMENT_NO_GOVERNED_STATE: no hay archivos en el almacén gobernado de este entorno")
    if apply and not remote_enabled():
        out["environment_warnings"].append("REMOTE_STORE_DISABLED: sin credenciales de Spaces nada puede quedar en remote; el reinicio queda bloqueado")
    # ── regla de reinicio: SOLO si todo lo regenerado está confirmado en remote ──
    #    Se mira TODO lo enriquecido del alcance (regenerado ahora O ya enriquecido antes), no solo lo regenerado en esta corrida.
    counts = out["counts"]
    confirmed_any = counts["regenerated_remote"] + counts["already_enriched_remote_confirmed"] > 0
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
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--client", default="", help="limitar a un cliente (por defecto: todas las entradas del Catálogo)")
    ap.add_argument("--dry-run", action="store_true", help="solo informa qué se generaría (en enriquecimiento: corre el motor en memoria y muestra viejo→nuevo)")
    ap.add_argument("--enrich-current-missing", choices=sorted(ENRICHMENTS), metavar="NOMBRE",
                    help="modo EXPLÍCITO: regenera los assessments gobernados CURRENT a los que les falta el hecho requerido (" + ", ".join(sorted(ENRICHMENTS)) + "). "
                         "Exige --dry-run o --apply")
    ap.add_argument("--apply", action="store_true", help="solo con --enrich-current-missing: escribe los reemplazos (excluyente con --dry-run)")
    args = ap.parse_args(argv)
    if args.enrich_current_missing:
        if args.dry_run == args.apply:                                           # ninguno o ambos: usage, CERO escrituras
            ap.error("--enrich-current-missing exige EXACTAMENTE uno de --dry-run o --apply")
    elif args.apply:
        ap.error("--apply solo existe con --enrich-current-missing (el backfill normal no lo usa)")
    _mod, entries = catalog_reads._catalog_entries()
    wanted = args.client.strip().lower()
    entries = [e for e in (entries or []) if not wanted or str(getattr(e, "cliente", "")).strip().lower() == wanted]
    if args.enrich_current_missing:
        report = enrich_current(entries, args.enrich_current_missing, apply=args.apply)
        code = 1 if report["counts"]["failed"] else (2 if report["environment_warnings"] else 0)
    else:
        report = backfill(entries, dry_run=args.dry_run)
        code = 1 if report["failed"] else 0
    sys.stdout.buffer.write((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))   # UTF-8 aun con stdout redirigido (cp1252)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
