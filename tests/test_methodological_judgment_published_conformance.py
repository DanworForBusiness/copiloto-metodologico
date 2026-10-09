"""
judgment-rules/v2 — PUB-STR-01: segunda vía de reconocimiento (conformidad con SUSTANCIA, sin asociaciones obligatorias) + explicación de LVL-06.

Contrato (A1**, aprobado tras el diagnóstico Kensing Raw G/L vs Delivery Document):
  * vía 1 (≥2 familias afirmativas independientes) INTACTA; vía 2: ≥1 familia afirmativa A NIVEL DE CAMPO + linaje completo + sin fuga + ≥1 medida publicada + identidad de negocio en TODAS las
    medidas + ningún campo publicado con el nombre de la columna fuente. Una asociación sola nunca completa la fortaleza;
  * ``declared_dimensions_not_associated`` es solo INFORMATIVA (no bloquea ninguna vía);
  * LVL-06 (ND por falta de fortalezas) identifica las dimensiones sin fortaleza y distingue ausencia de fortaleza (evaluado) de evidencia insuficiente; es aditivo y no cambia el nivel;
  * sin umbrales, naming ni reglas por cliente. Fixtures SINTÉTICAS (constructor ``PB``).
"""
from __future__ import annotations

import inspect

import pytest

from tests._judgment_profiles import PB
from tests.test_methodological_judgment_rules import buena_model
from tools.catalog.methodological_judgment import RULESET_VERSION, JUDGMENT_SCHEMA_VERSION, evaluate_judgment
from tools.catalog.methodological_judgment import engine as E
from tools.catalog.methodological_judgment import rules as R
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc


def _base(*, measure=True, enriched=True, field=True, technical_field=False) -> PB:
    b = PB().view("V1", ["H1"]).view("H1", ["CDS1"]).cand("H1", sources=["CDS1"]).remote("CDS1").product("V1").flow("RF", delta=True)
    if measure:
        b.measure("M1", enriched=enriched)
    if field:
        b.field("F_T", technical=True) if technical_field else b.field("F1")
    return b


def _build(b: PB) -> dict:
    return b.trace_all("CDS1", character="IDENTITY_OR_RENAME", through=True, label="EQUALS_LEAF_LABEL", via=["H1"]).build()


def _pub_basis(j):
    s = next((x for x in j["strengths"] if x["rule_id"] == "PUB-STR-01"), None)
    return None if s is None else s["deterministic_basis"]


def _verdict(j):
    return (j["status"], j["level"], j["level_rule"])


# ── la nueva vía ────────────────────────────────────────────────────────────
def test_a_governed_one_to_one_contract_with_consumption_substance_is_recognized_without_associations():
    j = evaluate_judgment(_build(_base()))
    assert _verdict(j) == ("DETERMINED", "BUENA", "LVL-04") and j["closure"] == "FINAL"
    basis = _pub_basis(j)
    assert "PUB.conformance_with_substance_without_required_associations" in basis and "PUB.at_least_two_independent_affirmative_families" not in basis
    assert "PUB.published_measures_with_business_identity" in basis and "PUB.no_source_column_names_in_the_consumption_vocabulary" in basis
    assert j["supporting_evidence"]["published_business_association_targets"] == []


def test_the_original_path_is_intact_and_keeps_its_basis():
    j = evaluate_judgment(buena_model().build())
    assert _verdict(j) == ("DETERMINED", "BUENA", "LVL-04")
    basis = _pub_basis(j)
    assert "PUB.at_least_two_independent_affirmative_families" in basis and "PUB.conformance_with_substance_without_required_associations" not in basis


def test_the_original_path_does_not_inherit_the_substance_guards_nor_any_gap_block():
    """La vía 1 queda EXACTAMENTE como estaba: una medida sin identidad (que la vía 2 rechazaría) y una brecha declarada no la afectan."""
    b = buena_model().gap("DIM_X")
    b._measures[0]["business_name_equals_technical"] = True
    j = evaluate_judgment(b.build())
    assert _verdict(j) == ("DETERMINED", "BUENA", "LVL-04") and "PUB.at_least_two_independent_affirmative_families" in _pub_basis(j)


