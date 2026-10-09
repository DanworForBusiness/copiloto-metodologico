"""
test_business_classification.py — Clasificación de NEGOCIO v2: línea de negocio (taxonomía GOBERNADA) + dominio, desde el linaje canónico.

Fija: reuso del vocabulario gobernado (prefijos LoB de la convención de nombres), raíces reales/calibración SIN excepciones por cliente, abstención y conflictos (jamás
desempate arbitrario), prior contextual que no fuerza, precedencia manual > HIGH > sin clasificar (MEDIUM no mueve), invariantes de ortogonalidad con el Juicio/perfil,
persistencia hermana versionada, backfill idempotente con conteos de negocio y presentación en Assessment [04].
Las raíces «reales» son copias literales de la estructura del Catálogo local (Biogenesis / Danone / Loma Negra) o de sus nombres de cierre; el motor no conoce ningún cliente.
"""
from __future__ import annotations

import dataclasses
import json
import re
import socket
from pathlib import Path

import pytest
import yaml

import webapp.main as main
from tests.webapp.test_governed_assessment_durability import files, restart, world  # noqa: F401  (fixture reutilizada)
from tools.catalog import business_classification as bc
from tools.catalog import catalog_service as cs
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import domain_classification_service as dcs

REPO = Path(__file__).resolve().parents[2]


def _run(cur, root, names, srcs=None, labels=None, domains=()):
    srcs, labels = srcs or {}, labels or {}
    return bc.classify_business(current_domain=cur, root={"name": root, "label": labels.get(root, "")}, client="C", client_domains=[cur, *domains],
                                closure=[{"name": n, "label": labels.get(n, ""), "source": srcs.get(n, "")} for n in names])


def _lob(r):
    return (r["line_of_business"] or {}).get("id")


# ── raíces reales (estructura literal del Catálogo local) ───────────────────────────────────────────────────────────────────────────────
BIOGENESIS_STOCK = dict(cur="Stock", root="AM_BASE_STOCKS", labels={"AM_BASE_STOCKS": "Modelo analítico - Base de Stock"},
                        names=["1TR_ZCDS_DIM_TVKWZ", "1TR_ZCDS_DSP_FACT_MCHB", "1TR_ZCDS_MARD", "VT_MATERIAL_TEXT", "ZCDS_DSP_DIM_MCH1", "ZCDS_DSP_FACT_CKMLCR", "ZCDS_DSP_FACT_MBEW",
                               "ZDSP_CDSMKVE", "2VR_BASE_STOCK", "VD_MVKE", "VD_Material", "VF_BASE_STOCK", "AM_BASE_STOCKS"],
                        srcs={"1TR_ZCDS_DSP_FACT_MCHB": "ABAPTABLES.NSDM_V_MCHB", "1TR_ZCDS_MARD": "ABAP_CDS.ZCDS_MARD$F", "ZCDS_DSP_DIM_MCH1": "ABAP_CDS.ZCDS_DSP_DIM_MCH1$E",
                              "ZCDS_DSP_FACT_MBEW": "ABAP_CDS.ZCDS_DSP_FACT_MBEW$F"})
DANONE_INV = dict(cur="Inventario", root="3VF_IVT_MOVIMIENTOS_INVENTARIO_KPI", labels={"Remote.BWBRIDGE.ZIM_V01": "Movimientos inventario (BW)"},
                  names=["Remote.BWBRIDGE.ZIM_V01", "2VF_IVT_MOVIMIENTOS_INVENTARIO", "3VF_IVT_MOVIMIENTOS_INVENTARIO_KPI", "3VF_IVT_MOV_INVENTARIO_RESUM_PBI"])
LOMA_STOCK = dict(cur="Stock", root="4MA_STOCK", names=["1TR_MBEW", "1TR_MBEW_S4", "2TF_MSEG", "2TR_MBEW", "2VS_MBEW_R3_S4", "2VS_MSEG", "3VF_STOCK", "3VS_STOCK", "4MA_STOCK"],
                  srcs={"1TR_MBEW": "ABAPTABLES.MBEW", "1TR_MBEW_S4": "ABAPTABLES.MBEW"})
