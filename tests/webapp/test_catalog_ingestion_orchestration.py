"""«Orquestación de ingesta»: relaciones SOLO del manifiesto del CLI, detalle/oráculo en los JSON técnicos; sin inventar enlaces; ausencia vs no evaluado."""
import json
import os
import re
from pathlib import Path

import pytest

from webapp.services import assessment_service as asvc
from webapp.services import catalog_ingestion_orchestration as co

CLIENT = "Acme"


def _edge(rel, st, sn, tt, tn):
    return {"source_type": st, "source_name": sn, "relation": rel, "target_type": tt, "target_name": tn}


def produce(rf, table, src_obj=None, conn="BACKEND_S4"):
    out = [_edge("PRODUCES", "replication-flows", rf, "local-tables", table), _edge("USED_BY", "connection", conn, "replication-flows", rf)]
    if src_obj:
        out.append(_edge("REPLICATED_BY", "external-source", src_obj, "replication-flows", rf))
    return out


def orchestrate(tc, rf, tt="replication-flows"):
    return _edge("ORCHESTRATES", "task-chains", tc, tt, rf)


class Env:
    def __init__(self, root: Path, monkeypatch):
        self.root, self.n = root, 0
        monkeypatch.setenv("DANWOR_CATALOG_DIR", str(root / "catalog"))
        self.pool = root / "pool"
        monkeypatch.setattr(asvc, "pool_client_files", lambda client: (self.pool, sorted(self.pool.rglob("*.json"))) if self.pool.exists() else None)

    def manifest(self, edges, objects=(), features=None, am="AM1", client=CLIENT, sub=None):
        self.n += 1
        d = self.root / "catalog" / ".lineage_runs" / client / (sub or "") / am
        d.mkdir(parents=True, exist_ok=True)
        body = {"client": client, "analyticModel": am, "objects": [{"object_type": t, "technical_name": n} for t, n in objects], "edges": edges, "externalSources": []}
        if features is not None:
            body["effectiveFeatures"] = features
        (d / "lineage-manifest.json").write_text(json.dumps(body), encoding="utf-8")

    def rf(self, name, tasks, trigger="ON_DELTA_INTERVAL", conn="BACKEND_S4", ctype="SAPS4HANAOP", container="/CDS_EXTRACTION"):
        p = self.pool / "replication-flows"
        p.mkdir(parents=True, exist_ok=True)
        body = {"contents": {"sourceSystem": [{"connectionId": conn, "connectionType": ctype, "container": container}], "deltaLoadTrigger": trigger,
                             "replicationTasks": [{"name": f"t{i}", "loadType": lt, "sourceObject": {"name": s}, "targetObject": {"name": t}} for i, (s, t, lt) in enumerate(tasks)]}}
        (p / f"{name}.json").write_text(json.dumps({"replicationflows": {name: body}}), encoding="utf-8")

    def tc(self, name, label=None, description=None):
        p = self.pool / "task-chains"
        p.mkdir(parents=True, exist_ok=True)
        body = {"@EndUserText.label": label, "nodes": [], "links": []}
        if description:
            body["description"] = description
        (p / f"{name}.json").write_text(json.dumps({"taskchains": {name: body}}), encoding="utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


T = "1TR_SAP_ZCDS_DSP_DIM_EKKN"
FEATS = ["analytic-models", "replication-flows", "task-chains"]


def test_ekkn_shape_known_producer_without_chain_is_explicit_not_an_error(env):
    env.manifest(produce("RF_MD_EKKN", T, "ZCDS_DSP_DIM_EKKN") + [orchestrate("TC_X", "RF_OTRO")], objects=[("local-tables", T)])
    env.rf("RF_MD_EKKN", [("ZCDS_DSP_DIM_EKKN", T, "INITIAL")])
    o = co.orchestration_for(CLIENT, T)
    assert o.state == co.ESTABLISHED and len(o.producers) == 1
    p = o.producers[0]
    assert (p.flow, p.load_type, p.target_kind, p.physical_target) == ("RF_MD_EKKN", "INITIAL", co.BASE, T)
    assert p.source_objects == ("ZCDS_DSP_DIM_EKKN",) and (p.connection, p.connection_type, p.container) == ("BACKEND_S4", "SAPS4HANAOP", "/CDS_EXTRACTION")
    assert p.chains == () and p.chains_state == co.NOT_FOUND_IN_EXPORT          # el manifiesto escaneó cadenas (otra arista ORCHESTRATES) y ninguna ejecuta este RF


def test_chain_confirmed_with_label_and_delta_shadow_target(env):
    base = "1TR_SAP_MD_ACCOUNTINGDOCUMENTTYPE"
    env.manifest(produce("RF_MD_ACC", base, "ZCDS_X") + [orchestrate("TC_MD_01", "RF_MD_ACC")], objects=[("local-tables", base)], features=FEATS)
    env.rf("RF_MD_ACC", [("ZCDS_X", base + "_Delta", "REPLICATE")])
    env.tc("TC_MD_01", label="Task Chain Master Data 01")
    delta = {"state": "ON", "companions": [base + "_Delta"]}
    p = co.orchestration_for(CLIENT, base, delta).producers[0]
    assert p.target_kind == co.DELTA_SHADOW and p.physical_target == base + "_Delta"
    assert p.chains_state == co.CONFIRMED and [(c.name, c.label, c.path) for c in p.chains] == [("TC_MD_01", "Task Chain Master Data 01", ("TC_MD_01",))]
    html = co.render_html(co.orchestration_for(CLIENT, base, delta), "a")
    assert "proyección activa (no es la misma tabla)" in html and "<code>" + base + "_Delta</code>" in html


def test_delta_target_without_canonical_evidence_makes_no_claim(env):
    base = "1TR_A"
    env.manifest(produce("RF_A", base, "X"), objects=[("local-tables", base)], features=FEATS)
    env.rf("RF_A", [("X", base + "_Delta", "REPLICATE")])
    for delta in (None, {"state": "OFF", "companions": []}, {"state": "UNDETERMINED", "companions": []}):
        p = co.orchestration_for(CLIENT, base, delta).producers[0]
        assert p.target_kind == co.UNKNOWN or p.target_kind == co.OTHER
        assert p.target_kind != co.DELTA_SHADOW
    assert "proyección" not in co.render_html(co.orchestration_for(CLIENT, base, None), "a")


def test_chain_state_never_says_no_chain_when_chains_were_not_scanned(env):
    env.manifest(produce("RF_A", "1TR_A", "X"), objects=[("local-tables", "1TR_A")])                                   # sin effectiveFeatures y sin ORCHESTRATES
    p = co.orchestration_for(CLIENT, "1TR_A").producers[0]
    assert p.chains_state == co.NOT_EVALUATED
    assert "no evaluada" in co.render_html(co.orchestration_for(CLIENT, "1TR_A"), "a") and "Sin Task Chain comprobada" not in co.render_html(co.orchestration_for(CLIENT, "1TR_A"), "a")
    env.manifest(produce("RF_B", "1TR_B", "X"), objects=[("local-tables", "1TR_B")], features=["analytic-models", "replication-flows"], am="AM2")       # familia declarada sin task-chains
    assert co.orchestration_for(CLIENT, "1TR_B").producers[0].chains_state == co.NOT_EVALUATED
    env.manifest(produce("RF_C", "1TR_C", "X"), objects=[("local-tables", "1TR_C")], features=FEATS, am="AM3")                                                # escaneada y sin cadena
    assert co.orchestration_for(CLIENT, "1TR_C").producers[0].chains_state == co.NOT_FOUND_IN_EXPORT


def test_no_manifest_and_uncovered_table_are_not_evaluated(env):
    assert co.orchestration_for(CLIENT, "1TR_A").state == co.NOT_EVALUATED
    env.manifest(produce("RF_A", "1TR_A", "X"), objects=[("local-tables", "1TR_A")])
    o = co.orchestration_for(CLIENT, "1TR_OTRA")
    assert o.state == co.NOT_EVALUATED and o.producers == () and "no se evaluó" in o.note
    assert co.orchestration_for("OtroCliente", "1TR_A").state == co.NOT_EVALUATED                                       # los manifiestos de otro cliente no cuentan


def test_covered_table_without_producer_is_insufficient_evidence_not_an_invented_flow(env):
    env.manifest([_edge("DEPENDS_ON", "views", "V1", "local-tables", "1TR_A")], objects=[("local-tables", "1TR_A")])
    o = co.orchestration_for(CLIENT, "1TR_A")
    assert o.state == co.NO_PRODUCER_FOUND and o.producers == () and "Evidencia insuficiente" in o.note


def test_several_producers_and_several_orchestrators_are_all_listed_sorted(env):
    env.manifest(produce("RF_B", "1TR_A", "S2") + produce("RF_A", "1TR_A", "S1") + [orchestrate("TC_2", "RF_A"), orchestrate("TC_1", "RF_A"), orchestrate("TC_3", "RF_B")],
                 objects=[("local-tables", "1TR_A")], features=FEATS)
    env.rf("RF_A", [("S1", "1TR_A", "INITIAL")])
    env.rf("RF_B", [("S2", "1TR_A", "REPLICATE")])
    o = co.orchestration_for(CLIENT, "1TR_A")
    assert [p.flow for p in o.producers] == ["RF_A", "RF_B"]
    assert [c.name for c in o.producers[0].chains] == ["TC_1", "TC_2"] and [c.name for c in o.producers[1].chains] == ["TC_3"]
    html = co.render_html(o, "a")
    assert "Productor 1 de 2" in html and "Productor 2 de 2" in html and "Orquestación de ingesta (2)" in html


def test_sub_chains_follow_only_explicit_edges_with_cycle_and_depth_guards(env):
    env.manifest(produce("RF_A", "1TR_A", "S") + [orchestrate("TC_DIR", "RF_A"), orchestrate("TC_TOP", "TC_DIR", tt="task-chains"), orchestrate("TC_ROOT", "TC_TOP", tt="task-chains")],
                 objects=[("local-tables", "1TR_A")], features=FEATS)
    p = co.orchestration_for(CLIENT, "1TR_A").producers[0]
    assert [c.path for c in p.chains] == [("TC_ROOT", "TC_TOP", "TC_DIR")] and p.chains[0].name == "TC_DIR"
    assert "<code>TC_ROOT</code> → <code>TC_TOP</code> → <code>TC_DIR</code>" in co.render_html(co.orchestration_for(CLIENT, "1TR_A"), "a")
    env.manifest(produce("RF_Z", "1TR_Z", "S") + [orchestrate("A", "RF_Z"), orchestrate("B", "A", tt="task-chains"), orchestrate("A", "B", tt="task-chains")], objects=[("local-tables", "1TR_Z")], features=FEATS, am="AM2")
    cyc = co.orchestration_for(CLIENT, "1TR_Z").producers[0]
    assert cyc.chains and all(len(c.path) <= co.MAX_CHAIN_DEPTH for c in cyc.chains)                                  # termina y no repite nodos
    assert all(len(set(c.path)) == len(c.path) for c in cyc.chains)


def test_only_explicit_references_count_never_names_or_free_text(env):
    env.manifest(produce("RF_MD_EKKN", T, "ZCDS_DSP_DIM_EKKN"), objects=[("local-tables", T), ("local-tables", "1TR_SAP_ZCDS_DSP_DIM_EKKB")], features=FEATS)
    env.rf("RF_MD_EKKN", [("ZCDS_DSP_DIM_EKKN", T, "INITIAL")])
    env.tc("TC_MENCIONA", label="Ejecuta RF_MD_EKKN según la descripción", description="RF_MD_EKKN")                       # la cadena solo LO MENCIONA: no hay arista
    assert co.orchestration_for(CLIENT, T).producers[0].chains == ()
    near = co.orchestration_for(CLIENT, "1TR_SAP_ZCDS_DSP_DIM_EKKB")                                                      # nombre parecido, sin PRODUCES
    assert near.state == co.NO_PRODUCER_FOUND and near.producers == ()
    env.manifest(produce("RF_NOPREFIX", "TablaSinPrefijo", "S"), objects=[("local-tables", "TablaSinPrefijo")], am="AM9")  # el prefijo 1TR_ no es criterio
    assert co.orchestration_for(CLIENT, "TablaSinPrefijo").state == co.ESTABLISHED


def test_multi_task_flow_attributes_only_the_task_that_loads_this_table(env):
    env.manifest(produce("RF_M", "1TR_A", "S_A") + [_edge("REPLICATED_BY", "external-source", "S_B", "replication-flows", "RF_M")], objects=[("local-tables", "1TR_A")], features=FEATS)
    env.rf("RF_M", [("S_A", "1TR_A", "INITIAL"), ("S_B", "1TR_B", "REPLICATE")])
    p = co.orchestration_for(CLIENT, "1TR_A").producers[0]
    assert p.source_objects == ("S_A",) and p.load_type == "INITIAL" and p.source_task_certain
    env2 = co.orchestration_for(CLIENT, "1TR_A")
    assert "S_B" not in co.render_html(env2, "a")
    import shutil
    shutil.rmtree(env.pool)                                                                                                # sin el JSON del RF y con 2 fuentes en el manifiesto: no se adivina cuál
    co._memo.clear()
    q = co.orchestration_for(CLIENT, "1TR_A").producers[0]
    assert set(q.source_objects) == {"S_A", "S_B"} and not q.source_task_certain and q.target_kind == co.UNKNOWN
    assert "no se fijó cuál alimenta esta tabla" in co.render_html(co.orchestration_for(CLIENT, "1TR_A"), "a") and "No disponible" in co.render_html(co.orchestration_for(CLIENT, "1TR_A"), "a")


def test_duplicated_manifest_copies_do_not_duplicate_producers_or_chains(env):
    edges = produce("RF_A", "1TR_A", "S") + [orchestrate("TC_1", "RF_A")]
    env.manifest(edges, objects=[("local-tables", "1TR_A")], features=FEATS)
    env.manifest(edges, objects=[("local-tables", "1TR_A")], features=FEATS, sub="DSDEV")
    o = co.orchestration_for(CLIENT, "1TR_A")
    assert len(o.producers) == 1 and len(o.producers[0].chains) == 1


def test_chains_come_from_other_manifests_of_the_client_for_the_same_flow(env):
    env.manifest(produce("RF_A", "1TR_A", "S"), objects=[("local-tables", "1TR_A")], features=FEATS, am="AM1")
    env.manifest([_edge("DEPENDS_ON", "views", "V", "replication-flows", "RF_A"), orchestrate("TC_7", "RF_A")], objects=[], features=FEATS, am="AM2")
    assert [c.name for c in co.orchestration_for(CLIENT, "1TR_A").producers[0].chains] == ["TC_7"]


def test_html_is_collapsed_escaped_and_only_links_existing_cds_code(env):
    env.manifest(produce("RF_<A>", "1TR_A", "ZCDS_<X>"), objects=[("local-tables", "1TR_A")], features=FEATS)
    o = co.orchestration_for(CLIENT, "1TR_A")
    html = co.render_html(o, "orquestacion-x", lambda obj: "")
    assert html.startswith('<details class="dashboard-section" id="orquestacion-x">') and " open" not in html.split(">")[0]
    assert "<script" not in html and "RF_&lt;A&gt;" in html and "ZCDS_&lt;X&gt;" in html
    assert "Abrir CDS View" not in html
    linked = co.render_html(o, "a", lambda obj: '<a href="#">Abrir CDS View</a>')
    assert "Abrir CDS View" in linked


def test_injection_goes_right_after_fields_and_leaves_everything_else_untouched():
    fields = '<details class="dashboard-section" id="campos"><summary class="dashboard-section__head"><span class="dashboard-section__caret"></span><span class="dashboard-section__title">Campos (3)</span></summary><div>T</div></details>'
    section = f"<h2>X</h2><p>antes</p>{fields}<p>despues</p>"
    out = co.inject_after_fields(section, "<BLOCK/>")
    assert out == f"<h2>X</h2><p>antes</p>{fields}<BLOCK/><p>despues</p>"
    assert out.replace("<BLOCK/>", "") == section                                                                         # byte a byte igual sin el bloque
    assert co.inject_after_fields("<p>sin campos</p>", "<BLOCK/>") == "<p>sin campos</p><BLOCK/>"


def test_service_is_read_only_and_never_raises(env, monkeypatch):
    env.manifest(produce("RF_A", "1TR_A", "S"), objects=[("local-tables", "1TR_A")], features=FEATS)
    before = sorted(str(p) for p in env.root.rglob("*"))
    co.orchestration_for(CLIENT, "1TR_A")
    assert sorted(str(p) for p in env.root.rglob("*")) == before
    monkeypatch.setattr(co, "_manifests", lambda c: (_ for _ in ()).throw(RuntimeError("boom")))
    o = co.orchestration_for(CLIENT, "1TR_A")
    assert o.state == co.NOT_EVALUATED and o.producers == ()


def test_assessment_still_does_not_read_task_chains():
    """Frontera intacta: el lector de cadenas vive en la presentación del Hub, no en el Assessment (que las tiene prohibidas como procedencia)."""
    src = Path(co.__file__).read_text(encoding="utf-8")
    assert "modeling_assessment" not in src.replace("assessment_service", "")
    readers = Path(asvc.__file__).parent.parent.parent / "tools" / "catalog" / "modeling_assessment" / "readers.py"
    assert "catalog_ingestion_orchestration" not in readers.read_text(encoding="utf-8")


# ───────────────────────── ORÁCULO con el corpus real (opcional: DANWOR_ORCH_ORACLE_DIR = catálogo con .csn_cache y .lineage_runs) ─────────────────────────
ORACLE = os.environ.get("DANWOR_ORCH_ORACLE_DIR", "").strip()


@pytest.mark.skipif(not ORACLE, reason="sin corpus real (DANWOR_ORCH_ORACLE_DIR)")
def test_oracle_service_matches_raw_json_for_every_ingestion_table(monkeypatch):
    base = Path(ORACLE)
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(base))
    monkeypatch.delenv("DANWOR_CATALOG_CSN_CACHE", raising=False)
    checked = 0
    for client in sorted(p.name for p in (base / ".lineage_runs").iterdir() if p.is_dir()):
        pool = base / ".csn_cache" / client
        rfs, tcs, tables = {}, {}, {}
        for f in (pool / "replication-flows").glob("*.json") if (pool / "replication-flows").is_dir() else []:
            for name, b in (json.loads(f.read_text(encoding="utf-8")).get("replicationflows") or {}).items():
                rfs[name] = b["contents"]
        for f in (pool / "task-chains").glob("*.json") if (pool / "task-chains").is_dir() else []:
            for name, b in (json.loads(f.read_text(encoding="utf-8")).get("taskchains") or {}).items():
                tcs[name] = b
        for f in (pool / "local-tables").glob("*.json") if (pool / "local-tables").is_dir() else []:
            for name, d in (json.loads(f.read_text(encoding="utf-8")).get("definitions") or {}).items():
                tables[name] = d
        monkeypatch.setattr(asvc, "pool_client_files", lambda c, _p=pool: (_p, sorted(_p.rglob("*.json"))))
        co._memo.clear()
        manifest_tables = set()
        for mf in (base / ".lineage_runs" / client).rglob("lineage-manifest.json"):
            for e in json.loads(mf.read_text(encoding="utf-8"))["edges"]:
                if e["relation"] == "PRODUCES":
                    manifest_tables.add(e["target_name"])
        for t in sorted(manifest_tables):
            companions = [c for c in ((tables.get(t) or {}).get("@DataWarehouse.delta") or {}).get("deltaFromEntities", [])]
            o = co.orchestration_for(client, t, {"state": "ON" if companions else "OFF", "companions": companions})
            # oráculo estructural independiente: RF cuyo destino físico es la tabla o su compañera delta declarada
            truth = sorted(n for n, c in rfs.items() if any((tk.get("targetObject") or {}).get("name") in {t, *companions} for tk in c.get("replicationTasks", [])))
            assert [p.flow for p in o.producers] == truth, (client, t)
            for p in o.producers:
                owners = sorted(n for n, c in tcs.items() for nd in c.get("nodes", []) if (nd.get("taskIdentifier") or {}).get("objectId") == p.flow
                                and (nd.get("taskIdentifier") or {}).get("applicationId") == "REPLICATION_FLOWS")
                if p.chains_state == co.CONFIRMED:
                    assert sorted({c.name for c in p.chains}) == owners, (client, t, p.flow)
                else:
                    assert not owners or p.chains_state == co.NOT_EVALUATED, (client, t, p.flow)   # una cadena real que el manifiesto no vio solo es aceptable si ese manifiesto no escaneó cadenas
                task = [tk for tk in rfs[p.flow]["replicationTasks"] if (tk.get("targetObject") or {}).get("name") in {t, *companions}][0]
                assert p.physical_target == task["targetObject"]["name"] and p.source_objects == (task["sourceObject"]["name"],)
            checked += 1
    assert checked >= 20
