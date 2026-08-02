"""Portabilidad BDC — evaluador cross-runtime (HANA → HDLF/Object Store → Databricks).

Cubre los 9 escenarios (A-I) del diseño + los 2 incidentes reales de Danwor como
fixtures. Herméticos: columnas sintéticas + la matriz (seeds de respaldo), sin depender
de catalog_local.
"""
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent / "tools" / "catalog"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import portability_bdc as pb  # noqa: E402

MATRIX = pb.load_matrix(None)  # seeds de respaldo (independiente del pack)


def _col(campo, tipo="", longitud="", *, artefacto="ART", label="", native="",
         key=False, medida=False, assoc="", assoc_col="", derived=False, cast=""):
    return {"artefacto": artefacto, "campo": campo, "label": label, "tipo": tipo,
            "longitud": longitud, "native": native, "key": key, "medida": medida,
            "assoc": assoc, "assoc_col": assoc_col, "derived": derived, "cast": cast}


def _by_rule(res):
    return {f["regla"] for f in res["findings"]}


# ── CASO A — DOMINIO con DecimalFloat monetario (incidente 1, preventivo) ──────────
def test_caso_a_dominio_decimalfloat_monetario():
    cols = [_col("NetAmount", "DecimalFloat", native="DECFLOAT", label="Net value")]
    res = pb.evaluate_portability(cols, "dominio", MATRIX)
    f = next(f for f in res["findings"] if f["regla"] == "PB-001")
    assert f["tipo_hallazgo"] == "riesgo" and f["severidad"] == "alto"
    assert "DECIMAL(38,6)" in f["recomendacion"]
    assert "contractual" in f["capa_correccion"].lower()
    assert res["n_riesgos"] >= 1 and res["score"] < 90


# ── CASO B — DOMINIO con DECIMAL(37,19) derivado (incidente 2, preventivo) ─────────
def test_caso_b_dominio_decimal_37_19_derivado():
    cols = [_col("ConfirmationCoveragePercent", "Decimal", "37,19",
                 label="Confirmation coverage", derived=True, cast="")]
    res = pb.evaluate_portability(cols, "dominio", MATRIX)
    reglas = _by_rule(res)
    assert "PB-002" in reglas   # escala inferida alta
    f2 = next(f for f in res["findings"] if f["regla"] == "PB-002")
    assert f2["tipo_hallazgo"] == "riesgo"
    # recomienda cast en el ORIGEN, no adaptación tardía
    assert "nace" in f2["recomendacion"].lower() or "origina" in f2["capa_correccion"].lower()
    assert res["score"] < 85


# ── CASO C — DATA PRODUCT con DECIMAL(38,6) monetario (normalización, no penaliza) ─
def test_caso_c_data_product_decimal_38_6_no_penaliza():
    cols = [_col("NetAmount", "Decimal", "38,6", label="Net amount")]
    res = pb.evaluate_portability(cols, "data_product", MATRIX)
    assert res["n_riesgos"] == 0                     # NO penaliza
    f = next(f for f in res["findings"] if f["regla"] == "PB-010")
    assert f["tipo_hallazgo"] == "adaptacion" and f["afecta_scoring"] is False
    assert f["confianza"] == "media"
    assert res["score"] >= 90 and res["clasificacion"] in ("plena", "buena")


# ── CASO D — DATA PRODUCT con DECIMAL(15,6) en porcentaje (normalización) ──────────
def test_caso_d_data_product_decimal_15_6_porcentaje():
    cols = [_col("ConfirmationCoveragePercent", "Decimal", "15,6", label="Coverage percent")]
    res = pb.evaluate_portability(cols, "data_product", MATRIX)
    assert res["n_riesgos"] == 0
    f = next(f for f in res["findings"] if f["regla"] == "PB-011")
    assert f["tipo_hallazgo"] == "adaptacion" and not f["afecta_scoring"]
    assert "documentar" in f["recomendacion"].lower()


