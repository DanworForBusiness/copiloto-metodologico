"""Invariantes de FRONTERA del paquete `tools/catalog/modeling_assessment` (Fase 1, hito M0).

Estos tests no prueban reglas de assessment (todavia no existen): prueban que el paquete
nace con la relacion correcta respecto del evaluador legacy y del producto:

  R-I2  dependencia unidireccional (el legacy no importa el paquete nuevo)
  R-I3  todo acceso al legacy pasa por `legacy_bridge.py`
  R-I4  el paquete no usa el evaluador/veredictos legacy ni engancha `generate_all`
  R-I6  salida LOCAL-ONLY explicita (sin default en catalog_local), fail-closed dentro de PERSIST_PATHS
  Fase 1: ningun identificador del paquete habla de score/banda/peso/umbral/penalidad.

(R-I1 "ningun archivo legacy modificado" y R-I5 "la firma del generador no cambia" son
propiedades del DIFF del PR: se verifican en el gate del hito con `git diff --stat`, no en
un test permanente, para no congelar archivos que otros tickets legitimamente editan.)
"""
from __future__ import annotations

import ast
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PKG_DIR = REPO / "tools" / "catalog" / "modeling_assessment"
PKG_NAME = "tools.catalog.modeling_assessment"
BRIDGE = PKG_DIR / "legacy_bridge.py"

_SKIP_DIR_NAMES = {"node_modules", "site-packages", "__pycache__", "catalog_local", "reports", "case_library"}


def _py_files(root: Path):
    for p in root.rglob("*.py"):
        parts = p.relative_to(root).parts
        if any(x.startswith(".") or x.startswith("venv") or x in _SKIP_DIR_NAMES for x in parts):
            continue
        yield p


