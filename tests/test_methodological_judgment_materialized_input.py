"""MATERIALIZED_INPUT — integracion OPT-IN de ``leaf-materialization/v1`` con las variantes experimentales T5_LOCAL_PHYSICAL y LEVEL_RESOLUTION.

Contrato: la derivacion es un MODIFICADOR de entrada (no una regla); sin el, T5/LEVEL se comportan exactamente como antes; sin CSN explicito las relaciones no verificables quedan UNKNOWN y no aportan evidencia
positiva; ``materialization_kind``, ``derived_boundary_class``, ``filter_nature``, gobierno de la fuente (``source_contract_classes``) y estado de cada dimension se mantienen INDEPENDIENTES. T4_HAR no se toca."""
from __future__ import annotations

import copy
import glob
import gzip
import json
import os
from pathlib import Path

import pytest

from tests._judgment_cases import BENCHMARK, fresh
from tests.test_leaf_materialization import CDS, DF_CONSUMER, DF_PRODUCER, PHYS, PROJECTION, STDVIEW, bnd, dcsn, df, ident, lp, pbl, prod, rf
from tests.test_methodological_judgment_level_resolution import LV, _replica_model, states_of
from tests.test_methodological_judgment_rules import buena_model, reason, rules
from tests.test_methodological_judgment_t4_t5 import T4, T5, _with_local_leaf
from tools.catalog.methodological_judgment import contract as C
from tools.catalog.methodological_judgment import evaluate_judgment
from tools.catalog.methodological_judgment.profile_access import ProfileEvidence

MI = C.VARIANT_MATERIALIZED_INPUT
T5M = (C.VARIANT_T5_LOCAL_PHYSICAL, MI)
LVM = (C.VARIANT_LEVEL_RESOLUTION, MI)
S, M, D, NE, NA, UN = C.DS_STRENGTH, C.DS_MET, C.DS_DEFICIENCY, C.DS_NOT_EVALUABLE, C.DS_NOT_APPLICABLE, C.DS_UNRESOLVED


def mat(p: dict, producers: list[dict], flows: list[dict], pb_record: dict | None, basis="REPLICATION_FLOW", leaf="LT1") -> dict:
    """Reemplaza la procedencia/flujos/bordes de ``leaf`` en un perfil sintetico por evidencia con la FORMA persistida."""
    ce = p["canonical_evidence"]
    for rec in ce["leaf_provenance"]:
        if rec["leaf"] == leaf:
            rec.update(basis=basis, chain_complete=True, producer_flows=producers)
    ce["processing_flow_properties"] = {"flows": flows}
    ce["physical_source_boundaries"] = {"leaves": [pb_record] if pb_record else []}
    return p


def rf_replica(leaf="LT1", cls=CDS, mappings=None, delta_shadow=None) -> dict:
    p = _replica_model()
    return mat(p, [prod("REPLICATION_FLOW", "RF1", load_types=["REPLICATE"])], [rf("RF1", leaf, mappings)], pbl(leaf, [bnd(None, "CDS_X", cls, ["RF1"])], ["RF1"]))


def delta_profile(leaf="LT1", shadow="LT1_Delta") -> dict:
    p = _replica_model()
    flow = rf("RF1", shadow)
    flow["closure_targets"] = flow["targets"] = [shadow]
    return mat(p, [prod("REPLICATION_FLOW", "RF1", match="DELTA_SHADOW_SUFFIX", load_types=["REPLICATE"])], [flow], pbl(leaf, [bnd(None, "CDS_X", CDS, ["RF1"])], ["RF1"]))


def csn_for(leaf="LT1", shadow="LT1_Delta") -> dict:
    return dcsn(leaf=leaf, shadow=shadow)


def phys_profile(kind="COPY", cls=PHYS, boundary="PHYSICAL_TABLE", bounds=None, gaps=None, complete=True, producers=None, flows=None, match="EXACT") -> dict:
    """Tabla local alimentada por un Data Flow desde una vista intermedia hasta tablas ABAP fisicas (OCUPACION-like)."""
    p = _with_local_leaf("DATA_FLOW", boundary)
    comps = (DF_CONSUMER, DF_PRODUCER) + ((PROJECTION,) if kind == "TRANSFORMED" else ())
    return mat(p, producers if producers is not None else [prod("DATA_FLOW", "DF1", match=match)], flows if flows is not None else [df("DF1", "LT1", ident("A"), comps)],
               pbl("LT1", bounds if bounds is not None else [bnd("B1", "ABAPTABLES.MCHB", cls, ["DF1"])], ["DF1"], gaps=gaps, complete=complete), basis="DATA_FLOW")


