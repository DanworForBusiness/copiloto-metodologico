"""
analyze_cases.py
================
Analiza la case_library/ y genera reportes en reports/.

Uso:
    python analyze_cases.py                   # solo Nivel 1 (sin LLM)
    python analyze_cases.py --llm             # Nivel 1 + Nivel 2 (requiere OPENAI_API_KEY)
    python analyze_cases.py --min-cases 2     # umbral mínimo para reportar patrones

Salida:
    reports/gap_nodes.yaml       → nodos con más casos uncharted
    reports/route_patterns.yaml  → rutas frecuentes y su outcome
    reports/leaf_outcomes.yaml   → resultados por árbol/leaf
    reports/text_clusters.yaml   → patrones de texto libre (solo con --llm)
    reports/summary.txt          → resumen legible para copiar/pegar
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime

import yaml


# =========================================================
# Config
# =========================================================

BASE_DIR      = Path(__file__).parent
CASE_DIR      = BASE_DIR / "case_library"
REPORTS_DIR   = BASE_DIR / "reports"
MIN_CASES_DEFAULT = 2


# =========================================================
# Loader
# =========================================================

def load_cases(case_dir: Path) -> list[dict]:
    cases = []
    if not case_dir.exists():
        print(f"[WARN] No existe {case_dir} — no hay casos para analizar.")
        return cases
    for p in sorted(case_dir.glob("*.yaml")):
        try:
            c = yaml.safe_load(p.read_text(encoding="utf-8"))
            if isinstance(c, dict) and c.get("case_id"):
                c["_file"] = p.name
                cases.append(c)
        except Exception as e:
            print(f"[WARN] Error leyendo {p.name}: {e}")
    return cases


# =========================================================
# Nivel 1 — análisis estructurado (sin LLM)
# =========================================================

def analyze_gap_nodes(cases: list[dict], min_cases: int) -> dict:
    """
    ¿Qué nodos generan más casos uncharted?
    Señal directa de dónde el árbol está incompleto.
    """
    uncharted = [c for c in cases if c.get("uncharted")]

    gap_counter: Counter = Counter()
    gap_outcomes: dict[str, Counter] = defaultdict(Counter)
    gap_trees: dict[str, set] = defaultdict(set)
    gap_examples: dict[str, list] = defaultdict(list)

    for c in uncharted:
        node  = c.get("gap_node") or "unknown"
        tree  = (c.get("source") or {}).get("tree_id", "unknown")
        out   = c.get("outcome", "desconocido")
        ctx   = c.get("context_description", "")

        gap_counter[node] += 1
        gap_outcomes[node][out] += 1
        gap_trees[node].add(tree)
        if ctx and len(gap_examples[node]) < 3:
            gap_examples[node].append(ctx[:120])

    report = {
        "generated_at": datetime.now().isoformat(),
        "total_uncharted": len(uncharted),
        "nodes": [],
    }

    for node, count in gap_counter.most_common():
        if count < min_cases:
            continue
        report["nodes"].append({
            "node_id":        node,
            "uncharted_cases": count,
            "outcomes":       dict(gap_outcomes[node]),
            "trees_affected": sorted(gap_trees[node]),
            "context_examples": gap_examples[node],
            "priority":       "alta" if count >= 5 else "media" if count >= 3 else "baja",
        })

    return report


def analyze_route_patterns(cases: list[dict], min_cases: int) -> dict:
    """
    ¿Qué rutas se repiten? ¿Con qué outcome?
    Detecta caminos frecuentes → candidatos a simplificar el árbol.
    """
    completed = [c for c in cases if not c.get("uncharted") and c.get("route")]

    # Firma de ruta: tupla de "node→answer"
    route_counter: Counter = Counter()
    route_outcomes: dict[str, Counter] = defaultdict(Counter)
    route_leaf: dict[str, Counter] = defaultdict(Counter)
    route_trees: dict[str, set] = defaultdict(set)

    for c in completed:
        route = c.get("route") or []
        sig   = " → ".join(f"{r['node']}:{r['answer']}" for r in route if isinstance(r, dict))
        if not sig:
            continue

        tree    = (c.get("source") or {}).get("tree_id", "unknown")
        outcome = c.get("outcome", "desconocido")
        leaf    = c.get("leaf_id") or "unknown"

        route_counter[sig] += 1
        route_outcomes[sig][outcome] += 1
        route_leaf[sig][leaf] += 1
        route_trees[sig].add(tree)

    report = {
        "generated_at": datetime.now().isoformat(),
        "total_completed": len(completed),
        "patterns": [],
    }

    for sig, count in route_counter.most_common():
        if count < min_cases:
            continue
        outcomes = dict(route_outcomes[sig])
        leaves   = dict(route_leaf[sig])
        # Confianza: % de outcome "anduvo" sobre total con outcome definido
        total_with_outcome = sum(v for k, v in outcomes.items() if k != "en curso")
        anduvo  = outcomes.get("anduvo", 0)
        confidence = round(anduvo / total_with_outcome, 2) if total_with_outcome else None

        report["patterns"].append({
            "route_signature": sig,
            "occurrences":     count,
            "outcomes":        outcomes,
            "leaf_reached":    leaves,
            "trees":           sorted(route_trees[sig]),
            "confidence":      confidence,
        })

    return report


def analyze_leaf_outcomes(cases: list[dict], min_cases: int) -> dict:
    """
    Por árbol y por leaf: ¿qué tan bien están funcionando las decisiones?
    """
    completed = [c for c in cases if not c.get("uncharted")]

    tree_leaf_outcomes: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))

    for c in completed:
        tree = (c.get("source") or {}).get("tree_id", "unknown")
        leaf = c.get("leaf_id") or "unknown"
        out  = c.get("outcome", "desconocido")
        tree_leaf_outcomes[tree][leaf][out] += 1

    report = {
        "generated_at": datetime.now().isoformat(),
        "trees": [],
    }

    for tree_id, leaves in sorted(tree_leaf_outcomes.items()):
        tree_entry = {"tree_id": tree_id, "leaves": []}
        for leaf_id, outcomes in sorted(leaves.items()):
            total = sum(outcomes.values())
            if total < min_cases:
                continue
            anduvo = outcomes.get("anduvo", 0)
            no_anduvo = outcomes.get("no anduvo", 0)
            success_rate = round(anduvo / total, 2) if total else None
            tree_entry["leaves"].append({
                "leaf_id":      leaf_id,
                "total_cases":  total,
                "outcomes":     dict(outcomes),
                "success_rate": success_rate,
                "flag":         "revisar" if (no_anduvo / total) >= 0.3 else "ok",
            })
        if tree_entry["leaves"]:
            report["trees"].append(tree_entry)

    return report


def analyze_lessons(cases: list[dict]) -> dict:
    """
    Extrae todas las lecciones registradas, agrupadas por árbol.
    Input directo para el prompt de Nivel 2.
    """
    lessons_by_tree: dict[str, list] = defaultdict(list)

    for c in cases:
        tree    = (c.get("source") or {}).get("tree_id", "unknown")
        lessons = c.get("lessons") or []
        leaf_notes = c.get("leaf_notes") or []
        ctx     = c.get("context_description", "")
        dec     = c.get("decision_taken", "")

        for l in lessons:
            if l:
                lessons_by_tree[tree].append({
                    "lesson":   l,
                    "context":  ctx[:100] if ctx else None,
                    "outcome":  c.get("outcome"),
                    "uncharted": c.get("uncharted", False),
                })
        for n in leaf_notes:
            if n:
                lessons_by_tree[tree].append({
                    "lesson":   n,
                    "context":  ctx[:100] if ctx else None,
                    "outcome":  c.get("outcome"),
                    "uncharted": False,
                    "source":   "leaf_note",
                })

    return {
        "generated_at": datetime.now().isoformat(),
        "total_lessons": sum(len(v) for v in lessons_by_tree.values()),
        "by_tree": {k: v for k, v in sorted(lessons_by_tree.items())},
    }


# =========================================================
# Nivel 2 — análisis de texto libre con LLM (opcional)
# =========================================================

def analyze_text_clusters_llm(cases: list[dict], model: str = "gpt-4o-mini") -> dict:
    """
    Usa el LLM para agrupar context_description y detectar patrones
    que los campos estructurados no pueden capturar.
    Requiere OPENAI_API_KEY en el entorno.
    """
    try:
        from openai import OpenAI
    except ImportError:
        print("[ERROR] openai no instalado. Corré: pip install openai")
        return {}

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("[ERROR] OPENAI_API_KEY no encontrada en el entorno.")
        return {}

    client = OpenAI(api_key=api_key)

    # Preparar input compacto para el LLM
    entries = []
    for c in cases:
        ctx = (c.get("context_description") or "").strip()
        dec = (c.get("decision_taken") or "").strip()
        out = c.get("outcome", "")
        les = "; ".join(c.get("lessons") or [])
        tree = (c.get("source") or {}).get("tree_id", "")
        unc  = c.get("uncharted", False)

        if ctx:
            entries.append({
                "tree":      tree,
                "context":   ctx[:200],
                "decision":  dec[:200] if dec else None,
                "outcome":   out,
                "lessons":   les[:200] if les else None,
                "uncharted": unc,
            })

    if not entries:
        return {"note": "No hay entradas con context_description para analizar."}

    prompt = f"""Analizá los siguientes {len(entries)} casos de decisiones técnicas SAP.
