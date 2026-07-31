"""Clasificación de tipo de artefacto (Entity.kind_label).

Regresión del caso Data Products: una Local Table persistida con delta activo
(p. ej. `1TR_SAP_CSALESDOCUMENTITEMDEX1`) trae un `query` que es solo la proyección
de activación de su tabla `_Delta` → la heurística `has_query → Vista` la etiquetaba
mal. El discriminante autoritativo es `@DataWarehouse.repository.technicalType`
(DWC_LOCAL_TABLE), que el parser guarda en `Entity.repository_type`.
"""
from tools.catalog.generate_catalog import Entity


def _ent(**kw) -> Entity:
    return Entity(name=kw.pop("name", "X"), file=kw.pop("file", "X.json"), **kw)


def test_local_table_con_query_es_tabla_local_no_vista():
    # Local Table delta-active: tiene query (activación desde _Delta) PERO technicalType lo marca.
    ent = _ent(repository_type="DWC_LOCAL_TABLE", has_query=True)
    assert ent.kind_label == "Tabla local"


def test_vista_real_sigue_siendo_vista():
    # Vista SQL: query, sin technicalType de tabla → Vista.
    assert _ent(has_query=True).kind_label == "Vista"


def test_tabla_sin_query_sigue_siendo_tabla_local():
    assert _ent().kind_label == "Tabla local"


def test_remote_table_federada_es_tabla_remota_no_origen_remoto():
    # Remote Table federada (remote_entity, sin DWC_LOCAL_TABLE) → "Tabla remota":
    # el tipo describe el objeto; la remotez del origen va en la fila "Origen".
    assert _ent(remote_entity="SAPI.FOO").kind_label == "Tabla remota"
    # Pero si además está persistida como Local Table, la persistencia gana.
    assert _ent(remote_entity="SAPI.FOO", repository_type="DWC_LOCAL_TABLE").kind_label == "Tabla local"


def test_dataflow_no_se_ve_afectado():
    assert _ent(is_dataflow=True, has_query=True).kind_label == "Data Flow (tabla local)"


def test_metadata_rows_contrato_canonico_compartido():
    # FUENTE ÚNICA del orden/labels de la tabla de metadata (Dominios ↔ Data Products).
    from tools.catalog.generate_catalog import artifact_metadata_rows
    rows = artifact_metadata_rows(
        capa="Ingesta", artefacto="Tabla remota", uso="Relacional",
        origen="`X` · conexión `Y`", deriva_de=["A"], asociaciones=["B"],
        modeling_pattern="DATA_STRUCTURE",
    )
    assert [k for k, _ in rows] == [
        "Capa", "Artefacto", "Uso semántico", "Origen", "Deriva de",
        "Asociaciones", "Patrón de modelado",
    ]
    # opcionales fuera → no aparecen; el nombre técnico NO es una fila (va en el heading)
    minimal = artifact_metadata_rows(capa="Ingesta", artefacto="Tabla local")
    assert [k for k, _ in minimal] == ["Capa", "Artefacto"]
