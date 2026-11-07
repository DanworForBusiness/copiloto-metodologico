"""Clase del contacto con la fuente de una hoja MIXED_BOUNDARY_CLASSES (varias fronteras, cada una con clase explicita) y trazabilidad de No determinable. Datos sinteticos.

Una hoja cuya procedencia llega SOLO a clases gobernadas (la MISMA definicion compartida que clasifica una hoja de clase unica, ``layer_semantics.GOVERNED_CONTRACT_CLASSES``) es un contacto
gobernado; una mezcla con tabla fisica, externa o desconocida sigue indeterminada. El informe nombra las hojas y campos responsables con las mismas funciones del motor."""
from __future__ import annotations

import pytest

from tools.catalog.methodological_judgment.profile_access import EXTERNAL_CONTRACT_UNDETERMINED, GOVERNED_SAP_CONTRACT, ProfileEvidence
from webapp.services import assessment_presenter as presenter


def _ev(boundary_classes, remote_entities=("ABAP_CDS.Z$F", "ABAPTABLES.NSDM_V_MCHB")):
    leaf = {"leaf": "2TR_X", "leaf_kind": "LOCAL_FLOW_PRODUCED", "contract_class": "MIXED_BOUNDARY_CLASSES", "classification_basis": "MULTIPLE_BOUNDARY_CLASSES",
            "classification_bases": ["REMOTE_ENTITY_NAMESPACE"], "boundary_classes": list(boundary_classes), "remote_entities": list(remote_entities), "source_systems": []}
    return ProfileEvidence({"canonical_evidence": {"source_contract_classes": {"leaves": [leaf]}}})


@pytest.mark.parametrize("classes,expected", [
    (["GOVERNED_SAP_CDS_CONTRACT", "SAP_STANDARD_VIEW_ON_PHYSICAL_TABLE"], GOVERNED_SAP_CONTRACT),        # caso real: 3 CDS + NSDM_V_MCHB
    (["GOVERNED_SAP_CDS_CONTRACT", "GOVERNED_SAP_ODP_SAPI_CONTRACT"], GOVERNED_SAP_CONTRACT),
    (["SAP_STANDARD_VIEW_ON_PHYSICAL_TABLE", "PHYSICAL_ABAP_TABLE_BOUNDARY"], EXTERNAL_CONTRACT_UNDETERMINED),   # con tabla fisica cruda: sigue indeterminada
    (["GOVERNED_SAP_CDS_CONTRACT", "EXTERNAL_UNDETERMINED"], EXTERNAL_CONTRACT_UNDETERMINED),
    ([], EXTERNAL_CONTRACT_UNDETERMINED),
])
def test_mixed_leaf_is_governed_only_when_every_boundary_class_is_governed(classes, expected):
    assert _ev(classes).classify_source("2TR_X") == expected


def test_nd_detail_names_the_responsible_leaf():
    ev_profile = {"canonical_evidence": {"source_contract_classes": {"leaves": [{
        "leaf": "2TR_X", "leaf_kind": "LOCAL_FLOW_PRODUCED", "contract_class": "MIXED_BOUNDARY_CLASSES", "classification_basis": "MULTIPLE_BOUNDARY_CLASSES",
        "classification_bases": [], "boundary_classes": ["SAP_STANDARD_VIEW_ON_PHYSICAL_TABLE", "PHYSICAL_ABAP_TABLE_BOUNDARY"],
        "remote_entities": ["ABAPTABLES.MLGN", "ABAPTABLES.NSDM_V_MCHB"], "source_systems": []}]}}}
    judgment = {"status": "NOT_DETERMINABLE", "level": None, "level_rule": "LVL-06", "not_determinable_reason": "INSUFFICIENT_AFFIRMATIVE_EVIDENCE",
                "not_determinable_detail": {"missing_strength_dimensions": [
                    {"dimension": "SOURCE_INGESTION", "state": "INSUFFICIENT_EVIDENCE", "cause": "SOURCE_CONTRACT_CLASS_UNDETERMINED"},
                    {"dimension": "PUBLISHED_CONTRACT", "state": "NO_STRENGTH", "cause": "UNRESOLVED_VOCABULARY_REACHES_THE_CONSUMER", "unmet_conditions": ["no_vocabulary_leak"]}]}}
    view = presenter._with_nd_responsibles(presenter.judgment_view(judgment), ev_profile, judgment)
    rows = {d["dimension"]: d["responsible_text"] for d in view["nd"]["dimensions"]}
    assert "2TR_X (MIXED_BOUNDARY_CLASSES: ABAPTABLES.MLGN, ABAPTABLES.NSDM_V_MCHB)" in rows["Ingesta"]
    assert "Condiciones no cumplidas" in rows["Contrato publicado"]
    assert presenter._with_nd_responsibles(presenter.judgment_view({"status": "DETERMINED", "level": "BUENA"}), ev_profile, {}) is not None   # con nivel: sin detalle
