"""Fixtures SINTETICAS compartidas por los tests de M3 (nombres genericos; sin datos de clientes reales).

Producto sintetico `AM_M` (cubo terminal) y un universo con otros AM terminales:

    AM_M --lineage--> VF_M --lineage--> VR_M --lineage--> SRC1, SRC2   (remotas)
                                                      \\-> DIM_V        (dimension declarada, via LINAJE; con fan-in no)
                                                      \\-> NOQ_V        (vista SIN query visible)
    AM_M --assoc--> DIM_A, DIM_B  (y una 2da asociacion a DIM_A con OTRO nombre de campo)
    VF_M --assoc--> DIM_A, DIM_B  (mismos destinos, otros nombres de campo: el AM los HEREDA)
    DIM_A --assoc--> DIM_A_TXT ;  DIM_B sin asociaciones
    DIM_A --assoc--> SAP.TIME.DAY (sistema) --assoc--> SAP.TIME.M (sistema, hoja)
    AM_N --assoc--> DIM_A ; AM_O --assoc--> DIM_A, DIM_B ; AM_P --lineage--> VF_P (no contiene DIM_A/DIM_B)
    DF_T: target de Data Flow (contexto Processing; fuera del grafo de Modeling).

Reuso esperado desde AM_M (otros AM terminales: AM_N, AM_O, AM_P):  CROSS_AM_REUSE(DIM_A)=2 (AM_N, AM_O);
CROSS_AM_REUSE(DIM_B)=1 (AM_O); el AM evaluado se excluye.
"""
from __future__ import annotations

from tests.test_modeling_assessment_graph import ent, pool


def fld(name, *, tipo="String", longitud="", measure=False, derived=False, label=True, key=False, assoc="", cast=""):
    return {"campo": name, "label": name.title() if label else "", "tipo": tipo, "longitud": longitud, "key": key,
            "medida": measure, "assoc": assoc, "derived": derived, "cast": cast}


def with_assocs(e, pairs):
    """Asociaciones con nombres de campo EXPLICITOS: [(campo_asociacion, destino), ...]."""
    e.associations = [{"campo": c, "target": t, "on": {"K": "K"}} for c, t in pairs]
    e.assoc_upstreams = sorted({t for _, t in pairs})
    return e


def m3_entities():
    am = with_assocs(
        ent("AM_M", up=["VF_M"], cube=True, has_query=True, n_elements=14,
            fields=[fld("K", key=False), fld("A"), fld("B", assoc="DIM_B"), fld("AMT", tipo="Decimal", longitud="17,3", measure=True),
                    fld("QTY", tipo="Decimal", longitud="15,3", measure=True),
                    fld("CALC", tipo="Decimal", longitud="17,3", measure=True, derived=True),
                    fld("W", tipo="Decimal", longitud="13,3"), fld("TXT", label=False)],
            measures=[{"campo": "AMT", "agg": ""}, {"campo": "QTY", "agg": ""}, {"campo": "CALC", "agg": "FORMULA"}]),
        [("_dimA", "DIM_A"), ("_dimB", "DIM_B"), ("_dimA2", "DIM_A")])
    vf = with_assocs(
        ent("VF_M", up=["VR_M"], pattern="ANALYTICAL_FACT", has_query=True, keys=["K"],
            fields=[fld("K", key=True), fld("A"), fld("B"), fld("AMT", tipo="Decimal", longitud="17,3", measure=True, derived=True),
                    fld("QTY", tipo="Decimal", longitud="15,3", measure=True, derived=True), fld("W", tipo="Decimal", longitud="13,3"),
                    fld("NEWF"), fld("OLDF")],
            measures=[{"campo": "AMT", "agg": "SUM"}, {"campo": "QTY", "agg": "SUM"}]),
        [("_a1", "DIM_A"), ("_b1", "DIM_B")])
    vr = ent("VR_M", up=["SRC1", "SRC2", "DIM_V", "NOQ_V"], has_query=True, keys=["K"],
             fields=[fld("K", key=True), fld("A"), fld("B"), fld("AMT", tipo="Decimal", longitud="17,3", derived=True),
                     fld("W", tipo="Decimal", longitud="13,3"), fld("OLDF")])
    dim_a = with_assocs(
        ent("DIM_A", pattern="ANALYTICAL_DIMENSION", has_query=True, fields=[fld("K"), fld("T")]),
        [("_txt", "DIM_A_TXT"), ("_day", "SAP.TIME.DAY")])
    dim_a.upstreams = []
    dim_b = ent("DIM_B", pattern="ANALYTICAL_DIMENSION", has_query=True, fields=[fld("K"), fld("T"), fld("U")])
    dim_v = ent("DIM_V", up=["SRC3"], pattern="ANALYTICAL_DIMENSION", has_query=True, fields=[fld("K"), fld("T")])
    noq = ent("NOQ_V", up=["SRC1"], fields=[fld("K"), fld("A")])
    sys_day = with_assocs(ent("SAP.TIME.DAY", pattern="ANALYTICAL_DIMENSION", fields=[fld("K")]), [("_m", "SAP.TIME.M")])
    ents = [
        am, vf, vr, dim_a, dim_b, dim_v, noq, sys_day, ent("SAP.TIME.M", fields=[fld("K")]), ent("DIM_A_TXT", fields=[fld("K")]),
        ent("SRC1", remote_entity="ABAP_CDS.S1", remote_connection="C1", fields=[fld("K"), fld("A")]),
        ent("SRC2", remote_entity="ABAP_CDS.S2", remote_connection="C1", fields=[fld("K"), fld("B")]),
        ent("SRC3", remote_entity="ABAP_CDS.S3", remote_connection="C1", fields=[fld("K")]),
        with_assocs(ent("AM_N", up=["VF_N"], cube=True, has_query=True), [("_x", "DIM_A")]),
        ent("VF_N", up=["SRC1"], pattern="ANALYTICAL_FACT", has_query=True),
        with_assocs(ent("AM_O", cube=True, has_query=True), [("_y", "DIM_A"), ("_z", "DIM_B")]),
        ent("AM_P", up=["VF_P"], cube=True, has_query=True), ent("VF_P", pattern="ANALYTICAL_FACT", has_query=True),
        ent("DF_T", dataflow=True, up=["ONLY_VIA_DF"]), ent("ONLY_VIA_DF"),
    ]
    # AM_M, VF_M tambien dependen de DF_T? No: DF_T queda como contexto Processing, referenciado desde VR_M.
    ents[2].upstreams = sorted(ents[2].upstreams + ["DF_T"])
    return pool(*ents)
