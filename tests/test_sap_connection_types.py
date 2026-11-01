"""Contrato `sap-connection-types/v1`: preservación, filtrado de datos del tenant, tres estados, disponibilidad condicional,
allowlist estricta, estabilidad e idempotencia. El export crudo NO se versiona: los tests usan un fixture sintético con la misma
forma y, además, verifican el JSON saneado del content pack."""
from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from tools.catalog import sap_connection_types as sct

PACK_JSON = Path(__file__).resolve().parents[1] / "content_packs" / "datasphere_core" / "sap_connection_types.json"
M_AGENT, M_AGENT2, M_IP, M_FLAG = "MRK_AGENT_DEVQA", "MRK_AGENT_OTHER", "10.11.12.13", "MRK_INTERNAL_FLAG_NAME"
M_URL = "https://mrk-partner.invalid/setup"

RAW = {"connectionTypes": [
    {"typeId": "ABAP", "categories": ["OnPremise", "CloudApp"], "sources": ["SAP"], "iconLocation": "images/storages/SAP-Generic.png",
     "dpAgentRequired": True, "advancedMode": True, "capabilities": ["HanaSdi", "DataflowSource", "ReplicationflowSource"],
     "adapter": {"id": "ABAPAdapter"},
     "dpAgents": [{"agentName": M_AGENT, "location": "agent", "connected": True}, {"agentName": M_AGENT2, "location": "agent", "connected": False}]},
    {"typeId": "PARTNERX", "categories": ["CloudApp"], "sources": ["Partner"], "partnerAPIVersion": 1, "iconLocation": "images/storages/P.png",
     "partner": "PartnerX", "iFrameUrlConnectionCreation": M_URL, "iFramePostMessageOrigin": "\\.mrk-partner\\.invalid$",
     "capabilities": ["PartnerSchema"]},
    {"typeId": "AZURESQL", "categories": ["CloudApp"], "sources": ["NonSAP"], "iconLocation": "images/storages/Azure.png",
     "dpAgentRequired": True, "ipWhitelistingRequired": True, "capabilities": ["HanaSdi"], "adapter": {"id": "MssqlLogReaderAdapter"},
     "ipAddresses": [f"{M_IP},10.11.12.14"]},
    {"typeId": "MYSQL", "ccmTypeId": "MYSQL", "ccmTypeVersion": "3.0.0", "generic": True, "featureFlag": M_FLAG,
     "categories": ["CloudApp"], "sources": ["NonSAP"], "iconLocation": "images/storages/MySQL.png", "disReplicationRequired": True,
     "capabilities": ["ReplicationflowSource"]},
    {"typeId": "OPEN_CONNECTORS", "categories": ["CloudApp"], "sources": ["SAP"], "iconLocation": "images/storages/OpenConnectors.png",
     "capabilities": ["DataflowSource"], "ocnConfigured": False, "ocnInstances": [{"name": "MRK_INSTANCE"}]},
    {"typeId": "SAPSF", "categories": ["CloudApp"], "sources": ["SAP"], "iconLocation": "images/storages/SAP-Generic.png",
     "dpAgentRequired": False, "disReplicationRequired": True, "capabilities": ["HanaSdi", "DataflowSource"], "adapter": {"id": "ODataAdapter"},
     "CSNInjection": {"remoteTable": {"annotations": [{"name": "@x", "value": {"toDate": {"@Consumption.defaultValue": "2999-12-31"}}}]},
                      "dataflow": {"parameters": [{"name": "toDate", "datatype": "STRING", "low": "2999-12-31"},
                                                  {"name": "fromDate", "datatype": "STRING", "low": "1900-01-01"}]}}},
    {"typeId": "GENERICHTTP", "categories": ["CloudApp", "OnPremise"], "sources": ["SAP"], "iconLocation": "images/storages/http.png",
     "disReplicationRequired": True, "capabilities": ["ApiTasks"]},
]}


def _by_id(doc):
    return {t["type_id"]: t for t in doc["connection_types"]}


# ═══════════════ preservación ═══════════════
def test_preserves_types_order_and_declared_attributes_exactly():
    doc = sct.sanitize(copy.deepcopy(RAW))
    assert [t["type_id"] for t in doc["connection_types"]] == [t["typeId"] for t in RAW["connectionTypes"]]
    for raw, t in zip(RAW["connectionTypes"], doc["connection_types"]):
        assert t["categories"] == raw["categories"] and t["sources"] == raw["sources"] and t["capabilities"] == raw["capabilities"]
        assert t["icon_location"] == raw["iconLocation"]
    assert doc["count"] == len(RAW["connectionTypes"]) and sct.validate(doc, RAW) == []


