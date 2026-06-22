"""
analyze_feedback.py
===================
Analiza el feedback_log/ y genera reportes en reports/feedback/.

Tipos de entrada en feedback_log/:
  - section_action / quick_action / chat  → feedback de outputs IA (👍/👎)
  - tree_leaf                             → feedback de decisión en árbol
  - missing_tree                          → árbol faltante solicitado por consultor

Uso:
    python analyze_feedback.py                  # todos los tipos
    python analyze_feedback.py --type ia        # solo feedback de outputs IA
    python analyze_feedback.py --type trees     # solo feedback de árboles
    python analyze_feedback.py --type missing   # solo árboles faltantes
    python analyze_feedback.py --module 03      # filtrar por módulo

Salida en reports/feedback/:
    ia_feedback.yaml        → likes/dislikes por módulo/sección/acción
    tree_feedback.yaml      → feedback por árbol/leaf con tasa de éxito
    missing_trees.yaml      → árboles solicitados, agrupados y priorizados
    summary.txt             → resumen legible
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import yaml

from pipeline.common import write_report


# =========================================================
# Config
# =========================================================

BASE_DIR     = Path(__file__).parent
FEEDBACK_DIR = BASE_DIR / "feedback_log"
REPORTS_DIR  = BASE_DIR / "reports" / "feedback"


# =========================================================
# Loader
# =========================================================

def load_feedback(feedback_dir: Path, type_filter: str | None = None,
                  module_filter: str | None = None) -> list[dict]:
    entries = []
    if not feedback_dir.exists():
        print(f"[WARN] No existe {feedback_dir} — no hay feedback registrado.")
        return entries

    for p in sorted(feedback_dir.glob("*.yaml")):
        try:
            e = yaml.safe_load(p.read_text(encoding="utf-8"))
            if not isinstance(e, dict):
                continue
            e["_file"] = p.name

            # Filtros opcionales
            if module_filter and str(e.get("module", "")) != module_filter:
                continue

            ftype = e.get("type", "")
            if type_filter == "ia" and ftype not in ("section_action", "quick_action", "chat"):
                continue
            if type_filter == "trees" and ftype != "tree_leaf":
                continue
            if type_filter == "missing" and ftype != "missing_tree":
                continue

            entries.append(e)
        except Exception as ex:
            print(f"[WARN] Error leyendo {p.name}: {ex}")

    return entries


# =========================================================
# Análisis — IA feedback (section_action / quick_action / chat)
# =========================================================

def analyze_ia_feedback(entries: list[dict]) -> dict:
    """
    Résumen de likes/dislikes de outputs IA.
    Agrupa por módulo, acción, y tipo de feedback.
    Destaca tópicos con mayor ratio de dislikes (señal de mejora).
    """
    ia_types = {"section_action", "quick_action", "chat"}
    ia = [e for e in entries if e.get("type") in ia_types]

    if not ia:
        return {"total": 0, "note": "Sin feedback de outputs IA registrado."}

    by_module:  dict[str, Counter] = defaultdict(Counter)
    by_action:  dict[str, Counter] = defaultdict(Counter)
    by_topic:   dict[str, Counter] = defaultdict(Counter)
    comments_down: list[dict] = []

    for e in ia:
        mod    = e.get("module", "?")
        action = e.get("action", "?")
        topic  = e.get("topic", "?")
        rating = e.get("rating", "?")

        by_module[mod][rating] += 1
        by_action[action][rating] += 1
        by_topic[f"{mod}::{topic}"][rating] += 1

        if rating == "down" and e.get("comment"):
            comments_down.append({
                "module":  mod,
                "action":  action,
                "topic":   topic,
                "comment": e["comment"],
                "ts":      e.get("ts", ""),
            })

    total_up   = sum(c.get("up", 0)   for c in by_module.values())
    total_down = sum(c.get("down", 0) for c in by_module.values())
    total      = total_up + total_down

    # Tópicos con más dislikes — señal de contenido a mejorar
    flagged_topics = []
    for key, counts in by_topic.items():
        up   = counts.get("up", 0)
        down = counts.get("down", 0)
        t    = up + down
        if t >= 2 and down / t >= 0.4:
            mod, topic = key.split("::", 1)
            flagged_topics.append({
                "module":       mod,
                "topic":        topic,
                "down":         down,
                "up":           up,
                "dislike_rate": round(down / t, 2),
            })
    flagged_topics.sort(key=lambda x: x["dislike_rate"], reverse=True)

    return {
        "generated_at":   datetime.now().isoformat(),
        "total":          total,
        "total_up":       total_up,
        "total_down":     total_down,
        "satisfaction":   round(total_up / total, 2) if total else None,
        "by_module":      {k: dict(v) for k, v in sorted(by_module.items())},
        "by_action":      {k: dict(v) for k, v in sorted(by_action.items())},
        "flagged_topics": flagged_topics,
        "comments_down":  comments_down,
    }


# =========================================================
# Análisis — tree_leaf feedback
# =========================================================

def analyze_tree_feedback(entries: list[dict]) -> dict:
    """
    Feedback de decisiones en árbol (rating + outcome + lesson).
    Detecta leaves con alta tasa de 'no anduvo'.
    """
    tree_entries = [e for e in entries if e.get("type") == "tree_leaf"]

    if not tree_entries:
        return {"total": 0, "note": "Sin feedback de árboles registrado."}

    by_tree_leaf: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    lessons: list[dict] = []

    for e in tree_entries:
        tree   = e.get("tree_id", "?")
        leaf   = e.get("leaf_id", "?")
        rating = e.get("rating", "?")
        outcome = e.get("outcome") or rating  # fallback a rating si no hay outcome
        lesson  = e.get("lesson", "")

        by_tree_leaf[tree][leaf][outcome] += 1

        if lesson:
            lessons.append({
                "tree":   tree,
                "leaf":   leaf,
                "lesson": lesson,
                "rating": rating,
                "ts":     e.get("ts", ""),
            })

    # Construir reporte por árbol/leaf
    trees_report = []
    for tree_id, leaves in sorted(by_tree_leaf.items()):
        tree_entry = {"tree_id": tree_id, "leaves": []}
        for leaf_id, outcomes in sorted(leaves.items()):
            total     = sum(outcomes.values())
            no_anduvo = outcomes.get("no anduvo", 0)
            anduvo    = outcomes.get("anduvo", 0)
            tree_entry["leaves"].append({
                "leaf_id":      leaf_id,
                "total":        total,
                "outcomes":     dict(outcomes),
                "success_rate": round(anduvo / total, 2) if total else None,
                "flag":         "revisar" if total >= 2 and (no_anduvo / total) >= 0.3 else "ok",
            })
        trees_report.append(tree_entry)

    return {
        "generated_at": datetime.now().isoformat(),
        "total":        len(tree_entries),
        "trees":        trees_report,
        "lessons":      lessons,
    }


# =========================================================
# Análisis — missing_tree
# =========================================================

def analyze_missing_trees(entries: list[dict]) -> dict:
    """
    Árboles solicitados por consultores que no existen.
    Agrupa por similitud textual básica y prioriza por frecuencia.
    Input directo para decidir qué árboles construir primero.
    """
    missing = [e for e in entries if e.get("type") == "missing_tree"]

    if not missing:
        return {"total": 0, "note": "Sin solicitudes de árboles faltantes registradas."}

    by_module: dict[str, list] = defaultdict(list)
    for e in missing:
        mod = e.get("module", "?")
        by_module[mod].append({
            "tree_needed": e.get("tree_needed", ""),
            "context":     e.get("context") or "",
            "ts":          e.get("ts", ""),
        })

    # Ordenar por módulo y dentro por fecha
    modules_report = []
    for mod, requests in sorted(by_module.items()):
        requests_sorted = sorted(requests, key=lambda x: x["ts"])
        modules_report.append({
            "module":   mod,
            "count":    len(requests),
            "priority": "alta" if len(requests) >= 3 else "media" if len(requests) >= 2 else "baja",
            "requests": requests_sorted,
        })
    # Ordenar por count descendente
    modules_report.sort(key=lambda x: x["count"], reverse=True)

    return {
        "generated_at":   datetime.now().isoformat(),
        "total":          len(missing),
        "by_module":      modules_report,
    }


# =========================================================
# Summary legible
# =========================================================

def build_summary(ia: dict, trees: dict, missing: dict) -> str:
    lines = [
        "=" * 60,
        "REPORTE DE FEEDBACK — COPILOTO METODOLÓGICO DANWOR",
        f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "=" * 60,
        "",
    ]

    # -- IA feedback
    lines.append("-- FEEDBACK DE OUTPUTS IA --")
    lines.append("")
    if ia.get("total", 0) == 0:
        lines.append("  Sin datos.")
    else:
        sat = ia.get("satisfaction")
        sat_str = f"{int(sat*100)}%" if sat is not None else "n/d"
        lines.append(f"  Total: {ia['total']}  👍 {ia['total_up']}  👎 {ia['total_down']}  "
                     f"Satisfacción: {sat_str}")
        lines.append("")

        if ia.get("by_module"):
            lines.append("  Por módulo:")
            for mod, counts in ia["by_module"].items():
                lines.append(f"    {mod}: 👍 {counts.get('up',0)}  👎 {counts.get('down',0)}")
            lines.append("")

        if ia.get("by_action"):
            lines.append("  Por acción:")
            for action, counts in ia["by_action"].items():
                lines.append(f"    {action}: 👍 {counts.get('up',0)}  👎 {counts.get('down',0)}")
            lines.append("")

        flagged = ia.get("flagged_topics", [])
        if flagged:
            lines.append("  [!] Tópicos con alto índice de dislike (≥40%):")
            for t in flagged:
                lines.append(f"    [{t['module']}] {t['topic']} — "
                             f"{int(t['dislike_rate']*100)}% dislikes "
                             f"({t['down']} de {t['up']+t['down']})")
            lines.append("")

        comments = ia.get("comments_down", [])
        if comments:
            lines.append(f"  Comentarios negativos ({len(comments)}):")
            for c in comments[:5]:
                lines.append(f"    [{c['module']}·{c['action']}] \"{c['comment']}\"")
            if len(comments) > 5:
                lines.append(f"    ... y {len(comments)-5} más en ia_feedback.yaml")
            lines.append("")

    # -- Tree feedback
    lines.append("-- FEEDBACK DE ÁRBOLES --")
    lines.append("")
    if trees.get("total", 0) == 0:
        lines.append("  Sin datos.")
    else:
        lines.append(f"  Total entradas: {trees['total']}")
        for tree in trees.get("trees", []):
            flagged_leaves = [l for l in tree["leaves"] if l["flag"] == "revisar"]
            if flagged_leaves:
                lines.append(f"  [!] {tree['tree_id']} — leaves a revisar:")
                for l in flagged_leaves:
                    lines.append(f"    {l['leaf_id']} — {l['outcomes']} "
                                 f"(success: {int((l['success_rate'] or 0)*100)}%)")
        lessons = trees.get("lessons", [])
        if lessons:
            lines.append(f"  Lecciones registradas: {len(lessons)}")
            for l in lessons[:3]:
                lines.append(f"    [{l['tree']}·{l['leaf']}] {l['lesson']}")
            if len(lessons) > 3:
                lines.append(f"    ... y {len(lessons)-3} más en tree_feedback.yaml")
    lines.append("")

    # -- Missing trees
    lines.append("-- ÁRBOLES FALTANTES SOLICITADOS --")
    lines.append("")
    if missing.get("total", 0) == 0:
        lines.append("  Sin solicitudes registradas.")
    else:
        lines.append(f"  Total solicitudes: {missing['total']}")
        lines.append("")
        for mod in missing.get("by_module", []):
            lines.append(f"  [{mod['priority'].upper()}] Módulo {mod['module']} "
                         f"— {mod['count']} solicitud{'es' if mod['count'] > 1 else ''}:")
            for r in mod["requests"]:
                lines.append(f"    • {r['tree_needed']}")
                if r.get("context"):
                    lines.append(f"      Contexto: {r['context'][:100]}")
        lines.append("")

    lines += ["=" * 60, ""]
    return "\n".join(lines)


# =========================================================
# Main
# =========================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Analizador de feedback_log/")
    parser.add_argument(
        "--type", choices=["ia", "trees", "missing", "all"], default="all",
        help="Tipo de feedback a analizar (default: all)",
    )
    parser.add_argument("--module",      default=None, help="Filtrar por módulo (ej: 03)")
    parser.add_argument("--feedback-dir", default=str(FEEDBACK_DIR))
    parser.add_argument("--report-dir",   default=str(REPORTS_DIR))
    args = parser.parse_args()

    feedback_dir = Path(args.feedback_dir)
    report_dir   = Path(args.report_dir)

    type_map = {
        "ia":      "ia",
        "trees":   "trees",
        "missing": "missing",
        "all":     None,
    }
    type_filter = type_map[args.type]

    print(f"\n[DIR] Cargando feedback desde {feedback_dir}...")
    entries = load_feedback(feedback_dir, type_filter=type_filter, module_filter=args.module)
    if not entries:
        print("No hay entradas de feedback para analizar.")
        sys.exit(0)
    print(f"   {len(entries)} entradas cargadas.\n")

    print("[>>] Analizando...")
    ia_report      = analyze_ia_feedback(entries)
    trees_report   = analyze_tree_feedback(entries)
    missing_report = analyze_missing_trees(entries)

    print("\n[OK] Escribiendo reportes...")
    write_report(report_dir, "ia_feedback.yaml",    ia_report)
    write_report(report_dir, "tree_feedback.yaml",  trees_report)
    write_report(report_dir, "missing_trees.yaml",  missing_report)

    print("\n[..] Generando resumen...")
    summary = build_summary(ia_report, trees_report, missing_report)
    write_report(report_dir, "summary.txt", summary)

    print("\n" + summary)


if __name__ == "__main__":
    main()