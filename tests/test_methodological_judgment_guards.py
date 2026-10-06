"""
Gate 8D.1 — guardas ESTATICAS del Juicio: genericidad (sin clientes/raices), aislamiento (sin Legacy/LLM/persistencia/UI), sin puntajes/pesos numericos y sin
umbrales de mayoria estricta como decisor de nivel.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from tests._judgment_cases import FIXTURES, fresh
from tools.catalog.methodological_judgment import evaluate_judgment

PKG = Path(__file__).resolve().parents[1] / "tools" / "catalog" / "methodological_judgment"
ASSESSMENT = Path(__file__).resolve().parents[1] / "tools" / "catalog" / "modeling_assessment"
SOURCES = sorted(PKG.glob("*.py"))

CLIENTS = ("illolay", "biogenesis", "kensing", "pedidos_entregas", "base_stocks", "descuentos", "prefacturacion", "lista_precios", "reserva_material", "sellout", "glaccount")
FORBIDDEN_IMPORT_ROOTS = ("anthropic", "openai", "requests", "httpx", "urllib", "socket", "sqlite3", "fastapi", "starlette", "jinja2", "streamlit", "boto3")
FORBIDDEN_IMPORT_PARTS = ("legacy", "verdict", "scoring", "persistence", "store", "webapp", "case_library")


def _code_without_docstrings(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and node.body and isinstance(node.body[0], ast.Expr) \
                and isinstance(getattr(node.body[0], "value", None), ast.Constant) and isinstance(node.body[0].value.value, str):
            node.body[0].value.value = ""
    return ast.unparse(tree)


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_client_or_root_names_in_engine_source(path):
    code = _code_without_docstrings(path).lower()
    assert not [c for c in CLIENTS if c in code], f"{path.name}: hay nombres de cliente/raiz en codigo ejecutable"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_forbidden_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        mods = [a.name for a in n.names] if isinstance(n, ast.Import) else ([n.module or ""] if isinstance(n, ast.ImportFrom) else [])
        for m in mods:
            low = m.lower()
            assert low.split(".")[0] not in FORBIDDEN_IMPORT_ROOTS, (path.name, m)
            assert not any(part in low.split(".") for part in FORBIDDEN_IMPORT_PARTS), (path.name, m)


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_io_clock_random_or_environment_in_engine(path):
    code = _code_without_docstrings(path)
    for token in ("open(", "os.environ", "datetime", "time.time", "random", "uuid", "subprocess", "Path(", "write_text", "write_bytes"):
        assert token not in code, (path.name, token)


def test_engine_has_no_numeric_scoring_weights_or_majority_thresholds():
    """Sin pesos/puntajes y sin umbral de 'mayoria estricta' (>50%) como decisor de nivel. Los calificadores pueden tener cortes descriptivos (sin efecto en nivel)."""
    rules_code = _code_without_docstrings(PKG / "rules.py") + _code_without_docstrings(PKG / "engine.py")
    for token in ("score", "weight", "points", "* 0.", "> 0.5", ">= 0.5", "0.51", "majority"):
        assert token not in rules_code.lower(), token
    engine = _code_without_docstrings(PKG / "engine.py")
    assert "naming" not in engine.lower() or "PROVISIONAL" not in re.findall(r"naming[^\n]*", engine.lower())


def test_every_rule_module_function_is_pure_over_the_evidence_abstraction():
    """El motor solo lee la evidencia por ``ProfileEvidence``; nunca accede al perfil crudo por clave de nivel/Juicio."""
    for name in ("rules.py", "engine.py", "cta.py", "executive.py"):
        code = _code_without_docstrings(PKG / name)
        assert 'profile["judgment' not in code and "profile['judgment" not in code


def test_judgment_is_a_standalone_sibling_of_the_assessment_package_and_imports_none_of_it():
    for f in PKG.glob("*.py"):
        assert "modeling_assessment" not in f.read_text(encoding="utf-8"), f.name      # lee el JSON persistido, no importa el paquete


def test_judgment_package_is_not_imported_by_the_assessment_package():
    """El Assessment NO depende del Juicio (consumidor hermano, no parte del Assessment)."""
    for f in ASSESSMENT.glob("*.py"):
        assert "methodological_judgment" not in f.read_text(encoding="utf-8"), f.name


def test_judgment_does_not_depend_on_domain_classification():
    for f in PKG.glob("*.py"):
        assert "domain_classification" not in f.read_text(encoding="utf-8").lower().replace("domain_classification_extension_point", ""), f.name


def test_fixtures_are_pseudonymized():
    """Los fixtures versionados no contienen nombres reales de clientes ni de raices."""
    for f in FIXTURES.glob("*.json.gz"):
        import gzip
        text = gzip.open(f).read().decode("utf-8").lower()
        for c in ("illolay", "biogenesis", "kensing"):
            assert c not in text, (f.name, c)


def test_assessment_profile_stays_free_of_judgment_vocabulary_after_evaluation():
    p = fresh("biogenesis_base_stocks")
    before = json.dumps(p, sort_keys=True)
    evaluate_judgment(p)
    assert json.dumps(p, sort_keys=True) == before
