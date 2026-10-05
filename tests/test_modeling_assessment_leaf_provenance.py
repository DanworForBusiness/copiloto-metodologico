"""
G1 (Gate 4) — procedencia canonica de hojas locales (``source.leaf_provenance``).

Una hoja local sin ``remote_entity`` gana procedencia ESTABLECIDA solo por un productor (RF/DF) que la declara como DESTINO; DECLARADA por una
anotacion del propio artefacto; NOT_OBSERVABLE solo con alcance DECLARADO y completo; si no, NOT_ASSESSED. La evidencia de Procesamiento satisface
SOLO esta superficie. Sin inferencia por nombre; los Task Chains no son procedencia; los Transformation Flows no se interpretan.
"""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

import pytest

from tools.catalog.modeling_assessment import profile as ma_profile
from tools.catalog.modeling_assessment import shadow as ma_shadow
from tools.catalog.modeling_assessment.provenance import resolve_leaf_provenance
from tools.catalog.modeling_assessment.readers import SupplementaryEvidence

AM = "4AM_X"
ALL_PRODUCERS = ["data-flows", "replication-flows", "transformation-flows"]
SCOPE_ALL = {"effective_features": ALL_PRODUCERS, "requested_features": ALL_PRODUCERS}


def _w(root: Path, rel: str, body) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")


def _local(name: str, **annotations) -> dict:
    return {"definitions": {name: {"kind": "entity", "elements": {"A": {"type": "cds.String"}}, **annotations}}}


def _rf(name: str, targets: list[str], source_objects: list[str], *, container: str = "/CDS_EXTRACTION", load: str = "REPLICATE") -> dict:
    return {"replicationflows": {name: {
        "kind": "sap.dis.replicationflow",
        "contents": {"sourceSystem": [{"connectionId": "S4_HANA", "connectionType": "SAPS4HANAOP", "container": container}],
                     "replicationTasks": [{"name": "t1", "loadType": load, "sourceObject": {"name": so}, "targetObject": {"name": tg}}
                                          for so, tg in zip(source_objects, targets)]},
        "sources": {}, "targets": {t: {"elements": {}} for t in targets}}}}


def _df(name: str, sources: list[str], targets: list[str]) -> dict:
    return {"dataflows": {name: {"kind": "sap.dis.dataflow", "contents": {}, "sources": {s: {} for s in sources}, "targets": {t: {} for t in targets}}}}


def _pool(leaves: dict[str, dict | None] | None = None, extra: dict[str, object] | None = None) -> Path:
    """AM <- VF <- {leaves}. ``leaves``: nombre -> anotaciones del local table (None = tabla manual sin anotaciones)."""
    leaves = {"LOCAL_X": None} if leaves is None else leaves
    root = Path(tempfile.mkdtemp(prefix="leafprov_"))
    cols = {"A": {"type": "cds.String", "@EndUserText.label": "A"}}
    for n, ann in leaves.items():
        _w(root, f"Acme/local-tables/{n}.json", _local(n, **(ann or {})))
    frm = {"ref": [next(iter(leaves))]} if len(leaves) == 1 else {"join": "inner", "args": [{"ref": [n]} for n in leaves], "on": []}
    _w(root, "Acme/views/3VF_X.json", {"definitions": {"3VF_X": {"kind": "entity", "elements": cols, "@ObjectModel.modelingPattern": {"#": "ANALYTICAL_FACT"},
        "query": {"SELECT": {"from": frm, "columns": [{"ref": [next(iter(leaves)), "A"], "as": "A"}]}}}}})
    _w(root, f"Acme/analytic-models/{AM}.json", {"definitions": {AM: {"kind": "entity", "elements": cols, "@ObjectModel.modelingPattern": {"#": "ANALYTICAL_CUBE"},
        "@Analytics.dataCategory": {"#": "CUBE"}, "query": {"SELECT": {"from": {"ref": ["3VF_X"]}, "columns": [{"ref": ["3VF_X", "A"], "as": "A"}]}}}}})
    for rel, body in (extra or {}).items():
        _w(root, rel, body)
    return root


def _doc(root: Path, scope: dict | None = None) -> dict:
    kw = {"evidence_scope": scope} if scope else {}
    doc, _ = ma_shadow.assess(root, "Acme", AM, "General", **kw)
    return doc


