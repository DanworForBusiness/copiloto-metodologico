"""
propose_tree_updates.py
=======================
Lee los reportes de analyze_cases.py y propone cambios concretos
a los YAMLs de los árboles.

Uso:
    python propose_tree_updates.py                  # lee reports/ y trees/ por defecto
    python propose_tree_updates.py --apply          # escribe propuestas en trees/proposed/

Salida:
    reports/proposed_updates.yaml   → propuestas estructuradas
    trees/proposed/<tree_id>.yaml   → árbol modificado (solo con --apply)
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml


BASE_DIR     = Path(__file__).parent
REPORTS_DIR  = BASE_DIR / "reports"
# Fuente canónica: los árboles viven en el content pack (las carpetas raíz se retiraron).
TREES_DIR    = BASE_DIR / "content_packs" / "datasphere_core" / "trees"


# =========================================================
# Loaders
# =========================================================

def load_report(reports_dir: Path, filename: str) -> dict:
    p = reports_dir / filename
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def load_tree(trees_dir: Path, tree_id: str) -> dict | None:
    for p in trees_dir.glob("*.yaml"):
        try:
            t = yaml.safe_load(p.read_text(encoding="utf-8"))
            if isinstance(t, dict) and t.get("id") == tree_id:
                t["_path"] = str(p)
                t["_filename"] = p.name
                return t
        except Exception:
            continue
    return None


# =========================================================
# Generador de propuestas
# =========================================================

def propose_from_gap_nodes(gap_report: dict) -> list[dict]:
    """
    Por cada nodo con casos uncharted, propone agregar una rama
    o convertir el nodo en punto de captura explícito.
    """
    proposals = []
    for node in gap_report.get("nodes", []):
        priority = node.get("priority", "baja")
        node_id  = node["node_id"]
        trees    = node.get("trees_affected", [])
        examples = node.get("context_examples", [])
        outcomes = node.get("outcomes", {})

        # Si hay casos que "anduvieron", hay decisión documentada → candidato a rama nueva
        has_success = outcomes.get("anduvo", 0) > 0

        proposals.append({
            "type":        "new_branch",
            "priority":    priority,
            "tree_ids":    trees,
            "target_node": node_id,
            "rationale":   (
                f"{node['uncharted_cases']} caso(s) no contemplado(s) en nodo '{node_id}'. "
                + (f"Contextos: {'; '.join(examples[:2])}." if examples else "")
            ),
            "suggested_action": (
                "Agregar opción 'Otro / No aplica' con rama específica"
                if has_success else
                "Agregar nodo de captura con post_action: needs_human_review"
            ),
            "new_node_template": _new_node_template(node_id, examples, has_success),
            "reviewed": False,
        })

    return proposals


def propose_from_leaf_outcomes(leaf_report: dict) -> list[dict]:
    """
    Por cada leaf con success_rate bajo, propone revisión de la decisión.
    """
    proposals = []
    for tree in leaf_report.get("trees", []):
        for leaf in tree.get("leaves", []):
            if leaf.get("flag") == "revisar":
                proposals.append({
                    "type":        "review_leaf",
                    "priority":    "alta",
                    "tree_ids":    [tree["tree_id"]],
                    "target_leaf": leaf["leaf_id"],
                    "rationale":   (
                        f"Leaf '{leaf['leaf_id']}' tiene success_rate "
                        f"{int((leaf['success_rate'] or 0)*100)}% "
                        f"({leaf['outcomes']})."
                    ),
                    "suggested_action": (
                        "Revisar la decisión del leaf. Considerar agregar "
                        "post_action: needs_human_review o split en sub-árbol."
                    ),
                    "reviewed": False,
                })
    return proposals


def propose_from_lessons(lessons_report: dict) -> list[dict]:
    """
    Por cada lección registrada, propone dónde incorporarla como nota en el árbol.
    """
    proposals = []
    for tree_id, lessons in (lessons_report.get("by_tree") or {}).items():
        if not lessons:
            continue
        lesson_texts = [l["lesson"] for l in lessons if l.get("lesson")]
        proposals.append({
            "type":        "add_notes",
            "priority":    "baja",
            "tree_ids":    [tree_id],
            "target_node": "leaves (general)",
            "rationale":   f"{len(lesson_texts)} lección(es) registrada(s) por consultores.",
            "suggested_action": "Incorporar como 'notes' en los leaves correspondientes.",
            "lessons_to_add": lesson_texts[:10],
            "reviewed": False,
        })
    return proposals


def propose_from_llm_clusters(llm_report: dict) -> list[dict]:
    """
    Convierte los clusters del LLM en propuestas estructuradas.
    """
    proposals = []
    for cluster in llm_report.get("clusters", []):
        rec = cluster.get("tree_recommendation", "")
        if not rec:
            continue
        proposals.append({
            "type":        "llm_pattern",
            "priority":    cluster.get("confidence", "baja"),
            "tree_ids":    [],  # el LLM no siempre especifica árbol
            "rationale":   cluster.get("pattern", ""),
            "suggested_action": rec,
            "cluster_label":    cluster.get("label", ""),
            "cases_count":      cluster.get("cases_count", 0),
            "reviewed": False,
        })
    return proposals


def _new_node_template(gap_node: str, examples: list[str], has_success: bool) -> dict:
    """Genera un template de nodo YAML para insertar en el árbol."""
    node_id = f"{gap_node}_uncharted_branch"
    if has_success:
        return {
            node_id: {
                "type":     "single_choice",
                "question": "¿Podés describir mejor tu contexto?",
                "options":  ["[completar según casos reales]", "No aplica"],
                "next": {
                    "[completar según casos reales]": "leaf_[completar]",
                    "No aplica": "leaf_needs_review",
                },
                "_comment": "BORRADOR — completar con contextos reales de case_library/",
            }
        }
    else:
        return {
            node_id: {
                "type":     "single_choice",
                "question": "Este caso requiere intervención.",
                "options":  ["Escalar a equipo técnico"],
                "next":     {"Escalar a equipo técnico": "leaf_needs_review"},
                "_comment": "BORRADOR — revisar si se puede estructurar mejor",
            }
        }


# =========================================================
# Aplicar propuestas al árbol (--apply)
# =========================================================

def apply_proposals_to_tree(tree: dict, proposals: list[dict]) -> dict:
    """
    Aplica las propuestas de tipo new_branch al árbol.
    Devuelve el árbol modificado (no escribe a disco aún).
    Solo aplica propuestas con type=new_branch para no romper el árbol.
    """
    import copy
    modified = copy.deepcopy(tree)

    for p in proposals:
        if p.get("type") != "new_branch":
            continue
        if p.get("reviewed"):
            continue

        target_node_id = p.get("target_node")
        template       = p.get("new_node_template", {})

        if not template or target_node_id not in modified.get("nodes", {}):
            continue

        # Agregar los nuevos nodos al árbol
        for new_node_id, new_node in template.items():
            if new_node_id not in modified["nodes"]:
                modified["nodes"][new_node_id] = new_node
                print(f"  + Nodo '{new_node_id}' agregado al árbol '{modified.get('id')}'")

    modified["_proposed_at"] = datetime.now().isoformat()
    return modified


# =========================================================
# Main
# =========================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Generador de propuestas de actualización de árboles")
    parser.add_argument("--apply",      action="store_true", help="Escribir árboles propuestos en trees/proposed/")
    parser.add_argument("--report-dir", default=str(REPORTS_DIR))
    parser.add_argument("--trees-dir",  default=str(TREES_DIR))
    args = parser.parse_args()

    report_dir = Path(args.report_dir)
    trees_dir  = Path(args.trees_dir)

    print("\n📂 Cargando reportes...")
    gap_report     = load_report(report_dir, "gap_nodes.yaml")
    leaf_report    = load_report(report_dir, "leaf_outcomes.yaml")
    lessons_report = load_report(report_dir, "lessons.yaml")
    llm_report     = load_report(report_dir, "text_clusters.yaml")

    if not any([gap_report, leaf_report, lessons_report]):
        print("[ERROR] No se encontraron reportes. Corré analyze_cases.py primero.")
        sys.exit(1)

    print("🔍 Generando propuestas...")
    proposals = []
    proposals += propose_from_gap_nodes(gap_report)
    proposals += propose_from_leaf_outcomes(leaf_report)
    proposals += propose_from_lessons(lessons_report)
    if llm_report:
        proposals += propose_from_llm_clusters(llm_report)

    print(f"   {len(proposals)} propuesta(s) generada(s).")

    # Escribir propuestas
    report_dir.mkdir(parents=True, exist_ok=True)
    dest = report_dir / "proposed_updates.yaml"
    dest.write_text(
        yaml.dump(
            {"generated_at": datetime.now().isoformat(), "proposals": proposals},
            allow_unicode=True, sort_keys=False, default_flow_style=False,
        ),
        encoding="utf-8",
    )
    print(f"  ✓ {dest.relative_to(dest.parent.parent)}")

    # Mostrar resumen de propuestas
    print("\n── PROPUESTAS ──\n")
    for i, p in enumerate(proposals, 1):
        print(f"  [{i}] [{p['priority'].upper()}] {p['type']} → {', '.join(p['tree_ids'])}")
        print(f"      {p['rationale'][:100]}")
        print(f"      → {p['suggested_action'][:100]}")
        print()

    # Aplicar al árbol si se pidió
    if args.apply:
        print("✏️  Aplicando propuestas a los árboles...")
        proposed_dir = trees_dir / "proposed"
        proposed_dir.mkdir(parents=True, exist_ok=True)

        trees_to_update: dict[str, list[dict]] = defaultdict(list)
        for p in proposals:
            for tree_id in p.get("tree_ids", []):
                trees_to_update[tree_id].append(p)

        for tree_id, tree_proposals in trees_to_update.items():
            tree = load_tree(trees_dir, tree_id)
            if not tree:
                print(f"  [WARN] Árbol '{tree_id}' no encontrado en {trees_dir}")
                continue
            modified = apply_proposals_to_tree(tree, tree_proposals)
            out_path = proposed_dir / (tree.get("_filename") or f"{tree_id}.yaml")
            # Limpiar campos internos antes de escribir
            for k in ["_path", "_filename", "_proposed_at"]:
                modified.pop(k, None)
            out_path.write_text(
                yaml.dump(modified, allow_unicode=True, sort_keys=False, default_flow_style=False),
                encoding="utf-8",
            )
            print(f"  ✓ trees/proposed/{out_path.name}")


if __name__ == "__main__":
    main()