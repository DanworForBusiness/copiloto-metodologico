"""test_star_rating.py — wrapper del harness node que prueba el primitivo
COMPARTIDO de rating de estrellas (`initStarRatings`, app.js): hover acumulativo
(preview), mouseleave restaura el valor commiteado, click commitea, teclado
(←/↓/→/↑/1..N), input hidden entero (vacío = sin rating). Ver el .node.js.

Incluye además una aserción SSR liviana: Alta y Edición renderizan el control
de estrellas (`data-star-rating`) para `potential_rating`, NO un input de texto
libre (owner closure P3, "no free-text numeric input remains visible")."""
from __future__ import annotations
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_H = Path(__file__).resolve().parent / "star_rating.node.js"
_FIELDS_HTML = (
    Path(__file__).resolve().parents[2] / "webapp" / "templates" / "partials" / "human_capital_profile_fields.html"
).read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible")
def test_star_rating_invariants_hold():
    r = subprocess.run(["node", str(_H)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"star rating invariants failed:\n{r.stdout}\n{r.stderr}"
    assert "ALL STAR RATING INVARIANTS PASSED" in r.stdout


def test_potential_rating_renders_star_control_not_text_input():
    # El macro compartido de campos invoca star_rating() para potential_rating...
    assert "star_rating(" in _FIELDS_HTML
    assert 'name="potential_rating"' in _FIELDS_HTML or "name='potential_rating'" in _FIELDS_HTML
    # ...y ya NO hay un <input type="text" name="potential_rating"> visible.
    assert not re.search(r'<input[^>]*name="potential_rating"[^>]*type="text"', _FIELDS_HTML)
    assert not re.search(r'<input[^>]*type="text"[^>]*name="potential_rating"', _FIELDS_HTML)