# ── CASO E — numérico convertido a String (advertencia contextual, sin afirmar) ────
def test_caso_e_numerico_como_string():
    cols = [_col("OrderQuantity", "String", "10", label="Order quantity")]
    dom = pb.evaluate_portability(cols, "dominio", MATRIX)
    dp = pb.evaluate_portability(cols, "data_product", MATRIX)
    assert "PB-003" in _by_rule(dom) and "PB-003" in _by_rule(dp)
    fdom = next(f for f in dom["findings"] if f["regla"] == "PB-003")
    fdp = next(f for f in dp["findings"] if f["regla"] == "PB-003")
    assert fdom["tipo_hallazgo"] == "riesgo" and fdom["severidad"] == "nota"  # contextual, bajo impacto
    assert fdom["semaforo"] == "amber"                                        # revisión, no rojo
    assert fdp["tipo_hallazgo"] == "hint" and not fdp["afecta_scoring"]  # DP: posible workaround, no error


# ── CASO F — PK y FK con tipos diferentes (riesgo relacional, bloqueante) ──────────
def test_caso_f_pk_fk_tipos_distintos():
    cols = [
        _col("SoldToParty", "String", "10", artefacto="FACT",
             assoc="DIM_CUSTOMER", assoc_col="Customer"),
        _col("Customer", "String", "8", artefacto="DIM_CUSTOMER", key=True),
    ]
    res = pb.evaluate_portability(cols, "dominio", MATRIX)
    f = next(f for f in res["findings"] if f["regla"] == "PB-005")
    assert f["tipo_hallazgo"] == "riesgo" and f["severidad"] == "alto" and f["bloqueante"]
    assert res["score"] <= 49  # bloqueante impide buena/plena


def test_caso_f_pk_fk_iguales_no_dispara():
    cols = [
        _col("SoldToParty", "String", "10", artefacto="FACT",
             assoc="DIM_CUSTOMER", assoc_col="Customer"),
        _col("Customer", "String", "10", artefacto="DIM_CUSTOMER", key=True),
    ]
    res = pb.evaluate_portability(cols, "dominio", MATRIX)
    assert "PB-005" not in _by_rule(res)


# ── CASO G — metadata insuficiente (no evaluable, no falso positivo) ───────────────
def test_caso_g_metadata_insuficiente():
    cols = [_col("Foo", "", ""), _col("Bar", "—", "")]
    res = pb.evaluate_portability(cols, "dominio", MATRIX)
    assert res["campos_no_evaluables"] == 2 and res["campos_revisados"] == 0
    assert res["clasificacion"] == "no_evaluable" and res["confianza"] == "baja"
    assert res["n_riesgos"] == 0   # nunca marca compatible ni riesgo sin evidencia


# ── CASO H — dominio completamente portable (sin falsos positivos) ─────────────────
def test_caso_h_dominio_portable_sin_falsos_positivos():
    cols = [
        _col("SalesDocument", "String", "10", key=True),
        _col("OrderQuantity", "Decimal", "15,3", label="Order quantity"),
        _col("NetAmount", "Decimal", "38,6", label="Net amount"),   # monetario normalizado
    ]
    res = pb.evaluate_portability(cols, "dominio", MATRIX)
    assert res["n_riesgos"] == 0
    assert "PB-010" in _by_rule(res)   # reconoce la normalización explícita
    assert res["score"] >= 90 and res["clasificacion"] == "plena"


# ── CASO I — data product publicado sin riesgos ni workarounds (limpio) ────────────
def test_caso_i_data_product_limpio_sin_hints_artificiales():
    cols = [
        _col("SalesDocument", "String", "10", key=True),
        _col("OrderQuantity", "Decimal", "15,3", label="Order quantity"),
    ]
    res = pb.evaluate_portability(cols, "data_product", MATRIX)
    assert res["findings"] == []            # sin hints artificiales
    assert res["score"] >= 90


# ── Contrato de la matriz + config ────────────────────────────────────────────────
def test_matriz_configurable_y_reglas_desactivables():
    m = pb.load_matrix(None)
    ids = {r["id"] for r in m["reglas"]}
    assert {"PB-001", "PB-002", "PB-003", "PB-004", "PB-005"} <= ids
    assert m["config"]["peso_global"] == 0   # dimensión paralela por defecto (no altera históricos)


def test_regla_deshabilitada_no_dispara():
    m = pb.load_matrix(None)
    for r in m["reglas"]:
        if r["id"] == "PB-001":
            r = dict(r); r["habilitada"] = False
    m2 = {"config": m["config"],
          "reglas": [({**r, "habilitada": False} if r["id"] == "PB-001" else r) for r in m["reglas"]]}
    cols = [_col("NetAmount", "DecimalFloat", native="DECFLOAT")]
    res = pb.evaluate_portability(cols, "dominio", m2)
    assert "PB-001" not in _by_rule(res)
