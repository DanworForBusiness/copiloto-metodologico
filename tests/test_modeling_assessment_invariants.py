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
# Catalog Assessment Report V1: la UNICA integración autorizada del paquete es la capa web delgada que consume su API
# Python (servicio + presenter). El legacy (tools/catalog/*, core, el resto de webapp) sigue sin poder importarlo.
SANCTIONED_WEB_INTEGRATION = {
    Path("webapp/services/assessment_service.py"),
    Path("webapp/services/assessment_presenter.py"),
}


def test_ri2_legacy_never_imports_new_package():
    offenders = []
    for p in _py_files(REPO):
        if PKG_DIR in p.parents or "tests" in p.relative_to(REPO).parts:
            continue
        if p.relative_to(REPO) in SANCTIONED_WEB_INTEGRATION:
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


def test_ri2_only_the_sanctioned_web_integration_imports_the_package_outside_it():
    importers = set()
    for p in _py_files(REPO):
        if PKG_DIR in p.parents or "tests" in p.relative_to(REPO).parts:
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        if any("modeling_assessment" in mod or any("modeling_assessment" in n for n in names) for _l, mod, names in _imports(tree)):
            importers.add(p.relative_to(REPO))
    assert importers == SANCTIONED_WEB_INTEGRATION, importers        # ni más (legacy aislado) ni menos (la integración existe)


# ── R-I2b: la integración web usa SOLO la superficie pública del paquete ─────
# Superficie soportada para consumidores externos: módulos públicos y, dentro de ellos, nombres sin guion bajo. El vocabulario
# y el formato humano compartidos con el Markdown se consumen por `presentation` (API pública), jamás por privados de `render_md`.
SUPPORTED_INTEGRATION_MODULES = {"presentation", "profile", "shadow", "scope", "paths", "render_md"}


def _private_package_accesses(tree: ast.AST) -> list[str]:
    """Accesos a símbolos/módulos con guion bajo del paquete desde CÓDIGO EXTERNO: `from pkg.m import _x`, `import pkg._m`,
    `alias._x` (alias de un import del paquete), la cadena completa `pkg.m._x` y `getattr(alias, "_x")`."""
    out: list[str] = []
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == PKG_NAME or a.name.startswith(PKG_NAME + "."):
                    aliases.add(a.asname or a.name)
                    if any(part.startswith("_") for part in a.name.split(".")):
                        out.append(f"import {a.name}")
        elif isinstance(node, ast.ImportFrom) and not node.level and (node.module == PKG_NAME or (node.module or "").startswith(PKG_NAME + ".")):
            if any(part.startswith("_") for part in node.module.split(".")):
                out.append(f"from {node.module}")
            for a in node.names:
                aliases.add(a.asname or a.name)
                if a.name.startswith("_"):
                    out.append(f"from {node.module} import {a.name}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("_") and not node.attr.startswith("__"):
            base = node.value
            chain = []
            while isinstance(base, ast.Attribute):
                chain.append(base.attr)
                base = base.value
            root = base.id if isinstance(base, ast.Name) else None
            dotted = ".".join([root, *reversed(chain)]) if root else ""
            if root in aliases or dotted == PKG_NAME or dotted.startswith(PKG_NAME + "."):
                out.append(f"{dotted}.{node.attr}")
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2
              and isinstance(node.args[0], ast.Name) and node.args[0].id in aliases
              and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str) and node.args[1].value.startswith("_")):
            out.append(f"getattr({node.args[0].id}, {node.args[1].value!r})")
    return out


def _package_surface_violations(tree: ast.AST) -> list[str]:
    """Imports del paquete fuera de `SUPPORTED_INTEGRATION_MODULES`."""
    out = []
    for level, mod, names in _imports(tree):
        if level or not (mod == PKG_NAME or mod.startswith(PKG_NAME + ".")):
            continue
        if mod == PKG_NAME:
            out += [f"{PKG_NAME}.{n}" for n in names if n not in SUPPORTED_INTEGRATION_MODULES]
        else:
            top = mod[len(PKG_NAME) + 1:].split(".")[0]
            if top not in SUPPORTED_INTEGRATION_MODULES:
                out.append(mod)
    return out


def _external_package_consumers() -> dict[Path, ast.AST]:
    found = {}
    for p in _py_files(REPO):
        if PKG_DIR in p.parents or "tests" in p.relative_to(REPO).parts:
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        if any("modeling_assessment" in mod or any("modeling_assessment" in n for n in names) for _l, mod, names in _imports(tree)):
            found[p.relative_to(REPO)] = tree
    return found