# Nombres de cierre de otros clientes (Kensing / Illolay), conservados de la inspección del Catálogo completo.
KENSING_GL = dict(cur="Raw Data of G/L Account Line Item", root="4AM_FNC_GLACCOUNTLINEITRAWDATA",
                  labels={"4AM_FNC_GLACCOUNTLINEITRAWDATA": "Raw Data of G/L Account Line Item"},
                  names=["1TR_SAP_FNC_GLACCOUNTLINEITEMRAWDATA", "1TR_SAP_MD_CHARTOFACCOUNTS", "1TR_SAP_MD_COMPANYCODE", "1TR_SAP_MD_COSTCENTER", "2VD_MD_CHARTOFACCOUNTS", "2VD_MD_COMPANYCODE",
                         "2VR_FNC_GLACCOUNTLINEITEMRAWDATA", "3VF_FNC_GLACCOUNTLINEITRAWDATA", "4AM_FNC_GLACCOUNTLINEITRAWDATA"])
KENSING_DELIVERY = dict(cur="Delivery Document", root="4AM_SND_DELIVERYDOCUMENT", names=["1TR_SAP_SND_DELIVERYDOCUMENT", "2VR_SND_DELIVERYDOCUMENT", "3VF_SND_DELIVERYDOCUMENT", "4AM_SND_DELIVERYDOCUMENT"])
ILLOLAY_PEDIDOS = dict(cur="General", root="4MA_PEDIDOS_ENTREGAS",
                       names=["1TR_VBAK", "1TR_KNA1", "1TR_KNVV", "1TR_MARA", "1TR_MAKT", "2TR_LIKP", "2TR_LIPS", "2TR_VBAK", "2TR_VBAP", "2TR_VBRK", "2TR_VBRP", "2VS_KNA1",
                              "3VF_PEDIDOS_ENTREGAS", "V_Entregas", "V_Entregas_Final", "V_Pedidos", "4MA_PEDIDOS_ENTREGAS"],
                       labels={"1TR_VBAK": "Sales Document: Header Data"})


@pytest.mark.parametrize("case,lob", [(BIOGENESIS_STOCK, "SUPPLY_CHAIN"), (DANONE_INV, "SUPPLY_CHAIN"), (LOMA_STOCK, "SUPPLY_CHAIN"), (KENSING_GL, "FINANCE"),
                                      (KENSING_DELIVERY, "SALES"), (ILLOLAY_PEDIDOS, "SALES")])
def test_real_roots_classify_to_their_line_of_business_with_high_confidence_from_generic_evidence(case, lob):
    r = _run(case["cur"], case["root"], case["names"], case.get("srcs"), case.get("labels"))
    assert _lob(r) == lob and r["confidence"] == bc.HIGH and r["status"] == bc.CLASSIFIED
    assert r["reason_code"] in (bc.R_ROOT_AND_LINEAGE, bc.R_SOURCE_AND_LINEAGE, bc.R_ROOT_AND_SOURCE) and r["evidence"] and r["contract_version"] == bc.BUSINESS_CLASSIFICATION_VERSION
    assert len({e["family"] for e in r["evidence"]}) >= 2                                                # HIGH = ≥2 familias de evidencia independientes


def test_a_generic_current_domain_with_strong_evidence_gets_a_line_and_a_specific_domain_suggestion():
    r = _run(**{k: ILLOLAY_PEDIDOS[k] for k in ("cur", "root", "names", "labels")})
    assert r["status"] == bc.CLASSIFIED and r["domain"] == {"id": "pedidos-entregas", "label": "Pedidos / Entregas", "origin": "SUGGESTED"}
    assert r["alignment"] is None                                                                      # «General» no es prior: ni coincide ni discrepa


def test_the_existing_domain_is_a_prior_only_and_never_forces_the_line():
    r = _run("Ventas", KENSING_GL["root"], KENSING_GL["names"], labels=KENSING_GL["labels"])           # el cliente lo llama «Ventas», pero toda la evidencia canónica es de finanzas
    assert _lob(r) == "FINANCE" and r["alignment"] == "DISAGREES"
    g = _run("General", KENSING_GL["root"], KENSING_GL["names"])
    assert _lob(g) == "FINANCE" and g["confidence"] == bc.HIGH                                         # un dominio genérico no bloquea la evidencia fuerte


