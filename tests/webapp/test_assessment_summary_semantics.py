"""test_assessment_summary_semantics.py — el "Resumen" del informe individual describe, por separado, los mismos conceptos que los KPI (Gate 2.1.1).

Cobertura estricta · Cobertura inclusiva · Confianza · Revisiones requeridas (= ocurrencias de los grupos de revisión) · Contexto abierto
(= tipos de pregunta distintos que solo el proyecto puede responder). Antes la fila "Contexto requerido" describía hallazgos/grupos de revisión y no
había fila de revisiones. Sin cambiar cálculos; Markdown y HTML usan las mismas funciones; sin vocabulario de puntaje.
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
    assert [r[0] for r in rows] == ["Cobertura estricta", "Cobertura inclusiva", "Confianza", "Revisiones requeridas", "Contexto abierto", "Observaciones"]
    assert "Contexto requerido" not in str(rows)                                                      # un solo término: "Contexto abierto"


def test_revisiones_and_contexto_use_their_own_units_and_copy(doc, view):
    rows = {r[0]: r for r in _summary_table(templates.get_template("partials/catalog_assessment_report.html").render(v=view))}
    review_occ = sum(g["occurrence_count"] for g in doc["review_required"])
    ctx = doc["summary"]["context_required"]
    rev, ctxrow = rows["Revisiones requeridas"], rows["Contexto abierto"]
    assert rev[1] == str(review_occ)                                                                  # unidad: OCURRENCIAS de revisión
    assert ctxrow[1] == presenter._plural(ctx["open_questions"], "pregunta de contexto abierta", "preguntas de contexto abiertas")   # unidad: tipos de pregunta
    assert "ocurrencia" in rev[2] and "grupo" in rev[2] and "revisión de una persona" in rev[2]
    for forbidden in ("grupo de observación", "grupos de observación", "grupo de revisión", "grupos de revisión", "Hallazgos"):
        assert forbidden not in ctxrow[2], forbidden                                                  # "Contexto abierto" ya no describe hallazgos/revisiones
    assert "Cuenta tipos de pregunta, no ocurrencias" in ctxrow[2] and f"{ctx['questions_without_findings']} corresponden" in ctxrow[2]
    for banned_text in ("pregunta de contexto", "tipos de pregunta"):
        assert banned_text not in rev[2]                                                              # y revisiones no habla de preguntas


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
    assert f"- **Revisiones requeridas:** {head}" in md and f"  - {detail}" in md
    chead, cdetail = render_md.context_open_text(doc["summary"]["context_required"])
    assert f"- **Contexto abierto:** {chead}; {cdetail}" in md
    assert view["summary"]["context_reading"] == cdetail and view["summary"]["review_reading"] == f"{head}. {detail}"


def test_the_visible_report_keeps_the_vocabulary_guard(view, doc):
    from tools.catalog.modeling_assessment import render_md
    html = re.sub(r"<[^>]+>", " ", templates.get_template("partials/catalog_assessment_report.html").render(v=view))
    assert not BANNED.search(html), BANNED.search(html).group(0)
    assert not BANNED.search(render_md.render_markdown(doc)), BANNED.search(render_md.render_markdown(doc)).group(0)