def test_ri2b_external_code_never_touches_private_package_symbols():
    consumers = _external_package_consumers()
    assert set(consumers) == SANCTIONED_WEB_INTEGRATION                          # no se amplía la superficie permitida
    offenders = {str(p): _private_package_accesses(t) for p, t in consumers.items() if _private_package_accesses(t)}
    assert offenders == {}, f"código externo usa símbolos PRIVADOS (con guion bajo) del paquete: {offenders}"


def test_ri2b_integration_imports_only_the_supported_public_modules():
    for p, tree in _external_package_consumers().items():
        assert _package_surface_violations(tree) == [], str(p)


def test_ri2b_presenter_gets_shared_wording_only_through_the_public_presentation_api():
    tree = ast.parse((REPO / "webapp/services/assessment_presenter.py").read_text(encoding="utf-8"))
    mods = {mod + "." + n if mod == PKG_NAME else mod for _l, mod, names in _imports(tree) for n in names if PKG_NAME in mod}
    assert mods == {f"{PKG_NAME}.presentation"}, mods                             # ni render_md ni nada más


# CONTROL NEGATIVO PERMANENTE del detector: cada forma de acceso privado DEBE ser detectada; las públicas, no.
@pytest.mark.parametrize("src", [
    "from tools.catalog.modeling_assessment.render_md import _AREA_TITLE\n",
    "from tools.catalog.modeling_assessment import render_md as R\nx = R._occurrence_line\n",
    "from tools.catalog.modeling_assessment import render_md\nx = render_md._pct(1)\n",
    "import tools.catalog.modeling_assessment.render_md as R\nR._plural(1, 'a')\n",
    "import tools.catalog.modeling_assessment.paths\ntools.catalog.modeling_assessment.paths._is_within(1, 2)\n",
    "from tools.catalog.modeling_assessment import paths as ma_paths\nma_paths._is_within(1, 2)\n",
    "from tools.catalog.modeling_assessment import render_md as R\ngetattr(R, '_LEVEL_TEXT')\n",
    "from tools.catalog.modeling_assessment._internal import thing\n",
])
def test_ri2b_detector_flags_every_form_of_private_access(src):
    assert _private_package_accesses(ast.parse(src)), src


@pytest.mark.parametrize("src", [
    "from tools.catalog.modeling_assessment import presentation as P\nP.area_title('X')\n",
    "from tools.catalog.modeling_assessment.render_md import render_markdown\nrender_markdown({})\n",
    "from tools.catalog.modeling_assessment import shadow\nshadow.assess\nshadow.__name__\n",
    "x = self._private\n",                                                        # atributos privados propios no son del paquete
])
def test_ri2b_detector_does_not_flag_public_or_unrelated_access(src):
    assert _private_package_accesses(ast.parse(src)) == [], src


def test_ri2b_surface_detector_flags_modules_outside_the_supported_set():
    assert _package_surface_violations(ast.parse("from tools.catalog.modeling_assessment import signals\n"))
    assert _package_surface_violations(ast.parse("from tools.catalog.modeling_assessment.legacy_bridge import x\n"))
    assert not _package_surface_violations(ast.parse("from tools.catalog.modeling_assessment.presentation import area_title\n"))


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
    m2 = {"readers.py", "facts_support.py", "facts_source.py", "facts_ingestion.py"}
    m3 = {"contract.py", "facts_am.py", "facts_propagation.py", "facts_harmonization.py"}
    m4 = {"naming_rules.py", "facts_naming.py"}
    m5 = {"signals.py"}
    m6 = {"assessability.py"}
    m7 = {"profile.py", "render_md.py", "shadow.py", "__main__.py"}
    view_type = {"facts_view_type.py"}       # View Type Intelligence (Gate 2): hecho descriptivo am.view_type_composition (sin juicio)
    public_api = {"presentation.py"}        # Catalog Assessment Report V1: API pública de presentación para consumidores externos
    present = {p.name for p in PKG_DIR.iterdir() if p.is_file()}
    approved = m0 | m1 | m2 | m3 | m4 | m5 | m6 | m7 | view_type | public_api
    assert approved <= present, sorted(approved - present)
    # Hito actual = M7: NO se adelantan milestones posteriores (M8: informe de validacion shadow contra el legacy) ni aparecen archivos no aprobados.
    assert present == approved, sorted(present - approved)


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
