"""
test_human_capital_catalog_refresh_auth.py — CCHH Catalog Foundation: el
refresh de catálogos usa el token de LECTURA del Hub (`dwGraphToken`,
User.Read/Sites.Read.All), NUNCA el HC document write token
(`dwHumanCapitalDocumentWriteToken`, Files.SelectedOperations.Selected) que
`human_capital_profile_documents_panel.html` sí necesita.

MISMO espíritu que `test_human_capital_document_write_scope.py` (que prueba
lo inverso: que el form de documentos NUNCA use `dwGraphToken`) -- acá se
prueba la mitad simétrica para `catalogos.html`."""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = _ROOT / "webapp" / "templates" / "human_capital" / "catalogos.html"


def _script_body() -> str:
    text = _TEMPLATE.read_text(encoding="utf-8")
    # Solo el <script> real, nunca el comentario Jinja que lo explica (mismo
    # criterio que test_human_capital_document_write_scope.py).
    return text.split("<script>", 1)[1]


def test_catalogos_refresh_form_declares_the_read_marker():
    text = _TEMPLATE.read_text(encoding="utf-8")
    assert "data-hc-graph-read-form" in text
    assert 'name="graph_token"' in text


def test_catalogos_refresh_uses_the_hub_wide_read_token_function():
    script = _script_body()
    assert "window.dwGraphToken" in script


def test_catalogos_refresh_never_uses_the_document_write_capable_token():
    # El refresh de catálogos es un READ de Graph -- pedir el token de
    # escritura documental sería sobre-alcance de permisos y, además,
    # acoplaría el catálogo al mismo BLOCKED_EXTERNAL que documentos/CV.
    script = _script_body()
    assert "dwHumanCapitalDocumentWriteToken" not in script


def test_catalogos_refresh_form_intercepts_only_the_first_submit():
    # MISMO idioma que el form de documentos: un dataset flag evita
    # interceptar el segundo submit real (el que ya trae el token puesto).
    script = _script_body()
    assert "hcTokenReady" in script
