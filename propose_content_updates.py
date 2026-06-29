"""
propose_content_updates.py
==========================
Analiza feedback_log/ y case_library/ para proponer mejoras concretas
al contenido de los modulos (.md).

Fuentes de señal:
  - feedback_log/: tópicos con alto dislike, comentarios negativos,
                   missing trees frecuentes
  - case_library/: lecciones registradas, rutas uncharted, outcomes
                   negativos en leaves

Sin --llm: propuestas estructuradas basadas en señales detectadas.
Con --llm: redacta el texto concreto a agregar/reescribir en cada caso.

Uso:
    python propose_content_updates.py
    python propose_content_updates.py --llm
    python propose_content_updates.py --module 03
    python propose_content_updates.py --min-signals 1

Salida en reports/content_proposals/:
    proposals.yaml      -> propuestas estructuradas por modulo/seccion
    summary.txt         -> resumen legible para revision manual
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml

from pipeline.common import write_report, MODEL


# =========================================================
# Config
# =========================================================

BASE_DIR         = Path(__file__).parent
FEEDBACK_DIR     = BASE_DIR / "feedback_log"
CASE_DIR         = BASE_DIR / "case_library"
CONTENT_DIR      = BASE_DIR / "content_packs" / "datasphere_core" / "content"
REPORTS_DIR      = BASE_DIR / "reports" / "content_proposals"

PRIORITY_HIGH    = "alta"
PRIORITY_MEDIUM  = "media"
PRIORITY_LOW     = "baja"

# Umbral de dislike para marcar como reescritura necesaria
DISLIKE_THRESHOLD = 0.40   # 40%
MIN_RATINGS       = 2      # minimo de ratings para considerar el dato

# =========================================================
# Loaders
# =========================================================

def load_feedback(feedback_dir: Path, module_filter: str | None) -> list[dict]:
    entries = []
    if not feedback_dir.exists():
        return entries
    for p in sorted(feedback_dir.glob("*.yaml")):
        try:
            e = yaml.safe_load(p.read_text(encoding="utf-8"))
            if not isinstance(e, dict):
                continue
            if module_filter and str(e.get("module", "")) != module_filter:
                continue
            entries.append(e)
        except Exception:
            pass
    return entries


def load_cases(case_dir: Path, module_filter: str | None) -> list[dict]:
    cases = []
    if not case_dir.exists():
        return cases
    for p in sorted(case_dir.glob("*.yaml")):
        try:
            c = yaml.safe_load(p.read_text(encoding="utf-8"))
            if not isinstance(c, dict):
                continue
            mod = str(c.get("source", {}).get("module", "")).split("_")[0]
            if module_filter and mod != module_filter:
                continue
            c["_module_code"] = mod
            cases.append(c)
        except Exception:
            pass
    return cases


def load_content_index(content_dir: Path) -> dict[str, list[str]]:
    """
    Devuelve {module_code: [heading_text, ...]} para poder
    vincular propuestas a secciones reales.
    """
    import re
    index: dict[str, list[str]] = {}
    if not content_dir.exists():
        return index
    for p in content_dir.glob("*.md"):
        code = p.stem.split("_")[0]
        headings = re.findall(r"^#{1,3}\s+(.+)$", p.read_text(encoding="utf-8"), re.M)
        index[code] = headings
    return index


# =========================================================
# Signal extractors
# =========================================================

def signals_from_feedback(entries: list[dict]) -> list[dict]:
    """
    Extrae señales de mejora desde feedback_log/.
    Tipos detectados:
      - high_dislike:    tópico con ratio dislike >= DISLIKE_THRESHOLD
      - negative_comment: comentario negativo explicito
      - missing_tree:    árbol faltante frecuente (señal de gap en contenido)
    """
    signals = []

    # -- Agrupar ratings por modulo+topic
    by_topic: dict[str, dict] = defaultdict(lambda: {"up": 0, "down": 0, "comments": [], "module": ""})
    for e in entries:
        ftype = e.get("type", "")
        if ftype not in ("section_action", "quick_action", "chat"):
            continue
        key = f"{e.get('module','?')}::{e.get('topic','?')}"
        by_topic[key]["module"] = str(e.get("module", "?"))
        by_topic[key]["topic"]  = str(e.get("topic",  "?"))
        if e.get("rating") == "up":
            by_topic[key]["up"] += 1
        elif e.get("rating") == "down":
            by_topic[key]["down"] += 1
            if e.get("comment"):
                by_topic[key]["comments"].append(e["comment"].strip())

    for key, data in by_topic.items():
        total = data["up"] + data["down"]
        if total < MIN_RATINGS:
            continue
        rate = data["down"] / total
        if rate >= DISLIKE_THRESHOLD:
            signals.append({
                "type":     "high_dislike",
                "module":   data["module"],
                "topic":    data["topic"],
                "detail":   f"{int(rate*100)}% dislike ({data['down']} de {total})",
                "comments": data["comments"],
                "priority": PRIORITY_HIGH if rate >= 0.6 else PRIORITY_MEDIUM,
                "source":   "feedback_log",
            })
        elif data["comments"]:
            # Hay comentarios negativos aunque el ratio no sea critico
            signals.append({
                "type":     "negative_comment",
                "module":   data["module"],
                "topic":    data["topic"],
                "detail":   f"{len(data['comments'])} comentario(s) negativo(s)",
                "comments": data["comments"],
                "priority": PRIORITY_MEDIUM,
                "source":   "feedback_log",
            })

    # -- Missing trees -> gap de contenido
    missing_count: dict[str, dict] = defaultdict(lambda: {"count": 0, "module": "", "requests": []})
    for e in entries:
        if e.get("type") != "missing_tree":
            continue
        tree_needed = e.get("tree_needed", "").strip()
        if not tree_needed:
            continue
        key = f"{e.get('module','?')}::{tree_needed.lower()[:60]}"
        missing_count[key]["module"]   = str(e.get("module", "?"))
        missing_count[key]["tree"]     = tree_needed
        missing_count[key]["count"]   += 1
        if e.get("context"):
            missing_count[key]["requests"].append(e["context"].strip())

    for key, data in missing_count.items():
        signals.append({
            "type":     "missing_tree_gap",
            "module":   data["module"],
            "topic":    data["tree"],
            "detail":   f"Solicitado {data['count']} vez/veces — sin árbol ni contenido que lo cubra",
            "comments": data["requests"],
            "priority": PRIORITY_HIGH if data["count"] >= 3 else (PRIORITY_MEDIUM if data["count"] >= 2 else PRIORITY_LOW),
            "source":   "feedback_log",
        })

    return signals


def signals_from_cases(cases: list[dict]) -> list[dict]:
    """
    Extrae señales desde case_library/.
    Tipos detectados:
      - lesson_to_add:   lección registrada que deberia estar en el contenido
      - uncharted_gap:   ruta no contemplada — falta contenido que oriente
      - bad_outcome_leaf: leaf con outcome negativo — recomendacion a revisar
    """
    signals = []

    for case in cases:
        mod      = case.get("_module_code", "?")
        tree_id  = case.get("source", {}).get("tree_id", "?")
        tree_title = case.get("source", {}).get("tree_title", tree_id)
        leaf_id  = case.get("leaf_id") or ""
        decision = case.get("decision") or ""
        outcome  = case.get("outcome") or ""

        # -- Lecciones registradas
        for lesson in (case.get("lessons") or []):
            if not lesson:
                continue
            signals.append({
                "type":     "lesson_to_add",
                "module":   mod,
                "topic":    tree_title,
                "leaf_id":  leaf_id,
                "detail":   f"Lección de caso real: \"{lesson}\"",
                "comments": [lesson],
                "priority": PRIORITY_MEDIUM,
                "source":   "case_library",
                "case_id":  case.get("case_id", ""),
            })

        # -- Rutas no contempladas
        if case.get("uncharted"):
            gap_node = case.get("gap_node", "?")
            ctx      = case.get("context_description") or ""
            taken    = case.get("decision_taken") or ""
            signals.append({
                "type":     "uncharted_gap",
                "module":   mod,
                "topic":    tree_title,
                "detail":   f"Ruta no contemplada desde nodo '{gap_node}': {ctx}",
                "comments": [f"Decision tomada: {taken}"] if taken else [],
                "priority": PRIORITY_MEDIUM,
                "source":   "case_library",
                "case_id":  case.get("case_id", ""),
            })

        # -- Outcomes negativos en leaves
        if outcome and "no" in outcome.lower() and leaf_id:
            signals.append({
                "type":     "bad_outcome_leaf",
                "module":   mod,
                "topic":    tree_title,
                "leaf_id":  leaf_id,
                "decision": decision,
                "detail":   f"Outcome negativo en leaf '{leaf_id}': \"{outcome}\"",
                "comments": [case.get("context_description") or ""],
                "priority": PRIORITY_HIGH,
                "source":   "case_library",
                "case_id":  case.get("case_id", ""),
            })

    return signals


# =========================================================
# Proposal builder
# =========================================================

_PROPOSAL_TEMPLATES = {
    "high_dislike": (
        "Revisar y reescribir la sección '{topic}' del módulo {module}. "
        "El {detail} indica que el contenido actual no está siendo útil para los consultores."
    ),
    "negative_comment": (
        "Mejorar la sección '{topic}' del módulo {module} considerando el feedback recibido: {comments_str}."
    ),
    "missing_tree_gap": (
        "Agregar contenido en módulo {module} que oriente sobre '{topic}'. "
        "{detail}. Mientras no exista un árbol dedicado, una sección conceptual "
        "con criterios de decisión reduciría el gap."
    ),
    "lesson_to_add": (
        "Incorporar en módulo {module} (árbol '{topic}', leaf '{leaf_id}') "
        "la siguiente lección de campo: {comments_str}."
    ),
    "uncharted_gap": (
        "Agregar contenido en módulo {module} que cubra el escenario: {detail}. "
        "Este path no está contemplado en el árbol '{topic}'."
    ),
    "bad_outcome_leaf": (
        "Revisar la recomendación del leaf '{leaf_id}' en árbol '{topic}' del módulo {module}. "
        "Outcome registrado: {detail}. Considerar agregar advertencias o condiciones de uso."
    ),
}

_ACTION_LABELS = {
    "high_dislike":     "reescribir_seccion",
    "negative_comment": "mejorar_seccion",
    "missing_tree_gap": "agregar_seccion",
    "lesson_to_add":    "incorporar_leccion",
    "uncharted_gap":    "documentar_escenario",
    "bad_outcome_leaf": "revisar_recomendacion",
}


def build_proposals(signals: list[dict], min_signals: int = 1) -> list[dict]:
    """
    Agrupa señales por módulo+topic y genera una propuesta por grupo.
    Señales múltiples sobre el mismo topic se consolidan.
    """
    # Agrupar por modulo + tipo de señal + topic
    grouped: dict[str, list[dict]] = defaultdict(list)
    for s in signals:
        key = f"{s['module']}::{s['type']}::{s['topic'][:50]}"
        grouped[key].append(s)

    proposals = []
    for key, group in grouped.items():
        if len(group) < min_signals:
            continue
        base = group[0]
        all_comments = []
        for g in group:
            all_comments.extend(g.get("comments", []))
        all_comments = [c for c in all_comments if c]

        comments_str = "; ".join(all_comments[:3]) if all_comments else "(sin comentarios)"
        tmpl = _PROPOSAL_TEMPLATES.get(base["type"], "{detail}")
        proposal_text = tmpl.format(
            topic        = base.get("topic", "?"),
            module       = base.get("module", "?"),
            detail       = base.get("detail", ""),
            leaf_id      = base.get("leaf_id", "?"),
            comments_str = comments_str,
        )

        proposals.append({
            "module":       base["module"],
            "topic":        base["topic"],
            "action":       _ACTION_LABELS.get(base["type"], "revisar"),
            "signal_type":  base["type"],
            "priority":     base["priority"],
            "proposal":     proposal_text,
            "comments":     all_comments[:5],
            "source":       list({g["source"] for g in group}),
            "signal_count": len(group),
            "generated_at": datetime.now().isoformat(),
        })

    # Ordenar: alta primero, luego media, luego baja
    priority_order = {PRIORITY_HIGH: 0, PRIORITY_MEDIUM: 1, PRIORITY_LOW: 2}
    proposals.sort(key=lambda p: (priority_order.get(p["priority"], 9), p["module"]))
    return proposals


# =========================================================
# LLM enrichment
# =========================================================

def enrich_with_llm(proposals: list[dict], model: str = MODEL) -> list[dict]:
    """
    Para cada propuesta de prioridad alta o media, pide al LLM que redacte
    el texto concreto a agregar/modificar en el modulo.
    """
    try:
        from openai import OpenAI
    except ImportError:
        print("[ERROR] openai no instalado.")
        return proposals

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("[ERROR] OPENAI_API_KEY no encontrada en el entorno.")
        return proposals

    client  = OpenAI(api_key=api_key)
    to_enrich = [p for p in proposals if p["priority"] in (PRIORITY_HIGH, PRIORITY_MEDIUM)]
    print(f"[LLM] Enriqueciendo {len(to_enrich)} propuestas con {model}...")

    for i, prop in enumerate(to_enrich, 1):
        comments_block = "\n".join(f"  - {c}" for c in prop.get("comments", []) if c)
        prompt = f"""Sos un consultor senior de SAP Datasphere revisando el contenido metodológico de un módulo de capacitación interna.