def test_declared_dimensions_not_associated_stay_informative_and_never_block_either_path():
    gap_only = evaluate_judgment(_build(_base().gap("DIM_X")))
    assert _verdict(gap_only) == ("DETERMINED", "BUENA", "LVL-04")                                       # vía 2 con brecha declarada
    assert gap_only["supporting_evidence"]["declared_dimensions_not_associated"] == ["DIM_X"]            # la señal sigue visible, sin efecto de nivel
    assert not any(r["classification"] in ("MATERIAL_DEVIATION", "RELEVANT_WEAKNESS", "LOCAL_WEAKNESS") for r in gap_only["reasons"])
    via_pair = evaluate_judgment(buena_model().gap("DIM_X").build())
    assert _verdict(via_pair) == ("DETERMINED", "BUENA", "LVL-04")                                       # vía 1 con brecha: misma regla en ambas vías
    assert evaluate_judgment(_build(_base().assoc("D1")))["level"] == "BUENA"


# ── controles negativos de SUSTANCIA y LINAJE: no se promueve un contrato vacío / técnico / sin consumo ──
@pytest.mark.parametrize("label,build", [
    ("sin medidas (sin sustento de consumo analítico)", lambda: _build(_base(measure=False))),
    ("medida sin identidad de negocio", lambda: _build(_base(enriched=False))),
    ("campo publicado con el nombre de la columna fuente", lambda: _build(_base(technical_field=True))),
    ("contrato publicado vacío", lambda: PB().view("V1", ["H1"]).view("H1", ["CDS1"]).cand("H1", sources=["CDS1"]).remote("CDS1").product("V1").flow("RF", delta=True).trace_all("CDS1").build()),
    ("linaje incompleto", lambda: _build(_base().incomplete("F1"))),
])
def test_substance_and_lineage_negative_controls_stay_not_determinable(label, build):
    j = evaluate_judgment(build())
    assert _verdict(j) == ("NOT_DETERMINABLE", None, "LVL-06"), label
    assert "PUB-STR-01" not in {s["rule_id"] for s in j["strengths"]}


def test_vocabulary_leak_from_a_physical_table_stays_not_determinable():
    b = _base().remote("TBL", raw=True)
    b.trace("F1", "TBL", character="IDENTITY_ONLY", through=False, label="EQUALS_LEAF_LABEL")
    j = evaluate_judgment(_build(b))
    assert "PUB-STR-01" not in {s["rule_id"] for s in j["strengths"]} and j["level"] != "BUENA"


def test_a_lone_association_family_never_completes_the_strength_even_with_substance():
    b = PB().view("V1", ["CDS1"]).remote("CDS1").product("V1").measure("M").field("F1").assoc("D1").assoc("D2")
    b.trace_all("CDS1", character="IDENTITY_ONLY", through=False, label="EQUALS_LEAF_LABEL")              # gobernada, sin armonización: la asociación es la ÚNICA familia
    assert "PUB-STR-01" not in {s["rule_id"] for s in evaluate_judgment(b.build())["strengths"]}


def test_the_other_three_contracts_are_still_required():
    b = PB().view("V1", ["CDS1"]).remote("CDS1").product("V1").measure("M1").field("F1").flow("RF", delta=True)      # sin armonización
    j = evaluate_judgment(b.trace_all("CDS1").build())
    assert j["level"] != "BUENA" and "HAR-STR-01" not in {s["rule_id"] for s in j["strengths"]}
    b2 = _base().remote("TBL", external=True)                                                                    # fuente no gobernada
    assert evaluate_judgment(_build(b2))["level"] != "BUENA"


def test_the_conformance_rule_has_no_naming_threshold_or_client_logic():
    src = inspect.getsource(R.published_contract_conformance)
    for needle in ("startswith", "endswith", "re.", "Kensing", "Biogenesis", "len(measures) >", "ratio", "0."):
        assert needle not in src, needle


