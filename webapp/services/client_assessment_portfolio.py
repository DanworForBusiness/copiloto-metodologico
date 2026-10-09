"""
client_assessment_portfolio.py — "Assessment del cliente" (consolidado V1): MODELO DE LECTURA DERIVADO, solo lectura.

Unidad = ENTRADA del Catálogo gobernado (``CatalogEntry``: un flujo/consumo del cliente). NO se afirma que una entrada sea una raíz
publicada ni un modelo: por eso el consolidado habla de "artefactos del catálogo".

    entradas del Catálogo del cliente  +  estado canónico de cada una (``assessment_service.statuses_for_entries``)
      → snapshot (esta función) → HTML / JSON / Markdown, los tres del MISMO snapshot

Abrirlo JAMÁS conecta a Datasphere, no pide credenciales, no ejecuta el motor de assessment ni regenera nada: ``statuses_for_entries`` lee
el pool y la caché pero no ejecuta ni escribe. STALE sigue STALE y NOT_YET_ASSESSED sigue visible. Los assessments ad-hoc quedan fuera
(no son entradas del Catálogo). Solo los assessments VIGENTES (CURRENT) aportan métricas; el resto muestra "—", nunca 0.
Sin score, banda, peso ni penalidad: vigencia, cobertura, confianza (distribución), observaciones, revisiones y hallazgos recurrentes.

COMPOSICIÓN DEL MODELADO (aditiva, descriptiva; sin juicio de calidad): dos lecturas que NO se suman ni se mezclan.
  * ``inventory`` — inventario técnico disponible: artefactos ÚNICOS del pool de crudos del cliente (`client_modeling_inventory`); no es el tenant.
  * ``current_assessment_scope`` — vistas únicas que participan en los Assessments VIGENTES: unión por nombre técnico de ``views_in_closure /
    view_artifacts`` de los perfiles CURRENT. Solo se muestra como completa si TODOS los CURRENT traen el hecho `am.view_type_composition`; si solo
    X de Y lo traen, la unión parcial NO se presenta como valor del cliente (``available`` False y las métricas en None). Nunca se suman los
    `total_views` de cada Assessment: ``view_occurrences`` (suma) es un dato interno de procedencia, no un conteo de objetos únicos.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import catalog_reads
from webapp.services import client_modeling_inventory as inventory
from webapp.services import domain_classification_service as dcs

log = logging.getLogger("danwor.client_assessment")

SCHEMA_VERSION = "client-assessment-portfolio/v1"
CONFIDENCE_ORDER = ("HIGH", "MEDIUM", "LOW")
FINDING_KIND_LABELS = {"OBSERVATION": "Observación", "REVIEW_REQUIRED": "Revisión requerida"}
DASH = "—"


def client_entries(client: str) -> list:
    """Entradas del Catálogo de ``client`` (misma fuente que el resto del Catálogo). Orden determinista."""
    _mod, entries = catalog_reads._catalog_entries()
    wanted = (client or "").strip().lower()
    mine = [e for e in (entries or []) if wanted and str(getattr(e, "cliente", "")).strip().lower() == wanted]
    return sorted(mine, key=lambda e: (str(e.dominio).lower(), e.entry_id))


def _failed(entry: Any) -> svc.AssessmentStatus:
    return svc.AssessmentStatus(svc.FAILED, getattr(entry, "entry_id", ""), str(getattr(entry, "cliente", "")), None,
                                reason="No se pudo leer el assessment de este artefacto.", error_code="READ_ERROR")


def _statuses(entries: list) -> dict[str, svc.AssessmentStatus]:
    """Estados canónicos del lote (SOLO lectura). Si el lote falla se degrada por entrada: una entrada rota es una fila FAILED."""
    try:
        return svc.statuses_for_entries(entries)
    except Exception:                                                   # noqa: BLE001
        log.exception("client_assessment: falló la lectura en lote; se degrada por entrada")
    out: dict[str, svc.AssessmentStatus] = {}
    for e in entries:
        try:
            out[e.entry_id] = svc.status_for_entry(e)
        except Exception:                                               # noqa: BLE001
            log.exception("client_assessment: falló la lectura de %s", getattr(e, "entry_id", "?"))
            out[e.entry_id] = _failed(e)
    return out


def _metrics(status: svc.AssessmentStatus) -> dict:
    empty = {"coverage_strict": None, "confidence": None, "observation_occurrences": None, "review_occurrences": None}
    summary = status.summary if status.state == svc.CURRENT else None
    if not summary:
        return empty
    return {"coverage_strict": summary["strict"], "confidence": summary["confidence"],
            "observation_occurrences": summary["observation_occurrences"], "review_occurrences": summary["review_occurrences"]}


def _identity(title: str | None, am: str | None, domain: str) -> str:
    """Una sola descripción del artefacto para HTML / JSON / Markdown: ``Descripción · ID``. La agrupación legacy (``entry.dominio``) ya NO se muestra como
    clasificación: bajo el nombre va la Línea de negocio EFECTIVA (``line_of_business``); el dominio solo es el último recurso de nombre si no hay título ni id."""
    return f"{title} · {am}" if title and am else (am or title or domain)


def _row(entry: Any, status: svc.AssessmentStatus, pack: str, lob: dict | None = None) -> dict:
    m = _metrics(status)
    am = status.terminal_am
    try:
        desc, _name = presenter.report_title(entry, am)
    except Exception:                                                   # noqa: BLE001
        desc = None
    cov, conf, review_n = m["coverage_strict"], m["confidence"], m["review_occurrences"]
    title = desc if desc and desc != am else None
    label = _identity(title, am, str(entry.dominio))
    return {
        "entry_id": entry.entry_id, "domain": str(entry.dominio), "asset_id": am, "asset_title": title,
        "artifact_label": label,
        # Línea de negocio EFECTIVA persistida (manual > automática HIGH > Sin clasificar): contexto de negocio, no adopción/cobertura/confianza/Juicio.
        "line_of_business": lob or presenter.lob_display(None),
        # UN activo, UN grado primario: Adopción metodológica PERSISTIDA (misma fuente que la tile). Sin Juicio → estado neutro; el veredicto legacy del catálogo ya no se muestra.
        "adoption": {k: presenter.tile_grade(status)[k] for k in ("label", "tone", "has_judgment")},
        "state": status.state, "state_label": presenter.STATE_LABELS[status.state],
        "coverage_strict": cov, "coverage_text": presenter.percent(cov) if cov is not None else DASH,
        "confidence": conf, "confidence_text": presenter.confidence_label(conf) if conf else DASH,
        "observation_occurrences": m["observation_occurrences"], "review_occurrences": review_n,
        "requires_review": (review_n > 0) if review_n is not None else None,
        "reason": status.reason if status.state in (svc.NOT_APPLICABLE, svc.FAILED, svc.INCOMPATIBLE_SCHEMA) else None,
        "assessment_ref": ({"generated_at": status.generated_at, "terminal_am": am}
                           if status.state in (svc.CURRENT, svc.STALE) else None),
        "detail_url": presenter.flow_url(entry, pack),         # drilldown: el detalle del flujo, donde vive el informe individual
    }


def _recurring_findings(rows: list[dict], statuses: dict[str, svc.AssessmentStatus]) -> list[dict]:
    """Ids canónicos de observación / revisión requerida agregados por ENTRADA: cada entrada cuenta UNA vez por hallazgo. Solo vigentes."""
    acc: dict[str, dict] = {}
    for r in rows:
        if r["state"] != svc.CURRENT:
            continue
        prof = statuses[r["entry_id"]].profile or {}
        for key, kind in (("observations", "OBSERVATION"), ("review_required", "REVIEW_REQUIRED")):
            for g in prof.get(key, []) or []:
                gid = presenter.canonical_signal_id(g["id"])                                    # un id historico y el vigente son LA MISMA senal
                item = acc.setdefault(gid, {"id": gid, "title": presenter.P.display_title(gid, g.get("title") or gid), "kind": kind,
                                                "kind_label": FINDING_KIND_LABELS[kind], "_entries": {}})
                item["_entries"][r["entry_id"]] = r["asset_id"] or r["domain"]
    out = []
    for item in acc.values():
        entries = item.pop("_entries")
        out.append({**item, "entries": len(entries), "asset_ids": sorted(entries.values(), key=str.lower)})
    return sorted(out, key=lambda f: (-f["entries"], f["id"]))


def _view_composition_of(profile: dict | None) -> tuple[int, list[dict]] | None:
    """(vistas del cierre, [vistas {name, view_type, ...}]) del hecho `am.view_type_composition` de UN perfil, o None si el perfil no lo trae
    (perfiles anteriores al hecho: siguen siendo legibles, simplemente no aportan)."""
    for area in (profile or {}).get("areas") or []:
        highlights = {h.get("id"): h for h in area.get("highlights") or [] if isinstance(h, dict)}
        if "views_in_closure" in highlights:
            total = highlights["views_in_closure"].get("value")
            arts = (highlights.get("view_artifacts") or {}).get("value") or []
            return (int(total or 0), [a for a in arts if isinstance(a, dict) and a.get("name")])
    return None


def _assessment_scope(rows: list[dict], statuses: dict[str, svc.AssessmentStatus]) -> dict:
    """Participación en los Assessments VIGENTES (CURRENT): unión ÚNICA por nombre técnico, solo si TODOS los CURRENT traen el hecho."""
    current = [r for r in rows if r["state"] == svc.CURRENT]
    union: dict[str, str] = {}
    occurrences = enriched = 0
    for r in current:
        comp = _view_composition_of(statuses[r["entry_id"]].profile)
        if comp is None:
            continue
        enriched += 1
        occurrences += comp[0]
        for a in comp[1]:
            union.setdefault(str(a["name"]), str(a.get("view_type") or "UNKNOWN"))
    complete = bool(current) and enriched == len(current)
    out = {"available": complete, "enriched_assessments": enriched, "total_current_assessments": len(current),
           "unique_views": None, "graphical": None, "sql": None, "unknown": None, "view_occurrences": None}
    if complete:
        out.update({"unique_views": len(union), "graphical": sum(1 for t in union.values() if t == "GRAPHICAL"),
                    "sql": sum(1 for t in union.values() if t == "SQL"), "unknown": sum(1 for t in union.values() if t not in ("GRAPHICAL", "SQL")),
                    "view_occurrences": occurrences})
    return out


def build_snapshot(entry: Any, pack: str, *, now: datetime | None = None) -> dict:
    """Snapshot puntual del consolidado del cliente de ``entry``. Determinista dado el estado actual del Catálogo y de las cachés."""
    client = str(entry.cliente)
    entries = client_entries(client)
    statuses = _statuses(entries)
    rows = []
    for e in entries:
        statuses[e.entry_id] = statuses.get(e.entry_id) or _failed(e)
    try:                                                                # UNA lectura por lote del resolver efectivo del Catálogo (sin derivar ni escribir)
        lobs = {k: presenter.lob_display(v) for k, v in dcs.effective_lob_by_entry(entries, statuses).items()}
    except Exception:                                                   # noqa: BLE001 — un fallo del lote se informa como «no disponible», no rompe el consolidado
        log.exception("client_assessment: no se pudo resolver la línea de negocio efectiva")
        lobs = {}
    for e in entries:
        st = statuses[e.entry_id]
        try:
            rows.append(_row(e, st, pack, lobs.get(e.entry_id)))
        except Exception:                                               # noqa: BLE001 — una fila rota no rompe el consolidado
            log.exception("client_assessment: no se pudo armar la fila de %s", e.entry_id)
            bad = _failed(e)
            statuses[e.entry_id] = bad
            rows.append(_row(e, bad, pack, lobs.get(e.entry_id)))
    states = {s: sum(1 for r in rows if r["state"] == s) for s in svc.STATES}
    current = [r for r in rows if r["state"] == svc.CURRENT]
    covs = [r["coverage_strict"] for r in current if r["coverage_strict"] is not None]
    ts = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")
    return {
        "schema_version": SCHEMA_VERSION, "generated_at": ts, "client": client,
        "catalog_assets": len(rows),
        "states": states,
        "summary": {
            "current_assessments": len(current),
            "requires_review": sum(1 for r in current if r["requires_review"]),
            "coverage": {"average_strict": (sum(covs) / len(covs)) if covs else None, "over_current_assessments": len(covs)},
            "confidence_distribution": {lvl: sum(1 for r in current if r["confidence"] == lvl) for lvl in CONFIDENCE_ORDER},
        },
        "assets": rows,
        "recurring_findings": _recurring_findings(rows, statuses),
        "modeling_composition": {"inventory": inventory.inventory_for(client), "current_assessment_scope": _assessment_scope(rows, statuses)},
    }


# ───────────────────── presentación / exportaciones (del MISMO snapshot) ─────────────────────
# Rótulos CORTOS de estado para el HTML del consolidado (la columna ya se titula "Estado del Assessment"). UNA sola fuente para Resumen y Artefactos;
# los enums, el JSON (`state_label`) y el Markdown conservan los rótulos canónicos del presenter.
STATE_DISPLAY_LABELS = {
    svc.CURRENT: "Vigente", svc.STALE: "Desactualizado", svc.NOT_YET_ASSESSED: "Sin Iniciar",
    svc.NOT_APPLICABLE: "No Aplica", svc.FAILED: "No Disponible", svc.INCOMPATIBLE_SCHEMA: "No Compatible",
}


def confidence_story(dist: dict) -> str | None:
    """Lectura ejecutiva de la DISTRIBUCIÓN de confianza de los assessments vigentes (no es otra métrica ni un puntaje).
    Reglas (pocas ramas cualitativas): sin vigentes → nada; baja material (≥ 25%) → cautela; si no, predomina alta o media."""
    alta, media, baja = (int(dist.get(k) or 0) for k in CONFIDENCE_ORDER)
    n = alta + media + baja
    if n == 0:
        return None
    if baja * 4 >= n:
        return ("La evidencia muestra un panorama más heterogéneo y conviene interpretar los resultados con mayor cautela, "
                "dado el peso de assessments con confianza baja.")
    if alta >= media:
        return ("La evidencia presenta una base sólida, con predominio de assessments de confianza alta y una presencia "
                "acotada de niveles inferiores.")
    if alta * 4 >= n:
        return ("La evidencia presenta una base consistente: predominan los assessments de confianza media, acompañados por una "
                "proporción relevante de confianza alta y sin señales significativas de confianza baja.")
    return ("La evidencia presenta una base razonable: predominan los assessments de confianza media, "
            "con poca presencia de confianza alta y sin señales significativas de confianza baja.")


ND = "No disponible"
UNKNOWN_PARTIAL_CAVEAT = ("La identificación de vistas sin clasificar es parcial porque este inventario no informa la familia de todos los artefactos.")


def _n(v: Any) -> str:
    """Número del snapshot para presentación: None = no disponible (jamás se muestra como 0)."""
    return ND if v is None else str(v)


def _pct(part: Any, total: Any) -> str:
    return ND if part is None or not total else presenter.percent(part / total, 0)


def _majority(g: int, sql: int, total: int) -> str | None:
    if total <= 0:
        return None
    if g * 2 > total:
        return "mayoritariamente gráfica"
    if sql * 2 > total:
        return "mayoritariamente SQL"
    return "mixta, sin un tipo mayoritario"


def _distribution_rows(g: Any, sql: Any, unknown: Any, total: Any) -> list[dict]:
    return [{"label": lbl, "count": _n(c), "share": _pct(c, total)} for lbl, c in (("Vistas gráficas", g), ("Vistas SQL", sql), ("Sin clasificar", unknown))]


def composition_view(snap: dict) -> dict:
    """Presentación de la Composición del modelado: SOLO formatea el snapshot (no recalcula ni infiere). HTML y Markdown usan esto."""
    mc = snap["modeling_composition"]
    inv, scope = mc["inventory"], mc["current_assessment_scope"]
    v = inv["views"]
    exhaustive = v["unknown_detection"] == inventory.EXHAUSTIVE
    inv_story = None
    if v["total"]:
        shape = _majority(v["graphical"], v["sql"], v["total"])
        if exhaustive:
            inv_story = f"El inventario técnico disponible presenta una composición {shape}."
        else:
            inv_story = f"Entre las vistas identificadas en el inventario técnico disponible, la composición es {shape}."
    flows = [{"key": k, "label": FLOW_LABELS[k], "value": _n(inv[k]), "evidence": inv["flow_evidence"][k]} for k in inventory.FLOW_ORDER]
    if scope["available"]:
        cur, cat = scope["total_current_assessments"], snap["catalog_assets"]
        scope_note = (f"Calculado sobre {cur} {'assessment vigente' if cur == 1 else 'assessments vigentes'} de {cat} "
                      f"{'artefacto' if cat == 1 else 'artefactos'} del catálogo; las vistas compartidas entre cierres cuentan una sola vez.")
        if cur < cat:
            scope_note += (f" Los {cat - cur} restantes no tienen un assessment vigente y no participan: "
                           "esta lectura no representa a todos los artefactos del catálogo." if cat - cur != 1 else
                           " El restante no tiene un assessment vigente y no participa: esta lectura no representa a todos los artefactos del catálogo.")
    elif scope["total_current_assessments"] == 0:
        scope_note = "No hay assessments vigentes."
    else:
        scope_note = (f"No disponible: solo {scope['enriched_assessments']} de {scope['total_current_assessments']} assessments vigentes "
                      "incluyen la composición por tipo de vista; los restantes son anteriores a este dato y se actualizan al regenerarlos.")
    return {
        "inventory": {
            "available": inv["available"], "show_tiles": v["total"] is not None,
            "note": None if v["total"] is not None else "No disponible: no hay evidencia técnica de vistas en el pool de este cliente.",
            "kpis": [{"label": "Vistas", "value": _n(v["total"])}, {"label": "Vistas gráficas", "value": _n(v["graphical"])},
                     {"label": "Vistas SQL", "value": _n(v["sql"])}, {"label": "Sin clasificar", "value": _n(v["unknown"])}],
            "distribution": _distribution_rows(v["graphical"], v["sql"], v["unknown"], v["total"]),
            "flows": flows, "story": inv_story, "limited_unknown": bool(v["total"]) and not exhaustive,
            "unknown_caveat": UNKNOWN_PARTIAL_CAVEAT if v["unknown_detection"] == inventory.PARTIAL else None,
        },
        "scope": {
            "available": scope["available"], "note": scope_note,
            "kpis": [{"label": "Vistas únicas", "value": _n(scope["unique_views"])}, {"label": "Vistas gráficas", "value": _n(scope["graphical"])},
                     {"label": "Vistas SQL", "value": _n(scope["sql"])}, {"label": "Sin clasificar", "value": _n(scope["unknown"])}],
            "distribution": _distribution_rows(scope["graphical"], scope["sql"], scope["unknown"], scope["unique_views"]),
        },
    }


FLOW_LABELS = {"replication_flows": "Replication Flows", "data_flows": "Data Flows",
               "transformation_flows": "Transformation Flows", "task_chains": "Task Chains"}


def report_view(snap: dict) -> dict:
    s, n = snap["summary"], snap["catalog_assets"]
    cov = s["coverage"]
    cur = s["current_assessments"]
    return {
        "snapshot": snap,
        "composition": composition_view(snap),
        "generated_text": snap["generated_at"].replace("T", " ").replace("+00:00", " UTC"),
        "kpis": [
            {"label": "Total de Artefactos", "value": str(n)},
            {"label": "Assessments vigentes", "value": str(cur)},
            {"label": "Requieren revisión", "value": str(s["requires_review"])},
            {"label": "Cobertura promedio", "value": presenter.percent(cov["average_strict"]) if cov["average_strict"] is not None else DASH},
        ],
        "state_labels": dict(STATE_DISPLAY_LABELS), "state_tones": dict(presenter.STATE_TONES),
        "coverage_whole": {r["entry_id"]: (presenter.percent(r["coverage_strict"], 0) if r["coverage_strict"] is not None else DASH)
                           for r in snap["assets"]},                  # solo presentación de la tabla HTML; el snapshot/JSON conserva el valor y su precisión
        "confidence_story": confidence_story(s["confidence_distribution"]),
        "state_rows": [{"state": st, "label": presenter.STATE_LABELS[st], "count": snap["states"][st]} for st in svc.STATES],
        "confidence_rows": [{"level": lvl, "label": presenter.confidence_label(lvl), "count": s["confidence_distribution"][lvl]}
                            for lvl in CONFIDENCE_ORDER],
    }


def to_json(snap: dict) -> str:
    return json.dumps(snap, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _cell(v: Any) -> str:
    return DASH if v is None or v == "" else str(v).replace("|", "\\|").replace("\n", " ")


def to_markdown(snap: dict) -> str:
    v = report_view(snap)
    s = snap["summary"]
    lines = [f"# Assessment del cliente — {snap['client']}", "",
             f"_Generado {v['generated_text']}. Derivado de los assessments canónicos ya generados de los artefactos del catálogo; "
             "no se conectó a Datasphere ni se ejecutó ninguna evaluación._", "", "## Resumen", ""]
    lines += [f"- **{k['label']}:** {k['value']}" for k in v["kpis"]]
    lines += ["", "## Estado de los artefactos del catálogo", ""] + [f"- {r['label']}: {r['count']}" for r in v["state_rows"]]
    lines += ["", f"## Confianza (sobre {s['current_assessments']} assessments vigentes)", ""]
    lines += [f"- {r['label']}: {r['count']}" for r in v["confidence_rows"]]
    if v["confidence_story"]:
        lines += ["", f"> {v['confidence_story']}"]
    lines += ["", "## Artefactos", "", "| Artefacto | Estado | Cobertura | Confianza | Observaciones | Revisión requerida | Adopción metodológica |", "|---|---|---|---|---|---|---|"]
    for r in snap["assets"]:
        lines.append("| " + " | ".join(_cell(x) for x in (
            r["artifact_label"] + f" ({r['line_of_business']['label']})", r["state_label"], r["coverage_text"] if r["coverage_strict"] is not None else None,
            r["confidence_text"] if r["confidence"] else None, r["observation_occurrences"], r["review_occurrences"], r["adoption"]["label"] if r["adoption"]["has_judgment"] else None)) + " |")
    lines += ["", "## Hallazgos recurrentes", ""]
    if snap["recurring_findings"]:
        lines += [f"- **{f['title']}** ({f['id']}): {presenter.plural(f['entries'], 'artefacto')}" for f in snap["recurring_findings"]]
    else:
        lines.append("Sin hallazgos recurrentes en los assessments vigentes.")
    c = v["composition"]
    lines += ["", "## Composición del modelado", "",
              "_Dos lecturas distintas que no se suman. Descriptivo: no es un juicio de calidad._", "", "### Inventario técnico disponible", "",
              "_Artefactos únicos de la evidencia disponible para el Copiloto (no es el total del tenant de Datasphere)._", ""]
    lines += [f"- **{k['label']}:** {k['value']}" for k in c["inventory"]["kpis"]]
    lines += [f"- **{f['label']}:** {f['value']}" for f in c["inventory"]["flows"]]
    if c["inventory"]["unknown_caveat"]:
        lines += ["", f"_{c['inventory']['unknown_caveat']}_"]
    if c["inventory"]["story"]:
        lines += ["", f"> {c['inventory']['story']}"]
    lines += ["", "### Participación en Assessments vigentes", "", f"_{c['scope']['note']}_", ""]
    lines += [f"- **{k['label']}:** {k['value']}" for k in c["scope"]["kpis"]]
    return "\n".join(lines) + "\n"


def export_filename(snap: dict, ext: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in snap["client"]) or "cliente"
    return f"assessment-cliente_{safe}.{ext}"