Se detectó la siguiente necesidad de mejora de contenido:

Módulo: {prop['module']}
Tema/Sección: {prop['topic']}
Tipo de ajuste necesario: {prop['action']}
Señal detectada: {prop['proposal']}
Feedback/contexto adicional:
{comments_block if comments_block else '  (sin comentarios adicionales)'}

Tu tarea: redactá el texto concreto (1 a 3 párrafos en español) que se debería agregar o reemplazar en el módulo para resolver esta necesidad. Sé específico, técnico y orientado a la práctica de consultoría SAP. No incluyas introducción ni cierre — solo el contenido listo para pegar en el documento."""

        try:
            print(f"  [{i}/{len(to_enrich)}] {prop['topic'][:50]}...")
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600,
                temperature=0.4,
            )
            draft = response.choices[0].message.content.strip()
            prop["draft_text"] = draft
        except Exception as ex:
            print(f"  [!] Error en propuesta {i}: {ex}")
            prop["draft_text"] = None

    return proposals


# =========================================================
# Summary
# =========================================================

def build_summary(proposals: list[dict]) -> str:
    lines = [
        "=" * 60,
        "PROPUESTAS DE MEJORA DE CONTENIDO",
        f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "=" * 60,
        "",
        f"Total propuestas: {len(proposals)}",
        f"  Alta prioridad:  {sum(1 for p in proposals if p['priority'] == PRIORITY_HIGH)}",
        f"  Media prioridad: {sum(1 for p in proposals if p['priority'] == PRIORITY_MEDIUM)}",
        f"  Baja prioridad:  {sum(1 for p in proposals if p['priority'] == PRIORITY_LOW)}",
        "",
    ]

    current_module = None
    for p in proposals:
        if p["module"] != current_module:
            current_module = p["module"]
            lines.append(f"-- MODULO {current_module} --")
            lines.append("")

        priority_tag = {"alta": "[!]", "media": "[~]", "baja": "[ ]"}.get(p["priority"], "[ ]")
        lines.append(f"  {priority_tag} [{p['action']}] {p['topic']}")
        lines.append(f"      {p['proposal']}")

        if p.get("comments"):
            lines.append(f"      Señales: {'; '.join(p['comments'][:2])}")

        if p.get("draft_text"):
            lines.append("")
            lines.append("      -- BORRADOR PROPUESTO (LLM) --")
            for draft_line in p["draft_text"].split("\n")[:6]:
                lines.append(f"      {draft_line}")
            lines.append("      [...]")

        lines.append("")

    lines += ["=" * 60, ""]
    return "\n".join(lines)


# =========================================================
# Main
# =========================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Propuestas de mejora de contenido")
    parser.add_argument("--llm",         action="store_true", help="Enriquecer propuestas con LLM")
    parser.add_argument("--module",      default=None,        help="Filtrar por modulo (ej: 03)")
    parser.add_argument("--min-signals", type=int, default=1, help="Minimo de señales para generar propuesta")
    parser.add_argument("--model",       default=MODEL)
    parser.add_argument("--feedback-dir", default=str(FEEDBACK_DIR))
    parser.add_argument("--case-dir",     default=str(CASE_DIR))
    parser.add_argument("--report-dir",   default=str(REPORTS_DIR))
    args = parser.parse_args()

    feedback_dir = Path(args.feedback_dir)
    case_dir     = Path(args.case_dir)
    report_dir   = Path(args.report_dir)

    print(f"\n[DIR] Cargando datos...")
    feedback = load_feedback(feedback_dir, args.module)
    cases    = load_cases(case_dir, args.module)
    print(f"   {len(feedback)} entradas de feedback, {len(cases)} casos")

    if not feedback and not cases:
        print("Sin datos para analizar.")
        sys.exit(0)

    print("\n[>>] Extrayendo señales...")
    signals  = signals_from_feedback(feedback) + signals_from_cases(cases)
    print(f"   {len(signals)} señales detectadas")

    if not signals:
        print("Sin señales de mejora detectadas.")
        sys.exit(0)

    print("\n[>>] Generando propuestas...")
    proposals = build_proposals(signals, min_signals=args.min_signals)
    print(f"   {len(proposals)} propuestas generadas")

    if args.llm:
        print("\n[LLM] Enriqueciendo con borradores...")
        proposals = enrich_with_llm(proposals, model=args.model)

    print("\n[OK] Escribiendo reportes...")
    write_report(report_dir, "proposals.yaml", proposals)

    summary = build_summary(proposals)
    write_report(report_dir, "summary.txt", summary)

    print("\n" + summary)


if __name__ == "__main__":
    main()