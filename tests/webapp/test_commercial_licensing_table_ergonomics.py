"""test_commercial_licensing_table_ergonomics.py — Director §18-27 (ver
HANDOFF commercial_licensing_20260925): reorder + resize de columnas para
Oportunidades de licenciamiento y Perfil de licenciamiento por cliente.

El contrato TABLE-AGNOSTIC del primitivo compartido (initDraggableColumns/
initResizableColumns, app.js) se prueba en test_app_table_ergonomics.py contra
una tabla sintética sin relación con Comercial. Acá se prueba el WIRING
específico: catálogo de columnas de cada tabla, namespaces de preferencia
independientes, identidad por `data-column-key` (nunca posición) en el
colgroup, la fila de Total order-aware, y que ninguna de las dos acciones de
Oportunidades (fila -> Detalle Oportunidades, botón -> detalle de cuenta) se
rompe por un reorder.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "commercial_licensing_table_ergonomics.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_licensing_table_ergonomics_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Commercial licensing table ergonomics failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL COMMERCIAL LICENSING TABLE ERGONOMICS INVARIANTS PASSED" in result.stdout


# ============================================================================
# CCHH UX homologation -- shared drag-state CSS contract (tools.css), never
# duplicated per vertical/table again. Text-level checks: no CSS engine
# available in the node mini-sandbox above (see section I there for the
# structural half of this same proof).
# ============================================================================
_TOOLS_CSS = Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css"
_COMMERCIAL_CSS = Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial.css"


def test_shared_drag_state_selector_lives_in_tools_css():
    css = _TOOLS_CSS.read_text(encoding="utf-8")
    assert '[data-draggable-columns-key] th[data-column-key][draggable="true"] {' in css
    assert '[data-draggable-columns-key] th[data-column-key][draggable="true"].is-dragging {' in css
    assert '[data-draggable-columns-key] th[data-column-key][draggable="true"].is-drop-target {' in css


def test_commercial_css_no_longer_declares_the_superseded_vertical_rule():
    css = _COMMERCIAL_CSS.read_text(encoding="utf-8")
    assert "commercial-licprofile-th--draggable" not in css
    assert "commercial-licencias-th--draggable" not in css
