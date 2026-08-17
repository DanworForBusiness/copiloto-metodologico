"""
test_reporting_hub_callout.py — HUB TRANSVERSAL PRIMITIVE TEST (owner
directive): Horas Productivas reusa el MISMO primitivo de presentación de
callout que Copilot (`content_service.py::callout()`), movido de
content.css a tools.css (shared Hub layer) — ver reporting_hub_callout.node.js
para las invariantes del lado Reporting/JS. Los tests de Copilot en
test_content_service.py (sin cambios) protegen que `{{callout}}` siga
renderizando igual.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_hub_callout.node.js"
_CONTENT_CSS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "content.css").read_text(encoding="utf-8")
_TOOLS_CSS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_hub_callout_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Hub callout invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL HUB CALLOUT INVARIANTS PASSED" in result.stdout


def test_callout_presentation_lives_in_shared_tools_css_not_content_css():
    # El primitivo GENÉRICO (.callout/.callout__head/.callout__icon/
    # .callout__title/.callout__body) vive en tools.css (Hub compartido) —
    # content.css (Copilot/Academy-specific) ya NO lo define, solo el
    # parser de Markdown `{{callout:...}}` (content_service.py) sigue ahí.
    assert ".callout {" in _TOOLS_CSS
    assert ".callout__body" in _TOOLS_CSS
    assert ".callout {" not in _CONTENT_CSS
    assert ".callout__body" not in _CONTENT_CSS
