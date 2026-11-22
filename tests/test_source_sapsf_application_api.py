"""Controles criticos de SOURCE/SAPSF (alternativa B, canonical-evidence/v6):
  * lector del Catalogo de Conexiones scopeado al cliente, con abstencion ante typeId divergente y tolerante a la ausencia;
  * clasificacion positiva / negativos / ambiguedad / preservacion CDS-ODP (via la clasificacion de la hoja);
  * invariante del validador v6 (connection_type opcional, presente <=> GOVERNED_SAP_APPLICATION_API) y su prohibicion en v3/v4/v5;
  * el Juicio reconoce GOVERNED_SAP_APPLICATION_API como contrato de origen gobernado.
Pruebas PURAS: sin pool pesado, sin tocar produccion. La prueba end-to-end del gate sobre datos reales (AM_Turnover) esta documentada en el handoff."""
import json
from pathlib import Path

import pytest

from tools.catalog.modeling_assessment import connection_catalog as CC
from tools.catalog.modeling_assessment import facts_field_lineage as FFL
from tools.catalog.modeling_assessment.evidence_contract_v3 import validate_source_contract_classes
from tools.catalog.modeling_assessment.evidence_errors import CanonicalEvidenceError
from tools.catalog.methodological_judgment.profile_access import ProfileEvidence
from tools.catalog import layer_semantics

V6 = "canonical-evidence/v6"


# ───────────────────────────── lector del Catalogo de Conexiones (ambito C) ─────────────────────────────
def _ficha(d: Path, space: str, name: str, type_id: str):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"conn_space_{space}.json").write_text(json.dumps({"name": name, "typeId": type_id, "space_name": space}), encoding="utf-8")


def test_reader_resolves_client_scoped_typeid(tmp_path):
    _ficha(tmp_path / "Contreras" / "Conexiones", "PRD_SSFF", "SSFF", "SAPSF")
    _ficha(tmp_path / "Contreras" / "Conexiones", "PRD_GDA", "GDA", "ODATA")
    m = CC.connection_types(tmp_path, "Contreras")
    assert m == {"SSFF": "SAPSF", "GDA": "ODATA"}


def test_reader_is_client_scoped_never_crosses_clients(tmp_path):
    _ficha(tmp_path / "Otro" / "Conexiones", "PRD", "SSFF", "SAPSF")          # SSFF de OTRO cliente
    assert CC.connection_types(tmp_path, "Contreras") == {}                    # no cruza clientes


def test_reader_abstains_on_divergent_typeid_for_same_name(tmp_path):
    base = tmp_path / "Contreras" / "Conexiones"
    _ficha(base, "PRD", "SSFF", "SAPSF")
    _ficha(base, "QAS", "SSFF", "ODATA")                                       # mismo nombre, typeId distinto
    assert "SSFF" not in CC.connection_types(tmp_path, "Contreras")            # abstencion


def test_reader_tolerates_absent_catalog(tmp_path):
    assert CC.connection_types(tmp_path, "SinCatalogo") == {}


# ───────────────────────────── vocabulario compartido ─────────────────────────────
def test_application_api_is_a_governed_contract_class():
    assert FFL.SCC_APP_API == "GOVERNED_SAP_APPLICATION_API"
    assert FFL.SCC_APP_API in layer_semantics.GOVERNED_CONTRACT_CLASSES          # compartido por Juicio y senales
    assert FFL._GOVERNED_APPLICATION_CONNECTION_TYPES == frozenset({"SAPSF"})    # allow-list estricta (no ODATA/MSSQL)


# ───────────────────────────── validador v6 (integridad de la evidencia) ─────────────────────────────
def _leaf(cls, basis, *, conn="SSFF", conn_type=None, remote=("FOCompany",)):
    r = {"leaf": "1TR_SSF_FOCompany", "leaf_kind": "REMOTE_IMPORTED", "contract_class": cls, "classification_basis": basis,
         "classification_bases": [basis], "boundary_classes": [], "source_systems": [], "remote_entities": list(remote), "remote_connection": conn,
         "definition": "x"}
    if conn_type is not None:
        r["connection_type"] = conn_type
    return r


def _section(leaf):
    return {"leaves": [leaf], "by_class": [{"contract_class": leaf["contract_class"], "count": 1}], "definition": "x"}


def test_v6_accepts_application_api_leaf_with_connection_type():
    sec = _section(_leaf("GOVERNED_SAP_APPLICATION_API", "CONNECTION_TYPE", conn_type="SAPSF"))
    assert validate_source_contract_classes(sec, version=V6) == {"1TR_SSF_FOCompany": "GOVERNED_SAP_APPLICATION_API"}


def test_v6_rejects_application_api_leaf_without_connection_type():
    sec = _section(_leaf("GOVERNED_SAP_APPLICATION_API", "CONNECTION_TYPE"))     # falta connection_type
    with pytest.raises(CanonicalEvidenceError):
        validate_source_contract_classes(sec, version=V6)