def reasons_of(j):
    return sorted((r["rule_id"], r["classification"]) for r in j["reasons"])


# ───────────────────────────── el modificador no cambia nada por si solo ni sin T5/LEVEL ─────────────────────────────
def test_without_the_modifier_t5_and_level_never_touch_the_materialization(monkeypatch):
    def boom(self):
        raise AssertionError("la derivacion no debe consultarse sin MATERIALIZED_INPUT")
    monkeypatch.setattr(ProfileEvidence, "materialization", property(boom))
    for p in (rf_replica(), phys_profile(), buena_model().build()):
        for v in ((), T4, T5, LV, (*T4, *T5, *LV)):
            evaluate_judgment(p, variants=v)


def test_the_modifier_alone_changes_no_level_reason_or_strength_and_its_options_are_recorded():
    for p in (rf_replica(), phys_profile(), buena_model().build()):
        base, only = evaluate_judgment(p), evaluate_judgment(p, variants=(MI,))
        assert (only["status"], only["level"], only["level_rule"], only["strengths"], only["reasons"]) == (base["status"], base["level"], base["level_rule"], base["strengths"], base["reasons"])
        assert only["ruleset_options"] == {"variants": [MI]} and "materialization" not in only["ruleset_options"]            # sin T5/LEVEL no se consume


def test_golden_signatures_are_unchanged_when_no_variant_is_active_and_a_csn_without_the_modifier_is_ignored():
    for alias in sorted(BENCHMARK):
        p = fresh(alias)
        base = evaluate_judgment(copy.deepcopy(p))
        assert evaluate_judgment(copy.deepcopy(p), variants=())["output_signature"] == base["output_signature"] and "ruleset_options" not in base
        assert evaluate_judgment(copy.deepcopy(p), materialization_csn=csn_for())["output_signature"] == base["output_signature"]                # CSN sin modificador: ignorado
        full = evaluate_judgment(copy.deepcopy(p), variants=(*T4, *T5, *LV, MI))
        assert (full["status"], full["level"], full["level_rule"]) == (base["status"], base["level"], base["level_rule"]), alias            # controles Buena y LVL-05 intactos


def test_the_variants_set_includes_the_modifier_and_unknown_variants_still_fail():
    assert MI in C.VARIANTS and C.VARIANT_T4_HAR in C.VARIANTS
    with pytest.raises(ValueError):
        evaluate_judgment(rf_replica(), variants=("MATERIALIZED_INPUT_V2",))


# ───────────────────────────── T5 integrada ─────────────────────────────
def test_t5_materialized_counts_a_local_table_with_verified_physical_provenance_through_a_data_flow_chain():
    p = phys_profile()
    j = evaluate_judgment(p, variants=T5M)
    r = reason(j, "SRC-LOC-01")
    assert "LT1" in r["affected_artifacts"] and r["classification"] == "LOCAL_WEAKNESS"
    assert "SRC.local_table_physical_provenance_verified_by_leaf_materialization" in r["deterministic_basis"] and "ce:physical_source_boundaries/LT1" in r["evidence_refs"]
    base = evaluate_judgment(p)
    assert (j["status"], j["level"], j["level_rule"]) == (base["status"], base["level"], base["level_rule"])                                          # LOCAL: nunca cambia el nivel
    legacy = evaluate_judgment(p, variants=T5)
    assert {a for a in reason(legacy, "SRC-LOC-01")["affected_artifacts"]} == {a for a in r["affected_artifacts"]}                                    # misma poblacion que T5 anterior


@pytest.mark.parametrize("kind", ["COPY", "TRANSFORMED"])
def test_t5_materialized_is_independent_of_the_materialization_kind_and_the_filter(kind):
    p = phys_profile(kind)
    assert "SRC-LOC-01" in rules(evaluate_judgment(p, variants=T5M), "LOCAL_WEAKNESS")
    assert ProfileEvidence(p).materialization_of("LT1")["materialization_kind"] == kind
    q = phys_profile(kind, flows=[df("DF1", "LT1", ident("A"), (DF_CONSUMER, DF_PRODUCER) + ((PROJECTION,) if kind == "TRANSFORMED" else ()), where=[{"ref": ["A"]}, "=", {"val": "1"}])])
    assert ProfileEvidence(q).materialization_of("LT1")["flags"]["filtered"] is True and "SRC-LOC-01" in rules(evaluate_judgment(q, variants=T5M), "LOCAL_WEAKNESS")


