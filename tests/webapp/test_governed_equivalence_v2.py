"""Equivalencia metodológica v2 (E1–E4): fronteras de plataforma acreditadas, materialidad de flujos, observabilidad, versionado y renovación.
Perfiles SINTÉTICOS con la forma del caso Contreras / AM_Workforce (sin datos de cliente)."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from tools.catalog.modeling_assessment import evidence_observability as eo
from tools.catalog.modeling_assessment import platform_boundary as pbm
from webapp.services import governed_evidence as ge
from webapp.services import governed_update_approval as gua
from webapp.services import governed_update_requests as gur
from tests.webapp.test_governed_update_requests import _create, world  # noqa: F401  (fixture compartida)

G, X, Y, M = "SAP.TIME.VIEW_DIMENSION_DAY", "SAP.TIME.M_TIME_DIMENSION", "SAP.TIME.M_TIME_DIMENSION_RAW", "SAP.TIME.VIEW_DIMENSION_MONTH"
TDS, NS = "TIME_DIMENSION_SETTING", "NAMESPACE_ONLY"
VERS = {"canonical_evidence": "canonical-evidence/v6", "closure": "closure-rules/v1", "identity": "identity-rules/v1"}
REMOTE = {"leaf": "1TR_R", "leaf_kind": "REMOTE_IMPORTED", "contract_class": "GOVERNED_SAP_APPLICATION_API", "remote_connection": "SSFF",
          "connection_type": "SAPSF", "remote_entities": ["EmpJob"], "source_systems": []}


def _plat(name):
    return {"leaf": name, "leaf_kind": "PLATFORM_OBJECT", "contract_class": "PLATFORM_PROVIDED", "remote_connection": None, "connection_type": None,
            "remote_entities": [], "source_systems": []}


def _prof(closure, leaves, *, unresolved=0, flow_members=(), versions=VERS, am="AM_X"):
    return {"versions": dict(versions),
            "scope": {"client": "Acme", "terminal_analytic_model": am, "root_object": am, "root_type": "analytic-model", "closure_artifacts": sorted(closure),
                      "evidence_scope": None},
            "canonical_evidence": {"version": versions["canonical_evidence"], "source_contract_classes": {"leaves": copy.deepcopy(leaves)},
                                   "closure": {"reference_resolution": {"members_total": len(closure), "counts": {"RESOLVED": 5, "UNRESOLVED": unresolved, "EXCLUDED_BY_PLANE": 0,
                                                                                                                 "AMBIGUOUS": 0},
                                                                        "platform_provided_total": 0, "members_with_open_references": []},
                                               "plane_context": {"flow_produced_members": [{"artifact": m} for m in flow_members]}}}}


def _pb(objects, refs, consumers):
    return {"version": "platform-boundary/v1", "objects": [{"name": n, "basis": b, "status": "COMPLETED", "granularity": "day"} for n, b in objects],
            "references": [{"from": a, "to": b, "kind": "lineage", "resolution": r} for a, b, r in refs], "direct_consumers": consumers}


def _obs(source, states):
    return {"version": "evidence-observability/v1", "source": source, "categories": {c: {"state": s, "artifacts": 1} for c, s in states.items()}}


ALL_OBSERVED = {c: "OBSERVED" for c in eo.CATEGORIES}
POOL_OBS = {**{c: "OBSERVED" for c in ("analytic-models", "views", "tables")}, **{c: "UNAVAILABLE" for c in ("data-flows", "replication-flows", "transformation-flows", "task-chains")}}
CLIENT = {"AM_X", "3VF_A", "2VD_B", "1TR_R"}


def workforce_like():
    """Ad hoc en vivo resuelve G hasta X; el pool deja a G como hoja (X fuera del pool, referencia PLATFORM_PROVIDED)."""
    adhoc = _prof(CLIENT | {G, X}, [REMOTE, _plat(X)])
    a_ext = {"platform_boundary": _pb([(G, TDS), (X, TDS)], [("3VF_A", G, "RESOLVED"), ("AM_X", G, "RESOLVED"), (G, X, "RESOLVED")],
                                      {G: ["3VF_A", "AM_X"], X: [G]}),
             "evidence_observability": _obs("EXTRACTOR", ALL_OBSERVED)}
    gov = _prof(CLIENT | {G}, [REMOTE, _plat(G)])
    g_ext = {"platform_boundary": _pb([(G, TDS)], [("3VF_A", G, "RESOLVED"), ("AM_X", G, "RESOLVED"), (G, X, "PLATFORM_PROVIDED")], {G: ["3VF_A", "AM_X"]}),
             "evidence_observability": _obs("POOL", POOL_OBS)}
    return adhoc, a_ext, gov, g_ext


def _bound(profile, ext):
    """Liga el sidecar a SU perfil como lo hace el motor en la MISMA evaluación (I1)."""
    return None if ext is None else {**ext, "profile_binding": pbm.profile_binding(profile)}


def _eq(a, ae, g, gx):
    fa, fg = ge.fingerprint_v2(a, _bound(a, ae)), ge.fingerprint_v2(g, _bound(g, gx))
    return fa["equivalence_digest"] == fg["equivalence_digest"] and fa["identity_digest"] == fg["identity_digest"], fa, fg


# ── positivas ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_p1_workforce_like_declared_scope_and_accredited_platform_depth_are_equivalent():
    ok, fa, fg = _eq(*workforce_like())
    assert ok and fa["undetermined"] == [] and fg["undetermined"] == []
    a, ae, g, gx = workforce_like()
    assert ge.fingerprint_v1(a)["equivalence_digest"] != ge.fingerprint_v1(g)["equivalence_digest"]      # v1 (congelada) seguía rechazando


def test_p2_same_platform_frontier_at_different_depths_is_equivalent():
    a, ae, g, gx = workforce_like()
    a["scope"]["closure_artifacts"] = sorted(CLIENT | {G, X, Y})
    a["canonical_evidence"]["source_contract_classes"]["leaves"] = [REMOTE, _plat(Y)]
    ae["platform_boundary"] = _pb([(G, TDS), (X, TDS), (Y, TDS)], [("3VF_A", G, "RESOLVED"), (G, X, "RESOLVED"), (X, Y, "RESOLVED")],
                                  {G: ["3VF_A", "AM_X"], X: [G], Y: [X]})
    assert _eq(a, ae, g, gx)[0]


LOCAL = {"leaf": "1TL_L", "leaf_kind": "LOCAL_NO_PRODUCER", "contract_class": "UNKNOWN", "remote_connection": None, "connection_type": None,
         "remote_entities": [], "source_systems": []}
FLOWED = {**LOCAL, "leaf_kind": "LOCAL_FLOW_PRODUCED", "contract_class": "GOVERNED_SAP_APPLICATION_API", "source_systems": [{"connection_id": "S4", "connection_type": "S4", "container": "C"}]}


def test_p3_local_tables_without_producer_with_flows_observed_are_equivalent():
    a, ae, g, gx = workforce_like()
    for p in (a, g):
        p["scope"]["closure_artifacts"].append("1TL_L")
        p["canonical_evidence"]["source_contract_classes"]["leaves"].append(dict(LOCAL))
    assert _eq(a, ae, g, gx)[0]


def test_p4_same_material_flow_on_both_sides_is_equivalent():
    a, ae, g, gx = workforce_like()
    for p in (a, g):
        p["scope"]["closure_artifacts"].append("1TL_L")
        p["canonical_evidence"]["source_contract_classes"]["leaves"].append(dict(FLOWED))
        p["canonical_evidence"]["closure"]["plane_context"]["flow_produced_members"] = [{"artifact": "1TL_L"}]
    assert _eq(a, ae, g, gx)[0]


# ── negativas ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("field,value", [("remote_connection", "OTRA"), ("connection_type", "ODATA"), ("remote_entities", ["PerPerson"]),
                                         ("source_systems", ["ECC"])])
def test_n1_n3_remote_contract_changes_are_not_equivalent(field, value):
    a, ae, g, gx = workforce_like()
    g["canonical_evidence"]["source_contract_classes"]["leaves"][0][field] = value
    assert not _eq(a, ae, g, gx)[0]


def test_n4_client_artifact_change_is_not_equivalent():
    a, ae, g, gx = workforce_like()
    g["scope"]["closure_artifacts"].append("2VD_NEW")
    assert not _eq(a, ae, g, gx)[0]


def test_n5_different_platform_frontiers_are_not_equivalent():
    a, ae, g, gx = workforce_like()
    g["scope"]["closure_artifacts"] = sorted(CLIENT | {M})
    g["canonical_evidence"]["source_contract_classes"]["leaves"] = [REMOTE, _plat(M)]
    gx["platform_boundary"] = _pb([(M, TDS)], [("3VF_A", M, "RESOLVED")], {M: ["3VF_A", "AM_X"]})
    assert not _eq(a, ae, g, gx)[0]


def test_n6_descendant_consumed_directly_by_the_client_is_not_collapsed():
    a, ae, g, gx = workforce_like()
    ae["platform_boundary"]["direct_consumers"][X] = [G, "3VF_A"]
    assert not _eq(a, ae, g, gx)[0]
    ae["platform_boundary"]["direct_consumers"][X] = [G, "AM_X"]                  # el modelo analítico también es consumidor del cliente
    assert not _eq(a, ae, g, gx)[0]


def test_n7_descendant_without_an_accredited_path_is_not_collapsed():
    a, ae, g, gx = workforce_like()
    ae["platform_boundary"]["references"] = [r for r in ae["platform_boundary"]["references"] if r["to"] != X]
    assert not _eq(a, ae, g, gx)[0]


def test_n8_namespace_only_classification_never_collapses():
    a, ae, g, gx = workforce_like()
    ae["platform_boundary"]["objects"] = [{"name": G, "basis": TDS}, {"name": X, "basis": NS}]
    assert not _eq(a, ae, g, gx)[0]
    a2, ae2, g2, gx2 = workforce_like()
    ae2["platform_boundary"]["objects"] = [{"name": G, "basis": NS}, {"name": X, "basis": TDS}]          # frontera sin base explícita
    assert not _eq(a2, ae2, g2, gx2)[0]


def test_n9_material_flow_on_one_side_only_is_not_equivalent():
    a, ae, g, gx = workforce_like()
    a["scope"]["closure_artifacts"].append("1TL_L")
    g["scope"]["closure_artifacts"].append("1TL_L")
    a["canonical_evidence"]["source_contract_classes"]["leaves"].append(dict(FLOWED))
    a["canonical_evidence"]["closure"]["plane_context"]["flow_produced_members"] = [{"artifact": "1TL_L"}]
    g["canonical_evidence"]["source_contract_classes"]["leaves"].append(dict(LOCAL))
    assert not _eq(a, ae, g, gx)[0]


def test_n10_same_flow_with_different_physical_source_is_not_equivalent():
    a, ae, g, gx = workforce_like()
    for p, leaf in ((a, FLOWED), (g, {**FLOWED, "source_systems": [{"connection_id": "ECC", "connection_type": "ABAP", "container": "C"}]})):
        p["scope"]["closure_artifacts"].append("1TL_L")
        p["canonical_evidence"]["source_contract_classes"]["leaves"].append(dict(leaf))
    assert not _eq(a, ae, g, gx)[0]


def test_n11_n13_local_table_without_assessed_provenance_abstains_even_with_empty_effective_features():
    a, ae, g, gx = workforce_like()
    for p in (a, g):
        p["scope"]["closure_artifacts"].append("1TL_L")
        p["canonical_evidence"]["source_contract_classes"]["leaves"].append({**LOCAL, "leaf_kind": "LOCAL_PROVENANCE_NOT_ASSESSED"})
    g["scope"]["evidence_scope"] = {"requested_features": [], "effective_features": []}                 # [] nunca prueba ausencia
    ok, fa, fg = _eq(a, ae, g, None)
    assert "FLOW_MATERIALITY_UNDETERMINED" in fg["undetermined"]


def test_n12_adhoc_without_core_evidence_abstains():
    a, ae, g, gx = workforce_like()
    ae["evidence_observability"] = _obs("EXTRACTOR", {**ALL_OBSERVED, "views": "NOT_OBSERVED"})
    assert "CORE_CATEGORY_NOT_OBSERVED" in ge.fingerprint_v2(a, _bound(a, ae))["undetermined"]


def test_n14_incomplete_closure_abstains():
    a, ae, g, gx = workforce_like()
    g["canonical_evidence"]["closure"]["reference_resolution"]["counts"]["UNRESOLVED"] = 1
    assert "CLOSURE_INCOMPLETE" in ge.fingerprint_v2(g, gx)["undetermined"]


def test_n15_identity_change_is_not_equivalent():
    a, ae, g, gx = workforce_like()
    g["scope"]["terminal_analytic_model"] = g["scope"]["root_object"] = "AM_OTRO"
    assert not _eq(a, ae, g, gx)[0]


@pytest.mark.parametrize("key,value", [("canonical_evidence", "canonical-evidence/v7"), ("closure", "closure-rules/v2"), ("identity", "identity-rules/v2")])
def test_n16_version_changes_are_not_equivalent(key, value):
    a, ae, g, gx = workforce_like()
    g["versions"][key] = value
    assert not _eq(a, ae, g, gx)[0]


def test_n16_platform_boundary_contract_version_change_is_not_equivalent():
    a, ae, g, gx = workforce_like()
    gx["platform_boundary"]["version"] = "platform-boundary/v2"
    assert not _eq(a, ae, g, gx)[0]


# ── procedencia y compatibilidad ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _vp(tmp_path, adhoc_fp, gov, gov_ext):
    return ge.verify_provenance(client_dir="Acme", am="AM_X", declared_spaces=["DEV"], governed_profile=gov, adhoc_fingerprint=adhoc_fp, adhoc_space="DEV",
                                connection_records=[{"space": "DEV", "name": "SSFF", "typeId": "SAPSF"}], catalog_dir=tmp_path, pool_root=None,
                                governed_extensions=gov_ext)


def test_provenance_v2_accredits_the_workforce_like_case(tmp_path):
    a, ae, g, gx = workforce_like()
    out = _vp(tmp_path, ge.fingerprint_v2(a, _bound(a, ae)), g, _bound(g, gx))
    assert out["state"] == ge.ACCREDITED and out["checks"]["fingerprint_schema"] == ge.FINGERPRINT_SCHEMA_V2


def test_provenance_v2_abstains_with_explicit_undetermined_reason(tmp_path):
    a, ae, g, gx = workforce_like()
    g["canonical_evidence"]["closure"]["reference_resolution"]["counts"]["AMBIGUOUS"] = 1
    out = _vp(tmp_path, ge.fingerprint_v2(a, _bound(a, ae)), g, _bound(g, gx))
    assert out["state"] == ge.NOT_ACCREDITED and any(r.startswith("EVIDENCE_SCOPE_UNDETERMINED:") for r in out["reasons"])


def test_c1_a_v1_fingerprint_is_still_interpreted_as_v1(tmp_path):
    a, ae, g, gx = workforce_like()
    out = _vp(tmp_path, ge.fingerprint_v1(a), g, gx)
    assert "fingerprint_schema" not in out["checks"] and "EVIDENCE_NOT_EQUIVALENT" in out["reasons"]       # mismo resultado y estructura que antes (I2)
    same = _vp(tmp_path, ge.fingerprint_v1(g), g, gx)
    assert same["state"] == ge.ACCREDITED                                                                  # v1 idéntica sigue acreditando


def test_c2_schemas_are_never_mixed_and_unknown_schemas_abstain(tmp_path):
    a, ae, g, gx = workforce_like()
    out = _vp(tmp_path, {**ge.fingerprint_v2(a, ae), "schema": "governed-evidence-fingerprint/v9"}, g, gx)
    assert "FINGERPRINT_SCHEMA_UNSUPPORTED" in out["reasons"] and out["state"] == ge.NOT_ACCREDITED
    with pytest.raises(ValueError):
        ge.fingerprint(g, gx, schema="governed-evidence-fingerprint/v9")


def test_c4_approved_expectations_are_verified_under_their_original_contract():
    fresh = {"state": gur.PREVIEW_READY, "provenance": {"state": "ACCREDITED", "digest": "v"}, "plan_digest": "p", "governed_sha256": "s",
             "dependency_signature": "d", "fingerprint": {"equivalence_digest": "e"}, "fingerprint_schema": ge.FINGERPRINT_SCHEMA_V2}
    legacy = {"plan_digest": "p", "provenance_digest": "v", "equivalence_digest": "e", "envelope_sha256": "s", "dependency_signature": "d"}
    reasons, _ = gua._precheck({"request_id": "r"}, {"declared_spaces": ["DEV"], "expected": legacy}, lambda rec, spaces=None: dict(fresh))
    assert reasons == []                                                                                     # aprobación previa a v2: no se le exige el esquema
    reasons, _ = gua._precheck({"request_id": "r"}, {"declared_spaces": ["DEV"], "expected": {**legacy, "fingerprint_schema": ge.FINGERPRINT_SCHEMA_V1}},
                               lambda rec, spaces=None: dict(fresh))
    assert reasons == ["FINGERPRINT_SCHEMA_CHANGED"]


# ── E4: renovación v1 → v2 ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────
V1_FP = {"schema": ge.FINGERPRINT_SCHEMA_V1, "identity": {}, "identity_digest": "1" * 64, "equivalence_digest": "2" * 64}


def _v1_request(**kw):
    rec, _ = _create(**kw)
    with gur._lock:
        fresh = gur.load(rec["request_id"])
        fresh["source"]["fingerprint"] = dict(V1_FP)
        fresh["preview"] = {"state": gur.PREVIEW_READY, "plan_digest": "p" * 64}
        gur._write(fresh)
    return gur.load(rec["request_id"])


def test_c5_an_open_v1_request_is_renewed_by_a_v2_adhoc_and_needs_a_new_dry_run(world):
    old = _v1_request()
    before = json.dumps(old, sort_keys=True)
    new, created = _create()
    assert created and new["supersedes"] == old["request_id"] and new["source"]["fingerprint"]["schema"] == ge.FINGERPRINT_SCHEMA_V2
    assert new["preview"] is None and new.get("approval") is None                                          # dry-run nuevo: no hereda plan ni preview
    retired = gur.load(old["request_id"])
    assert retired["status"] == gur.SUPERSEDED and retired["superseded"]["reason"] == gur.SUPERSEDE_REASON_SCHEMA
    assert retired["source"]["fingerprint"] == V1_FP                                                         # huella histórica intacta
    assert {k: v for k, v in json.loads(before).items() if k not in ("status",)} == {k: v for k, v in retired.items() if k not in ("status", "superseded")}


@pytest.mark.parametrize("state", ["REGISTERED", "VERIFYING", "STARTED", "RECONCILIATION_REQUIRED", "INTERRUPTED"])
def test_c3_a_v1_request_with_an_active_or_uncertain_approval_is_not_renewed(world, state):
    old = _v1_request()
    with gur._lock:
        fresh = gur.load(old["request_id"])
        fresh["approval"] = {"approval_id": "a" * 32, "state": state}
        gur._write(fresh)
    same, created = _create()
    assert not created and same["request_id"] == old["request_id"]


def test_c5_a_current_v2_request_keeps_the_existing_deduplication(world):
    first, c1 = _create()
    second, c2 = _create()
    assert c1 and not c2 and first["request_id"] == second["request_id"] and not gur.is_renewable(first)


# ── E1 / E2: evidencia del motor (sidecar, nunca en el perfil) ────────────────────────────────────────────────────────────────────────────────
def test_e1_markers_only_accredit_names_declared_by_the_same_definition(tmp_path):
    good = tmp_path / "views" / f"{G}.json"
    good.parent.mkdir()
    good.write_text(json.dumps({"definitions": {G: {}}, "timeDimensionSetting": {G: {"timesettings": {"status": "COMPLETED", "granularity": "day"}},
                                                                                  "SAP.TIME.AJENO": {"timesettings": {}}}}), encoding="utf-8")
    plain = tmp_path / "views" / "SAP.OTRO.json"
    plain.write_text(json.dumps({"definitions": {"SAP.OTRO": {}}}), encoding="utf-8")
    marks = pbm._markers([good, plain])
    assert marks == {G: {"basis": TDS, "status": "COMPLETED", "granularity": "day"}}                        # ni el ajeno ni el SAP sin marca


def test_e1_block_publishes_bases_references_and_direct_consumers():
    edge = lambda a, b: SimpleNamespace(provider=b, kind=SimpleNamespace(value="lineage"))              # noqa: E731
    edges = {"AM_X": [edge("AM_X", "3VF_A"), edge("AM_X", G)], "3VF_A": [edge("3VF_A", G)], G: [], "SAP.NS": []}
    graph = SimpleNamespace(edges_of=lambda n: edges.get(n, []),
                            unresolved_of=lambda c: [SimpleNamespace(consumer=G, target=X, kind=SimpleNamespace(value="lineage"))],
                            consumers=lambda n: [k for k, es in edges.items() if any(e.provider == n for e in es)])
    universe = SimpleNamespace(graph=graph, closure_of=lambda r: ("AM_X", "3VF_A", G, "SAP.NS"))
    pbm_markers = pbm._markers
    try:
        pbm._markers = lambda files: {G: {"basis": TDS, "status": "COMPLETED", "granularity": "day"}}
        block = pbm.build_platform_boundary(universe, "AM_X", [])
    finally:
        pbm._markers = pbm_markers
    assert {o["name"]: o["basis"] for o in block["objects"]} == {G: TDS, "SAP.NS": NS}
    assert {(r["from"], r["to"], r["resolution"]) for r in block["references"]} == {("AM_X", G, "RESOLVED"), ("3VF_A", G, "RESOLVED"), (G, X, "PLATFORM_PROVIDED")}
    assert block["direct_consumers"][G] == ["3VF_A", "AM_X"]


def test_e2_observability_distinguishes_observed_not_observed_and_unavailable(tmp_path):
    files = [tmp_path / "views" / "V.json", tmp_path / "remote-tables" / "R.json"]
    ex = eo.build_observability({"requested_features": ["views", "tables", "data-flows"], "effective_features": ["views", "tables"]}, files)
    assert ex["source"] == "EXTRACTOR" and ex["categories"]["data-flows"]["state"] == "NOT_OBSERVED" and ex["categories"]["tables"] == {"state": "OBSERVED", "artifacts": 1}
    pool = eo.build_observability(None, files)
    assert pool["source"] == "POOL" and pool["categories"]["views"]["state"] == "OBSERVED" and pool["categories"]["data-flows"]["state"] == "UNAVAILABLE"


# ── I1: integridad del sidecar (ligado al perfil de la MISMA evaluación) ─────────────────────────────────────────────────────────────────────
def test_i1_the_engine_binds_the_sidecar_to_the_exact_profile_with_the_artifact_signature():
    import hashlib
    from tools.catalog.modeling_assessment import profile as ma_profile
    a, ae, g, gx = workforce_like()
    b = pbm.profile_binding(a)
    assert b == {"method": "sha256:canonical-assessment-json", "digest": hashlib.sha256(ma_profile.canonical_assessment_json(a).encode("utf-8")).hexdigest()}
    assert ge.extensions_bound(a, _bound(a, ae)) and "Acme" not in json.dumps(b)                            # solo un digest: sin datos del modelo


@pytest.mark.parametrize("case", ["ajeno", "alterado", "sin_vinculo", "ausente", "metodo_desconocido"])
def test_i1_a_foreign_altered_or_missing_sidecar_never_accredits(tmp_path, case):
    a, ae, g, gx = workforce_like()
    if case == "ajeno":
        ext = _bound(g, ae)                                                    # sidecar ligado a OTRO perfil (el gobernado)
    elif case == "alterado":
        ext = _bound(a, ae)
        a["scope"]["closure_artifacts"].append("2VD_ALTERADO")                 # el perfil cambió después de ligar el sidecar
    elif case == "sin_vinculo":
        ext = dict(ae)
    elif case == "ausente":
        ext = None
    else:
        ext = {**_bound(a, ae), "profile_binding": {**_bound(a, ae)["profile_binding"], "method": "sha1:otro"}}
    fa = ge.fingerprint_v2(a, ext)
    assert "EVIDENCE_EXTENSIONS_UNBOUND" in fa["undetermined"] and fa["contract_versions"]["platform_boundary"] is None   # nada del sidecar se usó
    out = _vp(tmp_path, fa, g, _bound(g, gx))
    assert out["state"] == ge.NOT_ACCREDITED
    assert any(r.startswith("EVIDENCE_SCOPE_UNDETERMINED:") and "EVIDENCE_EXTENSIONS_UNBOUND" in r for r in out["reasons"])


def test_i1_does_not_require_equal_pool_signatures_between_adhoc_and_reconstruction():
    a, ae, g, gx = workforce_like()
    a["scope"]["pool_signature"], g["scope"]["pool_signature"] = "pool-en-vivo", "pool-del-catalogo"
    assert _eq(a, ae, g, gx)[0]


# ── I2: procedencia v1 idéntica a la histórica ───────────────────────────────────────────────────────────────────────────────────────────────
# Digests calculados con ``verify_provenance`` de c3199af (anterior a v2) sobre ESTA misma evidencia sintética: deben reproducirse exactamente.
V1_GOLDEN = {"adhoc_distinto": ("385d61aac735d20c6cd009ba8594dc9cd57cbf1c533fc11b92259b1220e9555e", "NOT_ACCREDITED"),
             "mismo": ("6960dd824981635c0634ec36fc007506484305b903737b55b0629e62b8d05a5c", "ACCREDITED")}


@pytest.mark.parametrize("case", sorted(V1_GOLDEN))
def test_i2_v1_provenance_digest_and_structure_are_the_historical_ones(tmp_path, case):
    a, ae, g, gx = workforce_like()
    out = ge.verify_provenance(client_dir="Acme", am="AM_X", declared_spaces=["DEV"], governed_profile=g,
                               adhoc_fingerprint=ge.fingerprint_v1(a if case == "adhoc_distinto" else g), adhoc_space="DEV",
                               connection_records=[{"space": "DEV", "name": "SSFF", "typeId": "SAPSF"}], catalog_dir=tmp_path, pool_root=None,
                               governed_extensions=_bound(g, gx))
    assert (out["digest"], out["state"]) == V1_GOLDEN[case]
    assert "fingerprint_schema" not in out["checks"] and "undetermined" not in out["checks"]


def test_i2_a_real_dry_run_of_a_v1_request_is_computed_and_accredited_under_v1(world, monkeypatch):
    """Dry-run REAL (``compute_preview`` completo: migración en modo preview + procedencia) de una solicitud v1: huella gobernada v1 y digest de
    procedencia igual al de ``verify_provenance`` v1 sobre la misma evidencia (el sidecar no interviene)."""
    from webapp.services import assessment_backfill as ab
    from webapp.services import assessment_service as svc
    a, ae, g, gx = workforce_like()
    rec, _ = _create()
    with gur._lock:
        fresh = gur.load(rec["request_id"])
        fresh["source"]["fingerprint"] = ge.fingerprint_v1(g)                 # solicitud histórica v1 (misma evidencia que la reconstrucción)
        gur._write(fresh)
    rec = gur.load(rec["request_id"])
    cand = SimpleNamespace(profile=g, extensions=_bound(g, gx))

    def migrate(entries, migration, *, apply=False, regenerate=None, **kw):
        regenerate(entries[0], migration)
        return {"plan": {"digest": "d" * 64, "items": [{"client": "Contreras", "root": "AM_Workforce"}]}, "records": []}
    monkeypatch.setattr(gur, "_entries_for", lambda c, am: [SimpleNamespace(entry_id="e1", cliente=c)])
    monkeypatch.setattr(svc, "status_for_entry", lambda e: SimpleNamespace(state=svc.CURRENT, pending_migration="evidence_source_application_api_v6", stale_targeted=None))
    monkeypatch.setattr(svc, "regenerate_stale_evidence_replay", lambda e, m: cand)
    monkeypatch.setattr(ab, "migrate_stale_evidence", migrate)
    monkeypatch.setattr(svc, "connection_records_of", lambda c: [{"space": "DEV", "name": "SSFF", "typeId": "SAPSF"}])
    monkeypatch.setattr(svc, "pool_root", lambda: None)
    monkeypatch.setattr(svc, "closure_signature", lambda res: "dep")
    monkeypatch.setattr(svc, "resolve_entry", lambda e: e)
    out = gur.compute_preview(rec, spaces=["DEV"])
    assert out["fingerprint_schema"] == ge.FINGERPRINT_SCHEMA_V1 and out["fingerprint"]["schema"] == ge.FINGERPRINT_SCHEMA_V1
    assert out["fingerprint"]["equivalence_digest"] == rec["source"]["fingerprint"]["equivalence_digest"]
    prov = out["provenance"]
    assert prov["state"] == ge.ACCREDITED and "fingerprint_schema" not in prov["checks"]
    expected = ge.verify_provenance(client_dir="Contreras", am="AM_Workforce", declared_spaces=["DEV"], governed_profile=g,
                                    adhoc_fingerprint=rec["source"]["fingerprint"], adhoc_space="DEV",
                                    connection_records=[{"space": "DEV", "name": "SSFF", "typeId": "SAPSF"}], catalog_dir=svc.catalog_dir(), pool_root=None)
    assert prov["digest"] == expected["digest"]