Cada caso tiene: árbol de decisión usado, contexto técnico, decisión tomada, outcome y lecciones.

CASOS:
{json.dumps(entries, ensure_ascii=False, indent=2)}

Detectá y devolvé ÚNICAMENTE un JSON con esta estructura (sin texto adicional, sin markdown):
{{
  "clusters": [
    {{
      "id": "cluster_001",
      "label": "nombre corto del patrón",
      "description": "qué tienen en común estos casos",
      "cases_count": N,
      "context_keywords": ["keyword1", "keyword2"],
      "dominant_outcome": "anduvo|no anduvo|inconcluso",
      "pattern": "cuando [contexto], se decidió [decisión] con resultado [outcome]",
      "tree_recommendation": "qué rama o nodo agregar/modificar en el árbol",
      "confidence": "alta|media|baja"
    }}
  ],
  "global_insights": [
    "insight 1 sobre el conjunto de casos",
    "insight 2"
  ],
  "gaps_detected": [
    "situación técnica recurrente que el árbol no contempla"
  ]
}}

Criterios:
- Un cluster debe tener al menos 2 casos similares
- Priorizá clusters con outcome "no anduvo" o "inconcluso" 
- tree_recommendation debe ser concreta y accionable
- Respondé SOLO con el JSON, sin explicaciones"""

    print(f"[LLM] Enviando {len(entries)} casos al modelo {model}...")
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=2000,
    )

    raw = response.choices[0].message.content or ""

    # Limpiar posibles backticks
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        result = json.loads(raw)
        result["generated_at"] = datetime.now().isoformat()
        result["cases_analyzed"] = len(entries)
        result["model"] = model
        return result
    except json.JSONDecodeError as e:
        print(f"[ERROR] No se pudo parsear respuesta LLM: {e}")
        return {"raw_response": raw, "error": str(e)}


# =========================================================
# Summary legible
# =========================================================

def build_summary(
    cases: list[dict],
    gap_report: dict,
    route_report: dict,
    leaf_report: dict,
    lessons_report: dict,
    llm_report: dict | None,
) -> str:
    lines = [
        "=" * 60,
        "REPORTE DE CASE LIBRARY — COPILOTO METODOLÓGICO DANWOR",
        f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "=" * 60,
        "",
        f"TOTAL DE CASOS: {len(cases)}",
        f"  ├─ Completados (leaf alcanzado): {sum(1 for c in cases if not c.get('uncharted'))}",
        f"  └─ No contemplados (uncharted):  {sum(1 for c in cases if c.get('uncharted'))}",
        "",
    ]

    # Gap nodes
    gap_nodes = gap_report.get("nodes", [])
    if gap_nodes:
        lines += [
            "-- NODOS CON CASOS NO CONTEMPLADOS --",
            "",
        ]
        for n in gap_nodes:
            lines.append(
                f"  [{n['priority'].upper()}] {n['node_id']} "
                f"— {n['uncharted_cases']} casos "
                f"| árboles: {', '.join(n['trees_affected'])}"
            )
            for ex in n.get("context_examples", []):
                lines.append(f"      Ej: \"{ex}\"")
        lines.append("")

    # Route patterns
    patterns = route_report.get("patterns", [])
    if patterns:
        lines += ["-- RUTAS MÁS FRECUENTES --", ""]
        for p in patterns[:5]:
            conf = f"{int(p['confidence']*100)}%" if p.get("confidence") is not None else "n/d"
            lines.append(
                f"  {p['occurrences']}x | confianza: {conf} | {p['route_signature'][:80]}"
            )
        lines.append("")

    # Leaf outcomes con flag
    for tree in leaf_report.get("trees", []):
        flagged = [l for l in tree["leaves"] if l["flag"] == "revisar"]
        if flagged:
            lines += [f"-- DECISIONES A REVISAR en {tree['tree_id']} --", ""]
            for l in flagged:
                lines.append(
                    f"  {l['leaf_id']} — {l['outcomes']} "
                    f"(success rate: {int((l['success_rate'] or 0)*100)}%)"
                )
            lines.append("")

    # Lecciones
    total_lessons = lessons_report.get("total_lessons", 0)
    if total_lessons:
        lines += [f"-- LECCIONES REGISTRADAS: {total_lessons} --", ""]
        for tree_id, lessons in (lessons_report.get("by_tree") or {}).items():
            lines.append(f"  {tree_id}:")
            for l in lessons[:3]:
                lines.append(f"    • {l['lesson']}")
            if len(lessons) > 3:
                lines.append(f"    ... y {len(lessons)-3} más")
        lines.append("")

    # LLM insights
    if llm_report and llm_report.get("clusters"):
        lines += ["-- PATRONES DETECTADOS POR IA --", ""]
        for cl in llm_report["clusters"]:
            lines.append(f"  [{cl.get('confidence','?').upper()}] {cl['label']}")
            lines.append(f"    {cl.get('pattern','')}")
            if cl.get("tree_recommendation"):
                lines.append(f"    → Árbol: {cl['tree_recommendation']}")
        lines.append("")

        gaps = llm_report.get("gaps_detected", [])
        if gaps:
            lines += ["-- GAPS DETECTADOS POR IA --", ""]
            for g in gaps:
                lines.append(f"  • {g}")
            lines.append("")

    lines += ["=" * 60, ""]
    return "\n".join(lines)


# =========================================================
# Writer
# =========================================================

def write_report(reports_dir: Path, filename: str, data: dict | str) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    dest = reports_dir / filename
    if isinstance(data, str):
        dest.write_text(data, encoding="utf-8")
    else:
        dest.write_text(
            yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
    print(f"  [ok] {dest.relative_to(dest.parent.parent)}")


# =========================================================
# Main
# =========================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Analizador de case_library/")
    parser.add_argument("--llm",        action="store_true", help="Activar análisis LLM (Nivel 2)")
    parser.add_argument("--min-cases",  type=int, default=MIN_CASES_DEFAULT,
                        help=f"Mínimo de casos para reportar un patrón (default: {MIN_CASES_DEFAULT})")
    parser.add_argument("--model",      default="gpt-4o-mini", help="Modelo OpenAI a usar")
    parser.add_argument("--case-dir",   default=str(CASE_DIR),  help="Directorio de casos")
    parser.add_argument("--report-dir", default=str(REPORTS_DIR), help="Directorio de reportes")
    args = parser.parse_args()

    case_dir   = Path(args.case_dir)
    report_dir = Path(args.report_dir)
    min_cases  = args.min_cases

    print(f"\n[DIR] Cargando casos desde {case_dir}...")
    cases = load_cases(case_dir)
    if not cases:
        print("No hay casos para analizar. Completá algunos árboles primero.")
        sys.exit(0)
    print(f"   {len(cases)} casos cargados.\n")

    print("[>>] Nivel 1 — análisis estructurado...")
    gap_report    = analyze_gap_nodes(cases, min_cases)
    route_report  = analyze_route_patterns(cases, min_cases)
    leaf_report   = analyze_leaf_outcomes(cases, min_cases)
    lessons_report = analyze_lessons(cases)

    print("\n[OK] Escribiendo reportes...")
    write_report(report_dir, "gap_nodes.yaml",      gap_report)
    write_report(report_dir, "route_patterns.yaml", route_report)
    write_report(report_dir, "leaf_outcomes.yaml",  leaf_report)
    write_report(report_dir, "lessons.yaml",        lessons_report)

    llm_report = None
    if args.llm:
        print("\n[AI] Nivel 2 — análisis LLM...")
        llm_report = analyze_text_clusters_llm(cases, model=args.model)
        if llm_report:
            write_report(report_dir, "text_clusters.yaml", llm_report)

    print("\n[..] Generando resumen...")
    summary = build_summary(cases, gap_report, route_report, leaf_report, lessons_report, llm_report)
    write_report(report_dir, "summary.txt", summary)

    print("\n" + summary)


if __name__ == "__main__":
    main()