# ── abstención / conflicto / ruido ───────────────────────────────────────────────────────────────────────────────────────────────────────
def test_insufficient_generic_and_noisy_evidence_abstains():
    generic = _run("General", "AM_REPORTE_GERENCIAL", ["V_Base", "T_Data", "AM_REPORTE_GERENCIAL"])
    assert generic["status"] == bc.UNDETERMINED and _lob(generic) is None and generic["reason_code"] == bc.R_INSUFFICIENT and generic["confidence"] == bc.LOW
    empty = _run("", "X", ["X"])
    assert empty["status"] == bc.UNDETERMINED and empty["line_of_business"] is None


def test_a_single_noisy_token_is_never_enough_for_high():
    r = _run("General", "AM_STOCK", ["AM_STOCK", "V_Stock", "T_Stock"])                                # una sola palabra («stock») repetida: ruido, no corroboración
    assert _lob(r) == "SUPPLY_CHAIN" and r["confidence"] == bc.MEDIUM and r["status"] == bc.SUGGESTED and r["reason_code"] in (bc.R_SINGLE_SIGNAL, bc.R_SINGLE_FAMILY)   # solo la raíz: sin corroboración
    one = _run("General", "AM_X", ["AM_X", "1TR_MARD"], srcs={"1TR_MARD": "ABAPTABLES.MARD"})            # un solo objeto fuente: no alcanza ni para votar
    assert one["status"] == bc.UNDETERMINED


def test_cross_line_conflict_is_undetermined_and_never_resolved_arbitrarily():
    r = _run("General", "AM_VENTAS_GASTOS", ["V_Ventas", "T_Ventas", "V_Gasto", "T_Gasto", "AM_VENTAS_GASTOS"])
    assert r["status"] == bc.UNDETERMINED and r["reason_code"] == bc.R_CONFLICT and r["line_of_business"] is None
    swapped = _run("General", "AM_VENTAS_GASTOS", ["T_Gasto", "V_Gasto", "T_Ventas", "V_Ventas", "AM_VENTAS_GASTOS"])
    assert swapped["reason_code"] == bc.R_CONFLICT                                                     # el orden de entrada no desempata
    assert _run("G", "AM_VENTAS_GASTOS", ["V_Ventas", "T_Ventas", "V_Gasto", "T_Gasto", "AM_VENTAS_GASTOS"]) == _run("G", "AM_VENTAS_GASTOS", ["V_Ventas", "T_Ventas", "V_Gasto", "T_Gasto", "AM_VENTAS_GASTOS"])


def test_master_data_is_not_a_line_of_business_and_generates_no_signal():
    r = _run("General", "4AM_FNC_X", ["1TR_SAP_MD_A", "1TR_SAP_MD_B", "2VD_MD_A", "2VD_MD_B", "1TR_SAP_FNC_X", "2VR_FNC_X", "4AM_FNC_X"])
    assert _lob(r) == "FINANCE"                                                                         # los artefactos MD no votan ni compiten
    only_md = _run("General", "2VD_MD_CUSTOMERGROUP", ["1TR_SAP_MD_CUSTOMERGROUP", "2VD_MD_CUSTOMERGROUP"])
    assert only_md["line_of_business"] is None and only_md["status"] == bc.UNDETERMINED               # «datos maestros» no es una línea de negocio
    masters = _run("General", "2TR_KNA1", ["1TR_KNA1", "1TR_MARA", "1TR_LFA1", "2TR_KNA1"], srcs={"1TR_KNA1": "ABAPTABLES.KNA1", "1TR_MARA": "ABAPTABLES.MARA"})
    assert masters["line_of_business"] is None                                                          # objetos puramente maestros tampoco


