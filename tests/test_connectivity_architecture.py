"""Invariantes de arquitectura del paquete de conectividad (F2.1).

Garantiza que: (1) el paquete puro + la capa de extracción NO arrastran el CLI pesado ni el webapp al
importarse (la fuente real los carga PEREZOSO); (2) la extracción observada no depende de los contratos
GOBERNADOS de homologación (observado ≠ gobernado); (3) nada del paquete importa webapp a nivel módulo.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "tools" / "catalog" / "connectivity_governance"


def _toplevel_imports(path: Path):
    """Solo imports de NIVEL MÓDULO (incluye los de try/except de nivel módulo); excluye imports perezosos
    dentro de funciones/métodos."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bodies = list(tree.body)
    for node in list(bodies):
        if isinstance(node, ast.Try):
            bodies.extend(node.body)
            bodies.extend(h for handler in node.handlers for h in handler.body)
            bodies.extend(node.orelse)
            bodies.extend(node.finalbody)
    mods = set()
    for node in bodies:
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.add(node.module)
    return mods


def test_importing_package_does_not_pull_cli_or_webapp():
    # Subproceso limpio: aislar de la contaminación de sys.modules de otros tests de la sesión.
    code = (
        "import sys; import tools.catalog.connectivity_governance;"
        "bad=[m for m in sys.modules if m=='webapp' or m.startswith('webapp.')"
        " or m=='tools.catalog.DSP_CLI_Export_Lineage'];"
        "print(bad); sys.exit(1 if bad else 0)"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), capture_output=True, text=True)
    assert r.returncode == 0, f"importar el paquete cargó módulos pesados: {r.stdout}{r.stderr}"


def test_pure_and_extraction_modules_have_no_toplevel_cli_or_webapp_import():
    offenders = {}
    for py in PKG.glob("*.py"):
        bad = [m for m in _toplevel_imports(py)
               if m == "webapp" or m.startswith("webapp.") or m == "tools.catalog.DSP_CLI_Export_Lineage"]
        if bad:
            offenders[py.name] = bad
    # sources.py referencia el CLI SOLO perezosamente (dentro de un método), no a nivel módulo.
    assert offenders == {}, f"imports de nivel módulo prohibidos: {offenders}"


def test_extraction_does_not_depend_on_governed_homologation_contract():
    # observado ≠ gobernado: la extracción no importa contract.py (homologación gobernada).
    assert "contract" not in {m.split(".")[-1] for m in _toplevel_imports(PKG / "extraction.py")}
    assert "contract" not in {m.split(".")[-1] for m in _toplevel_imports(PKG / "inventory.py")}
