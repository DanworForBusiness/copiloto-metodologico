#!/usr/bin/env python3
"""analyze_reconciliation.py — Bucle diagnóstico ⇄ automatización (epic B 1.2).

Reconcilia, sobre el EJE DE EXTRACCIÓN (donde se solapan), dos miradas del mismo
dominio para converger en un criterio homogéneo:

  • AUTOMATIZACIÓN  — el motor `evaluate_compliance` que AUDITA los flujos publicados
                      del catálogo (veredicto, ratio de desacople, crudas, name-vs-reality).
  • DIAGNÓSTICO     — el árbol `03_extraction_options` (criterio PRESCRIPTIVO: qué
                      deberías hacer) + `reports/lessons.yaml` (lecciones de campo
                      cosechadas de uso real) + el override del senior en cada flujo.

Cierra el bucle en las dos direcciones (determinístico, sin LLM):
  auto → DESAFÍA al diagnóstico:  divergencias veredicto-auto ↔ override-senior, y
                                  el criterio del árbol contrastado contra la realidad
                                  auditada del catálogo.
  diagnóstico → ENRIQUECE al auto: las lecciones de campo del eje se exponen como
                                  criterio a incorporar (caveats homogéneos).

Escribe reports/reconciliation.yaml + reports/reconciliation_summary.txt. Solo lectura
sobre el catálogo/árbol/lecciones; NO muta flujos (el v1 vive en Analítica).
"""
from __future__ import annotations

import argparse
import glob
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from config import BASE_DIR, CONTENT_PACKS_DIR
from pipeline.common import write_report

REPORTS_DIR = BASE_DIR / "reports"
AXIS = "extraction"
TREE_ID = "03_extraction_options"

# Clasificación del origen — MISMO criterio que generate_catalog.classify_extraction
# (fuente única conceptual: si divergen, es un bug a reconciliar). Se replica local
# para no acoplar el script al pipeline del catálogo, pero se testea la paridad.
def _classify(remote_entity: str) -> str:
    r = (remote_entity or "").upper()
    if r.startswith("ABAP_CDS."):
        return "ideal"
    if r.startswith("ABAPTABLES.") and ("NSDM" in r or "_V_" in r):
        return "acceptable_view"
    if r.startswith("ABAPTABLES."):
        return "poor_raw"
    if "BWBRIDGE" in r:
        return "acceptable_bw"
    return "unknown" if r else ""


def _frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except Exception:
        return {}


def load_flows(cat_dir: Path) -> list[dict]:
    """Lee los flujo_*.md del catálogo: veredicto AUTO, override del senior, orígenes."""
    flows = []
    for fp in sorted(glob.glob(str(cat_dir / "**" / "flujo_*.md"), recursive=True)):
        fm = _frontmatter(Path(fp).read_text(encoding="utf-8", errors="ignore"))
        auto = fm.get("auto") or {}
        if auto.get("tipo") != "flujo":
            continue
        comp = auto.get("compliance") or {}
        manual = fm.get("manual") or {}
        cliente = str(manual.get("cliente") or "")
        dominio = str(manual.get("dominio") or auto.get("dominio") or "")
        flows.append({
            "flow": f"{cliente} · {dominio}".strip(" ·"),
            "auto_verdict": str(comp.get("mirada_danwor") or ""),
            "senior_override": str(manual.get("compliance_veredicto_confirmado")
                                   or manual.get("veredicto") or "").strip().lower(),
            "origenes": list(auto.get("origenes") or []),
            "hallazgos": comp.get("hallazgos") or [],
        })
    return flows


def load_tree_criteria(pack: str) -> dict:
    """Criterio PRESCRIPTIVO del árbol de extracción: decisiones + notas de sus hojas."""
    path = CONTENT_PACKS_DIR / pack / "trees" / f"{TREE_ID}.yaml"
    if not path.exists():
        return {}
    try:
        tree = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    patterns = []
    for leaf_id, leaf in (tree.get("leaves") or {}).items():
        if not isinstance(leaf, dict):
            continue
        decision = str(leaf.get("decision") or leaf.get("title") or "").strip()
        notes = [str(n).strip() for n in (leaf.get("notes") or []) if str(n).strip()]
        pa = leaf.get("post_actions") or {}
        next_steps = [str(s).strip() for s in (pa.get("next_steps") or []) if str(s).strip()]
        if decision or notes or next_steps:
            patterns.append({"leaf": leaf_id, "decision": decision,
                             "next_steps": next_steps, "notes": notes})
    return {"tree": tree.get("id") or TREE_ID,
            "title": tree.get("title") or "", "recommended_patterns": patterns}


