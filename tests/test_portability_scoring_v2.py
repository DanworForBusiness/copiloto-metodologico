"""portability-scoring/v2 — consistencia estructural de tipos (independiente del destino).

Cubre: reglas v2 (PB-001 solo data product, PB-002/PB-002H, PB-003 con evidencia, PB-004 numérico, PB-005/PB-005B), confianza calibrada (cobertura + estabilidad frente a
heurísticas por nombre + evidencia estructural ejercida), KPI por campos distintos, paridad YAML↔seeds, firma del generador, independencia de Judgment/Evidence/Confidence,
validación de PB-003 contra dos muestras etiquetadas (calibración e independiente) y regresión sobre el corpus restaurado (si está disponible)."""
from __future__ import annotations

import ast
import csv
import hashlib
import os
import re
import shutil
import socket
from pathlib import Path

import pytest
import yaml

from tools.catalog import portability_bdc as pb

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures" / "portability_v2"
MX = pb.load_matrix("datasphere_core")
SEED = pb.load_matrix(None)


def col(campo, tipo, longitud="", *, label="", artefacto="A", key=False, derived=False, cast="", assoc="", assoc_col="", medida=False):
    return {"artefacto": artefacto, "campo": campo, "label": label or campo, "tipo": tipo, "longitud": longitud, "native": "", "key": key, "medida": medida,
            "assoc": assoc, "assoc_col": assoc_col, "derived": derived, "cast": cast}


def run(cols, ctx="dominio", mx=MX):
    return pb.evaluate_portability(cols, ctx, mx)


def rules(res):
    return {f["regla"] for f in res["findings"]}


# ── contrato versionado ───────────────────────────────────────────────────────────────────────────────────────────────
def test_result_is_versioned_and_exposes_evidence_distinct_fields_and_internal_class():
    r = run([col("A", "String", "10")])
    assert r["version"] == "portability-scoring/v2" == MX["config"]["version_scoring"]
    assert set(r["evidencia"]) == {"derivadas_numericas", "relaciones_fk_resueltas", "ejercida"} and set(r["campos_distintos"]) == {"riesgos", "indicios", "normalizaciones"}
    assert r["clase_evidencia"] in {"ejercida_sin_hallazgos", "ejercida_con_indicios", "no_ejercida", "con_penalizacion"}
    assert {"cobertura", "sensible_a_heuristicas", "evidencia_ejercida"} <= set(r["confianza_detalle"])


def test_yaml_rules_and_config_are_mirrored_by_the_seed_fallback():
    yaml_rules = {r["id"]: r for r in MX["reglas"]}
    seed_rules = {r["id"]: r for r in SEED["reglas"]}
    assert set(yaml_rules) == set(seed_rules)
    for rid, y in yaml_rules.items():
        for k in ("cuando", "dominio", "data_product", "depende_de_nombre"):
            assert y.get(k) == seed_rules[rid].get(k), (rid, k)
    for k in ("semantica_texto", "no_magnitud", "no_magnitud_numerico", "escala_legitima", "version_scoring"):
        assert MX["config"][k] == SEED["config"][k], k


def test_name_dependent_rules_are_declared_in_the_matrix_not_hardcoded():
    assert {r["id"] for r in MX["reglas"] if r.get("depende_de_nombre")} == {"PB-001", "PB-002H", "PB-003", "PB-004", "PB-010", "PB-011"}


# ── PB-002 / PB-002H ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_pb002_needs_evidence_of_inference_and_pb002h_preserves_declared_high_scales_without_penalty():
    inferred = run([col("CoveragePercent", "Decimal", "37,19", derived=True)])
    assert "PB-002" in rules(inferred) and inferred["score"] < 100
    declared = run([col("Precio_Lista", "Decimal", "38,14", label="Precio de lista")])
    assert rules(declared) == {"PB-002H"} and declared["score"] == 100 and declared["n_riesgos"] == 0
    f = declared["findings"][0]
    assert f["tipo_hallazgo"] == "hint" and f["peso"] == 0 and "No es una incompatibilidad confirmada" in f["recomendacion"]


