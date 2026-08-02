"""
test_routing_corpus_regression.py — Red de seguridad del router (revivida).

`tests/README.md` describe una suite de caracterización previa a la migración
Streamlit->FastAPI (`ui/tools_panel.py`, `core/routing.py`) que ya no existe —
quedó huérfana junto con `tests/prompts_corpus.yaml` (el corpus en sí, 26 casos
reales por módulo, sigue vigente) y `tests/golden/routing_decisions.json` (el
golden VIEJO, de la cascada Streamlit — no lo tocamos ni lo reusamos acá).

Esta suite reconecta el mismo corpus contra el router ACTUAL
(`webapp.services.assistant_routing.AssistantRoutingService.route`), pineando
la decisión real (mensaje + acciones) en `tests/golden/routing_decisions_v2.json`.
No juzga si la decisión es la correcta (mismo espíritu que el corpus: el
`intent` es documentación, no aserción) — solo garantiza que un cambio de
`section_meta`/`out_of_scope_redirect`/headings en un módulo no mueva
silenciosamente a qué módulo/sección deriva una pregunta real.

Uso:
    # Verificar que el routing NO cambió
    .venv/Scripts/python.exe -m pytest tests/test_routing_corpus_regression.py -q

    # Regenerar el golden tras un cambio de contenido intencional y revisado
    UPDATE_GOLDEN=1 .venv/Scripts/python.exe -m pytest \
        tests/test_routing_corpus_regression.py::test_regenerate_golden -s
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from webapp.services.assistant_routing import AssistantRoutingService
from webapp.services.content_service import load_module

PACK = "datasphere_core"
CORPUS_PATH = Path(__file__).parent / "prompts_corpus.yaml"
GOLDEN_PATH = Path(__file__).parent / "golden" / "routing_decisions_v2.json"

_service = AssistantRoutingService()


def _load_corpus() -> list[tuple[str, str, str]]:
    """(module_stem, intent, prompt) por cada caso — puede repetir stem (el
    corpus tiene bloques adicionales para el mismo módulo)."""
    raw = yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8")) or []
    cases: list[tuple[str, str, str]] = []
    for block in raw:
        stem = block["module"]
        for case in block["cases"]:
            cases.append((stem, case["intent"], case["prompt"]))
    return cases


def _decide(stem: str, prompt: str) -> dict:
    document = load_module(stem, PACK)
    route = _service.route(document, prompt, PACK)
    if route is None:
        return {"route": None}
    return {
        "route": {
            "message": route.message,
            "actions": [
                {"label": a.label, "url": a.url, "external": a.external, "embed": a.embed}
                for a in route.actions
            ],
            "explain_module": route.explain_module,
            "explain_heading": route.explain_heading,
        }
    }


_CASES = _load_corpus()
_CASE_IDS = [f"{stem}::{intent}::{prompt[:28]}" for stem, intent, prompt in _CASES]


@pytest.mark.parametrize("stem,intent,prompt", _CASES, ids=_CASE_IDS)
def test_routing_decision_matches_golden(stem: str, intent: str, prompt: str):
    if not GOLDEN_PATH.exists():
        pytest.skip("Golden no generado todavía — correr test_regenerate_golden con UPDATE_GOLDEN=1")
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    key = f"{stem}::{intent}::{prompt}"
    assert key in golden, f"Caso nuevo sin golden: {key!r} — regenerar con UPDATE_GOLDEN=1"
    actual = _decide(stem, prompt)
    assert actual == golden[key], (
        f"El routing cambió para {stem!r} / intent={intent!r} / prompt={prompt!r}.\n"
        "Si es intencional (cambio de contenido revisado), regenerar con:\n"
        "  UPDATE_GOLDEN=1 pytest tests/test_routing_corpus_regression.py::test_regenerate_golden -s"
    )


def test_regenerate_golden():
    if not os.environ.get("UPDATE_GOLDEN"):
        pytest.skip("Solo corre con UPDATE_GOLDEN=1 (regeneración intencional).")
    golden = {}
    for stem, intent, prompt in _CASES:
        key = f"{stem}::{intent}::{prompt}"
        golden[key] = _decide(stem, prompt)
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(
        json.dumps(golden, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"\nGolden regenerado: {GOLDEN_PATH} ({len(golden)} casos)")
