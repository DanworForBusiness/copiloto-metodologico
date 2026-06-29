"""
build_analysis_prompt.py
========================
Genera un prompt dinámico listo para copiar y usar en cualquier LLM
(ChatGPT, Claude, etc.) con el contenido actual de la case_library/.

Útil para análisis ad-hoc sin correr el pipeline completo.

Uso:
    python build_analysis_prompt.py                  # imprime el prompt
    python build_analysis_prompt.py --out prompt.txt # lo guarda en archivo
    python build_analysis_prompt.py --mode update    # foco en propuestas de árbol
    python build_analysis_prompt.py --mode insights  # foco en patrones y lecciones (default)
    python build_analysis_prompt.py --mode gaps      # foco en casos no contemplados
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml


BASE_DIR  = Path(__file__).parent
CASE_DIR  = BASE_DIR / "case_library"
# Fuente canónica: los árboles viven en el content pack (las carpetas raíz se retiraron).
TREES_DIR = BASE_DIR / "content_packs" / "datasphere_core" / "trees"


# =========================================================
# Loaders
# =========================================================

def load_cases(case_dir: Path) -> list[dict]:
    cases = []
    for p in sorted(case_dir.glob("*.yaml")):
        try:
            c = yaml.safe_load(p.read_text(encoding="utf-8"))
            if isinstance(c, dict) and c.get("case_id"):
                cases.append(c)
        except Exception:
            continue
    return cases


def load_tree_summaries(trees_dir: Path) -> list[dict]:
    summaries = []
    for p in sorted(trees_dir.glob("*.yaml")):
        try:
            t = yaml.safe_load(p.read_text(encoding="utf-8"))
            if not isinstance(t, dict) or not t.get("id"):
                continue
            # Solo metadata + lista de nodos/opciones, sin el detalle completo
            nodes_summary = {}
            for nid, node in (t.get("nodes") or {}).items():
                nodes_summary[nid] = {
                    "question": node.get("question", ""),
                    "options":  node.get("options", []),
                }
            summaries.append({
                "id":     t.get("id"),
                "title":  t.get("title", ""),
                "nodes":  nodes_summary,
                "leaves": list((t.get("leaves") or {}).keys()),
            })
        except Exception:
            continue
    return summaries


def serialize_cases_compact(cases: list[dict]) -> list[dict]:
    """Versión compacta de los casos para no exceder el contexto del LLM."""
    compact = []
    for c in cases:
        entry = {
            "id":        c.get("case_id", ""),
            "tree":      (c.get("source") or {}).get("tree_id", ""),
            "uncharted": c.get("uncharted", False),
            "gap_node":  c.get("gap_node"),
            "route":     [
                f"{r['node']}→{r['answer']}"
                for r in (c.get("route") or [])
                if isinstance(r, dict)
            ],
            "leaf":      c.get("leaf_id"),
            "decision":  c.get("decision"),
            "context":   (c.get("context_description") or "")[:200],
            "decision_taken": (c.get("decision_taken") or "")[:200],
            "outcome":   c.get("outcome"),
            "lessons":   c.get("lessons") or [],
            "leaf_notes": c.get("leaf_notes") or [],
        }
        # Limpiar campos nulos para reducir tokens
        compact.append({k: v for k, v in entry.items() if v not in [None, "", [], {}]})
    return compact


# =========================================================
# Builders de prompt por modo
# =========================================================

CONTEXT_BLOCK = """Soy consultor SAP en Danwor. Tengo una herramienta interna llamada "Copiloto Metodológico" que guía decisiones técnicas mediante árboles de decisión estructurados en YAML.

Cada árbol tiene nodos (preguntas) y leaves (decisiones). Los consultores navegan el árbol y al final se genera un caso YAML que queda registrado en una case_library/.

A continuación te comparto:
1. Los árboles disponibles (estructura resumida)
2. Los casos registrados hasta ahora

Tu tarea está al final."""


def build_prompt_insights(cases_compact: list[dict], trees: list[dict]) -> str:
    return f"""{CONTEXT_BLOCK}

═══ ÁRBOLES ═══
{json.dumps(trees, ensure_ascii=False, indent=2)}

═══ CASOS REGISTRADOS ({len(cases_compact)}) ═══
{json.dumps(cases_compact, ensure_ascii=False, indent=2)}

═══ TU TAREA ═══
Analizá los casos y devolvé:

