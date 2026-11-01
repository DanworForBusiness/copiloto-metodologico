"""Catálogo de conectividad del módulo 01: dropdown «Catálogo de conectividad» (Conexiones · Clientes · Disponibilidad SAP) con la mecánica
del 04; Clientes sobre conexiones PUBLICADAS (`connections_for_client`); Disponibilidad SAP SOLO desde el snapshot saneado del pack y en el PANEL
CENTRAL (`GET /catalog/sap-types` → #catalog-detail-slot): en el panel derecho queda únicamente como opción de navegación."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastapi.testclient import TestClient

import webapp.main as main
from webapp.services import auth_service, catalog_reads, sap_connection_catalog

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


def _owner():
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "owner@danwor.com", "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def _central():
    sap_connection_catalog.catalog.cache_clear()
    r = _owner().get("/catalog/sap-types?pack=datasphere_core", headers={"HX-Request": "true"})
    assert r.status_code == 200
    return r.text


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


def test_right_panel_keeps_sap_availability_only_as_navigation():
    sap_connection_catalog.catalog.cache_clear()
    html = render(*M01)
    sec = _sap_section(html)
    assert "data-sap-type" not in html and "data-sap-filter" not in html and "dwSapTypes" not in html     # sin tarjetas, filtros ni resultados
    assert 'hx-get="/catalog/sap-types?pack=datasphere_core" hx-target="#catalog-detail-slot" hx-swap="innerHTML" hx-trigger="click, dw-sap-open"' in sec
    assert "37 tipos de conexión" in sec
    assert "dwConnectivityKindChanged(catalogKind, $refs.sapOpen, $refs.registryOpen)" in html                               # elegir la opción abre / salir cierra


def test_central_view_renders_filters_search_counter_and_cards():
    html = _central()
    assert html.lstrip().startswith('<div class="sap-types-page">') and '<div class="catalog-actionbar">' in html   # barra de acciones de las vistas centrales
    assert '<a class="catalog-back" href="#" onclick="return dwCatalogBack();">' in html          # mismo «← Volver» del panel central
    assert '<header class="content-heading has-subtitle">' in html and '<blockquote class="section-subtitle">' in html  # encabezado de sección del Hub
    assert '<span class="tone-black">Tipos de conexión de </span><span class="tone-gold">SAP Datasphere</span>' in html   # {dw-black} / {dw-gold}
    assert 'class="hc-profile-filters filter-toolbar-card sap-types-toolbar"' in html             # barra de filtros compartida del Hub
    assert "catalog-feature-chip--on" not in html and html.count('status-badge status-badge--otro') > 37   # atributos neutros, no «éxito»
    assert "Disponibilidad SAP" in html and 'x-data="dwSapTypes(37)"' in html and "data-sap-view" in html
    groups = re.findall(r'<label id="sap-filter-(\w+)">([^<]+)</label>', html)
    assert groups == [("capabilities", "Capacidades"), ("categories", "Categorías"), ("sources", "Orígenes"), ("requirements", "Requisitos")]
    assert html.count('type="checkbox"') == sum(len(v) for v in sap_connection_catalog.catalog("datasphere_core")["filters"].values())
    assert 'id="catalog-sap-search"' in html and "data-sap-count" in html and ">37 de 37</span>" in html and "catalog-flows-list--grid" in html
    doc = json.loads(PACK_JSON.read_text(encoding="utf-8"))
    assert re.findall(r'data-sap-type="([^"]+)"', html) == [t["type_id"] for t in doc["connection_types"]]
    assert re.findall(r'data-icon-location="([^"]+)"', html) == [t["icon_location"] for t in doc["connection_types"]]
    assert "data-sap-conditional" not in html and "No garantiza disponibilidad en todos los tenants" in html   # sin badge en el tile: la condición vive en «Detalle técnico»
    assert 'data-sap-requirement="dp_agent_required:null"' in html and "No declarado" in html
    srcs = re.findall(r'<img class="sap-type-icon" src="([^"]+)"', html)
    assert len(srcs) == 37 and all(src.startswith("http://testserver/static/icons/vendors/sap-datasphere/connection-types/") for src in srcs)


def test_central_view_ends_with_the_section_feedback_bar_and_records_it(monkeypatch, tmp_path):
    html = _central()
    bar = html[html.index('id="catalog-sfb-disponibilidad-sap"'):]
    fb_at = html.index('id="catalog-sfb-disponibilidad-sap"')
    assert fb_at > html.rindex("</details>") and fb_at > html.rindex('data-sap-type=')                   # FUERA del agrupador, al final
    view = html[html.index("data-sap-view"):fb_at]
    assert view.count("<div") <= view.count("</div>")                                                   # y fuera de la vista (tokens del Hub)
    assert 'hx-post="/catalog/section-feedback"' in bar and 'name="slug" value="disponibilidad-sap"' in bar
    assert "¿Qué te pareció esta sección?" in bar
    from webapp.routers import feedback as fb
    monkeypatch.setattr(fb, "FEEDBACK_LOG_DIR", tmp_path)
    r = _owner().post("/catalog/section-feedback", data={"cliente": "", "dominio": "Disponibilidad SAP", "slug": "disponibilidad-sap",
                                                        "rating": "up", "fb_prompt": "¿Qué te pareció esta sección?", "pack": "datasphere_core"},
                      headers={"Origin": "http://testserver"})
    assert r.status_code == 200 and "Gracias" in r.text
    saved = list(tmp_path.glob("catalog_section_*.yaml"))
    assert len(saved) == 1 and "disponibilidad-sap" in saved[0].read_text(encoding="utf-8")


def test_central_view_direct_navigation_and_session():
    sap_connection_catalog.catalog.cache_clear()
    full = _owner().get("/catalog/sap-types?pack=datasphere_core")
    assert full.status_code == 200 and "<title>Disponibilidad SAP" in full.text and 'id="content"' in full.text and "data-sap-view" in full.text
    anon = TestClient(main.app).get("/catalog/sap-types?pack=datasphere_core", follow_redirects=False)
    assert anon.status_code in (302, 303, 307) and "data-sap-type" not in anon.text


def test_central_view_never_exposes_tenant_or_operational_data():
    view = _central()
    view = view[view.index("data-sap-view"):]
    assert not re.search(r"\d{1,3}(?:\.\d{1,3}){3}", view)                  # IPs
    assert not re.search(r"[a-z][a-z0-9+.-]*://", view, re.I)                 # URLs (iFrame de partners, etc.)
    for marker in ("dpAgents", "agentName", "ipAddresses", "featureFlag", "iFrame", "ocnInstances", "ocnConfigured", "connected"):
        assert marker not in view, marker


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
        r = _owner().get("/catalog/sap-types?pack=datasphere_core", headers={"HX-Request": "true"})
        assert "Sin snapshot de referencia" in r.text and "data-sap-type" not in r.text
    finally:
        sap_connection_catalog.catalog.cache_clear()


# ═══════════════ biblioteca local de íconos originales ═══════════════
ICON_DIR = REPO / "webapp" / "static" / "icons" / "vendors" / "sap-datasphere" / "connection-types"


def _png_chunks(data):
    import struct
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, out = 8, []
    while pos < len(data):
        ln, typ = struct.unpack(">I4s", data[pos:pos + 8])
        out.append(typ.decode("latin1"))
        pos += 12 + ln
        if typ == b"IEND":
            break
    assert pos == len(data), "datos después de IEND"
    return out


def test_icon_library_matches_the_unique_icon_locations_exactly():
    doc = json.loads(PACK_JSON.read_text(encoding="utf-8"))
    expected = {Path(t["icon_location"]).name for t in doc["connection_types"]}
    present = {p.name for p in ICON_DIR.iterdir()}
    assert len(expected) == 20 and present == expected                     # ni faltantes ni sobrantes; mayúsculas EXACTAS (Linux)
    for p in ICON_DIR.iterdir():
        data = p.read_bytes()
        chunks = _png_chunks(data)
        assert chunks[0] == "IHDR" and chunks[-1] == "IEND" and len(data) <= 64 * 1024, p.name
        assert not set(chunks) & {"eXIf", "iTXt", "tEXt", "zTXt", "tIME"}, p.name          # sin metadatos
        assert not re.search(rb"https?://", data), p.name


def test_icon_resolution_is_local_exact_and_safe():
    sap_connection_catalog._icon_names.cache_clear()
    path = sap_connection_catalog.icon_path
    assert path("images/storages/Google.png") == "icons/vendors/sap-datasphere/connection-types/Google.png"
    for bad in ("images/storages/google.png", "images/storages/../app.js", "images/storages/x/../../a.png", "https://tenant/x.png",
                "images/storages/Missing.png", "", None):
        assert path(bad) is None, bad
    sap_connection_catalog.catalog.cache_clear()
    assert all(t["icon_path"] for t in sap_connection_catalog.catalog("datasphere_core")["types"])


def test_missing_icons_fall_back_to_the_monogram(monkeypatch, tmp_path):
    monkeypatch.setattr(sap_connection_catalog, "ICON_DIR", tmp_path)
    sap_connection_catalog._icon_names.cache_clear()
    sap_connection_catalog.catalog.cache_clear()
    try:
        html = _central()
        assert "<img" not in html and html.count("data-sap-icon-fallback") == 37
        assert re.search(r'data-sap-icon-fallback>SA</span>', html)                          # SAPBW → «SA» (sin inventar)
    finally:
        sap_connection_catalog._icon_names.cache_clear()
        sap_connection_catalog.catalog.cache_clear()


def test_icons_are_served_locally():
    r = _owner().get("/static/icons/vendors/sap-datasphere/connection-types/SAP-Generic.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:8] == b"\x89PNG\r\n\x1a\n"


# ═══════════════ comportamiento en el cliente (filtros combinables, búsqueda, contador) ═══════════════
def test_sap_types_filters_and_search_harness():
    r = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr


def test_whats_new_deep_link_opens_sap_availability_with_the_dropdown_in_sync(monkeypatch):
    """El enlace de What's New abre el módulo 01 en Catálogo con «Disponibilidad SAP» elegida: el dropdown arranca en `sap_types` y el navegador
    abre la vista central con el MISMO mecanismo que la selección manual. Sin el parámetro (o con otro valor), arranca en «Conexiones»."""
    from webapp.services import whats_new_service as wn
    entry = next(e for e in wn.entries() if e["ref"] == "catalogo-conectividad-sap")
    assert entry["link"] == "/modules/01_Arquitectura_y_Conectividad?pack=datasphere_core&tab=catalogo&catalog_kind=sap_types"
    monkeypatch.setattr(catalog_reads, "_catalog_domains", lambda: ["Dominio"])          # la pestaña Catálogo existe (como con el catálogo real)
    owner = _owner()
    deep = owner.get(entry["link"]).text
    assert "x-data=\"dwCatalogBrowse('sap_types')\"" in deep
    for url in ("/modules/01_Arquitectura_y_Conectividad?pack=datasphere_core&tab=catalogo",
                "/modules/01_Arquitectura_y_Conectividad?pack=datasphere_core&tab=catalogo&catalog_kind=<script>"):
        assert "x-data=\"dwCatalogBrowse('connections')\"" in owner.get(url).text
    js = (REPO / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'if (defaultKind === "sap_types")' in js and "dwConnectivityKindChanged(this.catalogKind, this.$refs.sapOpen)" in js


def test_sapsf_card_shows_its_own_connectivity_auth_and_features():
    """Ficha SAPSF: URL/versión + OAuth (grant type, token endpoint, company id); sin campos ABAP ni DP Agent / Cloud Connector. Lo faltante
    figura como no disponible; las features separan el soporte del TIPO (contrato sap-connection-types/v1) de lo que declara la conexión."""
    from tools.catalog import catalog_service as cs
    conn = cs.CatalogConnectionEntry(path="p", cliente="Contreras", space="PRD", connection="SSFF", type_id="SAPSF",
                                     info={"url": "https://api19.sapsf.com/odata/v2", "version": "V2", "authentication": "OAuth2",
                                           "oauth2GrantType": "saml_bearer", "oauth2CompanyId": "contrerash"},
                                     authentication={"auth_type": "OAuth2"}, feature_values={"dataflows": "Sí", "replicationflows": "No"})
    rows = {label: value for kind, label, value in main._catalog_connection_rows(cs, conn) if kind == "row"}
    groups = [label for kind, label, _v in main._catalog_connection_rows(cs, conn) if kind == "group"]
    assert groups == ["Identificación", "Conectividad", "Autenticación", "Features"]
    assert rows["URL"] == "`https://api19.sapsf.com/odata/v2`" and rows["Versión"] == "V2" and rows["Método"] == "OAuth 2.0"
    assert rows["Grant Type"] == "`saml_bearer`" and rows["Company ID"] == "`contrerash`" and rows["Token Endpoint"] == "No disponible"
    assert not {"Application Server", "System Number", "Mandante", "Idioma", "Configurado", "Data Provisioning Agent"} & set(rows)
    assert rows["Remote tables"] == "Soportado por el tipo · no informado por esta conexión"           # flag ausente ≠ «No»
    assert rows["Data flows"] == "Sí en esta conexión · soportado por el tipo"
    assert rows["Replication Flows"] == "No en esta conexión · no soportado por el tipo"


def test_abap_card_keeps_its_template():
    from tools.catalog import catalog_service as cs
    conn = cs.CatalogConnectionEntry(path="p", cliente="X", space="DEV", connection="ERP", type_id="ABAP", info={"host": "erp.local", "client": "100"})
    labels = [label for _k, label, _v in main._catalog_connection_rows(cs, conn)]
    assert {"Application Server", "System Number", "Mandante", "Cloud Connector", "Remote Tables"} <= set(labels) and "Token Endpoint" not in labels


def test_sapsf_sanitization_keeps_oauth_endpoint_and_company_but_drops_secrets():
    from webapp.services import connectivity_catalog_update as cup
    oauth2 = {"oauth2GrantType": "saml_bearer", "oauth2TokenEndpoint": "https://api19.sapsf.com/oauth/token", "oauth2CompanyId": "contrerash",
              "oauth2UserId": "u1", "oauth2ClientSecret": "s", "accessToken": "t", "samlAssertion": "PHNhbWw+"}
    clean = cup._strip({"oauth2": oauth2})["oauth2"]
    assert clean == {"oauth2GrantType": "saml_bearer", "oauth2TokenEndpoint": "https://api19.sapsf.com/oauth/token", "oauth2CompanyId": "contrerash"}
    assert cup.unsafe_properties({"oauth2": oauth2}) == []
