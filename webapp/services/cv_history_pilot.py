"""cv_history_pilot.py — PILOTO de extracción mínima y conciliación de identidad del histórico de CVs (incremento 2 de la importación histórica).

Alcance (y límites): descarga de forma CONTROLADA una muestra pequeña (10–20 contenidos únicos), extrae LOCALMENTE nombre, apellido, email y celular, concilia contra los perfiles del Banco y deja un manifiesto
persistente y reanudable con el estado de cada documento y sus excepciones. NO crea perfiles, NO copia documentos a `CVs`, NO modifica ni elimina nada en SharePoint, NO toca catálogos y NO usa OpenAI ni envía CVs
a ningún servicio externo. Los bytes descargados viven solo en memoria durante el procesamiento: no se escriben a disco; de cada documento se conserva su sha256 y los cuatro campos.

Identidades distintas:
  * DOCUMENTO (contenido): un mismo contenido en varias carpetas es UN registro (`content_key`) que conserva TODAS las especialidades heredadas del árbol de SharePoint y se procesa UNA sola vez; si dos registros
    resultan idénticos por sha256 tras la descarga (p. ej. sin hash en Graph), el segundo queda como ALIAS del primero y sus especialidades se suman;
  * PERSONA: se SUGIERE por email, celular y nombre (`cv_reconcile`), contra el Banco y dentro del lote. Nunca se fusiona ni se vincula automáticamente.

La especialidad es la subcarpeta de primer nivel (heredada, sin inferir puesto). Su valor propuesto para `profile_skills` es el texto libre «Esp1, Esp2» (el campo es texto libre sin separadores ni validación
propios: ver `human_capital_service.validate_and_shape_fields`); aquí solo se PROPONE, no se escribe en ningún perfil.

AUTORIZACIÓN: crear el lote exige la frase de autorización del owner para procesar datos personales; queda registrada (quién y cuándo) en el lote."""
from __future__ import annotations

import hashlib
import secrets
import time
import urllib.error
import urllib.parse
from collections import Counter
from datetime import datetime, timedelta, timezone

from core import microsoft365
from webapp.services import cv_extract, cv_history_inventory as inventory, cv_history_manifest as manifest, cv_reconcile, profile_document_validation, profile_repository

AUTHORIZATION_PHRASE = "AUTORIZO-PROCESAR-DATOS-PERSONALES-PILOTO"
SAMPLE_MIN, SAMPLE_MAX = 10, 20
PROCESS_TIME_BUDGET_S = 60.0
DOWNLOAD_TIMEOUT_S = 60

SELECTED, EXTRACTED, FAILED, ALIAS = "SELECTED", "EXTRACTED", "FAILED", "ALIAS"
PENDING_STATES = (SELECTED, FAILED)
DOWNLOAD_FORBIDDEN, DOWNLOAD_NOT_FOUND, DOWNLOAD_ERROR, FORMAT_INVALID, SIZE_MISMATCH, CONTENT_CHANGED = (
    "DOWNLOAD_FORBIDDEN", "DOWNLOAD_NOT_FOUND", "DOWNLOAD_ERROR", "FORMAT_INVALID", "SIZE_MISMATCH", "CONTENT_CHANGED")
SAME_PERSON_IN_RUN, RECONCILIATION_UNAVAILABLE = "SAME_PERSON_IN_RUN", "RECONCILIATION_UNAVAILABLE"


class PilotInputError(ValueError):
    """Entrada inválida (autorización, tamaño de muestra, link): mensaje seguro."""


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def content_key(item: dict) -> str:
    """Identidad de CONTENIDO: el hash que Graph informa (+ tamaño) o, sin hash, el id Graph (cada elemento sin hash es su propio contenido hasta que la descarga demuestre lo contrario)."""
    if item.get("hash"):
        return _sha(f"{item['hash_algo']}:{item['hash']}:{item['size']}")[:16]
    return _sha(f"item:{item['item_id']}")[:16]


