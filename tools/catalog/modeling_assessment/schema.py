"""Esquema JSON del perfil, GENERADO desde las dataclasses de `model.py`.

Al derivarse por introspeccion, el esquema no puede desincronizarse del contrato.
La copia versionada `profile_schema.v1.json` es un golden: si cambia el contrato hay
que regenerarla de forma deliberada (y revisable en el PR):

    python -m tools.catalog.modeling_assessment.schema --write
    python -m tools.catalog.modeling_assessment.schema --check
"""
from __future__ import annotations

import argparse
import json
import sys
import types
import typing
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

from .model import CoverageCounts, ModelingProfile, SCHEMA_VERSION

SCHEMA_FILE = Path(__file__).with_name("profile_schema.v1.json")

# Campos calculados que `to_jsonable` agrega (no son campos de la dataclass).
_COMPUTED: dict[type, dict[str, dict[str, Any]]] = {
    CoverageCounts: {
        "applicable": {"type": "integer", "minimum": 0},
        "ratio_strict": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "ratio_inclusive": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
    },
}


def _schema_for(tp: Any, defs: dict[str, Any]) -> dict[str, Any]:
    origin = get_origin(tp)
    if tp is Any:
        return {}
    if tp is str:
        return {"type": "string"}
    if tp is bool:
        return {"type": "boolean"}
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is type(None):
        return {"type": "null"}
    if isinstance(tp, type) and issubclass(tp, Enum):
        _register_enum(tp, defs)
        return {"$ref": f"#/$defs/{tp.__name__}"}
    if is_dataclass(tp):
        _register_dataclass(tp, defs)
        return {"$ref": f"#/$defs/{tp.__name__}"}
    if origin in (tuple, list):
        args = [a for a in get_args(tp) if a is not Ellipsis]
        return {"type": "array", "items": _schema_for(args[0], defs) if args else {}}
    if origin in (typing.Union, types.UnionType):
        return {"anyOf": [_schema_for(a, defs) for a in get_args(tp)]}
    raise TypeError(f"tipo no soportado en el contrato: {tp!r}")


def _register_enum(tp: type[Enum], defs: dict[str, Any]) -> None:
    defs.setdefault(tp.__name__, {"type": "string", "enum": [m.value for m in tp]})


def _register_dataclass(tp: type, defs: dict[str, Any]) -> None:
    if tp.__name__ in defs:
        return
    defs[tp.__name__] = {}                      # evita recursion infinita
    hints = get_type_hints(tp)
    props: dict[str, Any] = {}
    required: list[str] = []
    for f in fields(tp):
        props[f.name] = _schema_for(hints[f.name], defs)
        required.append(f.name)
    for name, sch in _COMPUTED.get(tp, {}).items():
        props[name] = sch
        required.append(name)
    defs[tp.__name__] = {"type": "object", "additionalProperties": False,
                         "properties": props, "required": sorted(required)}


def build_profile_schema() -> dict[str, Any]:
    defs: dict[str, Any] = {}
    _register_dataclass(ModelingProfile, defs)
    root = defs.pop(ModelingProfile.__name__)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_VERSION,
        "title": "Modeling Self-Assessment — perfil (Fase 1: sin scores ni bandas)",
        **root,
        "$defs": dict(sorted(defs.items())),
    }


def schema_text() -> str:
    return json.dumps(build_profile_schema(), sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def iter_schema_names(node: Any) -> list[str]:
    """Nombres de propiedades y valores de enum presentes en el esquema (para el test de vocabulario)."""
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "properties" and isinstance(v, dict):
                out.extend(v.keys())
            if k == "enum" and isinstance(v, list):
                out.extend(str(x) for x in v)
            out.extend(iter_schema_names(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(iter_schema_names(v))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Esquema JSON del perfil de Modeling Self-Assessment")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true", help="regenera profile_schema.v1.json")
    g.add_argument("--check", action="store_true", help="falla si el golden no coincide")
    args = ap.parse_args(argv)
    text = schema_text()
    if args.write:
        SCHEMA_FILE.write_text(text, encoding="utf-8")
        print(f"escrito {SCHEMA_FILE}")
        return 0
    current = SCHEMA_FILE.read_text(encoding="utf-8") if SCHEMA_FILE.exists() else ""
    if current != text:
        print("profile_schema.v1.json DESINCRONIZADO con model.py (usar --write y revisar el diff)")
        return 1
    print("esquema sincronizado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