def test_v6_connection_type_only_on_application_api_leaf():
    sec = _section(_leaf("GOVERNED_SAP_CDS_CONTRACT", "REMOTE_ENTITY_NAMESPACE", conn_type="SAPSF"))   # clase CDS NO puede llevar connection_type
    with pytest.raises(CanonicalEvidenceError):
        validate_source_contract_classes(sec, version=V6)


def test_pre_v6_forbids_connection_type():
    sec = _section(_leaf("EXTERNAL_CONTRACT_UNDETERMINED", "NO_RECOGNIZED_NAMESPACE", conn_type="SAPSF"))
    with pytest.raises(CanonicalEvidenceError):
        validate_source_contract_classes(sec, version="canonical-evidence/v5")


def test_pre_v6_unchanged_shape_still_validates():
    sec = _section(_leaf("EXTERNAL_CONTRACT_UNDETERMINED", "NO_RECOGNIZED_NAMESPACE"))
    assert validate_source_contract_classes(sec, version="canonical-evidence/v5") == {"1TR_SSF_FOCompany": "EXTERNAL_CONTRACT_UNDETERMINED"}


# ───────────────────────────── el Juicio reconoce la clase como gobernada ─────────────────────────────
def _profile(cls):
    leaf = _leaf(cls, "CONNECTION_TYPE" if cls == "GOVERNED_SAP_APPLICATION_API" else "NO_RECOGNIZED_NAMESPACE",
                 conn_type="SAPSF" if cls == "GOVERNED_SAP_APPLICATION_API" else None)
    return {"canonical_evidence": {"version": V6, "source_contract_classes": {"leaves": [leaf]}}}


def test_judgment_classifies_application_api_as_governed_source_contract():
    ev = ProfileEvidence(_profile("GOVERNED_SAP_APPLICATION_API"))
    assert ev.classify_source("1TR_SSF_FOCompany") == "GOVERNED_SAP_CONTRACT"


def test_judgment_keeps_undetermined_as_external():
    ev = ProfileEvidence(_profile("EXTERNAL_CONTRACT_UNDETERMINED"))
    assert ev.classify_source("1TR_SSF_FOCompany") == "EXTERNAL_CONTRACT_UNDETERMINED"


# ───────────────────────────── paridad ad hoc (extracción live): enriquecer el workspace con la evidencia de conexión ─────────────────────────────
class _FakeConnSource:
    """Fuente de conexión simulada (misma interfaz que CliConnectionSource) para probar el enriquecimiento del workspace sin red ni CLI."""
    def __init__(self, **_kw):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *_a):
        return False
    def list_connections(self, space):
        return [{"name": "SSFF"}, {"name": "GDA"}, {"name": "NOTYPE"}]
    def get_connection(self, space, name):
        return {"SSFF": {"name": "SSFF", "typeId": "SAPSF", "space_name": space},   # positivo
                "GDA": {"name": "GDA", "typeId": "ODATA", "space_name": space},      # negativo: OData genérico
                "NOTYPE": {"name": "NOTYPE", "space_name": space}}[name]              # negativo: sin typeId


def test_adhoc_materializes_live_connections_with_source_parity(tmp_path, monkeypatch):
    from webapp.services import datasphere_assessment_service as das
    from tools.catalog.connectivity_governance import sources as cg_sources
    monkeypatch.setattr(cg_sources, "CliConnectionSource", _FakeConnSource)
    pool_dir = tmp_path / "pool" / "Contreras"
    pool_dir.mkdir(parents=True)
    vals = das.FormValues.from_mapping({"host": "h", "token_url": "t", "client_id": "c", "space": "PRD", "root_object": "AM_X", "client": "Contreras"})
    n = das._materialize_live_connections(pool_dir, vals, "secret")
    assert n == 2                                                                     # SSFF + GDA materializadas; NOTYPE omitida (sin typeId → el clasificador se abstiene)
    # Paridad: el MISMO reader que consume el clasificador (camino Catálogo) resuelve el typeId desde las fichas materializadas en el workspace ad hoc.
    assert CC.connection_types(tmp_path / "pool", "Contreras") == {"SSFF": "SAPSF", "GDA": "ODATA"}


def test_adhoc_materialize_is_noop_without_space(tmp_path, monkeypatch):
    from webapp.services import datasphere_assessment_service as das
    from tools.catalog.connectivity_governance import sources as cg_sources
    monkeypatch.setattr(cg_sources, "CliConnectionSource", _FakeConnSource)
    pool_dir = tmp_path / "pool" / "Contreras"
    pool_dir.mkdir(parents=True)
    vals = das.FormValues.from_mapping({"host": "h", "token_url": "t", "client_id": "c", "space": "", "root_object": "AM_X", "client": "Contreras"})
    assert das._materialize_live_connections(pool_dir, vals, "secret") == 0           # sin espacio no se puede acotar el ámbito: se abstiene
    assert not (pool_dir / "Conexiones").exists()