def build_groups(items: list[dict]) -> list[dict]:
    """Agrupa los archivos COMPATIBLES por contenido; cada grupo conserva todas las especialidades y todas las copias (identidad Graph) que lo contienen."""
    groups: dict[str, dict] = {}
    for item in sorted((i for i in items if i["cls"] == inventory.COMPATIBLE), key=lambda i: (i["specialty"], i["item_id"])):
        key = content_key(item)
        g = groups.setdefault(key, {"content_key": key, "size": item["size"], "hash": {"algo": item["hash_algo"], "value": item["hash"]} if item.get("hash") else None,
                                    "members": [], "specialties": [], "representative": {"item_id": item["item_id"], "drive_id": item["drive_id"], "name": item["name"]}})
        if any(m["item_id"] == item["item_id"] for m in g["members"]):
            continue
        g["members"].append({"item_ref": inventory._ref(item["item_id"]), "item_id": item["item_id"], "drive_id": item["drive_id"], "specialty": item["specialty"]})
        if item["specialty"] not in g["specialties"]:
            g["specialties"].append(item["specialty"])
    return [groups[k] for k in sorted(groups)]


def select_sample(groups: list[dict], n: int) -> list[dict]:
    """Muestra DETERMINÍSTICA y estratificada: una vuelta por especialidad (en orden), tomando el próximo contenido aún no elegido que la tenga; garantiza incluir un contenido repartido en varias
    especialidades si existe (es el caso que el piloto debe ejercitar)."""
    by_spec: dict[str, list[dict]] = {}
    for g in groups:
        for s in g["specialties"]:
            by_spec.setdefault(s, []).append(g)
    chosen: list[dict] = []
    taken: set[str] = set()
    specs = sorted(by_spec)
    while len(chosen) < n and any(by_spec[s] for s in specs):
        for s in specs:
            while by_spec[s] and by_spec[s][0]["content_key"] in taken:
                by_spec[s].pop(0)
            if by_spec[s] and len(chosen) < n:
                g = by_spec[s].pop(0)
                taken.add(g["content_key"])
                chosen.append(g)
    cross = [g for g in groups if len(g["specialties"]) > 1 and g["content_key"] not in taken]
    if chosen and cross and not any(len(g["specialties"]) > 1 for g in chosen):
        covered = lambda picks: {s for g in picks for s in g["specialties"]}          # noqa: E731
        want = covered(chosen)
        for candidate in cross:                                                        # reemplazar un elegido SIN perder cobertura de especialidades, si se puede
            for i in range(len(chosen) - 1, -1, -1):
                trial = chosen[:i] + [candidate] + chosen[i + 1:]
                if covered(trial) >= want:
                    chosen = trial
                    break
            else:
                continue
            break
        else:
            chosen[-1] = cross[0]
    return sorted(chosen, key=lambda g: g["content_key"])


def _population(items: list[dict], groups: list[dict], report: dict) -> dict:
    compat = [i for i in items if i["cls"] == inventory.COMPATIBLE]
    return {"complete": bool(report.get("complete")), "compatible_files": len(compat), "content_groups": len(groups), "redundant_copies": len(compat) - len(groups),
            "items_without_hash": sum(1 for i in compat if not i.get("hash")), "cross_specialty_groups": sum(1 for g in groups if len(g["specialties"]) > 1),
            "specialties": len({s for g in groups for s in g["specialties"]})}


