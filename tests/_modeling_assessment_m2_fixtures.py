"""Fixtures SINTETICAS compartidas por los tests de M2 (nombres genericos; sin datos de clientes reales).

Producto sintetico `AM_P`:

    AM_P --lineage--> VF_P --lineage--> VR_P --lineage--> RAW_T   (tabla fisica, conexion S4PREM, con filtro `where`)
                                                       \\-> RAW_T2  (MISMA entidad remota, conexion ERP: duplicado + dual)
                                                       \\-> CDS_A, CDS_A2 (misma entidad CDS: duplicado)
                                                       \\-> LOCAL_T (estructura local; la referencia un RF)
                                                       \\-> LOCAL_U (estructura local sin referencia de Processing)
                                                       \\-> DF_T    (target de Data Flow => contexto de Processing)
    AM_P --assoc--> DIM_P --(FROM/JOIN degradado por el loader)--> DIM_SRC (SAPI)  y  SAP.TIME.X (sistema)
    AM_Q --> VF_Q --> CDS_A3 (misma entidad CDS que CDS_A: duplicado en el universo, fuera de la clausura de AM_P)
    ORPHAN: no pertenece a ninguna clausura.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.test_modeling_assessment_graph import ent, pool


def f(*names):
    return [{"campo": n, "label": n, "key": False} for n in names]


def m2_entities():
    ents = [
        ent("AM_P", up=["VF_P"], assoc=["DIM_P"], declared=["DIM_P"], raw=["VF_P"], cube=True, fields=f("A", "B")),
        ent("VF_P", up=["VR_P"], raw=["VR_P"], pattern="ANALYTICAL_FACT", fields=f("A", "B", "C"), has_query=True),
        ent("VR_P", up=["RAW_T", "RAW_T2", "CDS_A", "CDS_A2", "LOCAL_T", "LOCAL_U", "DF_T"],
            raw=["RAW_T", "RAW_T2", "CDS_A", "CDS_A2", "LOCAL_T", "LOCAL_U", "DF_T", "Union 1"],
            fields=f("A", "B", "C"), has_query=True),
        ent("RAW_T", remote_entity="ABAPTABLES.TAB1", remote_connection="S4PREM", keys=["MANDT", "K1"],
            fields=f("MANDT", "K1", "A", "B", "C", "D"), n_elements=7, label="CDS View TAB1: texto"),
        ent("RAW_T2", remote_entity="ABAPTABLES.TAB1", remote_connection="ERP", fields=f("A", "B")),
        ent("CDS_A", remote_entity="ABAP_CDS.CDSA$E", remote_connection="S4PREM", keys=["K"], fields=f("A", "X")),
        ent("CDS_A2", remote_entity="ABAP_CDS.CDSA$E", remote_connection="S4PREM", fields=f("A")),
        ent("LOCAL_T", fields=f("A")),
        ent("LOCAL_U", fields=f("A")),
        ent("DF_T", dataflow=True, up=["ONLY_VIA_DF"]),
        ent("ONLY_VIA_DF"),
        ent("DIM_P", assoc=["DIM_SRC", "SAP.TIME.X"], raw=["DIM_SRC", "SAP.TIME.X"], pattern="ANALYTICAL_DIMENSION",
            fields=f("K", "T"), has_query=True),
        ent("DIM_SRC", remote_entity="SAPI.0DIMSRC_ATTR", remote_connection="S4PREM", fields=f("K", "T")),
        ent("SAP.TIME.X"),
        ent("AM_Q", up=["VF_Q"], raw=["VF_Q"], cube=True),
        ent("VF_Q", up=["CDS_A3"], raw=["CDS_A3"], pattern="ANALYTICAL_FACT", has_query=True),
        ent("CDS_A3", remote_entity="ABAP_CDS.CDSA$E", remote_connection="S4PREM"),
        ent("ORPHAN", remote_entity="ABAPTABLES.ORPH", remote_connection="S4PREM"),
    ]
    return pool(*ents)


def _remote_table(name, connection, remote_entity, *, where=None, extra=None):
    body = {"kind": "entity", "@DataWarehouse.remote.connection": connection,
            "@DataWarehouse.remote.entity": remote_entity,
            "@ObjectModel.supportedCapabilities": [{"#": "SQL_DATA_SOURCE"}, {"#": "DATA_STRUCTURE"}],
            "@ObjectModel.modelingPattern": {"#": "DATA_STRUCTURE"}, "elements": {"A": {"type": "cds.String"}}}
    if where is not None:
        body["@DataWarehouse.remote.query"] = {"SELECT": {"columns": [], "from": {"ref": [name]}, "where": where}}
    body.update(extra or {})
    return {"definitions": {name: body}}


WHERE_LANG = [{"ref": ["LANGUAGE"]}, "=", {"val": "S"}]


def write_pool_files(root: Path, *, replication_annotation: bool = False, with_flows: bool = True,
                     include_cds_a_file: bool = True) -> list[Path]:
    """Escribe archivos CSN sinteticos con la MISMA forma que el export real y devuelve sus rutas."""
    files: dict[str, dict | str] = {
        "remote-tables/RAW_T.json": _remote_table("RAW_T", "S4PREM", "ABAPTABLES.TAB1", where=WHERE_LANG,
                                                  extra={"@DataWarehouse.replication.mode": "X"} if replication_annotation else None),
        "remote-tables/RAW_T2.json": _remote_table("RAW_T2", "ERP", "ABAPTABLES.TAB1"),
        "remote-tables/CDS_A2.json": _remote_table("CDS_A2", "S4PREM", "ABAP_CDS.CDSA$E"),
        "remote-tables/DIM_SRC.json": _remote_table("DIM_SRC", "S4PREM", "SAPI.0DIMSRC_ATTR"),
        "local-tables/LOCAL_T.json": {"definitions": {"LOCAL_T": {"kind": "entity", "elements": {}}}},
    }
    if include_cds_a_file:
        files["remote-tables/CDS_A.json"] = _remote_table("CDS_A", "S4PREM", "ABAP_CDS.CDSA$E")
    if with_flows:
        files["replication-flows/RF_LOCAL.json"] = {"replicationflows": {"RF_LOCAL": {
            "kind": "replicationflow",
            "contents": {"sourceSystem": [{"connectionId": "S4PREM", "connectionType": "SAPS4HANAOP",
                                           "container": "/CDS_EXTRACTION"}],
                         "replicationTasks": [{"name": "t1", "sourceObject": {"name": "LOCAL_T"},
                                               "targetObject": {"name": "LOCAL_T_Delta"}}]},
            "sources": {}, "targets": {"LOCAL_T_Delta": {"elements": {}}}, "connections": {"S4PREM": {}}}}}
        files["data-flows/DF_X.json"] = {"dataflows": {"DF_X": {
            "kind": "dataflow", "contents": {}, "sources": {"VR_P": {"elements": {}}}, "targets": {"DF_T": {"elements": {}}}}}}
    files["views/INVALID.json"] = "{ esto no es json"
    files["views/LIST_ROOT.json"] = ["no", "es", "objeto"]
    out = []
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
        out.append(p)
    return out
