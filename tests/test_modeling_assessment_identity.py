"""M1 — procedencia de implementacion e identidad (CLASIFICACION, no calidad).

Goldens SINTETICOS que validan las REGLAS DETERMINISTAS del motor v2 (no se fuerza ningun conteo historico):
    CASE 3-like  (35 artefactos): PROJECT_ASSIGNED 17 / SOURCE_DERIVED_IMPORTED 16 / UNKNOWN 2
    CASE 4-like  (54 artefactos): PROJECT_ASSIGNED 29 / SOURCE_DERIVED_IMPORTED 16 / SYSTEM 5 / UNKNOWN 4
El dossier HUMANO historico de CASE 4 conto 30/16/5/3 (con UNA decision heuristica: un remoto renombrado como
PROJECT_ASSIGNED). v2 es mas conservador: sin metadata que pruebe la identidad local, esa identidad es UNKNOWN.
Los nombres son genericos (sin datos de clientes reales); el replay real corre solo local.
"""
from __future__ import annotations

import pytest

from tools.catalog.modeling_assessment import legacy_bridge as bridge
from tools.catalog.modeling_assessment import model as m
from tools.catalog.modeling_assessment.identity import (
    IDENTITY_RULESET, Certainty, IdentityProvenance as I, IdentityRule as R, ImplementationProvenance as Impl,
    classify, classify_universe, heuristic_distribution, identity_distribution, implementation_distribution,
    name_tokens, remote_stem,
)

E = bridge.Entity


def remote(name, remote_entity, **kw):
    return E(name=name, file=f"remote-tables/{name}.json", remote_entity=remote_entity, remote_connection="CONN", **kw)


def authored(name, **kw):
    return E(name=name, file=f"views/{name}.json", has_query=True, **kw)


def structure(name, **kw):
    return E(name=name, file=f"local-tables/{name}.json", **kw)


# ── stems y tokens ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("remote_entity,stem", [
    ("ABAP_CDS.IFICOSTCENTER$P", "IFICOSTCENTER"), ("ABAP_CDS.ZCDS_DIM_X$E", "ZCDS_DIM_X"),
    ("SAPI.0COORDER_ATTR", "0COORDER_ATTR"), ("ABAPTABLES.GLPCP", "GLPCP"),
    ("ABAPTABLES./BIC/AZA_OCU_AL2", "AZA_OCU_AL2"), ('"dbo"."GP_Clientes"', "GP_Clientes"),
    ("ABAPTABLES.NSDM_V_MCHB", "NSDM_V_MCHB"), ("PLAIN", "PLAIN"), ("", ""), (None, ""),
])
def test_remote_stem(remote_entity, stem):
    assert remote_stem(remote_entity) == stem


def test_name_tokens_ignore_case_and_separators():
    assert name_tokens("1cds_Ifi-Cost.center") == ("1CDS", "IFI", "COST", "CENTER")
    assert name_tokens("") == () and name_tokens(None) == ()


# ── eje A: implementacion ──────────────────────────────────────────────────
def test_implementation_provenance_precedence():
    assert classify(authored("SAP.TIME.V")).implementation is Impl.SYSTEM_PROVIDED          # sistema gana sobre query
    assert classify(remote("A", "ABAP_CDS.A$E")).implementation is Impl.REMOTE_IMPORTED
    assert classify(authored("L", repository_type="DWC_LOCAL_TABLE")).implementation is Impl.LOCAL_TABLE   # query != vista autorada
    assert classify(authored("V")).implementation is Impl.QUERY_DEFINED
    assert classify(structure("S")).implementation is Impl.STRUCTURE_ONLY


# ── eje B: reglas de identidad (una prueba por regla) ──────────────────────
def test_rule_system_namespace():
    for n in ("SAP", "SAP.TIME.M_TIME_DIMENSION", "SAP.TIME.VIEW_DIMENSION_DAY"):
        c = classify(authored(n))
        assert (c.identity, c.rule, c.certainty) == (I.SYSTEM, R.ID_SYSTEM_NAMESPACE, Certainty.PROVEN)
    for n in ("SAP_CUSTOM_VIEW", "SAPX.THING", "sap.lower"):                                  # no es el namespace reservado
        assert classify(authored(n)).identity is not I.SYSTEM


def test_rule_source_exact_is_proven():
    c = classify(remote("IFICOSTCENTER", "ABAP_CDS.IFICOSTCENTER$P"))
    assert (c.identity, c.rule, c.certainty, c.remote_stem) == (
        I.SOURCE_DERIVED_IMPORTED, R.ID_SOURCE_EXACT, Certainty.PROVEN, "IFICOSTCENTER")


