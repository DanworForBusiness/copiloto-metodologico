"""Gate 8D.HAR.1 — paridad de IDENTIDAD de la clase de contrato de fuente frente a los delta-shadows.

``ProfileEvidence.classify_source(X)`` debe resolver un registro de evidencia registrado bajo el nombre FÍSICO de
captura de cambios ``X_Delta`` (misma identidad canónica ``X_Delta <-> X`` que alineó la adquisición en Gate A), SIN
fabricar clase ni colapsar un ``X_Delta`` independiente. Solo acceso/identidad: no cambia HAR-STR-01 ni la adquisición.
"""
from __future__ import annotations

import pytest

from tools.catalog.methodological_judgment.profile_access import (
    EXTERNAL_CONTRACT_UNDETERMINED,
    GOVERNED_SAP_CONTRACT,
    PHYSICAL_RAW_BOUNDARY,
    ProfileEvidence,
    _DELTA_SHADOW_SUFFIX,
)
from tools.catalog.methodological_judgment import rules


def _ev(leaves):
    return ProfileEvidence({"canonical_evidence": {"source_contract_classes": {"leaves": list(leaves)}}})


def _leaf(name, cls):
    return {"leaf": name, "contract_class": cls}


# A ─ exact source classifies unchanged
def test_a_exact_source_is_classified_unchanged():
    ev = _ev([_leaf("1TR_SAP_MD_HOUSEBANK", "GOVERNED_SAP_CDS_CONTRACT")])
    assert ev.classify_source("1TR_SAP_MD_HOUSEBANK") == GOVERNED_SAP_CONTRACT


# B ─ delta-shadow identity resolves to the existing class
def test_b_delta_shadow_identity_resolves_governed():
    ev = _ev([_leaf("1TR_SAP_MD_COSTCENTER_Delta", "GOVERNED_SAP_CDS_CONTRACT")])
    assert ev.classify_source("1TR_SAP_MD_COSTCENTER") == GOVERNED_SAP_CONTRACT


# C ─ independent X_Delta entity is not collapsed; exact precedence wins
def test_c_independent_delta_entity_is_not_collapsed():
    ev = _ev([_leaf("X", "PHYSICAL_ABAP_TABLE_BOUNDARY"),
              _leaf("X_Delta", "GOVERNED_SAP_CDS_CONTRACT")])
    # X tiene su propio registro: se usa ese, NO el de X_Delta
    assert ev.classify_source("X") == PHYSICAL_RAW_BOUNDARY
    # X_Delta se clasifica por sí mismo; nunca se redirige a X
    assert ev.classify_source("X_Delta") == GOVERNED_SAP_CONTRACT


# D ─ unknown X_Delta evidence does NOT manufacture GOVERNED
def test_d_unknown_delta_shadow_does_not_manufacture_governed():
    ev = _ev([_leaf("Y_Delta", "UNKNOWN")])
    assert ev.classify_source("Y") == EXTERNAL_CONTRACT_UNDETERMINED
    assert ev.classify_source("Y") != GOVERNED_SAP_CONTRACT


# E ─ no evidence record stays None
def test_e_no_record_stays_none():
    ev = _ev([_leaf("A", "GOVERNED_SAP_CDS_CONTRACT")])
    assert ev.classify_source("ZZZ") is None
    assert ev.classify_source("ZZZ_Delta") is None


# F ─ HOUSEBANK exact behaviour unchanged (never needs normalization)
def test_f_housebank_exact_unchanged():
    ev = _ev([_leaf("1TR_SAP_MD_HOUSEBANK", "GOVERNED_SAP_CDS_CONTRACT"),
              _leaf("1TR_SAP_MD_HOUSEBANKACCOUNT", "GOVERNED_SAP_CDS_CONTRACT")])
    assert ev.classify_source("1TR_SAP_MD_HOUSEBANK") == GOVERNED_SAP_CONTRACT
    assert ev.classify_source("1TR_SAP_MD_HOUSEBANKACCOUNT") == GOVERNED_SAP_CONTRACT


# H ─ physical/raw and external semantics unchanged under the shadow identity
def test_h_physical_delta_shadow_stays_physical_not_governed():
    ev = _ev([_leaf("1TR_SAP_MD_RAW_Delta", "PHYSICAL_ABAP_TABLE_BOUNDARY")])
    assert ev.classify_source("1TR_SAP_MD_RAW") == PHYSICAL_RAW_BOUNDARY


def test_h_a_name_already_ending_in_the_suffix_is_not_double_normalized():
    ev = _ev([_leaf("W_Delta_Delta", "GOVERNED_SAP_CDS_CONTRACT")])
    # buscar "W_Delta": exact miss y NO se intenta "W_Delta_Delta" (el nombre consultado ya es sombra)
    assert ev.classify_source("W_Delta") is None


# ── HAR-STR-01 identity-robustness (G) — la fortaleza depende solo de la clase resuelta, no de la forma del nombre ──
def _har_profile(source_leaf_name, source_cls):
    """Perfil mínimo con un candidato de armonización 1:1 legítimo cuya fuente lógica es ``X`` pero cuyo registro de
    clase está bajo ``source_leaf_name`` (``X`` o ``X_Delta``)."""
    return ProfileEvidence({"canonical_evidence": {
        "source_contract_classes": {"leaves": [_leaf(source_leaf_name, source_cls)]},
        "harmonization_contract": {"artifacts": [{
            "artifact": "2VD_MD_X",
            "character": {"value": "TECHNICAL_MATERIALIZATION"},
            "sources": ["1TR_SAP_MD_X"],
            "source_vocabulary": {"fields_with_source_column_name": 0},
            "label_origin_counts": {"DIFFERENT_FROM_PROVIDER": 0, "UNKNOWN_PROVIDER_LABEL": 0, "NO_LABEL": 0,
                                    "EQUALS_PROVIDER_LABEL": 5},
        }]},
    }})


def _har_ids(ev):
    return {s["rule_id"] for s in rules.strengths(ev, "UNDETERMINED", has_pro_mat=False)}


def test_g_har_str_01_emits_when_governed_source_is_logical_identity():
    assert "HAR-STR-01" in _har_ids(_har_profile("1TR_SAP_MD_X", "GOVERNED_SAP_CDS_CONTRACT"))


def test_g_har_str_01_emits_when_governed_source_is_delta_shadow_identity():
    # misma fuente lógica, registrada bajo el nombre físico delta-shadow: la fortaleza DEBE emitir igual
    assert "HAR-STR-01" in _har_ids(_har_profile("1TR_SAP_MD_X_Delta", "GOVERNED_SAP_CDS_CONTRACT"))


def test_g_har_str_01_does_not_emit_when_delta_shadow_evidence_is_unknown():
    # identidad resuelta pero clase UNKNOWN: NO se fabrica gobernanza -> sin fortaleza
    assert "HAR-STR-01" not in _har_ids(_har_profile("1TR_SAP_MD_X_Delta", "UNKNOWN"))


# ── contrato de espejo: el sufijo coincide con la constante canónica del paquete de evidencia ──
def test_delta_shadow_suffix_mirrors_the_canonical_constant():
    from tools.catalog.modeling_assessment.readers import DELTA_SHADOW_SUFFIX
    assert _DELTA_SHADOW_SUFFIX == DELTA_SHADOW_SUFFIX
