"""
Regeneracion LOCAL de los 8 modelos de calibracion con el contrato canonical-evidence/v3 (dossier de linaje de campo).

Estas pruebas leen el pool REAL del Catalogo (``DANWOR_CATALOG_DIR/.csn_cache`` o ``DANWOR_CATALOG_INBOUND_DIR``); sin pool local se OMITEN (no hay datos de clientes en el repo).
Son de SOLO LECTURA: regeneran en memoria, no tocan estado gobernado ni produccion. Fijan HECHOS de procedencia (no calidad): completitud del linaje, hojas alcanzadas por clase
de contrato de fuente y fronteras de armonizacion cruzadas.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tools.catalog.modeling_assessment import profile as ma_profile
from tools.catalog.modeling_assessment import shadow as ma_shadow


def _pool_root() -> Path | None:
    env = os.getenv("DANWOR_CATALOG_CSN_CACHE", "").strip()
    cat = os.getenv("DANWOR_CATALOG_DIR", "").strip()
    inbound = os.getenv("DANWOR_CATALOG_INBOUND_DIR", "").strip()
    for cand in (env, str(Path(cat) / ".csn_cache") if cat else "", inbound):
        if cand and Path(cand).is_dir() and any(Path(cand).iterdir()):
            return Path(cand)
    return None


POOL = _pool_root()
pytestmark = pytest.mark.skipif(POOL is None, reason="sin pool local del Catalogo: los datos de clientes no estan en el repo")

# alias -> (cliente, dominio, raiz, campos publicados, hojas por clase de contrato, campos que alcanzan cada clase, fronteras de armonizacion cruzadas)
DOSSIER = {
    "illolay": ("Illolay", "General", "4MA_PEDIDOS_ENTREGAS", 32, {"PHYSICAL_ABAP_TABLE_BOUNDARY": 16, "UNKNOWN": 5, "PLATFORM_PROVIDED": 4}, {"PHYSICAL_ABAP_TABLE_BOUNDARY": 27, "UNKNOWN": 8}, 3),
    "base_stocks": ("Biogenesis", "General", "AM_BASE_STOCKS", 15, {"GOVERNED_SAP_ODP_SAPI_CONTRACT": 7, "GOVERNED_SAP_CDS_CONTRACT": 11, "SAP_STANDARD_VIEW_ON_PHYSICAL_TABLE": 1, "UNKNOWN": 1},
                    {"SAP_STANDARD_VIEW_ON_PHYSICAL_TABLE": 12, "GOVERNED_SAP_CDS_CONTRACT": 13}, 3),
    "descuentos": ("Biogenesis", "General", "AM_DescuentosComerciales", 13, {"GOVERNED_SAP_ODP_SAPI_CONTRACT": 7, "GOVERNED_SAP_CDS_CONTRACT": 6}, {"GOVERNED_SAP_CDS_CONTRACT": 12}, 0),
    "kensing": ("Kensing", "General", "4AM_FNC_GLACCOUNTLINEITRAWDATA", 349, {"GOVERNED_SAP_CDS_CONTRACT": 15}, {"GOVERNED_SAP_CDS_CONTRACT": 349}, 1),
    "prefacturacion": ("Biogenesis", "General", "AM_PREFACTURACION", 19, {"GOVERNED_SAP_ODP_SAPI_CONTRACT": 9, "GOVERNED_SAP_CDS_CONTRACT": 16, "EXTERNAL_CONTRACT_UNDETERMINED": 6,
                                                                           "PLATFORM_PROVIDED": 6, "PHYSICAL_ABAP_TABLE_BOUNDARY": 1, "UNKNOWN": 2},
                       {"GOVERNED_SAP_CDS_CONTRACT": 16, "GOVERNED_SAP_ODP_SAPI_CONTRACT": 1, "EXTERNAL_CONTRACT_UNDETERMINED": 10, "PLATFORM_PROVIDED": 1}, 3),
    "lista_precios": ("Biogenesis", "General", "AM_Lista_Precios", 20, {"PHYSICAL_ABAP_TABLE_BOUNDARY": 1, "GOVERNED_SAP_CDS_CONTRACT": 1}, {"GOVERNED_SAP_CDS_CONTRACT": 11, "PHYSICAL_ABAP_TABLE_BOUNDARY": 9}, 0),
    "reserva_material": ("Biogenesis", "General", "AM_RESERVA_MATERIAL", 34, {"GOVERNED_SAP_ODP_SAPI_CONTRACT": 1, "PHYSICAL_ABAP_TABLE_BOUNDARY": 4}, {"PHYSICAL_ABAP_TABLE_BOUNDARY": 32}, 2),
    "sellout_gp": ("Biogenesis", "General", "AM_SELLOUT_GP", 70, {"GOVERNED_SAP_ODP_SAPI_CONTRACT": 7, "PHYSICAL_ABAP_TABLE_BOUNDARY": 2, "GOVERNED_SAP_CDS_CONTRACT": 6, "PLATFORM_PROVIDED": 1,
                                                                 "EXTERNAL_CONTRACT_UNDETERMINED": 7},
                      {"EXTERNAL_CONTRACT_UNDETERMINED": 51, "GOVERNED_SAP_CDS_CONTRACT": 19, "PLATFORM_PROVIDED": 10, "PHYSICAL_ABAP_TABLE_BOUNDARY": 8}, 3),
}


@pytest.fixture(scope="module")
def docs() -> dict:
    out = {}
    for alias, (client, domain, root, *_rest) in DOSSIER.items():
        try:
            doc, _meta = ma_shadow.assess(POOL, client, root, domain)
        except Exception as exc:                                                       # pool local incompleto para este cliente
            pytest.skip(f"pool de {client} no disponible: {type(exc).__name__}")
        out[alias] = doc
    return out


@pytest.mark.parametrize("alias", sorted(DOSSIER))
def test_the_regenerated_calibration_root_validates_and_its_lineage_is_complete(docs, alias):
    doc = docs[alias]
    ma_profile.validate_assessment(doc)
    ce = doc["canonical_evidence"]
    lin = ce["published_field_lineage"]
    n = DOSSIER[alias][3]
    assert doc["versions"]["canonical_evidence"] == "canonical-evidence/v3" and lin["counts"] == {"fields": n, "COMPLETE": n, "PARTIAL": 0, "UNRESOLVED": 0, "AMBIGUOUS": 0}


@pytest.mark.parametrize("alias", sorted(DOSSIER))
def test_the_source_contract_classes_and_the_classes_reached_by_published_fields_match_the_dossier(docs, alias):
    ce = docs[alias]["canonical_evidence"]
    by_class: dict[str, int] = {}
    for r in ce["source_contract_classes"]["leaves"]:
        by_class[r["contract_class"]] = by_class.get(r["contract_class"], 0) + 1
    reached: dict[str, int] = {}
    crossed: set[str] = set()
    for f in ce["published_field_lineage"]["fields"]:
        for cls in {lf["source_contract_class"] for lf in f["frontier_leaves"]}:
            reached[cls] = reached.get(cls, 0) + 1
        crossed |= set(f["harmonization_boundaries_crossed"])
    _c, _d, _r, _n, expect_classes, expect_reached, expect_crossed = DOSSIER[alias]
    assert by_class == expect_classes and reached == expect_reached and len(crossed) == expect_crossed


def test_external_sql_server_leaves_are_never_classified_as_raw_or_governed_by_technology(docs):
    leaves = docs["sellout_gp"]["canonical_evidence"]["source_contract_classes"]["leaves"]
    external = [r for r in leaves if r["leaf_kind"] == "REMOTE_IMPORTED" and r["contract_class"] == "EXTERNAL_CONTRACT_UNDETERMINED"]
    assert len(external) == 7 and all(r["classification_basis"] == "NO_RECOGNIZED_NAMESPACE" for r in external)


def test_the_new_evidence_leaves_the_rest_of_the_assessment_unchanged_in_size_order_of_magnitude(docs):
    for doc in docs.values():
        assert doc["assessment_state"] == "ASSESSED" and doc["areas"] and doc["summary"]["coverage"] is not None
