"""
test_catalog_filter_search.py — filtro del 2.º dropdown + buscador en vivo del Catálogo (Modelos / Data Products / Líneas de negocio / Clientes).

Contrato (solo UX y filtrado en el cliente; ni backend ni contenido del catálogo cambian):
  * dropdown 1 siempre visible y funcional; elegido el valor del 2.º dropdown este se reemplaza por «Buscar...» + «Limpiar» y aparece la leyenda «Filtrado por <catálogo> ✕»
    con el primitivo del Hub (`.dashboard-subhead__filter/__reset`);
  * la ✕ restaura el 2.º dropdown; «Limpiar» solo vacía el texto; cambiar el dropdown 1 resetea todo; sin resultados → estado vacío del Hub;
  * Data Products no tiene 2.º dropdown: buscador desde el inicio; módulos 01 (conexiones) y 02 (seguridad) no se tocan;
  * el comportamiento (estado, búsqueda sin tildes/mayúsculas por cualquier texto del tile) lo verifica el arnés de Node sobre el app.js real.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import webapp.main as main

REPO = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("catalog_filter_search.node.js")
DP = {"product_id": "DP_1", "title": "Producto", "business_name": "Producto uno", "veredicto": "plena", "apto": "Apto", "artifact_names": ["ART_1"],
      "data_categories": ["Finanzas"], "industries": ["Química"], "delivery_mode": "BDC"}


def render(number="04", stem="04_Modelado", **over):
    ctx = dict(document=SimpleNamespace(summary=SimpleNamespace(number=number, stem=stem)), pack="datasphere_core", request=None, catalog_models=["Despachos", "Compras"],
               catalog_domains=["Despachos"], catalog_lobs=[{"id": "FINANCE", "label": "Finanzas"}], catalog_clients=["Biogenesis", "Kensing"], catalog_data_products=[DP])
    ctx.update(over)
    return main.templates.get_template("partials/catalog_tab.html").render(**ctx)


def section(html, kind):
    start = html.index(f'data-catalog-kind="{kind}"')
    nxt = [html.index(f'data-catalog-kind="{k}"') for k in ("domains", "lob", "clients", "data_products") if k != kind and html.index(f'data-catalog-kind="{k}"') > start]
    return html[start: min(nxt)] if nxt else html[start:]


def test_the_catalog_bar_is_one_component_and_the_first_dropdown_is_untouched():
    html = render()
    assert 'x-data="dwCatalogBrowse()"' in html and "x-data=\"{ catalogKind: '' }\"" not in html
    head = html[html.index('<select id="catalog-kind"'): html.index("</select>", html.index('<select id="catalog-kind"'))]
    assert 'x-model="catalogKind"' in head and '@change="resetPick()"' in head                                     # cambiar el dropdown 1 resetea el 2.º dropdown, el buscador y la leyenda
    assert re.findall(r'<option value="([a-z_]*)">([^<]*)</option>', head)[:5] == [("", "Elegí un catálogo..."), ("domains", "Modelos"), ("data_products", "Data Products"), ("lob", "Líneas de negocio"), ("clients", "Clientes")]


@pytest.mark.parametrize("kind,select_id,label", [("domains", "catalog-dominio", "Modelo"), ("lob", "catalog-lob", "Línea de negocio"), ("clients", "catalog-client", "Cliente")])
def test_each_secondary_dropdown_is_replaced_by_search_and_clear_once_a_value_is_chosen(kind, select_id, label):
    sec = section(render(), kind)
    wrap = sec[sec.index('<div x-show="!picked">'): sec.index('<div x-show="showSearch" x-cloak>')]   # el dropdown 2 vive en un contenedor que se oculta al elegir
    assert f'<label for="{select_id}">{label}</label>' in wrap and f'<select id="{select_id}"' in wrap and '@change="pick($event)"' in wrap
    assert 'hx-get="/catalog/' in wrap and 'hx-trigger="change"' in wrap                                           # la carga de tiles sigue siendo la de siempre (htmx)
    row = sec[sec.index('<div x-show="showSearch" x-cloak>'):]
    row = row[: row.index("</button>") + len("</button>")]
    sid = re.search(r'<label for="(catalog-search-[a-z]+)">Buscar</label>', row).group(1)                         # etiqueta «Buscar» con la misma gramática que «Cliente»/«Modelo»
    assert f'id="{sid}"' in row and 'type="search"' in row and 'placeholder="Buscar..."' in row and 'aria-label="Buscar en el catálogo"' in row and 'x-model="q"' in row and '@input="search()"' in row
    assert '<button type="button" class="whatsnew-filter__clear" @click="clearSearch()">✕ Limpiar</button>' in row   # patrón del Hub para «✕ Limpiar»
    assert "<label class=\"whatsnew-filter" not in row                                                              # el contenedor del input NO es un <label>: dentro de `.tree-picker` heredaba display:block y margen inferior
    assert sec.count("data-catalog-results") == 1 and 'x-show="noMatch"' in sec and "Sin coincidencias" in sec    # estado vacío del Hub (`catalog_state.html`), no un hueco


def test_the_gold_legend_sits_on_the_catalog_title_row_before_the_plus_and_uses_the_shared_hub_primitive():
    html = render()
    legend = html[html.index('<p class="dashboard-subhead catalog-filter-legend"'): html.index("</p>", html.index('<p class="dashboard-subhead catalog-filter-legend"'))]
    assert "x-show=\"hasPick && catalogView !== 'publish'\"" in legend and '<span class="dashboard-subhead__filter">Filtrado por <strong x-text="pickedLabel"></strong>' in legend
    assert 'class="dashboard-subhead__reset"' in legend and '@click="clearPick()"' in legend and ">✕</button>" in legend
    assert ':aria-label="\'Quitar el filtro por \' + kindLabel + \': \' + pickedLabel"' in legend                    # nombre accesible: qué filtro y con qué valor (la leyenda muestra el valor elegido: «Filtrado por Finanzas»)
    head = html[html.index('<div class="tree-picker-head'): html.index('<div class="tree-picker-row"')]
    assert 'for="catalog-kind">Catálogo de datos</label>' in head and "catalog-filter-legend" in head                # en la MISMA fila que «CATÁLOGO DE DATOS»
    assert html.index("catalog-filter-legend") < html.index('id="catalog-kind"') < html.index('data-catalog-kind="domains"')
    # el «+» es absoluto (no agranda la fila); la leyenda queda a continuación del título y el «+» es absoluto
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8")
    assert ".catalog-picker--kind .tree-picker-head { display: flex; flex-wrap: wrap;" in css and ".catalog-filter-legend { margin: 0;" in css


def test_search_mode_has_no_extra_space_before_the_first_tile():
    """Causa del hueco de ~50px: el picker conservaba relleno + separador inferiores y `.whatsnew-filters` aportaba 18px de margen. En modo búsqueda el picker los pierde."""
    html = render()
    assert html.count(":class=\"picked && 'catalog-picker--search'\"") == 3 and 'class="tree-picker catalog-picker--secondary catalog-picker--search"' in html   # los 3 pickers + Data Products
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8")
    assert ".catalog-picker--search { padding-bottom: 0; border-bottom: 0; }" in css and ".catalog-picker--search .whatsnew-filters { margin: 0; }" in css


def test_data_products_have_no_second_dropdown_so_search_is_available_from_the_start():
    sec = section(render(), "data_products")
    assert "<select" not in sec and 'x-show="showSearch"' in sec and 'placeholder="Buscar..."' in sec and 'class="catalog-flows-list" data-catalog-results' in sec
    assert 'x-show="noMatch"' in sec
    empty = section(render(catalog_data_products=[]), "data_products")
    assert 'placeholder="Buscar..."' not in empty and "No se detectaron Data Products" in empty                        # sin productos no hay nada que buscar


def test_architecture_and_security_catalogs_are_untouched():
    for number, stem in (("01", "01_Arquitectura_y_Conectividad"), ("02", "02_Seguridad_y_Gobernanza")):
        html = render(number=number, stem=stem, catalog_connection_types=[], catalog_security_families=[])
        assert "dwCatalogBrowse" not in html and "data-catalog-search" not in html


def test_search_is_client_side_only_no_route_or_backend_change():
    js = (REPO / "webapp/static/app.js").read_text(encoding="utf-8")
    block = js[js.index("const DwCatalogSearch = {"): js.index("function dwCatalogBrowse()") + 3000]
    assert "fetch(" not in block and "htmx.ajax" not in block and "XMLHttpRequest" not in block                      # nunca pide nada: filtra los tiles ya renderizados
    assert 'normalize("NFD")' in block and "toLowerCase()" in block                                                  # sin tildes ni mayúsculas


@pytest.mark.skipif(shutil.which("node") is None, reason="node no está disponible")
def test_catalog_filter_search_harness():
    r = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
