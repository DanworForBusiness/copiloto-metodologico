from __future__ import annotations

from pathlib import Path

import pytest

from tools.catalog import DSP_CLI_Export_Lineage as dsp


class FakeCli:
    def __init__(self, inventories=None, objects=None, diagnostics=None):
        self.inventories = inventories or {}
        self.objects = objects or {}
        self.list_calls = []
        self.read_calls = []
        self.diagnostics = diagnostics or dsp.ExtractionDiagnostics(False)

    def list_objects(self, space, object_type):
        self.list_calls.append(object_type)
        return [{"technicalName": x} for x in self.inventories.get(object_type, [])]

    def read_object(self, space, object_type, technical_name):
        self.read_calls.append((object_type, technical_name))
        return self.objects.get((object_type, technical_name), {})


def root_inventory(root_type: str, name: str = "ROOT"):
    inv = {t: [] for t in dsp.ROOT_OBJECT_TYPES}
    inv[dsp.ROOT_TYPE_TO_OBJECT_TYPE[root_type]] = [name]
    return inv


@pytest.mark.parametrize("root_type", sorted(dsp.SUPPORTED_ROOT_TYPES))
def test_detects_all_supported_root_types(tmp_path, root_type):
    cli = FakeCli(root_inventory(root_type))
    exporter = dsp.LineageExporter(cli, "S", output_dir=tmp_path, root_object="ROOT")
    assert exporter.detect_root_type() == root_type


def test_root_not_found(tmp_path):
    cli = FakeCli({t: [] for t in dsp.ROOT_OBJECT_TYPES})
    exporter = dsp.LineageExporter(cli, "S", output_dir=tmp_path, root_object="MISSING")
    with pytest.raises(dsp.DatasphereCliError, match="ROOT_OBJECT_NOT_FOUND"):
        exporter.detect_root_type()


def test_ambiguous_root(tmp_path):
    inv = {t: [] for t in dsp.ROOT_OBJECT_TYPES}
    inv["views"] = ["X"]
    inv["local-tables"] = ["X"]
    cli = FakeCli(inv)
    exporter = dsp.LineageExporter(cli, "S", output_dir=tmp_path, root_object="X")
    with pytest.raises(dsp.DatasphereCliError, match="AMBIGUOUS_ROOT_OBJECT_TYPE"):
        exporter.detect_root_type()


def test_export_request_root_and_legacy_contracts():
    canonical = dsp.ExportRequest(host="h", client_id="id", client_secret="secret", root_object="V")
    legacy = dsp.ExportRequest(host="h", client_id="id", client_secret="secret", analytic_model="AM")
    assert canonical.root_object == "V"
    assert legacy.analytic_model == "AM"
    assert "secret" not in repr(canonical)


def test_argument_conflict(tmp_path):
    cli = FakeCli()
    with pytest.raises(dsp.DatasphereCliError, match="ROOT_ARGUMENT_CONFLICT"):
        dsp.LineageExporter(cli, "S", analytic_model="AM", root_object="V", output_dir=tmp_path)


@pytest.mark.parametrize("root_type", ["view", "local-table", "remote-table"])
def test_constraint_rejects_non_am_before_deep_work(tmp_path, root_type):
    diagnostics = dsp.ExtractionDiagnostics(True)
    cli = FakeCli(root_inventory(root_type), diagnostics=diagnostics)
    exporter = dsp.LineageExporter(
        cli,
        "S",
        output_dir=tmp_path,
        root_object="ROOT",
        allowed_root_types={"analytic-model"},
        diagnostics=diagnostics,
    )
    with pytest.raises(dsp.RootTypeConstraintError, match="ROOT_TYPE_CONSTRAINT_FAILED"):
        exporter.run()
    assert cli.read_calls == []
    assert not list(tmp_path.rglob("*.json"))
    phases = diagnostics.snapshot()["by_phase"]
    assert "ROOT / INVENTORY" in phases
    assert "LINEAGE" not in phases
    assert "PRODUCER DISCOVERY" not in phases
    assert "TASK CHAINS" not in phases
    assert "WRITE / EXPORT" not in phases


def test_constraint_allows_analytic_model(tmp_path):
    cli = FakeCli(root_inventory("analytic-model"), {("analytic-models", "ROOT"): {}})
    exporter = dsp.LineageExporter(
        cli,
        "S",
        output_dir=tmp_path,
        root_object="ROOT",
        requested_features={"analytic-models"},
        allowed_root_types={"analytic-model"},
    )
    exporter.run()
    assert ("analytic-models", "ROOT") in exporter.nodes


@pytest.mark.parametrize("root_type", ["view", "local-table", "remote-table"])
def test_none_constraint_preserves_generic_extraction(tmp_path, root_type):
    object_type = dsp.ROOT_TYPE_TO_OBJECT_TYPE[root_type]
    body = {}
    if root_type == "view":
        body = {"definitions": {"ROOT": {"@DataWarehouse.sqlEditor.query": "SELECT 1"}}}
    cli = FakeCli(root_inventory(root_type), {(object_type, "ROOT"): body})
    features = {"views"} if root_type == "view" else {"tables"}
    exporter = dsp.LineageExporter(
        cli,
        "S",
        output_dir=tmp_path,
        root_object="ROOT",
        requested_features=features,
        allowed_root_types=None,
    )
    exporter.run()
    assert (object_type, "ROOT") in exporter.nodes
    assert (tmp_path / object_type / "ROOT.json").exists()


def test_allowed_root_types_normalized_and_validated_once_at_exporter_boundary(tmp_path):
    cli = FakeCli(root_inventory("view"))
    exporter = dsp.LineageExporter(
        cli,
        "S",
        output_dir=tmp_path,
        root_object="ROOT",
        allowed_root_types=["view", "analytic-model", "view"],
    )
    assert exporter.allowed_root_types == frozenset({"view", "analytic-model"})
    with pytest.raises(dsp.DatasphereCliError, match="INVALID_ROOT_TYPE_CONSTRAINT"):
        dsp.LineageExporter(
            cli,
            "S",
            output_dir=tmp_path,
            root_object="ROOT",
            allowed_root_types={"bogus"},
        )


def test_diagnostics_distinguish_logical_read_and_cache_hit(tmp_path):
    diagnostics = dsp.ExtractionDiagnostics(True)
    cli = FakeCli(root_inventory("view"), {("views", "ROOT"): {"x": 1}}, diagnostics)
    exporter = dsp.LineageExporter(
        cli, "S", output_dir=tmp_path, root_object="ROOT", diagnostics=diagnostics
    )
    assert exporter.safe_read("views", "ROOT") == {"x": 1}
    assert exporter.safe_read("views", "ROOT") == {"x": 1}
    family = diagnostics.snapshot()["by_family"]["views"]
    assert family["logical_reads"] == 2
    assert family["duplicate_logical_reads"] == 1
    assert family["cache_hits"] == 1
    assert family["reused_parsed_objects"] == 1
    assert len(cli.read_calls) == 1


def test_classify_root_constraint_error():
    assert dsp.classify_error("ROOT_TYPE_CONSTRAINT_FAILED - x") == "ROOT_TYPE_CONSTRAINT_FAILED"


def test_capture_output_streaming():
    seen = []
    with dsp.capture_output(on_line=seen.append):
        dsp._say("hello")
    assert seen == ["hello"]


def test_export_result_has_diagnostics_contract():
    result = dsp.ExportResult(ok=True, diagnostics={"enabled": True})
    assert result.diagnostics == {"enabled": True}
