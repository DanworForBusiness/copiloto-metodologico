"""Data Product como tipo de artefacto PROPIO del registro central de íconos, aplicado de forma transversal (listado, encabezado, linaje completo y vista simple), sin tocar el cubo
del Modelo Analítico ni los diagramas legacy."""
from __future__ import annotations

import re
from types import SimpleNamespace

import webapp.main as main
from tests.webapp.test_catalog_navigation_states import _tab
from webapp.presentation import artifact_icons as ai


def _dp(*, kind_label="Tabla local"):
    nodes = [
        {"name": "hana::I_CUSTOMER", "display": "I_CUSTOMER", "label": "Customer", "kind_label": kind_label, "runtime": "hana", "upstreams": [], "assoc_upstreams": [], "published": False},
        {"name": "hdlf::SALES", "display": "SALES", "label": "Sales", "kind_label": kind_label, "runtime": "object_store", "upstreams": ["hana::I_CUSTOMER"], "assoc_upstreams": [], "published": True},
    ]
    if not kind_label:
        for n in nodes:
            n.pop("kind_label")
    return SimpleNamespace(entities=[{"kind": "entity", "name": "DP.SALES", "label": "Sales"}], lineage_nodes=nodes, business_name="Ventas DP", title="Ventas DP", product_id="DP_X")


def test_data_product_is_its_own_type_distinct_from_the_analytic_model_cube():
    assert "data_product" in ai.ARTIFACT_TYPES and "analytic_model" in ai.ARTIFACT_TYPES
    assert ai.ARTIFACT_TYPES["data_product"].glyph != ai.ARTIFACT_TYPES["analytic_model"].glyph and ai.label_of("data_product") == "Data Product"
    assert ai.artifact_type(artefacto_tecnico="Data Product") == "data_product" and ai.artifact_type(object_type="data-products") == "data_product"
    assert ai.artifact_type(artefacto_tecnico="Analytic Model") == "analytic_model" and ai.artifact_type(object_type="analytic-models") == "analytic_model"   # coexisten
    assert "M12 12l8-5" not in ai.ARTIFACT_TYPES["data_product"].glyph                                              # sin las aristas del cubo
    svg = ai.symbol_markup("data_product")
    assert 'viewBox="0 0 24 24"' in svg and not re.search(r"<script|href=|on\w+=|<image|style=|#[0-9a-fA-F]{3,6}\b", svg)


def test_standalone_symbol_is_accessible():
    assert 'role="img"' in ai.symbol_markup("data_product") and "<title>Data Product</title>" in ai.symbol_markup("data_product")


def test_full_data_product_lineage_shows_the_product_icon_on_the_root_and_the_artifact_types_elsewhere():
    svg = main._catalog_data_product_lineage_svg(_dp(), True)
    assert svg.count("ai-icon--data_product") == 1 and svg.count("ai-icon--table") == 2 and "ai-icon--analytic_model" not in svg
    assert ">Data Product<" in svg and 'class="ln-num"' in svg and "Ventas DP" in svg                              # conserva la numeración y el subtexto


def test_full_lineage_without_node_type_metadata_keeps_the_legacy_nodes_and_only_the_root_gets_the_icon():
    svg = main._catalog_data_product_lineage_svg(_dp(kind_label=""), True)
    assert svg.count("ai-icon--data_product") == 1 and "ai-icon--table" not in svg                                  # nunca se adivina el tipo por nombre


def test_simple_data_product_view_has_the_product_icon_and_types_only_when_the_lineage_knows_them():
    svg = main._catalog_data_product_lineage_svg(_dp(), False)
    assert svg.count("ai-icon--data_product") == 1 and "ai-icon--analytic_model" not in svg and "Ventas DP" in svg
    bare = SimpleNamespace(entities=[{"kind": "entity", "name": "DP.SALES", "label": ""}], lineage_nodes=[], business_name="P", title="P", product_id="P")
    legacy = main._catalog_data_product_lineage_svg(bare, False)
    assert '<tspan class="ln-num">02</tspan> SALES</text>' in legacy and legacy.count("ai-icon--") == 1   # entidad sin tipo: salida histórica (solo el ícono del producto)


def test_listing_and_detail_header_carry_no_icon_only_the_lineage_diagrams_do():
    dp = SimpleNamespace(veredicto="buena", business_name="DP", title="DP", apto="x", product_id="p", artifact_names=[], data_categories=[], industries=[], delivery_mode="")
    assert "ai-icon" not in _tab(catalog_data_products=[dp])
    header = main.templates.get_template("partials/catalog_data_product_content.html")
    out = header.render(product=SimpleNamespace(business_name="DP", title="DP", product_id="p", veredicto="buena", apto="x", artifact_names=[], sections=[]), sections=[], lineage_svg="",
                        show_lineage=False, pack="datasphere_core", request=None)
    assert "ai-icon" not in out and "Cat&aacute;logo Danwor &middot; Data Products" in out


def test_domain_lineage_icons_for_other_artifacts_are_unchanged():
    nodes = [{"name": "A", "label": "A", "title": "A", "layer": "1", "upstreams": [], "assoc_upstreams": [], "slug": "a", "num": 1, "is_factcube": False, "icon": "table"},
             {"name": "B", "label": "B", "title": "B", "layer": "3", "upstreams": ["A"], "assoc_upstreams": [], "slug": "b", "num": 2, "is_factcube": True, "icon": "analytic_model"}]
    svg = main._catalog_lineage_svg(nodes, False, {"A", "B"})
    assert svg.count("ai-icon--table") == 1 and svg.count("ai-icon--analytic_model") == 1 and "ai-icon--data_product" not in svg