def test_t5_materialized_also_covers_a_replication_flow_that_brings_in_a_physical_table():
    p = _with_local_leaf("REPLICATION_FLOW", "PHYSICAL_TABLE")
    mat(p, [prod("REPLICATION_FLOW", "RF1", load_types=["REPLICATE"])], [rf("RF1", "LT1")], pbl("LT1", [bnd(None, "ABAPTABLES.MARA", PHYS, ["RF1"])], ["RF1"]))
    assert "SRC-LOC-01" not in rules(evaluate_judgment(p, variants=T5))                      # T5 anterior: solo Data Flow
    assert "SRC-LOC-01" in rules(evaluate_judgment(p, variants=T5M), "LOCAL_WEAKNESS")        # integrada: contacto fisico comprobado, venga por el flujo que venga


@pytest.mark.parametrize("tag, kw", [
    ("cadena con huecos", dict(gaps=["OUTSIDE_POOL"])),
    ("cadena incompleta", dict(complete=False)),
    ("borde gobernado", dict(bounds=[bnd("B1", "ABAP_CDS.X", CDS, ["DF1"])])),
    ("borde mixto gobernado + fisico", dict(bounds=[bnd("B1", "ABAPTABLES.MCHB", PHYS, ["DF1"]), bnd("B2", "ABAPTABLES.V", STDVIEW, ["DF1"])])),
    ("borde sin clasificar", dict(bounds=[bnd("B1", "ABAPTABLES.MCHB", "UNCLASSIFIED", ["DF1"])])),
    ("productor no EXACT", dict(match="DELTA_SHADOW_SUFFIX")),
    ("evidencia contradictoria (productor fuera de los flujos del borde)", dict(bounds=[bnd("B1", "ABAPTABLES.MCHB", PHYS, ["DF_OTRO"])])),
])
def test_t5_materialized_does_not_count_unverified_or_non_physical_provenance(tag, kw):
    p = phys_profile(**kw)
    if "bounds" in kw and any(b["via_flows"] == ["DF_OTRO"] for b in kw["bounds"]):
        p["canonical_evidence"]["physical_source_boundaries"]["leaves"][0]["flows"] = ["DF_OTRO"]
    assert "SRC-LOC-01" not in rules(evaluate_judgment(p, variants=T5M)), tag


def test_t5_materialized_needs_the_explicit_source_class_to_agree_and_every_local_table_is_not_a_raw_boundary():
    # derivacion fisica pero la clase explicita de fuente dice gobernada: la identidad/gobierno de la fuente manda (no se cuenta)
    p = phys_profile(boundary="GOVERNED_CDS_CONTRACT")
    assert ProfileEvidence(p).materialization_of("LT1")["derived_boundary_class"] == "PHYSICAL_DERIVED"
    assert "SRC-LOC-01" not in rules(evaluate_judgment(p, variants=T5M))
    assert "SRC-LOC-01" not in rules(evaluate_judgment(buena_model().build(), variants=T5M))
    assert "SRC-LOC-01" not in rules(evaluate_judgment(rf_replica(), variants=T5M))           # replica de un contrato gobernado: no es frontera cruda


def test_t5_materialized_never_deduces_by_name():
    p = phys_profile()
    for rec in p["canonical_evidence"]["leaf_provenance"]:
        if rec["leaf"] == "LT1":
            rec["leaf"] = "RAW_TABLE_PHYSICAL_ABAP_MCHB"                                     # un nombre que "parece" fisico no hace nada; la evidencia (clase/borde/productor) esta ligada a LT1
    assert "RAW_TABLE_PHYSICAL_ABAP_MCHB" not in (reason(evaluate_judgment(p, variants=T5M), "SRC-LOC-01") if "SRC-LOC-01" in rules(evaluate_judgment(p, variants=T5M)) else {"affected_artifacts": []})["affected_artifacts"]


# ───────────────────────────── LEVEL_RESOLUTION integrada ─────────────────────────────
def test_an_accredited_replica_gives_responsibility_met_with_materialization_qualifiers_and_nothing_else():
    p = rf_replica()
    base, legacy, v = evaluate_judgment(p), evaluate_judgment(p, variants=LV), evaluate_judgment(p, variants=LVM)
    assert (v["status"], v["level"], v["level_rule"]) == ("DETERMINED", "ADECUADA", "LVL-08") == (legacy["status"], legacy["level"], legacy["level_rule"])
    ds = {d["dimension"]: d for d in v["dimension_states"]}
    assert ds["PROPAGATION"]["state"] == M and "PRO.product_consumes_only_a_technically_accredited_replica_of_a_governed_contract" in ds["PROPAGATION"]["basis"]
    q = ds["PROPAGATION"]["materialization"][0]
    assert (q["leaf"], q["materialization_kind"], q["derived_boundary_class"], q["filtered"], q["implicit_identity"]) == ("LT1", "REPLICA", "GOVERNED_DERIVED", False, True)
    assert v["strengths"] == base["strengths"] and v["reasons"] == base["reasons"]                                                     # REPLICA no es fortaleza ni crea/quita razones
    assert states_of(v)["SOURCE_INGESTION"] == states_of(legacy)["SOURCE_INGESTION"] and states_of(v)["PUBLISHED_CONTRACT"] == states_of(legacy)["PUBLISHED_CONTRACT"]
    assert v["ruleset_options"]["variants"] == sorted(LVM) and v["ruleset_options"]["materialization"]["csn_evidence_provided"] is False