# ── taxonomía gobernada ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_taxonomy_is_business_oriented_with_stable_neutral_ids_and_governed_prefixes_only_as_evidence():
    gov = yaml.safe_load((REPO / "content_packs/datasphere_core/trees/04_convencion_de_nombres.yaml").read_text(encoding="utf-8"))
    codes = None
    stack = [gov]
    while stack and codes is None:
        node = stack.pop()
        if isinstance(node, dict):
            if "lob_prefixes" in node and isinstance(node["lob_prefixes"], dict) and "FNC" in node["lob_prefixes"]:
                codes = node["lob_prefixes"]
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    assert codes and set(codes) >= {"MD", "FNC", "IVT", "PUR", "SND"}
    assert {c: bc.LOB_BY_CODE[c] for c in ("FNC", "SND", "IVT", "PUR", "MNF", "RRHH")} == {"FNC": "FINANCE", "SND": "SALES", "IVT": "SUPPLY_CHAIN", "PUR": "SUPPLY_CHAIN",
                                                                                          "MNF": "MANUFACTURING", "RRHH": "HUMAN_RESOURCES"}        # los prefijos son EVIDENCIA hacia una línea
    assert "MD" not in bc.LOB_BY_CODE and "MASTER_DATA" not in bc.LOB_BY_ID                             # datos maestros NO es una línea de negocio
    assert [(x["id"], x["label"]) for x in bc.LINES_OF_BUSINESS] == [("FINANCE", "Finanzas"), ("SALES", "Ventas"), ("SUPPLY_CHAIN", "Cadena de suministro"),
                                                                     ("MANUFACTURING", "Manufactura"), ("HUMAN_RESOURCES", "Recursos Humanos")]   # IDs neutros (inglés); etiquetas en español
    assert all(re.fullmatch(r"[A-Z_]+", x["id"]) for x in bc.LINES_OF_BUSINESS) and "SALES_DISTRIBUTION" not in bc.LOB_BY_ID   # nada de vocabulario de módulo SAP como ID


def test_prefixes_contribute_evidence_to_their_line_without_defining_it():
    for code, lob in (("IVT", "SUPPLY_CHAIN"), ("PUR", "SUPPLY_CHAIN"), ("FNC", "FINANCE"), ("SND", "SALES"), ("MNF", "MANUFACTURING"), ("RRHH", "HUMAN_RESOURCES")):
        r = _run("General", f"AM_{code}_X", [f"1TR_{code}_A", f"2VR_{code}_A", f"3VF_{code}_A", f"AM_{code}_X"])
        assert _lob(r) == lob and r["confidence"] == bc.HIGH, (code, r["reason_code"])


def test_the_classifier_never_invents_a_line_of_business_from_model_names():
    r = _run("General", "AM_ZETA_QUUX", ["V_Zeta", "T_Zeta", "V_Quux", "T_Quux", "AM_ZETA_QUUX"])
    assert r["line_of_business"] is None and r["status"] == bc.UNDETERMINED
    for lob in (_run(**{k: c[k] for k in ("cur", "root", "names")}) for c in (BIOGENESIS_STOCK, KENSING_GL, ILLOLAY_PEDIDOS)):
        assert _lob(lob) in bc.LOB_BY_ID


# ── agrupación efectiva ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_effective_grouping_precedence_manual_over_high_over_unclassified_and_medium_never_moves():
    high = _run(**{k: KENSING_GL[k] for k in ("cur", "root", "names", "labels")})
    medium = _run("General", "AM_STOCK", ["AM_STOCK", "V_Stock", "T_Stock"])
    none = _run("General", "AM_REPORTE_GERENCIAL", ["V_Base", "AM_REPORTE_GERENCIAL"])
    assert bc.effective_classification(high)["source"] == "AUTOMATIC" and bc.effective_classification(high)["line_of_business"] == "FINANCE"
    m = bc.effective_classification(medium)
    assert m["source"] == "UNCLASSIFIED" and m["line_of_business"] is None and m["suggestion"]["id"] == "SUPPLY_CHAIN"       # la MEDIUM queda como sugerencia
    assert bc.effective_classification(none)["source"] == "UNCLASSIFIED" and bc.effective_classification(None)["source"] == "UNCLASSIFIED"
    man = bc.effective_classification(high, "SALES")
    assert man["source"] == "MANUAL" and man["line_of_business"] == "SALES" and man["automatic_alignment"] == "DISAGREES"   # se conserva y se informa la discrepancia
    assert bc.effective_classification(high, "FINANCE")["automatic_alignment"] == "ALIGNED"
    assert bc.effective_classification(none, "HUMAN_RESOURCES")["source"] == "MANUAL"
    assert bc.effective_classification(high, "NO_EXISTE")["source"] == "AUTOMATIC"                       # un ID fuera de la taxonomía no es una asignación manual válida


