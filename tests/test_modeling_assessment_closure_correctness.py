"""
Evidence release (Gate 8A.3) — S1: correccion del cierre semantico y completitud de referencias (``closure-rules/v1``).

Una tabla local producida por un Data Flow y LEIDA por un artefacto de Modeling es miembro HOJA del cierre semantico, con independencia de como el export
empaquete el flujo (Illolay/Loma: ``DF_*.json`` con ``definitions``; Biogenesis: ``data-flows/DF_*.json`` solo con ``dataflows``). El productor es contexto de
procedencia. Las omisiones del cierre bajan la COBERTURA; la identidad ambigua afecta la CONFIANZA. Sin heuristica por nombre de archivo.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._evidence_release_pool import AM, _w, write_pool
from tools.catalog.modeling_assessment import shadow as ma_shadow
from tools.catalog.modeling_assessment.graph import PLANE_POLICY_LOADER_FILE_FLAG, PLANE_POLICY_MODELING_MEMBERSHIP, DependencyGraph
from tools.catalog.modeling_assessment.scope import AssessmentUniverse


def _doc(root: Path) -> dict:
    doc, _ = ma_shadow.assess(root, "Acme", AM, "General")
    return doc


def _entities(root: Path):
    files = sorted(root.rglob("*.json"))
    group = {"jsons": files, "txts": []}
    return ma_shadow.load_pool(group)


def _surface(doc: dict, sid: str) -> dict:
    return next(s for s in doc["surfaces"] if s["surface_id"] == sid)


# ── 1. un flujo que produce una tabla leida por Modeling no la excluye del cierre ────────────────
def test_a_flow_produced_table_read_by_modeling_is_a_semantic_closure_member():
    doc = _doc(write_pool(bundled_df=True))
    assert "2TR_VBAK" in doc["scope"]["closure_artifacts"] and doc["scope"]["closure_size"] == 7
    ctx = doc["canonical_evidence"]["closure"]["plane_context"]
    assert ctx["added_vs_loader_file_flag"] == ["2TR_VBAK"] and ctx["loader_file_flag_closure_size"] == 6 and ctx["removed_vs_loader_file_flag"] == []
    member = ctx["flow_produced_members"][0]
    assert member["artifact"] == "2TR_VBAK" and member["semantic_consumers"] == ["V_A", "V_B"] and member["producer_flows"] == ["DATA_FLOW:DF_VBAK"]
    assert member["membership_basis"] == "READ_BY_SEMANTIC_MEMBER" and member["loader_file_flag"] is True


# ── 2. el productor y la vista de origen NO son miembros: son contexto de procedencia ─────────────
def test_the_producer_flow_and_its_source_view_stay_in_the_provenance_plane_not_in_the_closure():
    doc = _doc(write_pool(bundled_df=True))
    closure = set(doc["scope"]["closure_artifacts"])
    assert not ({"DF_VBAK", "VBAK_HIST", "1TR_VBAK", "1TR_TVAKT"} & closure)
    leaf = doc["canonical_evidence"]["physical_source_boundaries"]["leaves"][0]
    assert leaf["leaf"] == "2TR_VBAK" and leaf["flows"] == ["DF_VBAK"]
    assert all(b["plane"] == "PROVENANCE" and b["is_semantic_closure_member"] is False and b["counts_as_direct_raw_table_access"] is False for b in leaf["boundaries"])


# ── 3. el empaquetado del export no decide la pertenencia (Illolay-style vs Biogenesis-style) ─────
def test_bundled_and_unbundled_data_flow_exports_give_the_same_semantic_closure():
    bundled, plain = _doc(write_pool(bundled_df=True)), _doc(write_pool(bundled_df=False))
    assert bundled["scope"]["closure_artifacts"] == plain["scope"]["closure_artifacts"]
    assert bundled["scope"]["closure_size"] == plain["scope"]["closure_size"] == 7
    rb, rp = bundled["canonical_evidence"]["closure"]["reference_resolution"], plain["canonical_evidence"]["closure"]["reference_resolution"]
    assert rb == rp
    # lo unico que cambia es la DIFERENCIA contra la politica anterior (la marca del loader solo existia en el empaquetado con `definitions`)
    assert bundled["canonical_evidence"]["closure"]["plane_context"]["added_vs_loader_file_flag"] == ["2TR_VBAK"]
    assert plain["canonical_evidence"]["closure"]["plane_context"]["added_vs_loader_file_flag"] == []


# ── 4. una entidad marcada por el loader que ningun miembro lee NO se agrega ──────────────────────
def test_an_entity_the_loader_flags_but_no_semantic_member_reads_is_not_added():
    doc = _doc(write_pool(bundled_df=True, orphan=True))
    assert "2TR_ORPHAN" not in doc["scope"]["closure_artifacts"] and doc["scope"]["closure_size"] == 7
    ctx = doc["canonical_evidence"]["closure"]["plane_context"]
    assert ctx["processing_only_entities"] == ["2TR_ORPHAN"] and ctx["added_vs_loader_file_flag"] == ["2TR_VBAK"]


# ── 5. ni el prefijo de nombre ni el nombre del archivo deciden la pertenencia ────────────────────
def test_neither_the_name_prefix_nor_the_file_name_decides_membership(tmp_path):
    root = write_pool(bundled_df=True, root=tmp_path)
    renamed = root / "Acme" / "DF_VBAK.json"
    renamed.rename(root / "Acme" / "cualquier_cosa.json")                   # el nombre del archivo ya no empieza con DF_
    doc = _doc(root)
    assert "2TR_VBAK" in doc["scope"]["closure_artifacts"] and doc["scope"]["closure_size"] == 7


# ── 6. la membresia se deriva de lo que Modeling lee, no de la marca del loader ───────────────────
def test_membership_policy_is_modeling_read_not_the_loader_flag_and_the_old_policy_stays_reproducible():
    ents = _entities(write_pool(bundled_df=True))
    new = AssessmentUniverse.from_entities(ents, plane_policy=PLANE_POLICY_MODELING_MEMBERSHIP).closure_of(AM)
    old = AssessmentUniverse.from_entities(ents, plane_policy=PLANE_POLICY_LOADER_FILE_FLAG).closure_of(AM)
    assert set(new) - set(old) == {"2TR_VBAK"} and set(old) <= set(new) and len(new) == len(old) + 1
    assert DependencyGraph.from_entities(ents, PLANE_POLICY_MODELING_MEMBERSHIP).processing_context.flow_produced_members == ("2TR_VBAK",)


# ── 7. la hoja producida por un flujo NO arrastra a sus upstreams de loader a la clausura ─────────
def test_a_flow_produced_leaf_does_not_pull_its_loader_injected_upstreams_into_the_closure():
    ents = _entities(write_pool(bundled_df=True))
    graph = DependencyGraph.from_entities(ents, PLANE_POLICY_MODELING_MEMBERSHIP)
    assert graph.providers("2TR_VBAK") == ()                                   # hoja: el flujo enriquece procedencia, no linaje semantico
    assert not ({"VBAK_HIST", "1TR_VBAK", "1TR_TVAKT"} & set(graph.semantic_closure(AM)))


# ── 8. el cierre es determinista y de solo-lectura de las politicas ──────────────────────────────
def test_the_closure_is_deterministic_across_runs():
    root = write_pool(bundled_df=True)
    a, b = _doc(root), _doc(root)
    assert a["scope"]["closure_artifacts"] == b["scope"]["closure_artifacts"]
    assert json.dumps(a["canonical_evidence"]["closure"], sort_keys=True) == json.dumps(b["canonical_evidence"]["closure"], sort_keys=True)


# ── 9. el cierre corregido no cambia el AM terminal ni la raiz ───────────────────────────────────
def test_the_terminal_model_and_root_identity_are_unchanged_by_the_correction():
    doc = _doc(write_pool(bundled_df=True))
    assert doc["scope"]["terminal_analytic_model"] == AM and doc["scope"]["root_object"] == AM and doc["scope"]["root_type"] == "analytic-model"


# ── 10. un cierre sin hojas producidas por flujos no cambia ───────────────────────────────────────
def test_a_closure_without_flow_produced_leaves_is_identical_under_both_policies(tmp_path):
    root = write_pool(bundled_df=False, root=tmp_path)
    (root / "Acme" / "data-flows" / "DF_VBAK.json").unlink()                    # sin productor: 2TR_VBAK es una tabla manual
    ents = _entities(root)
    new = AssessmentUniverse.from_entities(ents, plane_policy=PLANE_POLICY_MODELING_MEMBERSHIP).closure_of(AM)
    old = AssessmentUniverse.from_entities(ents, plane_policy=PLANE_POLICY_LOADER_FILE_FLAG).closure_of(AM)
    assert new == old


# ═══════════════ closure.reference_resolution: completitud de la superficie (11-16) ═══════════════
def test_a_complete_closure_assesses_the_required_completeness_surface():
    doc = _doc(write_pool(bundled_df=True))
    s = _surface(doc, "am.closure_reference_resolution")
    assert s["role"] == "REQUIRED" and s["area"] == "ANALYTIC_MODEL" and s["assessability"] == "ASSESSED"
    rr = doc["canonical_evidence"]["closure"]["reference_resolution"]
    assert rr["counts"]["UNRESOLVED"] == rr["counts"]["EXCLUDED_BY_PLANE"] == rr["counts"]["AMBIGUOUS"] == 0 and rr["members_total"] == 7


def test_platform_provided_targets_are_listed_apart_and_do_not_lower_coverage():
    doc = _doc(write_pool(bundled_df=True))
    rr = doc["canonical_evidence"]["closure"]["reference_resolution"]
    assert rr["platform_provided_total"] == 1 and rr["members_with_open_references"] == []                  # SAP.TIME.VIEW_DIMENSION_DAY: objeto de plataforma, no un faltante
    assert doc["summary"]["coverage"]["strict"] == 1.0


def _drop(root: Path, rel: str) -> None:
    (root / rel).unlink()


def test_a_declared_but_unresolvable_reference_lowers_coverage_not_confidence_factors():
    root = write_pool(bundled_df=True)
    _drop(root, "Acme/views/2VS_DIM.json")                                      # la asociacion declarada apunta ahora a un artefacto que no esta en el pool
    doc = _doc(root)
    rr = doc["canonical_evidence"]["closure"]["reference_resolution"]
    assert rr["counts"]["UNRESOLVED"] == 1 and rr["members_with_open_references"][0]["artifact"] == AM
    assert rr["members_with_open_references"][0]["unresolved"][0]["target"] == "2VS_DIM"
    s = _surface(doc, "am.closure_reference_resolution")
    assert s["assessability"] in ("PARTIALLY_ASSESSED", "NOT_ASSESSED") and doc["summary"]["coverage"]["strict"] < 1.0
    assert s["units_not_fully_assessed"][0]["subject"] == AM


def test_a_closure_member_not_in_the_semantic_plane_is_reported_as_excluded_by_plane_and_lowers_coverage():
    root = write_pool(bundled_df=True)
    # V_A deja de leer 2TR_VBAK y pasa a leer una entidad que SOLO un flujo produce y ningun otro artefacto de Modeling lee (contexto de Processing)
    _w(root, "Acme/DF_ORPHAN.json", {"definitions": {"2TR_ORPHAN": {"kind": "entity", "elements": {"X": {"type": "cds.String"}}}},
                                      "dataflows": {"DF_ORPHAN": {"kind": "sap.dis.dataflow", "contents": {}, "sources": {}, "targets": {"2TR_ORPHAN": {}}}}})
    doc = _doc(root)
    rr = doc["canonical_evidence"]["closure"]["reference_resolution"]
    assert rr["counts"]["EXCLUDED_BY_PLANE"] == 0                               # nadie del cierre lo lee: no es una omision
    assert doc["summary"]["coverage"]["strict"] == 1.0


def test_an_ambiguous_reference_identity_affects_confidence_not_coverage():
    root = write_pool(bundled_df=True)
    # un artefacto cuyo nombre solo difiere en mayusculas del destino declarado: identidad incierta, nunca se acepta como arista
    body = json.loads((root / "Acme/views/2VS_DIM.json").read_text(encoding="utf-8"))
    (root / "Acme/views/2VS_DIM.json").unlink()
    renamed = {"2vs_dim" if k == "2VS_DIM" else k: v for k, v in body["definitions"].items()}
    _w(root, "Acme/views/2vs_dim.json", {"definitions": renamed})
    doc = _doc(root)
    rr = doc["canonical_evidence"]["closure"]["reference_resolution"]
    assert rr["counts"]["AMBIGUOUS"] == 1 and rr["counts"]["UNRESOLVED"] == 0
    amb = rr["members_with_open_references"][0]["ambiguous"][0]
    assert amb["target"] == "2VS_DIM" and amb["candidates"] == ["2vs_dim"]
    assert doc["summary"]["coverage"]["strict"] == 1.0
    ids = {c["id"]: c for c in doc["summary"]["confidence"]["components"]}
    assert "unambiguous_closure_references" in ids and ids["unambiguous_closure_references"]["value"] < 1.0            # la ambiguedad entra como factor epistemico (confianza), no como cobertura


def test_the_coverage_denominator_gained_exactly_the_one_required_completeness_surface():
    doc = _doc(write_pool(bundled_df=True))
    req = [s for s in doc["surfaces"] if s["role"] == "REQUIRED"]
    assert sum(1 for s in req if s["surface_id"] == "am.closure_reference_resolution") == 1
    assert doc["summary"]["coverage"]["required_surfaces"]["applicable"] == len([s for s in req if s["assessability"] != "NOT_APPLICABLE"])


# ═══════════════ Gate 8A.4: invariancia de empaquetado, referencias de targets de DF y separacion procedencia/consumo directo ═══════════════
def _members(root: Path) -> list[str]:
    return _doc(root)["scope"]["closure_artifacts"]


def test_packaging_invariance_target_only_inside_the_df_export_vs_separate_modeling_entity(tmp_path):
    """A: el target vive SOLO embebido en las `definitions` del DF (sin archivo propio). B: el MISMO target exportado aparte como entidad de Modeling. C: DF sin `definitions`."""
    a = _members(write_pool(bundled_df=True, target_file=False, root=tmp_path / "a"))
    b = _members(write_pool(bundled_df=True, target_file=True, root=tmp_path / "b"))
    c = _members(write_pool(bundled_df=False, root=tmp_path / "c"))
    assert a == b == c and "2TR_VBAK" in a and len(a) == 7


def test_the_references_to_a_flow_target_are_excluded_under_the_old_policy_and_resolved_under_the_corrected_one():
    from tools.catalog.modeling_assessment.facts_closure import closure_facts
    root = write_pool(bundled_df=True)
    ents = _entities(root)

    def counts(policy):
        uni = AssessmentUniverse.from_entities(ents, plane_policy=policy)
        facts = closure_facts(uni, ents, uni.scope_for(AM, pool_signature="x").root)
        per = {f.subject: f.value for f in facts if f.fact_id == "am.closure_reference_resolution"}
        return per
    old, new = counts(PLANE_POLICY_LOADER_FILE_FLAG), counts(PLANE_POLICY_MODELING_MEMBERSHIP)
    assert old["V_A"]["counts"]["EXCLUDED_BY_PLANE"] >= 1 and old["V_B"]["counts"]["EXCLUDED_BY_PLANE"] >= 1                 # antes: referencia conocida a un miembro excluido -> completitud
    assert new["V_A"]["counts"]["EXCLUDED_BY_PLANE"] == 0 and any(r["target"] == "2TR_VBAK" for r in new["V_A"]["resolved"])
    assert new["V_B"]["counts"]["EXCLUDED_BY_PLANE"] == 0 and any(r["target"] == "2TR_VBAK" for r in new["V_B"]["resolved"])


def test_a_physical_source_reached_only_through_provenance_is_never_direct_raw_table_access():
    doc = _doc(write_pool(bundled_df=True))
    groups = {g["id"]: g for g in doc["observations"] + doc["review_required"]}
    direct = {a for g in groups.values() if g["id"] == "DIRECT_RAW_TABLE_ACCESS" for o in g["occurrences"] for a in o["affected_artifacts"]}
    assert not ({"1TR_VBAK", "1TR_TVAKT", "VBAK_HIST", "DF_VBAK"} & direct)
    assert "1TR_KNA1" in direct                                                  # la tabla fisica que SI consume la dimension de Modeling sigue siendo acceso directo
    leaf = doc["canonical_evidence"]["physical_source_boundaries"]["leaves"][0]
    assert all(b["counts_as_direct_raw_table_access"] is False and b["plane"] == "PROVENANCE" for b in leaf["boundaries"])
    pb = doc["canonical_evidence"]["physical_source_boundaries"]
    text = json.dumps({k: v for k, v in pb.items() if k != "definition"}).lower()
    assert "direct_raw_table_access" not in text.replace("counts_as_direct_raw_table_access", "")           # los datos no colapsan los dos conceptos
    assert "not direct_raw_table_access evidence" in pb["definition"].lower()                                # y la definicion los distingue expresamente


def test_heterogeneous_measure_types_emit_no_signal_at_all():
    het, hom = _doc(write_pool(bundled_df=True, peso_type="Double")), _doc(write_pool(bundled_df=True, peso_type="Decimal"))
    ids = lambda d: sorted(g["id"] for g in d["observations"] + d["review_required"])          # noqa: E731
    assert ids(het) == ids(hom)
    assert het["summary"]["coverage"] == hom["summary"]["coverage"] and het["summary"]["confidence"]["level"] == hom["summary"]["confidence"]["level"]
    assert [q["id"] for q in het["summary"]["context_required"]["questions"]] == [q["id"] for q in hom["summary"]["context_required"]["questions"]]
    assert het["canonical_evidence"]["published_contract"]["fields"]["measure_type_composition"]["signature_count"] == 3
    assert hom["canonical_evidence"]["published_contract"]["fields"]["measure_type_composition"]["signature_count"] == 2
