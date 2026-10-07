"""Gate 8D.HAR.2 — identidad canónica de hoja: el Juicio clasifica la fuente por la identidad EXACTA de la hoja
frontera, sin conocimiento de la implementación física delta-shadow de Datasphere.

Invariante canónico: ``source_contract_classes`` y ``leaf_provenance`` comparten SIEMPRE la misma identidad de hoja
frontera (ambos se construyen iterando las mismas hojas del cierre sin proveedores de linaje). Por eso el Juicio NO
necesita (ni debe tener) un fallback ``X -> X_Delta``: la preservación de la identidad lógica es responsabilidad de la
GENERACIÓN de evidencia canónica, no del consumidor. Una fuente de armonización gobernada clasifica por su clave
canónica EXACTA.
"""
from __future__ import annotations

import pytest

from tools.catalog.methodological_judgment.profile_access import (
    EXTERNAL_CONTRACT_UNDETERMINED,
    GOVERNED_SAP_CONTRACT,
    PHYSICAL_RAW_BOUNDARY,
    ProfileEvidence,
)
from tools.catalog.methodological_judgment import rules
from tests._judgment_cases import fresh


def _leaf(name, cls):
    return {"leaf": name, "contract_class": cls}


def _ev(leaves):
    return ProfileEvidence({"canonical_evidence": {"source_contract_classes": {"leaves": list(leaves)}}})


# ── el Juicio clasifica por identidad EXACTA de la hoja frontera ──
def test_exact_canonical_identity_classifies_governed():
    ev = _ev([_leaf("1TR_SAP_MD_HOUSEBANK", "GOVERNED_SAP_CDS_CONTRACT")])
    assert ev.classify_source("1TR_SAP_MD_HOUSEBANK") == GOVERNED_SAP_CONTRACT


def test_absent_leaf_is_unresolved():
    ev = _ev([_leaf("A", "GOVERNED_SAP_CDS_CONTRACT")])
    assert ev.classify_source("MISSING") is None


def test_no_suffix_fallback_logical_name_is_not_resolved_to_a_shadow_record():
    # el consumidor NO infiere X a partir de un registro X_Delta: eso es trabajo de la generación de evidencia
    ev = _ev([_leaf("X_Delta", "GOVERNED_SAP_CDS_CONTRACT")])
    assert ev.classify_source("X") is None


def test_independent_delta_entity_is_addressable_by_its_own_exact_key():
    # X y un X_Delta INDEPENDIENTE coexisten: cada uno se clasifica por sí mismo, ninguno colapsa al otro
    ev = _ev([_leaf("X", "PHYSICAL_ABAP_TABLE_BOUNDARY"), _leaf("X_Delta", "GOVERNED_SAP_CDS_CONTRACT")])
    assert ev.classify_source("X") == PHYSICAL_RAW_BOUNDARY
    assert ev.classify_source("X_Delta") == GOVERNED_SAP_CONTRACT


def test_unknown_contract_stays_undetermined():
    ev = _ev([_leaf("Y", "UNKNOWN")])
    assert ev.classify_source("Y") == EXTERNAL_CONTRACT_UNDETERMINED


def test_platform_leaf_is_not_a_source_contact():
    ev = _ev([_leaf("P", "PLATFORM_PROVIDED")])
    assert ev.classify_source("P") is None


# ── invariante canónico sobre un perfil REAL (golden): ambas secciones comparten identidad de hoja ──
def test_source_contract_classes_and_leaf_provenance_share_the_same_leaf_identity():
    ev = ProfileEvidence(fresh("kensing_gl_line_item_raw"))
    scc = set(ev.leaf_class_records)
    lp = set(ev.local_leaves)
    assert scc == lp and scc, (sorted(scc ^ lp))


def test_kensing_golden_classifies_every_harmonization_source_via_exact_identity():
    ev = ProfileEvidence(fresh("kensing_gl_line_item_raw"))
    sources = [s for c in ev.harmonization.values() for s in (c.get("sources") or [])]
    assert sources and all(ev.classify_source(s) == GOVERNED_SAP_CONTRACT for s in sources)
    assert all(s in ev.leaf_class_records for s in sources)          # resuelto por clave EXACTA, sin sufijo


# ── HAR-STR-01 emite por identidad canónica EXACTA (sin suposición de sufijo) ──
def _har_profile(source_cls):
    return ProfileEvidence({"canonical_evidence": {
        "source_contract_classes": {"leaves": [_leaf("1TR_SAP_MD_X", source_cls)]},
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


def test_har_str_01_emits_for_governed_exact_identity():
    assert "HAR-STR-01" in _har_ids(_har_profile("GOVERNED_SAP_CDS_CONTRACT"))


def test_har_str_01_does_not_emit_when_source_is_unresolved():
    # fuente no clasificable por identidad exacta -> sin fortaleza (el Juicio no inventa gobernanza)
    assert "HAR-STR-01" not in _har_ids(_har_profile("UNKNOWN"))