def start_run(token: str, share_url: str, *, sample_size: int, authorization: str, actor: str, min_sample: int = SAMPLE_MIN,
              clock=time.monotonic, sleep=time.sleep) -> dict:
    """Crea un lote: inventario de solo lectura, agrupación por contenido, selección de la muestra y registros iniciales. NO descarga ningún documento (eso es `process_run`)."""
    if (authorization or "").strip() != AUTHORIZATION_PHRASE:
        raise PilotInputError("Falta la confirmación explícita del owner para procesar datos personales.")
    if not min_sample <= int(sample_size) <= SAMPLE_MAX:
        raise PilotInputError(f"La muestra inicial es de {min_sample} a {SAMPLE_MAX} documentos.")
    items: list[dict] = []
    report = inventory.run_inventory(token, share_url, clock=clock, sleep=sleep, items_out=items)
    if not report.get("ok"):
        raise PilotInputError("No se pudo resolver el link a una carpeta con tus permisos actuales.")
    groups = build_groups(items)
    if not groups:
        raise PilotInputError("No hay documentos compatibles para procesar.")
    sample = select_sample(groups, int(sample_size))
    run_id = manifest.new_run_id(suffix=secrets.token_hex(2))                          # sufijo aleatorio: dos lotes creados en el mismo segundo (doble clic) no colisionan
    until = (datetime.now(timezone.utc) + timedelta(days=manifest.STAGING_RETENTION_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    run = manifest.create_run(run_id, {
        "staging": {"state": "PRESENT", "retention_days": manifest.STAGING_RETENTION_DAYS, "retention_until": until, "auto_delete": False,
                    "policy": "El área temporal (nombre, email y celular extraídos) se conserva hasta la fecha de vigencia como máximo y se elimina por acción explícita del owner; la auditoría (sin datos personales) se conserva."},
        "kind": "pilot", "state": "CREATED", "created_by": actor, "source": report["root"],
        "authorization": {"phrase": AUTHORIZATION_PHRASE, "confirmed_by": actor, "confirmed_at": manifest.now_iso()},
        "params": {"sample_size": len(sample)}, "population": _population(items, groups, report),
        "counters": {"selected": len(sample), "extracted": 0, "failed": 0, "alias": 0}})
    for g in sample:
        manifest.put_group(run_id, g["content_key"], {**g, "state": SELECTED, "sha256": None, "extraction": None, "reconciliation": None, "review": None, "alias_of": None,
                                                      "profile_skills_proposal": ", ".join(g["specialties"])})
        manifest.append_audit(run_id, g["content_key"], "SELECTED", specialties=len(g["specialties"]), copies=len(g["members"]))
    return run


# ═══════════════════ procesamiento ═══════════════════
def _download(item_id: str, drive_id: str, token: str, sleep) -> bytes:
    url = f"{inventory.GRAPH_BASE}/drives/{urllib.parse.quote(drive_id, safe='!,')}/items/{urllib.parse.quote(item_id, safe='')}/content"
    return inventory.call_with_retry(lambda: microsoft365.graph_get_bytes(url, token, timeout=DOWNLOAD_TIMEOUT_S), sleep=sleep)


def _review(extraction: dict, matches: list[dict], in_run: list[dict]) -> dict:
    reasons = list(extraction.get("blocking") or [])
    reasons += [f"DUP_EXISTING_{m['strength']}" for m in matches if m["strength"] in (cv_reconcile.STRONG, cv_reconcile.PROBABLE)][:1]
    if any(m["strength"] in (cv_reconcile.STRONG, cv_reconcile.PROBABLE) for m in in_run):
        reasons.append(SAME_PERSON_IN_RUN)
    return {"required": bool(reasons), "reasons": sorted(set(reasons))}


def _recompute_in_run(run_id: str) -> None:
    """Sugerencias de la MISMA persona entre los documentos del lote (no fusiona). Se persiste solo lo que cambia."""
    groups = {g["content_key"]: g for g in manifest.list_groups(run_id) if g["state"] == EXTRACTED and g.get("extraction")}
    clusters = cv_reconcile.cluster_in_run({k: g["extraction"] for k, g in groups.items()})
    for key, g in groups.items():
        rec = g.get("reconciliation") or {}
        if rec.get("in_run") != clusters[key]:
            rec["in_run"] = clusters[key]
            g["reconciliation"] = rec
            g["review"] = _review(g["extraction"], rec.get("profiles") or [], clusters[key])
            manifest.put_group(run_id, key, g)


def process_run(run_id: str, token: str, *, clock=time.monotonic, sleep=time.sleep, time_budget: float = PROCESS_TIME_BUDGET_S) -> dict:
    """Procesa los documentos pendientes (SELECTED/FAILED) dentro de un presupuesto de tiempo. Reanudable e idempotente: lo ya EXTRAÍDO no se vuelve a descargar. Un error por documento se registra y el
    lote sigue; un token vencido (401) detiene el lote sin marcar nada como fallido."""
    run = manifest.get_run(run_id)
    started = clock()
    try:
        profiles = profile_repository.list()
    except profile_repository.ProfileStorageError:
        return {"processed": 0, "failed": 0, "remaining": _pending(run_id), "stopped_reason": "PROFILES_UNAVAILABLE"}
    processed = failed = 0
    stopped: str | None = None
    for group in manifest.list_groups(run_id):
        if group["state"] not in PENDING_STATES:
            continue
        if clock() - started >= time_budget:
            stopped = "TIME_BUDGET"
            break
        key = group["content_key"]
        rep = group["representative"]
        try:
            try:
                data = _download(rep["item_id"], rep["drive_id"], token, sleep)
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    stopped = "TOKEN_EXPIRED"
                    break
                code = DOWNLOAD_FORBIDDEN if exc.code == 403 else DOWNLOAD_NOT_FOUND if exc.code == 404 else DOWNLOAD_ERROR
                _fail(run_id, group, code)
                failed += 1
                continue
            except (urllib.error.URLError, OSError, ValueError):
                _fail(run_id, group, DOWNLOAD_ERROR)
                failed += 1
                continue
            digest = hashlib.sha256(data).hexdigest()
            if group.get("sha256") and group["sha256"] != digest:
                _fail(run_id, group, CONTENT_CHANGED)                                          # nunca se pisa en silencio un contenido ya registrado
                failed += 1
                continue
            if len(data) != int(group["size"]):
                _fail(run_id, group, SIZE_MISMATCH)
                failed += 1
                continue
            try:
                profile_document_validation.validate_upload(rep["name"], data)
            except profile_document_validation.ProfileDocumentValidationError:
                _fail(run_id, group, FORMAT_INVALID)
                failed += 1
                continue
            manifest.append_audit(run_id, key, "DOWNLOADED", size=len(data))
            primary = next((g for g in manifest.list_groups(run_id) if g["content_key"] != key and g.get("sha256") == digest and g["state"] == EXTRACTED), None)
            if primary is not None:                                                            # mismo contenido que otro registro del lote: identidad DOCUMENTAL repetida
                _make_alias(run_id, group, primary, digest)
                processed += 1
                continue
            extraction = cv_extract.extract(rep["name"], data)
            del data
            matches = cv_reconcile.reconcile_with_profiles(extraction, profiles)
            group.update({"state": EXTRACTED, "sha256": digest, "extraction": extraction, "reconciliation": {"profiles": matches, "in_run": []},
                          "review": _review(extraction, matches, []), "processed_at": manifest.now_iso()})
            manifest.put_group(run_id, key, group)
            manifest.append_audit(run_id, key, "EXTRACTED", code=extraction.get("failure"), review=group["review"]["required"], reasons=",".join(group["review"]["reasons"]))
            processed += 1
        except manifest.ManifestDurabilityError:
            stopped = "PERSISTENCE_ERROR"
            break
    if stopped != "PERSISTENCE_ERROR":
        _recompute_in_run(run_id)
    _refresh_counters(run_id, stopped)
    return {"processed": processed, "failed": failed, "remaining": _pending(run_id), "stopped_reason": stopped}


def _fail(run_id: str, group: dict, code: str) -> None:
    group.update({"state": FAILED, "failure": code, "failed_at": manifest.now_iso()})
    manifest.put_group(run_id, group["content_key"], group)
    manifest.append_audit(run_id, group["content_key"], "FAILED", code=code)


def _make_alias(run_id: str, group: dict, primary: dict, digest: str) -> None:
    for member in group["members"]:
        if all(m["item_id"] != member["item_id"] for m in primary["members"]):
            primary["members"].append(member)
    for spec in group["specialties"]:
        if spec not in primary["specialties"]:
            primary["specialties"].append(spec)
    primary["profile_skills_proposal"] = ", ".join(primary["specialties"])
    manifest.put_group(run_id, primary["content_key"], primary)
    group.update({"state": ALIAS, "alias_of": primary["content_key"], "sha256": digest, "processed_at": manifest.now_iso()})
    manifest.put_group(run_id, group["content_key"], group)
    manifest.append_audit(run_id, group["content_key"], "ALIAS", alias_of=primary["content_key"])
    manifest.append_audit(run_id, primary["content_key"], "SPECIALTIES_MERGED", specialties=len(primary["specialties"]))


def _pending(run_id: str) -> int:
    return sum(1 for g in manifest.list_groups(run_id) if g["state"] in PENDING_STATES)


def _refresh_counters(run_id: str, stopped: str | None) -> None:
    states = Counter(g["state"] for g in manifest.list_groups(run_id))

    def mutate(doc: dict) -> None:
        doc["counters"] = {"selected": sum(states.values()), "extracted": states.get(EXTRACTED, 0), "failed": states.get(FAILED, 0), "alias": states.get(ALIAS, 0)}
        doc["state"] = "DONE" if not (states.get(SELECTED, 0) + states.get(FAILED, 0)) else "PROCESSING"
        doc["last_stop"] = stopped
    manifest.update_run(run_id, mutate)


# ═══════════════════ lectura: reporte general (sin datos personales) y vista del owner ═══════════════════
def discard_staging(run_id: str, *, confirm: str, actor: str) -> dict:
    """Eliminación EXPLÍCITA del área temporal con datos personales. Exige la frase `ELIMINAR-DATOS-<run_id>`. Antes de borrar guarda en `run.json` el reporte final (solo conteos) para que el lote siga
    siendo auditable y las decisiones de escala no se pierdan; luego elimina los archivos uno a uno (local y Spaces). Reanudable e idempotente; la auditoría se conserva."""
    if (confirm or "").strip() != discard_phrase(run_id):
        raise PilotInputError("Falta la confirmación explícita para eliminar los datos personales del lote.")
    run = manifest.get_run(run_id)
    if (run.get("staging") or {}).get("state") != "DISCARDED":
        if "final_report" not in run:
            report = run_report(run_id)
            manifest.update_run(run_id, lambda d: d.update({"final_report": report, "staging": {**d["staging"], "state": "DISCARDING"}}))
        removed = manifest.delete_staging_files(run_id)
        audit_dir = manifest._run_dir(run_id) / "audit"
        for entry in sorted(audit_dir.glob("*.json")) if audit_dir.is_dir() else []:          # la auditoría por contenido se conserva; se agrega el evento de cierre
            manifest.append_audit(run_id, entry.stem, "STAGING_DISCARDED")
        manifest.update_run(run_id, lambda d: d.update({"staging": {**d["staging"], "state": "DISCARDED", "discarded_at": manifest.now_iso(), "discarded_by": actor, "files_removed": removed}}))
    return manifest.get_run(run_id)["staging"]


def discard_phrase(run_id: str) -> str:
    return f"ELIMINAR-DATOS-{run_id}"


def _staging_view(run: dict) -> dict:
    st = dict(run.get("staging") or {"state": "PRESENT"})
    until = st.get("retention_until")
    st["expired"] = bool(until and st.get("state") != "DISCARDED" and datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") > until)
    return st


def run_report(run_id: str) -> dict:
    """Métricas del lote para decidir si se escala. SOLO conteos y tasas: nada personal. Tras descartar el área temporal devuelve el reporte final que se guardó antes de eliminarla."""
    run = manifest.get_run(run_id)
    if (run.get("staging") or {}).get("state") == "DISCARDED" and run.get("final_report"):
        return {**run["final_report"], "staging": _staging_view(run)}
    groups = manifest.list_groups(run_id)
    extracted = [g for g in groups if g["state"] == EXTRACTED]
    ok = [g for g in extracted if not (g["extraction"] or {}).get("failure")]
    codes = Counter(c for g in extracted for c in (g.get("review") or {}).get("reasons", []))
    failures = Counter(g.get("failure") for g in groups if g["state"] == FAILED)
    readable = len(ok)
    pct = lambda n, d: round(100.0 * n / d, 1) if d else None            # noqa: E731
    fields = {name: sum(1 for g in ok if g["extraction"].get(name)) for name in ("first_name", "email", "phone")}
    review = sum(1 for g in extracted if (g.get("review") or {}).get("required"))
    pop = run.get("population") or {}
    rate = (review + sum(failures.values())) / len(groups) if groups else None
    by_spec: dict[str, dict] = {}
    for g in groups:
        for s in g["specialties"]:
            e = by_spec.setdefault(s, {"specialty": s, "documents": 0, "needs_review": 0})
            e["documents"] += 1
            e["needs_review"] += int(bool((g.get("review") or {}).get("required")) or g["state"] == FAILED)
    return {
        "run_id": run_id, "state": run.get("state"), "population": pop, "counters": run.get("counters"), "staging": _staging_view(run),
        "documents": {"in_sample": len(groups), "extracted": len(extracted), "readable": readable, "failed": sum(failures.values()), "alias": sum(1 for g in groups if g["state"] == ALIAS),
                      "pending": sum(1 for g in groups if g["state"] in PENDING_STATES), "multi_specialty": sum(1 for g in groups if len(g["specialties"]) > 1)},
        "field_rates_pct": {"name": pct(fields["first_name"], readable), "email": pct(fields["email"], readable), "phone": pct(fields["phone"], readable)},
        "review": {"required": review, "required_pct": pct(review, len(extracted)), "complete_without_review": len(extracted) - review, "by_reason": dict(sorted(codes.items()))},
        "failures": dict(sorted((k, v) for k, v in failures.items() if k)),
        "reconciliation": {"strong_existing": sum(1 for g in extracted if any(m["strength"] == "STRONG" for m in (g.get("reconciliation") or {}).get("profiles", []))),
                           "probable_existing": sum(1 for g in extracted if any(m["strength"] == "PROBABLE" for m in (g.get("reconciliation") or {}).get("profiles", []))),
                           "same_person_in_run": sum(1 for g in extracted if (g.get("reconciliation") or {}).get("in_run"))},
        "by_specialty": sorted(by_spec.values(), key=lambda e: e["specialty"]),
        "scale_estimate": {"unique_contents": pop.get("content_groups"), "sample_review_or_failure_rate_pct": pct(review + sum(failures.values()), len(groups)),
                           "estimated_records_needing_attention": round(rate * pop["content_groups"]) if rate is not None and pop.get("content_groups") and len(groups) >= SAMPLE_MIN else None,
                           "note": "Extrapolación lineal de una muestra chica: orientativa, no un compromiso."},
    }


def review_view(run_id: str) -> list[dict]:
    """Resultados INDIVIDUALES para la vista del owner (CONTIENE datos personales: solo se llama desde la ruta protegida). Resuelve los perfiles coincidentes a un nombre visible."""
    out = []
    for g in manifest.list_groups(run_id):
        extraction = g.get("extraction") or {}
        matches = []
        for m in (g.get("reconciliation") or {}).get("profiles", []):
            profile = profile_repository.get(m["profile_id"]) or {}
            matches.append({**m, "display": f"{profile.get('last_name') or ''}, {profile.get('first_name') or ''}".strip(", ")})
        out.append({"content_key": g["content_key"], "state": g["state"], "failure": g.get("failure"), "specialties": g["specialties"], "copies": len(g["members"]),
                    "profile_skills_proposal": g.get("profile_skills_proposal"), "alias_of": g.get("alias_of"),
                    "review": g.get("review") or {"required": g["state"] == FAILED, "reasons": [g["failure"]] if g.get("failure") else []},
                    "first_name": extraction.get("first_name"), "last_name": extraction.get("last_name"), "email": extraction.get("email"), "phone": extraction.get("phone"),
                    "candidates": extraction.get("candidates") or {}, "evidence": extraction.get("evidence") or {}, "advisory": extraction.get("advisory") or [],
                    "matches": matches, "in_run": (g.get("reconciliation") or {}).get("in_run") or []})
    out.sort(key=lambda r: (not r["review"]["required"], r["content_key"]))
    return out
