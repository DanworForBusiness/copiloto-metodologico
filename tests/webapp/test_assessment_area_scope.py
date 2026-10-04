"""test_assessment_area_scope.py — indicadores de Propagación / Armonización (etiquetas fieles + ayuda (i)) y "Vistas consideradas" por área.

Contrato:
  * etiquetas visibles fieles a lo que se mide ("Pasos de linaje evaluados": aristas proveedor→consumidor evaluadas, también las de contrato idéntico;
    "Derivaciones repetidas": OCURRENCIAS (campo, consumidor, ancestro); "Artefactos con reuso": solo entre candidatos evaluados); ningún cálculo cambia;
  * "Vistas consideradas" = vistas ÚNICAS que participan en la evidencia de la sección (poblaciones SOLAPADAS, sin mapa artefacto→área ni capa numérica),
    tipadas por el clasificador único; se deriva UNA vez en el perfil (no en la plantilla); los perfiles anteriores siguen legibles sin la línea;
  * ayuda (i) con el primitivo del Hub, sin CSS/JS nuevo; sin señales, puntajes ni umbrales.
"""
from __future__ import annotations

import copy
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
OLD_LABELS = ("Pasos de linaje con delta de contrato", "Artefactos con campos derivados", "Campos derivados de nuevo en un ancestro del linaje",
              "Dimensiones declaradas en el cierre", "Artefactos de composición (dos o más proveedores de linaje)",
              "Artefactos contenidos también en otros modelos analíticos")


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


def _hl(doc, area, hid):
    return next((h for a in doc["areas"] if a["id"] == area for h in a["highlights"] if h["id"] == hid), None)


# ── etiquetas ───────────────────────────────────────────────────────────────
def test_the_visible_labels_are_the_agreed_ones_in_profile_html_and_markdown(doc):
    expected = {"lineage_steps_evaluated": "Pasos de linaje evaluados", "artifacts_with_derived_fields": "Artefactos con derivaciones",
                "rederived_field_occurrences": "Derivaciones repetidas", "declared_dimensions": "Dimensiones declaradas",
                "composition_artifacts": "Artefactos de composición", "cross_model_reuse_artifacts": "Artefactos con reuso"}
    assert render_md._HIGHLIGHT_LABELS == expected                                                      # una sola fuente visible
    for area in ("PROPAGATION", "HARMONIZATION"):
        for h in next(a for a in doc["areas"] if a["id"] == area)["highlights"]:
            if h["id"] in expected:
                assert h["label"] == expected[h["id"]]                                                  # el perfil nuevo ya trae la etiqueta vigente
    html, md = _html(_view(doc)), render_md.render_markdown(doc)
    for text in (html, md):
        for old in OLD_LABELS:
            assert old not in text, old
    assert "Pasos de linaje evaluados" in html and "Pasos de linaje evaluados" in md


def test_the_numeric_values_are_unchanged_and_an_identical_edge_still_counts(doc):
    # fixture sintético: la regla es `usables(contract_delta) + usables(am.contract_delta)`; una arista de contrato idéntico es un hecho usable más
    ident = make_fact("propagation.contract_delta", Area.PROPAGATION, "A->B", {"descriptors": {"schema_identical": True}}, EvidenceClass.B)
    changed = make_fact("propagation.contract_delta", Area.PROPAGATION, "B->C", {"descriptors": {"schema_identical": False}}, EvidenceClass.B)
    final = make_fact("am.contract_delta", Area.ANALYTIC_MODEL, "C->AM", {"descriptors": {"schema_identical": False}}, EvidenceClass.B)
    out = {h["id"]: h for h in ma_profile._highlights(FactIndex([ident, changed, final]), _comp(), M.Area.PROPAGATION)}
    assert out["lineage_steps_evaluated"]["value"] == 3                                                 # cuenta TODAS las aristas evaluadas, también la idéntica
    assert out["lineage_steps_evaluated"]["label"] == "Pasos de linaje evaluados"
    real = _hl(doc, "PROPAGATION", "lineage_steps_evaluated")
    assert real is not None and isinstance(real["value"], int)


def _comp(root="AM"):
    return SimpleNamespace(scope=SimpleNamespace(root=root, root_type="analytic-model"), findings=[])


def _derived(subject, n):
    return make_fact("propagation.derived_fields", Area.PROPAGATION, subject, {"n_derived": n, "derived_fields": [{"field": f"F{i}"} for i in range(n)]}, EvidenceClass.A)