def _flow_md(manual_line: str) -> str:
    lines = ["---", "auto:", "  tipo: flujo", "  dominio: Dom", "  artefactos: []", "manual:", "  cliente: Cli", manual_line, "---", "", "# Flujo", ""]
    return chr(10).join(lines)


def test_manual_line_of_business_is_read_from_the_flow_manual_block_without_touching_the_legacy_module(tmp_path):
    d = tmp_path / "Cli" / "Dom"
    (d / "vistas").mkdir(parents=True)
    flow = d / "flujo_Dom.md"
    flow.write_text(_flow_md("  linea_de_negocio: finance"), encoding="utf-8")
    e = cs.load_catalog(str(tmp_path))[0]
    assert dcs.manual_lob(e) == "FINANCE"
    flow.write_text(_flow_md("  linea_de_negocio: NO_EXISTE"), encoding="utf-8")
    assert dcs.manual_lob(e) is None                                                                    # un ID fuera de la taxonomía no es una asignación manual válida
    flow.write_text(_flow_md("  line_of_business: FINANCE"), encoding="utf-8")
    assert dcs.manual_lob(e) is None                                                                    # una sola clave canónica: `linea_de_negocio` (convención española del bloque manual)
    assert "manual_lob" not in (REPO / "tools/catalog/catalog_service.py").read_text(encoding="utf-8")   # el módulo legacy de scoring queda intacto


# ── invariantes de ortogonalidad ────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_engine_is_independent_of_judgment_assessment_and_the_network():
    src = (REPO / "tools/catalog/business_classification.py").read_text(encoding="utf-8")
    imports = re.findall(r"^(?:from|import)\s+([\w.]+)", src, re.M)
    assert not any(m.startswith(("tools.catalog.methodological_judgment", "tools.catalog.modeling_assessment", "webapp", "core", "requests", "urllib", "http", "socket")) for m in imports), imports
    code = src.split('"""', 2)[2]
    assert "judgment" not in code.lower() and "juicio" not in code.lower() and "confidence_level" not in code


def test_changing_the_classification_cannot_alter_judgment_evidence_profile_or_signatures(world, monkeypatch):
    svc.generate(world.acme)
    path = files(world)[0]
    env1 = json.loads(path.read_text(encoding="utf-8"))
    assert env1["business_classification"]["contract_version"] == bc.BUSINESS_CLASSIFICATION_VERSION and "business_classification" not in env1["judgment"]
    forced = {"contract_version": bc.BUSINESS_CLASSIFICATION_VERSION, "status": bc.CLASSIFIED, "confidence": bc.HIGH, "input_signature": "otra",
              "line_of_business": {"id": "FINANCE", "label": "Finanzas"}, "domain": None, "evidence": [], "competing": [], "reason_code": bc.R_ROOT_AND_LINEAGE, "alignment": None,
              "domain_classification": {}}
    monkeypatch.setattr(dcs, "derive_business_for_entry", lambda entry, am, all_entries=None: forced)
    restart()
    svc.generate(world.acme, force=True)
    env2 = json.loads(path.read_text(encoding="utf-8"))
    assert env2["business_classification"]["line_of_business"]["id"] == "FINANCE" != (env1["business_classification"]["line_of_business"] or {}).get("id")
    assert (env2["judgment"], env2["profile_json"], env2["assessment_artifact_signature"], env2["assessment_versions"]) == (
        env1["judgment"], env1["profile_json"], env1["assessment_artifact_signature"], env1["assessment_versions"])
    assert env2["domain_classification"]["version"] == env1["domain_classification"]["version"]        # la v1 de dominio conserva su propio contrato