def test_requirements_keep_three_states():
    t = _by_id(sct.sanitize(copy.deepcopy(RAW)))
    assert t["ABAP"]["requirements"]["dp_agent_required"] is True          # requerido
    assert t["SAPSF"]["requirements"]["dp_agent_required"] is False        # no requerido
    assert t["GENERICHTTP"]["requirements"]["dp_agent_required"] is None   # no declarado (≠ no requerido)
    assert t["AZURESQL"]["requirements"]["ip_allowlisting_required"] is True
    assert t["MYSQL"]["requirements"]["generic_framework"] is True and t["ABAP"]["requirements"]["generic_framework"] is None


def test_feature_flag_becomes_conditional_availability_without_its_identifier():
    doc = sct.sanitize(copy.deepcopy(RAW))
    t = _by_id(doc)
    assert t["MYSQL"]["availability"] == {"conditional_on_feature_toggle": True}
    assert all(t[i]["availability"] == {"conditional_on_feature_toggle": None} for i in t if i != "MYSQL")   # sin flag: no declarado
    assert M_FLAG not in json.dumps(doc)


def test_type_level_technical_data_is_summarized():
    t = _by_id(sct.sanitize(copy.deepcopy(RAW)))
    assert t["ABAP"]["adapter_id"] == "ABAPAdapter" and t["GENERICHTTP"]["adapter_id"] is None
    assert t["PARTNERX"]["partner"] == {"id": "PartnerX", "api_version": 1}
    assert t["MYSQL"]["generic_framework_type"] == {"ccm_type_id": "MYSQL", "ccm_type_version": "3.0.0"}
    assert t["SAPSF"]["default_parameters"] == ["fromDate", "toDate"]                          # nombres, nunca valores
    assert "default_parameters" not in t["ABAP"] and "partner" not in t["ABAP"]


def test_display_names_only_with_evidence():
    t = _by_id(sct.sanitize(copy.deepcopy(RAW)))
    assert t["ABAP"]["display_name"] == "SAP ABAP" and t["ABAP"]["display_name_source"] == "sap_ui_capture"
    assert t["MYSQL"]["display_name"] is None and t["MYSQL"]["display_name_source"] is None


# ═══════════════ filtrado del tenant ═══════════════
def test_tenant_specific_data_never_reaches_the_output():
    text = json.dumps(sct.sanitize(copy.deepcopy(RAW)), ensure_ascii=False)
    for marker in (M_AGENT, M_AGENT2, M_IP, "10.11.12.14", M_FLAG, M_URL, "mrk-partner", "MRK_INSTANCE", "connected", "2999-12-31",
                   "dpAgents", "agentName", "ipAddresses", "ocnInstances", "ocnConfigured", "iFrame", "featureFlag"):
        assert marker not in text, marker


@pytest.mark.parametrize("inject", [
    lambda d: d["connection_types"][0].__setitem__("agents", [M_AGENT]),
    lambda d: d["connection_types"][0].__setitem__("adapter_id", M_IP),
    lambda d: d["connection_types"][0].__setitem__("display_name", "Inventado"),
    lambda d: d["connection_types"][0]["requirements"].__setitem__("dp_agent_required", "yes"),
    lambda d: d["connection_types"][0].__setitem__("icon_location", M_URL),
    lambda d: d["connection_types"][0]["availability"].__setitem__("conditional_on_feature_toggle", False),
    lambda d: d.__setitem__("count", 99),
    lambda d: d["source"].__setitem__("tenant", "MRK"),
])
def test_validate_rejects_documents_outside_the_contract(inject):
    doc = sct.sanitize(copy.deepcopy(RAW))
    inject(doc)
    assert sct.validate(doc) != []


# ═══════════════ allowlist estricta ═══════════════
@pytest.mark.parametrize("mutate,fragment", [
    (lambda r: r["connectionTypes"][0].__setitem__("newTenantSetting", 1), "no clasificados"),
    (lambda r: r["connectionTypes"][0].pop("iconLocation"), "faltan"),
    (lambda r: r["connectionTypes"][0].pop("capabilities"), "faltan"),
    (lambda r: r["connectionTypes"][1].__setitem__("typeId", "ABAP"), "duplicados"),
    (lambda r: r["connectionTypes"][0].__setitem__("dpAgentRequired", "true"), "booleano"),
    (lambda r: r["connectionTypes"][0].__setitem__("adapter", {"id": "X", "host": "h"}), "adapter"),
    (lambda r: r.__setitem__("extra", {}), "connectionTypes"),
])
def test_unknown_or_malformed_attributes_reject_the_whole_transformation(mutate, fragment):
    raw = copy.deepcopy(RAW)
    mutate(raw)
    with pytest.raises(sct.ContractError, match=fragment):
        sct.sanitize(raw)