def test_artifacts_with_derivations_counts_each_artifact_once_regardless_of_its_fields():
    facts = [_derived("V1", 3), _derived("V2", 1), _derived("V3", 0)]
    out = {h["id"]: h for h in ma_profile._highlights(FactIndex(facts), _comp(), M.Area.PROPAGATION)}
    assert out["artifacts_with_derived_fields"]["value"] == 2                                          # V1 (3 campos) y V2 (1): un artefacto = 1


def test_repeated_derivations_count_occurrences_not_unique_fields():
    occ = [{"field": "X", "consumer": "C", "provider": "P1", "lineage_distance": 1, "adjacent": True},
           {"field": "X", "consumer": "C", "provider": "P2", "lineage_distance": 2, "adjacent": False},
           {"field": "Y", "consumer": "C", "provider": "P1", "lineage_distance": 1, "adjacent": True}]
    red = make_fact("propagation.rederivation", Area.PROPAGATION, "closure", {"occurrences": occ, "adjacent_count": 2}, EvidenceClass.B)
    out = {h["id"]: h for h in ma_profile._highlights(FactIndex([red]), _comp(), M.Area.PROPAGATION)}
    assert out["rederived_field_occurrences"]["value"] == 3 and len({o["field"] for o in occ}) == 2   # 3 ocurrencias aunque solo haya 2 campos únicos


def test_no_visible_wording_claims_a_comparison_between_layers(doc):
    # etiquetas, ayudas y líneas de alcance de los indicadores (el título de la regla CROSS_LAYER_REDERIVATION es un contrato canónico aparte)
    view = _view(doc)
    texts = [" ".join(lines) for lines in presenter.AREA_KPI_HELP.values()] + [" ".join(presenter.VIEW_SCOPE_HELP)] + list(render_md._HIGHLIGHT_LABELS.values())
    for a in view["areas"]:
        texts += [m["label"] for m in a["facts"]["headline"]] + [x for m in a["facts"]["headline"] for x in m.get("help", [])] + [a["facts"]["scope_line"] or ""]
    for t in texts:
        assert not re.search(r"otra capa|otras capas", t, re.I), t
    assert "aguas arriba" in presenter.AREA_KPI_HELP["rederived_field_occurrences"][0]                   # la ayuda explica el "ancestro" sin exponerlo en la etiqueta


def test_harmonization_labels_stay_faithful_to_the_evaluated_candidate_population():
    help_ = presenter.AREA_KPI_HELP
    assert "dimensiones y destinos de asociación evaluados" in help_["cross_model_reuse_artifacts"][0]
    assert "No representa todos los artefactos del cierre" in help_["cross_model_reuse_artifacts"][0]
    assert "No se infieren por nombre ni por capa" in help_["declared_dimensions"][0]
    assert "no implica por sí solo una desviación metodológica" in help_["composition_artifacts"][0]
    assert "Artefactos contenidos también" not in render_md._HIGHLIGHT_LABELS.values()


def test_help_copy_is_canonical_concise_and_never_reads_as_a_score_or_a_human_reviewer():
    banned = re.compile(r"\b(score|scoring|puntaje|puntuaci[oó]n|madurez|banda|ranking|rating|human[oa]s?|persona)\b", re.I)
    assert "pasos de linaje, no artefactos" in presenter.AREA_KPI_HELP["lineage_steps_evaluated"][0] and "idéntica" in presenter.AREA_KPI_HELP["lineage_steps_evaluated"][0]
    assert "un cero no garantiza ausencia" in presenter.AREA_KPI_HELP["artifacts_with_derived_fields"][1]
    assert "El consultor determina" in presenter.AREA_KPI_HELP["rederived_field_occurrences"][0]
    for lines in (*presenter.AREA_KPI_HELP.values(), presenter.VIEW_SCOPE_HELP):
        assert 1 <= len(lines) <= 2 and all(len(x) < 260 for x in lines)
        assert not banned.search(" ".join(lines))


