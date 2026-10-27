"""test_human_capital_candidates_table_ergonomics.py — CCHH UX homologation:
adoption of the shared reorder/resize primitive (initDraggableColumns/
initResizableColumns, app.js) by the Candidates list.

The table-agnostic contract of the primitive itself is proven in
test_app_table_ergonomics.py; Commercial's own wiring/regression lives in
test_commercial_licensing_table_ergonomics.py. This file covers CCHH's own
adapter (partials/human_capital_profiles_panel.html's inline <script>) —
Candidates is SSR/static, so unlike Commercial it never rebuilds HTML from a
column array; it moves already-rendered DOM nodes into the stored order."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "human_capital_candidates_table_ergonomics.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_human_capital_candidates_table_ergonomics_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Human Capital candidates table ergonomics failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL HUMAN CAPITAL CANDIDATES TABLE ERGONOMICS INVARIANTS PASSED" in result.stdout


_TEMPLATES = Path(__file__).resolve().parents[2] / "webapp" / "templates" / "partials"


def test_candidates_declares_the_layout_version_that_invalidates_widths_saved_before_the_selection_column():
    """Los anchos guardados antes de la columna de selección (registro legacy sin firma, tratado como layout v1) describen una tabla que ya no existe: Candidatos declara `data-layout-version >= 2` para que se descarten una vez."""
    import re
    html = (_TEMPLATES / "human_capital_profiles_panel.html").read_text(encoding="utf-8")
    container = re.search(r'<div class="matrix-scroll"[^>]*data-resizable-columns-key="cchh-candidatos"[^>]*>', html)
    assert container, "no se encontró el contenedor redimensionable de Candidatos"
    version = re.search(r'data-layout-version="(\d+)"', container.group(0))
    assert version and int(version.group(1)) >= 2
    colgroup = re.search(r"<colgroup>(.*?)</colgroup>", html, re.S).group(1)
    cols = re.findall(r"<col\b([^>]*)>", colgroup)
    assert "data-column-key" not in cols[0] and "data-column-key" not in cols[-1]          # casilla al inicio y Acciones al final: columnas SIN key (cuentan en la firma estructural)


def test_every_resizable_table_keeps_a_stable_unique_storage_key():
    """Cada tabla redimensionable persiste bajo SU key (aislamiento de preferencias): ninguna plantilla comparte key con otra tabla distinta."""
    import re
    keys: dict[str, str] = {}
    for tpl in sorted(_TEMPLATES.glob("*.html")):
        for key in re.findall(r'data-resizable-columns-key="([^"{}]+)"', tpl.read_text(encoding="utf-8")):
            assert keys.setdefault(key, tpl.name) == tpl.name, f"{key} lo usan {keys[key]} y {tpl.name}"
    assert "cchh-candidatos" in keys
