"""Pipeline visual de «Orquestación de ingesta» + registro central de íconos: nodos solo con evidencia, aristas reales, tipo por metadata (nunca por prefijo), renderer compartido intacto."""
import re

from webapp import main
from webapp.main import _catalog_lineage_svg
from webapp.presentation import artifact_icons as ai
from webapp.services import catalog_ingestion_orchestration as co

T = "1TR_SAP_MD_ACC"


def _orch(target_kind, chains=(), state=co.CONFIRMED, physical=None):
    p = co.Producer(flow="RF_A", load_type="REPLICATE", physical_target=physical or T, target_kind=target_kind, source_objects=("ZCDS_X",), chains=tuple(chains), chains_state=state)
    return co.IngestionOrchestration(T, co.ESTABLISHED, (p,))


def test_icon_registry_covers_the_eleven_types_with_safe_light_svg():
    assert set(ai.ARTIFACT_TYPES) == {"table", "graphical_view", "sql_view", "er_model", "analytic_model", "data_product", "data_flow", "replication_flow", "transformation_flow", "task_chain", "data_access_control"}
    for k in ai.ARTIFACT_TYPES:
        svg = ai.symbol_markup(k)
        assert 'viewBox="0 0 24 24"' in svg and len(svg) < 1500 and not re.search(r"<script|href=|on\w+=|<image|style=|#[0-9a-fA-F]{3,6}\b", svg)


def test_type_comes_from_metadata_never_from_the_name_prefix():
    assert ai.artifact_type(object_type="replication-flows") == "replication_flow" and ai.artifact_type(artefacto_tecnico="Tabla local") == "table"
    assert ai.artifact_type(artefacto_tecnico="Vista") is None and ai.artifact_type(artefacto_tecnico="Vista", view_type="SQL") == "sql_view"
    assert ai.artifact_type() is None                                             # sin metadata: no se adivina por nombre


def test_tc_rf_delta_table_pipeline_reads_artifact_first_with_only_proven_nodes():
    chain = co.Chain("TC_MD_01", "Master Data 01", ("TC_MD_01",))
    m = co.pipeline_model(_orch(co.DELTA_SHADOW, [chain], physical=T + "_Delta"))
    assert {n["name"] for n in m["nodes"]} == {"tc:TC_MD_01", "rf:RF_A", f"tbl:{T}_Delta", f"tbl:{T}"}
    # orden narrativo (izquierda → derecha): tabla base → tabla técnica `_Delta` → Replication Flow → Task Chain
    assert m["edge_kind"] == {(f"tbl:{T}", f"tbl:{T}_Delta"): "proj", ("rf:RF_A", "tc:TC_MD_01"): "orch"}      # RF → _Delta es dato (sin clase); base — _Delta proyección; TC orquesta
    up = {n["name"]: n["upstreams"] for n in m["nodes"]}
    assert up == {f"tbl:{T}": [], f"tbl:{T}_Delta": [f"tbl:{T}"], "rf:RF_A": [f"tbl:{T}_Delta"], "tc:TC_MD_01": ["rf:RF_A"]}    # sin arista RF → base
    assert set(m["edge_title"]) == {(f"tbl:{T}", f"tbl:{T}_Delta"), (f"tbl:{T}_Delta", "rf:RF_A"), ("rf:RF_A", "tc:TC_MD_01")}


def test_nested_task_chains_are_ordered_from_the_direct_chain_to_the_parent():
    chain = co.Chain("TC_CHILD", None, ("TC_PARENT", "TC_CHILD"))
    m = co.pipeline_model(_orch(co.BASE, [chain]))
    up = {n["name"]: n["upstreams"] for n in m["nodes"]}
    assert up == {f"tbl:{T}": [], "rf:RF_A": [f"tbl:{T}"], "tc:TC_CHILD": ["rf:RF_A"], "tc:TC_PARENT": ["tc:TC_CHILD"]}


def test_rf_only_unverified_target_and_not_evaluated_states_invent_nothing():
    m = co.pipeline_model(_orch(co.BASE, state=co.NOT_FOUND_IN_EXPORT))
    assert {n["name"] for n in m["nodes"]} == {"rf:RF_A", f"tbl:{T}"} and m["edge_kind"] == {}
    m2 = co.pipeline_model(_orch(co.UNKNOWN, state=co.NOT_EVALUATED))
    assert m2["edge_kind"] == {(f"tbl:{T}", "rf:RF_A"): "unverified"}
    assert co.pipeline_model(co.IngestionOrchestration(T, co.NOT_EVALUATED, note="x")) is None
    assert "ai-icon" not in co.render_html(co.IngestionOrchestration(T, co.NOT_EVALUATED, note="no se evaluó"), "a", diagram=_catalog_lineage_svg)


