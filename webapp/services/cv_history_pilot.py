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

AUTORIZACIÓN: crear el lote exige la frase de autorización del owner para procesar datos personales; queda registrada (quién y cuándo) en el lote.

CALIDAD DE LA IDENTIDAD (incremento 2.1): la tasa de «nombre obtenido» mide COBERTURA, no exactitud. El owner dictamina cada documento (propuesta correcta / corrección manual / identidad no determinada) y el reporte
separa tres cosas: cobertura (cuántos documentos tienen propuesta), precisión de las propuestas EVALUADAS (correctas / correctas + incorrectas) y casos no determinados (la ausencia de datos no es un error de identidad).
Los dictámenes son por lote y append-only, en archivos aparte: nunca reescriben el resultado de la extracción ni se pisan entre lotes. Un lote DERIVADO («reevaluación») recalcula la identidad de un lote original con las
reglas vigentes SOLO desde lo ya guardado —sin descargar ni releer ningún documento— y deja el original intacto; permite comparar contra los dictámenes del original y reutilizar su identidad confirmada como referencia."""
from __future__ import annotations

import hashlib
import re
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
KIND_PILOT, KIND_REARBITRATION, KIND_IMPORT = "pilot", "rearbitration", "import"
V_CORRECT, V_CORRECTED, V_UNDETERMINED = "CORRECT", "CORRECTED", "UNDETERMINED"
VERDICTS = (V_CORRECT, V_CORRECTED, V_UNDETERMINED)
LEGACY_V1 = "LEGACY_V1"
_NAME_FIELD = re.compile(r"^[^\W\d_]+(?:[ '\-.][^\W\d_]+)*$", re.UNICODE)


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


def _import_groups(run_id: str) -> dict[str, dict]:
    """Lote de IMPORTACIÓN: los contenidos pendientes se materializan desde el índice (los registros por contenido se escriben al procesarlos, no todos de una vez)."""
    index = manifest.get_index(run_id) or {"groups": []}
    persisted = {g["content_key"]: g for g in manifest.list_groups(run_id)}
    out = {}
    for entry in sorted(index["groups"], key=lambda g: g["content_key"]):
        out[entry["content_key"]] = persisted.get(entry["content_key"]) or {
            **entry, "state": SELECTED, "sha256": None, "extraction": None, "reconciliation": None, "review": None, "alias_of": None, "profile_skills_proposal": ", ".join(entry["specialties"])}
    return out


def _pending_groups(run_id: str, kind: str) -> list[dict]:
    groups = list(_import_groups(run_id).values()) if kind == KIND_IMPORT else manifest.list_groups(run_id)
    return [g for g in groups if g["state"] in PENDING_STATES]


def process_run(run_id: str, token: str, *, clock=time.monotonic, sleep=time.sleep, time_budget: float = PROCESS_TIME_BUDGET_S, max_documents: int | None = None) -> dict:
    """Procesa los documentos pendientes (SELECTED/FAILED) dentro de un presupuesto de tiempo y, opcionalmente, de un máximo de documentos por tanda (`max_documents`). Reanudable e idempotente: lo ya EXTRAÍDO
    no se vuelve a descargar. Un error por documento se registra y el lote sigue; un token vencido (401) detiene el lote sin marcar nada como fallido. Sirve a los lotes del piloto y a los de importación."""
    run = manifest.get_run(run_id)
    kind = run.get("kind", KIND_PILOT)
    if kind not in (KIND_PILOT, KIND_IMPORT):                                              # un lote derivado se calcula solo con lo guardado: jamás descarga ni lee documentos
        return {"processed": 0, "failed": 0, "remaining": 0, "stopped_reason": "NOT_A_PILOT_RUN"}
    started = clock()
    try:
        profiles = profile_repository.list()
    except profile_repository.ProfileStorageError:
        return {"processed": 0, "failed": 0, "remaining": _pending(run_id), "stopped_reason": "PROFILES_UNAVAILABLE"}
    processed = failed = 0
    stopped: str | None = None
    for group in _pending_groups(run_id, kind):
        if clock() - started >= time_budget:
            stopped = "TIME_BUDGET"
            break
        if max_documents is not None and processed + failed >= max_documents:
            stopped = "BATCH_LIMIT"
            break
        try:
            outcome = _process_one(run_id, group, token, profiles, sleep)
        except manifest.ManifestDurabilityError:
            stopped = "PERSISTENCE_ERROR"
            break
        if outcome == "TOKEN_EXPIRED":
            stopped = "TOKEN_EXPIRED"
            break
        if outcome == "failed":
            failed += 1
        else:
            processed += 1
    if stopped != "PERSISTENCE_ERROR" and kind == KIND_PILOT:
        _recompute_in_run(run_id)
    _refresh_counters(run_id, stopped)
    return {"processed": processed, "failed": failed, "remaining": _pending(run_id), "stopped_reason": stopped}


def _process_one(run_id: str, group: dict, token: str, profiles: list[dict], sleep) -> str:
    """Descarga (solo lectura), valida, extrae y concilia UN contenido. Devuelve `processed`, `failed` o `TOKEN_EXPIRED`. `ManifestDurabilityError` se deja propagar."""
    key = group["content_key"]
    rep = group["representative"]
    try:
        data = _download(rep["item_id"], rep["drive_id"], token, sleep)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return "TOKEN_EXPIRED"
        _fail(run_id, group, DOWNLOAD_FORBIDDEN if exc.code == 403 else DOWNLOAD_NOT_FOUND if exc.code == 404 else DOWNLOAD_ERROR)
        return "failed"
    except (urllib.error.URLError, OSError, ValueError):
        _fail(run_id, group, DOWNLOAD_ERROR)
        return "failed"
    digest = hashlib.sha256(data).hexdigest()
    if group.get("sha256") and group["sha256"] != digest:
        _fail(run_id, group, CONTENT_CHANGED)                                              # nunca se pisa en silencio un contenido ya registrado
        return "failed"
    if len(data) != int(group["size"]):
        _fail(run_id, group, SIZE_MISMATCH)
        return "failed"
    try:
        profile_document_validation.validate_upload(rep["name"], data)
    except profile_document_validation.ProfileDocumentValidationError:
        _fail(run_id, group, FORMAT_INVALID)
        return "failed"
    manifest.append_audit(run_id, key, "DOWNLOADED", size=len(data))
    primary = next((g for g in manifest.list_groups(run_id) if g["content_key"] != key and g.get("sha256") == digest and g["state"] == EXTRACTED), None)
    if primary is not None:                                                                # mismo contenido que otro registro del lote: identidad DOCUMENTAL repetida
        _make_alias(run_id, group, primary, digest)
        return "processed"
    extraction = cv_extract.extract(rep["name"], data)
    del data
    matches = cv_reconcile.reconcile_with_profiles(extraction, profiles)
    group.update({"state": EXTRACTED, "sha256": digest, "extraction": extraction, "reconciliation": {"profiles": matches, "in_run": []},
                  "review": _review(extraction, matches, []), "processed_at": manifest.now_iso()})
    manifest.put_group(run_id, key, group)
    manifest.append_audit(run_id, key, "EXTRACTED", code=extraction.get("failure"), review=group["review"]["required"], reasons=",".join(group["review"]["reasons"]))
    return "processed"


# ═══════════════════ identidad: propuesta, dictamen del owner y lote derivado ═══════════════════
def _proposal(extraction: dict | None) -> dict | None:
    """La propuesta automática de identidad de una extracción (None si no hay). Una propuesta NO es una identidad verificada. Para datos de la versión 1 (sin `name_decision`) la regla es `V1`."""
    ext = extraction or {}
    if ext.get("failure") or not ext.get("first_name"):
        return None
    decision = ext.get("name_decision") or {}
    return {"first_name": ext.get("first_name"), "last_name": ext.get("last_name"), "rule": decision.get("rule") or "V1", "state": decision.get("state") or LEGACY_V1,
            "extractor_version": ext.get("extractor_version", 1)}


def _identity_key(first: str | None, last: str | None) -> frozenset:
    """Clave para comparar identidades: conjunto de palabras sin tildes ni mayúsculas (el orden nombre/apellido no cambia a la persona)."""
    return frozenset(cv_extract.fold(w) for w in f"{first or ''} {last or ''}".split())


def _current_verdict(doc: dict | None) -> dict | None:
    return (doc or {}).get("current")


def confirmed_identity(verdict: dict | None) -> dict | None:
    """La identidad que el owner confirmó (propuesta correcta → la propuesta evaluada; corrección manual → la corrección); None si no hay o fue «no determinada»."""
    cur = _current_verdict(verdict) if verdict and "current" in verdict else verdict
    if not cur:
        return None
    if cur["status"] == V_CORRECT and cur.get("evaluated_proposal"):
        return {"first_name": cur["evaluated_proposal"]["first_name"], "last_name": cur["evaluated_proposal"]["last_name"]}
    if cur["status"] == V_CORRECTED and cur.get("corrected"):
        return dict(cur["corrected"])
    return None


def _clean_name(value: str, label: str) -> str:
    value = " ".join((value or "").split())
    if not value or len(value) > 60 or not _NAME_FIELD.match(value):
        raise PilotInputError(f"{label} inválido: solo letras, espacios, guion o apóstrofo (hasta 60 caracteres).")
    return value


def record_verdict(run_id: str, content_key: str, status: str, *, actor: str, first_name: str = "", last_name: str = "") -> dict:
    """Dictamen del owner sobre UN documento de ESTE lote. Queda en un archivo aparte (append-only); el resultado de la extracción no se toca. CORRECT exige que haya una propuesta que evaluar; CORRECTED exige
    nombre y apellido; UNDETERMINED no lleva datos. Se guarda la propuesta evaluada (o su ausencia) para que el dictamen siga siendo interpretable aunque las reglas cambien."""
    if status not in VERDICTS:
        raise PilotInputError("Dictamen inválido.")
    run = manifest.get_run(run_id)
    if (run.get("staging") or {}).get("state") != "PRESENT":
        raise PilotInputError("Los datos personales del lote ya no están disponibles: no se puede dictaminar.")
    group = manifest.get_group(run_id, content_key)
    if group is None or group.get("state") != EXTRACTED or (group.get("extraction") or {}).get("failure"):
        raise PilotInputError("Solo se dictamina un documento extraído y legible.")
    proposal = _proposal(group["extraction"])
    entry: dict = {"status": status, "actor": actor, "evaluated_proposal": proposal, "corrected": None}
    if status == V_CORRECT:
        if proposal is None:
            raise PilotInputError("El documento no tiene una propuesta que confirmar: cargá la corrección manual o marcalo como no determinado.")
    elif status == V_CORRECTED:
        entry["corrected"] = {"first_name": _clean_name(first_name, "Nombre"), "last_name": _clean_name(last_name, "Apellido")}
    doc = manifest.append_verdict(run_id, content_key, entry)
    manifest.append_audit(run_id, content_key, "VERDICT", code=status, had_proposal=proposal is not None, seq=doc["current"]["seq"])
    return doc


def _quality(groups: list[dict], verdicts: dict[str, dict]) -> dict:
    """Cobertura, precisión de las propuestas EVALUADAS y casos no determinados — por separado: que falte el dato no es un error de identidad, y una propuesta sin dictamen no cuenta ni a favor ni en contra."""
    readable = [g for g in groups if g["state"] == EXTRACTED and not (g.get("extraction") or {}).get("failure")]
    q = Counter()
    by_state: dict[str, Counter] = {}
    for g in readable:
        proposal = _proposal(g["extraction"])
        cur = _current_verdict(verdicts.get(g["content_key"]))
        status = cur["status"] if cur else None
        state = (proposal or {}).get("state") or "—"
        if proposal:
            q["proposals"] += 1
            if status == V_CORRECT:
                q["correct"] += 1
                by_state.setdefault(state, Counter())["correct"] += 1
            elif status == V_CORRECTED:
                q["wrong"] += 1
                by_state.setdefault(state, Counter())["wrong"] += 1
            elif status == V_UNDETERMINED:
                q["proposals_undetermined"] += 1
            else:
                q["proposals_unevaluated"] += 1
        else:
            q["without_proposal"] += 1
            if status == V_CORRECTED:
                q["without_proposal_resolved_manually"] += 1
            elif status == V_UNDETERMINED:
                q["without_proposal_undetermined"] += 1
            elif status is None:
                q["without_proposal_unevaluated"] += 1
        q["verdict_" + (status or "PENDING")] += 1
    pct = lambda n, d: round(100.0 * n / d, 1) if d else None            # noqa: E731
    evaluated = q["correct"] + q["wrong"]
    return {
        "readable": len(readable),
        "coverage": {"with_proposal": q["proposals"], "without_proposal": q["without_proposal"], "coverage_pct": pct(q["proposals"], len(readable))},
        "verdicts": {"pending": q["verdict_PENDING"], "correct": q["verdict_" + V_CORRECT], "corrected": q["verdict_" + V_CORRECTED], "undetermined": q["verdict_" + V_UNDETERMINED],
                     "evaluated_documents": len(readable) - q["verdict_PENDING"]},
        "proposal_quality": {"evaluated": evaluated, "correct": q["correct"], "wrong": q["wrong"], "precision_pct": pct(q["correct"], evaluated),
                             "unevaluated": q["proposals_unevaluated"], "undetermined": q["proposals_undetermined"],
                             "by_state": {s: {"correct": c["correct"], "wrong": c["wrong"], "precision_pct": pct(c["correct"], c["correct"] + c["wrong"])} for s, c in sorted(by_state.items())}},
        "undetermined": {"with_proposal": q["proposals_undetermined"], "without_proposal_undetermined": q["without_proposal_undetermined"],
                         "without_proposal_resolved_manually": q["without_proposal_resolved_manually"], "without_proposal_unevaluated": q["without_proposal_unevaluated"]},
    }


def _identity_states(groups: list[dict]) -> dict:
    states = Counter()
    versions = Counter()
    for g in groups:
        ext = g.get("extraction") or {}
        if g["state"] != EXTRACTED or ext.get("failure"):
            continue
        states[ext.get("name_state") or LEGACY_V1] += 1
        versions[str(ext.get("extractor_version", 1))] += 1
    return {"by_state": dict(sorted(states.items())), "extractor_versions": dict(sorted(versions.items()))}


def start_rearbitration(source_run_id: str, *, actor: str) -> dict:
    """Lote DERIVADO: reevalúa la identidad de un lote original con las reglas vigentes SOLO desde lo ya guardado (candidatos, nombre de archivo y emails). NO descarga ni lee ningún documento (no usa token de
    Graph) y NO modifica nada del lote original. Cada documento conserva su `content_key` y apunta a su origen (`derived_from`). Lo que el extractor no guardó (candidatos de texto adicionales, etiquetas) queda
    declarado como límite en cada decisión: por eso la reevaluación es una APROXIMACIÓN, no un reemplazo de releer los documentos."""
    source = manifest.get_run(source_run_id)
    if source.get("kind", KIND_PILOT) != KIND_PILOT:
        raise PilotInputError("Solo se reevalúa un lote original del piloto.")
    if (source.get("staging") or {}).get("state") != "PRESENT":
        raise PilotInputError("Los datos personales del lote original ya no están disponibles: no hay de dónde reevaluar.")
    groups = manifest.list_groups(source_run_id)
    extracted = [g for g in groups if g["state"] == EXTRACTED and g.get("extraction")]
    if not extracted:
        raise PilotInputError("El lote no tiene documentos extraídos para reevaluar.")
    try:
        profiles = profile_repository.list()
    except profile_repository.ProfileStorageError:
        raise PilotInputError("No se pudo leer el Banco para conciliar.") from None
    run_id = manifest.new_run_id(suffix=secrets.token_hex(2))
    until = (datetime.now(timezone.utc) + timedelta(days=manifest.STAGING_RETENTION_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    versions = sorted({int((g["extraction"] or {}).get("extractor_version", 1)) for g in extracted})
    manifest.create_run(run_id, {
        "staging": {"state": "PRESENT", "retention_days": manifest.STAGING_RETENTION_DAYS, "retention_until": until, "auto_delete": False,
                    "policy": "Área temporal con datos personales (derivada del lote original): vigencia hasta la fecha indicada y eliminación por acción explícita del owner; la auditoría se conserva."},
        "kind": KIND_REARBITRATION, "state": "DONE", "created_by": actor, "source": source.get("source"),
        "derived_from": {"run_id": source_run_id, "extractor_versions": versions, "to_extractor_version": cv_extract.EXTRACTOR_VERSION, "mode": "OFFLINE_FROM_STORED_DATA"},
        "authorization": {"inherited_from": source_run_id, "confirmed_by": (source.get("authorization") or {}).get("confirmed_by"), "derived_by": actor, "derived_at": manifest.now_iso()},
        "params": {"sample_size": len(extracted)}, "population": source.get("population") or {}, "counters": {"selected": 0, "extracted": 0, "failed": 0, "alias": 0}})
    for g in groups:
        if g["state"] not in (EXTRACTED, ALIAS):
            continue                                                                       # los FAILED no tienen datos que reevaluar y reintentarlos exigiría descargar
        base = {k: g.get(k) for k in ("content_key", "size", "hash", "members", "specialties", "representative", "profile_skills_proposal", "sha256", "alias_of")}
        derived = {**base, "state": g["state"], "failure": None, "derived_from": {"run_id": source_run_id, "content_key": g["content_key"]}, "review": None, "reconciliation": None, "extraction": None}
        if g["state"] == EXTRACTED:
            extraction = cv_extract.rearbitrate(g["extraction"], g["representative"]["name"])
            matches = cv_reconcile.reconcile_with_profiles(extraction, profiles)
            derived.update({"extraction": extraction, "reconciliation": {"profiles": matches, "in_run": []}, "review": _review(extraction, matches, []), "processed_at": manifest.now_iso(),
                            "source_extractor_version": (g["extraction"] or {}).get("extractor_version", 1)})
        manifest.put_group(run_id, g["content_key"], derived)
        manifest.append_audit(run_id, g["content_key"], "REARBITRATED" if g["state"] == EXTRACTED else "ALIAS", code=((derived["extraction"] or {}).get("name_decision") or {}).get("rule"), from_run=source_run_id)
    _recompute_in_run(run_id)
    _refresh_counters(run_id, None)
    return manifest.get_run(run_id)


def _comparison(run: dict, groups: list[dict]) -> dict | None:
    """Comparación de un lote derivado contra su ORIGEN: qué pasó con cada propuesta anterior y cómo queda frente a las identidades que el owner confirmó en el original. Sin datos personales (solo conteos).
    None si el origen ya no está disponible (se descartó su área temporal)."""
    src = (run.get("derived_from") or {}).get("run_id")
    if not src:
        return None
    try:
        source = manifest.get_run(src)
        if (source.get("staging") or {}).get("state") != "PRESENT":
            return None
        src_groups = {g["content_key"]: g for g in manifest.list_groups(src)}
        src_verdicts = manifest.list_verdicts(src)
    except manifest.ManifestError:
        return None
    c = Counter()
    wrong, right = Counter(), Counter()
    ref = Counter()
    for g in groups:
        if g["state"] != EXTRACTED or (g.get("extraction") or {}).get("failure"):
            continue
        origin = src_groups.get(g["content_key"])
        if not origin or not origin.get("extraction"):
            continue
        before, after = _proposal(origin["extraction"]), _proposal(g["extraction"])
        if before and after:
            same = _identity_key(before["first_name"], before["last_name"]) == _identity_key(after["first_name"], after["last_name"])
            move = "unchanged" if same else "changed"
        else:
            move = "removed" if before else "added" if after else "none"
        c[move] += 1
        cur = _current_verdict(src_verdicts.get(g["content_key"]))
        if before and cur and cur["status"] == V_CORRECTED:
            wrong[move] += 1                                                               # el original proponía algo y el owner lo corrigió
        elif before and cur and cur["status"] == V_CORRECT:
            right[move] += 1
        known = confirmed_identity(cur)
        if known:
            if not after:
                ref["abstains"] += 1
            elif _identity_key(after["first_name"], after["last_name"]) == _identity_key(known["first_name"], known["last_name"]):
                ref["matches"] += 1
            else:
                ref["contradicts"] += 1
    return {"source_run_id": src, "moves": dict(c),
            "original_wrong_proposals": {"total": sum(wrong.values()), "removed": wrong["removed"], "changed": wrong["changed"], "still_same": wrong["unchanged"]},
            "original_correct_proposals": {"total": sum(right.values()), "kept": right["unchanged"], "lost": right["removed"], "changed": right["changed"]},
            "against_confirmed_identity": {"known": sum(ref.values()), "matches": ref["matches"], "abstains": ref["abstains"], "contradicts": ref["contradicts"]},
            "note": "Reevaluación sin releer los documentos: usa solo los datos guardados. Una propuesta que desaparece no es un error (falta evidencia); una que contradice la identidad confirmada sí lo es."}


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


def _groups_of(run_id: str, run: dict) -> list[dict]:
    """Los registros del lote; en uno de IMPORTACIÓN incluye los contenidos todavía sin analizar (materializados desde el índice) para que los pendientes se cuenten bien."""
    return list(_import_groups(run_id).values()) if run.get("kind") == KIND_IMPORT else manifest.list_groups(run_id)


def _pending(run_id: str) -> int:
    return len(_pending_groups(run_id, manifest.get_run(run_id).get("kind", KIND_PILOT)))


def _refresh_counters(run_id: str, stopped: str | None) -> None:
    kind = manifest.get_run(run_id).get("kind", KIND_PILOT)
    groups = list(_import_groups(run_id).values()) if kind == KIND_IMPORT else manifest.list_groups(run_id)
    states = Counter(g["state"] for g in groups)

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
        _assert_no_active_drafts(run_id)                                                   # 1) antes de escribir nada
        if "final_report" not in run:
            report = run_report(run_id)
            manifest.update_run(run_id, lambda d: d.update({"final_report": report, "staging": {**d["staging"], "state": "DISCARDING"}}))   # desde acá no se aceptan borradores nuevos
            try:
                _assert_no_active_drafts(run_id)                                           # 2) con el área ya marcada: cierra la carrera con un guardado simultáneo
            except PilotInputError:
                manifest.update_run(run_id, lambda d: (d.pop("final_report", None), d.update({"staging": {**d["staging"], "state": "PRESENT"}})))
                raise
        removed = manifest.delete_staging_files(run_id)
        audit_dir = manifest._run_dir(run_id) / "audit"
        for entry in sorted(audit_dir.glob("*.json")) if audit_dir.is_dir() else []:          # la auditoría por contenido se conserva; se agrega el evento de cierre
            manifest.append_audit(run_id, entry.stem, "STAGING_DISCARDED")
        manifest.update_run(run_id, lambda d: d.update({"staging": {**d["staging"], "state": "DISCARDED", "discarded_at": manifest.now_iso(), "discarded_by": actor, "files_removed": removed}}))
    return manifest.get_run(run_id)["staging"]


def _assert_no_active_drafts(run_id: str) -> None:
    """Mientras no se defina la política de retención, el área temporal NO se elimina si hay borradores guardados de excepciones sin resolver que dependen de ella. Nunca se borra un borrador."""
    from webapp.services import cv_history_import_exec as exec_service                     # import diferido: ese módulo importa este
    active = exec_service.active_drafts(run_id)
    if active:
        raise PilotInputError(f"No se pueden eliminar los datos temporales: hay {active} borradores guardados de excepciones sin resolver que dependen de ellos. Los borradores no se borran solos (la política de retención "
                              "está pendiente). Resolvé o descartá esas excepciones desde «Históricos sin validación» y volvé a intentarlo.")


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
    groups = _groups_of(run_id, run)
    extracted = [g for g in groups if g["state"] == EXTRACTED]
    ok = [g for g in extracted if not (g["extraction"] or {}).get("failure")]
    verdicts = manifest.list_verdicts(run_id)
    quality = _quality(groups, verdicts)
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
        "run_id": run_id, "kind": run.get("kind", KIND_PILOT), "derived_from": run.get("derived_from"), "plan": run.get("plan"), "state": run.get("state"), "population": pop, "counters": run.get("counters"), "staging": _staging_view(run),
        "documents": {"in_sample": len(groups), "extracted": len(extracted), "readable": readable, "failed": sum(failures.values()), "alias": sum(1 for g in groups if g["state"] == ALIAS),
                      "pending": sum(1 for g in groups if g["state"] in PENDING_STATES), "multi_specialty": sum(1 for g in groups if len(g["specialties"]) > 1)},
        "coverage_pct": {"name": pct(fields["first_name"], readable), "email": pct(fields["email"], readable), "phone": pct(fields["phone"], readable)},
        "identity": {**_identity_states(groups), **{k: quality[k] for k in ("coverage", "verdicts", "proposal_quality", "undetermined")}, "readable": quality["readable"]},
        "comparison": _comparison(run, groups) if run.get("kind") == KIND_REARBITRATION else None,
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
    run = manifest.get_run(run_id)
    is_import = run.get("kind") == KIND_IMPORT
    plan = ((manifest.get_plan(run_id) or {}).get("decisions") or {}) if is_import else {}
    verdicts = manifest.list_verdicts(run_id)
    origin = (run.get("derived_from") or {}).get("run_id")
    origin_groups = {g["content_key"]: g for g in manifest.list_groups(origin)} if origin else {}
    origin_verdicts = manifest.list_verdicts(origin) if origin else {}
    for g in _groups_of(run_id, run):
        extraction = g.get("extraction") or {}
        verdict_doc = verdicts.get(g["content_key"])
        before = _proposal((origin_groups.get(g["content_key"]) or {}).get("extraction")) if origin else None
        reference = confirmed_identity(origin_verdicts.get(g["content_key"])) if origin else None
        matches = []
        for m in (g.get("reconciliation") or {}).get("profiles", []):
            profile = profile_repository.get(m["profile_id"]) or {}
            matches.append({**m, "display": f"{profile.get('last_name') or ''}, {profile.get('first_name') or ''}".strip(", ")})
        out.append({"content_key": g["content_key"], "state": g["state"], "failure": g.get("failure"), "specialties": g["specialties"], "copies": len(g["members"]),
                    "profile_skills_proposal": g.get("profile_skills_proposal"), "alias_of": g.get("alias_of"),
                    "review": g.get("review") or {"required": g["state"] == FAILED, "reasons": [g["failure"]] if g.get("failure") else []},
                    "first_name": extraction.get("first_name"), "last_name": extraction.get("last_name"), "email": extraction.get("email"), "phone": extraction.get("phone"),
                    "candidates": extraction.get("candidates") or {}, "evidence": extraction.get("evidence") or {}, "advisory": extraction.get("advisory") or [],
                    "name_state": extraction.get("name_state") or (LEGACY_V1 if extraction else None), "decision": extraction.get("name_decision") or {}, "proposal": _proposal(extraction),
                    "verdict": _current_verdict(verdict_doc), "verdict_count": len((verdict_doc or {}).get("history") or []), "can_verdict": (not is_import) and g["state"] == EXTRACTED and bool(extraction) and not extraction.get("failure"),
                    "plan": plan.get(g["content_key"]),
                    "original_proposal": before, "reference_identity": reference,
                    "matches": matches, "in_run": (g.get("reconciliation") or {}).get("in_run") or []})
    out.sort(key=lambda r: (not r["review"]["required"], r["content_key"]))
    return out