def test_rule_source_name_extends_stem_is_heuristic():
    for local, remote_entity in [("1CDS_IFICHOFACC", "ABAP_CDS.IFICHOFACC$P"), ("ZDSP_DIM_FACT_WIDGET", "ABAPTABLES.WIDGET"),
                                 ("1TR_ZCDS_DSP_DIM_ONE", "ABAP_CDS.ZCDS_DSP_DIM_ONE$E")]:
        c = classify(remote(local, remote_entity))
        assert (c.identity, c.rule, c.certainty) == (I.SOURCE_DERIVED_IMPORTED, R.ID_SOURCE_NAME_EXTENDS_STEM, Certainty.HEURISTIC)


def test_rule_source_name_abbreviates_stem_is_heuristic():
    c = classify(remote("ZCDS_DIM_THREE", "ABAP_CDS.ZCDS_DSP_DIM_THREE$E"))
    assert (c.identity, c.rule, c.certainty) == (I.SOURCE_DERIVED_IMPORTED, R.ID_SOURCE_NAME_ABBREVIATES_STEM, Certainty.HEURISTIC)


@pytest.mark.parametrize("local,remote_entity", [
    ("ZCDS_ITEM_BAK", "ABAP_CDS.ZCDS_ITEMBAK_LINE$F"),          # parte/une tokens: no es subsecuencia
    ("1TR_MATERIAL_LPRH_HIER", "SAPI.0MATERIAL_LPRH_HIER"),      # quita el `0` inicial de `0MATERIAL`
    ("1TR_COUNTRY_TEXT_S4", "ABAPTABLES.BWV_T005T"),             # nombre de negocio sin relacion de tokens con la tabla
])
def test_remote_whose_name_is_not_derivable_from_the_stem_is_unknown_never_a_guessed_project_assignment(local, remote_entity):
    """El motor v2 NO conjetura PROJECT_ASSIGNED por semejanza de nombres (decision del owner): UNKNOWN, sin
    certeza, con la evidencia (nombre + remote_entity + stem) registrada."""
    c = classify(remote(local, remote_entity))
    assert (c.identity, c.rule, c.certainty) == (I.UNKNOWN, R.ID_UNKNOWN_REMOTE_NAME_NOT_DERIVABLE, None)
    assert c.remote_stem == remote_stem(remote_entity)
    assert {r.attr_path for r in c.evidence} == {"name", "remote_entity"}
    assert c.implementation is Impl.REMOTE_IMPORTED


def test_no_rule_manufactures_project_assignment_from_name_resemblance():
    """PROJECT_ASSIGNED solo existe con una regla PROVEN (artefacto autorado): ninguna rama de remotos lo produce."""
    assert {r for r in R if r.name.startswith("ID_PROJECT")} == {R.ID_PROJECT_AUTHORED_QUERY}
    for local, remote_entity in [("A", "ABAP_CDS.B$E"), ("ZZZ_X", "SAPI.0Y"), ("1TR_FOO", "ABAPTABLES.BAR")]:
        assert classify(remote(local, remote_entity)).identity is not I.PROJECT_ASSIGNED


def test_rule_project_authored_query_is_proven():
    c = classify(authored("VD_SOMETHING"))
    assert (c.identity, c.rule, c.certainty) == (I.PROJECT_ASSIGNED, R.ID_PROJECT_AUTHORED_QUERY, Certainty.PROVEN)


def test_unknown_rules_have_no_certainty_and_are_never_guessed():
    c1 = classify(authored("ZCDS_DSP_STAGE", repository_type="DWC_LOCAL_TABLE"))           # DWC_LOCAL_TABLE aunque traiga query
    c2 = classify(structure("1TF_TXT_STAGE"))
    c3 = classify(remote("X", "ABAP_CDS."))                                                # stem vacio
    assert [(c.identity, c.rule, c.certainty) for c in (c1, c2, c3)] == [
        (I.UNKNOWN, R.ID_UNKNOWN_LOCAL_TABLE, None), (I.UNKNOWN, R.ID_UNKNOWN_STRUCTURE_ONLY, None),
        (I.UNKNOWN, R.ID_UNKNOWN_EMPTY_REMOTE_STEM, None)]


def test_identity_is_classification_only_the_name_quality_does_not_matter():
    """No se infiere calidad: un nombre feo o uno canonico reciben la misma identidad por la misma razon."""
    assert classify(authored("VD_X")).identity is classify(authored("totally wrong name!!")).identity is I.PROJECT_ASSIGNED