def test_derivation_needs_no_engine_and_no_network(world, monkeypatch):
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debe evaluar")))
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sin red")))
    r = dcs.derive_business_for_entry(world.acme, "4AM_ACME")
    assert r and r["contract_version"] == bc.BUSINESS_CLASSIFICATION_VERSION and r["status"] in (bc.CLASSIFIED, bc.SUGGESTED, bc.UNDETERMINED)
    assert dcs.derive_business_for_entry(world.acme, None) is None and dcs.derive_business_for_entry(None, "X") is None


def test_v1_domain_results_never_appear_as_v2():
    v1 = {"version": "domain-classification/v1", "input_signature": "x", "classification_status": "UNDETERMINED", "current_domain": "General", "confidence": "LOW",
          "suggested_domain": None, "suggestion_type": None, "evidence": [], "alternatives": [], "reason": "NO_COHERENT_EVIDENCE"}
    legacy_view = presenter.business_classification_view(None, presenter.domain_classification_view(v1))
    assert legacy_view["headline"] == "No hay evidencia suficiente para recomendar otro dominio." and not any(r["label"] == "Línea de negocio" for r in legacy_view["rows"])
    assert dcs.resolve_business(v1, None, None) == (None, False)                                      # una v1 persistida bajo la clave v2 no se acepta como v2


# ── persistencia + backfill ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _strip(world, *keys):
    svc.generate(world.acme)
    path = files(world)[0]
    env = json.loads(path.read_text(encoding="utf-8"))
    for k in keys:
        env.pop(k)
    path.write_text(json.dumps(env), encoding="utf-8")
    restart()
    return path, env


def test_business_backfill_is_an_independent_operator_idempotent_with_business_counts(world, monkeypatch):
    svc.generate(world.acme)
    path = files(world)[0]
    env = json.loads(path.read_text(encoding="utf-8"))
    original = {k: env[k] for k in ("profile_json", "judgment", "assessment_artifact_signature", "assessment_versions", "input_pool_signature", "generated_at", "domain_classification")}
    env.pop("business_classification")                                                                  # sobre histórico (anterior a v2)
    path.write_text(json.dumps(env), encoding="utf-8")
    restart()
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: (_ for _ in ()).throw(AssertionError("el backfill NO reevalúa")))
    dry = bf.backfill_business_classification([world.acme], apply=False)
    c = dry["counts"]
    assert dry["mode"] == "backfill-business-classification" and c["would_update"] == 1 and {"eligible", "classified_high", "suggested_medium", "undetermined", "conflicts", "unchanged", "failed"} <= set(c)
    assert "business_classification" not in json.loads(path.read_text(encoding="utf-8"))               # dry-run: cero escrituras
    done = bf.backfill_business_classification([world.acme], apply=True)
    assert done["counts"]["updated"] == 1 and done["counts"]["failed"] == 0
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["business_classification"]["contract_version"] == bc.BUSINESS_CLASSIFICATION_VERSION and {k: after[k] for k in original} == original   # incluida la v1 de dominio
    again = bf.backfill_business_classification([world.acme], apply=True)
    assert again["counts"]["updated"] == 0 and again["counts"]["unchanged"] == 1 and again["counts"]["complete"] is True    # idempotente / restart-safe
    changed = dataclasses.replace(world.acme, artefactos=[*world.acme.artefactos, "EXTRA_FNC_X"])      # cambia la evidencia → firma distinta → se actualiza
    assert bf.backfill_business_classification([changed], apply=False)["counts"]["would_update"] == 1