1. **PATRONES DETECTADOS**
   - ¿Qué contextos técnicos aparecen frecuentemente?
   - ¿Hay decisiones que consistentemente "anduvieron" o "no anduvieron"?
   - ¿Qué lecciones se repiten o se complementan?

2. **CONOCIMIENTO EMERGENTE**
   - ¿Qué sabe ahora Danwor que no estaba explícito en los árboles al inicio?
   - ¿Qué reglas implícitas se pueden detectar?

3. **RECOMENDACIONES PARA CONSULTORES**
   - Basado en los casos, ¿qué deberían tener en cuenta antes de tomar decisiones similares?

Usá lenguaje técnico SAP. Sé concreto y accionable."""


def build_prompt_update(cases_compact: list[dict], trees: list[dict]) -> str:
    uncharted = [c for c in cases_compact if c.get("uncharted")]
    return f"""{CONTEXT_BLOCK}

═══ ÁRBOLES ═══
{json.dumps(trees, ensure_ascii=False, indent=2)}

═══ CASOS NO CONTEMPLADOS ({len(uncharted)} de {len(cases_compact)} total) ═══
{json.dumps(uncharted, ensure_ascii=False, indent=2)}

═══ TU TAREA ═══
Para cada caso no contemplado, proponé una modificación concreta al árbol YAML correspondiente.

Para cada propuesta incluí:
1. **árbol afectado** (tree_id)
2. **nodo donde el árbol quedó corto** (gap_node)
3. **nueva opción o rama** a agregar (con YAML de ejemplo)
4. **justificación** basada en el caso real

Formato de respuesta por propuesta:
---
**Árbol:** extraction_options
**Nodo:** q1
**Problema:** El caso "ECC sin soporte ODP" no tiene rama específica
**Propuesta YAML:**
```yaml
q1_ecc_legacy:
  type: single_choice
  question: "¿..."
  options: [...]
  next: {{...}}
```
**Justificación:** ...
---

Solo proponé cambios que estén respaldados por casos reales. No inventes ramas."""


def build_prompt_gaps(cases_compact: list[dict], trees: list[dict]) -> str:
    return f"""{CONTEXT_BLOCK}

═══ ÁRBOLES ═══
{json.dumps(trees, ensure_ascii=False, indent=2)}

═══ CASOS REGISTRADOS ({len(cases_compact)}) ═══
{json.dumps(cases_compact, ensure_ascii=False, indent=2)}

═══ TU TAREA ═══
Identificá gaps en los árboles: situaciones técnicas SAP que los consultores pueden encontrar
pero que los árboles actuales no contemplan o contemplan insuficientemente.

Para cada gap:
1. **Descripción del gap**: qué situación no está cubierta
2. **Evidencia**: qué casos o patrones lo sugieren (o si es inferencia tuya)
3. **Impacto**: qué puede pasar si un consultor llega a ese punto sin guía
4. **Prioridad**: alta / media / baja

Basate en los casos existentes pero también podés señalar gaps que inferís del dominio SAP
(marcalos como "inferido, sin evidencia en casos" para distinguirlos)."""


# =========================================================
# Main
# =========================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Generador de prompt dinámico para análisis de cases")
    parser.add_argument("--mode",     choices=["insights", "update", "gaps"], default="insights")
    parser.add_argument("--out",      default=None, help="Guardar en archivo en vez de imprimir")
    parser.add_argument("--case-dir", default=str(CASE_DIR))
    parser.add_argument("--trees-dir",default=str(TREES_DIR))
    args = parser.parse_args()

    case_dir  = Path(args.case_dir)
    trees_dir = Path(args.trees_dir)

    cases = load_cases(case_dir)
    if not cases:
        print("[WARN] No hay casos en case_library/. El prompt se generará sin datos de casos.")

    trees = load_tree_summaries(trees_dir)
    cases_compact = serialize_cases_compact(cases)

    builders = {
        "insights": build_prompt_insights,
        "update":   build_prompt_update,
        "gaps":     build_prompt_gaps,
    }
    prompt = builders[args.mode](cases_compact, trees)

    # Header con metadata
    header = (
        f"# Prompt generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"# Modo: {args.mode} | Casos: {len(cases)} | Árboles: {len(trees)}\n"
        f"# Tokens estimados: ~{len(prompt)//4}\n\n"
    )
    output = header + prompt

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(output, encoding="utf-8")
        print(f"✓ Prompt guardado en {out_path}")
        print(f"  Modo: {args.mode} | Casos: {len(cases)} | ~{len(prompt)//4} tokens")
    else:
        print(output)


if __name__ == "__main__":
    main()