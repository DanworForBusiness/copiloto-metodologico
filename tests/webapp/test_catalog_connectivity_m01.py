"""Catálogo de conectividad del módulo 01: dropdown «Catálogo de conectividad» (Conexiones · Clientes · Disponibilidad SAP) con la mecánica
del 04; Clientes sobre conexiones PUBLICADAS (`connections_for_client`); Disponibilidad SAP SOLO desde el snapshot saneado del pack."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import webapp.main as main
from webapp.services import catalog_reads, sap_connection_catalog

REPO = Path(__file__).resolve().parents[2]
PACK_JSON = REPO / "content_packs" / "datasphere_core" / "sap_connection_types.json"
HARNESS = Path(__file__).with_name("catalog_sap_types.node.js")
M01 = ("01", "01_Arquitectura_y_Conectividad")


def _service():
    mod, _entries = catalog_reads._catalog_architecture_connections()
    if mod is None:                                                    # sin catalog_local: el módulo igual se importa
        import sys
        sys.path.insert(0, str(REPO / "tools" / "catalog"))
        import catalog_service as mod  # noqa: PLC0415
    return mod


def _entries(mod):
    E = mod.CatalogConnectionEntry
    return [
        E(path="a1", cliente="Acme", space="DEV", connection="ERP_DEV", type_id="ABAP", dp_agent=True, features=["remoteTables"]),
        E(path="a2", cliente="Acme", space="QAS", connection="ERP_QAS", type_id="ABAP", dp_agent=True, features=["remoteTables"]),
        E(path="a3", cliente="Acme", space="DEV", connection="SQL_DEV", type_id="MSSQL"),
        E(path="b1", cliente="Beta", space="PRD", connection="ECC_PRD", type_id="SAPECC"),
    ]


def render(number, stem, **over):
    ctx = dict(document=SimpleNamespace(summary=SimpleNamespace(number=number, stem=stem)), pack="datasphere_core", request=None,
               catalog_models=[], catalog_domains=[], catalog_lobs=[], catalog_clients=[], catalog_data_products=[],
               catalog_connection_types=[{"value": "ABAP", "label": "ABAP"}], catalog_security_families=[], catalog_connection_clients=[])
    ctx.update(over)
    return main.templates.get_template("partials/catalog_tab.html").render(**ctx)


def _sap_section(html):
    start = html.index('data-catalog-kind="sap_types"')
    return html[start:]


# ═══════════════ navegación (dropdown 1) ═══════════════
def test_dropdown_lists_the_three_catalogs_in_order_and_starts_in_connections():
    html = render(*M01)
    sel = html[html.index('<select id="catalog-conn-kind"'):]
    sel = sel[:sel.index("</select>")]
    assert re.findall(r'<option value="([^"]+)">([^<]+)</option>', sel) == [
        ("connections", "Conexiones"), ("clients", "Clientes"), ("sap_types", "Disponibilidad SAP")]
    assert "x-data=\"dwCatalogBrowse('connections')\"" in html and "Catálogo de conectividad" in html
    conns = html[html.index('data-catalog-kind="connections"') - 60:html.index('data-catalog-kind="connections"')]
    assert "x-cloak" not in conns                                            # «Conexiones» visible desde el primer render
    for kind in ("clients", "sap_types"):
        assert re.search(rf'x-show="catalogKind === \'{kind}\'" x-cloak data-catalog-kind="{kind}"', html)


def test_connections_view_keeps_the_existing_selector_route_and_guide():
    html = render(*M01)
    assert 'hx-get="/catalog/connections?pack=datasphere_core" hx-target="#catalog-flows"' in html
    assert '<select id="catalog-type-id" name="type_id" @change="pick($event)"' in html
    assert "1 origen disponible" in html and "¿Falta un Origen?" in html
    assert 'id="catalog-search-connections"' in html                         # buscador del 04 (search_row)


def test_module_04_and_02_do_not_receive_the_connectivity_catalog():
    for number, stem in (("04", "04_Modelado"), ("02", "02_Seguridad_y_Gobernanza")):
        html = render(number, stem)
        assert "catalog-conn-kind" not in html and "data-sap-type" not in html and "dwSapTypes" not in html


# ═══════════════ Clientes (relaciones ya publicadas) ═══════════════
def test_client_groups_reuse_published_connections_without_new_relations():
    mod = _service()
    entries = _entries(mod)
    assert mod.connection_clients(entries) == ["Acme", "Beta"]
    groups = mod.connection_type_groups_for_client(entries, "Acme")
    assert [(g["type_id"], g["cliente"], g["total_count"]) for g in groups] == [("ABAP", "Acme", 2), ("MSSQL", "Acme", 1)]
    assert {c.path for g in groups for c in g["connections"]} <= {e.path for e in mod.connections_for_client(entries, "Acme")}
    assert mod.connection_type_groups_for_client(entries, "Nadie") == []


def test_clients_view_renders_type_tiles_that_open_the_existing_detail(monkeypatch):
    mod = _service()
    entries = _entries(mod)
    monkeypatch.setattr(catalog_reads, "_catalog_architecture_connections", lambda: (mod, entries))
    clients = catalog_reads._catalog_connection_clients()
    html = render(*M01, catalog_connection_clients=clients)
    acme = html[html.index('data-conn-client="Acme"'):html.index('data-conn-client="Beta"')]
    assert re.findall(r"<strong>([^<]+)</strong>\s*<span class=\"catalog-card__verdict", acme) == ["ABAP", "MSSQL"]
    assert "/catalog/connections/detail?cliente=Acme&type_id=ABAP&pack=datasphere_core" in acme
    assert "/catalog/connections/detail?cliente=Acme&type_id=MSSQL&pack=datasphere_core" in acme
    assert "SAPECC" not in acme                                              # nada de otro cliente
    assert ':data-catalog-results="picked === &#34;Acme&#34; ? \'\' : null"' in html   # el buscador opera solo sobre el cliente elegido
    assert "2 clientes disponibles" in html


def test_connections_tile_keeps_its_output_when_not_grouped_by_type():
    mod = _service()
    groups = mod.connection_client_groups(mod.connections_for_type(_entries(mod), "ABAP"))
    html = main.templates.get_template("partials/catalog_connections.html").render(groups=groups, type_id="ABAP", type_label="ABAP", pack="datasphere_core")
    assert "<strong>Acme</strong>" in html and "cliente=Acme&type_id=ABAP" in html


# ═══════════════ Disponibilidad SAP (snapshot del pack) ═══════════════
def test_sap_view_comes_only_from_the_pack_snapshot():
    sap_connection_catalog.catalog.cache_clear()
    view = sap_connection_catalog.catalog("datasphere_core")
    doc = json.loads(PACK_JSON.read_text(encoding="utf-8"))
    assert view["total"] == 37 and [t["type_id"] for t in view["types"]] == [t["type_id"] for t in doc["connection_types"]]
    assert [t["icon_location"] for t in view["types"]] == [t["icon_location"] for t in doc["connection_types"]]
    counts = {k: {o["value"]: o["count"] for o in v} for k, v in view["filters"].items()}
    assert counts["sources"] == {"SAP": 16, "NonSAP": 19, "Partner": 2}
    assert counts["categories"] == {"CloudApp": 31, "OnPremise": 12}
    assert counts["capabilities"]["DataflowSource"] == 25 and counts["capabilities"]["ApiTasks"] == 1
    assert counts["requirements"]["availability:conditional"] == 1
    assert sap_connection_catalog.catalog("bdc_next")["types"] == []         # pack sin snapshot: vista vacía


def test_requirements_are_presented_in_three_states():
    sap_connection_catalog.catalog.cache_clear()
    t = {x["type_id"]: x for x in sap_connection_catalog.catalog("datasphere_core")["types"]}
    state = lambda tid, key: next(r for r in t[tid]["requirements"] if r["key"] == key)
    assert (state("ABAP", "dp_agent_required")["state"], state("ABAP", "dp_agent_required")["state_label"]) == ("true", "Requerido")
    assert (state("BIGQUERY", "dp_agent_required")["state"], state("BIGQUERY", "dp_agent_required")["state_label"]) == ("false", "No requerido")
    assert (state("GENERICHTTP", "dp_agent_required")["state"], state("GENERICHTTP", "dp_agent_required")["state_label"]) == ("null", "No declarado")
    assert state("ABAP", "advanced_mode")["state_label"] == "Sí" and state("ABAP", "generic_framework")["state_label"] == "No declarado"
    assert [x for x in t if t[x]["conditional"]] == ["MYSQL"]
    assert t["MYSQL"]["display_name"] is None and t["MYSQL"]["title"] == "MYSQL"          # sin nombre inventado
    assert t["ABAP"]["title"] == "SAP ABAP"


def test_sap_cards_render_with_notice_conditional_badge_and_original_icon_location():
    sap_connection_catalog.catalog.cache_clear()
    sec = _sap_section(render(*M01))
    doc = json.loads(PACK_JSON.read_text(encoding="utf-8"))
    assert re.findall(r'data-sap-type="([^"]+)"', sec) == [t["type_id"] for t in doc["connection_types"]]
    assert re.findall(r'data-icon-location="([^"]+)"', sec) == [t["icon_location"] for t in doc["connection_types"]]
    assert sec.count("data-sap-conditional") == 1 and "No garantiza disponibilidad en todos los tenants" in sec
    assert 'data-sap-requirement="dp_agent_required:null"' in sec and "No declarado" in sec
    assert "<img" not in sec                                                 # todavía sin biblioteca de imágenes
    assert "37 de 37 tipos de conexión" in sec and 'x-data="dwSapTypes(37)"' in sec


def test_sap_section_never_exposes_tenant_or_operational_data():
    sap_connection_catalog.catalog.cache_clear()
    sec = _sap_section(render(*M01))
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", sec)                  # IPs
    assert not re.search(r"\b[a-z][a-z0-9+.-]*://", sec, re.I)                 # URLs (iFrame de partners, etc.)
    for marker in ("dpAgents", "agentName", "ipAddresses", "featureFlag", "iFrame", "ocnInstances", "ocnConfigured", "connected"):
        assert marker not in sec, marker


def test_a_tampered_snapshot_fails_closed(monkeypatch, tmp_path):
    import config
    doc = json.loads(PACK_JSON.read_text(encoding="utf-8"))
    doc["connection_types"][0]["agents"] = ["X"]
    (tmp_path / "datasphere_core").mkdir()
    (tmp_path / "datasphere_core" / "sap_connection_types.json").write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(config, "CONTENT_PACKS_DIR", tmp_path)
    sap_connection_catalog.catalog.cache_clear()
    try:
        assert sap_connection_catalog.catalog("datasphere_core")["types"] == []
        assert "Sin snapshot de referencia" in _sap_section(render(*M01))
    finally:
        sap_connection_catalog.catalog.cache_clear()


# ═══════════════ comportamiento en el cliente (filtros combinables, búsqueda, contador) ═══════════════
def test_sap_types_filters_and_search_harness():
    r = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
