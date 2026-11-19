"""Explicabilidad v5 (presenter + informe): hechos / hipotesis / acciones separados, fuentes DECISIVAS de LVL-05 (no todas las sin clase), texto MIXED, compuerta de naming
(«incumple» vs «no acreditado») y estados del detector PRO-MAT-02 con su advertencia «no observado no significa correcto». Solo presentacion: el nivel no se recalcula."""
from __future__ import annotations

import copy

from bs4 import BeautifulSoup

from tests._judgment_cases import fresh
from tests._judgment_profiles import PB
from tests.webapp.test_assessment_report_classification_badge import _catalog_report_with
from tests.webapp.test_catalog_assessment_routes import world  # noqa: F401 — fixture
from tools.catalog.methodological_judgment import RULESET_V5, evaluate_judgment
from webapp.services import assessment_presenter as P

V5 = dict(ruleset=RULESET_V5, ratified_contracts=[])


def _lvl05_profile():
    return (PB().view("V1", ["EXT", "EXT2"], derived=1, joins=1).remote("EXT", external=True).remote("EXT2", external=True).remote("CDS1").product("V1").measure("M").measure("N")
            .trace("M", "EXT", character="IDENTITY_ONLY", through=False, label="EQUALS_LEAF_LABEL")
            .trace("N", "EXT2", character="IDENTITY_OR_RENAME", through=False, label="DIFFERENT_FROM_LEAF", name_equals=False).build())


def test_lvl05_counts_only_the_decisive_sources_and_separates_facts_hypotheses_and_actions():
    p = _lvl05_profile()
    j = evaluate_judgment(p, **V5)
    assert j["level_rule"] == "LVL-05"
    view = P._with_nd_responsibles(P.judgment_view(j), p, j)
    nd = view["nd"]
    assert [d["leaf"] for d in nd["decisive_evidence"]] == ["EXT"] and nd["decisive_evidence"][0]["fields"] == ["M"]       # EXT2 no decide
    assert any("1 fuente decide" in f["text"] for f in nd["facts"])
    assert nd["hypotheses"] and all(h["verified"] is False for h in nd["hypotheses"])
    assert nd["actions"] and nd["actions"][0]["responsible"].startswith("Arquitecto")


def test_the_mixed_boundary_is_described_as_a_known_mixture_not_as_without_class():
    p = PB().view("V1", ["MIX"], derived=1).remote("MIX").leaf_class("MIX", "MIXED_BOUNDARY_CLASSES").product("V1").measure("M").build()
    for r in p["canonical_evidence"]["source_contract_classes"]["leaves"]:
        if r["leaf"] == "MIX":
            r["boundary_classes"] = ["PHYSICAL_ABAP_TABLE_BOUNDARY", "GOVERNED_SAP_CDS_CONTRACT"]
            r["remote_entities"] = ["ABAPTABLES.MLGN", "ABAPTABLES.NSDM_V_MCHB"]
    j = evaluate_judgment(p, **V5)
    txt = P.nd_responsibles(p, j).get("SOURCE_CONTRACT_CLASS_UNDETERMINED", "")
    assert "mezcla fronteras de clase distinta" in txt and "PHYSICAL_ABAP_TABLE_BOUNDARY" in txt and "sin clase de contrato determinable" not in txt


def test_naming_gate_view_distinguishes_a_demonstrated_deviation_from_a_non_accreditation():
    j = evaluate_judgment(fresh("kensing_gl_line_item_raw"), **V5)
    v = P.naming_gate_view(j)
    assert v["state"] == "PASS" and v["limited_level"] is False and "solo puede limitar" in v["note"]
    blocked = {**copy.deepcopy(j), "level": "ADECUADA", "level_rule": "LVL-09", "naming_gate": {**j["naming_gate"], "state": "BLOCK"}}
    na = {**copy.deepcopy(j), "level": "ADECUADA", "level_rule": "LVL-09", "naming_gate": {**j["naming_gate"], "state": "NOT_ACCREDITED"}}
    assert P.naming_gate_view(blocked)["state_text"].startswith("Divergencia de nombres acreditada")
    assert P.naming_gate_view(na)["state_text"].startswith("La responsabilidad sobre los nombres no pudo acreditarse")
    # Cierre LVL-09: BLOCK afirma el incumplimiento; NOT_ACCREDITED NO lo afirma (distinción preservada tras la reescritura de copy).
    assert "incumplimientos en la convención de nombres" in P.judgment_view(blocked)["headline"] and "no se afirma un incumplimiento" not in P.judgment_view(blocked)["headline"]
    assert "no se afirma un incumplimiento" in P.judgment_view(na)["headline"] and "incumplimientos en la convención de nombres" not in P.judgment_view(na)["headline"]


def test_material_compensation_view_states_and_the_conservative_detector_warning():
    j = evaluate_judgment(fresh("biogenesis_sellout_gp"), **V5)
    v = P.material_compensation_view(j)
    states = {r["leaf"]: r["state_text"] for r in v["rows"]}
    assert states["ART_0007"] == "Desvío acreditado" and "No observado no significa" in v["note"].replace("«", "").replace("»", "")
    assert P.material_compensation_view({"status": "DETERMINED"}) is None                  # v4: sin la seccion


def test_the_report_renders_naming_gate_material_states_and_actions_without_breaking_the_closing_callout(world):  # noqa: F811
    gp = evaluate_judgment(fresh("biogenesis_sellout_gp"), **V5)
    html = _catalog_report_with(lambda j: {**j, "naming_gate": gp["naming_gate"], "material_compensation": gp["material_compensation"]})
    soup = BeautifulSoup(html, "html.parser")
    sec = soup.select_one("#asr-juicio")
    assert sec.select_one("[data-asr-naming-gate]") is not None and sec.select_one("[data-asr-material]") is not None
    assert "no significa que el modelo sea metodológicamente correcto" in sec.get_text(" ", strip=True)
    assert "Lectura de la adopción" in sec.select(".callout")[-1].get_text(" ", strip=True)       # el cierre editorial sigue siendo el ultimo callout
