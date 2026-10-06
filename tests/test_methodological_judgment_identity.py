"""Gate 8D.HAR.1 (+ safety review) — paridad de IDENTIDAD de la clase de contrato de fuente frente a delta-shadows.

``ProfileEvidence.classify_source(X)`` debe resolver un registro de evidencia registrado bajo el nombre FÍSICO de
captura de cambios ``X_Delta`` SOLO cuando la EVIDENCIA FACTUAL de procedencia de ``X`` prueba un productor
``DELTA_SHADOW_SUFFIX`` (nunca por el string "_Delta"), sin fabricar clase y sin colapsar un ``X_Delta`` modelado de
forma independiente. Solo acceso/identidad: no cambia HAR-STR-01 ni la adquisición.
"""
from __future__ import annotations

import pytest

from tools.catalog.methodological_judgment.profile_access import (
    EXTERNAL_CONTRACT_UNDETERMINED,
    GOVERNED_SAP_CONTRACT,
    PHYSICAL_RAW_BOUNDARY,
    ProfileEvidence,
    _DELTA_SHADOW_SUFFIX,
    _TARGET_MATCH_DELTA_SHADOW,
)
from tools.catalog.methodological_judgment import rules


def _leaf(name, cls):
    return {"leaf": name, "contract_class": cls}


def _prov(leaf, target_match):
    """Registro mínimo de leaf_provenance para la hoja lógica ``leaf`` con un productor de ``target_match`` dado."""
    return {"leaf": leaf, "producer_flows": [{"kind": "REPLICATION_FLOW", "target_match": target_match}]}


def _ev(leaves, provenance=()):
    ce = {"source_contract_classes": {"leaves": list(leaves)}}
    if provenance:
        ce["leaf_provenance"] = list(provenance)
    return ProfileEvidence({"canonical_evidence": ce})


# A ─ exact source classifies unchanged
def test_a_exact_source_is_classified_unchanged():
    ev = _ev([_leaf("1TR_SAP_MD_HOUSEBANK", "GOVERNED_SAP_CDS_CONTRACT")])
    assert ev.classify_source("1TR_SAP_MD_HOUSEBANK") == GOVERNED_SAP_CONTRACT


# B ─ delta-shadow identity resolves ONLY with the factual provenance proof
def test_b_delta_shadow_identity_resolves_with_factual_proof():
    ev = _ev([_leaf("1TR_SAP_MD_COSTCENTER_Delta", "GOVERNED_SAP_CDS_CONTRACT")],
             provenance=[_prov("1TR_SAP_MD_COSTCENTER", _TARGET_MATCH_DELTA_SHADOW)])
    assert ev.classify_source("1TR_SAP_MD_COSTCENTER") == GOVERNED_SAP_CONTRACT


# ★ SAFETY COUNTERFACTUAL ★ — independent X_Delta entity (no delta-shadow provenance for X) is NOT collapsed
def test_independent_delta_entity_without_shadow_provenance_is_not_collapsed():
    # X no tiene registro; X_Delta SÍ, pero NO hay procedencia delta-shadow para X -> X_Delta es entidad independiente
    ev = _ev([_leaf("X_Delta", "GOVERNED_SAP_CDS_CONTRACT")])
    assert ev.classify_source("X") is None
    # ni siquiera si X tiene procedencia pero su productor es EXACT (no sombra)
    ev2 = _ev([_leaf("X_Delta", "GOVERNED_SAP_CDS_CONTRACT")], provenance=[_prov("X", "EXACT")])
    assert ev2.classify_source("X") is None


# C ─ when X has its own record, exact precedence wins (never collapses to X_Delta)
def test_c_exact_record_takes_precedence_over_shadow():
    ev = _ev([_leaf("X", "PHYSICAL_ABAP_TABLE_BOUNDARY"),
              _leaf("X_Delta", "GOVERNED_SAP_CDS_CONTRACT")],
             provenance=[_prov("X", _TARGET_MATCH_DELTA_SHADOW)])
    assert ev.classify_source("X") == PHYSICAL_RAW_BOUNDARY          # usa su propio registro
    assert ev.classify_source("X_Delta") == GOVERNED_SAP_CONTRACT    # X_Delta por sí mismo


# D ─ unknown X_Delta evidence does NOT manufacture GOVERNED (even with factual shadow proof)
def test_d_unknown_delta_shadow_does_not_manufacture_governed():
    ev = _ev([_leaf("Y_Delta", "UNKNOWN")], provenance=[_prov("Y", _TARGET_MATCH_DELTA_SHADOW)])
    assert ev.classify_source("Y") == EXTERNAL_CONTRACT_UNDETERMINED
    assert ev.classify_source("Y") != GOVERNED_SAP_CONTRACT


# E ─ no evidence record stays None
def test_e_no_record_stays_none():
    ev = _ev([_leaf("A", "GOVERNED_SAP_CDS_CONTRACT")], provenance=[_prov("ZZZ", _TARGET_MATCH_DELTA_SHADOW)])
    assert ev.classify_source("ZZZ") is None                        # procedencia sombra pero no existe ZZZ_Delta
    assert ev.classify_source("QQQ") is None


