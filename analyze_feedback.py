"""
analyze_feedback.py
===================
Analiza el feedback_log/ y genera reportes en reports/feedback/.

Tipos de entrada actuales en feedback_log/ (schema de la webapp FastAPI):
  - assistant_response  → 👍/👎 sobre respuestas del asistente IA (+ comentario)
  - module_content      → 👍/👎 sobre el contenido de un módulo/apéndice
  - section_content     → 👍/👎 sobre una sección puntual de un módulo (+ comentario)
  - academy_content     → 👍/👎 sobre una página de Danwor Academy (+ comentario)

(El feedback de árboles/decisiones y los casos viven en case_library/ y los analiza
analyze_cases.py; este script cubre solo el feedback 👍/👎 de feedback_log/.)

Uso:
    python analyze_feedback.py                  # todo
    python analyze_feedback.py --type ia        # solo asistente IA
    python analyze_feedback.py --type modulos   # solo contenido de módulos
    python analyze_feedback.py --type secciones # solo secciones de módulos
    python analyze_feedback.py --type academy   # solo Danwor Academy
    python analyze_feedback.py --module 03      # filtrar por módulo (ia/modulos)

Salida en reports/feedback/:
    ia_feedback.yaml        → likes/dislikes del asistente por módulo/acción + comentarios
    module_feedback.yaml    → likes/dislikes por módulo/apéndice
    section_feedback.yaml   → likes/dislikes por sección + comentarios
    academy_feedback.yaml   → likes/dislikes por página de Academy + comentarios
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

# Alias de CLI → type real escrito por la webapp.
_TYPE_ALIASES = {
    "ia":        "assistant_response",
    "modulos":   "module_content",
    "secciones": "section_content",
    "academy":   "academy_content",
}


# =========================================================
# Loader
# =========================================================

def load_feedback(feedback_dir: Path, type_filter: str | None = None,
                  module_filter: str | None = None) -> list[dict]:
    entries: list[dict] = []
    if not feedback_dir.exists():
        print(f"[WARN] No existe {feedback_dir} — no hay feedback registrado.")
        return entries

    want_type = _TYPE_ALIASES.get(type_filter) if type_filter else None
    for p in sorted(feedback_dir.glob("*.yaml")):
        try:
            e = yaml.safe_load(p.read_text(encoding="utf-8"))
            if not isinstance(e, dict):
                continue
            e["_file"] = p.name
            if want_type and e.get("type") != want_type:
                continue
            if module_filter and str(e.get("module", "")) != module_filter:
                continue
            entries.append(e)
        except Exception as ex:
            print(f"[WARN] Error leyendo {p.name}: {ex}")

    return entries


def _satisfaction(up: int, down: int) -> float | None:
    total = up + down
    return round(up / total, 2) if total else None


# =========================================================
# Análisis — asistente IA (assistant_response)
# =========================================================

def analyze_assistant(entries: list[dict]) -> dict:
    """Likes/dislikes de respuestas del asistente IA, por módulo y acción,
    con tópicos flagged (alto ratio de dislike) y comentarios negativos."""
    items = [e for e in entries if e.get("type") == "assistant_response"]
    if not items:
        return {"total": 0, "note": "Sin feedback del asistente IA registrado."}

    by_module: dict[str, Counter] = defaultdict(Counter)
    by_action: dict[str, Counter] = defaultdict(Counter)
    by_topic:  dict[str, Counter] = defaultdict(Counter)
    comments_down: list[dict] = []

    for e in items:
        mod    = e.get("module", "?")
        action = e.get("action", "?")
        topic  = e.get("topic", "?")
        rating = e.get("rating", "?")
        by_module[mod][rating] += 1
        by_action[action][rating] += 1
        by_topic[f"{mod}::{topic}"][rating] += 1
        if rating == "down" and e.get("comment"):
            comments_down.append({
                "module": mod, "action": action, "topic": topic,
                "comment": e["comment"], "ts": e.get("ts", ""),
            })

    total_up   = sum(c.get("up", 0)   for c in by_module.values())
    total_down = sum(c.get("down", 0) for c in by_module.values())

    flagged_topics = []
    for key, counts in by_topic.items():
        up, down = counts.get("up", 0), counts.get("down", 0)
        t = up + down
        if t >= 2 and down / t >= 0.4:
            mod, topic = key.split("::", 1)
            flagged_topics.append({
                "module": mod, "topic": topic, "up": up, "down": down,
                "dislike_rate": round(down / t, 2),
            })
    flagged_topics.sort(key=lambda x: x["dislike_rate"], reverse=True)

    return {
        "generated_at":   datetime.now().isoformat(),
        "total":          total_up + total_down,
        "total_up":       total_up,
        "total_down":     total_down,
        "satisfaction":   _satisfaction(total_up, total_down),
        "by_module":      {k: dict(v) for k, v in sorted(by_module.items())},
        "by_action":      {k: dict(v) for k, v in sorted(by_action.items())},
        "flagged_topics": flagged_topics,
        "comments_down":  comments_down,
    }


# =========================================================
# Análisis — contenido de módulos (module_content)
# =========================================================

def analyze_modules(entries: list[dict]) -> dict:
    """Likes/dislikes del contenido de cada módulo/apéndice."""
    items = [e for e in entries if e.get("type") == "module_content"]
    if not items:
        return {"total": 0, "note": "Sin feedback de contenido de módulos registrado."}

    by_module: dict[str, Counter] = defaultdict(Counter)
    titles: dict[str, str] = {}
    for e in items:
        mod = e.get("module", "?")
        titles[mod] = e.get("module_title", mod)
        by_module[mod][e.get("rating", "?")] += 1

    total_up   = sum(c.get("up", 0)   for c in by_module.values())
    total_down = sum(c.get("down", 0) for c in by_module.values())
    rows = [{
        "module":       mod,
        "title":        titles.get(mod, mod),
        "up":           c.get("up", 0),
        "down":         c.get("down", 0),
        "satisfaction": _satisfaction(c.get("up", 0), c.get("down", 0)),
    } for mod, c in sorted(by_module.items())]

    return {
        "generated_at": datetime.now().isoformat(),
        "total":        total_up + total_down,
        "total_up":     total_up,
        "total_down":   total_down,
        "satisfaction": _satisfaction(total_up, total_down),
        "by_module":    rows,
    }


# =========================================================
# Análisis — secciones de módulos (section_content)
# =========================================================

def analyze_sections(entries: list[dict]) -> dict:
    """Likes/dislikes por sección puntual de cada módulo, con comentarios
    negativos. La clave agrupa módulo + slug de sección para desambiguar."""
    items = [e for e in entries if e.get("type") == "section_content"]
    if not items:
        return {"total": 0, "note": "Sin feedback de secciones registrado."}

    by_section: dict[str, Counter] = defaultdict(Counter)
    labels: dict[str, dict] = {}
    comments_down: list[dict] = []
    for e in items:
        mod = e.get("module", "?")
        slug = e.get("section_slug", "?")
        key = f"{mod}::{slug}"
        labels[key] = {
            "module": mod,
            "module_title": e.get("module_title", mod),
            "section_title": e.get("section_title", slug),
        }
        rating = e.get("rating", "?")
        by_section[key][rating] += 1
        if rating == "down" and e.get("comment"):
            comments_down.append({
                "module": mod,
                "section_title": labels[key]["section_title"],
                "comment": e["comment"], "ts": e.get("ts", ""),
            })

    total_up   = sum(c.get("up", 0)   for c in by_section.values())
    total_down = sum(c.get("down", 0) for c in by_section.values())
    rows = [{
        "module":        labels[key]["module"],
        "module_title":  labels[key]["module_title"],
        "section_title": labels[key]["section_title"],
        "up":            c.get("up", 0),
        "down":          c.get("down", 0),
        "satisfaction":  _satisfaction(c.get("up", 0), c.get("down", 0)),
    } for key, c in sorted(by_section.items())]

    return {
        "generated_at":  datetime.now().isoformat(),
        "total":         total_up + total_down,
        "total_up":      total_up,
        "total_down":    total_down,
        "satisfaction":  _satisfaction(total_up, total_down),
        "by_section":    rows,
        "comments_down": comments_down,
    }


# =========================================================
# Análisis — Danwor Academy (academy_content)
# =========================================================

def analyze_academy(entries: list[dict]) -> dict:
    """Likes/dislikes por página de Danwor Academy, con comentarios negativos."""
    items = [e for e in entries if e.get("type") == "academy_content"]
    if not items:
        return {"total": 0, "note": "Sin feedback de Danwor Academy registrado."}

    by_page: dict[str, Counter] = defaultdict(Counter)
    titles: dict[str, str] = {}
    comments_down: list[dict] = []
    for e in items:
        page = e.get("academy_page", "?")
        titles[page] = e.get("academy_title", page)
        rating = e.get("rating", "?")
        by_page[page][rating] += 1
        if rating == "down" and e.get("comment"):
            comments_down.append({
                "page": page, "title": titles[page],
                "comment": e["comment"], "ts": e.get("ts", ""),
            })

    total_up   = sum(c.get("up", 0)   for c in by_page.values())
    total_down = sum(c.get("down", 0) for c in by_page.values())
    rows = [{
        "page":         page,
        "title":        titles.get(page, page),
        "up":           c.get("up", 0),
        "down":         c.get("down", 0),
        "satisfaction": _satisfaction(c.get("up", 0), c.get("down", 0)),
    } for page, c in sorted(by_page.items())]

    return {
        "generated_at":  datetime.now().isoformat(),
        "total":         total_up + total_down,
        "total_up":      total_up,
        "total_down":    total_down,
        "satisfaction":  _satisfaction(total_up, total_down),
        "by_page":       rows,
        "comments_down": comments_down,
    }


# =========================================================
# Summary legible
# =========================================================

def _sat_str(sat: float | None) -> str:
    return f"{int(sat * 100)}%" if sat is not None else "n/d"


def build_summary(asis: dict, mods: dict, secs: dict, acad: dict) -> str:
    lines = [
        "=" * 60,
        "REPORTE DE FEEDBACK — COPILOTO METODOLÓGICO DANWOR",
        f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "=" * 60,
        "",
    ]

    # -- Asistente IA
    lines.append("-- ASISTENTE IA --")
    lines.append("")
    if asis.get("total", 0) == 0:
        lines.append("  Sin datos.")
    else:
        lines.append(f"  Total: {asis['total']}  👍 {asis['total_up']}  👎 {asis['total_down']}  "
                     f"Satisfacción: {_sat_str(asis.get('satisfaction'))}")
        if asis.get("by_module"):
            lines.append("  Por módulo:")
            for mod, counts in asis["by_module"].items():
                lines.append(f"    {mod}: 👍 {counts.get('up',0)}  👎 {counts.get('down',0)}")
        flagged = asis.get("flagged_topics", [])
        if flagged:
            lines.append("  [!] Tópicos con alto índice de dislike (≥40%):")
            for t in flagged:
                lines.append(f"    [{t['module']}] {t['topic']} — {int(t['dislike_rate']*100)}% "
                             f"({t['down']} de {t['up']+t['down']})")
        comments = asis.get("comments_down", [])
        if comments:
            lines.append(f"  Comentarios negativos ({len(comments)}):")
            for c in comments[:5]:
                lines.append(f"    [{c['module']}·{c['action']}] \"{c['comment']}\"")
            if len(comments) > 5:
                lines.append(f"    ... y {len(comments)-5} más en ia_feedback.yaml")
    lines.append("")

    # -- Contenido de módulos
    lines.append("-- CONTENIDO DE MÓDULOS --")
    lines.append("")
    if mods.get("total", 0) == 0:
        lines.append("  Sin datos.")
    else:
        lines.append(f"  Total: {mods['total']}  👍 {mods['total_up']}  👎 {mods['total_down']}  "
                     f"Satisfacción: {_sat_str(mods.get('satisfaction'))}")
        for row in mods.get("by_module", []):
            lines.append(f"    {row['module']} ({row['title']}): 👍 {row['up']}  👎 {row['down']}")
    lines.append("")

    # -- Secciones de módulos
    lines.append("-- SECCIONES DE MÓDULOS --")
    lines.append("")
    if secs.get("total", 0) == 0:
        lines.append("  Sin datos.")
    else:
        lines.append(f"  Total: {secs['total']}  👍 {secs['total_up']}  👎 {secs['total_down']}  "
                     f"Satisfacción: {_sat_str(secs.get('satisfaction'))}")
        for row in secs.get("by_section", []):
            lines.append(f"    [{row['module']}] {row['section_title']}: 👍 {row['up']}  👎 {row['down']}")
        comments = secs.get("comments_down", [])
        if comments:
            lines.append(f"  Comentarios negativos ({len(comments)}):")
            for c in comments[:5]:
                lines.append(f"    [{c['module']}·{c['section_title']}] \"{c['comment']}\"")
            if len(comments) > 5:
                lines.append(f"    ... y {len(comments)-5} más en section_feedback.yaml")
    lines.append("")

    # -- Danwor Academy
    lines.append("-- DANWOR ACADEMY --")
    lines.append("")
    if acad.get("total", 0) == 0:
        lines.append("  Sin datos.")
    else:
        lines.append(f"  Total: {acad['total']}  👍 {acad['total_up']}  👎 {acad['total_down']}  "
                     f"Satisfacción: {_sat_str(acad.get('satisfaction'))}")
        for row in acad.get("by_page", []):
            lines.append(f"    {row['title']}: 👍 {row['up']}  👎 {row['down']}")
        comments = acad.get("comments_down", [])
        if comments:
            lines.append(f"  Comentarios negativos ({len(comments)}):")
            for c in comments[:5]:
                lines.append(f"    [{c['title']}] \"{c['comment']}\"")
            if len(comments) > 5:
                lines.append(f"    ... y {len(comments)-5} más en academy_feedback.yaml")
    lines.append("")

    lines += ["=" * 60, ""]
    return "\n".join(lines)


# =========================================================
# Main
# =========================================================

def main() -> None:
    # El summary usa emojis (👍/👎). En consolas Windows (cp1252) el print final
    # crashea; forzamos UTF-8 si se puede. Con stdout redirigido (panel → StringIO)
    # no existe reconfigure y se ignora sin afectar la captura.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Analizador de feedback_log/")
    parser.add_argument(
        "--type", choices=["ia", "modulos", "secciones", "academy", "all"], default="all",
        help="Tipo de feedback a analizar (default: all)",
    )
    parser.add_argument("--module",       default=None, help="Filtrar por módulo (ej: 03)")
    parser.add_argument("--feedback-dir", default=str(FEEDBACK_DIR))
    parser.add_argument("--report-dir",   default=str(REPORTS_DIR))
    args = parser.parse_args()

    feedback_dir = Path(args.feedback_dir)
    report_dir   = Path(args.report_dir)
    type_filter  = None if args.type == "all" else args.type

    print(f"\n[DIR] Cargando feedback desde {feedback_dir}...")
    entries = load_feedback(feedback_dir, type_filter=type_filter, module_filter=args.module)
    if not entries:
        print("No hay entradas de feedback para analizar.")
        sys.exit(0)
    print(f"   {len(entries)} entradas cargadas.\n")

    print("[>>] Analizando...")
    asis_report = analyze_assistant(entries)
    mods_report = analyze_modules(entries)
    secs_report = analyze_sections(entries)
    acad_report = analyze_academy(entries)

    print("\n[OK] Escribiendo reportes...")
    write_report(report_dir, "ia_feedback.yaml",      asis_report)
    write_report(report_dir, "module_feedback.yaml",  mods_report)
    write_report(report_dir, "section_feedback.yaml", secs_report)
    write_report(report_dir, "academy_feedback.yaml", acad_report)

    print("\n[..] Generando resumen...")
    summary = build_summary(asis_report, mods_report, secs_report, acad_report)
    write_report(report_dir, "summary.txt", summary)

    print("\n" + summary)


if __name__ == "__main__":
    main()