def test_html_has_diagram_text_notes_and_keeps_the_full_technical_table():
    html = co.render_html(_orch(co.DELTA_SHADOW, [co.Chain("TC_1", None, ("TC_1",))], physical=T + "_Delta"), "a", diagram=_catalog_lineage_svg)
    assert 'class="lineage-svg"' in html and 'role="img"' in html and "aria-label=" in html and html.count("ai-icon") >= 4
    assert "Detalle técnico" in html and "<table>" in html and "Destino físico" in html and "Tabla base" in html         # contenido previo intacto en la vista secundaria
    assert "no es la misma tabla" not in html and "<image" not in html and "<script" not in html and 'href="#' not in html   # sin raster, sin scripts, sin enlaces inventados
    no_diagram = co.render_html(_orch(co.BASE), "a")
    assert "lineage-svg" not in no_diagram and "<table>" in no_diagram


def test_no_textual_legend_and_technical_detail_uses_the_standard_hub_collapsible():
    html = co.render_html(_orch(co.DELTA_SHADOW, [co.Chain("TC_1", None, ("TC_1",))], physical=T + "_Delta"), "a", diagram=_catalog_lineage_svg)
    for gone in ("orch-legend", "Flujo de datos", "no mueve datos", "Orquestación (Task Chain", "Proyección (no es una carga)", "content-collapsible"):
        assert gone not in html
    inner = html.split("Detalle técnico")[0].rsplit("<details", 1)[1]
    assert inner.startswith(' class="dashboard-section orch-text"') and "dashboard-section__head" in inner and html.count('<details class="dashboard-section') == 2


def test_shared_lineage_renderer_is_unchanged_without_the_new_optional_keys():
    nodes = [{"name": "A", "label": "A", "title": "A", "layer": "1", "upstreams": [], "assoc_upstreams": [], "slug": "a", "num": 1, "is_factcube": False},
             {"name": "B", "label": "B_LARGO_NOMBRE_TECNICO", "title": "B", "layer": "3", "upstreams": ["A"], "assoc_upstreams": [], "slug": "b", "num": 2, "is_factcube": True}]
    svg = _catalog_lineage_svg(nodes, False, {"A", "B"})
    assert "ai-icon" not in svg and "ln-node-a--static" not in svg and 'class="ln-num"' in svg and 'href="#a"' in svg and "ln-edge--" not in svg
    assert 'aria-label="Diagrama de trazabilidad del flujo"' in svg


def test_arrowheads_point_to_the_artifact_and_projection_has_none():
    html = co.render_html(_orch(co.DELTA_SHADOW, [co.Chain("TC_1", None, ("TC_1",))], physical=T + "_Delta"), "a", diagram=_catalog_lineage_svg)
    paths = re.findall(r'<path class="(ln-edge[^"]*)" d="[^"]*"( marker-start="url\(#ln-arrow-start-[0-9a-f]+\)")?>', html)
    by_kind = {("proj" if "--proj" in c else "orch" if "--orch" in c else "data"): bool(m) for c, m in paths}
    assert by_kind == {"proj": False, "orch": True, "data": True} and len(paths) == 3          # solo la proyección pierde la punta
    assert "marker-end" not in html and re.search(r'<marker id="ln-arrow-start-[0-9a-f]+"', html) and 'orient="auto-start-reverse"' in html
    assert "<title>TC_1 orquesta RF_A</title>" in html and f"<title>{T} es la proyección activa de {T}_Delta (no es una carga)</title>" in html


def test_edge_titles_and_start_arrow_are_opt_in_and_do_not_change_the_default_renderer():
    nodes = [{"name": "A", "label": "A", "title": "A", "layer": "1", "upstreams": [], "assoc_upstreams": [], "slug": "a", "num": 1, "is_factcube": False},
             {"name": "B", "label": "B", "title": "B", "layer": "3", "upstreams": ["A"], "assoc_upstreams": [], "slug": "b", "num": 2, "is_factcube": True}]
    base = _catalog_lineage_svg(nodes, False, {"A", "B"})
    assert base == _catalog_lineage_svg(nodes, False, {"A", "B"}, arrow_at="end", edge_title=None) and "ln-arrow-start" not in base and not re.search(r"<path[^>]*><title>", base)


