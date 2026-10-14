"""Pipeline visual de «Orquestación de ingesta» + registro central de íconos: nodos solo con evidencia, aristas reales, tipo por metadata (nunca por prefijo), renderer compartido intacto."""
import re

from webapp.main import _catalog_lineage_svg
from webapp.presentation import artifact_icons as ai
from webapp.services import catalog_ingestion_orchestration as co

T = "1TR_SAP_MD_ACC"


def _orch(target_kind, chains=(), state=co.CONFIRMED, physical=None):
    p = co.Producer(flow="RF_A", load_type="REPLICATE", physical_target=physical or T, target_kind=target_kind, source_objects=("ZCDS_X",), chains=tuple(chains), chains_state=state)
    return co.IngestionOrchestration(T, co.ESTABLISHED, (p,))


def test_icon_registry_covers_the_ten_types_with_safe_light_svg():
    assert set(ai.ARTIFACT_TYPES) == {"table", "graphical_view", "sql_view", "er_model", "analytic_model", "data_flow", "replication_flow", "transformation_flow", "task_chain", "data_access_control"}
    for k in ai.ARTIFACT_TYPES:
        svg = ai.symbol_markup(k)
        assert 'viewBox="0 0 24 24"' in svg and len(svg) < 1500 and not re.search(r"<script|href=|on\w+=|<image|style=|#[0-9a-fA-F]{3,6}\b", svg)


def test_type_comes_from_metadata_never_from_the_name_prefix():
    assert ai.artifact_type(object_type="replication-flows") == "replication_flow" and ai.artifact_type(artefacto_tecnico="Tabla local") == "table"
    assert ai.artifact_type(artefacto_tecnico="Vista") is None and ai.artifact_type(artefacto_tecnico="Vista", view_type="SQL") == "sql_view"
    assert ai.artifact_type() is None                                             # sin metadata: no se adivina por nombre


def test_tc_rf_delta_table_pipeline_has_only_proven_nodes_and_distinct_edge_kinds():
    chain = co.Chain("TC_MD_01", "Master Data 01", ("TC_MD_01",))
    m = co.pipeline_model(_orch(co.DELTA_SHADOW, [chain], physical=T + "_Delta"))
    assert {n["name"] for n in m["nodes"]} == {"tc:TC_MD_01", "rf:RF_A", f"tbl:{T}_Delta", f"tbl:{T}"}
    assert m["edge_kind"] == {("tc:TC_MD_01", "rf:RF_A"): "orch", (f"tbl:{T}_Delta", f"tbl:{T}"): "proj"}      # RF → _Delta es dato; _Delta → base es proyección; TC → RF es orquestación
    rf = next(n for n in m["nodes"] if n["name"] == "rf:RF_A")
    assert rf["upstreams"] == ["tc:TC_MD_01"] and next(n for n in m["nodes"] if n["name"] == f"tbl:{T}")["upstreams"] == [f"tbl:{T}_Delta"]   # sin arista RF → base


def test_rf_only_unverified_target_and_not_evaluated_states_invent_nothing():
    m = co.pipeline_model(_orch(co.BASE, state=co.NOT_FOUND_IN_EXPORT))
    assert {n["name"] for n in m["nodes"]} == {"rf:RF_A", f"tbl:{T}"} and m["edge_kind"] == {}
    m2 = co.pipeline_model(_orch(co.UNKNOWN, state=co.NOT_EVALUATED))
    assert m2["edge_kind"] == {("rf:RF_A", f"tbl:{T}"): "unverified"}
    assert co.pipeline_model(co.IngestionOrchestration(T, co.NOT_EVALUATED, note="x")) is None
    assert "ai-icon" not in co.render_html(co.IngestionOrchestration(T, co.NOT_EVALUATED, note="no se evaluó"), "a", diagram=_catalog_lineage_svg)


def test_html_has_diagram_text_notes_and_keeps_the_full_technical_table():
    html = co.render_html(_orch(co.DELTA_SHADOW, [co.Chain("TC_1", None, ("TC_1",))], physical=T + "_Delta"), "a", diagram=_catalog_lineage_svg)
    assert 'class="lineage-svg"' in html and 'role="img"' in html and "aria-label=" in html and html.count("ai-icon") >= 4
    assert "Detalle técnico" in html and "proyección activa (no es la misma tabla)" in html and "<table>" in html     # contenido previo intacto en la vista secundaria
    assert "<image" not in html and "<script" not in html and 'href="#' not in html                                    # sin raster, sin scripts, sin enlaces inventados
    no_diagram = co.render_html(_orch(co.BASE), "a")
    assert "lineage-svg" not in no_diagram and "<table>" in no_diagram


def test_shared_lineage_renderer_is_unchanged_without_the_new_optional_keys():
    nodes = [{"name": "A", "label": "A", "title": "A", "layer": "1", "upstreams": [], "assoc_upstreams": [], "slug": "a", "num": 1, "is_factcube": False},
             {"name": "B", "label": "B_LARGO_NOMBRE_TECNICO", "title": "B", "layer": "3", "upstreams": ["A"], "assoc_upstreams": [], "slug": "b", "num": 2, "is_factcube": True}]
    svg = _catalog_lineage_svg(nodes, False, {"A", "B"})
    assert "ai-icon" not in svg and "ln-node-a--static" not in svg and 'class="ln-num"' in svg and 'href="#a"' in svg and "ln-edge--" not in svg
    assert 'aria-label="Diagrama de trazabilidad del flujo"' in svg


def test_projection_edge_has_no_arrowhead_while_data_and_orchestration_edges_keep_theirs():
    html = co.render_html(_orch(co.DELTA_SHADOW, [co.Chain("TC_1", None, ("TC_1",))], physical=T + "_Delta"), "a", diagram=_catalog_lineage_svg)
    paths = re.findall(r'<path class="(ln-edge[^"]*)" d="[^"]*"( marker-end="url\(#ln-arrow\)")?/>', html.split('<ul class="orch-legend">')[0])
    by_kind = {("proj" if "--proj" in c else "orch" if "--orch" in c else "data"): bool(m) for c, m in paths}
    assert by_kind == {"proj": False, "orch": True, "data": True}                       # solo la proyección pierde la punta; datos y orquestación intactos
    assert len(paths) == 3 and "ln-edge--proj" in html


def test_legend_states_that_the_projection_is_not_a_load():
    html = co.render_html(_orch(co.DELTA_SHADOW, physical=T + "_Delta"), "a", diagram=_catalog_lineage_svg)
    assert "Proyección (no es una carga): tabla técnica — tabla base" in html
    assert "proyección activa (no es la misma tabla)" in html                           # el detalle textual conserva su semántica


def test_legacy_renderer_edges_always_keep_the_arrowhead_without_edge_kind():
    nodes = [{"name": "A", "label": "A", "title": "A", "layer": "1", "upstreams": [], "assoc_upstreams": [], "slug": "a", "num": 1, "is_factcube": False},
             {"name": "B", "label": "B", "title": "B", "layer": "3", "upstreams": ["A"], "assoc_upstreams": [], "slug": "b", "num": 2, "is_factcube": True}]
    svg = _catalog_lineage_svg(nodes, False, {"A", "B"})
    assert svg.count('marker-end="url(#ln-arrow)"') == 1
