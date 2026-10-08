"""test_assessment_area_scope.py — arquitectura de la información de "Arquitectura & Modelado": cada nivel dice lo suyo.

  SECCIÓN [02]  → resumen TRANSVERSAL: composición del cierre por tipo de vista (hecho canónico `am.view_type_composition`), UNA sola vez;
  CONTENEDOR DE ÁREA → solo su evidencia: indicadores propios (etiquetas fieles + ayuda (i) con el mismo componente que los KPI superiores), reglas y hallazgos.

Las poblaciones de vistas por área (rechazadas: se solapan y sugerían pertenencia exclusiva) NO existen en el perfil ni en la presentación.
Sin cálculos nuevos, sin señales ni puntajes, sin CSS/JS nuevo.
"""
from __future__ import annotations

import copy
import inspect
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.catalog.modeling_assessment import model as M
from tools.catalog.modeling_assessment import profile as ma_profile
from tools.catalog.modeling_assessment import render_md
from tools.catalog.modeling_assessment.facts_support import make_fact
from tools.catalog.modeling_assessment.model import Area, EvidenceClass
from tools.catalog.modeling_assessment.signals import SIGNAL_REGISTRY, FactIndex
from webapp.main import templates
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = "partials/catalog_assessment_report.html"
TEMPLATE_PATH = REPO / "webapp/templates/partials/catalog_assessment_report.html"
OLD_LABELS = ("Pasos de linaje con delta de contrato", "Artefactos con campos derivados", "Campos derivados de nuevo en un ancestro del linaje",
              "Dimensiones declaradas en el cierre", "Artefactos de composición (dos o más proveedores de linaje)",
              "Artefactos contenidos también en otros modelos analíticos")
OLD_RULE_TITLE, NEW_RULE_TITLE = "Campo derivado de nuevo en otra capa", "Derivación repetida en el linaje"


@pytest.fixture()
def doc(tmp_path):
    from tests._modeling_assessment_m7_fixtures import write_inbound
    from tools.catalog.modeling_assessment import shadow
    d, _meta = shadow.assess(write_inbound(tmp_path / "pool"), "Acme", "4AM_ACME")
    return d


def _view(d):
    entry = type("E", (), {"entry_id": "e", "cliente": "Acme", "dominio": "General", "artefactos": ["4AM_ACME"]})()
    status = svc.AssessmentStatus(svc.CURRENT, "e", "Acme", "4AM_ACME", generated_at="2026-10-04T00:00:00+00:00", profile=d)
    return presenter.report_view(status, entry, "datasphere_core")


def _html(view):
    return templates.get_template(TEMPLATE).render(v=view)


def _comp(root="AM"):
    return SimpleNamespace(scope=SimpleNamespace(root=root, root_type="analytic-model"), findings=[])


def _derived(subject, n):
    return make_fact("propagation.derived_fields", Area.PROPAGATION, subject, {"n_derived": n, "derived_fields": [{"field": f"F{i}"} for i in range(n)]}, EvidenceClass.A)


# ── etiquetas fieles (sin cambiar cálculos) ─────────────────────────────────
def test_the_visible_labels_are_the_agreed_ones_in_profile_html_and_markdown(doc):
    expected = {"lineage_steps_evaluated": "Pasos de linaje evaluados", "artifacts_with_derived_fields": "Artefactos con derivaciones",
                "rederived_field_occurrences": "Derivaciones repetidas", "declared_dimensions": "Dimensiones declaradas",
                "composition_artifacts": "Artefactos de composición", "cross_model_reuse_artifacts": "Artefactos con reuso"}
    assert render_md._HIGHLIGHT_LABELS == expected
    for area in ("PROPAGATION", "HARMONIZATION"):
        for h in next(a for a in doc["areas"] if a["id"] == area)["highlights"]:
            if h["id"] in expected:
                assert h["label"] == expected[h["id"]]
    html, md = _html(_view(doc)), render_md.render_markdown(doc)
    for text in (html, md):
        for old in OLD_LABELS:
            assert old not in text, old
    assert "Pasos de linaje evaluados" in html and "Pasos de linaje evaluados" in md


