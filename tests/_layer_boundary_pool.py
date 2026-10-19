"""Pool CSN sintetico y GENERICO para LAYER_BOUNDARY_REVIEW (signal-rules/v4): hojas remotas (tabla fisica ABAP / CDS gobernada), vistas de paso, vistas con logica y un modelo analitico.

Cada topologia es un dict:
    {"leaves": {nombre: entidad_remota}, "views": [{"name", "sources": [...], "kind": "pass" | "filter" | "join", "pattern": "DATA_STRUCTURE" | "ANALYTICAL_FACT" | ...}], "am_from": vista}
``kind``: ``pass`` = proyeccion 1:1 de UN proveedor (sin filtro, join ni derivadas); ``filter`` = un proveedor con WHERE; ``join`` = dos proveedores con INNER JOIN.
Sin nombres de cliente ni de modelo reales.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests._evidence_release_pool import _view, el, remote_table

CLIENT, AM = "Acme", "AM_TEST"
COLS = ("K", "A", "B")
TABLE = "ABAPTABLES.TAB1"                    # tabla fisica SAP (frontera cruda)
CDS = "ABAP_CDS.GOVCDS$F"                    # CDS gobernada


def _w(root: Path, rel: str, body: dict) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body), encoding="utf-8")


def _elements() -> dict:
    return {c: el(f"label {c}") for c in COLS}


def _view_def(spec: dict) -> dict:
    name, sources, kind = spec["name"], spec["sources"], spec.get("kind", "pass")
    pattern = spec.get("pattern", "DATA_STRUCTURE")
    if kind == "join":
        a, b = sources
        frm = {"join": "inner", "args": [{"ref": [a]}, {"ref": [b]}], "on": [{"ref": [a, "K"]}, "=", {"ref": [b, "K"]}]}
        cols = [{"ref": [a, "K"]}, {"ref": [a, "A"]}, {"ref": [b, "B"]}]
        return _view(name, cols, frm, _elements(), pattern=pattern)
    (s,) = sources
    cols = [{"ref": [s, c]} for c in COLS]
    where = [{"ref": [s, "A"]}, "=", {"val": "X"}] if kind == "filter" else None
    return _view(name, cols, {"ref": [s]}, _elements(), pattern=pattern, where=where)


def write_pool(root: Path, topology: dict) -> Path:
    for leaf, entity in topology["leaves"].items():
        _w(root, f"{CLIENT}/remote-tables/{leaf}.json", {"definitions": remote_table(leaf, entity, _elements(), connection="S4PREM")})
    for spec in topology["views"]:
        _w(root, f"{CLIENT}/views/{spec['name']}.json", {"definitions": _view_def(spec)})
    src = topology["am_from"]
    am_el = {**_elements(), "Cantidad": {"type": "cds.Decimal", "precision": 20, "scale": 3, "@EndUserText.label": "Cantidad", "@AnalyticsDetails.measureType": {"#": "BASE"}}}
    am_cols = [{"ref": [src, c], "as": c} for c in COLS] + [{"ref": [src, "A"], "as": "Cantidad"}]
    _w(root, f"{CLIENT}/analytic-models/{AM}.json", {"definitions": {AM: {
        "kind": "entity", "elements": am_el, "@ObjectModel.modelingPattern": {"#": "ANALYTICAL_CUBE"}, "@Analytics.dataCategory": {"#": "CUBE"},
        "query": {"SELECT": {"from": {"ref": [src]}, "columns": am_cols}}}}})
    return root


def view(name: str, sources: list[str], kind: str = "pass", pattern: str = "DATA_STRUCTURE") -> dict:
    return {"name": name, "sources": sources, "kind": kind, "pattern": pattern}