# F ─ HOUSEBANK exact behaviour unchanged (never needs normalization)
def test_f_housebank_exact_unchanged():
    ev = _ev([_leaf("1TR_SAP_MD_HOUSEBANK", "GOVERNED_SAP_CDS_CONTRACT"),
              _leaf("1TR_SAP_MD_HOUSEBANKACCOUNT", "GOVERNED_SAP_CDS_CONTRACT")])
    assert ev.classify_source("1TR_SAP_MD_HOUSEBANK") == GOVERNED_SAP_CONTRACT
    assert ev.classify_source("1TR_SAP_MD_HOUSEBANKACCOUNT") == GOVERNED_SAP_CONTRACT


# H ─ physical/raw delta-shadow stays physical; a name already ending in the suffix is not double-normalized
def test_h_physical_delta_shadow_stays_physical_not_governed():
    ev = _ev([_leaf("1TR_SAP_MD_RAW_Delta", "PHYSICAL_ABAP_TABLE_BOUNDARY")],
             provenance=[_prov("1TR_SAP_MD_RAW", _TARGET_MATCH_DELTA_SHADOW)])
    assert ev.classify_source("1TR_SAP_MD_RAW") == PHYSICAL_RAW_BOUNDARY


def test_h_a_name_already_ending_in_the_suffix_is_not_double_normalized():
    ev = _ev([_leaf("W_Delta_Delta", "GOVERNED_SAP_CDS_CONTRACT")],
             provenance=[_prov("W_Delta", _TARGET_MATCH_DELTA_SHADOW)])
    assert ev.classify_source("W_Delta") is None


# ── HAR-STR-01 identity-robustness (G) ──
def _har_profile(scc_leaf_name, source_cls, *, with_shadow_provenance=False):
    """Perfil mínimo con un candidato de armonización 1:1 legítimo cuya fuente lógica es ``1TR_SAP_MD_X`` pero cuyo
    registro de clase está bajo ``scc_leaf_name`` (``X`` o ``X_Delta``)."""
    ce = {
        "source_contract_classes": {"leaves": [_leaf(scc_leaf_name, source_cls)]},
        "harmonization_contract": {"artifacts": [{
            "artifact": "2VD_MD_X",
            "character": {"value": "TECHNICAL_MATERIALIZATION"},
            "sources": ["1TR_SAP_MD_X"],
            "source_vocabulary": {"fields_with_source_column_name": 0},
            "label_origin_counts": {"DIFFERENT_FROM_PROVIDER": 0, "UNKNOWN_PROVIDER_LABEL": 0, "NO_LABEL": 0,
                                    "EQUALS_PROVIDER_LABEL": 5},
        }]},
    }
    if with_shadow_provenance:
        ce["leaf_provenance"] = [_prov("1TR_SAP_MD_X", _TARGET_MATCH_DELTA_SHADOW)]
    return ProfileEvidence({"canonical_evidence": ce})


def _har_ids(ev):
    return {s["rule_id"] for s in rules.strengths(ev, "UNDETERMINED", has_pro_mat=False)}


def test_g_har_str_01_emits_when_governed_source_is_logical_identity():
    assert "HAR-STR-01" in _har_ids(_har_profile("1TR_SAP_MD_X", "GOVERNED_SAP_CDS_CONTRACT"))


def test_g_har_str_01_emits_when_governed_source_is_factual_delta_shadow_identity():
    assert "HAR-STR-01" in _har_ids(
        _har_profile("1TR_SAP_MD_X_Delta", "GOVERNED_SAP_CDS_CONTRACT", with_shadow_provenance=True))


def test_g_har_str_01_does_not_emit_for_independent_delta_shadow_without_proof():
    # X_Delta gobernado pero SIN prueba factual de sombra: no se resuelve -> no hay fortaleza (no se inventa)
    assert "HAR-STR-01" not in _har_ids(
        _har_profile("1TR_SAP_MD_X_Delta", "GOVERNED_SAP_CDS_CONTRACT", with_shadow_provenance=False))


def test_g_har_str_01_does_not_emit_when_delta_shadow_evidence_is_unknown():
    assert "HAR-STR-01" not in _har_ids(
        _har_profile("1TR_SAP_MD_X_Delta", "UNKNOWN", with_shadow_provenance=True))


# ── contrato de espejo: constante y valor de target_match coinciden con los canónicos del paquete de evidencia ──
def test_delta_shadow_suffix_mirrors_the_canonical_constant():
    from tools.catalog.modeling_assessment.readers import DELTA_SHADOW_SUFFIX
    assert _DELTA_SHADOW_SUFFIX == DELTA_SHADOW_SUFFIX


def test_target_match_value_mirrors_the_canonical_contract():
    from tools.catalog.modeling_assessment.evidence_contract import TARGET_MATCHES
    assert _TARGET_MATCH_DELTA_SHADOW in TARGET_MATCHES