def load_field_lessons() -> list[dict]:
    """Lecciones de campo del eje de extracción (reports/lessons.yaml → extraction_options)."""
    path = REPORTS_DIR / "lessons.yaml"
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    raw = (data.get("by_tree") or {}).get("extraction_options") or []
    seen, lessons = set(), []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("lesson") or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        lessons.append({"lesson": text, "context": item.get("context"),
                        "outcome": item.get("outcome"), "source": item.get("source")})
    return lessons


def audit_catalog(flows: list[dict]) -> dict:
    """Realidad auditada del sourcing ABAP a lo largo de TODOS los flujos."""
    agg = {"ideal": 0, "acceptable_view": 0, "poor_raw": 0, "acceptable_bw": 0, "unknown": 0}
    per_flow, raw_exceptions = [], []
    for f in flows:
        classes = {}
        for src in f["origenes"]:
            c = _classify(src)
            if c:
                classes[c] = classes.get(c, 0) + 1
                agg[c] = agg.get(c, 0) + 1
        raws = [s for s in f["origenes"] if _classify(s) == "poor_raw"]
        for s in raws:
            raw_exceptions.append({"flow": f["flow"], "origen": s})
        nvr = [h.get("evidencia") for h in f["hallazgos"]
               if isinstance(h, dict) and h.get("titulo", "").lower().startswith("nombre vs")]
        per_flow.append({"flow": f["flow"], "auto_verdict": f["auto_verdict"],
                         "abap_desacople": classes.get("ideal", 0) + classes.get("acceptable_view", 0),
                         "abap_crudas": classes.get("poor_raw", 0),
                         "name_vs_reality": nvr})
    abap_total = agg["ideal"] + agg["acceptable_view"] + agg["poor_raw"]
    decoupled = agg["ideal"] + agg["acceptable_view"]
    return {"abap_family_origins": abap_total, "abap_decoupled": decoupled,
            "abap_raw": agg["poor_raw"],
            "decouple_rate": round(decoupled / abap_total, 3) if abap_total else None,
            "by_class": agg, "by_flow": per_flow, "raw_exceptions": raw_exceptions}


def find_divergences(flows: list[dict]) -> list[dict]:
    """auto → desafía al senior: flujos donde el veredicto auto ≠ el override manual."""
    out = []
    for f in flows:
        ov = f["senior_override"]
        if ov and ov != f["auto_verdict"]:
            out.append({"flow": f["flow"], "auto": f["auto_verdict"], "senior": ov,
                        "nota": "El senior confirmó un veredicto distinto al auto — reconciliar "
                                "(¿el auto quedó corto/largo, o el override ya no aplica?)."})
    return out


def build_reconciliation(audit: dict, tree: dict, lessons: list[dict],
                         divergences: list[dict]) -> list[str]:
    """Observaciones determinísticas que cruzan ambas miradas (el corazón del bucle)."""
    obs = []
    rate = audit.get("decouple_rate")
    if rate is not None:
        pct = f"{rate:.0%}"
        obs.append(
            f"El árbol '{tree.get('title') or TREE_ID}' prescribe desacoplar vía CDS-as-API; "
            f"la auditoría muestra {audit['abap_decoupled']} de {audit['abap_family_origins']} "
            f"orígenes ABAP desacoplados ({pct}). El criterio se sostiene en la práctica."
            if rate >= 0.8 else
            f"El árbol prescribe CDS-as-API, pero solo {pct} del sourcing ABAP desacopla "
            f"({audit['abap_decoupled']}/{audit['abap_family_origins']}) — brecha criterio↔realidad a revisar."
        )
    if audit["raw_exceptions"]:
        head = audit["raw_exceptions"][:5]
        ex = "; ".join(f"{e['flow']}: {e['origen']}" for e in head)
        more = f" (+{len(audit['raw_exceptions']) - len(head)} más, ver catalog_audit.raw_exceptions)" \
            if len(audit["raw_exceptions"]) > len(head) else ""
        obs.append(f"Excepciones crudas auditadas ({len(audit['raw_exceptions'])}): {ex}{more}. "
                   "Contrastar contra el criterio del árbol (¿alternativa viable o deuda aceptada?).")
    if divergences:
        obs.append(f"{len(divergences)} flujo(s) con veredicto auto ≠ override del senior — reconciliar criterio.")
    else:
        obs.append("Sin divergencias auto↔senior: el veredicto automático coincide con el juicio del "
                   "senior en todos los flujos (el criterio convergió).")
    if lessons:
        obs.append(f"{len(lessons)} lección(es) de campo del eje de extracción a incorporar como criterio "
                   "homogéneo (ver field_lessons).")
    return obs


