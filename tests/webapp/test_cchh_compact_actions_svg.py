"""Acciones compactas de CCHH = íconos SVG del Hub (`.table-action-btn` + `.chart-tooltip`), nunca enlaces/botones de texto. Guarda estática de las superficies intervenidas (documentos del candidato y ranking de la pestaña Evaluación) y de TODAS las
tablas de la vertical: una celda de fila no admite `.btn-secondary`/`.btn-danger`/`.btn-link` (los botones principales de formulario —Guardar, Cancelar, Crear, Subir documento, Cerrar proceso, Eliminar búsqueda— viven fuera de las celdas)."""
from __future__ import annotations

import re
from pathlib import Path

PARTIALS = Path(__file__).resolve().parents[2] / "webapp" / "templates" / "partials"
PAGES = Path(__file__).resolve().parents[2] / "webapp" / "templates" / "human_capital"
SURFACES = sorted(PARTIALS.glob("human_capital_*_panel.html")) + [PARTIALS / "human_capital_busqueda_detail_body.html"]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_intervened_surfaces_have_no_textual_action_links():
    docs = _text(PARTIALS / "human_capital_profile_documents_panel.html")
    assert not re.search(r"<a\b[^>]*>\s*Abrir en SharePoint\s*</a>", docs)                                               # enlace externo = ícono + tooltip
    assert not re.search(r"data-hc-document-delete-button[^>]*>\s*Eliminar\s*<", docs) and 'class="btn-secondary" data-hc-document-delete-button' not in docs
    assert 'M15 3h6v6' in docs and "table-action-btn--danger" in docs                                                       # SVG de enlace externo y papelera con tinte destructivo
    scoring = _text(PARTIALS / "human_capital_scoring_panel.html")
    assert not re.search(r'<a class="btn-secondary"[^>]*evaluacion-cchh', scoring)


def test_no_table_row_in_the_vertical_uses_text_buttons_for_row_actions():
    offenders = []
    for path in SURFACES:
        for cell in re.findall(r"<td\b.*?</td>", _text(path), flags=re.S):
            if re.search(r'class="[^"]*\b(btn-secondary|btn-danger|btn-link)\b', cell):
                offenders.append(path.name)
    assert not offenders, f"acciones de fila de texto en: {sorted(set(offenders))}"


def test_every_icon_only_action_has_an_accessible_name_and_a_tooltip():
    for path in SURFACES:
        html = _text(path)
        for tag, body in re.findall(r'(<(?:a|button)\b[^>]*class="[^"]*table-action-btn[^"]*"[^>]*>)(.*?)</(?:a|button)>', html, flags=re.S):
            assert "aria-label=" in tag, f"{path.name}: acción icónica sin aria-label: {tag[:120]}"
            assert "chart-tooltip" in body and "<svg" in body, f"{path.name}: acción icónica sin SVG/tooltip: {tag[:120]}"