def test_the_numeric_values_are_unchanged_and_an_identical_edge_still_counts():
    ident = make_fact("propagation.contract_delta", Area.PROPAGATION, "A->B", {"descriptors": {"schema_identical": True}}, EvidenceClass.B)
    changed = make_fact("propagation.contract_delta", Area.PROPAGATION, "B->C", {"descriptors": {"schema_identical": False}}, EvidenceClass.B)
    final = make_fact("am.contract_delta", Area.ANALYTIC_MODEL, "C->AM", {"descriptors": {"schema_identical": False}}, EvidenceClass.B)
    out = {h["id"]: h for h in ma_profile._highlights(FactIndex([ident, changed, final]), _comp(), M.Area.PROPAGATION)}
    assert out["lineage_steps_evaluated"]["value"] == 3 and out["lineage_steps_evaluated"]["label"] == "Pasos de linaje evaluados"   # también la arista idéntica


def test_artifacts_with_derivations_counts_each_artifact_once_regardless_of_its_fields():
    out = {h["id"]: h for h in ma_profile._highlights(FactIndex([_derived("V1", 3), _derived("V2", 1), _derived("V3", 0)]), _comp(), M.Area.PROPAGATION)}
    assert out["artifacts_with_derived_fields"]["value"] == 2


def test_repeated_derivations_count_occurrences_not_unique_fields():
    occ = [{"field": "X", "consumer": "C", "provider": "P1", "lineage_distance": 1, "adjacent": True},
           {"field": "X", "consumer": "C", "provider": "P2", "lineage_distance": 2, "adjacent": False},
           {"field": "Y", "consumer": "C", "provider": "P1", "lineage_distance": 1, "adjacent": True}]
    red = make_fact("propagation.rederivation", Area.PROPAGATION, "closure", {"occurrences": occ, "adjacent_count": 2}, EvidenceClass.B)
    out = {h["id"]: h for h in ma_profile._highlights(FactIndex([red]), _comp(), M.Area.PROPAGATION)}
    assert out["rederived_field_occurrences"]["value"] == 3 and len({o["field"] for o in occ}) == 2


def test_harmonization_labels_stay_faithful_to_the_evaluated_candidate_population():
    help_ = presenter.AREA_KPI_HELP
    assert "dimensiones y destinos de asociación evaluados" in help_["cross_model_reuse_artifacts"][0]
    assert "No representa todos los artefactos del cierre" in help_["cross_model_reuse_artifacts"][0]
    assert "No se infieren por nombre ni por capa" in help_["declared_dimensions"][0]
    assert "no implica por sí solo una desviación metodológica" in help_["composition_artifacts"][0]


def test_help_copy_is_canonical_concise_and_never_reads_as_a_score_or_a_human_reviewer():
    banned = re.compile(r"\b(score|scoring|puntaje|puntuaci[oó]n|madurez|banda|ranking|rating|human[oa]s?|persona)\b", re.I)
    assert "pasos de linaje, no artefactos" in presenter.AREA_KPI_HELP["lineage_steps_evaluated"][0] and "idéntica" in presenter.AREA_KPI_HELP["lineage_steps_evaluated"][0]
    assert "un cero no garantiza ausencia" in presenter.AREA_KPI_HELP["artifacts_with_derived_fields"][1]
    assert "El consultor determina" in presenter.AREA_KPI_HELP["rederived_field_occurrences"][0] and "aguas arriba" in presenter.AREA_KPI_HELP["rederived_field_occurrences"][0]
    for lines in presenter.AREA_KPI_HELP.values():
        assert 1 <= len(lines) <= 2 and all(len(x) < 260 for x in lines)
        assert not banned.search(" ".join(lines))
        assert not re.search(r"otra capa|otras capas", " ".join(lines), re.I)                                # la implementación NO compara capas