def test_pb002h_does_not_fire_for_legitimate_high_scales_or_numerics_without_amount_semantics():
    for c in (col("0LONGITUDE", "Decimal", "15,12"), col("0LATITUDE", "Decimal", "15,12"), col("MarketDataAbsoluteExchangeRate", "Decimal", "29,14"),
              col("LEAD_TIME", "Decimal", "31,14"), col("DEC_VALUE_FROM", "Decimal", "31,14"), col("Factor", "Decimal", "30,12", label="Conversion factor")):
        assert "PB-002H" not in rules(run([c])), c["campo"]


# ── PB-003: evidencia, no solo nombre ─────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("campo,label,longitud", [
    ("CostCenter", "Cost center", "10"), ("CostCenterName", "Cost center name", "20"), ("GEWEI", "Unidad de peso", "3"), ("VOLEH", "Unidad de volumen", "3"),
    ("VALDT", "Fecha de valor", "8"), ("PricingDate", "Date for pricing and exchange rate", "8"), ("SPROF", "Perfil de precio", "1"), ("PMATA", "Material precio", "40"),
    ("QuantityIsFixed", "Quantity is fixed", "1"), ("FMENG", "Cantidad fija", "1"), ("KONDA", "Grupo de precio de cliente", "2"), ("TIPO_VALOR", "Tipo Valor", "4"),
    ("ImporteTotal", "Importe total", "20"), ("NetPriceAmount", "Net price amount", "20")])
def test_pb003_negatives_identifiers_units_dates_indicators_entities_and_amounts_as_text(campo, label, longitud):
    r = run([col(campo, "String", longitud, label=label)])
    assert "PB-003" not in rules(r) and r["score"] == 100


@pytest.mark.parametrize("campo,label,longitud", [
    ("FIBER_PART1", "Porcentaje de fibra (componente 1)", "3"), ("ANZPK", "Cantidad total de bultos de entrega", "5"),
    ("HdrOrderProbabilityInPercent", "Sales probability", "3"), ("FSH_FRATE", "% de tasa de cumplimiento", "3")])
def test_pb003_positives_are_informative_hints_that_never_penalize(campo, label, longitud):
    for ctx in ("dominio", "data_product"):
        r = run([col(campo, "String", longitud, label=label)], ctx)
        f = next(f for f in r["findings"] if f["regla"] == "PB-003")
        assert f["tipo_hallazgo"] == "hint" and f["peso"] == 0 and not f["afecta_scoring"] and r["score"] == 100 and "Indicio por nombre" in f["recomendacion"]


def test_text_never_gets_a_monetary_semantic_by_name_while_numeric_amounts_do():
    cfg = MX["config"]
    assert pb._semantics(col("ImporteTotal", "String", "20", label="Importe total"), cfg) == ""
    assert pb._semantics(col("NetPriceAmount", "String", "20", label="Net price amount"), cfg) == ""
    assert pb._semantics(col("ImporteTotal", "Decimal", "15,2", label="Importe total"), cfg) == "monetario"
    assert pb._semantics(col("FIBER_PART1", "String", "3", label="Porcentaje de fibra"), cfg) == "porcentaje_ratio"


def test_currency_in_the_label_no_longer_blanks_numeric_amounts():
    r = run([col("AmountInTransactionCurrency", "Decimal", "38,6", label="Amount in transaction currency")])
    assert "PB-010" in rules(r)                                                                 # reconocido como importe normalizado (antes `currency` lo blanqueaba)


# ── PB-004 / PB-005 / PB-005B ─────────────────────────────────────────────────────────────────────────────────────────
def test_pb004_applies_to_numeric_derived_fields_only():
    assert "PB-004" in rules(run([col("Importe_USD", "Decimal", "34,2", derived=True)]))
    assert "PB-004" not in rules(run([col("GDATU", "String", "8", label="Date As of Which the Exchange Rate Is Effective", derived=True)]))
    assert "PB-004" not in rules(run([col("Importe_USD", "Decimal", "34,2", derived=True, cast="Decimal(34,2)")]))


def _fk(fk_type, fk_len, pk_type="String", pk_len="10"):
    return [col("Fk", fk_type, fk_len, artefacto="F", assoc="DIM", assoc_col="Pk"), col("Pk", pk_type, pk_len, artefacto="DIM", key=True)]


