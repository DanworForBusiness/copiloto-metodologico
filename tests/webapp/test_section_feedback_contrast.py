"""test_section_feedback_contrast.py — contrato mínimo source-level: el
textarea de `.section-feedback__form` (Dislike -> comentario, compartido por
`section_feedback.html` y `catalog_section_feedback.html`) debe fijar un
color de texto EXPLÍCITO en tema claro, no `var(--ink)`.

Root cause (forensic, ver ticket "bug transversal de contraste en feedback
like/dislike"): este primitive nunca vive dentro de `.copilot-panel` -- el
único scope donde `--ink` se retematiza a claro (`tools.css`). Fuera de ahí,
`--ink` cae al valor GLOBAL oscuro de `app.css:root` (#e4e4ec) sobre el fondo
claro propio de este textarea (#f7f7f4) -> texto casi invisible en tema
claro. El override de tema oscuro (`.content-theme-dark ...`) ya funcionaba
(cae correctamente al mismo global oscuro, ahora bien emparejado con un
fondo oscuro) -- este test fija que el contrato NO vuelva a perder
exclusivamente el lado claro.

No es un test visual/screenshot -- lee el CSS fuente, mismo patrón que
test_human_capital_ux_parity_slice2.py::test_d2_no_positional_alignment_selectors_in_css.
"""
from __future__ import annotations

import re
from pathlib import Path

TOOLS_CSS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")


def _rule_body(css: str, selector: str, *, at_line_start: bool = False) -> str:
    prefix = r"^" if at_line_start else ""
    pattern = prefix + re.escape(selector) + r"\s*\{([^}]*)\}"
    m = re.search(pattern, css, re.MULTILINE)
    assert m, f"selector no encontrado en tools.css: {selector!r}"
    return m.group(1)


def test_light_theme_textarea_sets_an_explicit_non_token_color():
    body = _rule_body(TOOLS_CSS, ".content-theme-light .section-feedback__form textarea")
    assert re.search(r"color\s*:\s*#[0-9a-fA-F]{6}", body), (
        "el color de texto debe ser un literal explícito, no var(--ink) -- "
        "--ink sólo se retematiza a claro dentro de .copilot-panel, y este "
        "primitive nunca vive ahí (ver forensic)"
    )
    assert "var(--ink)" not in body


def test_dark_theme_override_is_unchanged():
    body = _rule_body(TOOLS_CSS, ".content-theme-dark .section-feedback__form textarea")
    assert "background" in body
    assert "color" in body


def test_base_rule_still_defines_the_light_gray_background():
    # El fix sólo tocó `color` -- el background base (#f7f7f4, correcto para
    # tema claro) no debe cambiar.
    body = _rule_body(TOOLS_CSS, ".section-feedback__form textarea", at_line_start=True)
    assert "#f7f7f4" in body
