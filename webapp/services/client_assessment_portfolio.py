"""
client_assessment_portfolio.py — "Assessment del cliente" (consolidado V1): MODELO DE LECTURA DERIVADO, solo lectura.

Unidad = ENTRADA del Catálogo gobernado (``CatalogEntry``: un flujo/consumo del cliente). NO se afirma que una entrada sea una raíz
publicada ni un modelo: por eso el consolidado habla de "activos del catálogo".

    entradas del Catálogo del cliente  +  estado canónico de cada una (``assessment_service.statuses_for_entries``)
      → snapshot (esta función) → HTML / JSON / Markdown, los tres del MISMO snapshot

Abrirlo JAMÁS conecta a Datasphere, no pide credenciales, no ejecuta el motor de assessment ni regenera nada: ``statuses_for_entries`` lee
el pool y la caché pero no ejecuta ni escribe. STALE sigue STALE y NOT_YET_ASSESSED sigue visible. Los assessments ad-hoc quedan fuera
(no son entradas del Catálogo). Solo los assessments VIGENTES (CURRENT) aportan métricas; el resto muestra "—", nunca 0.
Sin score, banda, peso ni penalidad: vigencia, cobertura, confianza (distribución), observaciones, revisiones y hallazgos recurrentes.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import catalog_reads

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
                                reason="No se pudo leer el assessment de este activo.", error_code="READ_ERROR")


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


def _row(entry: Any, status: svc.AssessmentStatus, pack: str) -> dict:
    m = _metrics(status)
    am = status.terminal_am
    try:
        desc, _name = presenter.report_title(entry, am)
    except Exception:                                                   # noqa: BLE001
        desc = None
    cov, conf, review_n = m["coverage_strict"], m["confidence"], m["review_occurrences"]
    return {
        "entry_id": entry.entry_id, "domain": str(entry.dominio), "asset_id": am, "asset_title": desc if desc and desc != am else None,
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
                item = acc.setdefault(g["id"], {"id": g["id"], "title": g.get("title") or g["id"], "kind": kind,
                                                "kind_label": FINDING_KIND_LABELS[kind], "_entries": {}})
                item["_entries"][r["entry_id"]] = r["asset_id"] or r["domain"]
    out = []
    for item in acc.values():
        entries = item.pop("_entries")
        out.append({**item, "entries": len(entries), "asset_ids": sorted(entries.values(), key=str.lower)})
    return sorted(out, key=lambda f: (-f["entries"], f["id"]))


def build_snapshot(entry: Any, pack: str, *, now: datetime | None = None) -> dict:
    """Snapshot puntual del consolidado del cliente de ``entry``. Determinista dado el estado actual del Catálogo y de las cachés."""
    client = str(entry.cliente)
    entries = client_entries(client)
    statuses = _statuses(entries)
    rows = []
    for e in entries:
        st = statuses.get(e.entry_id) or _failed(e)
        statuses[e.entry_id] = st
        try:
            rows.append(_row(e, st, pack))
        except Exception:                                               # noqa: BLE001 — una fila rota no rompe el consolidado
            log.exception("client_assessment: no se pudo armar la fila de %s", e.entry_id)
            bad = _failed(e)
            statuses[e.entry_id] = bad
            rows.append(_row(e, bad, pack))
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
    }


# ───────────────────── presentación / exportaciones (del MISMO snapshot) ─────────────────────
def report_view(snap: dict) -> dict:
    s, n = snap["summary"], snap["catalog_assets"]
    cov = s["coverage"]
    cur = s["current_assessments"]
    return {
        "snapshot": snap,
        "generated_text": snap["generated_at"].replace("T", " ").replace("+00:00", " UTC"),
        "kpis": [
            {"label": "Activos del catálogo", "value": str(n)},
            {"label": f"Assessments vigentes · de {n}", "value": f"{cur} / {n}"},
            {"label": f"Requieren revisión · sobre {cur} vigentes", "value": str(s["requires_review"])},
            {"label": f"Cobertura promedio · sobre {presenter.plural(cov['over_current_assessments'], 'assessment vigente', 'assessments vigentes')}",
             "value": presenter.percent(cov["average_strict"]) if cov["average_strict"] is not None else DASH},
        ],
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
             f"_Generado {v['generated_text']}. Derivado de los assessments canónicos ya generados de los activos del catálogo; "
             "no se conectó a Datasphere ni se ejecutó ninguna evaluación._", "", "## Resumen", ""]
    lines += [f"- **{k['label']}:** {k['value']}" for k in v["kpis"]]
    lines += ["", "## Estado de los activos del catálogo", ""] + [f"- {r['label']}: {r['count']}" for r in v["state_rows"]]
    lines += ["", f"## Confianza (sobre {s['current_assessments']} assessments vigentes)", ""]
    lines += [f"- {r['label']}: {r['count']}" for r in v["confidence_rows"]]
    lines += ["", "## Activos", "", "| Activo | Estado | Cobertura | Confianza | Observaciones | Revisión requerida |", "|---|---|---|---|---|---|"]
    for r in snap["assets"]:
        lines.append("| " + " | ".join(_cell(x) for x in (
            r["asset_id"] or r["domain"], r["state_label"], r["coverage_text"] if r["coverage_strict"] is not None else None,
            r["confidence_text"] if r["confidence"] else None, r["observation_occurrences"], r["review_occurrences"])) + " |")
    lines += ["", "## Hallazgos recurrentes", ""]
    if snap["recurring_findings"]:
        lines += [f"- **{f['title']}** ({f['id']}): {presenter.plural(f['entries'], 'activo')}" for f in snap["recurring_findings"]]
    else:
        lines.append("Sin hallazgos recurrentes en los assessments vigentes.")
    return "\n".join(lines) + "\n"


def export_filename(snap: dict, ext: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in snap["client"]) or "cliente"
    return f"assessment-cliente_{safe}.{ext}"