def _pkg_files():
    return sorted(p for p in PKG_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def _imports(tree: ast.AST):
    """(nivel, modulo, [alias,...]) de cada import del arbol (incluye imports dentro de funciones)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield 0, a.name, [a.name]
        elif isinstance(node, ast.ImportFrom):
            yield node.level, node.module or "", [a.name for a in node.names]


# ── R-I2: el legacy nunca importa el paquete nuevo ──────────────────────────
def test_ri2_legacy_never_imports_new_package():
    offenders = []
    for p in _py_files(REPO):
        if PKG_DIR in p.parents or "tests" in p.relative_to(REPO).parts:
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)    # archivos legacy con escapes invalidos en strings
                tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for _lvl, mod, names in _imports(tree):
            if "modeling_assessment" in mod or any("modeling_assessment" in n for n in names):
                offenders.append(str(p.relative_to(REPO)))
    assert offenders == [], f"codigo fuera del paquete importa modeling_assessment: {offenders}"


@pytest.mark.parametrize("legacy_file", [
    "tools/catalog/generate_catalog.py",
    "tools/catalog/compliance_criteria.py",
    "tools/catalog/catalog_service.py",
])
def test_ri2_legacy_sources_do_not_mention_the_package(legacy_file):
    assert "modeling_assessment" not in (REPO / legacy_file).read_text(encoding="utf-8")


# ── R-I3: solo stdlib + intra-paquete fuera del bridge ──────────────────────
def test_ri3_only_the_bridge_touches_legacy():
    stdlib = set(sys.stdlib_module_names)
    offenders = []
    for p in _pkg_files():
        if p == BRIDGE:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for level, mod, _names in _imports(tree):
            if level > 0:
                continue                                   # import relativo = intra-paquete
            top = mod.split(".")[0]
            if mod == PKG_NAME or mod.startswith(PKG_NAME + "."):
                continue
            if top in stdlib:
                continue
            offenders.append(f"{p.name}: import {mod}")
    assert offenders == [], f"imports fuera de stdlib/intra-paquete (deben ir por legacy_bridge): {offenders}"


def test_ri3_bridge_is_the_only_module_that_imports_legacy():
    legacy_tops = {"tools", "config", "core", "webapp"}
    importing = []
    for p in _pkg_files():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for level, mod, _n in _imports(tree):
            if level == 0 and mod.split(".")[0] in legacy_tops and not mod.startswith(PKG_NAME):
                importing.append(p.name)
    assert set(importing) <= {"legacy_bridge.py"}
    assert "legacy_bridge.py" in importing


def test_package_import_is_light_and_does_not_load_legacy():
    code = (
        "import sys, tools.catalog.modeling_assessment, tools.catalog.modeling_assessment.model, "
        "tools.catalog.modeling_assessment.schema, tools.catalog.modeling_assessment.paths; "
        "assert 'tools.catalog.generate_catalog' not in sys.modules, 'el paquete cargo el legacy'; "
        "assert 'webapp' not in sys.modules"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr


# ── R-I4: sin evaluador/veredictos legacy ni hook en generate_all ───────────
_FORBIDDEN_LEGACY_NAMES = {"evaluate_compliance", "APTO_CANONICAL", "VERDICTS", "generate_all",
                           "process_folder", "_write_flow", "render_flujo"}
# Allowlist DELIBERADA y vacia: el hito M8 (informe de validacion shadow) puede agregar aqui,
# de forma visible en el PR, el modulo que compare contra el veredicto legacy. Nunca el perfil.
_VALIDATION_ONLY_ALLOWLIST: set[str] = set()


def test_ri4_package_never_references_legacy_verdict_or_generate_all():
    offenders = []
    for p in _pkg_files():
        if p.name in _VALIDATION_ONLY_ALLOWLIST:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in _FORBIDDEN_LEGACY_NAMES:
                offenders.append(f"{p.name}: {node.id}")
            elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_LEGACY_NAMES:
                offenders.append(f"{p.name}: .{node.attr}")
            elif isinstance(node, ast.alias) and node.name in _FORBIDDEN_LEGACY_NAMES:
                offenders.append(f"{p.name}: import {node.name}")
    assert offenders == [], offenders


def test_ri4_bridge_exposes_no_legacy_verdict_symbols():
    from tools.catalog.modeling_assessment import legacy_bridge as b
    public = {n for n in dir(b) if not n.startswith("__")}
    assert not (public & {"evaluate_compliance", "APTO_CANONICAL", "VERDICTS", "generate_all", "process_folder"})


# ── Fase 1: ningun identificador del paquete usa vocabulario de scoring ─────
def test_phase1_no_scoring_vocabulary_in_any_identifier():
    from tools.catalog.modeling_assessment.model import find_forbidden_tokens
    offenders = []
    for p in _pkg_files():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(node.name)
            elif isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
            elif isinstance(node, ast.arg):
                names.append(node.arg)
            elif isinstance(node, ast.keyword) and node.arg:
                names.append(node.arg)
            for n in names:
                # "legacy" esta prohibido en las CLAVES del perfil (no se incrusta el veredicto legacy),
                # pero es un identificador legitimo del codigo (`legacy_bridge`, `legacy_pool_groups`).
                bad = tuple(t for t in find_forbidden_tokens(n) if t != "legacy")
                if bad:
                    offenders.append(f"{p.name}: {n} -> {bad}")
    assert offenders == [], offenders


# ── No hay hook en generate_all ni escritura fuera del subdirectorio propio ─
def test_package_layout_is_the_approved_one():
    m0 = {"__init__.py", "model.py", "schema.py", "paths.py", "legacy_bridge.py", "profile_schema.v1.json"}
    m1 = {"scope.py", "graph.py", "identity.py"}
    present = {p.name for p in PKG_DIR.iterdir() if p.is_file()}
    assert m0 <= present and m1 <= present
    # Hito actual = M1: NO se adelantan milestones posteriores (M2+: facts_*/readers, M5 signals,
    # M6 assessability, M7 profile/shadow/CLI/Markdown).
    later = {"signals.py", "assessability.py", "profile.py", "shadow.py", "readers.py", "render_md.py",
             "__main__.py", "facts_source.py", "facts_ingestion.py", "facts_harmonization.py",
             "facts_propagation.py", "facts_am.py", "facts_naming.py"}
    assert not (present & later), sorted(present & later)


# ── R-I6 (corregido en M0): salida LOCAL-ONLY, sin default dentro de catalog_local ─
def _docstring_node_ids(tree):
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant)                     and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def test_contract_is_not_bound_to_catalog_local_in_code():
    """El destino no esta atado a `catalog_local` (que `mirror_up()` espeja a Spaces): ninguna
    constante de codigo (fuera de docstrings) del paquete lo menciona."""
    offenders = []
    for p in _pkg_files():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        skip = _docstring_node_ids(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
                if "catalog_local" in node.value:
                    offenders.append(f"{p.name}: {node.value!r}")
    assert offenders == [], offenders


def test_there_is_no_default_output_dir_and_no_catalog_local_helpers():
    from tools.catalog.modeling_assessment import paths
    for gone in ("default_output_dir", "catalog_dir", "OUTPUT_SUBDIR_NAME"):
        assert not hasattr(paths, gone), f"{gone} reintroduce un default atado a catalog_local"


def test_output_dir_is_required_and_configurable():
    from tools.catalog.modeling_assessment import paths
    for missing in (None, "", "   "):
        with pytest.raises(paths.OutputDirRequiredError):
            paths.resolve_output_dir(missing, persisted_dirs=lambda: [])
    out = paths.resolve_output_dir("some/local/dir", persisted_dirs=lambda: [])
    assert out == paths.Path("some/local/dir")


def test_output_inside_persisted_dir_is_rejected_even_when_mirroring_is_off(tmp_path):
    """Un archivo creado con el espejado apagado lo subiria despues otro `mirror_up()`:
    la guarda NO depende de que el espejado este activo ahora."""
    from tools.catalog.modeling_assessment import paths
    persisted = tmp_path / "persisted_root"
    inside = persisted / "deep" / "out"
    with pytest.raises(paths.ShadowOutputPersistenceError):
        paths.resolve_output_dir(inside, persisted_dirs=lambda: [persisted])
    assert paths.persisted_dir_containing(inside, persisted_dirs=lambda: [persisted]) == persisted
    # el mismo directorio persistido, con el espejado "apagado": sigue rechazado
    assert paths.output_dir_is_remote_mirrored(inside, persist_enabled=lambda: False,
                                               persisted_dirs=lambda: [persisted]) is False
    with pytest.raises(paths.ShadowOutputPersistenceError):
        paths.resolve_output_dir(inside, persisted_dirs=lambda: [persisted])


def test_output_outside_persisted_dirs_is_accepted_and_exception_is_explicit(tmp_path):
    from tools.catalog.modeling_assessment import paths
    persisted = tmp_path / "persisted_root"
    outside = tmp_path / "elsewhere" / "out"
    assert paths.resolve_output_dir(outside, persisted_dirs=lambda: [persisted]) == outside
    inside = persisted / "out"
    assert paths.resolve_output_dir(inside, allow_persisted=True, persisted_dirs=lambda: [persisted]) == inside


def test_mirror_detection_helper_is_preserved(tmp_path):
    from tools.catalog.modeling_assessment import paths
    persisted = tmp_path / "p"
    assert paths.output_dir_is_remote_mirrored(persisted / "x", persist_enabled=lambda: True,
                                               persisted_dirs=lambda: [persisted]) is True
    assert paths.output_dir_is_remote_mirrored(tmp_path / "other", persist_enabled=lambda: True,
                                               persisted_dirs=lambda: [persisted]) is False


@pytest.mark.parametrize("am", ["AM_X", "flujo_x", "FLUJO_Y", "a/b c", "..", ""])
def test_profile_filenames_never_collide_with_catalog_flow_loader(am):
    from tools.catalog.modeling_assessment import paths
    for ext in ("json", "md"):
        name = paths.profile_filename(am, ext)
        assert not name.lower().startswith("flujo_"), name          # el loader hace rglob("flujo_*.md")
        assert name.endswith(f".profile.{ext}")
        assert "/" not in name and "\\" not in name and not name.startswith(".")
