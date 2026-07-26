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


# ── Invariantes de FUENTE ÚNICA del criterio (epic B 1.2 — solidez) ──────────
# Si alguien reintroduce el criterio por copia, estos tests se ponen rojos.

def test_invariante_fuente_unica_del_clasificador():
    """`classify_extraction` es UN solo objeto compartido por el motor de ponderación
    (generate_catalog) y la reconciliación — no copias que puedan divergir."""
    from tools.catalog.compliance_criteria import classify_extraction as C
    from tools.catalog.generate_catalog import classify_extraction as G
    import analyze_reconciliation as R
    assert C is G is R._classify


def test_invariante_fuente_unica_del_apto():
    """Las frases canónicas de `apto` son el MISMO objeto en catalog_service y en el
    módulo de criterio; y el generador deriva plena/parcial de esas bases (no copia)."""
    import sys
    from config import BASE_DIR
    sys.path.insert(0, str(BASE_DIR / "tools" / "catalog"))
    import catalog_service
    from tools.catalog.compliance_criteria import APTO_CANONICAL
    assert catalog_service._APTO_ESTANDAR is APTO_CANONICAL
    _, v_plena, apto_plena = evaluate_compliance(
        _entities(*[_ent(f"ZCDS_{i}", f"ABAP_CDS.Z{i}$E") for i in range(3)])
    )
    assert v_plena == "plena" and apto_plena.startswith(APTO_CANONICAL["plena"])
    _, v_parc, apto_parc = evaluate_compliance(
        _entities(_ent("FACT", "ABAPTABLES.MBEW", cube=True))
    )
    assert v_parc == "parcial" and apto_parc == APTO_CANONICAL["parcial"]


def test_invariante_reconciliacion_detecta_divergencia_auto_senior():
    """La reconciliación marca —sola— cuando el veredicto auto ≠ el override del
    senior (drift de criterio), y no marca cuando coinciden o no hay override."""
    import analyze_reconciliation as R
    flows = [
        {"flow": "X · A", "auto_verdict": "parcial", "senior_override": "buena", "origenes": [], "hallazgos": []},
        {"flow": "Y · B", "auto_verdict": "plena", "senior_override": "", "origenes": [], "hallazgos": []},
        {"flow": "Z · C", "auto_verdict": "buena", "senior_override": "buena", "origenes": [], "hallazgos": []},
    ]
    div = R.find_divergences(flows)
    assert [d["flow"] for d in div] == ["X · A"]