# ── procedencia de cada clasificacion ──────────────────────────────────────
def test_every_classification_carries_rule_certainty_evidence_and_ruleset():
    ents = [authored("V"), remote("R", "ABAP_CDS.R$E"), structure("S"), authored("SAP.X")]
    for e in ents:
        c = classify(e)
        assert c.ruleset_version == IDENTITY_RULESET == "identity-rules/v1"
        assert c.evidence and all(isinstance(r, m.EvidenceRef) for r in c.evidence)
        assert all(r.entity == e.name and r.plane is m.Plane.MODELING for r in c.evidence)
        assert {r.source_file for r in c.evidence} == {e.file}


def test_classification_is_pure_and_deterministic():
    e = remote("1CDS_X", "ABAP_CDS.X$P")
    before = dict(vars(e))
    assert classify(e) == classify(e)
    assert dict(vars(e)) == before                                        # no muta la entidad


def test_universe_classification_is_order_independent_and_sorted():
    ents = {x.name: x for x in [authored("B"), remote("A", "ABAP_CDS.A$E"), structure("C")]}
    rev = dict(reversed(list(ents.items())))
    assert list(classify_universe(ents)) == ["A", "B", "C"]
    assert classify_universe(ents) == classify_universe(rev)


# ── goldens sinteticos de las poblaciones canonicas ────────────────────────
def _case3_like():
    ents = []
    # 16 SOURCE_DERIVED_IMPORTED: 13 exactos (7 SAPI + 5 CDS + 1 tabla fisica) + 2 con prefijo + 1 con tokens quitados
    ents += [remote(f"0MAT_{i}_TEXT", f"SAPI.0MAT_{i}_TEXT") for i in range(7)]
    ents += [remote(f"ZCDS_DIM_E{i}", f"ABAP_CDS.ZCDS_DIM_E{i}$E") for i in range(5)]
    ents += [remote("A777", "ABAPTABLES.A777")]
    ents += [remote("1TR_ZCDS_DSP_DIM_ONE", "ABAP_CDS.ZCDS_DSP_DIM_ONE$E"),
             remote("1TR_ZCDS_DSP_DIM_TWO", "ABAP_CDS.ZCDS_DSP_DIM_TWO$E")]
    ents += [remote("ZCDS_DIM_THREE", "ABAP_CDS.ZCDS_DSP_DIM_THREE$E")]
    # 17 PROJECT_ASSIGNED: vistas/modelos autorados
    ents += [authored(f"{p}_{i}") for p, n in (("VD", 5), ("VT", 7), ("VR", 2)) for i in range(n)]
    ents += [authored("2VR_CENTRAL"), authored("3VF_CENTRAL"), authored("AM_CENTRAL")]
    # 2 UNKNOWN: tablas locales sin remoto ni query
    ents += [structure("ZCDS_DSP_FACT_AAA"), structure("2TR_ZDSP_FACT_BBB")]
    return {e.name: e for e in ents}


def _case4_like():
    ents = []
    # 16 SOURCE_DERIVED_IMPORTED: 11 exactos (3 SAPI + 8 CDS) + 4 prefijo `1CDS_` + 1 compuesto sobre tabla fisica
    ents += [remote(f"0S{i}_ATTR", f"SAPI.0S{i}_ATTR") for i in range(3)]
    ents += [remote(f"IFI{i}OBJ", f"ABAP_CDS.IFI{i}OBJ$P") for i in range(8)]
    ents += [remote(f"1CDS_IFIPFX{i}", f"ABAP_CDS.IFIPFX{i}$P") for i in range(4)]
    ents += [remote("ZDSP_DIM_FACT_WIDGET", "ABAPTABLES.WIDGET")]
    # 29 PROJECT_ASSIGNED: artefactos autorados (incluye el AM)
    ents += [authored(f"{p}_{i}") for p, n in (("VD", 9), ("VT", 4), ("VR", 9), ("VF", 3), ("VSQL", 2)) for i in range(n)]
    ents += [authored("2VR_FISCAL"), authored("AM_CONSOLIDADO")]
    # 1 remoto con nombre NO derivable del stem (el que el dossier humano conto como PROJECT_ASSIGNED heuristicamente)
    ents += [remote("ZCDS_ITEM_BAK", "ABAP_CDS.ZCDS_ITEMBAK_LINE$F")]
    # 5 SYSTEM: 4 tablas de plataforma + 1 vista de plataforma con query
    ents += [structure("SAP.TIME.M_TIME_DIMENSION"), structure("SAP.TIME.M_TIME_DIMENSION_TDAY"),
             structure("SAP.TIME.M_TIME_DIMENSION_TMONTH"), structure("SAP.TIME.M_TIME_DIMENSION_TQUARTER"),
             authored("SAP.TIME.VIEW_DIMENSION_DAY")]
    # 4 UNKNOWN: el remoto no derivable + 1 tabla local DWC (con query de activacion) + 2 estructuras sin remoto ni query
    ents += [authored("ZCDS_DSP_STAGE", repository_type="DWC_LOCAL_TABLE"), structure("1TF_TXT_STAGE"),
             structure("2TR_PERIODS_STATIC")]
    return {e.name: e for e in ents}


