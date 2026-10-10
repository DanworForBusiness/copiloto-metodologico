"""Compatibilidad BDC v1 (`compatibilidad_bdc/v1`): eje ADITIVO por restricciones, independiente del score de portabilidad.

Controles sintéticos por destino + integridad de la matriz (`content_packs/datasphere_core/portability_bdc.yaml`, sección `compatibilidad`).
La evidencia es el `tipo`/`longitud` canónico; el nombre del campo nunca decide nada."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools.catalog import compat_bdc as cb
from tools.catalog import portability_bdc as pb

REPO = Path(__file__).resolve().parents[1]
MX = cb.load_compatibility("datasphere_core")
RF_LTF = {"mecanismo": "replication_flow", "destino": "local_table_file"}
TF_LTF = {"mecanismo": "transformation_flow", "destino": "local_table_file"}
DP = {"mecanismo": "data_product_delta_share", "destino": "custom_data_product"}
RF_HANA = {"mecanismo": "replication_flow", "destino": "hana_local_table"}


def col(campo, tipo, longitud="", art="A1"):
    return {"artefacto": art, "campo": campo, "tipo": tipo, "longitud": longitud}


def run(cols, *declared):
    ids, bad = cb.parse_declared(list(declared) if declared else None, MX)
    assert not bad
    return cb.evaluate_compatibility(cols, ids, MX, bad)


def scen(result, sid):
    return next(s for s in result["escenarios"] if s["id"] == sid)


def hit(result, sid, rid):
    return next((r for r in scen(result, sid)["resultados"] if r["restriccion"] == rid), None)


KENSING_LIKE = ([col(f"Amount{i}", "DecimalFloat") for i in range(24)] + [col("CostCenter", "String", "10"), col("CostCenterName", "String", "40"),
                col("Qty", "Decimal", "23,3"), col("Uuid", "Binary", "16"), col("Created", "Timestamp"), col("Num", "Decimal", "15,0")])


# ── la matriz es válida y consistente ───────────────────────────────────────────────────────────────────────────────
def test_matrix_loads_with_the_versioned_contract():
    assert MX and MX["contrato"] == "compatibilidad_bdc/v1" and MX["restricciones"] and MX["escenarios"]


def test_matrix_integrity_every_constraint_is_typed_scoped_sourced_and_validated():
    ids, valid = set(), {t.lower() for t in MX["tipos_datasphere"]}
    for r in MX["restricciones"]:
        assert re.fullmatch(r"SAPC-[A-Z]+-\d{3}", r["id"]) and r["id"] not in ids, r["id"]
        ids.add(r["id"])
        assert r["resultado"] in MX["estados_compatibilidad"] and r["resultado"] != "no_evaluable", r["id"]
        assert r["estado_validacion"] in MX["estados_validacion"], r["id"]
        assert {t.lower() for t in r["tipos"]} <= valid, r["id"]                                   # solo tipos válidos en Datasphere
        alc = r["alcance"]
        assert set(alc["mecanismos"]) <= set(MX["mecanismos"]) | {"*"} and set(alc["destinos"]) <= set(MX["destinos"]), r["id"]
        assert set(r["fuentes"]) <= set(MX["fuentes"]), r["id"]
        assert r["recomendacion"] and r["detalle"] and r["metodologia"]["section_key"], r["id"]
        for e in r.get("evidencias") or []:
            assert e["estado_validacion"] in MX["estados_validacion"] and e["texto"]
        if r["resultado"] == "incompatible_explicita":                                              # «no figura» NUNCA alcanza: solo lo que SAP dice de forma expresa
            assert r["estado_validacion"] == "sap_documentado" and r["fuentes"], r["id"]
        if r["estado_validacion"] == "sap_documentado":
            assert r["fuentes"], r["id"]


def test_sources_are_the_four_official_sap_pages_and_labels_fit_the_support_link_contract():
    f = MX["fuentes"]
    assert set(f) == {"SAP-RF-TIPOS", "SAP-LTF-TIPOS", "SAP-COL-TIPOS", "SAP-DP-BDC"}
    for s in f.values():
        assert s["url"].startswith("https://help.sap.com/docs/SAP_DATASPHERE/") and s["url"].endswith(".html?locale=en-US")
        assert re.fullmatch(r"[A-Za-z0-9 _-]+", s["etiqueta"]), s["etiqueta"]                       # SUPPORT_ITEM_RE: solo [A-Za-z0-9 _-]
    assert len({s["etiqueta"] for s in f.values()}) == 4


def test_scenarios_are_unique_mechanism_destination_pairs_over_the_declared_vocabulary():
    pairs = [(s["mecanismo"], s["destino"]) for s in MX["escenarios"]]
    assert len(pairs) == len(set(pairs)) and all(m in MX["mecanismos"] and d in MX["destinos"] for m, d in pairs)


def test_no_score_and_no_pb_rules_are_part_of_the_axis():
    out = run(KENSING_LIKE)
    assert "score" not in out and "confianza" not in out and "clasificacion" not in out
    assert not any(k.startswith("PB-") for k in str(MX["restricciones"]).split("'"))               # las reglas históricas siguen siendo del otro eje


# ── destino no declarado: compatibilidad condicionada + preguntas ───────────────────────────────────────────────────
def test_without_a_declared_destination_the_state_is_conditioned_and_all_scenarios_are_hypotheses():
    out = run(KENSING_LIKE)
    assert out["declarado"] is False and out["estado_global"] == cb.UNDECLARED
    assert {s["id"] for s in out["escenarios"]} == {s["id"] for s in MX["escenarios"]} and not any(s["declarado"] for s in out["escenarios"])
    texts = [q["texto"] for q in out["preguntas"]]
    assert any("destino" in t.lower() for t in texts) and any("mecanismo" in t.lower() for t in texts) and any("Delta Sharing" in t for t in texts)
    assert any("UTC" in t for t in texts) and any("DecimalFloat" in t for t in texts)               # preguntas específicas de las restricciones relevantes


# ── DecimalFloat: detectado por TIPO, con alcance por mecanismo/destino (nunca universal) ──────────────────────────
def test_decimalfloat_is_detected_by_type_independent_of_the_field_name():
    named_money = run([col("AmountInCompanyCodeCurrency", "DecimalFloat")], RF_LTF)
    neutral = run([col("Foo", "DecimalFloat")], RF_LTF)
    for out in (named_money, neutral):
        h = hit(out, "ESC-RF-LTF", "SAPC-RFLTF-001")
        assert h and h["estado"] == "conversion_documentada" and h["campos"] == 1


def test_rf_to_local_table_file_is_a_documented_conversion_to_decimal_38_6():
    out = run([col("A", "DecimalFloat"), col("B", "DecimalFloat")], RF_LTF)
    h = hit(out, "ESC-RF-LTF", "SAPC-RFLTF-001")
    assert (h["estado"], h["estado_validacion"], h["convierte_a"]) == ("conversion_documentada", "sap_documentado", "DECIMAL(38,6)")
    assert {"SAP-RF-TIPOS", "SAP-LTF-TIPOS"} <= set(h["fuentes"]) and [t["id"] for t in h["tambien"]] == ["SAPC-LTF-001"]
    assert any(e["estado_validacion"] == "danwor_empirico" and "Pampa" in e["texto"] for e in h["evidencias"])
    assert scen(out, "ESC-RF-LTF")["estado"] == "conversion_documentada" and out["estado_global"] == "conversion_documentada"


def test_transformation_flow_to_hdlf_is_an_empirical_adaptation_with_the_three_validation_levels_visible():
    out = run([col("A", "DecimalFloat")], TF_LTF)
    h = hit(out, "ESC-TF-LTF", "SAPC-TFLTF-001")
    assert (h["estado"], h["estado_validacion"]) == ("adaptacion_requerida", "danwor_empirico")
    assert {e["estado_validacion"] for e in h["evidencias"]} == {"danwor_empirico", "pendiente_verificacion"}


def test_decimalfloat_is_not_declared_incompatible_with_every_destination():
    out = run([col("A", "DecimalFloat")])                                                          # sin declarar: todos los escenarios
    states = {s["id"]: s["estado"] for s in out["escenarios"]}
    assert states["ESC-RF-HANA"] == "compatible"                                                   # destino HANA: tipo nativo, sin restricción
    assert states["ESC-RF-LTF"] == "conversion_documentada"
    assert states["ESC-DP-DELTA"] == "condicionada" and "incompatible_explicita" not in states.values()
    h = hit(out, "ESC-DP-DELTA", "SAPC-LTF-001")
    assert h["estado_validacion"] == "pendiente_verificacion"                                      # ausencia en la matriz ≠ prohibición


def test_rf_to_hana_local_table_keeps_datasphere_types_as_compatible():
    out = run(KENSING_LIKE, RF_HANA)
    assert out["estado_global"] == "compatible" and scen(out, "ESC-RF-HANA")["resultados"] == []


# ── Data Product: tipos fuera del conjunto admitido ─────────────────────────────────────────────────────────────────
def test_data_product_with_types_outside_the_admitted_set_is_explicitly_incompatible_only_where_sap_says_so():
    out = run([col("B", "hana.BINARY", "8"), col("R", "hana.REAL"), col("D", "DecimalFloat"), col("N", "Decimal", "15,2"), col("Bi", "Binary", "16"),
               col("U", "UUID"), col("S", "String", "50")], DP)
    h = hit(out, "ESC-DP-DELTA", "SAPC-DP-001")
    assert (h["estado"], h["estado_validacion"], h["columnas"]) == ("incompatible_explicita", "sap_documentado", 2) and "SAP-DP-BDC" in h["fuentes"]
    assert hit(out, "ESC-DP-DELTA", "SAPC-LTF-001")["estado"] == "condicionada"                    # DecimalFloat: no listado → condicionado, no prohibido
    assert out["estado_global"] == "incompatible_explicita"
    hit_types = {t for r in scen(out, "ESC-DP-DELTA")["resultados"] for t in r["tipos"]}
    assert hit_types == {"hana.BINARY", "hana.REAL", "DecimalFloat"}                                # Decimal, Binary, UUID y String son compatibles


def test_data_product_conditions_cite_the_sap_data_product_page():
    s = scen(run([col("A", "String", "5")], DP), "ESC-DP-DELTA")
    assert len(s["condiciones"]) >= 3 and all("SAP-DP-BDC" in c["fuentes"] for c in s["condiciones"])


def test_most_specific_constraint_wins_and_the_rest_are_reported():
    out = run([col("T", "Time"), col("G", "hana.ST_POINT")], RF_LTF)
    h = hit(out, "ESC-RF-LTF", "SAPC-RFLTF-002")
    assert h["estado"] == "incompatible_explicita" and h["columnas"] == 2 and "SAPC-LTF-001" in {t["id"] for t in h["tambien"]}
    out_dp = run([col("T", "Time")], DP)
    assert hit(out_dp, "ESC-DP-DELTA", "SAPC-LTF-001")["estado"] == "condicionada"                 # sin mecanismo RF no hay «no se replica»


def test_documented_contradiction_is_conditioned_never_asserted():
    h = hit(run([col("X", "hana.TINYINT"), col("Y", "DateTime")], RF_LTF), "ESC-RF-LTF", "SAPC-RFLTF-003")
    assert h["estado"] == "condicionada" and h["estado_validacion"] == "pendiente_verificacion"


def test_timestamp_requires_utc_confirmation_as_a_documented_condition():
    h = hit(run([col("Created", "Timestamp")], RF_LTF), "ESC-RF-LTF", "SAPC-LTF-002")
    assert (h["estado"], h["estado_validacion"]) == ("condicionada", "sap_documentado")


# ── sin falsos positivos por naming ─────────────────────────────────────────────────────────────────────────────────
def test_names_never_trigger_the_axis_costcenter_strings_and_unit_columns_are_ignored():
    cols = [col("CostCenter", "String", "10"), col("AmountText", "String", "20"), col("PriceUnit", "String", "3"), col("CurrencyCode", "String", "5")]
    for declared in ((), (RF_LTF,), (TF_LTF,), (DP,)):
        out = run(cols, *declared)
        assert out["n_restricciones"] == 0 and all(s["resultados"] == [] for s in out["escenarios"]), declared


def test_a_decimalfloat_blanked_by_the_historic_semantics_heuristic_is_still_found_here():
    # `currency` en el label blanquea la semántica histórica (PB-001 no dispara); el eje nuevo se apoya solo en el tipo.
    cols = [{**col("AmountInCompanyCodeCurrency", "DecimalFloat"), "label": "Amount in company code currency"}]
    assert pb.evaluate_portability(cols, "dominio", pb.load_matrix("datasphere_core"))["n_riesgos"] == 0
    assert hit(run(cols, RF_LTF), "ESC-RF-LTF", "SAPC-RFLTF-001")["columnas"] == 1


# ── tipos desconocidos / sin tipo: no se asume compatibilidad ──────────────────────────────────────────────────────
def test_unknown_or_missing_types_are_not_evaluated_and_are_never_assumed_compatible():
    out = run([col("Z", "FancyType"), col("W", ""), col("S", "String", "5")], DP)
    assert out["inventario"]["no_reconocidas"] == 2
    assert {t["tipo"]: t["valido_datasphere"] for t in out["inventario"]["tipos"]} == {"FancyType": False, "—": False, "String": True}
    assert out["n_restricciones"] == 0


def test_inventory_reports_columns_distinct_fields_and_artifacts_per_type():
    cols = [col("A", "DecimalFloat", art="X"), col("A", "DecimalFloat", art="Y"), col("B", "DecimalFloat", art="Y")]
    row = run(cols)["inventario"]["tipos"][0]
    assert (row["tipo"], row["columnas"], row["campos"], row["artefactos"], row["valido_datasphere"]) == ("DecimalFloat", 3, 2, 2, True)


# ── contexto manual: se declara, se valida, no se infiere ──────────────────────────────────────────────────────────
def test_declared_context_accepts_a_dict_or_a_list_and_dedupes():
    assert cb.parse_declared(RF_LTF, MX) == (["ESC-RF-LTF"], [])
    assert cb.parse_declared([RF_LTF, RF_LTF, DP], MX) == (["ESC-RF-LTF", "ESC-DP-DELTA"], [])
    assert cb.parse_declared(None, MX) == ([], []) and cb.parse_declared([], MX) == ([], [])


def test_invalid_declarations_are_reported_and_the_context_stays_undeclared():
    ids, bad = cb.parse_declared([{"mecanismo": "replication_flow", "destino": "mars"}, {"mecanismo": "data_flow", "destino": "local_table_file"},
                                  {"mecanismo": "data_product_delta_share", "destino": "local_table_file"}, "nope"], MX)
    assert ids == [] and len(bad) == 4 and {b["motivo"] for b in bad} >= {"mecanismo o destino desconocido", "combinación sin escenario definido"}
    out = cb.evaluate_compatibility(KENSING_LIKE, ids, MX, bad)
    assert out["declarado"] is False and out["estado_global"] == cb.UNDECLARED and out["invalidos"] == bad


def test_declared_context_narrows_the_diagnosis_to_that_scenario_and_closes_only_what_is_open():
    out = run(KENSING_LIKE, RF_LTF)
    assert out["declarado"] and [s["id"] for s in out["escenarios"]] == ["ESC-RF-LTF"]
    assert not any("mecanismo" in q["texto"].lower() for q in out["preguntas"])                    # lo declarado deja de preguntarse
    assert out["estado_global"] == "condicionada"                                                  # Timestamp: UTC por confirmar
    assert [q["id"] for q in out["preguntas"]] == ["SAPC-LTF-002"]                                 # la conversión documentada no abre preguntas


# ── el eje no toca el score histórico ──────────────────────────────────────────────────────────────────────────────
def test_historic_portability_result_is_identical_with_and_without_the_axis():
    cols = [{**c, "label": c["campo"]} for c in KENSING_LIKE]
    before = pb.evaluate_portability(cols, "dominio", pb.load_matrix("datasphere_core"))
    run(cols, RF_LTF); run(cols)
    assert pb.evaluate_portability(cols, "dominio", pb.load_matrix("datasphere_core")) == before
    assert before["findings"] and {f["regla"] for f in before["findings"]} == {"PB-003"}           # los falsos positivos históricos por naming siguen igual (scoring v2)


def test_portability_module_does_not_depend_on_the_new_axis():
    src = (REPO / "tools/catalog/portability_bdc.py").read_text(encoding="utf-8")
    assert "compat_bdc" not in src and 'get("compatibilidad")' not in src and "def load_compatibility" not in src


KENSING_VISTAS = Path(r"C:\Users\Joe\ArquitectoDanwor\catalog_local\Kensing\Raw_Data_of_G_L_Account_Line_Item\vistas")


@pytest.mark.skipif(not KENSING_VISTAS.exists(), reason="datos restaurados de Kensing no disponibles")
def test_kensing_raw_gl_historic_result_stays_80_and_97_while_the_axis_finds_decimalfloat():
    import yaml
    cols = []
    for p in sorted(KENSING_VISTAS.glob("*.md")):
        m = re.match(r"---\r?\n(.*?)\r?\n---", p.read_text(encoding="utf-8"), re.S)
        a = (yaml.safe_load(m.group(1)) or {}).get("auto") or {}
        cols += [{**f, "artefacto": a.get("nombre")} for f in a.get("campos") or []]
    hist = pb.evaluate_portability(cols, "dominio", pb.load_matrix("datasphere_core"))
    assert (hist["score"], hist["confianza"], hist["n_riesgos"], hist["n_adaptaciones"], hist["campos_revisados"]) == (80, "alta", 97, 0, 2368)
    out = run(cols)
    inv = {t["tipo"]: t for t in out["inventario"]["tipos"]}
    assert (inv["DecimalFloat"]["columnas"], inv["DecimalFloat"]["campos"]) == (120, 24) and out["inventario"]["no_reconocidas"] == 0
    assert hit(out, "ESC-RF-LTF", "SAPC-RFLTF-001")["columnas"] == 120 and out["estado_global"] == cb.UNDECLARED


def test_the_existing_yaml_contract_is_unchanged_for_its_current_consumers():
    m = pb.load_matrix("datasphere_core")
    assert set(m) == {"config", "reglas"}                                                          # el cargador histórico no ve la sección nueva
    assert [r["id"] for r in m["reglas"]] == ["PB-001", "PB-002", "PB-003", "PB-004", "PB-005", "PB-010", "PB-011"]
    assert (m["config"]["score_base"], m["config"]["penalizacion_max_por_regla"], m["config"]["peso_global"]) == (100, 20, 0)
    pb001 = next(r for r in m["reglas"] if r["id"] == "PB-001")
    assert pb001["cuando"]["semantica"] == "monetario" and pb001["dominio"]["peso"] == 22        # PB-001 intacta
    assert "compatibilidad" not in str(pb._SEED_REGLAS) and "compatibilidad" not in pb._SEED_CONFIG


def test_the_axis_does_not_import_judgment_evidence_confidence_or_business_classification():
    for rel in ("tools/catalog/compat_bdc.py", "webapp/services/bdc_compatibility_service.py"):
        imports = " ".join(re.findall(r"^(?:from|import)\s+([\w.]+)(?:\s+import\s+([\w, ]+))?", (REPO / rel).read_text(encoding="utf-8"), re.M).__repr__().split())
        for forbidden in ("methodological_judgment", "canonical_evidence", "confidence", "domain_classification", "assessment_service", "modeling_assessment"):
            assert forbidden not in imports, (rel, forbidden)
