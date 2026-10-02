"""M2 — lectores SUPLEMENTARIOS de evidencia cruda: solo lectura, aislados del legacy, sin inferir lo ausente."""
from __future__ import annotations

import json
import random

from tests._modeling_assessment_m2_fixtures import WHERE_LANG, write_pool_files
from tools.catalog.modeling_assessment.readers import (
    DATA_FLOW, REPLICATION_FLOW, SupplementaryEvidence,
)


def _ev(tmp_path, **kw):
    files = write_pool_files(tmp_path, **kw)
    return SupplementaryEvidence.from_files(files, root=tmp_path), files


def test_remote_table_evidence_preserves_the_raw_query_filter_verbatim(tmp_path):
    ev, _ = _ev(tmp_path)
    rt = ev.remote_table("RAW_T")
    assert rt is not None and rt.has_remote_query is True
    assert rt.query_where == WHERE_LANG                              # `where` VERBATIM, sin interpretar
    assert rt.query_column_count == 0
    assert (rt.remote_connection, rt.remote_entity) == ("S4PREM", "ABAPTABLES.TAB1")
    assert rt.supported_capabilities == ("DATA_STRUCTURE", "SQL_DATA_SOURCE")
    assert rt.modeling_pattern == "DATA_STRUCTURE"
    assert rt.source_file == "remote-tables/RAW_T.json"              # relativa a `root` (L6)


def test_remote_table_without_query_reports_absence_explicitly_not_an_empty_filter(tmp_path):
    ev, _ = _ev(tmp_path)
    rt = ev.remote_table("RAW_T2")
    assert rt.has_remote_query is False and rt.query_where is None and rt.query_column_count is None


def test_annotation_inventory_lists_artifact_level_annotations_only(tmp_path):
    ev, _ = _ev(tmp_path)
    keys = ev.remote_table("RAW_T").annotation_keys
    assert keys == tuple(sorted(keys)) and "@DataWarehouse.remote.query" in keys and "@ObjectModel.modelingPattern" in keys
    assert "elements" not in keys and "kind" not in keys


def test_replication_like_annotations_are_detected_only_when_present(tmp_path):
    absent, _ = _ev(tmp_path / "a")
    assert all(t.replication_annotation_keys == () for t in absent.remote_tables.values())
    present, _ = _ev(tmp_path / "b", replication_annotation=True)
    assert present.remote_table("RAW_T").replication_annotation_keys == ("@DataWarehouse.replication.mode",)


def test_non_remote_definitions_are_not_remote_tables_but_are_still_provenance(tmp_path):
    ev, _ = _ev(tmp_path)
    assert ev.remote_table("LOCAL_T") is None                          # no hay evidencia remota: NO se fabrica
    assert ev.files_defining("LOCAL_T") == ("local-tables/LOCAL_T.json",)      # L6: archivos que la definen


def test_entity_defined_in_several_files_keeps_every_file(tmp_path):
    a = tmp_path / "a" / "X.json"
    b = tmp_path / "b" / "X.json"
    for p in (a, b):
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"definitions": {"X": {"kind": "entity"}}}), encoding="utf-8")
    ev = SupplementaryEvidence.from_files([b, a], root=tmp_path)
    assert ev.files_defining("X") == ("a/X.json", "b/X.json")            # el loader solo conserva el primero


def test_replication_flow_reference_and_delta_shadow_resolution(tmp_path):
    ev, _ = _ev(tmp_path)
    (rf,) = [f for f in ev.flows if f.kind == REPLICATION_FLOW]
    assert rf.name == "RF_LOCAL" and rf.target_entities == ("LOCAL_T_Delta",) and rf.source_entities == ("LOCAL_T",)
    assert [(s.connection_id, s.connection_type, s.container) for s in rf.source_systems] == [
        ("S4PREM", "SAPS4HANAOP", "/CDS_EXTRACTION")]
    roles = [(fl.name, role) for fl, role in ev.flows_touching("LOCAL_T")]
    assert roles == [("RF_LOCAL", "target")]                            # `LOCAL_T_Delta` -> tabla local `LOCAL_T`
    # el `sourceObject` de un RF vive en el sistema de ORIGEN: un nombre coincidente NO es la entidad de Datasphere
    assert rf.source_entities == ("LOCAL_T",) and ev.flows_touching("LOCAL_T")[0][1] != "source"
    assert ev.flows_touching("LOCAL_U") == ()


def test_data_flow_reference_sources_and_targets(tmp_path):
    ev, _ = _ev(tmp_path)
    (df,) = [f for f in ev.flows if f.kind == DATA_FLOW]
    assert df.source_entities == ("VR_P",) and df.target_entities == ("DF_T",) and df.source_systems == ()
    assert [(f.name, r) for f, r in ev.flows_touching("VR_P")] == [("DF_X", "source")]
    assert [(f.name, r) for f, r in ev.flows_touching("DF_T")] == [("DF_X", "target")]


def test_unreadable_files_are_recorded_explicitly_never_swallowed_or_guessed(tmp_path):
    ev, _ = _ev(tmp_path)
    reasons = {i.source_file: i.reason for i in ev.issues}
    assert "views/INVALID.json" in reasons and "views/LIST_ROOT.json" in reasons
    assert "raiz JSON no es objeto" in reasons["views/LIST_ROOT.json"]
    assert ev.available is True and ev.files_read == len(write_pool_files(tmp_path / "x")) - 2


def test_empty_evidence_is_explicitly_unavailable():
    ev = SupplementaryEvidence.empty()
    assert ev.available is False and ev.remote_table("X") is None and ev.flows_touching("X") == () and ev.files_defining("X") == ()


def test_missing_file_is_an_issue_not_a_crash(tmp_path):
    ev = SupplementaryEvidence.from_files([tmp_path / "no_existe.json"], root=tmp_path)
    assert [i.source_file for i in ev.issues] == ["no_existe.json"] and ev.available is False


def test_reading_is_deterministic_and_never_writes(tmp_path):
    files = write_pool_files(tmp_path)
    before = {p: p.read_bytes() for p in files}
    a = SupplementaryEvidence.from_files(files, root=tmp_path)
    shuffled = files[:]
    random.Random(3).shuffle(shuffled)
    b = SupplementaryEvidence.from_files(shuffled, root=tmp_path)
    assert (a.remote_tables, a.flows, a.issues) == (b.remote_tables, b.flows, b.issues)
    assert {p: p.read_bytes() for p in files} == before                 # solo lectura


def test_paths_outside_root_fall_back_to_the_file_name(tmp_path):
    other = tmp_path / "outside" / "Y.json"
    other.parent.mkdir()
    other.write_text(json.dumps({"definitions": {"Y": {"kind": "entity"}}}), encoding="utf-8")
    ev = SupplementaryEvidence.from_files([other], root=tmp_path / "inside")
    assert ev.files_defining("Y") == ("Y.json",)
