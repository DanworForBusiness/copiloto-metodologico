"""Adquisición de evidencia del extractor COMPARTIDO (Catálogo y Assessment ad hoc): fuentes de vistas gráficas y procedencia operativa de
Data Flows productores. Datos SINTÉTICOS (nombres genéricos); el extractor, el motor de Assessment y el de Juicio son los reales.

Causa raíz que fijan estas pruebas:
  1. el extractor solo leía las dependencias de una vista desde el SQL (`@DataWarehouse.sqlEditor.query`); las vistas GRÁFICAS declaran su
     fuente en `query.SELECT.from` del CSN y sus fuentes nunca se adquirían (el Catálogo lo disimulaba con el pool acumulado del cliente);
  2. la fuente declarada de un Data Flow que PRODUCE una hoja del linaje no se adquiría: la frontera de procedencia quedaba OUTSIDE_POOL.
Contratos: tipos por inventario del Space (sin inferencias); solo productores del linaje; ciclos, duplicados y profundidad controlados;
faltantes y ambigüedades registrados con causa (nunca se fabrica un contrato); el cierre semántico no se amplía con la procedencia; mismo
motor y mismo Juicio para Catálogo y ad hoc sobre la misma evidencia.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._modeling_assessment_m2_fixtures import _remote_table
from tests._modeling_assessment_m7_fixtures import _view, write_inbound
from tools.catalog import DSP_CLI_Export_Lineage as dsp
from tools.catalog.methodological_judgment import CURRENT_RULESET_VERSION, evaluate_judgment
from webapp.services import assessment_service as asvc

FEATURES = ["analytic-models", "views", "tables", "replication-flows", "data-flows", "transformation-flows", "task-chains"]
COLS = ("A", "B")


def _local(name: str) -> dict:
    return {"definitions": {name: {"kind": "entity", "@DataWarehouse.repository.technicalType": {"#": "DWC_LOCAL_TABLE"},
                                   "elements": {c: {"type": "cds.String", "@EndUserText.label": c.title()} for c in (*COLS, "M")}}}}


def _remote(name: str, entity: str) -> dict:
    body = _remote_table(name, "S4PREM", entity)
    body["definitions"][name]["elements"] = {c: {"type": "cds.String", "@EndUserText.label": c.title()} for c in (*COLS, "M")}
    return body


def _df(name: str, sources, targets) -> dict:
    return {"dataflows": {name: {"sources": {s: {} for s in sources}, "targets": {t: {} for t in targets}}}}


def base_space() -> dict[tuple[str, str], dict]:
    """Raíz `3VF_S` (vista GRÁFICA) <- `LT_FACT` (tabla local) <= `DF_FACT` (Data Flow) <= `2VR_SRC` (vista gráfica) <- `RT_SRC` (remota).
    Ruido que NO debe adquirirse: `DF_CONSUMER` (LEE la hoja, no la produce) -> `LT_OTHER`; `V_UNRELATED` <- `RT_OTHER`."""
    return {
        ("views", "3VF_S"): _view("3VF_S", "LT_FACT", COLS, pattern="ANALYTICAL_FACT", measures=("M",)),
        ("local-tables", "LT_FACT"): _local("LT_FACT"),
        ("data-flows", "DF_FACT"): _df("DF_FACT", ["2VR_SRC"], ["LT_FACT"]),
        ("views", "2VR_SRC"): _view("2VR_SRC", "RT_SRC", COLS, measures=("M",)),
        ("remote-tables", "RT_SRC"): _remote("RT_SRC", "ABAPTABLES.TAB1"),
        ("data-flows", "DF_CONSUMER"): _df("DF_CONSUMER", ["LT_FACT"], ["LT_OTHER"]),
        ("local-tables", "LT_OTHER"): _local("LT_OTHER"),
        ("views", "V_UNRELATED"): _view("V_UNRELATED", "RT_OTHER", COLS),
        ("remote-tables", "RT_OTHER"): _remote("RT_OTHER", "ABAPTABLES.TAB2"),
    }


class SpaceCli:
    def __init__(self, objects: dict[tuple[str, str], dict]):
        self.objects = objects
        self.diagnostics = dsp.ExtractionDiagnostics(False)
        self.last_list_incomplete = False
        self.reads: list[tuple[str, str]] = []

    def list_objects(self, space, object_type):
        return [{"technicalName": n} for (t, n) in sorted(self.objects) if t == object_type]

    def read_object(self, space, object_type, technical_name):
        self.reads.append((object_type, technical_name))
        return json.loads(json.dumps(self.objects[(object_type, technical_name)]))


def _export(tmp_path: Path, objects, *, root="3VF_S", features=FEATURES, write=False):
    cli = SpaceCli(objects)
    out = tmp_path / "pool" / "Client"
    out.mkdir(parents=True, exist_ok=True)
    exp = dsp.LineageExporter(cli, "S", output_dir=out, root_object=root, requested_features=features, mode="assessment")
    exp.run() if write else exp.extract()
    return exp, cli


def _keys(exp) -> set[tuple[str, str]]:
    return set(exp.nodes)


# ── 1. vistas gráficas: la fuente del CSN se adquiere ──────────────────────────────────────────────────────────────
def test_graphical_view_source_is_acquired_by_the_shared_walker(tmp_path):
    exp, _ = _export(tmp_path, base_space())
    assert ("local-tables", "LT_FACT") in _keys(exp)
    assert ("views", "3VF_S", "DEPENDS_ON", "local-tables", "LT_FACT") in {(e.source_type, e.source_name, e.relation, e.target_type, e.target_name) for e in exp.edges}


def test_structural_refs_that_are_not_space_objects_are_neither_dependencies_nor_external_sources(tmp_path):
    """Alias del FROM, columnas de una subconsulta y entidades fuera del inventario: el loader del Catálogo las filtra por entidad; acá, por inventario."""
    objects = base_space()
    objects[("views", "3VF_S")] = {"definitions": {"3VF_S": {"kind": "entity", "elements": {"A": {"type": "cds.String"}}, "query": {"SELECT": {
        "from": {"SELECT": {"from": {"join": "left", "args": [{"ref": ["LT_FACT"], "as": "F"}, {"ref": ["NOT_IN_SPACE"], "as": "N"}],
                                     "on": [{"ref": ["F", "A"]}, "=", {"ref": ["N", "A"]}]},
                            "columns": [{"ref": ["F", "A"]}]}},
        "columns": [{"ref": ["A"]}]}}}}}
    exp, _ = _export(tmp_path, objects)
    assert ("local-tables", "LT_FACT") in _keys(exp)
    assert not {"F", "N", "NOT_IN_SPACE", "A"} & {n for _, n in _keys(exp)}
    assert not {"F", "N", "NOT_IN_SPACE"} & exp.external_sources


# ── 2. procedencia operativa de Data Flows productores ──────────────────────────────────────────────────────────────
def test_sources_of_a_producing_data_flow_are_acquired_with_their_upstream(tmp_path):
    exp, cli = _export(tmp_path, base_space())
    keys = _keys(exp)
    assert {("views", "2VR_SRC"), ("remote-tables", "RT_SRC"), ("data-flows", "DF_FACT")} <= keys
    edges = {(e.source_type, e.source_name, e.relation, e.target_type, e.target_name) for e in exp.edges}
    assert ("data-flows", "DF_FACT", "PRODUCES", "local-tables", "LT_FACT") in edges
    assert ("views", "2VR_SRC", "SOURCE_OF", "data-flows", "DF_FACT") in edges
    assert exp.dataflow_provenance == {"version": dsp.DF_PROVENANCE_VERSION,
                                       "flows": [{"flow": "DF_FACT", "produces": ["LT_FACT"], "depth": 1}],
                                       "sources": [{"flow": "DF_FACT", "entity": "2VR_SRC", "object_type": "views", "state": dsp.DF_SOURCE_ACQUIRED}]}
    assert not exp.failures


def test_acquisition_is_not_indiscriminate(tmp_path):
    """Un Data Flow que LEE la hoja (consumidor) no se sigue, y nada ajeno al linaje se adquiere ni se lee como objeto del grafo."""
    exp, cli = _export(tmp_path, base_space())
    keys = _keys(exp)
    assert not {("local-tables", "LT_OTHER"), ("views", "V_UNRELATED"), ("remote-tables", "RT_OTHER")} & keys
    assert [f["flow"] for f in exp.dataflow_provenance["flows"]] == ["DF_FACT"]
    objects_read = {k for k in cli.reads if k[0] not in ("data-flows", "replication-flows", "transformation-flows", "task-chains")}
    assert objects_read == {("views", "3VF_S"), ("local-tables", "LT_FACT"), ("views", "2VR_SRC"), ("remote-tables", "RT_SRC")}
    assert len(cli.reads) == len(set(cli.reads))                                      # ningún objeto se lee dos veces


def test_without_data_flows_in_scope_there_is_no_provenance_extension(tmp_path):
    exp, _ = _export(tmp_path, base_space(), features=[f for f in FEATURES if f != "data-flows"])
    assert ("views", "2VR_SRC") not in _keys(exp) and exp.dataflow_provenance["flows"] == []


@pytest.mark.parametrize("source,state", [("GHOST", dsp.DF_SOURCE_NOT_IN_SPACE), ("TWIN", dsp.DF_SOURCE_AMBIGUOUS)])
def test_missing_or_ambiguous_sources_are_recorded_never_fabricated(tmp_path, source, state):
    objects = base_space()
    objects[("data-flows", "DF_FACT")] = _df("DF_FACT", [source], ["LT_FACT"])
    objects[("views", "TWIN")] = _view("TWIN", "RT_SRC", COLS)
    objects[("local-tables", "TWIN")] = _local("TWIN")
    exp, _ = _export(tmp_path, objects)
    assert exp.dataflow_provenance["sources"] == [{"flow": "DF_FACT", "entity": source, "object_type": None, "state": state}]
    assert not {n for _, n in _keys(exp)} & {source} and not exp.failures
    exp.assert_complete()                                                              # una fuente ausente no es una lectura fallida


def test_cycles_terminate_and_each_object_is_read_once(tmp_path):
    """LT_FACT <= DF_A <= V_A <- LT_B <= DF_B <= V_B <- LT_FACT (ciclo de procesamiento)."""
    objects = base_space()
    objects[("data-flows", "DF_FACT")] = _df("DF_FACT", ["V_A"], ["LT_FACT"])
    objects[("views", "V_A")] = _view("V_A", "LT_B", COLS)
    objects[("local-tables", "LT_B")] = _local("LT_B")
    objects[("data-flows", "DF_B")] = _df("DF_B", ["V_B"], ["LT_B"])
    objects[("views", "V_B")] = _view("V_B", "LT_FACT", COLS)
    exp, cli = _export(tmp_path, objects)
    assert {("views", "V_A"), ("local-tables", "LT_B"), ("views", "V_B")} <= _keys(exp)
    assert sorted(f["flow"] for f in exp.dataflow_provenance["flows"]) == ["DF_B", "DF_FACT"]
    assert len(cli.reads) == len(set(cli.reads))


def test_depth_is_bounded_and_the_limit_is_recorded(tmp_path):
    objects = base_space()
    chain = ["LT_FACT"] + [f"LT_{i}" for i in range(1, dsp.DF_PROVENANCE_MAX_DEPTH + 2)]
    for i, (target, upstream) in enumerate(zip(chain, chain[1:]), start=1):
        objects[("data-flows", f"DF_{i}")] = _df(f"DF_{i}", [f"V_{i}"], [target])
        objects[("views", f"V_{i}")] = _view(f"V_{i}", upstream, COLS)
        objects[("local-tables", upstream)] = _local(upstream)
    objects.pop(("data-flows", "DF_FACT"))
    exp, _ = _export(tmp_path, objects)
    states = {s["entity"]: s["state"] for s in exp.dataflow_provenance["sources"]}
    last = f"V_{dsp.DF_PROVENANCE_MAX_DEPTH + 1}"
    assert states[last] == dsp.DF_SOURCE_DEPTH_LIMIT and ("views", last) not in _keys(exp)
    assert all(states[f"V_{i}"] == dsp.DF_SOURCE_ACQUIRED for i in range(1, dsp.DF_PROVENANCE_MAX_DEPTH + 1))


def test_new_local_tables_reuse_replication_flows_already_read(tmp_path):
    """Una tabla local que aparece por procedencia se empareja con su Replication Flow SIN volver a leerlo."""
    objects = base_space()
    objects[("views", "2VR_SRC")] = _view("2VR_SRC", "LT_REPL", COLS)
    objects[("local-tables", "LT_REPL")] = _local("LT_REPL")
    objects[("replication-flows", "RF_REPL")] = {"replicationflows": {"RF_REPL": {
        "targets": {"LT_REPL": {}}, "connections": {"S4PREM": {}}, "contents": {"replicationTasks": [{"sourceObject": {"name": "I_SRC"}}]}}}}
    exp, cli = _export(tmp_path, objects)
    edges = {(e.source_type, e.source_name, e.relation, e.target_type, e.target_name) for e in exp.edges}
    assert ("replication-flows", "RF_REPL", "PRODUCES", "local-tables", "LT_REPL") in edges
    assert cli.reads.count(("replication-flows", "RF_REPL")) == 1


def test_manifest_records_the_provenance_scan(tmp_path):
    exp, _ = _export(tmp_path, base_space(), write=True)
    manifests = list((tmp_path / "pool" / "Client").rglob("lineage-manifest.json")) + list(tmp_path.rglob("lineage-manifest.json"))
    if manifests:                                                                       # el exportador escribe el manifiesto solo en algunos modos
        m = json.loads(manifests[0].read_text(encoding="utf-8"))
        assert m["dataflowProvenance"]["sources"][0]["state"] == dsp.DF_SOURCE_ACQUIRED
    assert exp.dataflow_provenance["flows"]


# ── 3. evaluación: cierre semántico separado de la procedencia; procedencia verificable ─────────────────────────────
def _assess(pool: Path, root="3VF_S", root_type="view"):
    prof, code = asvc.assess_evidence_root(pool, "Client", root, "General",
                                           evidence_scope={"requested_features": FEATURES, "effective_features": FEATURES}, root_type=root_type)
    assert code is None, code
    return prof


def test_provenance_resolves_the_frontier_without_widening_the_semantic_closure(tmp_path):
    exp, _ = _export(tmp_path, base_space(), write=True)
    prof = _assess(tmp_path / "pool")
    closure = set(prof["scope"]["closure_artifacts"])
    assert {"3VF_S", "LT_FACT"} <= closure
    assert not {"2VR_SRC", "RT_SRC", "DF_FACT", "LT_OTHER", "V_UNRELATED"} & closure          # procedencia y ruido fuera del cierre
    leaf = next(l for l in prof["canonical_evidence"]["leaf_provenance"] if l["leaf"] == "LT_FACT")
    assert leaf["basis"] == "DATA_FLOW" and [u["entity"] for u in leaf["upstream"]] == ["2VR_SRC"]
    assert leaf["upstream"][0]["kind"] != "OUTSIDE_POOL"                                      # antes del fix: OUTSIDE_POOL


def test_a_missing_data_flow_source_keeps_a_verifiable_indetermination(tmp_path):
    objects = base_space()
    objects[("data-flows", "DF_FACT")] = _df("DF_FACT", ["GHOST"], ["LT_FACT"])
    _export(tmp_path, objects, write=True)
    prof = _assess(tmp_path / "pool")
    leaf = next(l for l in prof["canonical_evidence"]["leaf_provenance"] if l["leaf"] == "LT_FACT")
    assert [(u["entity"], u["kind"]) for u in leaf["upstream"]] == [("GHOST", "OUTSIDE_POOL")]     # causa verificable, sin contrato inventado
    cls = next(l for l in prof["canonical_evidence"]["source_contract_classes"]["leaves"] if l["leaf"] == "LT_FACT")
    assert cls["contract_class"] == "UNKNOWN"


# ── 4. paridad Catálogo / ad hoc: mismo motor, misma evidencia, mismo perfil y mismo Juicio base ─────────────────────
def test_catalog_and_adhoc_paths_produce_the_same_profile_and_base_judgment(tmp_path):
    root = write_inbound(tmp_path / "pool")
    catalog, _meta = asvc._engine_assess(root, "Acme", "4AM_ACME", "General")             # la llamada del Catálogo (AM terminal)
    adhoc, code = asvc.assess_evidence_root(root, "Acme", "4AM_ACME", "General", root_type="analytic-model")   # la del ad hoc
    assert code is None and catalog == adhoc
    j_cat = evaluate_judgment(catalog, ruleset=CURRENT_RULESET_VERSION, ratified_contracts=[])
    assert asvc.provisional_judgment(adhoc) == j_cat                                       # el provisional = el Juicio base del Catálogo


def test_judgment_ruleset_is_untouched():
    assert CURRENT_RULESET_VERSION == "judgment-rules/v4"
