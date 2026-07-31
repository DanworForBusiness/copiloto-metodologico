"""Vínculo Data Product → CDS de extracción vía Replication Flow (Opción A).

En Data Products la capa de ingesta son tablas locales con delta capture; no traen
`@DataWarehouse.remote.entity` (eso es de las tablas remotas de Dominios). El vínculo
a la CDS de extracción vive en el objeto Replication Flow:
`replicationflows.<RF>.contents.replicationTasks[].sourceObject.name` (CDS) →
`targetObject.name` (tabla local, con sufijo `_Delta` cuando es delta capable).
"""
import json
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent / "tools" / "catalog"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import generate_data_products_catalog as g  # noqa: E402


def _write_rf(folder: Path, name: str, tasks: list[tuple[str, str]]) -> None:
    payload = {
        "replicationflows": {
            name: {"contents": {"replicationTasks": [
                {"sourceObject": {"name": src}, "targetObject": {"name": tgt}}
                for src, tgt in tasks
            ]}}
        }
    }
    (folder / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_replication_sources_mapea_target_sin_delta_a_su_cds(tmp_path):
    _write_rf(tmp_path, "RF_SalesDocumentItem",
              [("C_SALESDOCUMENTITEMDEX_1", "1TR_SAP_CSALESDOCUMENTITEMDEX1_Delta")])
    _write_rf(tmp_path, "RF_ICUSTOMER", [("I_CUSTOMER", "I_CUSTOMER")])

    m = g._replication_sources(tmp_path)
    # target con _Delta → se recorta a la tabla base; sin _Delta → tal cual (case-insensitive)
    assert m["1tr_sap_csalesdocumentitemdex1"] == "C_SALESDOCUMENTITEMDEX_1"
    assert m["i_customer"] == "I_CUSTOMER"


def test_replication_flow_no_se_confunde_con_artefacto_de_linaje(tmp_path):
    # Un RF trae también un bloque `definitions`; no debe contarse como artefacto.
    payload = {
        "definitions": {"1TR_SAP_X_Delta": {"kind": "entity"}},
        "replicationflows": {"RF_X": {"contents": {"replicationTasks": [
            {"sourceObject": {"name": "C_X"}, "targetObject": {"name": "1TR_SAP_X_Delta"}}]}}},
    }
    (tmp_path / "RF_X.json").write_text(json.dumps(payload), encoding="utf-8")
    assert g._is_replication_flow(payload) is True
    assert g._replication_sources(tmp_path)["1tr_sap_x"] == "C_X"