def test_pb005b_flags_only_fk_wider_than_pk_and_never_asserts_truncation():
    wide = run(_fk("String", "50"))
    f = next(f for f in wide["findings"] if f["regla"] == "PB-005B")
    assert f["severidad"] == "medio" and not f["bloqueante"] and "no se puede afirmar truncamiento ni pérdida" in f["recomendacion"] and wide["score"] > 49
    assert not re.search(r"trunca(rá|do|n)|se pierde|pérdida de datos", f["recomendacion"] + f["titulo"], re.I)               # solo metadata: sin afirmaciones de pérdida real
    assert "potencialmente" in f["titulo"]
    assert rules(run(_fk("String", "6"))) == set()                                                                           # ampliación compatible: la FK cabe en la PK
    assert rules(run(_fk("String", "10"))) == set()
    assert "PB-005B" in rules(run(_fk("Decimal", "20,4", "Decimal", "15,2")))                                                 # más escala y más enteros
    assert rules(run(_fk("Decimal", "10,2", "Decimal", "15,2"))) == set()


def test_pb005_base_type_mismatch_still_blocks():
    r = run(_fk("Integer64", "", "String", "3"))
    f = next(f for f in r["findings"] if f["regla"] == "PB-005")
    assert f["bloqueante"] and r["score"] <= 49 and "PB-005B" not in rules(r)


# ── confianza calibrada ───────────────────────────────────────────────────────────────────────────────────────────────
def test_confidence_high_requires_exercised_structural_evidence_not_just_absence_of_risks():
    nothing = run([col(f"C{i}", "String", "10") for i in range(5)])
    assert nothing["score"] == 100 and nothing["clase_evidencia"] == "no_ejercida" and nothing["confianza"] == "media"       # 100 sin nada que evaluar ⇒ NO «alta»
    assert nothing["confianza_detalle"]["evidencia_ejercida"] is False
    checked = run([col(f"C{i}", "String", "10") for i in range(5)] + _fk("String", "10"))
    assert checked["clase_evidencia"] == "ejercida_sin_hallazgos" and checked["confianza"] == "alta" and checked["evidencia"]["relaciones_fk_resueltas"] == 1


def test_confidence_drops_with_low_coverage_and_when_the_band_depends_on_name_heuristics():
    low = run([col("A", "String", "10"), col("B", "", ""), col("C", "", "")])
    assert low["confianza"] == "baja"
    mid = run([col(f"C{i}", "String", "10") for i in range(8)] + [col("X", "", "")] * 1)
    assert mid["confianza"] == "media" and mid["confianza_detalle"]["cobertura"] < 0.9
    sensitive = run([col(f"Importe_{i}", "Decimal", "15,2", derived=True) for i in range(8)] + [col("K", "String", "10", key=True)])
    assert rules(sensitive) == {"PB-004"} and sensitive["score"] == 80 and sensitive["clasificacion"] == "buena"
    assert sensitive["confianza_detalle"]["sensible_a_heuristicas"] is True and sensitive["confianza"] == "media"          # sin PB-004 (por nombre) la banda sería «plena»


def test_distinct_fields_count_fields_not_occurrences():
    cols = [col("Fk", "String", "50", artefacto=a, assoc="DIM", assoc_col="Pk") for a in ("A1", "A2", "A3")] + [col("Pk", "String", "10", artefacto="DIM", key=True)]
    r = run(cols)
    assert r["n_riesgos"] == 0 or r["campos_distintos"]["riesgos"] <= r["n_riesgos"]
    wide = [col("Fk", "String", "50", artefacto=a, assoc=a, assoc_col="Pk") for a in ("A1", "A2", "A3")] + [col("Pk", "String", "10", artefacto=a, key=True) for a in ("A1", "A2", "A3")]
    w = run(wide)
    assert w["n_riesgos"] == 3 and w["campos_distintos"]["riesgos"] == 1


# ── independencia ─────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_scoring_engine_is_independent_of_judgment_evidence_confidence_business_classification_and_compatibility():
    tree = ast.parse((REPO / "tools/catalog/portability_bdc.py").read_text(encoding="utf-8"))
    imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    for forbidden in ("methodological_judgment", "canonical_evidence", "confidence", "modeling_assessment", "domain_classification", "assessment_service", "compat_bdc"):
        assert not any(forbidden in m for m in imported), forbidden


