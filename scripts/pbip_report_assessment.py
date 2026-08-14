"""
pbip_report_assessment.py — Parser pragmático de un proyecto PBIP (PBIR +
TMDL) para asesorar la consolidación del reporte "Danwor - Tablero
Integral" (ver HANDOFF, work/reporting-integral-v0).

NO es un compilador PBIR de propósito general — extrae solo lo que este
refactor necesita: inventario de páginas (visuales/campos/medidas/slicers/
filtros), inventario de fuente (tablas/medidas/relaciones/lineage de Power
Query M), y una similitud Jaccard simple entre páginas para detectar
duplicación funcional.

El proyecto PBIP fuente es READ-ONLY input — nunca se copia a git (ver
.gitignore de este repo). Este script lee el proyecto desde una ruta
arbitraria pasada por `--project-dir` (o detectada automáticamente si el
directorio contiene un único `*.pbip`).

Uso:
    python scripts/pbip_report_assessment.py --project-dir "E:\\Joe\\...\\Tablero Integral" --out artifacts/reporting-integral-v0
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# ============================================================================
# PBIP project discovery
# ============================================================================

def find_pbip_project(project_dir: Path) -> tuple[Path, Path, Path | None]:
    """Devuelve (report_dir, model_dir, pbip_path) — busca un único `*.pbip`
    en `project_dir` y sus siblings `*.Report`/`*.SemanticModel`."""
    pbip_files = list(project_dir.glob("*.pbip"))
    if not pbip_files:
        raise FileNotFoundError(f"No se encontró ningún *.pbip en {project_dir}")
    pbip_path = pbip_files[0]
    stem = pbip_path.stem
    report_dir = project_dir / f"{stem}.Report"
    model_dir = project_dir / f"{stem}.SemanticModel"
    if not report_dir.is_dir():
        raise FileNotFoundError(f"Falta el sibling de Report: {report_dir}")
    return report_dir, (model_dir if model_dir.is_dir() else None), pbip_path


# ============================================================================
# Report (PBIR) parsing — páginas / visuales / campos / medidas / slicers /
# filtros
# ============================================================================

def _walk_field_refs(node) -> list[tuple[str, str, str]]:
    """Recorre recursivamente cualquier subárbol JSON de PBIR y devuelve
    ternas (kind, entity, property) — kind es "Column" o "Measure",
    reconocido por la clave PADRE literal (Power BI ya distingue
    columna/medida en el propio JSON, no hace falta cruzar contra el
    modelo). Cubre `query.queryState` de visuales Y `filterConfig.filters`
    de página/reporte (misma forma de field)."""
    refs: list[tuple[str, str, str]] = []
    if isinstance(node, dict):
        for key in ("Column", "Measure", "HierarchyLevel"):
            val = node.get(key)
            if isinstance(val, dict):
                prop = val.get("Property")
                expr = val.get("Expression", {})
                entity = None
                if isinstance(expr, dict):
                    source_ref = expr.get("SourceRef", {})
                    entity = source_ref.get("Entity") if isinstance(source_ref, dict) else None
                    # HierarchyLevel envuelve otra Column/Hierarchy — buscar Entity un nivel más adentro.
                    if entity is None and "Hierarchy" in expr:
                        inner_ref = expr.get("Hierarchy", {}).get("Expression", {}).get("SourceRef", {})
                        entity = inner_ref.get("Entity") if isinstance(inner_ref, dict) else None
                if entity and prop:
                    kind = "Measure" if key == "Measure" else "Column"
                    refs.append((kind, entity, prop))
        for v in node.values():
            refs.extend(_walk_field_refs(v))
    elif isinstance(node, list):
        for item in node:
            refs.extend(_walk_field_refs(item))
    return refs


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_page(page_dir: Path) -> dict:
    page_json = _load_json(page_dir / "page.json")
    page_id = page_json.get("name", page_dir.name)
    display_name = page_json.get("displayName", page_id)

    # Filtros de página — mismo shape de field que los visuales.
    page_filter_refs = _walk_field_refs(page_json.get("filterConfig", {}))
    page_filters = sorted({f"{e}.{p}" for _, e, p in page_filter_refs})

    visuals_dir = page_dir / "visuals"
    visual_types: Counter[str] = Counter()
    fields: set[str] = set()
    measures: set[str] = set()
    slicer_fields: set[str] = set()
    visual_count = 0
    layout_positions: list[dict] = []

    if visuals_dir.is_dir():
        for visual_json_path in visuals_dir.glob("*/visual.json"):
            try:
                vdata = _load_json(visual_json_path)
            except (json.JSONDecodeError, OSError):
                continue
            visual = vdata.get("visual")
            if not isinstance(visual, dict):
                continue  # grupos/contenedores sin visualType propio — no son visuales hoja
            visual_count += 1
            vtype = visual.get("visualType", "unknown")
            visual_types[vtype] += 1

            position = vdata.get("position", {})
            layout_positions.append({
                "visual": vdata.get("name"), "type": vtype,
                "x": position.get("x"), "y": position.get("y"),
                "w": position.get("width"), "h": position.get("height"),
            })

            refs = _walk_field_refs(visual.get("query", {}))
            for kind, entity, prop in refs:
                key = f"{entity}.{prop}"
                if kind == "Measure":
                    measures.add(key)
                else:
                    fields.add(key)
                if vtype == "slicer":
                    slicer_fields.add(key)

    return {
        "page_id": page_id,
        "display_name": display_name,
        "visual_count": visual_count,
        "visual_types": dict(sorted(visual_types.items())),
        "fields": sorted(fields),
        "measures": sorted(measures),
        "slicer_fields": sorted(slicer_fields),
        "page_filters": page_filters,
        "layout": layout_positions,
    }


def parse_report(report_dir: Path) -> dict:
    pages_meta = _load_json(report_dir / "definition" / "pages" / "pages.json")
    page_order = pages_meta.get("pageOrder", [])
    active_page = pages_meta.get("activePageName")

    pages = {}
    pages_dir = report_dir / "definition" / "pages"
    for page_dir in pages_dir.iterdir():
        if not page_dir.is_dir():
            continue
        page_data = parse_page(page_dir)
        pages[page_data["page_id"]] = page_data

    ordered_pages = [pages[pid] for pid in page_order if pid in pages]
    # páginas presentes en disco pero no en pageOrder (no debería pasar, pero fail-soft)
    for pid, pdata in pages.items():
        if pid not in page_order:
            ordered_pages.append(pdata)

    bookmarks_dir = report_dir / "definition" / "bookmarks"
    bookmark_count = len(list(bookmarks_dir.glob("Bookmark*"))) if bookmarks_dir.is_dir() else 0

    return {
        "active_page": active_page,
        "page_order": page_order,
        "pages": ordered_pages,
        "bookmark_count": bookmark_count,
    }


# ============================================================================
# SemanticModel (TMDL) parsing — tablas / columnas / medidas / relaciones /
# partitions (Power Query M) / lineage de fuente
# ============================================================================

_TABLE_HEADER_RE = re.compile(r"^table\s+(?:'([^']+)'|(\S+))\s*$")
_COLUMN_RE = re.compile(r"^\tcolumn\s+(?:'([^']+)'|(\S+))")
_MEASURE_RE = re.compile(r"^\tmeasure\s+(?:'([^']+)'|(\S+))\s*=")
_HIDDEN_RE = re.compile(r"^\tisHidden\b")
_PARTITION_RE = re.compile(r"^\tpartition\s+(\S+)\s*=\s*(\w+)")
_RELATIONSHIP_RE = re.compile(r"^relationship\s+(\S+)")
_FROM_COL_RE = re.compile(r"^\tfromColumn:\s*(.+)$")
_TO_COL_RE = re.compile(r"^\ttoColumn:\s*(.+)$")

# Funciones M de origen de datos identificables — pragmático, no exhaustivo.
_SOURCE_FN_RE = re.compile(
    r"(SharePoint\.(?:Tables|Files|Contents)|Web\.Contents|Excel\.Workbook|"
    r"Sql\.Database|Sql\.Databases|Folder\.Files|Csv\.Document|Json\.Document|"
    r"OData\.Feed)\s*\(([^\n]*)"
)


def parse_table_tmdl(tmdl_path: Path) -> dict:
    text = tmdl_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    table_name = tmdl_path.stem
    is_hidden = False
    columns: list[str] = []
    measures: list[str] = []
    partitions: list[dict] = []

    current_partition_lines: list[str] | None = None
    current_partition_name = None

    for i, line in enumerate(lines):
        if i == 0:
            m = _TABLE_HEADER_RE.match(line)
            if m:
                table_name = m.group(1) or m.group(2)
            continue
        if _HIDDEN_RE.match(line):
            is_hidden = True
        m = _COLUMN_RE.match(line)
        if m:
            columns.append(m.group(1) or m.group(2))
            continue
        m = _MEASURE_RE.match(line)
        if m:
            measures.append(m.group(1) or m.group(2))
            continue
        m = _PARTITION_RE.match(line)
        if m:
            current_partition_name = m.group(1)
            current_partition_lines = []
            continue
        if current_partition_lines is not None:
            # una partition M termina cuando aparece una línea de nivel raíz
            # (sin indentación) que no es parte del bloque `source =` — pragmático:
            # se corta al llegar a otro `\tpartition`/EOF, ya cubierto por el loop.
            current_partition_lines.append(line)

    if current_partition_name is not None:
        m_text = "\n".join(current_partition_lines or [])
        src_match = _SOURCE_FN_RE.search(m_text)
        partitions.append({
            "name": current_partition_name,
            "source_kind": src_match.group(1) if src_match else None,
            "source_detail": (src_match.group(2)[:120].strip(' "') if src_match else None),
        })

    return {
        "table": table_name,
        "is_hidden": is_hidden,
        "column_count": len(columns),
        "columns": columns,
        "measure_count": len(measures),
        "measures": measures,
        "partitions": partitions,
    }


def parse_relationships(relationships_path: Path) -> list[dict]:
    if not relationships_path.exists():
        return []
    text = relationships_path.read_text(encoding="utf-8")
    rels = []
    current = None
    for line in text.splitlines():
        m = _RELATIONSHIP_RE.match(line)
        if m:
            if current:
                rels.append(current)
            current = {"id": m.group(1), "from": None, "to": None}
            continue
        if current is None:
            continue
        m = _FROM_COL_RE.match(line)
        if m:
            current["from"] = m.group(1).strip()
        m = _TO_COL_RE.match(line)
        if m:
            current["to"] = m.group(1).strip()
    if current:
        rels.append(current)
    return rels


def parse_semantic_model(model_dir: Path) -> dict:
    tables_dir = model_dir / "definition" / "tables"
    tables = [parse_table_tmdl(p) for p in sorted(tables_dir.glob("*.tmdl"))] if tables_dir.is_dir() else []
    relationships = parse_relationships(model_dir / "definition" / "relationships.tmdl")
    return {"tables": tables, "relationships": relationships}


# ============================================================================
# Similitud entre páginas (Jaccard sobre 4 señales)
# ============================================================================

def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return round(len(a & b) / len(union), 3)


def compute_similarity(pages: list[dict]) -> list[dict]:
    results = []
    for i in range(len(pages)):
        for j in range(i + 1, len(pages)):
            a, b = pages[i], pages[j]
            fa, fb = set(a["fields"]), set(b["fields"])
            ma, mb = set(a["measures"]), set(b["measures"])
            va = Counter(a["visual_types"])
            vb = Counter(b["visual_types"])
            va_set, vb_set = set(va), set(vb)
            sa, sb = set(a["slicer_fields"]), set(b["slicer_fields"])

            field_j = jaccard(fa, fb)
            measure_j = jaccard(ma, mb)
            visual_type_j = jaccard(va_set, vb_set)
            slicer_j = jaccard(sa, sb)
            combined = round((field_j + measure_j + visual_type_j + slicer_j) / 4, 3)

            results.append({
                "page_a": a["display_name"], "page_b": b["display_name"],
                "field_overlap": field_j, "measure_overlap": measure_j,
                "visual_type_overlap": visual_type_j, "slicer_overlap": slicer_j,
                "combined_score": combined,
                "shared_fields": sorted(fa & fb)[:15],
                "shared_measures": sorted(ma & mb)[:15],
            })
    results.sort(key=lambda r: r["combined_score"], reverse=True)
    return results


# ============================================================================
# Main
# ============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, help="Directorio que contiene el *.pbip y sus siblings .Report/.SemanticModel")
    parser.add_argument("--out", required=True, help="Directorio de salida para los artifacts JSON/MD")
    args = parser.parse_args()

    project_dir = Path(args.project_dir)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    report_dir, model_dir, pbip_path = find_pbip_project(project_dir)
    report_data = parse_report(report_dir)
    model_data = parse_semantic_model(model_dir) if model_dir else {"tables": [], "relationships": []}

    # --- report_manifest.json ---
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pbip_name": pbip_path.stem if pbip_path else None,
        "source_path": str(project_dir),
        "page_count": len(report_data["pages"]),
        "active_page": report_data["active_page"],
        "bookmark_count": report_data["bookmark_count"],
        "pages": [{"page_id": p["page_id"], "display_name": p["display_name"], "visual_count": p["visual_count"]} for p in report_data["pages"]],
        "model_table_count": len(model_data["tables"]),
        "model_relationship_count": len(model_data["relationships"]),
    }
    (out_dir / "report_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- page_inventory.json ---
    (out_dir / "page_inventory.json").write_text(
        json.dumps(report_data["pages"], indent=2, ensure_ascii=False), encoding="utf-8",
    )

    # --- page_similarity.json ---
    similarity = compute_similarity(report_data["pages"])
    (out_dir / "page_similarity.json").write_text(
        json.dumps(similarity, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    # --- source_inventory.json ---
    # referenced_by: qué páginas usan campos de esta tabla (Entity == table name)
    referenced_by: dict[str, set[str]] = {}
    for p in report_data["pages"]:
        entities = {f.split(".", 1)[0] for f in (p["fields"] + p["measures"] + p["slicer_fields"])}
        for e in entities:
            referenced_by.setdefault(e, set()).add(p["display_name"])

    source_inventory = []
    for t in model_data["tables"]:
        source_inventory.append({
            "table": t["table"],
            "is_hidden": t["is_hidden"],
            "column_count": t["column_count"],
            "measure_count": t["measure_count"],
            "measures": t["measures"],
            "partitions": t["partitions"],
            "referenced_by_pages": sorted(referenced_by.get(t["table"], [])),
        })
    (out_dir / "source_inventory.json").write_text(
        json.dumps(source_inventory, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    print(f"OK — {len(report_data['pages'])} páginas, {len(model_data['tables'])} tablas, "
          f"{len(similarity)} pares de similitud. Artifacts en {out_dir}")


if __name__ == "__main__":
    main()