# ── LVL-06: explicación (aditiva) ───────────────────────────────────────────
def _nd_missing_pub_only():
    b = _base(measure=False)                                                                                       # evaluable, sin sustancia → falta solo el contrato publicado
    return evaluate_judgment(_build(b))


def test_lvl06_identifies_the_missing_dimensions_and_distinguishes_absence_of_strength_from_insufficient_evidence():
    j = _nd_missing_pub_only()
    d = j["not_determinable_detail"]
    assert j["not_determinable_reason"] == "INSUFFICIENT_AFFIRMATIVE_EVIDENCE"                                      # el código histórico no cambia
    assert d["level_rule"] == "LVL-06" and d["evidence_sufficient"] is True
    (m,) = d["missing_strength_dimensions"]
    assert (m["dimension"], m["state"], m["cause"]) == ("PUBLISHED_CONTRACT", "NO_STRENGTH", "PUBLISHED_CONTRACT_SUBSTANCE_NOT_ESTABLISHED") and "measures_published" in m["unmet_conditions"]
    assert j["what_would_change_the_judgment"][0]["missing_strength_dimensions"] == ["PUBLISHED_CONTRACT"]
    inc = evaluate_judgment(_build(_base().incomplete("F1")))["not_determinable_detail"]
    (mi,) = inc["missing_strength_dimensions"]
    assert (mi["state"], mi["cause"]) == ("INSUFFICIENT_EVIDENCE", "PUBLISHED_LINEAGE_INCOMPLETE") and inc["evidence_sufficient"] is False


def test_the_detail_exists_only_for_lvl06_and_never_changes_the_level():
    assert "not_determinable_detail" not in evaluate_judgment(buena_model().build())
    unjudgeable = evaluate_judgment({"assessment_state": "NOT_ASSESSED"})
    assert unjudgeable["status"] == "NOT_DETERMINABLE" and "not_determinable_detail" not in unjudgeable          # otros ND (no LVL-06) no cambian de forma
    assert _nd_missing_pub_only()["level"] is None and _nd_missing_pub_only()["status"] == "NOT_DETERMINABLE"


def test_the_presenter_says_evidence_is_sufficient_when_only_strengths_are_missing():
    v = presenter.judgment_view(_nd_missing_pub_only())
    assert v["determined"] is False and v["nd_evidence_sufficient"] is True
    assert "suficiente" in v["headline"] and "Contrato publicado" in v["headline"]
    assert v["nd_missing"][0]["state"] == "NO_STRENGTH" and v["nd_missing"][0]["cause_text"]
    vi = presenter.judgment_view(evaluate_judgment(_build(_base().incomplete("F1"))))
    assert vi["nd_evidence_sufficient"] is False and "Falta evidencia en: Contrato publicado" in vi["headline"]


# ── versionado y migración dirigida (mecanismo existente: --backfill-judgment) ──
def test_the_judgment_ruleset_is_versioned_and_the_schema_is_unchanged():
    assert RULESET_VERSION == "judgment-rules/v2" and JUDGMENT_SCHEMA_VERSION == "judgment/v1"
    assert E.evaluate_judgment(buena_model().build())["ruleset_version"] == "judgment-rules/v2"


def test_a_persisted_v1_judgment_is_classified_version_stale_and_rederived_from_the_persisted_profile():
    prof = _build(_base())
    j = evaluate_judgment(prof)
    old = {**j, "ruleset_version": "judgment-rules/v1", "status": "NOT_DETERMINABLE", "level": None, "level_rule": "LVL-06", "output_signature": "sha256:old"}
    env = {"profile_json": prof, "judgment": old}
    action, fresh = svc.classify_judgment_backfill(env)
    assert action == svc.JDG_VERSION_STALE and (fresh["status"], fresh["level"]) == ("DETERMINED", "BUENA") and fresh["ruleset_version"] == "judgment-rules/v2"
    env2 = {"profile_json": prof, "judgment": j}
    assert svc.classify_judgment_backfill(env2)[0] == svc.JDG_CURRENT                                         # idempotente
    assert svc.classify_judgment_backfill({"profile_json": None, "judgment": old})[0] == svc.JDG_NO_PROFILE