# ── firma del generador: un cambio de scoring invalida el cache incremental ───────────────────────────────────────────
def test_generator_signature_covers_the_scoring_engine_and_matrix(monkeypatch):
    from tools.catalog import generate_catalog as gen
    base = gen._catalog_generator_signature()
    real = gen._catalog_sha256
    for target in ("portability_bdc.py", "portability_bdc.yaml"):
        monkeypatch.setattr(gen, "_catalog_sha256", lambda p, t=target: "cambiado" if Path(p).name == t else real(p))
        assert gen._catalog_generator_signature() != base, target
    monkeypatch.setattr(gen, "_catalog_sha256", real)
    assert gen._catalog_generator_signature() == base


# ── PB-003 validado contra muestras etiquetadas ───────────────────────────────────────────────────────────────────────
def _flagged(row) -> bool:
    return "PB-003" in rules(run([col(row["campo"], "String", row["longitud"], label=row["label"])]))


def _load(name):
    with open(FIX / name, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def test_the_two_pb003_samples_are_disjoint_calibration_vs_independent():
    cal, ind = _load("pb003_calibration.csv"), _load("pb003_independent.csv")
    assert len(cal) == 362 and len(ind) == 195
    assert not ({(r["campo"], r["label"], r["longitud"]) for r in cal} & {(r["campo"], r["label"], r["longitud"]) for r in ind})
    assert not ({r["campo"].upper() for r in cal} & {r["campo"].upper() for r in ind})                                       # ni siquiera por nombre de campo


def test_pb003_on_the_calibration_sample_is_precise_and_reports_its_false_negatives():
    rows = [r for r in _load("pb003_calibration.csv") if r["etiqueta"] != "A"]
    pred = [r for r in rows if _flagged(r)]
    tp = [r for r in pred if r["etiqueta"] == "P"]
    scope = [r for r in rows if r["alcance_positivo"] == "categoria_regla"]
    assert len(pred) >= 15 and len(tp) / len(pred) >= 0.9                                                                    # precisión en la muestra de calibración (ajustada en muestra)
    assert sum(1 for r in scope if _flagged(r)) / len(scope) >= 0.8                                                           # recall dentro de las categorías de la regla
    fn_out = {r["campo"] for r in rows if r["alcance_positivo"] == "duracion_fuera_de_categoria" and not _flagged(r)}
    assert {"BETOL", "VALTG", "LET01"} <= fn_out                                                                              # falsos negativos documentados: duraciones en días
    assert {"/VSO/R_I_NO_LYR", "TotalNumberOfPackage"} <= {r["campo"] for r in scope if not _flagged(r)}                      # falsos negativos documentados dentro de categoría


def test_pb003_on_the_independent_sample_produces_no_false_positives_and_reports_what_it_misses():
    rows = _load("pb003_independent.csv")
    negatives = [r for r in rows if r["etiqueta"] == "N"]
    assert len(negatives) == 191 and not [r["campo"] for r in negatives if _flagged(r)]                                      # 0 falsos positivos en 191 negativos independientes
    positives = [r for r in rows if r["etiqueta"] == "P"]
    assert {r["campo"] for r in positives} == {"LeaseDurationInFiscalPeriods", "LeaseDurationInFiscalYears"}                 # los únicos positivos: duraciones (fuera de categoría)
    assert not any(_flagged(r) for r in positives)                                                                            # FN conocidos; sin positivos en categoría: recall NO estimable
    assert all(r["alcance_positivo"] == "duracion_fuera_de_categoria" for r in positives)


# ── corpus restaurado (si está disponible) ───────────────────────────────────────────────────────────────────────────
CATALOG = Path(os.environ.get("DANWOR_TEST_CATALOG", r"C:\Users\Joe\ArquitectoDanwor\catalog_local"))
_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.S)


def _fm(p: Path) -> dict:
    m = _FM.match(p.read_text(encoding="utf-8", errors="ignore"))
    return (yaml.safe_load(m.group(1)) or {}) if m else {}


