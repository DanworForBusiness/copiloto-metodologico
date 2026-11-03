"""Task Chains con pasos sobre Remote Tables: reconocimiento ESTRUCTURAL en el exportador y cobertura real del escaneo (`taskChainScan`).

Forma verificada en una definición real (un Space DEV con 23 pasos `REMOTE_TABLES / REPLICATE`): `taskchains.<TC>.nodes[]` con `type: TASK` y
`taskIdentifier {applicationId, activity, objectId}`. Estos fixtures son SINTÉTICOS con esa misma forma; las relaciones con Replication Flows conservan el
criterio histórico (coincidencia exacta de nombre en la definición) y se comparan contra él. Sin red ni Datasphere."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.catalog import DSP_CLI_Export_Lineage as dsp

REPO = Path(__file__).resolve().parents[1]


class FakeCli:
    def __init__(self, inventories=None, objects=None, failing=(), list_fails=False):
        self.inventories, self.objects, self.failing, self.list_fails = inventories or {}, objects or {}, set(failing), list_fails
        self.diagnostics = dsp.ExtractionDiagnostics(False)
        self.calls: list[tuple[str, str]] = []

    def list_objects(self, space, object_type):
        self.calls.append(("list", object_type))
        if self.list_fails:
            raise dsp.DatasphereCliError("INVENTORY_FAILED")
        return [{"technicalName": x} for x in self.inventories.get(object_type, [])]

    def read_object(self, space, object_type, technical_name):
        self.calls.append(("read", f"{object_type}/{technical_name}"))
        if technical_name in self.failing:
            raise dsp.DatasphereCliError("READ_FAILED")
        return self.objects.get((object_type, technical_name), {})


def _step(node_id, object_id, app="REMOTE_TABLES", activity="REPLICATE"):
    return {"id": node_id, "type": "TASK", "taskIdentifier": {"applicationId": app, "activity": activity, "objectId": object_id}, "ignoreError": False}


def _chain(name, steps, label=None, **extra):
    return {"taskchains": {name: {"kind": "sap.dwc.taskChain", "@EndUserText.label": label or name, "nodes": [{"id": 0, "type": "START"}, *steps],
                                  "links": [{"startNode": {"nodeId": 0, "statusRequired": "ANY"}, "endNode": {"nodeId": s["id"]}, "id": i} for i, s in enumerate(steps)],
                                  "options": {"layout": "VERTICAL"}, "schemaVersion": 2, **extra}}}


def _exporter(tmp_path, chains: dict, *, remote=(), local=(), flows=(), **cli_kw):
    cli = FakeCli({"task-chains": sorted(chains)}, {("task-chains", n): body for n, body in chains.items()}, **cli_kw)
    exp = dsp.LineageExporter(cli, "DEV", output_dir=tmp_path, root_object="ROOT")
    exp.effective_features = {"task-chains", "replication-flows"}
    for n in remote:
        exp.add_node("remote-tables", n)
    for n in local:
        exp.add_node("local-tables", n)
    for n in flows:
        exp.add_node("replication-flows", n)
    return exp, cli


def _orch(exp):
    """Relaciones observadas: aristas del pool (solo flujos) + pasos sobre Remote Tables registrados en `taskChainScan` (evidencia descriptiva, nunca arista)."""
    assert not [e for e in exp.edges if e.target_type == "remote-tables"]           # separación de evidencias: ninguna arista del pool hacia Remote Tables
    steps = [(s["taskChain"], "remote-tables", s["objectId"]) for s in exp.task_chain_scan.get("remoteTableSteps", [])]
    return sorted([(e.source_name, e.target_type, e.target_name) for e in exp.edges if e.relation == "ORCHESTRATES"] + steps)


# ── reconocimiento estructural ──
def test_remote_tables_without_flows_are_scanned_and_only_structural_replicate_steps_relate(tmp_path):
    chains = {"TC_SNAP": _chain("TC_SNAP", [_step(1, "RT_A"), _step(2, "RT_B"), _step(3, "RT_FUERA_DEL_LINAJE")], label="TC: Snapshot")}
    exp, cli = _exporter(tmp_path, chains, remote=("RT_A", "RT_B", "RT_SIN_PASO"))
    exp.discover_task_chains()
    assert ("list", "task-chains") in cli.calls                                   # antes: SKIP sin listar (no había flows)
    assert _orch(exp) == [("TC_SNAP", "remote-tables", "RT_A"), ("TC_SNAP", "remote-tables", "RT_B")]
    assert ("task-chains", "TC_SNAP") not in exp.nodes                          # solo replica Remote Tables: evidencia en el manifiesto, NO se adquiere al pool
    assert {s["taskChainLabel"] for s in exp.task_chain_scan["remoteTableSteps"]} == {"TC: Snapshot"}
    scan = exp.task_chain_scan
    assert (scan["version"], scan["state"], scan["inventoryComplete"], scan["chainsListed"], scan["chainsRead"], scan["readFailures"]) == \
           ("task-chain-scan/v1", "SCANNED", True, 1, 1, [])
    assert scan["remoteTables"] == ["RT_A", "RT_B", "RT_SIN_PASO"]
    assert [(s["objectId"], s["applicationId"], s["activity"], s["nodeId"]) for s in scan["remoteTableSteps"]] == \
           [("RT_A", "REMOTE_TABLES", "REPLICATE", 1), ("RT_B", "REMOTE_TABLES", "REPLICATE", 2)]


def test_names_only_in_labels_or_other_fields_never_create_a_relation(tmp_path):
    body = _chain("TC_TXT", [_step(1, "OTRA_COSA")], label="Replica RT_A todas las noches", description="RT_A y RT_B")
    body["taskchains"]["TC_TXT"]["nodes"].append({"id": 2, "type": "TASK", "taskIdentifier": {"applicationId": "REMOTE_TABLES", "activity": "REPLICATE"}, "comment": "RT_A"})
    exp, _ = _exporter(tmp_path, {"TC_TXT": body}, remote=("RT_A", "RT_B"))
    exp.discover_task_chains()
    assert _orch(exp) == [] and ("task-chains", "TC_TXT") not in exp.nodes
    assert exp.task_chain_scan["state"] == "SCANNED" and exp.task_chain_scan["remoteTableSteps"] == []


@pytest.mark.parametrize("app, activity", [("REMOTE_TABLES", "REMOVE_REPLICATED_DATA"), ("VIEWS", "PERSIST"), ("REMOTE_TABLES", "replicate")])
def test_unverified_step_kinds_are_recorded_but_never_relate(tmp_path, app, activity):
    exp, _ = _exporter(tmp_path, {"TC_X": _chain("TC_X", [_step(1, "RT_A", app, activity)])}, remote=("RT_A",))
    exp.discover_task_chains()
    assert _orch(exp) == []
    assert [(s["objectId"], s["applicationId"], s["activity"]) for s in exp.task_chain_scan["unrecognizedRemoteTableSteps"]] == [("RT_A", app, activity)]


def test_object_id_matches_exactly_never_by_prefix_or_case(tmp_path):
    exp, _ = _exporter(tmp_path, {"TC_X": _chain("TC_X", [_step(1, "rt_a"), _step(2, "RT_A_Delta"), _step(3, "RT_")])}, remote=("RT_A",))
    exp.discover_task_chains()
    assert _orch(exp) == []


# ── cobertura del escaneo ──
def test_no_flows_and_no_remote_tables_keeps_skipping_without_listing(tmp_path):
    exp, cli = _exporter(tmp_path, {"TC_X": _chain("TC_X", [_step(1, "RT_A")])}, local=("LT_A",))
    exp.discover_task_chains()
    assert cli.calls == [] and exp.task_chain_scan["state"] == "NO_CANDIDATES" and _orch(exp) == []


def test_feature_disabled_is_recorded_as_disabled(tmp_path):
    exp, cli = _exporter(tmp_path, {"TC_X": _chain("TC_X", [_step(1, "RT_A")])}, remote=("RT_A",))
    exp.effective_features = {"replication-flows"}
    exp.discover_task_chains()
    assert cli.calls == [] and exp.task_chain_scan["state"] == "DISABLED"


def test_read_failures_and_failed_inventory_make_the_scan_incomplete(tmp_path):
    exp, _ = _exporter(tmp_path, {"TC_OK": _chain("TC_OK", [_step(1, "RT_A")]), "TC_KO": _chain("TC_KO", [])}, remote=("RT_A", "RT_B"), failing=("TC_KO",))
    exp.discover_task_chains()
    assert exp.task_chain_scan["readFailures"] == ["TC_KO"] and exp.task_chain_scan["chainsRead"] == 1
    assert _orch(exp) == [("TC_OK", "remote-tables", "RT_A")]
    exp2, _ = _exporter(tmp_path / "b", {}, remote=("RT_A",), list_fails=True)
    exp2.discover_task_chains()
    assert exp2.task_chain_scan["inventoryComplete"] is False
    # Escaneo SOLO por Remote Tables: sus fallas no bloquean la publicación (salen de `failures`) y quedan registradas en el escaneo.
    assert not [f for f in exp.failures + exp2.failures if f["object_type"] == "task-chains"]
    assert exp.task_chain_scan["nonBlockingFailures"] == ["READ_FAILED"] and exp2.task_chain_scan["nonBlockingFailures"] == ["INVENTORY_FAILED"]


def test_with_flows_in_the_lineage_task_chain_failures_keep_blocking_as_before(tmp_path):
    exp, _ = _exporter(tmp_path, {"TC_KO": _chain("TC_KO", [])}, flows=("RF_A",), remote=("RT_A",), failing=("TC_KO",))
    exp.discover_task_chains()
    assert [f["code"] for f in exp.failures if f["object_type"] == "task-chains"] == ["READ_FAILED"] and "nonBlockingFailures" not in exp.task_chain_scan


def test_manifest_records_the_scan_coverage(tmp_path):
    exp, _ = _exporter(tmp_path, {"TC_SNAP": _chain("TC_SNAP", [_step(1, "RT_A")])}, remote=("RT_A",))
    exp.root_type = "analytic-model"
    exp.discover_task_chains()
    path = tmp_path / "lineage-manifest.json"
    dsp._write_manifest_v2(path=path, exporter=exp, client="Acme", space="DEV", mode="publish", deleted_stale=0, retained_shared=0)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["taskChainScan"]["state"] == "SCANNED" and data["taskChainScan"]["remoteTableSteps"][0]["objectId"] == "RT_A"
    assert not [e for e in data["edges"] if e["target_type"] == "remote-tables"]         # la evidencia remota no es una arista del manifiesto


# ── Replication Flows: criterio histórico intacto ──
def _legacy_rf_edges(chains: dict, flows: set) -> list:
    """Algoritmo ANTERIOR (referencia): coincidencia exacta del nombre del flujo en cualquier lugar de la definición."""
    out = []
    for name, body in chains.items():
        for rf in flows:
            if dsp.LineageExporter._contains_exact_string(body, rf):
                out.append((name, "replication-flows", rf))
    return sorted(out)


def test_replication_flow_relations_are_identical_to_the_previous_criterion(tmp_path):
    rf_step = lambda i, rf: {"id": i, "type": "TASK", "taskIdentifier": {"applicationId": "REPLICATION_FLOWS", "activity": "EXECUTE", "objectId": rf}}  # noqa: E731
    chains = {"TC_MD": _chain("TC_MD", [rf_step(1, "RF_A"), rf_step(2, "RF_B")]), "TC_OTRA": _chain("TC_OTRA", [rf_step(1, "RF_Z")]),
              "TC_MIX": _chain("TC_MIX", [rf_step(1, "RF_A"), _step(2, "RT_A")])}
    exp, _ = _exporter(tmp_path, chains, flows=("RF_A", "RF_B"), remote=("RT_A",))
    exp.discover_task_chains()
    rf_edges = sorted(x for x in _orch(exp) if x[1] == "replication-flows")
    assert rf_edges == _legacy_rf_edges(chains, {"RF_A", "RF_B"})
    assert [x for x in _orch(exp) if x[1] == "remote-tables"] == [("TC_MIX", "remote-tables", "RT_A")]
    assert {n for t, n in exp.nodes if t == "task-chains"} == {"TC_MD", "TC_MIX"}        # las que orquestan flujos se adquieren como siempre


def test_a_chain_that_only_replicates_remote_tables_does_not_change_the_assessment_dependencies(tmp_path):
    """El JSON de la cadena no entra al pool: la firma de dependencias (que cuenta toda mención a la clausura) de los assessments no cambia por esta evidencia."""
    exp, _ = _exporter(tmp_path, {"TC_SNAP": _chain("TC_SNAP", [_step(1, "RT_A")])}, remote=("RT_A",))
    exp.discover_task_chains()
    assert not [k for k in exp.nodes if k[0] == "task-chains"] and _orch(exp) == [("TC_SNAP", "remote-tables", "RT_A")]


@pytest.mark.parametrize("client", ["Kensing", "Pampa"])
def test_real_cached_chains_keep_exactly_their_replication_flow_relations(tmp_path, client):
    """Cadenas REALES de la caché local (si existen; no se versionan): las aristas a Replication Flows son las del criterio anterior y no aparece ninguna a Remote Tables."""
    folder = REPO / "catalog_local" / ".csn_cache" / client / "task-chains"
    if not folder.is_dir():
        pytest.skip(f"sin caché local de {client}")
    chains = {}
    for f in sorted(folder.glob("*.json")):
        chains.update({n: {"taskchains": {n: b}} for n, b in (json.loads(f.read_text(encoding="utf-8")).get("taskchains") or {}).items()})
    flows = {s["objectId"] for body in chains.values() for s in dsp.task_chain_steps(body) if s["applicationId"] == "REPLICATION_FLOWS"}
    exp, _ = _exporter(tmp_path, chains, flows=sorted(flows), remote=("RT_AJENA",))
    exp.discover_task_chains()
    assert sorted(x for x in _orch(exp) if x[1] == "replication-flows") == _legacy_rf_edges(chains, flows)
    assert not [x for x in _orch(exp) if x[1] == "remote-tables"]


def test_one_inventory_and_one_read_per_chain_per_extraction_regardless_of_remote_tables(tmp_path):
    """Varios modelos/tablas del mismo Space: el inventario de Task Chains se lista UNA vez por extracción y cada cadena se lee UNA vez (caché del exportador)."""
    remote = [f"RT_{i:02d}" for i in range(30)]
    chains = {"TC_1": _chain("TC_1", [_step(i + 1, n) for i, n in enumerate(remote[:15])]), "TC_2": _chain("TC_2", [_step(i + 1, n) for i, n in enumerate(remote[15:])])}
    exp, cli = _exporter(tmp_path, chains, remote=remote)
    exp.discover_task_chains()
    exp.discover_task_chains()                                                     # una segunda pasada en la MISMA extracción reutiliza inventario y lecturas
    assert cli.calls.count(("list", "task-chains")) == 1
    assert sorted(c for c in cli.calls if c[0] == "read") == [("read", "task-chains/TC_1"), ("read", "task-chains/TC_2")]
    assert len([x for x in _orch(exp) if x[1] == "remote-tables"]) == 30