def _leaf(doc: dict, name: str = "LOCAL_X") -> dict:
    return next(l for l in doc["canonical_evidence"]["leaf_provenance"] if l["leaf"] == name)


def _evidence(root: Path, scope: dict | None = None) -> SupplementaryEvidence:
    files = sorted(root.rglob("*.json"))
    return SupplementaryEvidence.from_files(files, root=root, effective_features=(scope or {}).get("effective_features") if scope else None)


class _E:                                                         # entidad minima del loader
    def __init__(self, remote_entity: str = ""):
        self.remote_entity = remote_entity


# ── 1. RF destination resolves the leaf provenance ───────────────────────────────────────────────
def test_replication_flow_destination_resolves_leaf_provenance_with_origin_and_load_type():
    doc = _doc(_pool(extra={"Acme/replication-flows/RF_X.json": _rf("RF_X", ["LOCAL_X"], ["ZCDS_SRC"], load="INITIAL")}))
    leaf = _leaf(doc)
    assert (leaf["provenance_state"], leaf["basis"], leaf["assessability"], leaf["chain_complete"]) == ("ESTABLISHED", "REPLICATION_FLOW", "ASSESSED", True)
    assert leaf["origin"] == {"systems": [{"connection_id": "S4_HANA", "connection_type": "SAPS4HANAOP", "container": "/CDS_EXTRACTION"}],
                              "source_objects": ["ZCDS_SRC"]}
    assert leaf["producer_flows"] == [{"kind": "REPLICATION_FLOW", "name": "RF_X", "role": "target", "target_match": "EXACT", "load_types": ["INITIAL"]}]


def test_the_documented_change_capture_suffix_rule_is_recorded_as_such_not_as_an_exact_match():
    doc = _doc(_pool(extra={"Acme/replication-flows/RF_X.json": _rf("RF_X", ["LOCAL_X_Delta"], ["ZCDS_SRC"])}))
    leaf = _leaf(doc)
    assert leaf["provenance_state"] == "ESTABLISHED" and leaf["producer_flows"][0]["target_match"] == "DELTA_SHADOW_SUFFIX"


# ── 2. DF provenance resolves recursively where deterministic ────────────────────────────────────
def test_data_flow_chain_resolves_recursively_through_local_tables_to_the_replication_origin():
    root = _pool({"LOCAL_B": None, "LOCAL_A": None}, {
        "Acme/replication-flows/RF_A.json": _rf("RF_A", ["LOCAL_A"], ["ZCDS_A"]),
        "Acme/data-flows/DF_B.json": _df("DF_B", ["LOCAL_A"], ["LOCAL_B"])})
    doc = _doc(root)
    b = _leaf(doc, "LOCAL_B")
    assert (b["provenance_state"], b["basis"], b["chain_complete"]) == ("ESTABLISHED", "DATA_FLOW", True)
    assert b["upstream"] == [{"entity": "LOCAL_A", "kind": "LOCAL_TABLE", "provenance_state": "ESTABLISHED", "basis": "REPLICATION_FLOW", "remote_entity": None,
                          "chain_complete": True}]


def test_data_flow_chain_is_incomplete_when_it_terminates_in_a_datasphere_view_or_outside_the_pool():
    root = _pool({"LOCAL_B": None}, {"Acme/data-flows/DF_B.json": _df("DF_B", ["3VF_X", "NOT_IN_POOL"], ["LOCAL_B"])})
    b = _leaf(_doc(root), "LOCAL_B")
    assert b["provenance_state"] == "ESTABLISHED" and b["chain_complete"] is False                # la produccion de la hoja esta establecida; el origen ultimo no
    assert {u["entity"]: u["kind"] for u in b["upstream"]} == {"3VF_X": "DATASPHERE_VIEW", "NOT_IN_POOL": "OUTSIDE_POOL"}


def test_data_flow_chain_terminates_in_a_remote_table_with_its_remote_entity():
    root = _pool({"LOCAL_B": None}, {
        "Acme/remote-tables/REM.json": {"definitions": {"REM": {"kind": "entity", "@DataWarehouse.remote.connection": "S4PREM",
                                                                  "@DataWarehouse.remote.entity": "ABAP_CDS.REM$P", "elements": {"A": {"type": "cds.String"}}}}},
        "Acme/data-flows/DF_B.json": _df("DF_B", ["REM"], ["LOCAL_B"])})
    ev = _evidence(root)
    rec = resolve_leaf_provenance("LOCAL_B", ev, {"REM": _E("ABAP_CDS.REM$P"), "LOCAL_B": _E()})
    assert rec["chain_complete"] is True and rec["upstream"][0]["kind"] == "REMOTE_TABLE" and rec["upstream"][0]["remote_entity"] == "ABAP_CDS.REM$P"