FLOWS = {
    'Biogenesis/AM_Lista_Precios': (100, 'media', 'no_ejercida', 0, 0, 0),
    'Biogenesis/AM_Ordenes_Transporte': (100, 'media', 'no_ejercida', 0, 0, 0),
    'Biogenesis/AM_RESERVA_MP': (100, 'alta', 'ejercida_con_indicios', 0, 0, 4),
    'Biogenesis/An_lisis_de_Vencimiento': (100, 'alta', 'ejercida_con_indicios', 0, 0, 6),
    'Biogenesis/Despachos': (94, 'alta', 'con_penalizacion', 2, 1, 6),
    'Biogenesis/Desv_o_en_ingresos_de_materiales': (100, 'alta', 'ejercida_con_indicios', 0, 0, 4),
    'Biogenesis/Exportaciones': (94, 'alta', 'con_penalizacion', 2, 2, 4),
    'Biogenesis/Modelo_Anal_tico_SellOut_GP': (74, 'media', 'con_penalizacion', 3, 2, 6),
    'Biogenesis/Modelo_Analitico_Facturas': (100, 'media', 'no_ejercida', 0, 0, 6),
    'Biogenesis/Modelo_Logistico_Bultos_Despachados': (100, 'alta', 'ejercida_con_indicios', 0, 0, 6),
    'Biogenesis/Otras_Salidas_de_Bienes_de_Cambio': (92, 'alta', 'con_penalizacion', 3, 3, 5),
    'Biogenesis/Reserva_de_Material': (100, 'alta', 'ejercida_sin_hallazgos', 0, 0, 0),
    'Biogenesis/Sell_Out': (55, 'alta', 'con_penalizacion', 12, 8, 7),
    'Biogenesis/Sell_Out_Hojobar': (94, 'alta', 'con_penalizacion', 2, 1, 5),
    'Biogenesis/Sell_out_Hojobar_NC': (100, 'alta', 'ejercida_con_indicios', 0, 0, 5),
    'Biogenesis/Stock_Inventario': (94, 'alta', 'con_penalizacion', 2, 2, 4),
    'Biogenesis/Stock_Inventario_AM_OCUPACION_ALMACENES': (100, 'alta', 'ejercida_con_indicios', 0, 0, 4),
    'Biogenesis/Stock_Inventario_AM_OCUPACION_ALMACENES_TERCERO': (100, 'alta', 'ejercida_con_indicios', 0, 0, 4),
    'Biogenesis/Ventas': (100, 'media', 'no_ejercida', 0, 0, 4),
    'Biogenesis/Ventas_AM_BASE_VENTAS': (86, 'alta', 'con_penalizacion', 5, 3, 7),
    'Biogenesis/Ventas_AM_BASE_VENTAS_140325': (97, 'alta', 'con_penalizacion', 1, 1, 6),
    'Biogenesis/Ventas_AM_DescuentosComerciales': (100, 'alta', 'ejercida_con_indicios', 0, 0, 4),
    'Biogenesis/Ventas_AM_PREFACTURACION': (86, 'alta', 'con_penalizacion', 5, 4, 7),
    'Biogenesis/Ventas_AM_TEST_VENTA_2': (86, 'alta', 'con_penalizacion', 5, 3, 7),
    'Biogenesis/Vista_anal_tica_consolidada_Gastos': (100, 'alta', 'ejercida_con_indicios', 0, 0, 1),
    'Danone/Stock_Inventario': (100, 'alta', 'ejercida_sin_hallazgos', 0, 0, 0),
    'Illolay/General': (100, 'alta', 'ejercida_con_indicios', 0, 0, 2),
    'Kensing/Billing_Document_Item_Basic': (100, 'alta', 'ejercida_sin_hallazgos', 0, 0, 0),
    'Kensing/Delivery_Document': (100, 'media', 'no_ejercida', 0, 0, 0),
    'Kensing/Delivery_Document_Item': (100, 'alta', 'ejercida_sin_hallazgos', 0, 0, 0),
    'Kensing/Raw_Data_of_G_L_Account_Line_Item': (100, 'alta', 'ejercida_sin_hallazgos', 0, 0, 0),
    'Kensing/Ventas': (100, 'alta', 'ejercida_sin_hallazgos', 0, 0, 0),
    'Kensing/Ventas_4AM_SND_SALESDOCUMENTITEMDEX': (100, 'alta', 'ejercida_con_indicios', 0, 0, 2),
    'Loma/Stock_Inventario': (49, 'alta', 'con_penalizacion', 4, 4, 5),
    'Pampa/Compras': (100, 'media', 'no_ejercida', 0, 0, 0),
}