# ═══════════════ estabilidad e idempotencia ═══════════════
def test_transformation_is_deterministic_and_does_not_mutate_its_input():
    raw = copy.deepcopy(RAW)
    first = sct.render(sct.sanitize(raw))
    assert raw == RAW
    assert sct.render(sct.sanitize(copy.deepcopy(RAW))) == first


def test_contract_shape_is_stable():
    doc = sct.sanitize(copy.deepcopy(RAW))
    assert set(doc) == {"schema", "source", "count", "connection_types"} and doc["schema"] == "sap-connection-types/v1"
    assert doc["source"]["nature"] == "reference_snapshot"
    for t in doc["connection_types"]:
        assert set(t) - sct.OPTIONAL_TYPE_KEYS == sct.TYPE_KEYS - sct.OPTIONAL_TYPE_KEYS


def test_cli_generates_checks_and_rejects(tmp_path, capsys):
    raw_path, out = tmp_path / "raw.json", tmp_path / "out.json"
    raw_path.write_text(json.dumps(RAW), encoding="utf-8")
    assert sct.main([str(raw_path), "--out", str(out)]) == 0
    first = out.read_bytes()
    assert sct.main([str(raw_path), "--out", str(out)]) == 0 and out.read_bytes() == first           # idempotente
    assert sct.main(["--check", str(out)]) == 0
    tampered = json.loads(first)
    tampered["connection_types"][0]["agents"] = [M_AGENT]
    out.write_text(json.dumps(tampered), encoding="utf-8")
    assert sct.main(["--check", str(out)]) == 1
    bad = copy.deepcopy(RAW)
    bad["connectionTypes"][0]["newTenantSetting"] = 1
    raw_path.write_text(json.dumps(bad), encoding="utf-8")
    assert sct.main([str(raw_path), "--out", str(tmp_path / "x.json")]) == 2 and not (tmp_path / "x.json").exists()


# ═══════════════ JSON saneado versionado (content pack) ═══════════════
def test_pack_json_is_valid_and_rendered_canonically():
    text = PACK_JSON.read_text(encoding="utf-8")
    doc = json.loads(text)
    assert sct.validate(doc) == [] and text == sct.render(doc)


def test_pack_json_preserves_the_reference_snapshot():
    doc = json.loads(PACK_JSON.read_text(encoding="utf-8"))
    types = doc["connection_types"]
    assert doc["count"] == len(types) == 37
    assert [t["type_id"] for t in types] == [
        "ABAP", "ADL", "AdverityDatatap", "PRECOG", "ATHENA", "AZURESQL", "BIGQUERY", "CDI", "GCS", "GENERICHTTP", "GENERICJDBC",
        "HANA", "HDFS", "HDLDB", "HDL_FILES", "MSSQL", "MYSQL", "ODATA", "ONELAKE", "OPEN_CONNECTORS", "ORACLEDB", "REDSHIFT", "S3",
        "SAPBW", "SAPECC", "SAPSF", "SAPS4HANACLOUD", "SAPS4HANAOP", "SAPFIELDGLASS", "SAPMARKETINGCLOUD", "SAPBWMODELTRANSFER",
        "SIGNAVIO", "SFTP", "WASB", "KAFKA", "CONFLUENT", "SNOWFLAKE"]
    caps = Counter(c for t in types for c in t["capabilities"])
    assert caps == {"DataflowSource": 25, "HanaSdi": 19, "ReplicationflowSource": 18, "ReplicationflowTarget": 10, "ModelTransfer": 4,
                    "PartnerSchema": 2, "ApiTasks": 1}
    has = lambda t, *c: any(x in t["capabilities"] for x in c)
    # Mismos conteos que los filtros de la UI de SAP («Create Connection»):
    assert sum(has(t, "ReplicationflowSource", "ReplicationflowTarget") for t in types) == 21
    assert sum(has(t, "HanaSdi", "PartnerSchema") for t in types) == 21
    assert Counter(c for t in types for c in t["categories"]) == {"CloudApp": 31, "OnPremise": 12}
    assert Counter(s for t in types for s in t["sources"]) == {"NonSAP": 19, "SAP": 16, "Partner": 2}
    assert [t["type_id"] for t in types if t["availability"]["conditional_on_feature_toggle"]] == ["MYSQL"]
    assert sum(1 for t in types if t["display_name"]) == 12
    assert len({t["icon_location"] for t in types}) == 20 and all(t["icon_location"].startswith("images/storages/") for t in types)
