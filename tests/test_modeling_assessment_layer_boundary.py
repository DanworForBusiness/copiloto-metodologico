"""LAYER_BOUNDARY_REVIEW (signal-rules/v4): primer consumidor FUNCIONAL, capa respaldada por evidencia y fuentes de contrato gobernado.

Antes (v3) la revision decidia «consumida directamente por la capa >= 3» con la capa INFERIDA del consumidor inmediato; ese consumidor era a menudo una vista de paso (proyeccion 1:1) cuya
capa salia del ultimo recurso de la heuristica (el NOMBRE empieza con ``V…``/``VR``). Ahora: (a) se atraviesan las vistas de paso (misma definicion que el Juicio); (b) NAME_PREFIX/UNKNOWN no
son fundamento; (c) una fuente con contrato de origen gobernado no genera la revision solo por carecer de una frontera de armonizacion. Pruebas end-to-end sobre pools CSN sinteticos y genericos.
"""
from __future__ import annotations

import copy

import pytest

from tests import _layer_boundary_pool as LB
from tests.test_modeling_assessment_signals import _engine_facts
from tools.catalog import layer_semantics
from tools.catalog.methodological_judgment import evaluate_judgment
from tools.catalog.methodological_judgment import profile_access
from tools.catalog.modeling_assessment import contract as C
from tools.catalog.modeling_assessment import layer_evidence
from tools.catalog.modeling_assessment import model as m
from tools.catalog.modeling_assessment import profile as P
from tools.catalog.modeling_assessment import render_md
from tools.catalog.modeling_assessment import shadow as ma_shadow
from tools.catalog.modeling_assessment import signals as S

V = LB.view


def assess(tmp_path, topology):
    root = LB.write_pool(tmp_path, topology)
    doc, _meta = ma_shadow.assess(root, LB.CLIENT, LB.AM, "General")
    assert doc["assessment_state"] == "ASSESSED"
    P.validate_assessment(doc)
    return doc


def lbr(doc):
    return [r for r in doc["review_required"] if r["id"] == "LAYER_BOUNDARY_REVIEW"]


def occurrences(doc):
    return {o["values"]["source"]: o for r in lbr(doc) for o in r["occurrences"]}


# ── 1. CDS gobernada + proyeccion 1:1 ─────────────────────────────────────────────────────────────────────────
def test_a_governed_cds_behind_a_one_to_one_projection_does_not_raise_the_review(tmp_path):
    doc = assess(tmp_path, {"leaves": {"SRC_CDS": LB.CDS}, "views": [V("VR_CDS", ["SRC_CDS"]), V("VF_NODE", ["VR_CDS"], "filter", "ANALYTICAL_FACT")], "am_from": "VF_NODE"})
    assert lbr(doc) == []
    rule = next(r for r in doc["rule_results"] if r["rule"] == "LAYER_BOUNDARY_REVIEW")
    assert rule["result"] == "ASSESSED_ABSENCE" and rule["finding_count"] == 0            # evaluada y sin hallazgo: no es «no evaluada»


# ── 2. Tabla fisica SAP + proyeccion 1:1 ───────────────────────────────────────────────────────────────────────
def test_a_physical_sap_table_behind_a_projection_reports_the_functional_consumer_and_never_a_direct_connection(tmp_path):
    doc = assess(tmp_path, {"leaves": {"SRC_TAB": LB.TABLE}, "views": [V("VR_TAB", ["SRC_TAB"]), V("VF_NODE", ["VR_TAB"], "filter", "ANALYTICAL_FACT")], "am_from": "VF_NODE"})
    (occ,) = occurrences(doc).values()
    v = occ["values"]
    assert v["source"] == "SRC_TAB"
    assert v["lineage_consumers"] == [{"consumer": "VR_TAB", "inferred_layer": "3", "inferred_layer_basis": "NAME_PREFIX"}]              # consumidor INMEDIATO, tal cual
    assert v["functional_consumers"] == [{"consumer": "VF_NODE", "inferred_layer": "3", "inferred_layer_basis": "DECLARED_ROLE", "reached_through": ["VR_TAB"]}]
    assert occ["affected_artifacts"] == ["SRC_TAB", "VF_NODE", "VR_TAB"]
    assert {"ingestion.first_consumers@SRC_TAB", "harmonization.view_complexity@VR_TAB", "harmonization.view_complexity@VF_NODE",
            "propagation.contract_delta@VR_TAB->VF_NODE", "source.contract_class@SRC_TAB"} <= set(occ["evidence_facts"])
    review = lbr(doc)[0]
    blob = " ".join([review["title"], review["explanation"], review["why_shown"], *review["interpretation_limits"]])
    assert "funcional" in blob and "directamente" not in blob.replace("no se afirma una conexión directa", "")      # no afirma una conexion directa inexistente
    assert "no se afirma una conexión directa" in blob