@pytest.mark.parametrize("tag, mappings, producers, flows", [
    ("renombre real (transformacion tecnica)", [{"target": "FKDAT_2", "expression": '"FKDAT"'}], None, None),
    ("renombre solo-mayusculas sin CSN (no verificable)", [{"target": "audat", "expression": '"AUDAT"'}], None, None),
    ("replica escrita ademas por un Data Flow (multi-productor)", None, [prod("REPLICATION_FLOW", "RF1"), prod("DATA_FLOW", "DF1")],
     [rf("RF1", "LT1"), df("DF1", "LT1", ident("A"))]),
    ("copia por Data Flow (no es una replica)", None, [prod("DATA_FLOW", "DF1")], [df("DF1", "LT1", ident("A"))]),
])
def test_only_an_accredited_replica_counts_transformations_copies_and_multi_producers_do_not(tag, mappings, producers, flows):
    p = rf_replica(mappings=mappings)
    if producers is not None:
        mat(p, producers, flows, pbl("LT1", [bnd(None, "CDS_X", CDS, [x["name"] for x in producers])], [x["name"] for x in producers]), basis="REPLICATION_FLOW")
    v = evaluate_judgment(p, variants=LVM)
    assert states_of(v)["PROPAGATION"] == UN and v["status"] == "NOT_DETERMINABLE" and v["level_rule"] == "LVL-06", tag


def test_replica_without_governed_source_class_or_with_a_different_derived_class_is_not_responsibility_met():
    p = rf_replica(cls=PHYS)                                              # la derivacion dice fisica: no hay gobierno
    assert states_of(evaluate_judgment(p, variants=LVM))["PROPAGATION"] == UN
    p = rf_replica()
    p["canonical_evidence"]["source_contract_classes"]["leaves"] = [dict(r, contract_class="PHYSICAL_ABAP_TABLE_BOUNDARY") if r["leaf"] == "LT1" else r for r in p["canonical_evidence"]["source_contract_classes"]["leaves"]]
    assert states_of(evaluate_judgment(p, variants=LVM))["PROPAGATION"] == UN     # gobierno = clase explicita; la replica no lo otorga
    q = rf_replica()
    q["canonical_evidence"]["physical_source_boundaries"]["leaves"][0]["chain_gaps"] = ["OUTSIDE_POOL"]
    assert states_of(evaluate_judgment(q, variants=LVM))["PROPAGATION"] == UN


def test_a_delta_shadow_without_csn_degrades_safely_and_with_explicit_csn_is_met_with_a_cdc_filter_qualifier():
    p = delta_profile()
    legacy = evaluate_judgment(p, variants=LV)
    assert states_of(legacy)["PROPAGATION"] == M                                                    # T5/LEVEL anterior: aceptaba cualquier RF sin verificar la relacion
    safe = evaluate_judgment(p, variants=LVM)                                                       # perfil persistido SIN CSN: no verificable => UNKNOWN => sin evidencia positiva
    assert states_of(safe)["PROPAGATION"] == UN and (safe["status"], safe["level_rule"]) == ("NOT_DETERMINABLE", "LVL-06") and safe["ruleset_options"]["materialization"]["csn_evidence_provided"] is False
    full = evaluate_judgment(p, variants=LVM, materialization_csn=csn_for())
    ds = {d["dimension"]: d for d in full["dimension_states"]}
    assert ds["PROPAGATION"]["state"] == M and (full["level"], full["level_rule"]) == ("ADECUADA", "LVL-08")
    q = ds["PROPAGATION"]["materialization"][0]
    assert q["resolved_via"] == ["CSN_DELTA_LINK"] and q["filtered"] is True and q["filter_natures"] == ["ROW_FILTER"]               # filtro CDC: calificador, ni transformacion ni exposicion integra
    assert full["ruleset_options"]["materialization"]["csn_evidence_provided"] is True
    assert full["ruleset_options"]["materialization"]["digest"] != safe["ruleset_options"]["materialization"]["digest"] and full["output_signature"] != safe["output_signature"]
    assert evaluate_judgment(p, variants=LVM, materialization_csn=csn_for())["output_signature"] == full["output_signature"]          # determinista


