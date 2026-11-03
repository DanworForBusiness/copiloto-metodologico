"""Task Chains de Remote Tables a través de la publicación REAL: manifiesto transaccional, espejo al bucket (Spaces simulado) y lectura del Catálogo.

Frontera simulada: el tenant de Datasphere y el bucket (``tests.publication_fakes``). El exportador, la publicación segura, ``remote_store`` y el lector del panel son los REALES.
"""
from __future__ import annotations

import json

import pytest

from tests.publication_fakes import FakeS3, analytic_model, enable_remote, view
from tests.publication_fixtures import ctx  # noqa: F401
from tools.catalog import publication_txn as txn
from webapp.services import catalog_ingestion_orchestration as co

AM, RT, RT2 = "AM_Remoto", "1TR_EMP", "1TR_JOB"


def remote_table(name, entity):
    return {"definitions": {name: {"kind": "entity", "@DataWarehouse.remote.connection": "SRC", "@DataWarehouse.remote.entity": entity,
                                   "elements": {c: {"type": "cds.String"} for c in ("A", "B", "M")}}}}


def chain(name, objects, app="REMOTE_TABLES", activity="REPLICATE"):
    nodes = [{"id": 0, "type": "START"}] + [{"id": i + 1, "type": "TASK", "taskIdentifier": {"applicationId": app, "activity": activity, "objectId": o}, "ignoreError": False}
                                            for i, o in enumerate(objects)]
    return {"taskchains": {name: {"kind": "sap.dwc.taskChain", "@EndUserText.label": f"TC: {name}", "nodes": nodes, "links": []}}}


@pytest.fixture()
def world(ctx, monkeypatch):                                                                     # noqa: F811
    t = ctx.tenant
    t.put("PROD", "remote-tables", RT, remote_table(RT, "EmpEmployment"))
    t.put("PROD", "remote-tables", RT2, remote_table(RT2, "EmpJob"))
    t.put("PROD", "views", "3VF_REM", view("3VF_REM", [RT, RT2]))
    t.put("PROD", "analytic-models", AM, analytic_model(AM, "3VF_REM", label="Modelo Analítico - Remoto"))
    t.put("PROD", "task-chains", "TC_SNAP", chain("TC_SNAP", [RT]))
    monkeypatch.setenv("DANWOR_GOVERNED_ASSESSMENTS_DIR", str(ctx.tmp / "governed"))
    co._memo.clear()
    return ctx


def _manifest(w) -> dict:
    found = sorted(w.catalog.joinpath(".lineage_runs").rglob("lineage-manifest.json"))
    return json.loads(found[-1].read_text(encoding="utf-8"))


def _publish(w, **kw):
    res, lines = w.publish(AM, features=["analytic-models", "views", "tables", "replication-flows", "task-chains"], **kw)
    co._memo.clear()
    return res


def test_scan_and_relations_survive_the_transactional_publication_the_bucket_and_the_catalog_read(world, monkeypatch):
    s3 = FakeS3()
    enable_remote(monkeypatch, s3)
    res = _publish(world, mirror=True)
    assert res.status == txn.COMPLETED
    m = _manifest(world)
    assert m["schemaVersion"] == 3 and "integrity" in m                                    # manifiesto TRANSACCIONAL sellado
    assert m["taskChainScan"]["state"] == "SCANNED" and [s["objectId"] for s in m["taskChainScan"]["remoteTableSteps"]] == [RT]
    assert not [e for e in m["edges"] if e["source_type"] == "task-chains"]                  # evidencia descriptiva: ni arista ni nodo del pool (la compuerta H1 no la ve)
    remote = [k for k in s3.objects if k.endswith("lineage-manifest.json")]
    assert remote and json.loads(s3.objects[remote[0]])["taskChainScan"] == m["taskChainScan"]      # el espejo lleva el mismo contenido
    assert not [p for p in world.pool.rglob("*.json") if p.parent.name == "task-chains"]          # la cadena solo-remota NO entra al pool
    assert co.replication_for("Acme", RT).state == co.OBSERVED                                   # lectura del Catálogo sobre lo publicado
    assert co.replication_for("Acme", RT2).state == co.NOT_OBSERVED


def test_a_republication_reflects_a_removed_or_modified_chain_and_never_keeps_old_relations(world):
    assert _publish(world).status == txn.COMPLETED
    assert co.replication_for("Acme", RT).state == co.OBSERVED
    world.tenant.put("PROD", "task-chains", "TC_SNAP", chain("TC_SNAP", [RT2]))               # la cadena ahora replica OTRA tabla
    assert _publish(world).status == txn.COMPLETED
    assert (co.replication_for("Acme", RT).state, co.replication_for("Acme", RT2).state) == (co.NOT_OBSERVED, co.OBSERVED)
    world.tenant.drop("PROD", "task-chains", "TC_SNAP")                                         # la cadena se eliminó
    assert _publish(world).status == txn.COMPLETED
    m = _manifest(world)
    assert m["taskChainScan"]["remoteTableSteps"] == [] and m["taskChainScan"]["chainsListed"] == 0
    assert (co.replication_for("Acme", RT).state, co.replication_for("Acme", RT2).state) == (co.NOT_OBSERVED, co.NOT_OBSERVED)


def test_a_task_chain_that_cannot_be_read_does_not_block_the_publication_and_is_not_assessed(world):
    assert _publish(world).status == txn.COMPLETED
    world.tenant.fail_reads.add(("task-chains", "TC_SNAP"))
    res = _publish(world)
    assert res.status == txn.COMPLETED                                                          # evidencia descriptiva: su lectura fallida no bloquea la publicación del modelo
    m = _manifest(world)
    assert m["taskChainScan"]["readFailures"] == ["TC_SNAP"] and m["taskChainScan"]["remoteTableSteps"] == []
    assert m["taskChainScan"]["nonBlockingFailures"] == ["READ_FAILED"]
    assert co.replication_for("Acme", RT).state == co.NOT_ASSESSED


def test_a_failed_task_chain_inventory_does_not_block_the_publication_and_is_not_assessed(world):
    world.tenant.fail_lists.add("task-chains")
    res = _publish(world)
    assert res.status == txn.COMPLETED
    assert _manifest(world)["taskChainScan"]["inventoryComplete"] is False
    assert co.replication_for("Acme", RT).state == co.NOT_ASSESSED