def test_area_help_uses_the_existing_hub_primitive_and_no_css_or_js_was_added(doc):
    html = _html(_view(doc))
    body = html[html.index('id="asr-m-architecture_modeling"'):html.index('id="asr-observaciones"')]
    tiles = re.findall(r'<div class="activity-kpi"><span class="activity-kpi__value">[^<]*</span><span class="activity-kpi__label">([^<]*)</span>'
                       r'(<button type="button" class="activity-kpi__info" aria-label="[^"]*"><svg viewBox="0 0 24 24" aria-hidden="true">.*?</svg></button><span class="chart-tooltip"><p>.*?</span>)?</div>', body)
    helped = {label for label, info in tiles if info}
    assert {"Pasos de linaje evaluados", "Artefactos con derivaciones", "Dimensiones declaradas", "Artefactos de composición"} & helped
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8")
    js = (REPO / "webapp/static/app.js").read_text(encoding="utf-8")
    assert not re.search(r"view-scope|vistas-consideradas|asr-scope", css + js)
    line = re.search(r'<p data-block="view-scope">.*?</p>', body, re.S).group(0)
    assert 'class="chart-tooltip"' in line and "<p>" not in line.replace('<p data-block="view-scope">', "")      # el tooltip vive DENTRO del párrafo (un <p> anidado lo partiría)
    assert set(" ".join(re.findall(r'class="([^"]+)"', line)).split()) <= {"chart-tooltip-anchor", "activity-kpi__info", "chart-tooltip"}   # solo primitivos existentes (precedente CCHH)


# ── Vistas consideradas ─────────────────────────────────────────────────────
def _vt_fact(views):
    return make_fact("am.view_type_composition", Area.ANALYTIC_MODEL, "AM", {"views": [{"technical_name": n, "view_type": t} for n, t in views]}, EvidenceClass.A)


def _scope(facts, area, root="AM"):
    return {h["id"]: h for h in ma_profile._highlights(FactIndex(facts), _comp(root), area)}.get("views_considered")


VIEWS = [("V1", "GRAPHICAL"), ("V2", "SQL"), ("V3", "UNKNOWN"), ("V4", "GRAPHICAL"), ("V5", "SQL")]


def test_the_area_view_scope_is_the_unique_union_of_its_evidence_population_typed_by_the_canonical_classifier():
    facts = [_vt_fact(VIEWS), _derived("V1", 1), _derived("V1", 2), _derived("V2", 0), _derived("V3", 1),                  # V1 aparece dos veces: cuenta una
             make_fact("propagation.contract_delta", Area.PROPAGATION, "V1->V2", {"x": 1}, EvidenceClass.B),
             make_fact("am.contract_delta", Area.ANALYTIC_MODEL, "V2->AM", {"x": 1}, EvidenceClass.B)]
    h = _scope(facts, M.Area.PROPAGATION)
    assert h["value"] == 3                                                                                  # V1, V2, V3 (el AM raíz y V4/V5 no participan)
    assert {b["name"]: b["count"] for b in h["breakdown"]} == {"gráficas": 1, "SQL": 1, "sin clasificar": 1}
    assert sum(b["count"] for b in h["breakdown"]) == h["value"]                                            # G + S + U == vistas identificadas
    import inspect
    src = inspect.getsource(ma_profile._views_considered)
    assert "GRAPHICALVIEWBUILDER" not in src and "sqlEditor" not in src                                     # sin segundo clasificador: lee el tipo del hecho canónico


def test_the_same_view_can_belong_to_both_areas_without_error_or_partition():
    dim = make_fact("harmonization.dimension_artifacts", Area.HARMONIZATION, "closure",
                    {"count": 1, "with_own_associations": 0, "artifacts": [{"artifact": "V1"}]}, EvidenceClass.A)
    comp = make_fact("harmonization.composition_artifacts", Area.HARMONIZATION, "closure", {"count": 1, "artifacts": [{"artifact": "V4"}]}, EvidenceClass.B)
    cand = make_fact("harmonization.local_chain_reuse", Area.HARMONIZATION, "V2", {"x": 1}, EvidenceClass.B)
    facts = [_vt_fact(VIEWS), dim, comp, cand, _derived("V1", 1), _derived("V4", 0), _derived("V5", 1)]
    h = _scope(facts, M.Area.HARMONIZATION)
    p = _scope(facts, M.Area.PROPAGATION)
    assert h["value"] == 3 and p["value"] == 3                                                              # H = {V1, V4, V2}; P = {V1, V4, V5}
    assert h["value"] + p["value"] != len(VIEWS)                                                            # NO es una partición del cierre: se solapan (V1, V4)


def test_no_scope_without_view_evidence_or_without_an_evidence_population():
    assert _scope([_derived("V1", 1)], M.Area.PROPAGATION) is None                                         # sin `am.view_type_composition` no se inventa un cero
    assert _scope([_vt_fact(VIEWS)], M.Area.PROPAGATION) is None                                           # sin población de evidencia tampoco