# ── regla: redacción pública, ids internos intactos ─────────────────────────
def test_the_repeated_derivation_rule_is_named_the_same_everywhere_and_the_legacy_id_is_a_read_alias(doc):
    from tools.catalog.modeling_assessment.model import LEGACY_SIGNAL_ALIASES
    assert ma_profile.DISPLAY["REPEATED_DERIVATION_IN_LINEAGE"][0] == NEW_RULE_TITLE
    groups = [g for g in doc["review_required"] if g["id"] == "REPEATED_DERIVATION_IN_LINEAGE"]
    rules = [r for r in doc["rule_results"] if r["rule"] == "REPEATED_DERIVATION_IN_LINEAGE"]
    assert rules and all(r["title"] == NEW_RULE_TITLE for r in rules) and all(g["title"] == NEW_RULE_TITLE for g in groups)
    assert "REPEATED_DERIVATION_IN_LINEAGE" in {getattr(s, "signal_id", s) for s in SIGNAL_REGISTRY}              # id canónico vigente
    assert "CROSS_LAYER_REDERIVATION" not in {getattr(s, "signal_id", s) for s in SIGNAL_REGISTRY}                # el identificador engañoso ya no se emite
    assert LEGACY_SIGNAL_ALIASES["CROSS_LAYER_REDERIVATION"] == "REPEATED_DERIVATION_IN_LINEAGE"
    view, md = _view(doc), render_md.render_markdown(doc)
    html = re.sub(r"<[^>]+>", " ", _html(view))
    for text in (html, md):
        assert OLD_RULE_TITLE not in text
    assert NEW_RULE_TITLE in html and NEW_RULE_TITLE in md


def test_profiles_persisted_with_the_old_rule_id_and_title_stay_valid_and_read_with_the_current_title(doc):
    legacy = copy.deepcopy(doc)
    for key in ("review_required", "rule_results"):
        for item in legacy[key]:
            if item.get("id") == "REPEATED_DERIVATION_IN_LINEAGE" or item.get("rule") == "REPEATED_DERIVATION_IN_LINEAGE":
                item["title"] = OLD_RULE_TITLE
                if "id" in item:
                    item["id"] = "CROSS_LAYER_REDERIVATION"                                                   # perfil gobernado persistido con el id historico
                if "rule" in item:
                    item["rule"] = "CROSS_LAYER_REDERIVATION"
    for area in legacy["areas"]:
        area["finding_groups"] = ["CROSS_LAYER_REDERIVATION" if g == "REPEATED_DERIVATION_IN_LINEAGE" else g for g in area["finding_groups"]]
    for g in legacy["review_required"]:                                                                      # y sin los campos que aportó G3
        for o in g["occurrences"]:
            o["values"].pop("field_relations", None)
    legacy.pop("canonical_evidence", None)
    legacy["versions"].pop("canonical_evidence", None)
    ma_profile.validate_assessment(legacy)
    view, md = _view(legacy), render_md.render_markdown(legacy)
    for text in (re.sub(r"<[^>]+>", " ", _html(view)), md):
        assert OLD_RULE_TITLE not in text
    assert render_md.display_title("CROSS_LAYER_REDERIVATION", OLD_RULE_TITLE) == NEW_RULE_TITLE
    assert render_md.display_title("OTHER", "Otro título") == "Otro título"                                    # solo cambia el título conocido