def test_data_flow_cycles_do_not_recurse_forever():
    root = _pool({"LOCAL_A": None, "LOCAL_B": None}, {"Acme/data-flows/DF_1.json": _df("DF_1", ["LOCAL_B"], ["LOCAL_A"]),
                                                      "Acme/data-flows/DF_2.json": _df("DF_2", ["LOCAL_A"], ["LOCAL_B"])})
    a = _leaf(_doc(root), "LOCAL_A")
    assert a["provenance_state"] == "ESTABLISHED" and a["chain_complete"] is False


# ── 3. declared dataset is DECLARED, never invented ESTABLISHED ──────────────────────────────────
def test_declared_dataset_is_declared_and_never_promoted_to_established():
    ann = {"@DataWarehouse.dataSource.schema": "DEV#SCHEMA", "@DataWarehouse.dataSource.entity": "sap.dataset.conf_abc_view"}
    leaf = _leaf(_doc(_pool({"LOCAL_X": ann})))
    assert (leaf["provenance_state"], leaf["basis"], leaf["assessability"]) == ("DECLARED", "DECLARED_DATASET", "ASSESSED")
    assert leaf["declaration"] == {"kind": "DECLARED_DATASET", "data_source_schema": "DEV#SCHEMA", "data_source_entity": "sap.dataset.conf_abc_view"}
    assert leaf["producer_flows"] == [] and leaf["origin"] is None


def test_table_function_is_declared_with_its_own_basis():
    leaf = _leaf(_doc(_pool({"LOCAL_X": {"@DataWarehouse.tableFunction.script": "RETURN 1"}})))
    assert (leaf["provenance_state"], leaf["basis"]) == ("DECLARED", "TABLE_FUNCTION")


def test_a_producer_flow_takes_priority_over_a_declaration():
    ann = {"@DataWarehouse.dataSource.entity": "sap.dataset.x"}
    root = _pool({"LOCAL_X": ann}, {"Acme/replication-flows/RF_X.json": _rf("RF_X", ["LOCAL_X"], ["ZCDS_SRC"])})
    assert _leaf(_doc(root))["provenance_state"] == "ESTABLISHED"


# ── 4/5/6. conclusive absence is an assessed result ONLY with a complete declared scope ─────────
def test_manual_leaf_with_a_conclusive_scope_is_assessed_not_observable():
    doc = _doc(_pool(), SCOPE_ALL)
    leaf = _leaf(doc)
    assert (leaf["provenance_state"], leaf["basis"], leaf["assessability"]) == ("NOT_OBSERVABLE", "NONE", "ASSESSED")
    assert leaf["scope"] == {"declared": True, "producer_families_acquired": sorted(ALL_PRODUCERS), "conclusive": True, "limitation": None}
    assert not [l for l in doc["evidence_limitations"] if l["surface_id"] == "source.leaf_provenance"]


def test_the_same_manual_leaf_with_incomplete_acquisition_remains_not_assessed():
    for scope in (None, {"effective_features": ["replication-flows", "data-flows"], "requested_features": []}):
        doc = _doc(_pool(), scope)
        leaf = _leaf(doc)
        assert leaf["provenance_state"] is None and leaf["assessability"] == "NOT_ASSESSED" and leaf["scope"]["conclusive"] is False
        assert leaf["cause_code"] == "SUPPLEMENTARY_EVIDENCE_NOT_PROVIDED"


@pytest.mark.parametrize("missing", ALL_PRODUCERS)
def test_a_missing_producer_family_never_becomes_a_false_not_observable(missing):
    scope = {"effective_features": [f for f in ALL_PRODUCERS if f != missing], "requested_features": []}
    leaf = _leaf(_doc(_pool(), scope))
    assert leaf["provenance_state"] is None and leaf["scope"]["limitation"] == "PRODUCER_FAMILY_NOT_ACQUIRED"