def test_the_markdown_render_distinguishes_the_immediate_consumer_from_the_functional_one(tmp_path):
    doc = assess(tmp_path, {"leaves": {"SRC_TAB": LB.TABLE}, "views": [V("VR_TAB", ["SRC_TAB"]), V("VF_NODE", ["VR_TAB"], "filter", "ANALYTICAL_FACT")], "am_from": "VF_NODE"})
    (occ,) = occurrences(doc).values()
    line = render_md._fmt_layer_boundary(occ)
    assert "primer consumidor funcional" in line and "VF_NODE" in line and "a través de" in line and "VR_TAB" in line and "directamente" not in line
    legacy = {"values": {"source": "SRC", "lineage_consumers": [{"consumer": "VR_X", "inferred_layer": "3"}]}}                  # ocurrencia persistida con signal-rules/v3
    assert "consumida directamente por" in render_md._fmt_layer_boundary(legacy)


# ── 3. Consumidor con rol declarado FACT ───────────────────────────────────────────────────────────────────────
def test_a_declared_fact_consumer_is_the_functional_consumer_without_any_look_through(tmp_path):
    doc = assess(tmp_path, {"leaves": {"SRC_TAB": LB.TABLE}, "views": [V("VF_NODE", ["SRC_TAB"], "filter", "ANALYTICAL_FACT")], "am_from": "VF_NODE"})
    (occ,) = occurrences(doc).values()
    assert occ["values"]["functional_consumers"] == [{"consumer": "VF_NODE", "inferred_layer": "3", "inferred_layer_basis": "DECLARED_ROLE", "reached_through": []}]
    assert occ["affected_artifacts"] == ["SRC_TAB", "VF_NODE"]


# ── 4. Capa inferida exclusivamente por NAME_PREFIX ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kind", ["filter", "pass"])
def test_a_layer_inferred_only_by_name_prefix_is_never_enough_to_claim_a_layer_jump(tmp_path, kind):
    # VR_NODE no declara rol ni nomenclatura de capas: su «capa 3» sale del prefijo del nombre. Con logica propia (filter) es su propio consumidor funcional; de paso, se
    # atraviesa y el AM (rol declarado cubo) pasa a ser el consumidor funcional... salvo que el AM sea el unico consumidor: se cubre abajo.
    topo = {"leaves": {"SRC_TAB": LB.TABLE}, "views": [V("VR_NODE", ["SRC_TAB"], "filter")], "am_from": "VR_NODE"}
    if kind == "pass":
        topo["views"] = [V("VR_NODE", ["SRC_TAB"], "pass")]
    doc = assess(tmp_path, topo)
    occ = occurrences(doc)
    if kind == "filter":
        assert occ == {}                                                                  # NAME_PREFIX no alcanza
    else:                                                                                 # proyeccion pura directo al AM: el consumidor funcional es el AM (capa 4 por rol declarado, con evidencia)
        assert occ["SRC_TAB"]["values"]["functional_consumers"][0]["consumer"] == LB.AM
        assert occ["SRC_TAB"]["values"]["functional_consumers"][0]["inferred_layer_basis"] == "DECLARED_ROLE"


# ── 5. Vistas intermedias con armonizacion efectiva ─────────────────────────────────────────────────────────────
def test_an_intermediate_view_with_effective_harmonization_and_an_explicit_layer_2_is_not_flagged(tmp_path):
    doc = assess(tmp_path, {"leaves": {"SRC_TAB": LB.TABLE}, "views": [V("2VR_HAR", ["SRC_TAB"], "filter"), V("VF_NODE", ["2VR_HAR"], "filter", "ANALYTICAL_FACT")], "am_from": "VF_NODE"})
    assert occurrences(doc) == {}


