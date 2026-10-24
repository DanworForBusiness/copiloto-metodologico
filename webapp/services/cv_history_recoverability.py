"""cv_history_recoverability.py — DIAGNÓSTICO DE SOLO LECTURA de la recuperabilidad de las excepciones abiertas de una importación histórica de CVs (Capital Humano).

Responde «¿cuántas de las excepciones abiertas podrían recuperarse con una segunda conciliación de identidad, sin relajar los criterios de confianza?». NO es un segundo motor de identidad y NO cambia ningún veredicto ni umbral:
reutiliza lo que ya existe —el listado de excepciones (`cv_history_import_exec.exceptions_overview`), la evidencia que el extractor dejó en el staging (regla R1–R6, alternativas, emails y celular candidatos, fallas de lectura),
los hechos que usa `cv_history_import.build_plan` (`_facts`, `_contradicts`), la conciliación (`cv_reconcile.reconcile_with_profiles`, `cluster_in_run`, fuerza STRONG/PROBABLE/WEAK) y el repositorio actual de perfiles—.
Lo único «nuevo» es MIRAR esa evidencia de otra forma: cada email candidato por separado (el plan solo usó el email cuando era único), el celular, y el Banco de HOY (que ya incluye los perfiles creados por la importación).

CADA excepción abierta cae en UNA categoría (primera que aplica):
  PROBLEMA_EXTRACCION      falla de lectura o sin registro de staging: no es un problema de identidad, requiere otro tratamiento técnico;
  CONFLICTO_IDENTIDAD      evidencias contradictorias (motivos de conflicto del plan, identidades que se contradicen, varios perfiles candidatos o un candidato que el documento contradice): revisión humana explícita;
  POSIBLE_VINCULACION      exactamente UN perfil vivo del Banco con coincidencia STRONG o PROBABLE y sin contradicción (con o sin respaldo propio de nombre en el documento);
  ALTA_ASISTIDA            una sola identidad plausible y anclada (propuesta del extractor, o una única alternativa corroborada/declarada/con texto y archivo coincidentes) sin perfil coincidente: una persona la confirma;
  INFORMACION_INSUFICIENTE el resto (sin identidad, solo estructural, o sin evidencia adicional).
UNA PROPUESTA NO ES UNA IDENTIDAD VERIFICADA: ni STRONG ni PROBABLE ni WEAK autorizan a vincular o crear automáticamente (`NAME_UNCORROBORATED` no se promueve). Las agrupaciones de documentos son TENTATIVAS.

PRIVACIDAD: el informe agregado lleva solo conteos y códigos. El detalle (protegido) lleva `content_key` y `profile_id` opacos, códigos y señales; JAMÁS nombres, emails ni teléfonos.
Este módulo no escribe nada: solo importa funciones de lectura (un test lo verifica)."""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations

from webapp.services import cv_extract, cv_history_import as planning, cv_history_import_exec as ex, cv_history_manifest as manifest, cv_history_pilot as pilot, cv_reconcile, profile_repository

LINK, ASSISTED, CONFLICT, INSUFFICIENT, EXTRACTION = "POSIBLE_VINCULACION", "ALTA_ASISTIDA", "CONFLICTO_IDENTIDAD", "INFORMACION_INSUFICIENTE", "PROBLEMA_EXTRACCION"
CATEGORIES = (LINK, ASSISTED, CONFLICT, INSUFFICIENT, EXTRACTION)
STRENGTHS = (cv_reconcile.STRONG, cv_reconcile.PROBABLE, cv_reconcile.WEAK)
_RANK = {cv_reconcile.STRONG: 0, cv_reconcile.PROBABLE: 1, cv_reconcile.WEAK: 2}
_CONFLICT_REASONS = {planning.NAME_CONFLICT, planning.EMAIL_CONFLICT, planning.EXISTING_NAME_CONFLICT, planning.EMAIL_NAME_MISMATCH, planning.MULTIPLE_EXISTING_MATCH, planning.EXISTING_DELETED}
_ANCHOR_BY_STATE = {"CORROBORATED": "CORROBORADA", "DECLARED": "DECLARADA", "UNANCHORED_AGREEMENT": "TEXTO_Y_ARCHIVO_COINCIDEN"}
_PHONE_ONLY = {cv_reconcile.PHONE_MATCH}