def test_scope_completeness_is_never_inferred_from_folders_or_names():
    # el pool trae carpetas con nombres de las tres familias, pero la extraccion NO declaro alcance
    root = _pool(extra={"Acme/replication-flows/RF_OTHER.json": _rf("RF_OTHER", ["OTHER"], ["S"]),
                        "Acme/data-flows/DF_OTHER.json": _df("DF_OTHER", ["S"], ["OTHER"]),
                        "Acme/transformation-flows/TF_OTHER.json": {"transformationflows": {}}})
    leaf = _leaf(_doc(root))
    assert leaf["provenance_state"] is None and leaf["scope"] == {"declared": False, "producer_families_acquired": [], "conclusive": False, "limitation": "SCOPE_NOT_DECLARED"}


def test_transformation_flow_files_in_the_pool_prevent_a_conclusive_absence_even_with_a_full_scope():
    root = _pool(extra={"Acme/transformation-flows/TF_Y.json": {"transformationflows": {"TF_Y": {"sources": {"LOCAL_X": {}}, "targets": {"LOCAL_Y": {}}}}}})
    leaf = _leaf(_doc(root, SCOPE_ALL))
    assert leaf["provenance_state"] is None and leaf["scope"]["limitation"] == "UNINTERPRETED_PRODUCER_EVIDENCE"


def test_an_unreadable_pool_file_prevents_a_conclusive_absence():
    root = _pool(extra={"Acme/views/BROKEN.json": "{ no es json"})
    leaf = _leaf(_doc(root, SCOPE_ALL))
    assert leaf["provenance_state"] is None and leaf["scope"]["limitation"] == "UNREADABLE_POOL_FILES"


def test_a_transformation_flow_targeting_the_leaf_is_not_interpreted_as_provenance():
    root = _pool(extra={"Acme/transformation-flows/TF_Y.json": {"transformationflows": {"TF_Y": {"sources": {"S": {}}, "targets": {"LOCAL_X": {}}}}}})
    leaf = _leaf(_doc(root, SCOPE_ALL))
    assert leaf["provenance_state"] != "ESTABLISHED" and leaf["basis"] == "NONE" and leaf["producer_flows"] == []


# ── 7. no name-based provenance inference ───────────────────────────────────────────────────────
def test_names_resembling_a_flow_or_its_target_never_establish_provenance():
    root = _pool({"LOCAL_X": None, "LOCAL_X2": None}, {
        "Acme/replication-flows/RF_LOCAL_X.json": _rf("RF_LOCAL_X", ["SOMETHING_ELSE"], ["ZCDS_SRC"]),         # el flujo se LLAMA como la hoja, pero no la produce
        "Acme/data-flows/DF_LOCAL_X2.json": _df("DF_LOCAL_X2", ["LOCAL_X2"], ["ELSEWHERE"])})                   # la hoja es FUENTE del flujo, no destino
    doc = _doc(root, SCOPE_ALL)
    for n in ("LOCAL_X", "LOCAL_X2"):
        assert _leaf(doc, n)["provenance_state"] == "NOT_OBSERVABLE" and _leaf(doc, n)["producer_flows"] == []


# ── 8/9. processing evidence satisfies the provenance surface only; unrelated Modeling facts unchanged ──
def test_processing_evidence_satisfies_the_provenance_surface_only():
    plain = _doc(_pool(), SCOPE_ALL)
    with_rf = _doc(_pool(extra={"Acme/replication-flows/RF_X.json": _rf("RF_X", ["LOCAL_X"], ["ZCDS_SRC"])}), SCOPE_ALL)
    surf = lambda d: {s["surface_id"]: s["assessability"] for s in d["surfaces"]}
    changed = {k for k in surf(plain) if surf(plain)[k] != surf(with_rf)[k]}
    assert changed <= {"source.leaf_provenance", "ingestion.processing_reference", "source.physical_boundary"}                                # + la frontera fisica que revela la cadena (complemento de procedencia)
    for area in ("ANALYTIC_MODEL", "PROPAGATION", "HARMONIZATION", "NAMING"):
        a = next(x for x in plain["areas"] if x["id"] == area)
        b = next(x for x in with_rf["areas"] if x["id"] == area)
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True), area
    keep = lambda d: [f for f in d["fact_index"] if not f["fact"].startswith(("source.leaf_provenance", "source.physical_boundary", "ingestion.processing", "ingestion.dataflow", "ingestion.flow_load"))]
    assert keep(plain) == keep(with_rf)