def test_a_pass_through_view_that_merely_carries_an_explicit_layer_2_name_is_looked_through(tmp_path):
    # un nombre de capa 2 NO convierte una proyeccion 1:1 en armonizacion: no hace nada, asi que el primer consumidor funcional es el siguiente (mismo criterio que el colapso del Juicio)
    doc = assess(tmp_path, {"leaves": {"SRC_TAB": LB.TABLE}, "views": [V("2VR_PT", ["SRC_TAB"]), V("VF_NODE", ["2VR_PT"], "filter", "ANALYTICAL_FACT")], "am_from": "VF_NODE"})
    (occ,) = occurrences(doc).values()
    assert occ["values"]["lineage_consumers"][0]["inferred_layer"] == "2" and occ["values"]["lineage_consumers"][0]["inferred_layer_basis"] == "SECTION_28_NAME"
    assert [c["consumer"] for c in occ["values"]["functional_consumers"]] == ["VF_NODE"] and occ["values"]["functional_consumers"][0]["reached_through"] == ["2VR_PT"]


# ── 6. Mezcla de hojas gobernadas y fisicas ─────────────────────────────────────────────────────────────────────
def test_a_model_mixing_governed_and_physical_leaves_flags_only_the_physical_one_and_keeps_the_other_rules(tmp_path):
    doc = assess(tmp_path, {"leaves": {"SRC_TAB": LB.TABLE, "SRC_CDS": LB.CDS},
                            "views": [V("VR_TAB", ["SRC_TAB"]), V("VR_CDS", ["SRC_CDS"]), V("VF_NODE", ["VR_TAB", "VR_CDS"], "join", "ANALYTICAL_FACT")], "am_from": "VF_NODE"})
    occ = occurrences(doc)
    assert set(occ) == {"SRC_TAB"}
    assert "SRC_CDS" not in occ["SRC_TAB"]["affected_artifacts"]
    # (c) es ACOTADA: la fuente gobernada sigue sujeta a las demas reglas (p. ej. el acceso directo a tabla fisica sigue reportando la tabla, y sus hechos siguen en el perfil)
    assert [o["values"]["remote_entity"] for g in doc["observations"] if g["id"] == "DIRECT_RAW_TABLE_ACCESS" for o in g["occurrences"]] == [LB.TABLE]
    assert any(f["fact"] == "source.contract_class@SRC_CDS" for f in doc["fact_index"])


# ── 7. Linaje ambiguo o incompleto ──────────────────────────────────────────────────────────────────────────────
def test_incomplete_lineage_never_produces_the_review(tmp_path):
    root = LB.write_pool(tmp_path, {"leaves": {"SRC_TAB": LB.TABLE}, "views": [V("VR_TAB", ["SRC_TAB"]), V("VF_NODE", ["VR_TAB"], "filter", "ANALYTICAL_FACT")], "am_from": "VF_NODE"})
    facts = _engine_facts(root, LB.CLIENT, LB.AM)
    assert [f.signal_id for f in S.evaluate_signals(facts).findings if f.signal_id == "LAYER_BOUNDARY_REVIEW"] == ["LAYER_BOUNDARY_REVIEW"]      # base: sin mutar, dispara
    no_edges = [f for f in facts if f.fact_id not in ("propagation.contract_delta", "am.contract_delta")]                                      # la vista de paso queda sin consumidor conocido
    assert not [f for f in S.evaluate_signals(no_edges).findings if f.signal_id == "LAYER_BOUNDARY_REVIEW"]
    no_views = [f for f in facts if f.fact_id != "harmonization.view_complexity"]                                                              # no se puede probar que sea de paso: no se asume transparencia
    assert not [f for f in S.evaluate_signals(no_views).findings if f.signal_id == "LAYER_BOUNDARY_REVIEW"]                                    # y VR_TAB (NAME_PREFIX) no es fundamento


def test_a_pass_through_view_feeding_consumers_in_different_layers_is_ambiguous_and_is_not_reported(tmp_path):
    topo = {"leaves": {"SRC_TAB": LB.TABLE},
            "views": [V("VR_PT", ["SRC_TAB"]), V("2VR_HAR", ["VR_PT"], "filter"), V("VF_NODE", ["VR_PT", "2VR_HAR"], "join", "ANALYTICAL_FACT")], "am_from": "VF_NODE"}
    assert occurrences(assess(tmp_path, topo)) == {}                                       # un camino llega a capa 2 con evidencia: no TODOS los consumidores funcionales estan en capa >= 3