def _dist(pool):
    cl = list(classify_universe(pool).values())
    return identity_distribution(cl), heuristic_distribution(cl), implementation_distribution(cl)


def test_golden_case3_like_17_16_2():
    pool = _case3_like()
    ident, heur, impl = _dist(pool)
    assert len(pool) == 35
    assert ident == {"PROJECT_ASSIGNED": 17, "SOURCE_DERIVED_IMPORTED": 16, "SYSTEM": 0, "UNKNOWN": 2}
    assert sum(ident.values()) == 35
    assert heur == {"PROJECT_ASSIGNED": 0, "SOURCE_DERIVED_IMPORTED": 3, "SYSTEM": 0, "UNKNOWN": 0}   # 2 extend + 1 abbreviate
    assert impl == {"SYSTEM_PROVIDED": 0, "REMOTE_IMPORTED": 16, "LOCAL_TABLE": 0, "QUERY_DEFINED": 17, "STRUCTURE_ONLY": 2}


HISTORICAL_HUMAN_DOSSIER_CASE4 = {"PROJECT_ASSIGNED": 30, "SOURCE_DERIVED_IMPORTED": 16, "SYSTEM": 5, "UNKNOWN": 3}


def test_golden_case4_like_v2_29_16_5_4():
    pool = _case4_like()
    ident, heur, impl = _dist(pool)
    assert len(pool) == 54
    assert ident == {"PROJECT_ASSIGNED": 29, "SOURCE_DERIVED_IMPORTED": 16, "SYSTEM": 5, "UNKNOWN": 4}
    assert sum(ident.values()) == 54
    assert heur == {"PROJECT_ASSIGNED": 0, "SOURCE_DERIVED_IMPORTED": 5, "SYSTEM": 0, "UNKNOWN": 0}   # PA ya no tiene heuristicas
    assert impl == {"SYSTEM_PROVIDED": 5, "REMOTE_IMPORTED": 17, "LOCAL_TABLE": 1, "QUERY_DEFINED": 29, "STRUCTURE_ONLY": 2}


def test_v2_differs_from_the_historical_human_dossier_only_by_the_one_heuristic_decision():
    """El historico (30/16/5/3) se PRESERVA como dossier humano con una decision heuristica explicita; v2 difiere
    deliberadamente en exactamente ese artefacto (PROJECT_ASSIGNED -> UNKNOWN): mayor disciplina de procedencia."""
    pool = _case4_like()
    v2 = _dist(pool)[0]
    delta = {k: v2[k] - HISTORICAL_HUMAN_DOSSIER_CASE4[k] for k in v2}
    assert delta == {"PROJECT_ASSIGNED": -1, "SOURCE_DERIVED_IMPORTED": 0, "SYSTEM": 0, "UNKNOWN": 1}
    moved = [c.artifact for c in classify_universe(pool).values() if c.rule is R.ID_UNKNOWN_REMOTE_NAME_NOT_DERIVABLE]
    assert moved == ["ZCDS_ITEM_BAK"]


def test_dataflow_targets_are_outside_the_identity_population():
    pool = dict(_case4_like())
    pool["DF_TARGET_TABLE"] = E(name="DF_TARGET_TABLE", file="local-tables/x.json", is_dataflow=True)
    cl = classify_universe(pool)
    assert "DF_TARGET_TABLE" not in cl and len(cl) == 54
    assert identity_distribution(cl.values()) == _dist(_case4_like())[0]
    assert classify(pool["DF_TARGET_TABLE"]).identity is I.UNKNOWN          # la funcion pura sigue pudiendo clasificarlo


def test_goldens_are_stable_under_input_shuffling():
    import random
    for build in (_case3_like, _case4_like):
        base = _dist(build())
        items = list(build().items())
        rng = random.Random(5)
        for _ in range(5):
            rng.shuffle(items)
            assert _dist(dict(items)) == base


def test_distribution_reports_every_state_even_when_zero():
    assert identity_distribution([]) == {"PROJECT_ASSIGNED": 0, "SOURCE_DERIVED_IMPORTED": 0, "SYSTEM": 0, "UNKNOWN": 0}


def test_identity_vocabulary_has_no_scoring_concepts():
    for enum in (I, R, Certainty, Impl):
        for member in enum:
            assert not m.find_forbidden_tokens(member.name) and not m.find_forbidden_tokens(str(member.value))
