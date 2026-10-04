"""test_assessment_open_questions.py — "Preguntas que solo el proyecto puede responder": UN contenedor y UNA tabla.

Antes: un título flotante + una tabla por tipo de hallazgo (+ un disclosure aparte para las preguntas sin hallazgo). Ahora el título es la cabecera del MISMO
contenedor del informe (`details.dashboard-section`, el de los bloques agrupados como "Modelo analítico"), con un resumen compacto y una sola tabla
`Pregunta | Vinculada a | Hallazgo | Ocurrencias`. Solo presentación: los enums (OBSERVATION / REVIEW) y los conteos canónicos no cambian.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from webapp.main import templates
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = "partials/catalog_assessment_report.html"

GROUPS = {"OBS_G": {"title": "Frontera de capas"}, "REV_G": {"title": "Mezcla de vocabulario"}, "REV_G2": {"title": "Etiqueta sin aporte"}}
CTX = {
    "open_questions": 5, "questions_backed_by_findings": 3, "questions_without_findings": 2, "occurrences": 9,
    "questions": [
        {"id": "REV_G", "question": "¿Q revisión A?", "origin": "REVIEW", "occurrence_count": 3},
        {"id": "OBS_G", "question": "¿Q observación?", "origin": "OBSERVATION", "occurrence_count": 4},
        {"id": "CTX_1", "question": "¿Q contexto 1?", "origin": "CONTEXT_ONLY", "occurrence_count": None},
        {"id": "REV_G2", "question": "¿Q revisión B?", "origin": "REVIEW", "occurrence_count": 2},
        {"id": "CTX_2", "question": "¿Q contexto 2?", "origin": "CONTEXT_ONLY", "occurrence_count": None},
    ],
}


@pytest.fixture()
def rows():
    return presenter._context_questions(copy.deepcopy(CTX), GROUPS)


@pytest.fixture()
def doc(tmp_path):
    from tests._modeling_assessment_m7_fixtures import write_inbound
    from tools.catalog.modeling_assessment import shadow
    d, _meta = shadow.assess(write_inbound(tmp_path / "pool"), "Acme", "4AM_ACME")
    return d


def _view(doc):
    entry = type("E", (), {"entry_id": "e", "cliente": "Acme", "dominio": "General", "artefactos": ["4AM_ACME"]})()
    status = svc.AssessmentStatus(svc.CURRENT, "e", "Acme", "4AM_ACME", generated_at="2026-10-04T00:00:00+00:00", profile=doc)
    return presenter.report_view(status, entry, "datasphere_core")


def _html(view):
    return templates.get_template(TEMPLATE).render(v=view)


def _block(html):
    start = html.index('id="asr-preguntas"')
    return html[start:html.index("</details>", start)]


def test_the_visible_link_mapping_is_observation_review_and_project_context(rows):
    by_text = {r["text"]: r for r in rows["context_rows"]}
    assert by_text["¿Q observación?"]["linked_to"] == "Observación"                                  # OBSERVATION → Observación
    assert by_text["¿Q revisión A?"]["linked_to"] == "Caso a revisar" and by_text["¿Q revisión B?"]["linked_to"] == "Caso a revisar"   # REVIEW → Caso a revisar
    assert by_text["¿Q contexto 1?"]["linked_to"] == "Contexto del proyecto" and by_text["¿Q contexto 2?"]["linked_to"] == "Contexto del proyecto"   # sin hallazgo


def test_context_only_rows_use_a_dash_never_an_artificial_zero(rows):
    for r in rows["context_rows"]:
        if r["linked_to"] == "Contexto del proyecto":
            assert r["occurrences"] == "—" and r["finding_title"] is None and r["finding_dom_id"] is None
        else:
            assert isinstance(r["occurrences"], int) and r["occurrences"] > 0 and r["finding_title"]
    html = re.sub(r"\s+", " ", _render_block(rows))
    assert html.count("<td>—</td><td>—</td>") == 2 and "<td>0</td>" not in html                      # Hallazgo y Ocurrencias: —


def test_every_canonical_question_appears_exactly_once_in_a_stable_order(rows):
    texts = [r["text"] for r in rows["context_rows"]]
    assert sorted(texts) == sorted(q["question"] for q in CTX["questions"]) and len(texts) == len(set(texts)) == 5
    assert [r["linked_to"] for r in rows["context_rows"]] == ["Observación", "Caso a revisar", "Caso a revisar", "Contexto del proyecto", "Contexto del proyecto"]
    assert rows["context_backed_n"] + rows["context_independent_n"] == rows["context_open_total"] == CTX["open_questions"]


def test_the_compact_summary_uses_the_canonical_counts_not_the_template(rows):
    assert rows["context_summary"] == "5 preguntas abiertas · 3 vinculadas a hallazgos · 2 de contexto del proyecto"
    one = presenter._context_questions({**CTX, "open_questions": 1, "questions_backed_by_findings": 1, "questions_without_findings": 0,
                                        "questions": CTX["questions"][:1]}, GROUPS)
    assert one["context_summary"] == "1 pregunta abierta · 1 vinculada a hallazgos · 0 de contexto del proyecto"
    tpl = (REPO / "webapp/templates/partials" / "catalog_assessment_report.html").read_text(encoding="utf-8")
    assert "context_summary" in tpl and "questions_backed" not in tpl                                  # la plantilla solo imprime; no cuenta


def _render_block(rows):
    summary = {**rows}
    tpl = templates.env.from_string('{% set s = summary %}' + _extract_block_source())
    return tpl.render(summary=summary)


def _extract_block_source():
    src = (REPO / "webapp/templates/partials" / "catalog_assessment_report.html").read_text(encoding="utf-8")
    start = src.index("  {% if s.context_rows %}")
    end = src.index("  {% endif %}", src.index("</details>", start)) + len("  {% endif %}")
    return src[start:end]


def test_one_container_one_table_one_title_in_the_rendered_report(doc):
    view = _view(doc)
    html = _html(view)
    ctx = doc["summary"]["context_required"]
    if not ctx["open_questions"]:
        pytest.skip("el fixture no abre preguntas")
    assert html.count("Preguntas que solo el proyecto puede responder") == 1                          # el título no se duplica fuera y dentro del contenedor
    block = _block(html)
    assert block.count("<table") == 1 and "<h3>" not in block                                         # una tabla y ningún título flotante
    assert block.count("<tr><td>") == ctx["open_questions"]                                           # cada pregunta, una vez
    assert "Contexto adicional del proyecto" not in html and "Preguntas sin hallazgo asociado" not in html   # el disclosure aparte ya no existe


def test_the_existing_report_container_is_reused_with_no_new_css(doc):
    html = _html(_view(doc))
    block = _block(html)
    head = re.search(r'<details class="dashboard-section" id="asr-preguntas" open>\s*<summary class="dashboard-section__head"><span class="dashboard-section__caret"></span>'
                     r'<span class="dashboard-section__title">Preguntas que solo el proyecto puede responder</span><span class="dashboard-section__count">(\d+)</span></summary>', html)
    assert head and int(head.group(1)) == _view(doc)["summary"]["context_open_n"]                     # título = cabecera del contenedor, con el conteo canónico
    assert '<div class="dashboard-section__body">' in block
    area = re.search(r'<details class="dashboard-section" id="asr-a-[^"]*"|<details class="dashboard-section" id="[^"]+" (?:open)?>', html)
    assert area                                                                                       # el mismo primitivo que ya usan los bloques de área
    classes = set(re.findall(r'class="([^"]+)"', block))
    assert classes <= {"dashboard-section", "dashboard-section__head", "dashboard-section__caret", "dashboard-section__title", "dashboard-section__count",
                       "dashboard-section__body"}, classes
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8")
    assert not re.search(r"asr-pregunt|open-questions|asr-oq", css)                                   # sin CSS propio para esta sección


def test_the_block_explains_the_relationship_in_one_sentence(doc):
    ctx = doc["summary"]["context_required"]
    if not ctx["open_questions"]:
        pytest.skip("el fixture no abre preguntas")
    block = _block(_html(_view(doc)))
    assert "Estas preguntas representan información que el análisis no puede obtener automáticamente del modelo." in block
    assert "Algunas surgen de hallazgos concretos y otras corresponden al contexto general del proyecto." in block


def test_the_visible_block_keeps_the_vocabulary_guard(doc):
    banned = re.compile(r"\b(score|puntaje|puntuaci[oó]n|madurez|banda)\b", re.I)
    ctx = doc["summary"]["context_required"]
    if ctx["open_questions"]:
        assert not banned.search(re.sub(r"<[^>]+>", " ", _block(_html(_view(doc)))))


def test_canonical_json_and_markdown_contracts_are_unchanged(doc):
    from tools.catalog.modeling_assessment import render_md
    ctx = doc["summary"]["context_required"]
    assert {"open_questions", "questions_backed_by_findings", "questions_without_findings", "questions", "occurrences"} <= set(ctx)
    assert all(q["origin"] in ("OBSERVATION", "REVIEW", "CONTEXT_ONLY") for q in ctx["questions"])                    # los enums siguen siendo los canónicos
    md = render_md.render_markdown(doc)
    for q in ctx["questions"]:
        assert q["question"] in md
