"""test_assessment_summary_semantics.py — el "Resumen" del informe individual describe, por separado, los mismos conceptos que los KPI (Gate 2.1.1).

Cobertura estricta · Cobertura inclusiva · Confianza · Casos a revisar (= ocurrencias de los grupos de revisión) · Preguntas abiertas
(= tipos de pregunta distintos que requieren información del proyecto). Solo vocabulario visible: los contratos internos (`review_required`,
`occurrence_count`, `open_questions`) no cambian. Antes se decía "Revisiones requeridas" / "Contexto abierto", fáciles de confundir. Sin cambiar cálculos; Markdown y HTML usan las mismas funciones; sin vocabulario de puntaje.
"""
from __future__ import annotations

import re

import pytest

from webapp.main import templates
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc

BANNED = re.compile(r"\b(score|scores|scoring|puntaje|puntajes|puntuaci[oó]n|madurez|maturity|banda|bandas|PLENO|BUENO|PARCIAL)\b", re.I)


@pytest.fixture()
def doc(tmp_path):
    from tests._modeling_assessment_m7_fixtures import write_inbound
    from tools.catalog.modeling_assessment import shadow
    d, _meta = shadow.assess(write_inbound(tmp_path / "pool"), "Acme", "4AM_ACME")
    return d


@pytest.fixture()
def view(doc):
    entry = type("E", (), {"entry_id": "e", "cliente": "Acme", "dominio": "General", "artefactos": ["4AM_ACME"]})()
    status = svc.AssessmentStatus(svc.CURRENT, "e", "Acme", "4AM_ACME", generated_at="2026-10-04T00:00:00+00:00", profile=doc)
    return presenter.report_view(status, entry, "datasphere_core")


def _summary_table(html: str):
    sec = html[html.index('id="asr-resumen"'):]
    sec = sec[: sec.index("</table>")]
    return [[re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td>(.*?)</td>", r, re.S)] for r in re.findall(r"<tr>(.*?)</tr>", sec, re.S) if "<td>" in r]


def test_resumen_rows_are_the_separate_canonical_concepts(view):
    rows = _summary_table(templates.get_template("partials/catalog_assessment_report.html").render(v=view))
    assert [r[0] for r in rows] == ["Cobertura estricta", "Cobertura inclusiva", "Confianza", "Casos a revisar", "Preguntas abiertas", "Observaciones"]
    assert "Contexto requerido" not in str(rows) and "Contexto abierto" not in str(rows) and "Revisiones requeridas" not in str(rows)   # un solo vocabulario


def test_casos_and_preguntas_use_their_own_units_and_copy(doc, view):
    rows = {r[0]: r for r in _summary_table(templates.get_template("partials/catalog_assessment_report.html").render(v=view))}
    review_occ = sum(g["occurrence_count"] for g in doc["review_required"])
    ctx = doc["summary"]["context_required"]
    rev, ctxrow = rows["Casos a revisar"], rows["Preguntas abiertas"]
    assert rev[1] == presenter._plural(review_occ, "caso")                                            # unidad: OCURRENCIAS de revisión
    assert ctxrow[1] == presenter._plural(ctx["open_questions"], "pregunta")                          # unidad: tipos de pregunta
    n_groups = len(doc["review_required"])
    assert f"identificó {presenter._plural(review_occ, 'caso concreto', 'casos concretos')}" in rev[2] and "validación humana" in rev[2]
    assert f"agrupados en {presenter._plural(n_groups, 'tipo de revisión', 'tipos de revisión')}" in rev[2]
    assert "defectos confirmados" in rev[2] or "defecto confirmado" in rev[2]
    for forbidden in ("grupo de observación", "grupos de observación", "Hallazgos", "grupo de revisión"):
        assert forbidden not in ctxrow[2], forbidden                                                  # las preguntas no describen revisiones
    assert f"{ctx['questions_backed_by_findings']} " in ctxrow[2] and "con hallazgos detectados" in ctxrow[2]
    assert f"{ctx['questions_without_findings']} " in ctxrow[2] and "sin hallazgo asociado" in ctxrow[2]
    assert "vinculada a varias ocurrencias" in ctxrow[2]                                              # una pregunta puede abarcar varias ocurrencias
    assert "pregunta" not in rev[2].lower() and "tipos de pregunta" not in rev[2]                     # y los casos no hablan de preguntas


def test_the_summary_states_the_relationship_once_and_concisely(doc, view):
    html = templates.get_template("partials/catalog_assessment_report.html").render(v=view)
    from tools.catalog.modeling_assessment import render_md
    assert html.count("Los casos a revisar son hallazgos concretos que requieren validación") == 1
    assert render_md.SUMMARY_RELATION_TEXT in html or "Los casos a revisar son hallazgos concretos" in html
    assert view["summary"]["relation_text"] == render_md.SUMMARY_RELATION_TEXT and len(render_md.SUMMARY_RELATION_TEXT) < 260


def test_the_visible_labels_are_the_same_in_kpis_summary_and_markdown(doc, view):
    from tools.catalog.modeling_assessment import render_md
    md = render_md.render_markdown(doc)
    assert [k["label"] for k in view["kpis"]][2:] == ["Casos a revisar", "Preguntas abiertas"]
    assert "- **Casos a revisar:**" in md and "- **Preguntas abiertas:**" in md
    assert "Revisiones requeridas" not in md and "Contexto abierto" not in md and "Contexto requerido" not in md
    assert "review_required" in doc and "open_questions" in doc["summary"]["context_required"]


def test_coverage_and_confidence_values_are_unchanged(doc, view):
    rows = {r[0]: r for r in _summary_table(templates.get_template("partials/catalog_assessment_report.html").render(v=view))}
    cov, conf = doc["summary"]["coverage"], doc["summary"]["confidence"]
    assert rows["Cobertura estricta"][1] == presenter.P.percent_text(cov["strict"]) and rows["Cobertura inclusiva"][1] == presenter.P.percent_text(cov["inclusive"])
    assert rows["Confianza"][1] == presenter.P.confidence_level_text(conf["level"])
    assert [k["value"] for k in view["kpis"]][:2] == [rows["Cobertura estricta"][1], rows["Confianza"][1]]            # KPI y Resumen coinciden


def test_the_summary_and_the_markdown_share_the_same_definitions(doc, view):
    from tools.catalog.modeling_assessment import render_md
    md = render_md.render_markdown(doc)
    head, detail = render_md.review_required_text(sum(g["occurrence_count"] for g in doc["review_required"]), len(doc["review_required"]))
    assert f"- **Casos a revisar:** {head}" in md and f"  - {detail}" in md
    chead, cdetail = render_md.context_open_text(doc["summary"]["context_required"])
    assert f"- **Preguntas abiertas:** {chead}" in md and f"  - {cdetail}" in md
    assert view["summary"]["context_reading"] == cdetail and view["summary"]["review_reading"] == detail.replace("*", "")
    assert "*" not in view["summary"]["review_reading"] and "(detalle en Revisión requerida)" in view["summary"]["review_reading"]   # sin marcas de Markdown en el HTML
    assert view["summary"]["review_open"] == head and view["summary"]["context_open"] == chead


def test_the_visible_report_keeps_the_vocabulary_guard(view, doc):
    from tools.catalog.modeling_assessment import render_md
    html = re.sub(r"<[^>]+>", " ", templates.get_template("partials/catalog_assessment_report.html").render(v=view))
    assert not BANNED.search(html), BANNED.search(html).group(0)
    assert not BANNED.search(render_md.render_markdown(doc)), BANNED.search(render_md.render_markdown(doc)).group(0)