def test_domain_backfill_keeps_its_v1_only_contract_and_the_two_operators_do_not_touch_each_others_key(world, monkeypatch):
    path, env = _strip(world, "domain_classification", "business_classification")
    rep = bf.backfill_domain_classification([world.acme], apply=True)                                    # v1: SOLO dominio
    after = json.loads(path.read_text(encoding="utf-8"))
    assert rep["mode"] == "backfill-domain-classification" and "domain_classification" in after and "business_classification" not in after
    assert not {"classified_high", "suggested_medium", "conflicts"} & set(rep["counts"])                 # el contrato v1 no gana conteos de negocio
    restart()
    rep2 = bf.backfill_business_classification([world.acme], apply=True)                                 # v2: SOLO negocio
    after2 = json.loads(path.read_text(encoding="utf-8"))
    assert "business_classification" in after2 and after2["domain_classification"] == after["domain_classification"] and rep2["counts"]["updated"] == 1
    path2, _ = _strip(world, "domain_classification")
    bf.backfill_business_classification([world.acme], apply=True)
    assert "domain_classification" not in json.loads(path2.read_text(encoding="utf-8"))                  # el operador de negocio NO materializa la v1 como efecto colateral


def test_the_business_operator_cli_is_explicit_and_mutually_exclusive(world):
    for argv in (["--backfill-business-classification"], ["--backfill-business-classification", "--dry-run", "--apply"],
                 ["--backfill-business-classification", "--backfill-domain-classification", "--dry-run"]):
        with pytest.raises(SystemExit):
            bf.main(argv)
    assert bf.main(["--backfill-business-classification", "--dry-run"]) in (0, 2)


def test_a_failing_root_does_not_roll_back_the_others(world, monkeypatch):
    _strip(world, "business_classification")
    other = dataclasses.replace(world.acme, entry_id="acme-other")
    real = svc.apply_business_classification_backfill
    calls = []

    def flaky(env_, b):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("disco lleno")
        return real(env_, b)
    monkeypatch.setattr(svc, "apply_business_classification_backfill", flaky)
    rep = bf.backfill_business_classification([world.acme, other], apply=True)
    assert rep["counts"]["failed"] >= 1 and rep["counts"]["complete"] is False                       # el fallo se informa; el resto del lote sigue
    assert any(r["action"] == "failed" for r in rep["records"])


# ── presentación en Assessment [04] ─────────────────────────────────────────────────────────────────────────────────────────────────────
def test_business_classification_view_shows_line_domain_confidence_state_and_evidence():
    r = _run(**{k: ILLOLAY_PEDIDOS[k] for k in ("cur", "root", "names", "labels")})
    v = presenter.business_classification_view(r, None)
    rows = {x["label"]: x["value"] for x in v["rows"]}
    assert rows["Línea de negocio"] == "Ventas" and rows["Dominio"].startswith("Pedidos / Entregas") and rows["Confianza"] == "Alta"
    assert rows["Estado"] == "Clasificación automática" and rows["Agrupación efectiva"] == "Ventas"
    assert v["evidence"] and {e["family"] for e in v["evidence"]} <= {"Objeto raíz", "Linaje del cierre", "Fuentes del origen"} and not any("score" in str(e).lower() for e in v["evidence"])
    sug = presenter.business_classification_view(_run("General", "AM_STOCK", ["AM_STOCK", "V_Stock", "T_Stock"]), None)
    assert {x["label"]: x["value"] for x in sug["rows"]}["Estado"] == "Sugerida" and {x["label"]: x["value"] for x in sug["rows"]}["Agrupación efectiva"] == "Sin clasificar"
    man = presenter.business_classification_view(r, None, "FINANCE")
    mrows = {x["label"]: x["value"] for x in man["rows"]}
    assert mrows["Estado"] == "Manual" and mrows["Línea de negocio"] == "Finanzas" and "difiere" in mrows["Clasificación automática"]
    und = presenter.business_classification_view(_run("General", "AM_X", ["AM_X"]), None)
    assert {x["label"]: x["value"] for x in und["rows"]}["Estado"] == "Sin determinar" and "No hay evidencia suficiente" in und["headline"]