DPS = {'DP_SND_OpenSalesOrderFulfilment': (100, 'alta', 'ejercida_con_indicios', 1), 'DP_SND_SalesDocumentItemCore': (100, 'media', 'no_ejercida', 4)}


@pytest.mark.skipif(not (CATALOG / "Kensing").exists(), reason="corpus restaurado no disponible")
def test_the_35_flows_match_the_calibrated_v2_results():
    got = {}
    for flujo in sorted(CATALOG.glob("*/*/flujo_*.md")):
        a = _fm(flujo).get("auto") or {}
        if a.get("tipo") != "flujo":
            continue
        cols = []
        for n in a.get("artefactos") or []:
            vp = flujo.parent / "vistas" / f"{n}.md"
            if vp.exists():
                cols += [{**f, "artefacto": n} for f in (_fm(vp).get("auto") or {}).get("campos") or []]
        r = run(cols)
        got[f"{flujo.relative_to(CATALOG).parts[0]}/{flujo.parent.name}"] = (r["score"], r["confianza"], r["clase_evidencia"], r["n_riesgos"], r["campos_distintos"]["riesgos"], r["campos_distintos"]["indicios"])
    assert got == FLOWS
    assert sum(1 for v in got.values() if v[0] >= 90) == 29 and sum(1 for v in got.values() if v[2] == "no_ejercida") == 6


@pytest.mark.skipif(not (CATALOG / "data_products").exists(), reason="corpus restaurado no disponible")
def test_the_two_data_products_are_validated_with_the_v2_rules():
    got = {}
    for p in sorted((CATALOG / "data_products").glob("*.md")):
        a = _fm(p).get("auto") or {}
        nodes = a.get("lineage_nodes") or []
        pub = [n for n in nodes if n.get("published") or n.get("runtime") == "object_store"]
        cols = [{**f, "artefacto": n.get("technical_name") or n.get("display") or ""} for n in (pub or nodes) for f in n.get("fields") or []]
        r = run(cols, "data_product")
        got[p.stem] = (r["score"], r["confianza"], r["clase_evidencia"], r["n_adaptaciones"])
    assert got == DPS


@pytest.mark.skipif(not (CATALOG / ".csn_cache" / "Danone").exists(), reason="corpus restaurado no disponible")
def test_the_full_generator_runs_on_an_isolated_copy_writes_v2_and_never_touches_the_original(tmp_path, monkeypatch):
    """Generador COMPLETO sobre una copia temporal: persiste `portability-scoring/v2`, no altera el corpus original y no abre conexiones (ni Spaces)."""
    from tools.catalog import generate_catalog as gen

    def tree_hash(root: Path) -> str:
        h = hashlib.sha256()
        for p in sorted(root.rglob("*")):
            if p.is_file():
                h.update(p.relative_to(root).as_posix().encode() + p.read_bytes())
        return h.hexdigest()

    for k in [k for k in os.environ if k.startswith(("SPACES_", "DANWOR_SPACES"))]:
        monkeypatch.delenv(k)
    attempts = []
    monkeypatch.setattr(socket.socket, "connect", lambda self, addr: attempts.append(addr) or (_ for _ in ()).throw(OSError("sin red")))
    inbound, out = tmp_path / "inbound", tmp_path / "out"
    shutil.copytree(CATALOG / ".csn_cache" / "Danone", inbound / "Danone")
    before = tree_hash(CATALOG / "Danone") if (CATALOG / "Danone").exists() else ""
    gen.generate_all(inbound, out)
    flows = list(out.glob("*/*/flujo_*.md"))
    assert flows and all((_fm(f).get("auto") or {}).get("portabilidad_bdc", {}).get("version") == "portability-scoring/v2" for f in flows)
    assert not attempts
    assert (tree_hash(CATALOG / "Danone") if (CATALOG / "Danone").exists() else "") == before
