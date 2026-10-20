"""Fixtures SEUDONIMIZADAS de Judgments «No determinables» reales (ver tests/fixtures/nd_explainability/README.md) + ratificaciones SINTETICAS para el caso LVL-05."""
from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "nd_explainability"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
ALIASES = sorted(MANIFEST)
HEX = "a" * 64


def load(alias: str) -> dict:
    with gzip.open(FIXTURES / f"{alias}.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)


def fresh(alias: str) -> dict:
    return copy.deepcopy(load(alias))


def ratification(profile: dict, leaf: str, event_id: str = "gd-simulated") -> dict:
    """Ratificacion SIMULADA (no es un evento real) valida para el motor: identidad exacta de la hoja + referencia de evidencia."""
    rec = next(r for r in profile["canonical_evidence"]["source_contract_classes"]["leaves"] if r["leaf"] == leaf)
    return {"leaf": leaf, "identity": {"remote_connection": rec["remote_connection"], "remote_entity": rec["remote_entities"][0]}, "event_id": event_id, "digest": HEX,
            "technical_id": f"tid:{leaf}", "evidence_ref": {"algo": "sha256", "fields": ["source_contract_classes.leaves[sql]"], "digest": HEX}}
