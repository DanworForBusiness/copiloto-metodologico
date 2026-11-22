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


# ───────────────────────────── paridad ad hoc (extracción live): autenticar + enriquecer el workspace con la evidencia de conexión ─────────────────────────────
from tools.catalog.connectivity_governance.sources import PreflightReport, PreflightCheck, CheckStatus   # noqa: E402
from tools.catalog.connectivity_governance.extraction import SourceError                                 # noqa: E402


class _FakeConnSource:
    """Fuente de conexión simulada (interfaz de CliConnectionSource) para probar el flujo preflight→consulta sin red ni CLI. Registra el orden de llamadas y el cierre."""
    def __init__(self, *, authorized=True, **_kw):
        self._authorized = authorized
        self.calls = []
        self.closed = False
    def __enter__(self):
        return self
    def __exit__(self, *_a):
        self.closed = True       # el `with` cierra el recurso en éxito y en error
        return False
    def preflight_report(self):
        self.calls.append("preflight")
        if self._authorized:
            checks = (PreflightCheck("oauth_token", CheckStatus.OK), PreflightCheck("list_connections", CheckStatus.OK))
        else:                    # login falla: oauth_token FAILED (detalle ya saneado por la fuente real)
            checks = (PreflightCheck("oauth_token", CheckStatus.FAILED, "OAuth no valido"), PreflightCheck("list_connections", CheckStatus.NOT_VERIFIED, "no ejecutada"))
        return PreflightReport(checks)
    def list_connections(self, space):
        self.calls.append("list")
        return [{"name": "SSFF"}, {"name": "GDA"}, {"name": "NOTYPE"}]
    def get_connection(self, space, name):
        self.calls.append("get:" + name)
        return {"SSFF": {"name": "SSFF", "typeId": "SAPSF", "space_name": space},   # positivo
                "GDA": {"name": "GDA", "typeId": "ODATA", "space_name": space},      # negativo: OData genérico
                "NOTYPE": {"name": "NOTYPE", "space_name": space}}[name]              # negativo: sin typeId


def _patch_source(monkeypatch, *, authorized=True):
    from tools.catalog.connectivity_governance import sources as cg_sources
    created = []
    def _factory(**kw):
        inst = _FakeConnSource(authorized=authorized, **kw)
        created.append(inst)
        return inst
    monkeypatch.setattr(cg_sources, "CliConnectionSource", _factory)
    return created


def _vals(space="PRD"):
    from webapp.services import datasphere_assessment_service as das
    return das.FormValues.from_mapping({"host": "h", "token_url": "t", "client_id": "c", "space": space, "root_object": "AM_X", "client": "Contreras"})


def test_adhoc_preflights_before_querying_and_materializes_sapsf(tmp_path, monkeypatch):
    from webapp.services import datasphere_assessment_service as das
    created = _patch_source(monkeypatch, authorized=True)
    pool_dir = tmp_path / "pool" / "Contreras"; pool_dir.mkdir(parents=True)
    n = das._materialize_live_connections(pool_dir, _vals(), "secret")
    assert n == 2                                                                     # SSFF + GDA; NOTYPE omitida (sin typeId)
    src = created[0]
    assert src.calls[0] == "preflight" and src.calls.index("preflight") < src.calls.index("list")   # login/preflight ANTES de consultar
    assert src.closed is True                                                         # recurso CLI cerrado en éxito
    # Paridad: el MISMO reader que consume el clasificador resuelve el typeId desde las fichas materializadas.
    assert CC.connection_types(tmp_path / "pool", "Contreras") == {"SSFF": "SAPSF", "GDA": "ODATA"}


def test_adhoc_failed_preflight_blocks_queries_and_does_not_accredit(tmp_path, monkeypatch):
    from webapp.services import datasphere_assessment_service as das
    created = _patch_source(monkeypatch, authorized=False)
    pool_dir = tmp_path / "pool" / "Contreras"; pool_dir.mkdir(parents=True)
    with pytest.raises(SourceError) as ei:
        das._materialize_live_connections(pool_dir, _vals(), "secret")
    assert ei.value.code == "PREFLIGHT_INSUFFICIENT"                                  # la excepción conserva un código diagnóstico seguro
    src = created[0]
    assert "list" not in src.calls and not any(c.startswith("get:") for c in src.calls)   # preflight fallido IMPIDE las consultas
    assert src.closed is True                                                         # recurso CLI cerrado también en error
    assert not (pool_dir / "Conexiones").exists()                                     # SOURCE no se acredita (sin fichas)


def test_adhoc_materialize_is_noop_without_space(tmp_path, monkeypatch):
    from webapp.services import datasphere_assessment_service as das
    _patch_source(monkeypatch, authorized=True)
    pool_dir = tmp_path / "pool" / "Contreras"; pool_dir.mkdir(parents=True)
    assert das._materialize_live_connections(pool_dir, _vals(space=""), "secret") == 0   # sin espacio no se puede acotar el ámbito: se abstiene
    assert not (pool_dir / "Conexiones").exists()
