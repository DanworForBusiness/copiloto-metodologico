"""Catalog Assessment Report V1 — API PÚBLICA de presentación (`presentation.py`).

Contrato: es la ÚNICA puerta soportada para que un consumidor externo obtenga el vocabulario y el formato humano (español) que ya usa el
Markdown. Solo delega en `render_md` (dependencia interna del paquete): no razona, no cambia el esquema ni las reglas ni el Markdown.
Los documentos de prueba son los mismos escenarios sintéticos que disparan TODAS las señales del registro.
"""
from __future__ import annotations

import ast
import types
from pathlib import Path

import pytest

from tests._modeling_assessment_m7_fixtures import scenario_pools
from tests.test_modeling_assessment_assessability import _local_chain
from tests.test_modeling_assessment_profile import _groups, build
from tools.catalog.modeling_assessment import presentation as PR
from tools.catalog.modeling_assessment import render_md as R

PKG = Path(__file__).resolve().parent.parent / "tools" / "catalog" / "modeling_assessment"


@pytest.fixture(scope="module")
def docs(tmp_path_factory):
    out = [(am, build(ents, am, ev)[0]) for ents, am, ev in scenario_pools(tmp_path_factory.mktemp("presentation"))]
    out.append(("AM_C", build(_local_chain(), "AM_C")[0]))
    return out


# ── forma de la API ─────────────────────────────────────────────────────────
def test_public_surface_is_explicit_and_has_no_underscore_names():
    assert PR.__all__ and all(not n.startswith("_") for n in PR.__all__)
    for name in PR.__all__:
        assert callable(getattr(PR, name)), name
    public_namespace = {n for n in dir(PR) if not n.startswith("_") and not isinstance(getattr(PR, n), types.ModuleType)}
    assert public_namespace - {"annotations", "Any"} == set(PR.__all__)           # nada público fuera de __all__


def test_module_only_delegates_to_render_md_and_adds_no_reasoning():
    tree = ast.parse((PKG / "presentation.py").read_text(encoding="utf-8"))
    imports = [(n.level, n.module, [a.name for a in n.names]) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert sorted(imports, key=str) == sorted([(0, "__future__", ["annotations"]), (0, "typing", ["Any"]), (1, None, ["render_md"])], key=str)
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.For, ast.While, ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp))]


# ── la API devuelve EXACTAMENTE lo que usa el Markdown ──────────────────────
@pytest.mark.parametrize("fn,table", [
    (PR.area_title, R._AREA_TITLE), (PR.macro_dimension_title, R._MACRO_TEXT), (PR.assessability_text, R._STATE_TEXT),
    (PR.assessability_plural_text, R._STATE_PLURAL_TEXT), (PR.execution_state_text, R._EXECUTION_TEXT), (PR.confidence_level_text, R._LEVEL_TEXT),
    (PR.finding_kind_text, R._KIND_TEXT), (PR.rule_result_text, R._RULE_RESULT_TEXT), (PR.unit_label, R._UNIT_TEXT),
])
def test_translations_equal_the_markdown_tables_and_unknown_ids_pass_through(fn, table):
    assert table, fn.__name__
    for key, text in table.items():
        assert fn(key) == text
    assert fn("ID_FUTURO_DESCONOCIDO") == "ID_FUTURO_DESCONOCIDO"                  # nunca lanza: una versión futura no rompe la lectura


def test_every_enum_value_present_in_real_documents_is_translated_not_passed_through(docs):
    seen = {"area": set(), "macro": set(), "assess": set(), "level": set(), "kind": set(), "rule": set(), "exec": set()}
    for _am, d in docs:
        seen["exec"].add(d["assessment_state"])
        seen["level"].add(d["summary"]["confidence"]["level"])
        for a in d["areas"]:
            seen["area"].add(a["id"]); seen["macro"].add(a["macro_dimension"]); seen["assess"].add(a["assessability"])
        for s in d["evidence_limitations"]:
            seen["assess"].add(s["assessability"]); seen["area"].add(s["area"])
        for g in _groups(d):
            seen["kind"].add(g["kind"]); seen["area"].add(g["area"])
        for r in d["rule_results"]:
            seen["rule"].add(r["result"])
    fns = {"area": PR.area_title, "macro": PR.macro_dimension_title, "assess": PR.assessability_text, "level": PR.confidence_level_text,
           "kind": PR.finding_kind_text, "rule": PR.rule_result_text, "exec": PR.execution_state_text}
    for kind, values in seen.items():
        assert values, kind
        for v in values:
            assert fns[kind](v) != v, (kind, v)


def test_formatters_equal_the_private_ones_the_markdown_uses():
    for v in (None, 0.0, 0.8974, 1.0):
        assert PR.percent_text(v) == R._pct(v)
    assert PR.percent_text(None) == "n/d" and PR.percent_text(0.8974) == "89.7%"
    for n in (0, 1, 2, 17):
        assert PR.plural(n, "ocurrencia") == R._plural(n, "ocurrencia") and PR.plural(n, "asociación", "asociaciones") == R._plural(n, "asociación", "asociaciones")
    for v in (3, "x", [1, 2], {"observed": 2, "applicable": 4}, [{"name": "a", "count": 1}]):
        assert PR.value_text(v) == R._value_text(v)
    assert PR.unit_label("closure") == "cierre semántico completo" and not PR.is_artifact_name("closure") and PR.is_artifact_name("VF_X")


def test_occurrence_text_is_the_markdown_line_for_every_occurrence_of_every_signal(docs):
    checked = kinds = 0
    for _am, d in docs:
        md = R.render_markdown(d)
        for g in _groups(d):
            kinds += 1
            for o in g["occurrences"]:
                line = PR.occurrence_text(g["id"], o)
                assert line == R._occurrence_line(g["id"], o)
                assert PR.occurrence_text(g["id"], o, plain=True) == line.replace("`", "") and "`" not in PR.occurrence_text(g["id"], o, plain=True)
                checked += 1
            for o in g["occurrences"][:R.MAX_OCCURRENCES_SHOWN]:                      # y esa línea ES la que aparece en el Markdown exportado
                assert f"  - {PR.occurrence_text(g['id'], o)}" in md
    assert checked > 20 and kinds >= 12                                               # los escenarios cubren (casi) todas las señales


def test_api_never_mutates_the_document_nor_the_markdown(docs):
    import copy
    _am, d = docs[0]
    before, md = copy.deepcopy(d), R.render_markdown(d)
    for g in _groups(d):
        for o in g["occurrences"]:
            PR.occurrence_text(g["id"], o)
            PR.occurrence_text(g["id"], o, plain=True)
    assert d == before and R.render_markdown(d) == md


def test_row_key_text_uses_the_markdown_vocabulary_and_never_raises():
    assert PR.row_key_text("compliant") == "cumplen" and PR.row_key_text("non_compliant") == "no cumplen"
    assert PR.row_key_text("some_new_key") == "some new key"                      # desconocida: con espacios, sin lanzar
    assert "row_key_text" in PR.__all__