# ── una sola implementación de tile con ayuda (la de los KPI superiores) ────
def test_area_help_uses_the_same_shared_tile_renderer_as_the_top_kpis(doc):
    view = _view(doc)
    html = _html(view)
    tpl = TEMPLATE_PATH.read_text(encoding="utf-8")
    body = tpl[tpl.index("{% macro area_body"):tpl.index("{% endmacro %}", tpl.index("{% macro area_body"))]
    assert "kpi_tile(m)" in body and "activity-kpi__info" not in body and "chart-tooltip" not in body        # el área NO tiene markup de tile propio
    macros = (REPO / "webapp/templates/partials/assessment_report_macros.html").read_text(encoding="utf-8")
    assert macros.count('class="activity-kpi__info"') == 1 and "inline_help" not in macros and "chart-tooltip-anchor" not in macros   # un solo renderer del (i)
    top = html[:html.index('id="asr-resumen"')]
    first_top = re.search(r'<div class="activity-kpi">.*?</div>', top, re.S).group(0)
    sec = html[html.index('id="asr-m-architecture_modeling"'):html.index('id="asr-observaciones"')]
    first_area = re.search(r'<div class="activity-kpi"><span class="activity-kpi__value">[^<]*</span><span class="activity-kpi__label">Pasos de linaje evaluados</span>.*?</div>', sec, re.S).group(0)

    def shape(s):
        return re.sub(r'>[^<]*<', '><', re.sub(r'aria-label="[^"]*"', 'aria-label=""', s)).replace('<br>', '')
    assert shape(first_top) == shape(first_area)                                                              # misma estructura de tile + (i) + tooltip
    helped = set(re.findall(r'<span class="activity-kpi__label">([^<]*)</span><button type="button" class="activity-kpi__info"', sec))
    assert {"Pasos de linaje evaluados", "Artefactos con derivaciones", "Derivaciones repetidas"} <= helped
    assert {"Dimensiones declaradas", "Artefactos de composición"} <= helped
    assert ("Artefactos con reuso" in helped) == ("Artefactos con reuso" in sec)                                # el tile de reuso solo existe si hay otros modelos con los que comparar


def test_tooltip_lines_are_not_paragraphs_so_the_report_typography_cannot_override_the_hub_tooltip(doc):
    html = _html(_view(doc))
    tips = re.findall(r'<span class="chart-tooltip">(.*?)</span>', html, re.S)
    assert len(tips) >= 8 and all("<p>" not in t for t in tips)                                              # `.methodology p` (gris, 14px) pisaba el tooltip dentro del informe


def test_no_clipping_workaround_css_or_js_was_added():
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8") + (REPO / "webapp/static/layout.css").read_text(encoding="utf-8")
    js = (REPO / "webapp/static/app.js").read_text(encoding="utf-8")
    assert not re.search(r"view-overview|asr-kpi|asm-facts[^{]*chart-tooltip|asr[^{]*chart-tooltip|methodology[^{]*chart-tooltip|view-scope", css + js)
    tpl = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "overflow" not in tpl and "style=" not in tpl


# ── resumen transversal [02] ────────────────────────────────────────────────
def _vt_closure(doc):
    return next(h for a in doc["areas"] if a["id"] == "ANALYTIC_MODEL" for h in a["highlights"] if h["id"] == "views_in_closure")


def test_section_overview_renders_the_canonical_closure_composition_once_before_the_first_area(doc):
    view = _view(doc)
    h = _vt_closure(doc)
    n = {b["name"]: b["count"] for b in h["breakdown"]}
    group = next(mg for mg in view["macro_groups"] if mg["id"] == "ARCHITECTURE_MODELING")
    tiles = {t["label"]: t["value"] for t in group["view_overview"]["tiles"]}
    assert tiles["Total de vistas"] == h["value"] and tiles["Vistas gráficas"] == n["gráficas"] and tiles["Vistas SQL"] == n["SQL"]   # valores del hecho, sin recalcular
    html = _html(view)
    sec = html[html.index('id="asr-m-architecture_modeling"'):html.index('id="asr-observaciones"')]
    assert sec.count('data-block="view-overview"') == 1
    assert sec.index('data-block="view-overview"') < sec.index("Modelo analítico")                            # tras el encabezado [02] y antes de la 1ª área
    assert html.count("Total de vistas") == 1 and "<h3>Vistas del cierre semántico</h3>" in sec
    overview = sec[sec.index('data-block="view-overview"'):sec.index("Modelo analítico")]
    assert "activity-kpi__info" not in overview and "chart-tooltip" not in overview                            # etiquetas autoexplicativas: sin (i)