def test_a_declared_delta_link_without_a_verifiable_wrapper_stays_unknown_even_with_csn():
    p = delta_profile()
    csn = csn_for()
    del csn["entity_definitions"]["LT1"]["query"]
    assert states_of(evaluate_judgment(p, variants=LVM, materialization_csn=csn))["PROPAGATION"] == UN
    csn = csn_for()
    csn["entity_definitions"]["LT1"]["query"]["SELECT"]["from"] = {"ref": ["OTRA"]}
    assert states_of(evaluate_judgment(p, variants=LVM, materialization_csn=csn))["PROPAGATION"] == UN


def test_unknown_not_evaluable_not_applicable_and_deficiency_keep_their_distinct_semantics_under_the_integration():
    for p in (_replica_model(join=True), _replica_model(state=None), buena_model().remote("RAW1", raw=True, spine=False).build()):
        a, b = states_of(evaluate_judgment(p, variants=LV)), states_of(evaluate_judgment(p, variants=LVM))
        assert {k: v for k, v in a.items() if k != "PROPAGATION"} == {k: v for k, v in b.items() if k != "PROPAGATION"}          # HARMONIZATION (UNRESOLVED por el join), SOURCE (DEFICIENCY) y las demas no se mueven
    for p in (_replica_model(state=None), buena_model().remote("RAW1", raw=True, spine=False).build()):
        assert states_of(evaluate_judgment(p, variants=LV)) == states_of(evaluate_judgment(p, variants=LVM))                     # sin replica acreditable en juego, ningun estado cambia
    # la replica sintetica SIN respaldo en processing_flow_properties (el productor no figura entre los flujos) ya no es evidencia positiva: se degrada a UNRESOLVED, no a DEFICIENCY ni NOT_EVALUABLE
    assert states_of(evaluate_judgment(_replica_model(), variants=LV))["PROPAGATION"] == M and states_of(evaluate_judgment(_replica_model(), variants=LVM))["PROPAGATION"] == UN
    p = rf_replica(mappings=[{"target": "X", "expression": '"Y"'}])                                                                    # TRANSFORMED: UNRESOLVED (sin evidencia positiva), no DEFICIENCY, no NOT_EVALUABLE
    st = states_of(evaluate_judgment(p, variants=LVM))
    assert st["PROPAGATION"] == UN and UN != NE != NA != D


def test_t4_is_untouched_by_the_integration_and_the_buena_controls_stay_buena():
    p = buena_model().build()
    for v in ((*T4, MI), (*T4, *T5, *LV, MI)):
        j = evaluate_judgment(p, variants=v)
        assert (j["status"], j["level"], j["level_rule"]) == ("DETERMINED", "BUENA", "LVL-04")
    from tests.test_methodological_judgment_rules import har_model
    h = har_model(authored=True).build()
    assert evaluate_judgment(h, variants=(*T4, MI))["strengths"] == evaluate_judgment(h, variants=T4)["strengths"]


def test_integration_is_read_only_over_the_profile():
    for p in (rf_replica(), delta_profile(), phys_profile()):
        snap = copy.deepcopy(p)
        evaluate_judgment(p, variants=(*T4, *T5, *LV, MI), materialization_csn=csn_for())
        assert p == snap


# ───────────────────────────── aislamiento arquitectonico ─────────────────────────────
def test_judgment_reaches_the_derivation_only_through_the_neutral_module_and_never_duplicates_its_logic():
    pkg = Path(__file__).resolve().parents[1] / "tools" / "catalog" / "methodological_judgment"
    for f in pkg.glob("*.py"):
        assert "modeling_assessment" not in f.read_text(encoding="utf-8"), f.name                                    # el guard de independencia sigue intacto
    rules_src = (pkg / "rules.py").read_text(encoding="utf-8")
    assert "from tools.catalog import leaf_materialization as LM" in rules_src
    for dup in ("physical_source_boundaries\").get(\"leaves\")", "attribute_mappings", "deltaFromEntities"):          # no relee ni reinterpreta los hechos que la derivacion ya resume
        assert dup not in rules_src, dup
    neutral = Path(__file__).resolve().parents[1] / "tools" / "catalog" / "leaf_materialization.py"
    code = "; ".join(l for l in neutral.read_text(encoding="utf-8").splitlines() if l.lstrip().startswith(("import ", "from ")))
    assert "methodological_judgment" not in code and "modeling_assessment" not in code