def test_establishing_provenance_changes_only_the_leaf_and_the_rule_inputs_it_feeds():
    base = _doc(_pool())                                                    # alcance no declarado: hoja NOT_ASSESSED
    est = _doc(_pool(extra={"Acme/replication-flows/RF_X.json": _rf("RF_X", ["LOCAL_X"], ["ZCDS_SRC"])}))
    surf = lambda d: {s["surface_id"]: s["assessability"] for s in d["surfaces"]}
    changed = {k for k in surf(base) if surf(base)[k] != surf(est)[k]}
    assert changed <= {"source.leaf_provenance", "ingestion.processing_reference", "rule.direct_raw_table_access", "rule.duplicated_remote_import",
                       "rule.layer_boundary_review", "source.physical_boundary"}                                                             # + el complemento cross-plane que CITA el flujo
    assert "source.leaf_provenance" in changed
    for key in ("observations", "review_required", "descriptive_signals"):
        assert json.dumps(base[key], sort_keys=True) == json.dumps(est[key], sort_keys=True), key


# ── 10. Task Chain presence alone does not establish provenance ─────────────────────────────────
def test_task_chain_presence_alone_never_establishes_provenance():
    tc = {"taskchains": {"TC_X": {"nodes": [{"name": "RF_X"}, {"name": "LOCAL_X"}], "targets": {"LOCAL_X": {}}}}}
    for scope in (None, SCOPE_ALL):
        leaf = _leaf(_doc(_pool(extra={"Acme/task-chains/TC_X.json": tc}), scope))
        assert leaf["provenance_state"] in (None, "NOT_OBSERVABLE") and leaf["basis"] == "NONE" and leaf["producer_flows"] == []


# ── 11. duplicate source identities are not created ─────────────────────────────────────────────
def test_flow_origins_never_create_remote_identities_or_duplicate_import_findings():
    remote = {"Acme/remote-tables/REM.json": {"definitions": {"REM": {"kind": "entity", "@DataWarehouse.remote.connection": "S4_HANA",
                                                                       "@DataWarehouse.remote.entity": "ABAP_CDS.ZCDS_SRC$P", "elements": {"A": {"type": "cds.String"}}}}}}
    base = _doc(_pool({"LOCAL_X": None}, dict(remote)), SCOPE_ALL)
    with_rf = _doc(_pool({"LOCAL_X": None}, {**remote, "Acme/replication-flows/RF_X.json": _rf("RF_X", ["LOCAL_X"], ["ZCDS_SRC"])}), SCOPE_ALL)
    ids = lambda d: [f["fact"] for f in d["fact_index"] if f["fact"].startswith("source.contract@")]
    assert ids(base) == ids(with_rf) and not any("LOCAL_X" in x for x in ids(with_rf))
    assert not [g for g in with_rf["review_required"] if g["id"] == "DUPLICATED_REMOTE_IMPORT"]


# ── 12. historical / root-based behavior outside provenance remains stable ──────────────────────
def test_old_profiles_without_canonical_evidence_stay_valid_and_the_generic_root_still_assesses():
    doc = _doc(_pool(extra={"Acme/replication-flows/RF_X.json": _rf("RF_X", ["LOCAL_X"], ["ZCDS_SRC"])}))
    old = copy.deepcopy(doc)
    old.pop("canonical_evidence")
    old["versions"].pop("canonical_evidence")                       # un perfil historico no declara la version
    ma_profile.validate_assessment(old)
    root = _pool(extra={"Acme/replication-flows/RF_X.json": _rf("RF_X", ["LOCAL_X"], ["ZCDS_SRC"])})
    gdoc, _ = ma_shadow.assess(root, "Acme", "3VF_X", "General", generic_root=True, root_type="view")
    ma_profile.validate_assessment(gdoc)
    assert gdoc["scope"]["root_object"] == "3VF_X" and _leaf(gdoc)["provenance_state"] == "ESTABLISHED"


def test_the_versions_block_carries_the_evidence_contract_so_old_profiles_become_stale():
    doc = _doc(_pool())
    assert doc["versions"]["canonical_evidence"] == "canonical-evidence/v2" and doc["versions"]["closure"] == "closure-rules/v1"
    assert doc["versions"]["assessability"] == "assessability-rules/v3" and doc["versions"]["signals"] == "signal-rules/v3"
    assert doc["versions"]["finding_evidence"] == "finding-evidence/v2"