def test_the_unknown_tile_is_conditional_and_the_canonical_fact_keeps_the_zero(doc):
    cv = {"total": 16, "graphical": 13, "sql": 3, "unknown": 0, "structural": False}
    assert [t["label"] for t in presenter._view_overview(cv)["tiles"]] == ["Total de vistas", "Vistas gráficas", "Vistas SQL"]    # sin tile dominante en 0
    assert [t["label"] for t in presenter._view_overview({**cv, "graphical": 12, "unknown": 1})["tiles"]][-1] == "Sin clasificar"
    assert [t["label"] for t in presenter._view_overview({"total": 0, "graphical": 0, "sql": 0, "unknown": 0, "structural": False})["tiles"]] == ["Total de vistas"]
    assert "sin clasificar" in {b["name"] for b in _vt_closure(doc)["breakdown"]}                              # el JSON canónico conserva UNKNOWN (incluso 0)
    assert any(x["fact"].startswith("am.view_type_composition@") for x in doc["fact_index"])                    # el hecho subyacente NO cambia ni se retira


def test_areas_do_not_repeat_the_view_composition_and_modelo_analitico_keeps_only_its_artifact_detail(doc):
    view = _view(doc)
    html = _html(view)
    for forbidden in ("Vistas consideradas", "Vistas de Armonización", "Vistas de Propagación", "El cierre semántico contiene", "Distribución por tipo de vista",
                      "Composición del cierre por tipo de vista"):
        assert forbidden not in html, forbidden
    for a in view["areas"]:
        assert a["facts"]["notes"] == []
        assert "Distribución por tipo de vista" not in [g["title"] for g in a["facts"]["groups"]]
    am = next(a for a in view["areas"] if a["id"] == "ANALYTIC_MODEL")
    assert "Vistas del cierre (nombre técnico y tipo)" in [g["title"] for g in am["facts"]["groups"]]           # detalle por artefacto (distinto de los totales): se conserva
    md = render_md.render_markdown(doc)
    assert "Vistas consideradas" not in md and "Distribución por tipo de vista" not in md


def test_the_rejected_area_level_views_considered_contract_was_removed():
    for mod in (ma_profile, render_md, presenter):
        assert "views_considered" not in inspect.getsource(mod)
    assert "views_considered" not in TEMPLATE_PATH.read_text(encoding="utf-8")
    assert not hasattr(render_md, "view_scope_text") and not hasattr(presenter, "VIEW_SCOPE_HELP")
    facts = [make_fact("am.view_type_composition", Area.ANALYTIC_MODEL, "AM", {"views": [{"technical_name": "V1", "view_type": "SQL"}]}, EvidenceClass.A),
             _derived("V1", 1)]
    for area in (M.Area.PROPAGATION, M.Area.HARMONIZATION):
        assert all(h["id"] != "views_considered" for h in ma_profile._highlights(FactIndex(facts), _comp(), area))


def test_legacy_profiles_without_the_closure_fact_or_with_old_labels_remain_readable(doc):
    legacy = copy.deepcopy(doc)
    old_by_id = {"lineage_steps_evaluated": OLD_LABELS[0], "artifacts_with_derived_fields": OLD_LABELS[1], "rederived_field_occurrences": OLD_LABELS[2],
                 "declared_dimensions": OLD_LABELS[3], "composition_artifacts": OLD_LABELS[4], "cross_model_reuse_artifacts": OLD_LABELS[5]}
    for a in legacy["areas"]:
        a["highlights"] = [h for h in a["highlights"] if h["id"] not in ("views_in_closure", "view_type_distribution", "view_artifacts")]
        for h in a["highlights"]:
            if h["id"] in old_by_id:
                h["label"] = old_by_id[h["id"]]
    view = _view(legacy)
    html, md = _html(view), render_md.render_markdown(legacy)
    assert 'data-block="view-overview"' not in html and all(mg["view_overview"] is None for mg in view["macro_groups"])      # sin hecho: no se inventa un 0
    for text in (html, md):
        for old in OLD_LABELS:
            assert old not in text, old


# ── Markdown alineado, sin señales ni puntajes ──────────────────────────────
def test_markdown_states_the_closure_composition_once_at_section_level_and_uses_the_same_terminology(doc):
    md = render_md.render_markdown(doc)
    h = _vt_closure(doc)
    sentence = render_md.view_composition_text(h["value"], h["breakdown"])
    assert md.count(sentence) == 1
    section = md[md.index("## Arquitectura / Modelado"):]
    assert section.index(sentence) < section.index("### Modelo analítico")
    assert "Pasos de linaje evaluados" in md and NEW_RULE_TITLE in md


