"""test_assessment_kpi_help.py — ayuda OPCIONAL (i) de los KPI del informe individual de Assessment (Gate 2.1).

Contrato:
  * el (i) es el MISMO primitivo del Tablero Integral (`.activity-kpi__info` + `.chart-tooltip` hermana; hover/foco/tap): cero CSS/JS propio;
  * `help` es opcional por KPI: sin ella el tile es idéntico al de siempre; con ella aparece el (i);
  * la copy vive en el presentador (`KPI_HELP`), derivada de los contratos canónicos, NO en la plantilla; el JSON/Markdown canónicos no cambian;
  * la copy no usa vocabulario de puntaje: cobertura, confianza y revisiones NO son una nota de calidad ni de madurez.
"""
from __future__ import annotations

import re
from pathlib import Path

from webapp.main import templates
from webapp.services import assessment_presenter as presenter

REPO = Path(__file__).resolve().parents[2]
MACROS = "partials/assessment_report_macros.html"


def _render_tile(k: dict) -> str:
    env = templates.env
    return env.from_string('{% from "' + MACROS + '" import kpi_tile %}{{ kpi_tile(k) }}').render(k=k)


def test_a_kpi_with_help_renders_the_shared_hub_info_affordance():
    html = _render_tile({"label": "Cobertura estricta", "value": "90.0%", "help": ["Línea uno.", "Línea dos."]})
    assert html.startswith('<div class="activity-kpi"><span class="activity-kpi__value">90.0%</span><span class="activity-kpi__label">Cobertura estricta</span>')
    assert '<button type="button" class="activity-kpi__info" aria-label="Información sobre Cobertura estricta: Línea uno. Línea dos.">' in html
    assert '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 7.8v.2"/></svg>' in html     # mismo ícono del Hub
    assert '</button><span class="chart-tooltip">Línea uno.<br>Línea dos.</span></div>' in html                                              # caja hermana del botón; sin <p>
    assert "<p>" not in html                                                                                                                  # `.methodology p` pisaría el color/tamaño del tooltip del Hub
    reference = (REPO / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")
    assert 'class="activity-kpi__info"' in reference and '<circle cx="12" cy="12" r="9"/>' in reference and 'class="chart-tooltip"' in reference   # es el del Tablero


def test_a_kpi_without_help_is_byte_identical_to_the_previous_markup():
    assert _render_tile({"label": "Confianza", "value": "ALTA"}) == '<div class="activity-kpi"><span class="activity-kpi__value">ALTA</span><span class="activity-kpi__label">Confianza</span></div>'
    assert "activity-kpi__info" not in _render_tile({"label": "X", "value": 1, "help": []})


def test_the_four_report_kpis_keep_labels_and_values_and_gain_help_from_the_presenter(_report_view):
    kpis = _report_view["kpis"]
    assert [k["label"] for k in kpis] == ["Cobertura estricta", "Confianza", "Casos a revisar", "Preguntas abiertas"]
    s = _report_view["summary"]
    assert [k["value"] for k in kpis] == [s["coverage_strict"], s["confidence"], s["review_occurrences"], s["context_open_n"]]       # valores sin cambios
    assert [k["help"] for k in kpis] == [list(presenter.KPI_HELP[key]) for key in ("coverage_strict", "confidence", "review_required", "context_open")]


def test_the_copy_lives_in_the_presenter_not_in_the_templates():
    for lines in presenter.KPI_HELP.values():
        for line in lines:
            for rel in ("catalog_assessment_report.html", "assessment_report_macros.html", "client_assessment_report.html"):
                assert line not in (REPO / "webapp" / "templates" / "partials" / rel).read_text(encoding="utf-8"), rel


def test_the_copy_follows_the_canonical_contract_and_never_reads_as_a_score():
    from tools.catalog.modeling_assessment import profile as ma_profile
    assert "REQUERIDA" in ma_profile.COVERAGE_MEANING and "No es una afirmación sobre la calidad" in ma_profile.COVERAGE_MEANING
    assert "independiente de la cobertura" in ma_profile.CONFIDENCE_MEANING and "No es una afirmación sobre la calidad" in ma_profile.CONFIDENCE_MEANING
    h = presenter.KPI_HELP
    assert "requerida y aplicable" in h["coverage_strict"][0] and "solo en parte no suma" in h["coverage_strict"][0]      # estricta = solo lo evaluado por completo
    assert "evidencia que sí se evaluó" in h["confidence"][0] and "independiente de la cobertura" in h["confidence"][1]
    assert "ocurrencias" in " ".join(h["review_required"]) and "criterio técnico del consultor" in h["review_required"][0]      # ocurrencias; requiere criterio del consultor
    assert "No implican defectos confirmados" in h["review_required"][0]
    assert "equipo del proyecto" in h["context_open"][0] and "varios hallazgos" in h["context_open"][0]                     # la misma pregunta puede asociarse a varios hallazgos
    assert "tipos de pregunta, no ocurrencias" in h["context_open"][1]                                                      # unidad: tipos de pregunta
    banned = re.compile(r"\b(score|scoring|puntaje|puntuaci[oó]n|nota|madurez|banda|ranking|rating)\b", re.I)
    for key, lines in h.items():
        assert 1 <= len(lines) <= 3 and all(len(x) < 230 for x in lines), key
        assert not banned.search(" ".join(lines)), key


def test_no_new_css_or_js_was_added_for_the_tooltip():
    macros = (REPO / "webapp" / "templates" / MACROS).read_text(encoding="utf-8")
    classes = set(re.findall(r'class="([^"]+)"', macros[macros.index("macro kpi_tile"):]))
    assert classes <= {"activity-kpi", "activity-kpi__value", "activity-kpi__label", "activity-kpi__info", "chart-tooltip"}   # solo primitivos del Hub
    css = (REPO / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    assert not re.search(r"\.(asr|asm)-[a-z_-]*(info|tooltip|help)", css)                                                      # sin CSS propio del Assessment
    assert "KPI_HELP" not in (REPO / "webapp" / "static" / "app.js").read_text(encoding="utf-8")


def test_the_canonical_json_and_markdown_are_unchanged_by_the_help(_doc):
    from tools.catalog.modeling_assessment import render_md
    md = render_md.render_markdown(_doc)
    import json
    blob = json.dumps(_doc, ensure_ascii=False)
    for lines in presenter.KPI_HELP.values():
        assert lines[0] not in md and lines[0] not in blob                                                                    # la ayuda es solo de presentación


def test_the_rendered_individual_report_shows_four_info_icons_and_the_portfolio_is_untouched(_report_view):
    html = templates.get_template("partials/catalog_assessment_report.html").render(v=_report_view)
    top = html[:html.index('id="asr-resumen"')]                                                    # los 4 indicadores superiores (las áreas tienen los suyos)
    assert top.count('class="activity-kpi__info"') == 4 and top.count('<span class="chart-tooltip">') == 4 and "<p>" not in "".join(re.findall(r'<span class="chart-tooltip">.*?</span>', top))
    for k in _report_view["kpis"]:
        assert f'<span class="activity-kpi__label">{k["label"]}</span>' in html
    portfolio = (REPO / "webapp" / "templates" / "partials" / "client_assessment_report.html").read_text(encoding="utf-8")
    # el Portfolio no gana ayuda (i): puede reusar el macro de tile (Gate 4), pero jamás le pasa `help` ni trae el primitivo del (i)
    assert "activity-kpi__info" not in portfolio and "help" not in re.sub(r"\{#.*?#\}", "", portfolio, flags=re.S)


# ── fixtures ────────────────────────────────────────────────────────────────
import pytest  # noqa: E402


@pytest.fixture()
def _doc(tmp_path):
    from tests._modeling_assessment_m7_fixtures import write_inbound
    from tools.catalog.modeling_assessment import shadow
    doc, _meta = shadow.assess(write_inbound(tmp_path / "pool"), "Acme", "4AM_ACME")
    return doc


@pytest.fixture()
def _report_view(_doc):
    from webapp.services import assessment_service as svc
    entry = type("E", (), {"entry_id": "e", "cliente": "Acme", "dominio": "General", "artefactos": ["4AM_ACME"]})()
    status = svc.AssessmentStatus(svc.CURRENT, "e", "Acme", "4AM_ACME", generated_at="2026-10-04T00:00:00+00:00", profile=_doc)
    return presenter.report_view(status, entry, "datasphere_core")