class RecoverabilityError(Exception):
    """Error de uso o de datos, con un código estable (sin datos personales)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ═══════════════════ lectura (solo lectura) ═══════════════════
def load_inputs(run_id: str) -> dict:
    """Todo lo que el diagnóstico necesita, leído UNA vez y sin escribir nada: lote, plan, excepciones abiertas, registros de staging, libro de ejecución y perfiles del Banco de hoy."""
    run = manifest.get_run(run_id)
    if run.get("kind") != pilot.KIND_IMPORT:
        raise RecoverabilityError("NOT_AN_IMPORT_RUN")
    plan = manifest.get_plan(run_id)
    if not plan:
        raise RecoverabilityError("NO_PLAN")
    if (run.get("staging") or {}).get("state") != "PRESENT":
        raise RecoverabilityError("STAGING_NOT_AVAILABLE")
    overview = ex.exceptions_overview(run_id, status="open")
    rows = overview["rows"]
    return {"run": run, "plan": plan, "overview": overview, "rows": rows, "groups": {r["content_key"]: manifest.get_group(run_id, r["content_key"]) for r in rows},
            "ledgers": manifest.list_ledgers(run_id), "profiles": profile_repository.list()}


# ═══════════════════ evidencia por documento ═══════════════════
def _usable_emails(extraction: dict) -> list[str]:
    emails = [cv_reconcile.norm_email(x) for x in ((extraction.get("candidates") or {}).get("emails") or [])]
    single = cv_reconcile.norm_email(extraction.get("email"))
    if single and single not in emails:
        emails.insert(0, single)
    return [x for x in dict.fromkeys(emails) if x and not cv_extract.is_role_mailbox(x)]


def _persons(group: dict, facts: dict) -> list[dict]:
    """Variantes de persona del documento para comparar contra el Banco y contra otros documentos. Una por identidad que el documento «reclama» (propuesta, o alternativas ancladas) con el email único y el celular, más una
    SOLO-EMAIL por cada email candidato adicional (el plan ignoró los emails cuando había varios). Solo primitivas ya extraídas: no se infiere ningún dato."""
    e = group["extraction"]
    emails = _usable_emails(e)
    base_email = facts["email"] if facts["email"] in emails else None
    names: list[tuple] = []
    proposal = facts["proposal"]
    if proposal:
        names.append((proposal["first_name"], proposal["last_name"]))
    else:
        names.extend((a["tokens"], "") for a in ((e.get("name_decision") or {}).get("alternatives") or []) if a.get("state") in planning._CLAIMING_STATES)
    variants = [{"first_name": first, "last_name": last, "email": base_email, "phone": e.get("phone"), "phone_digits": e.get("phone_digits")} for first, last in (names or [(None, None)])]
    variants += [{"first_name": None, "last_name": None, "email": x, "phone": None, "phone_digits": None} for x in emails if x != base_email]
    return variants


def _match_profiles(variants: list[dict], live: list[dict]) -> dict[str, dict]:
    """Por perfil vivo: la MEJOR fuerza entre todas las variantes y la unión de señales (`cv_reconcile`, sin umbrales nuevos)."""
    found: dict[str, dict] = {}
    for person in variants:
        for m in cv_reconcile.reconcile_with_profiles(person, live):
            slot = found.setdefault(m["profile_id"], {"strength": m["strength"], "signals": set()})
            slot["signals"] |= set(m["signals"])
            if _RANK[m["strength"]] < _RANK[slot["strength"]]:
                slot["strength"] = m["strength"]
    return found


def _identity_candidates(extraction: dict, facts: dict) -> list[dict]:
    """Identidades que el documento reclama con un ancla (la propuesta del extractor, o las alternativas CORROBORATED/DECLARED/UNANCHORED_AGREEMENT)."""
    if facts["proposal"]:
        return [{"state": facts["proposal"]["state"], "words": facts["words"]}]
    return [{"state": a["state"], "words": planning._words(a["tokens"], "")} for a in ((extraction.get("name_decision") or {}).get("alternatives") or []) if a.get("state") in planning._CLAIMING_STATES]


def classify(row: dict, group: dict | None, ledger: dict | None, live: list[dict], run_id: str) -> tuple[dict, list[dict]]:
    """Clasifica UNA excepción abierta. Devuelve `(detalle, variantes de persona)` (las variantes sirven para agrupar). PURA."""
    reasons = list(row.get("plan_reasons") or [])
    detail = {"content_key": row["content_key"], "estado": row["status"], "motivo_original": row.get("reason"), "razones_plan": reasons, "accion_plan": row.get("plan_action"), "estado_ledger": (ledger or {}).get("state"),
              "regla": None, "estado_nombre": None, "categoria": None, "subcategoria": None, "evidencia": [], "fuerza": None, "senales": [], "perfiles": [], "confirmar": [], "riesgos": []}
    extraction = (group or {}).get("extraction")
    if group is None or not extraction:
        return {**detail, "categoria": EXTRACTION, "subcategoria": "SIN_REGISTRO_DE_STAGING", "evidencia": ["STAGING_MISSING"]}, []
    detail["regla"] = (extraction.get("name_decision") or {}).get("rule")
    detail["estado_nombre"] = extraction.get("name_state")
    if extraction.get("failure") or planning.READ_FAILURE in reasons or group.get("state") == pilot.FAILED:
        code = extraction.get("failure") or group.get("failure") or "READ_FAILURE"
        return {**detail, "categoria": EXTRACTION, "subcategoria": code, "evidencia": [code]}, []

    facts = planning._facts(group)
    variants = _persons(group, facts)
    matches = _match_profiles(variants, live)
    by_id = {p["profile_id"]: p for p in live}
    strong_enough = {pid: m for pid, m in matches.items() if _RANK[m["strength"]] <= _RANK[cv_reconcile.PROBABLE]}
    weak = {pid: m for pid, m in matches.items() if m["strength"] == cv_reconcile.WEAK}
    candidates = _identity_candidates(extraction, facts)
    detail["perfiles"] = sorted(({"profile_id": pid, "fuerza": m["strength"], "senales": sorted(m["signals"]), "de_este_lote": (by_id[pid].get("import_origin") or {}).get("run_id") == run_id}
                                 for pid, m in matches.items()), key=lambda x: (_RANK[x["fuerza"]], x["profile_id"]))
    detail["fuerza"] = detail["perfiles"][0]["fuerza"] if detail["perfiles"] else None
    detail["senales"] = sorted({s for m in matches.values() for s in m["signals"]})
    risks = detail["riesgos"]
    if weak:
        risks.append("COINCIDENCIA_WEAK_EXISTE")
    if cv_reconcile.PHONE_MATCH in detail["senales"] and not {cv_reconcile.NAME_EXACT, cv_reconcile.NAME_SWAPPED, cv_reconcile.NAME_TOKENS_EQUAL, cv_reconcile.EMAIL_EXACT} & set(detail["senales"]):
        risks.append("SOLO_CELULAR")
    if any(m["de_este_lote"] for m in detail["perfiles"]):
        risks.append("PERFIL_CREADO_POR_ESTE_LOTE")
    if set(extraction.get("flags") or []) & {cv_extract.NAME_EDGE_TOKEN_UNEXPLAINED}:
        risks.append("TOKEN_DE_BORDE")
    if set(extraction.get("flags") or []) & {cv_extract.NAME_ORDER_UNCERTAIN, cv_extract.NAME_SPLIT_AMBIGUOUS}:
        risks.append("ORDEN_NOMBRE_INCIERTO")

    # ── conflicto ───────────────────────────────────────────────
    conflict: list[str] = [r for r in reasons if r in _CONFLICT_REASONS]
    if extraction.get("name_state") == "CONFLICT" and planning.NAME_CONFLICT not in conflict:
        conflict.append(planning.NAME_CONFLICT)
    if any(not planning._shares(a["words"], b["words"]) for a, b in combinations(candidates, 2)):
        conflict.append("IDENTIDADES_CONTRADICTORIAS")
    if len(strong_enough) > 1:
        conflict.append("VARIOS_PERFILES_CANDIDATOS")
    if len(strong_enough) == 1:
        pid = next(iter(strong_enough))
        pwords = planning._words(by_id[pid].get("first_name"), by_id[pid].get("last_name"))
        if planning._contradicts(facts, pwords):
            conflict.append("PERFIL_CANDIDATO_CONTRADICE_EL_DOCUMENTO")
    if conflict:
        detail.update(categoria=CONFLICT, subcategoria=sorted(set(conflict))[0], evidencia=sorted(set(conflict)))
        if len(strong_enough) == 1 and _RANK[next(iter(strong_enough.values()))["strength"]] == 0:
            risks.append("BANCO_RESPALDA_UNA_ALTERNATIVA")                                     # informativo: hay coincidencia STRONG, pero el documento se contradice: revisión humana
        return detail, variants

    # ── posible vinculación ─────────────────────────────────────
    if len(strong_enough) == 1:
        pid, m = next(iter(strong_enough.items()))
        profile = by_id[pid]
        pwords_source = (profile.get("first_name") or "").split() + (profile.get("last_name") or "").split()
        name_support = bool(facts["claims"]) or cv_extract.email_strong(pwords_source, _usable_emails(extraction))
        detail.update(categoria=LINK, subcategoria=f"{m['strength']}_{'CON' if name_support else 'SIN'}_RESPALDO_DE_NOMBRE", evidencia=sorted(m["signals"]))
        detail["confirmar"] = ["que el CV pertenece a ese perfil"] + ([] if name_support else ["el nombre (el documento no aporta evidencia propia)"])
        detail["respaldo_nombre"] = name_support
        return detail, variants

    # ── alta asistida ───────────────────────────────────────────
    if len(candidates) == 1:
        anchor = _ANCHOR_BY_STATE.get(candidates[0]["state"], str(candidates[0]["state"]))
        emails = _usable_emails(extraction)
        detail.update(categoria=ASSISTED, subcategoria=anchor, evidencia=[f"ANCLA_{anchor}"] + ([f"REGLA_{detail['regla']}"] if detail["regla"] else []))
        detail["confirmar"] = ["nombre y apellido"] + (["email del documento"] if len(emails) == 1 else [f"elegir el email entre {len(emails)}"] if len(emails) > 1 else ["sin email: solo celular o nada"])
        if set(extraction.get("flags") or []) & {cv_extract.NAME_ORDER_UNCERTAIN, cv_extract.NAME_SPLIT_AMBIGUOUS}:
            detail["confirmar"].append("orden nombre/apellido")
        return detail, variants

    # ── información insuficiente ────────────────────────────────
    alternatives = (extraction.get("name_decision") or {}).get("alternatives") or []
    sub = "SOLO_ESTRUCTURAL" if alternatives else "SIN_IDENTIDAD"
    detail.update(categoria=INSUFFICIENT, subcategoria=sub, evidencia=[sub] + ([planning.GENERIC_EMAIL] if planning.GENERIC_EMAIL in reasons else []))
    return detail, variants


# ═══════════════════ agrupaciones tentativas ═══════════════════
def _components(keys: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    parent = {k: k for k in keys}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    out: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        out[find(k)].append(k)
    return sorted((sorted(v) for v in out.values()), key=lambda c: c[0])


def group_documents(persons: dict[str, list[dict]], claims: dict[str, list]) -> dict:
    """Agrupa documentos que PODRÍAN ser la misma persona usando `cv_reconcile.cluster_in_run` sobre las variantes de persona. Dos niveles: fuerte (enlaces STRONG) y extendido (STRONG o PROBABLE). TENTATIVO: no es identidad verificada."""
    records = {f"{key}|{i}": person for key, variants in persons.items() for i, person in enumerate(variants)}
    clusters = cv_reconcile.cluster_in_run(records)
    owner = {rk: rk.rsplit("|", 1)[0] for rk in records}
    best: dict[tuple[str, str], dict] = {}
    for rk, links in clusters.items():
        for link in links:
            a, b = owner[rk], owner[link["key"]]
            if a == b:
                continue
            pair = (min(a, b), max(a, b))
            slot = best.setdefault(pair, {"strength": link["strength"], "signals": set()})
            slot["signals"] |= set(link["signals"])
            if _RANK[link["strength"]] < _RANK[slot["strength"]]:
                slot["strength"] = link["strength"]
    keys = sorted(persons)
    strong_edges = [p for p, v in best.items() if v["strength"] == cv_reconcile.STRONG]
    extended_edges = [p for p, v in best.items() if v["strength"] in (cv_reconcile.STRONG, cv_reconcile.PROBABLE)]
    strong_components = [c for c in _components(keys, strong_edges) if len(c) > 1]
    extended_components = [c for c in _components(keys, extended_edges) if len(c) > 1]

    def mixed(component: list[str]) -> bool:
        words = [claims.get(k) or [] for k in component]
        return any(wa and wb and not any(planning._shares(x, y) for x in wa for y in wb) for wa, wb in combinations(words, 2))
    phone_only = sum(1 for p in extended_edges if best[p]["signals"] <= _PHONE_ONLY)
    return {"strong": strong_components, "extended": extended_components, "edges_strong": len(strong_edges), "edges_extended": len(extended_edges), "edges_phone_only": phone_only,
            "mixed_extended": [c for c in extended_components if mixed(c)], "total_documents": len(keys),
            "decisions_no_grouping": len(keys), "decisions_strong_groups": len(keys) - sum(len(c) - 1 for c in strong_components), "decisions_extended_groups": len(keys) - sum(len(c) - 1 for c in extended_components)}


# ═══════════════════ informe ═══════════════════
LIMITATIONS = [
    "Ninguna coincidencia (STRONG, PROBABLE ni WEAK) verifica una identidad ni autoriza a vincular o crear perfiles: son propuestas para revisión humana.",
    "Las agrupaciones de documentos son tentativas: un celular o un nombre compartidos pueden unir personas distintas (familiares, agencias, homónimos).",
    "El diagnóstico usa solo la evidencia ya extraída del staging (no vuelve a leer los CV): no puede recuperar fallas de lectura ni identidades que el extractor no encontró.",
    "Las alternativas de nombre solo guardan el texto del nombre (sin separar nombre y apellido): la comparación por nombre es la de `cv_reconcile` sobre ese texto.",
    "El Banco cambia: cada acción posterior debe revalidarse contra el Banco vigente (como ya hace la ejecución).",
    "Un perfil dado de baja que coincide no se sugiere (no se reactiva ni se vincula).",
]


def _pending_by_reason(rows: list[dict]) -> dict[str, int]:
    return dict(sorted(Counter(r["reason"] or "—" for r in rows).items()))


def build_report(inputs: dict) -> dict:
    """Informe agregado (SIN datos personales ni identificadores) y detalle por contenido (protegido: `content_key` y `profile_id` opacos). PURA: no escribe nada."""
    run_id = inputs["run"]["run_id"]
    rows, groups, ledgers, plan = inputs["rows"], inputs["groups"], inputs["ledgers"], inputs["plan"]
    live = [p for p in inputs["profiles"] if not p.get("deleted_at")]
    detail: list[dict] = []
    persons: dict[str, list[dict]] = {}
    claims: dict[str, list] = {}
    for row in sorted(rows, key=lambda r: r["content_key"]):
        key = row["content_key"]
        item, variants = classify(row, groups.get(key), ledgers.get(key), live, run_id)
        detail.append(item)
        if variants:
            persons[key] = variants
            claims[key] = planning._facts(groups[key])["claims"]

    grouping = group_documents(persons, claims)
    group_of: dict[str, str] = {}
    strong_of: dict[str, str] = {}
    for n, component in enumerate(grouping["extended"], start=1):
        for k in component:
            group_of[k] = f"g{n:04d}"
    for n, component in enumerate(grouping["strong"], start=1):
        for k in component:
            strong_of[k] = f"s{n:04d}"
    for item in detail:
        item["grupo"], item["grupo_fuerte"] = group_of.get(item["content_key"]), strong_of.get(item["content_key"])

    by_cat = Counter(d["categoria"] for d in detail)
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: {c: 0 for c in CATEGORIES})
    for d in detail:
        matrix[d["motivo_original"] or "—"][d["categoria"]] += 1
    best = Counter(d["fuerza"] or "NINGUNA" for d in detail)
    links = [d for d in detail if d["categoria"] == LINK]
    link_profiles = {d["perfiles"][0]["profile_id"] for d in links}
    strong_supported = [d for d in links if d["fuerza"] == cv_reconcile.STRONG and d.get("respaldo_nombre")]
    other_links = [d for d in links if d not in strong_supported]
    assisted = [d for d in detail if d["categoria"] == ASSISTED]
    conflicts = [d for d in detail if d["categoria"] == CONFLICT]
    insufficient = [d for d in detail if d["categoria"] == INSUFFICIENT]
    extraction = [d for d in detail if d["categoria"] == EXTRACTION]
    grouped_free = [c for c in grouping["extended"] if all(next(d for d in detail if d["content_key"] == k)["categoria"] in (ASSISTED, INSUFFICIENT) for k in c)]

    pending = [r for r in rows if r["plan_action"] == planning.PENDING]
    additional = [r for r in rows if r["plan_action"] != planning.PENDING]
    plan_pending = ((plan.get("summary") or {}).get("actions") or {}).get(planning.PENDING, 0)
    plan_reasons = dict((plan.get("summary") or {}).get("pending_by_reason") or {})
    open_reasons_original = _pending_by_reason(pending)
    ledger_states = Counter((ledgers.get(r["content_key"]) or {}).get("state") or "SIN_LEDGER" for r in rows)

    aggregate = {
        "lote": run_id,
        "excepciones_analizadas": len(detail),
        "conciliacion": {
            "abiertas_segun_manifiesto": inputs["overview"]["total"], "clasificadas": len(detail), "diferencia": inputs["overview"]["total"] - len(detail),
            "por_estado": dict(sorted(Counter(r["status"] for r in rows).items())),
            "pendientes_originales": len(pending), "adicionales_bloqueadas_o_con_error": len(additional),
            "adicionales_por_motivo": _pending_by_reason(additional),
            "plan": {"pendientes_en_el_plan": plan_pending, "por_motivo_en_el_plan": plan_reasons, "resueltas_o_descartadas_desde_el_plan": plan_pending - len(pending)},
            "por_motivo_abiertas_originales": open_reasons_original,
            "estado_ledger_de_las_abiertas": dict(sorted(ledger_states.items())),
        },
        "categorias": {c: by_cat.get(c, 0) for c in CATEGORIES},
        "matriz_motivo_x_categoria": {r: matrix[r] for r in sorted(matrix)},
        "mejor_coincidencia_con_el_banco_por_documento": {s: best.get(s, 0) for s in (*STRENGTHS, "NINGUNA")},
        "posible_vinculacion": {
            "documentos": len(links), "perfiles_distintos": len(link_profiles),
            "por_fuerza": {s: sum(1 for d in links if d["fuerza"] == s) for s in (cv_reconcile.STRONG, cv_reconcile.PROBABLE)},
            "con_respaldo_de_nombre": sum(1 for d in links if d.get("respaldo_nombre")), "sin_respaldo_de_nombre": sum(1 for d in links if not d.get("respaldo_nombre")),
            "a_perfiles_creados_por_este_lote": sum(1 for d in links if "PERFIL_CREADO_POR_ESTE_LOTE" in d["riesgos"]),
            "por_senal": dict(sorted(Counter(s for d in links for s in d["evidencia"]).items())),
        },
        "alta_asistida": {"documentos": len(assisted), "por_ancla": dict(sorted(Counter(d["subcategoria"] for d in assisted).items())),
                          "dato_a_confirmar": dict(sorted(Counter(c for d in assisted for c in d["confirmar"]).items()))},
        "conflictos": {"documentos": len(conflicts), "por_subtipo": dict(sorted(Counter(c for d in conflicts for c in d["evidencia"]).items()))},
        "informacion_insuficiente": {"documentos": len(insufficient), "por_subtipo": dict(sorted(Counter(d["subcategoria"] for d in insufficient).items()))},
        "problema_de_extraccion": {"documentos": len(extraction), "por_codigo": dict(sorted(Counter(d["subcategoria"] for d in extraction).items()))},
        "agrupaciones_tentativas": {
            "aviso": "TENTATIVAS: no son identidades verificadas.",
            "documentos_analizados": grouping["total_documents"],
            "grupos_fuertes": len(grouping["strong"]), "documentos_en_grupos_fuertes": sum(len(c) for c in grouping["strong"]),
            "grupos_extendidos": len(grouping["extended"]), "documentos_en_grupos_extendidos": sum(len(c) for c in grouping["extended"]),
            "enlaces_fuertes": grouping["edges_strong"], "enlaces_fuertes_o_probables": grouping["edges_extended"], "enlaces_solo_celular": grouping["edges_phone_only"],
            "grupos_con_identidades_mezcladas": len(grouping["mixed_extended"]),
            "decisiones_humanas_estimadas": {"sin_agrupar": grouping["decisions_no_grouping"], "con_grupos_fuertes": grouping["decisions_strong_groups"], "con_grupos_extendidos": grouping["decisions_extended_groups"]},
            "tamano_de_grupos_extendidos": dict(sorted(Counter(len(c) for c in grouping["extended"]).items())),
        },
        "oportunidades_priorizadas": [
            {"prioridad": 1, "oportunidad": "Vincular a un perfil existente (STRONG con respaldo de nombre)", "documentos": len(strong_supported), "decisiones_estimadas": len({d["perfiles"][0]["profile_id"] for d in strong_supported}),
             "riesgo": "bajo", "nota": "no crea perfiles; requiere confirmación humana"},
            {"prioridad": 2, "oportunidad": "Vincular a un perfil existente (PROBABLE, o STRONG sin respaldo de nombre)", "documentos": len(other_links), "decisiones_estimadas": len({d["perfiles"][0]["profile_id"] for d in other_links}),
             "riesgo": "medio", "nota": "homónimos y celulares compartidos: revisar cada propuesta"},
            {"prioridad": 3, "oportunidad": "Decisión conjunta de grupos de documentos sin perfil (alta asistida o insuficiente)", "documentos": sum(len(c) for c in grouped_free), "decisiones_estimadas": len(grouped_free),
             "riesgo": "medio", "nota": "una decisión resolvería varios CV; los grupos son tentativos"},
            {"prioridad": 4, "oportunidad": "Alta asistida individual (una identidad plausible y anclada)", "documentos": len(assisted), "decisiones_estimadas": len(assisted), "riesgo": "medio-alto",
             "nota": "crea perfiles: máxima cautela con duplicados"},
            {"prioridad": 5, "oportunidad": "Conflictos de identidad", "documentos": len(conflicts), "decisiones_estimadas": len(conflicts), "riesgo": "alto", "nota": "revisión explícita; sin atajo"},
            {"prioridad": 6, "oportunidad": "Problemas de extracción (otro tratamiento técnico)", "documentos": len(extraction), "decisiones_estimadas": len(extraction), "riesgo": "n/a", "nota": "OCR, PDF cifrado, formato"},
            {"prioridad": 7, "oportunidad": "Información insuficiente", "documentos": len(insufficient), "decisiones_estimadas": len(insufficient), "riesgo": "n/a", "nota": "sin fundamento para proponer"},
        ],
        "riesgos": dict(sorted(Counter(r for d in detail for r in d["riesgos"]).items())),
        "limitaciones": LIMITATIONS,
    }
    return {"aggregate": aggregate, "detail": detail}
