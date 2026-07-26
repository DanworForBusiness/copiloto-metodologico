"""Rúbrica de cumplimiento del catálogo de flujos (generate_catalog.evaluate_compliance).

Cubre la rúbrica PROPORCIONAL (epic B (a)) y la regla nombre-vs-realidad (1.1):
- una tabla ABAP cruda minoritaria y periférica es `buena · reserva puntual`, no `parcial`;
- pero es `parcial` (sistémico) si es material (>1/3 del sourcing ABAP), si alimenta el
  hecho/cubo, o si hay fuentes en paralelo (dual_source);
- `name_vs_reality` marca (nota, no baja veredicto) un artefacto ZCDS cuyo origen no es
  ABAP_CDS.
"""
from tools.catalog.generate_catalog import Entity, evaluate_compliance


def _ent(name: str, remote_entity: str = "", conn: str = "S4PREM", *, cube: bool = False) -> Entity:
    return Entity(
        name=name, file=f"{name}.json",
        data_category=("CUBE" if cube else ""),
        remote_connection=conn, remote_entity=remote_entity,
    )


def _entities(*ents: Entity) -> dict:
    return {e.name: e for e in ents}


def _verdict(entities: dict) -> str:
    return evaluate_compliance(entities)[1]


def _finding_ids(entities: dict) -> set:
    return {f.id for f in evaluate_compliance(entities)[0]}


def test_todo_ideal_es_plena():
    ents = _entities(*[_ent(f"ZCDS_DIM_{i}", f"ABAP_CDS.ZCDS_DIM_{i}$E") for i in range(5)])
    assert _verdict(ents) == "plena"


def test_cruda_periferica_minoritaria_es_buena_reserva():
    # 10 orígenes desacoplan (ideal) + 1 tabla cruda periférica = reserva puntual.
    ideal = [_ent(f"ZCDS_DIM_{i}", f"ABAP_CDS.ZCDS_DIM_{i}$E") for i in range(10)]
    raw = _ent("1TR_A004", "ABAPTABLES.A004")
    findings, veredicto, apto = evaluate_compliance(_entities(*ideal, raw))
    assert veredicto == "buena"
    assert "reserva puntual" in apto.lower()
    # el hallazgo es `medio`, no `alto`, y expone el ratio en la evidencia
    ad = next(f for f in findings if f.id == "abap_direct")
    assert ad.severidad == "medio"
    assert "10 de 11" in ad.evidencia


def test_cruda_en_el_hecho_es_parcial():
    # una sola cruda, pero ES el cubo/hecho → sistémico → parcial.
    ideal = [_ent(f"ZCDS_DIM_{i}", f"ABAP_CDS.ZCDS_DIM_{i}$E") for i in range(10)]
    raw_fact = _ent("FACT_CRUDO", "ABAPTABLES.MBEW", cube=True)
    findings, veredicto, _ = evaluate_compliance(_entities(*ideal, raw_fact))
    assert veredicto == "parcial"
    assert next(f for f in findings if f.id == "abap_direct").severidad == "alto"


def test_cruda_material_es_parcial():
    # 2 desacoplan + 2 crudas = 50% del sourcing ABAP (>1/3) → material → parcial.
    ents = _entities(
        _ent("ZCDS_A", "ABAP_CDS.ZCDS_A$E"),
        _ent("ZCDS_B", "ABAP_CDS.ZCDS_B$E"),
        _ent("RAW_A", "ABAPTABLES.TA"),
        _ent("RAW_B", "ABAPTABLES.TB"),
    )
    assert _verdict(ents) == "parcial"


def test_dual_source_es_parcial():
    # misma entidad origen por 2 conexiones en paralelo → alto → parcial,
    # aunque la extracción sea ideal.
    ents = _entities(
        _ent("ZCDS_X_ERP", "ABAP_CDS.ZCDS_X$E", conn="ERP"),
        _ent("ZCDS_X_S4", "ABAP_CDS.ZCDS_X$E", conn="S4_HANA"),
    )
    findings, veredicto, _ = evaluate_compliance(ents)
    assert veredicto == "parcial"
    assert "dual_source" in {f.id for f in findings}


def test_name_vs_reality_marca_zcds_sobre_vista_estandar():
    # nombre ZCDS (pretende CDS corporativa) pero origen NSDM_V (vista estándar).
    ents = _entities(_ent("1TR_ZCDS_DSP_FACT_MCHB", "ABAPTABLES.NSDM_V_MCHB"))
    assert "name_vs_reality" in _finding_ids(ents)


def test_name_vs_reality_no_marca_zcds_sobre_abap_cds():
    # nombre ZCDS y origen ABAP_CDS.* → nombre == realidad, sin nota.
    ents = _entities(_ent("1TR_ZCDS_DSP_DIM_ADRC", "ABAP_CDS.ZCDS_DSP_DIM_ADRC$E"))
    assert "name_vs_reality" not in _finding_ids(ents)