def test_numbered_lineage_adds_the_type_icon_and_keeps_the_number_and_link():
    nodes = [{"name": "A", "label": "A", "title": "A", "layer": "1", "upstreams": [], "assoc_upstreams": [], "slug": "a", "num": 1, "is_factcube": False, "icon": "table"},
             {"name": "B", "label": "B", "title": "B", "layer": "3", "upstreams": ["A"], "assoc_upstreams": [], "slug": "b", "num": 2, "is_factcube": True, "icon": "sql_view"}]
    svg = _catalog_lineage_svg(nodes, False, {"A", "B"})
    assert svg.count('<g class="ai-icon ') == 2 and 'class="ln-num"' in svg and ">01<" in svg and ">02<" in svg and 'href="#a"' in svg and 'href="#b"' in svg


def test_numbered_lineage_is_byte_identical_when_no_node_has_a_resolvable_type():
    plain = [{"name": "A", "label": "A", "title": "A", "layer": "1", "upstreams": [], "assoc_upstreams": [], "slug": "a", "num": 1, "is_factcube": False},
             {"name": "B", "label": "B", "title": "B", "layer": "3", "upstreams": ["A"], "assoc_upstreams": [], "slug": "b", "num": 2, "is_factcube": True}]
    assert main._catalog_node_icon({"artefacto_tecnico": "Vista"}, "V", {}) == {}                       # Vista sin viewType: no se adivina
    assert main._catalog_node_icon({}, "X", {}) == {} and main._catalog_node_icon({"artefacto_tecnico": "Desconocido"}, "X", {}) == {}
    with_empty_icons = [{**n, **main._catalog_node_icon({"artefacto_tecnico": "Vista"}, n["name"], {})} for n in plain]
    assert _catalog_lineage_svg(with_empty_icons, False, {"A", "B"}) == _catalog_lineage_svg(plain, False, {"A", "B"}) and "ai-icon" not in _catalog_lineage_svg(plain, False, {"A", "B"})
    assert main._catalog_node_icon({"artefacto_tecnico": "Vista"}, "V", {"V": "GRAPHICAL"}) == {"icon": "graphical_view"}
    assert main._catalog_node_icon({"artefacto_tecnico": "Tabla local"}, "T", {}) == {"icon": "table"}


def test_legacy_renderer_edges_always_keep_the_arrowhead_without_edge_kind():
    nodes = [{"name": "A", "label": "A", "title": "A", "layer": "1", "upstreams": [], "assoc_upstreams": [], "slug": "a", "num": 1, "is_factcube": False},
             {"name": "B", "label": "B", "title": "B", "layer": "3", "upstreams": ["A"], "assoc_upstreams": [], "slug": "b", "num": 2, "is_factcube": True}]
    svg = _catalog_lineage_svg(nodes, False, {"A", "B"})
    assert svg.count('marker-end="url(#ln-arrow)"') == 1


def test_start_arrow_marker_id_is_unique_per_diagram():
    a = co.render_html(_orch(co.BASE), "a", diagram=_catalog_lineage_svg)
    b = co.render_html(co.IngestionOrchestration("1TR_OTHER", co.ESTABLISHED, (co.Producer(flow="RF_B", load_type="REPLICATE", physical_target="1TR_OTHER", target_kind=co.BASE, source_objects=("ZCDS_Y",), chains=(), chains_state=co.NOT_FOUND_IN_EXPORT),)), "b", diagram=_catalog_lineage_svg)
    ida, idb = (re.search(r'<marker id="(ln-arrow-start-[0-9a-f]+)"', x).group(1) for x in (a, b))
    assert ida != idb and f'url(#{ida})' in a and f'url(#{idb})' in b      # `url(#id)` resuelve al primero del documento: un id compartido rompería diagramas en <details> cerrados


def test_no_chain_sentence_lives_only_in_the_technical_detail():
    html = co.render_html(_orch(co.BASE, state=co.NOT_FOUND_IN_EXPORT), "a", diagram=_catalog_lineage_svg)
    head, detail = html.split("Detalle técnico", 1)
    assert "Sin Task Chain comprobada" not in head and "Sin Task Chain comprobada" in detail          # fuera del diagrama/notas, dentro del detalle
    nd = co.render_html(_orch(co.UNKNOWN, state=co.NOT_EVALUATED), "a", diagram=_catalog_lineage_svg)
    assert "Orquestación no evaluada" in nd.split("Detalle técnico", 1)[0]                             # estado «no evaluada» sigue avisado junto al diagrama