# ── Definiciones compartidas y Judgment intacto ────────────────────────────────────────────────────────────────
def test_layer_evidence_bases_are_real_inferred_layer_bases_and_exclude_the_name_heuristics():
    bases = {b.value for b in C.InferredLayerBasis}
    assert layer_semantics.LAYER_EVIDENCE_BASES <= bases
    assert not {"NAME_PREFIX", "UNKNOWN", "REMOTE_ENTITY"} & layer_semantics.LAYER_EVIDENCE_BASES
    assert layer_semantics.LAYER_EVIDENCE_BASES == {"SECTION_28_NAME", "DECLARED_ROLE"}


def test_the_judgment_and_the_signals_share_one_definition_of_pass_through_and_governed_contract():
    assert profile_access._GOVERNED_CONTRACT_CLASSES is layer_semantics.GOVERNED_CONTRACT_CLASSES
    assert layer_evidence.GOVERNED_CONTRACT_CLASSES is layer_semantics.GOVERNED_CONTRACT_CLASSES and layer_evidence.LAYER_EVIDENCE_BASES is layer_semantics.LAYER_EVIDENCE_BASES
    assert layer_evidence.is_pass_through_view is layer_semantics.is_pass_through_view
    base = {"providers": 1, "metrics": {"select_nodes": 1, "joins": 0, "unions": 0, "derived_columns": 0, "filters": {"where_nodes": 0, "having_nodes": 0}}}
    variants = [base, None, {}, {**base, "providers": 2}, {**base, "metrics": {**base["metrics"], "joins": 1}}, {**base, "metrics": {**base["metrics"], "unions": 1}},
                {**base, "metrics": {**base["metrics"], "derived_columns": 2}}, {**base, "metrics": {**base["metrics"], "select_nodes": 2}},
                {**base, "metrics": {**base["metrics"], "filters": {"where_nodes": 1, "having_nodes": 0}}}, {**base, "metrics": {**base["metrics"], "filters": {"where_nodes": 0, "having_nodes": 1}}}]
    for v in variants:
        ev = profile_access.ProfileEvidence.__new__(profile_access.ProfileEvidence)
        ev.__dict__["views"] = {"X": v} if v is not None else {}
        assert ev.is_pass_through("X") == layer_semantics.is_pass_through_view(v), v
    assert [layer_semantics.is_pass_through_view(v) for v in variants] == [True, False, False, False, False, False, False, False, False, False]
    text = (S.__file__ and open(S.__file__, encoding="utf-8").read())
    assert "select_nodes" not in text and "where_nodes" not in text                       # el criterio de «vista de paso» no se duplica en las senales


def test_the_ruleset_is_versioned_and_the_signal_is_still_a_review_without_level_effect():
    assert S.SIGNALS_RULESET == "signal-rules/v4"
    spec = next(s for s in S.SIGNAL_REGISTRY if s.signal_id == "LAYER_BOUNDARY_REVIEW")
    assert spec.kind is m.SignalKind.REVIEW and "harmonization.view_complexity" in spec.required_fact_ids and "source.contract_class" in spec.required_fact_ids


def test_the_judgment_is_identical_with_or_without_the_review(tmp_path):
    for topo in ({"leaves": {"SRC_TAB": LB.TABLE}, "views": [V("VR_TAB", ["SRC_TAB"]), V("VF_NODE", ["VR_TAB"], "filter", "ANALYTICAL_FACT")], "am_from": "VF_NODE"},
                 {"leaves": {"SRC_TAB": LB.TABLE, "SRC_CDS": LB.CDS}, "views": [V("VR_TAB", ["SRC_TAB"]), V("VR_CDS", ["SRC_CDS"]), V("VF_NODE", ["VR_TAB", "VR_CDS"], "join", "ANALYTICAL_FACT")],
                  "am_from": "VF_NODE"}):
        doc = assess(tmp_path / str(len(topo["leaves"])), topo)
        assert lbr(doc)
        without = copy.deepcopy(doc)
        without["review_required"] = [r for r in without["review_required"] if r["id"] != "LAYER_BOUNDARY_REVIEW"]
        a, b = evaluate_judgment(doc), evaluate_judgment(without)
        strip = lambda j: {k: v for k, v in j.items() if k not in ("source_assessment", "output_signature")}            # noqa: E731
        assert strip(a) == strip(b)
        assert a["ruleset_version"] == "judgment-rules/v2" or a["ruleset_version"].startswith("judgment-rules/")