def test_assessment_section_04_renders_the_business_classification_with_hub_primitives(world):
    st = svc.generate(world.acme)
    view = presenter.report_view(st, world.acme, "datasphere_core")
    view["domain_classification"] = presenter.business_classification_view(_run(**{k: ILLOLAY_PEDIDOS[k] for k in ("cur", "root", "names", "labels")}), None)
    html = main.templates.get_template("partials/catalog_assessment_report.html").render(v=view, pack="datasphere_core")
    sec = html[html.index('id="asr-dominio"'):]
    sec = sec[:sec.index("</details>")]
    for text in ("Clasificación de negocio", "Línea de negocio", "Ventas", "Dominio", "Confianza", "Alta", "Estado", "Evidencia", "Agrupación efectiva"):
        assert text in sec, text
    assert "catalog-card__verdict" not in sec and not re.search(r"asr-negocio|business-class", (REPO / "webapp/static/tools.css").read_text(encoding="utf-8"))   # sin insignia de grado ni CSS propio


# ── ajustes genéricos de la calibración sobre el corpus real ─────────────────────────────────────────────────────────────────────────────────
def test_descriptive_dimension_artifacts_do_not_vote_in_the_lineage():
    names = ["2VG_DIM_LIDER_VENTAS", "2VG_DIM_ZONA_VENTAS", "2VS_0CUST_SALES_HIER", "2VG_ZBW_CUST_SALES_ATTR", "2VT_VENTAS_TEXTO",       # maestros / dimensiones de «ventas»
             "2TF_MSEG", "2TR_MBEW", "2VS_STOCK_VALORADO", "3VF_STOCK", "3VS_STOCK", "4MA_STOCK"]
    r = _run("Stock", "4MA_STOCK", names, labels={"2VG_DIM_LIDER_VENTAS": "Dato Maestro Líder de Ventas"})
    assert _lob(r) == "SUPPLY_CHAIN" and r["confidence"] == bc.HIGH and r["reason_code"] != bc.R_CONFLICT        # el vocabulario de las dimensiones no compite con la transacción


def test_a_single_repeated_word_in_auxiliary_artifacts_is_not_corroboration():
    r = _run("General", "AM_RESERVA_MP", ["AM_RESERVA_MP", "VD_ZSD_MATNR_MEINS", "ZDSP_MATRNMEINS", "VD_X", "VD_Y"],
             labels={"VD_ZSD_MATNR_MEINS": "Vista Unidad Mínima Venta", "ZDSP_MATRNMEINS": "Tabla Unidad Mínima de Venta"})
    assert r["line_of_business"] is None and r["status"] == bc.UNDETERMINED                               # «venta» ×2 en helpers: ruido, ni siquiera sugerencia


def test_source_and_lineage_are_not_double_counted_for_the_same_sourced_artifacts():
    names = ["1TR_ZCDS_LIKP", "1TR_ZCDS_LIPS", "AM_X"]
    r = _run("General", "AM_X", names, srcs={"1TR_ZCDS_LIKP": "ABAPTABLES.LIKP", "1TR_ZCDS_LIPS": "ABAPTABLES.LIPS"})
    assert _lob(r) == "SALES" and r["confidence"] == bc.MEDIUM                                            # SOURCE solo: el objeto del nombre y del origen es UN mismo hecho
    assert [e["family"] for e in r["evidence"]] == ["SOURCE", "SOURCE"]


def test_organizational_and_master_objects_never_vote():
    r = _run("General", "AM_X", ["1TR_T001", "1TR_T001L", "1TR_T001W", "AM_X"], srcs={"1TR_T001": "ABAPTABLES.T001", "1TR_T001L": "ABAPTABLES.T001L", "1TR_T001W": "ABAPTABLES.T001W"})
    assert r["line_of_business"] is None


def test_technical_identifiers_are_not_business_domains_and_ties_report_the_competing_lines():
    r = _run("AM_Lista_Precios", "AM_Lista_Precios", ["V_Precio", "T_Precio", "AM_Lista_Precios"], domains=["AM_Lista_Precios"])
    assert _lob(r) == "SALES" and r["domain"] is None                                                    # el eco del nombre del modelo no es un dominio
    t = _run("General", "AM_VENTAS_GASTOS", ["V_Ventas", "T_Ventas", "V_Gasto", "T_Gasto", "AM_VENTAS_GASTOS"])
    assert t["reason_code"] == bc.R_CONFLICT and {c["id"] for c in t["competing"]} == {"SALES", "FINANCE"}  # el empate informa QUÉ líneas compiten
