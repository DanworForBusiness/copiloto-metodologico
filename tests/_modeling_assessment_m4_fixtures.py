"""Fixtures SINTETICAS de M4 (Naming). Nombres genericos; sin datos de clientes reales.

Producto sintetico con AM terminal `AM_X` y un universo mixto de procedencias y de formas de nombre:

    AM_X --L--> 3VF_X --L--> 2VR_X --L--> VD_DIM      --L--> ZCDS_A (remota, exacta)   , 0MAT_ATTR (remota SAPI, exacta)
                                     \\--L--> 1TR_ZCDS_B (remota; el proyecto agrego el prefijo `1TR`)
                                     \\--L--> LOC_T       (tabla local sin remoto ni query: identidad UNKNOWN)
    AM_X --assoc--> SAP.TIME.DAY (sistema)

Cumplimiento de §28 esperado (parse_name tal cual):
    cumplen:    AM_X (artefacto tipado), 3VF_X, 2VR_X, 1TR_ZCDS_B
    no cumplen: VD_DIM (MISSING_LAYER_PREFIX), ZCDS_A / 0MAT_ATTR / LOC_T (NO_RECOGNIZED_PATTERN)
Procedencia (M1): PROJECT_ASSIGNED = AM_X, 3VF_X, 2VR_X, VD_DIM ; SOURCE_DERIVED_IMPORTED = ZCDS_A, 1TR_ZCDS_B, 0MAT_ATTR
(1TR_ZCDS_B por regla HEURISTICA EXTENDS_STEM) ; UNKNOWN = LOC_T ; SYSTEM = SAP.TIME.DAY.

Etiquetas: "Sociedad" la comparten BUKRS y COMPANYCODE ; "Material" la comparten MATNR y MATERIAL ;
"Importe ARS" la comparten AMT_ML y DUP (ambos en el AM). `LAND1` tiene etiqueta == campo ; `ORDERID` no tiene etiqueta.
"""
from __future__ import annotations

from tests._modeling_assessment_m3_fixtures import with_assocs
from tests.test_modeling_assessment_graph import ent, pool


def f4(name, label="", *, derived=False):
    return {"campo": name, "label": label, "tipo": "String", "longitud": "", "key": False, "medida": False,
            "assoc": "", "derived": derived, "cast": ""}


def m4_entities():
    src_a = ent("ZCDS_A", remote_entity="ABAP_CDS.ZCDS_A", remote_connection="C1",
                fields=[f4("BUKRS", "Sociedad"), f4("MATNR", "Material"), f4("ZED_NIVEL", "Nivel"), f4("LAND1", "LAND1")])
    src_b = ent("1TR_ZCDS_B", remote_entity="ABAP_CDS.ZCDS_B", remote_connection="C1",
                fields=[f4("COMPANYCODE", "Sociedad"), f4("MATERIAL", "Material"), f4("ORDERID", "")])
    sapi = ent("0MAT_ATTR", remote_entity="SAPI.0MAT_ATTR", remote_connection="C2", fields=[f4("MATNR", "Material")])
    loc = ent("LOC_T", repository_type="DWC_LOCAL_TABLE", fields=[f4("LK", "Clave")])
    vd = ent("VD_DIM", up=["ZCDS_A", "0MAT_ATTR"], pattern="ANALYTICAL_DIMENSION", has_query=True,
             fields=[f4("BUKRS", "Sociedad"), f4("MATNR", "Material"), f4("DESC_NEW", "Descripcion")])
    vr = ent("2VR_X", up=["VD_DIM", "1TR_ZCDS_B", "LOC_T"], has_query=True,
             fields=[f4("BUKRS", "Sociedad"), f4("COMPANYCODE", "Sociedad"), f4("MATNR", "Material"),
                     f4("MID", "Medio"), f4("LK", "Clave")])
    vf = ent("3VF_X", up=["2VR_X"], pattern="ANALYTICAL_FACT", has_query=True,
             fields=[f4("BUKRS", "Sociedad"), f4("COMPANYCODE", "Sociedad"), f4("MID", "Medio"),
                     f4("AMT_ML", "Importe ARS", derived=True), f4("LK", "Clave")])
    am = with_assocs(
        ent("AM_X", up=["3VF_X"], cube=True, has_query=True, n_elements=7,
            fields=[f4("BUKRS", "Sociedad"), f4("MID", "Medio"), f4("AMT_ML", "Importe ARS", derived=True),
                    f4("DUP", "Importe ARS"), f4("LK", "Clave")]),
        [("_day", "SAP.TIME.DAY")])
    day = ent("SAP.TIME.DAY", pattern="ANALYTICAL_DIMENSION", fields=[f4("DAY", "Dia")])
    return pool(am, vf, vr, vd, src_a, src_b, sapi, loc, day)


def m4_pair_with_identical_artifact_naming(*, rich_fields: bool):
    """Dos universos con EXACTAMENTE los mismos nombres de artefacto (mismo §28, misma procedencia) y distinto
    contrato de CAMPO: demuestra que el nivel artefacto solo no separa casos y que M4 conserva lo que si los separa."""
    def label(name):
        return name.title() if rich_fields else name           # etiqueta de negocio vs etiqueta == nombre tecnico
    src = ent("ZCDS_Q", remote_entity="ABAP_CDS.ZCDS_Q", remote_connection="C1",
              fields=[f4("vbeln", label("vbeln")), f4("matnr", label("matnr"))])
    vr = ent("VQ_X", up=["ZCDS_Q"], has_query=True,
             fields=[f4("vbeln", label("vbeln")), f4("matnr", label("matnr"))])
    am = ent("AM_Q", up=["VQ_X"], cube=True, has_query=True,
             fields=[f4("vbeln", label("vbeln")), f4("matnr", label("matnr"))])
    return pool(am, vr, src)