def test_no_signal_threshold_or_scoring_was_introduced(doc):
    ids = {str(getattr(s, "signal_id", s)) for s in SIGNAL_REGISTRY}
    assert not any(re.search(r"VIEW_?TYPE|GRAPHICAL|SQL_VIEW|VIEW_SCOPE|CONSIDERED", i) for i in ids)
    assert "score" not in doc and "versions" in doc


# ── los cuatro contenedores de área de [02] cargan colapsados ───────────────
AREA_IDS = ("ANALYTIC_MODEL", "HARMONIZATION", "PROPAGATION", "INGESTION")


def _architecture_section(doc):
    html = _html(_view(doc))
    start = html.index('id="asr-m-architecture_modeling"')
    end = min(i for i in (html.find(m, start) for m in ('id="asr-gobierno"', 'id="asr-portabilidad"', 'id="asr-revision"')) if i != -1)   # [03] termina donde empieza la siguiente sección
    return html, html[start:end]


def test_the_four_area_containers_load_collapsed_and_keep_their_order(doc):
    _html_full, sec = _architecture_section(doc)
    tags = re.findall(r'<details class="dashboard-section" id="asr-a-([A-Z_]+)"([^>]*)>', sec)
    assert [t[0] for t in tags] == ["ANALYTIC_MODEL", "PROPAGATION", "HARMONIZATION", "INGESTION", "SOURCE_EXTRACTION"]   # las 4 de siempre + Ingesta / Fuente & Extracción (UX.5 cleanup)
    for area_id, attrs in tags:
        assert "open" not in attrs.split(), area_id                                                           # colapsado por defecto (Modelo analítico, Armonización, Propagación, Ingesta)
    assert set(AREA_IDS) <= {t[0] for t in tags}


def test_collapsed_areas_keep_their_content_badges_and_the_overview_stays_visible(doc):
    html, sec = _architecture_section(doc)
    assert sec.count('data-block="view-overview"') == 1
    details_spans = [(m.start(), m.end()) for m in re.finditer(r'<details class="dashboard-section" id="asr-a-[A-Z_]+".*?</details>', sec, re.S)]
    ov = sec.index('data-block="view-overview"')
    assert not any(a < ov < b for a, b in details_spans)                                                      # el resumen transversal queda FUERA de los contenedores
    for _a, _b in details_spans:
        block = sec[_a:_b]
        assert "dashboard-section__head" in block and "dashboard-section__state" not in block                  # todas evaluadas: el estado NO se repite por fila (se dice una vez en el subtítulo)
        assert "dashboard-section__body" in block and "Superficies de evidencia requeridas" in block            # el contenido sigue en el HTML (accesible al expandir)
    assert "Pasos de linaje evaluados" in sec and "Evaluación de reglas" in sec
    assert "Todas las subáreas fueron evaluadas." in sec                                                      # estado a nivel sección, una sola vez


def test_other_sections_keep_their_own_default_state_and_no_accordion_or_js_was_added(doc):
    html, sec = _architecture_section(doc)
    assert re.search(r'<details class="dashboard-section" open>\s*<summary class="dashboard-section__head"><span class="dashboard-section__caret"></span><span class="dashboard-section__title">Indicadores del assessment', html)
    assert "<details class=\"dashboard-section\" id=\"asr-preguntas\" open>" in html or "asr-preguntas" not in html
    tpl = TEMPLATE_PATH.read_text(encoding="utf-8")
    area_loop = tpl[tpl.index("{% for a in mg.areas %}\n  <details"):tpl.index("{% endfor %}", tpl.index("{% for a in mg.areas %}\n  <details"))]
    assert "<script" not in area_loop and "x-data" not in area_loop and "@click" not in area_loop and "name=" not in area_loop    # sin JS ni acordeón (`name` agruparía los <details>)