def test_the_scope_line_comes_from_the_profile_and_the_template_does_not_calculate_it(doc):
    tpl = (REPO / "webapp/templates/partials" / "catalog_assessment_report.html").read_text(encoding="utf-8")
    assert "a.facts.scope_line" in tpl and "views_considered" not in tpl and "breakdown" not in tpl.split("{% macro area_body")[1].split("{% endmacro %}")[0]
    view = _view(doc)
    html = _html(view)
    for a in view["areas"]:
        h = _hl(doc, a["id"], "views_considered")
        if h is None:
            assert a["facts"]["scope_line"] is None
            continue
        assert a["facts"]["scope_line"] == render_md.view_scope_text(h["value"], h["breakdown"])
        assert a["facts"]["scope_line"] in html
    assert render_md.view_scope_text(16, [{"name": "gráficas", "count": 13}, {"name": "SQL", "count": 3}, {"name": "sin clasificar", "count": 0}]) == "Vistas consideradas: 16 · 13 gráficas · 3 SQL"
    assert render_md.view_scope_text(16, [{"name": "gráficas", "count": 12}, {"name": "SQL", "count": 3}, {"name": "sin clasificar", "count": 1}]) == \
        "Vistas consideradas: 16 · 12 gráficas · 3 SQL · 1 sin clasificar"


def test_the_scope_line_sits_between_the_evidence_sentence_and_the_kpi_tiles_with_the_overlap_help(doc):
    html = _html(_view(doc))
    sec = html[html.index('id="asr-m-architecture_modeling"'):html.index('id="asr-observaciones"')]
    i_sup, i_scope, i_tiles = sec.index("Superficies de evidencia requeridas", sec.index("Propagación")), sec.index('data-block="view-scope"'), sec.index("asm-facts")
    assert i_sup < i_scope
    assert "Una misma vista puede participar en Armonización y Propagación" in sec and "no representa pertenencia exclusiva a una capa" in sec
    assert "Vistas de Armonización" not in html and "Vistas de Propagación" not in html                       # jamás se afirma pertenencia por área
    assert i_scope < sec.index("asm-facts", i_scope)


def test_markdown_carries_the_same_line_and_terminology(doc):
    md = render_md.render_markdown(doc)
    view = _view(doc)
    for a in view["areas"]:
        if a["facts"]["scope_line"]:
            assert a["facts"]["scope_line"] in md
    assert "Vistas consideradas" in md and "Pasos de linaje evaluados" in md


def test_legacy_profiles_without_the_scope_and_with_the_old_labels_remain_readable(doc):
    legacy = copy.deepcopy(doc)
    old_by_id = {"lineage_steps_evaluated": OLD_LABELS[0], "artifacts_with_derived_fields": OLD_LABELS[1], "rederived_field_occurrences": OLD_LABELS[2],
                 "declared_dimensions": OLD_LABELS[3], "composition_artifacts": OLD_LABELS[4], "cross_model_reuse_artifacts": OLD_LABELS[5]}
    for a in legacy["areas"]:
        a["highlights"] = [h for h in a["highlights"] if h["id"] != "views_considered"]
        for h in a["highlights"]:
            if h["id"] in old_by_id:
                h["label"] = old_by_id[h["id"]]
    view = _view(legacy)
    html, md = _html(view), render_md.render_markdown(legacy)
    assert "Vistas consideradas" not in html and "Vistas consideradas" not in md and 'data-block="view-scope"' not in html     # sin línea (no se inventa un 0)
    assert all(a["facts"]["scope_line"] is None for a in view["areas"])
    for text in (html, md):
        for old in OLD_LABELS:
            assert old not in text, old                                                                    # la lectura usa la redacción vigente aun con el JSON viejo


def test_no_signal_threshold_or_scoring_was_introduced(doc):
    ids = {str(getattr(s, "signal_id", s)) for s in SIGNAL_REGISTRY}
    assert not any(re.search(r"VIEW_?TYPE|GRAPHICAL|SQL_VIEW|VIEW_SCOPE|CONSIDERED", i) for i in ids)
    assert "score" not in doc and "versions" in doc
    assert {h["id"] for a in doc["areas"] for h in a["highlights"] if h["id"] == "views_considered"} <= {"views_considered"}
    for a in doc["areas"]:
        assert a["assessability"] in ("ASSESSED", "PARTIALLY_ASSESSED", "NOT_ASSESSED", "NOT_APPLICABLE")
