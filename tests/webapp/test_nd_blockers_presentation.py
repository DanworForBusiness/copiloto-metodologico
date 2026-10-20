"""Presentacion de la explicabilidad de «No determinable» en el Hub: solo vocabulario y orden sobre ``explain_determination`` (sin recalcular nada), una sola explicacion por pantalla, compatibilidad sin perfil."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._nd_cases import ALIASES, MANIFEST, fresh
from tests.test_methodological_judgment_rules import buena_model
from tests.webapp.test_catalog_assessment_routes import _post, client_for, world  # noqa: F401
from tools.catalog.methodological_judgment import contract as C
from tools.catalog.methodological_judgment import evaluate_judgment
from tools.catalog.methodological_judgment import explain as X
from webapp import main
from webapp.services import assessment_presenter as P
from webapp.services import assessment_service as svc

REPO = Path(__file__).resolve().parents[2]
LEGACY_KEYS = {"cause", "label", "text", "dimensions", "reason_code", "level_rule"}
NEW_KEYS = {"blockers", "blockers_summary", "blockers_resolved", "diagnosis_limits", "corroboration", "blockers_footnote"}


def judgment(alias):
    p = fresh(alias)
    return evaluate_judgment(p, ruleset=C.RULESET_V4, ratified_contracts=[]), p


def render(world, j, p):
    c = client_for()
    _post(c)
    e0 = world.entries[0]
    view = P.report_view(svc.status_for_entry(e0), e0, "datasphere_core")
    view["judgment"] = P.judgment_view(j, p)
    html = main.templates.get_template("partials/catalog_assessment_report.html").render(v=view, pack="datasphere_core")
    return html[html.index('id="asr-juicio"'):html.index("</section>", html.index('id="asr-juicio"'))]


def test_without_a_profile_the_explanation_is_exactly_the_one_that_existed_and_with_a_profile_it_only_adds_blockers():
    j, p = judgment("nd_lvl06_harmonization_label_unknown")
    old, new = P.nd_explanation(j), P.nd_explanation(j, p)
    assert set(old) == LEGACY_KEYS and set(new) == LEGACY_KEYS | NEW_KEYS and {k: new[k] for k in LEGACY_KEYS} == old
    assert P.nd_explanation(j, None) == old and P.nd_explanation({"status": "DETERMINED", "level": "ADECUADA"}, p) is None


def test_a_determined_judgment_has_no_nd_block_and_no_blockers():
    p = buena_model().build()
    j = evaluate_judgment(p, ruleset=C.RULESET_V4, ratified_contracts=[])
    v = P.judgment_view(j, p)
    assert j["status"] == "DETERMINED" and v["nd"] is None and "blockers" not in v


def test_the_nd_view_exposes_concrete_blockers_and_the_headline_leads_with_the_concrete_summary_instead_of_adding_a_second_explanation():
    j, p = judgment("nd_lvl06_harmonization_label_unknown")
    v = P.judgment_view(j, p)
    (r,) = v["nd"]["blockers"]
    assert v["determined"] is False and v["level_label"] == "No determinable"                          # el valor oficial NO cambia
    assert r["dimension"] == "Armonización" and r["category_text"] == "Evidencia existente pero no interpretable" and r["resolution_text"] == "Evidencia técnica"
    assert r["artifacts"] == [a for a in r["artifacts"] if a.startswith("ART_")] and r["confidence_text"] == "Alta" and r["corroborated"] is True and r["dom_id"] == "asr-nd-1"
    assert v["headline"].startswith(P._JDG_ND_HEADLINE) and v["nd"]["blockers_summary"] in v["headline"] and "campo derivado" in r["why"] and "no indica cuál es" in r["why"]
    assert v["nd"]["blockers_footnote"].startswith("Resolver un impedimento no garantiza")             # nunca se promete un nivel


def test_every_real_nd_renders_its_blockers_once_in_a_disclosure_without_the_legacy_table(world):
    for alias in ALIASES:
        j, p = judgment(alias)
        sec = render(world, j, p)
        n = len(MANIFEST[alias]["blockers"])
        assert sec.count('data-block="nd-blockers"') == 1 and len(re.findall(r'<details class="dashboard-section" id="asr-nd-\d+">', sec)) == n, alias
        assert "<th>Causa</th>" not in sec and "Detalle del impedimento" in sec and "Certeza del diagnóstico" in sec and "Resolver un impedimento no garantiza" in sec
        for r in P.judgment_view(j, p)["nd"]["blockers"]:
            seg = sec[sec.index(f'id="{r["dom_id"]}"'):]
            seg = seg[:seg.index("</details>")]
            assert seg.count(r["why"]) == 1 and r["why"].startswith(r["short"]), (alias, r["condition_id"])   # el texto completo vive en el detalle (bajo demanda); el resumen ejecutivo solo lleva su primera oración
            assert sec.count(r["why"]) <= 2


def test_without_a_readable_profile_the_page_falls_back_to_the_per_contract_table_never_to_an_invented_detail(world):
    j, _p = judgment("nd_lvl06_harmonization_label_unknown")
    sec = render(world, j, None)
    assert "<th>Causa</th>" in sec and "nd-blockers" not in sec and "Detalle del impedimento" not in sec


def test_a_determined_judgment_renders_no_blockers_section(world):
    p = buena_model().build()
    sec = render(world, evaluate_judgment(p, ruleset=C.RULESET_V4, ratified_contracts=[]), p)
    assert "nd-blockers" not in sec and "Por qué no hay un nivel" not in sec


def test_the_diagnosis_limit_is_shown_when_the_profile_cannot_say_which_field(world):
    j, p = judgment("nd_lvl06_harmonization_label_unknown")
    sec = render(world, j, p)
    assert "Límite del diagnóstico" in sec and "no cuáles" in sec


# ───────────────────────────── el catalogo de textos cubre todo el contrato (sin claves crudas en pantalla) ─────────────────────────────
@pytest.mark.parametrize("cause", sorted(X._SPEC))
def test_every_recognized_cause_has_its_own_concrete_text_and_catalog_entries(cause):
    cat, rtype, required = X._SPEC[cause]
    txt = P._nd_why({"condition_id": cause, "facts": {}, "artifacts": []})
    assert txt.strip() and cause not in txt and cat in P._ND_CATEGORY and rtype in P._ND_RESOLUTION and all(k in P._ND_REQUIRED for k in required)


def test_the_text_catalogs_are_complete_for_every_category_resolution_state_and_confidence():
    assert set(X.CATEGORIES) <= set(P._ND_CATEGORY) and set(X.RESOLUTION_TYPES) <= set(P._ND_RESOLUTION) and {C.DS_NOT_EVALUABLE, C.DS_UNRESOLVED} <= set(P._ND_STATE)
    assert {X.HIGH, X.MEDIUM, X.LOW} <= set(P._ND_CONFIDENCE)
    for key in ("HARMONIZATION_CANDIDATE_CHARACTER_NOT_DETERMINABLE", "SOURCE_CLASS_RATIFICATION_HAS_NO_EFFECT_OUTSIDE_LVL05", "EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL"):
        assert P._ND_HINT[key].strip()


def test_the_judgment_service_does_not_call_the_explainer_so_the_persisted_judgment_and_its_signature_never_change():
    assert "explain_determination" not in (REPO / "webapp/services/assessment_service.py").read_text(encoding="utf-8")
    assert "explain_determination" in (REPO / "webapp/services/assessment_presenter.py").read_text(encoding="utf-8")
