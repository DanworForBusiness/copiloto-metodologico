"""
test_human_capital_create_cv_terminal_outcome.py — wrapper pytest del harness
node que prueba el CONTRATO de outcome terminal del flujo create-with-CV
(CCHH): el clic con archivo SIEMPRE termina en EXACTAMENTE un `form.submit()`,
con un BOUND que neutraliza una promesa de token pendiente (el defecto raíz
probado). Ver human_capital_create_cv_terminal_outcome.node.js."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "human_capital_create_cv_terminal_outcome.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_create_with_cv_terminal_outcome_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        "CCHH create-with-CV terminal-outcome invariants failed:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "ALL CCHH CREATE-CV TERMINAL OUTCOME INVARIANTS PASSED" in result.stdout


def test_new_form_uses_silent_only_and_bounded_submit():
    # Guardas de código (además del harness de comportamiento): el submit del
    # alta pide el token SILENT-ONLY y acota el flujo con un bound.
    src = (Path(__file__).resolve().parents[2] / "webapp" / "templates" / "partials"
           / "human_capital_profile_new_form.html").read_text(encoding="utf-8")
    assert "dwHumanCapitalDocumentWriteToken({ interactive: false })" in src
    assert "Promise.race" in src
    assert "data-hc-token-bound-ms" in src
    assert "if (submitted) return;" in src  # exactly-one submit guard