def build_summary(audit, tree, lessons, divergences, reconciliation) -> str:
    L = ["=" * 72,
         "RECONCILIACIÓN DIAGNÓSTICO ⇄ AUTOMATIZACIÓN — EJE EXTRACCIÓN",
         "Copiloto Metodológico | Arquitecto Danwor", "=" * 72, "",
         f"Flujos auditados: {len(audit['by_flow'])}",
         f"Sourcing ABAP: {audit['abap_decoupled']}/{audit['abap_family_origins']} desacoplan "
         f"({(audit['decouple_rate'] or 0):.0%}); {audit['abap_raw']} cruda(s).", ""]
    L.append("── AUTO → DESAFÍA AL DIAGNÓSTICO ──")
    if divergences:
        for d in divergences:
            L.append(f"  ⚠ {d['flow']}: auto={d['auto']} vs senior={d['senior']}")
    else:
        L.append("  ✓ Sin divergencias auto↔senior (criterio convergido).")
    if audit["raw_exceptions"]:
        L.append("  Excepciones crudas:")
        for e in audit["raw_exceptions"]:
            L.append(f"    · {e['flow']} → {e['origen']}")
    L.append("")
    L.append("── DIAGNÓSTICO → ENRIQUECE AL AUTO (criterio de campo) ──")
    for ln in lessons[:12]:
        oc = f" [{ln['outcome']}]" if ln.get("outcome") else ""
        L.append(f"  • {ln['lesson']}{oc}")
    if len(lessons) > 12:
        L.append(f"  … (+{len(lessons) - 12} lecciones)")
    L.append("")
    L.append("── OBSERVACIONES DE RECONCILIACIÓN ──")
    for o in reconciliation:
        L.append(f"  → {o}")
    L.append("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description="Reconciliación diagnóstico ⇄ automatización (eje extracción)")
    ap.add_argument("--pack", default=None, help="Content pack (default: DANWOR_CONTENT_PACK o datasphere_core)")
    ap.add_argument("--catalog-dir", default=None, help="Directorio del catálogo (default: catalog_local)")
    ap.add_argument("--report-dir", default=str(REPORTS_DIR))
    args = ap.parse_args()

    import os
    pack = args.pack or os.getenv("DANWOR_CONTENT_PACK", "").strip() or "datasphere_core"
    cat_dir = Path(args.catalog_dir or os.getenv("DANWOR_CATALOG_DIR", "").strip()
                   or str(BASE_DIR / "catalog_local"))
    report_dir = Path(args.report_dir)

    print(f"\n[DIR] Catálogo: {cat_dir} | pack: {pack}")
    flows = load_flows(cat_dir)
    if not flows:
        print("No hay flujos en el catálogo para reconciliar. Generá el catálogo primero.")
        sys.exit(0)
    print(f"   {len(flows)} flujo(s).")

    audit = audit_catalog(flows)
    tree = load_tree_criteria(pack)
    lessons = load_field_lessons()
    divergences = find_divergences(flows)
    reconciliation = build_reconciliation(audit, tree, lessons, divergences)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "axis": AXIS,
        "catalog_audit": audit,
        "senior_divergences": divergences,
        "tree_criteria": tree,
        "field_lessons": lessons,
        "reconciliation": reconciliation,
    }
    print("\n[OK] Escribiendo reportes...")
    write_report(report_dir, "reconciliation.yaml", report)
    summary = build_summary(audit, tree, lessons, divergences, reconciliation)
    write_report(report_dir, "reconciliation_summary.txt", summary)
    # El resumen tiene glifos (⇄, ✓) que la consola cp1252 de Windows no encodea;
    # en el webapp stdout va a un buffer UTF-8 (no falla). Blindamos el eco de CLI.
    try:
        print("\n" + summary)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(("\n" + summary).encode("utf-8", "replace"))


if __name__ == "__main__":
    main()
