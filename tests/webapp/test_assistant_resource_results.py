"""Contrato TRANSVERSAL de resultados navegables (paso 1.5): identidad compuesta `(kind, ref)`, título/subtítulo/acción independientes de la familia, modos `embed|link|external`, atributos del Catálogo
compuestos (no obligatorios para el resto) y primer caso transversal real: los recursos curados del Hub (`intent_assets`) como filas navegables en lugar de N botones «Abrir recurso» idénticos.

Solo reutiliza los metadatos y destinos que el recurso ya declara: sin búsqueda general de SharePoint, sin tokens Graph, sin conectores ni persistencia nueva. Un enlace curado NO garantiza permiso
de lectura en el destino. Fixtures SINTÉTICAS (más una verificación acotada contra el pack real)."""
from __future__ import annotations

import html as _html
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import webapp.main as main
from webapp.services import assistant_capabilities as caps
from webapp.services import assistant_routing, auth_service
from webapp.services.content_service import load_module
from webapp.templating import templates

PACK = "datasphere_core"
HOST = "04_Modelado_y_Desarrollo"
SHP = "https://danwor.sharepoint.com/:x:/s/Practica/IQC_ejemplo"


def _render(items, footer="") -> str:
    rs = caps.ResultSet("list", total=len(items), items=tuple(items), footer=footer)
    return templates.env.get_template("partials/assistant_result_rows.html").render(route=SimpleNamespace(result=rs))


def _rows(page: str) -> list[dict]:
    return [dict(re.findall(r'([\w-]+)="([^"]*)"', tag)) for tag in re.findall(r'<a class="assistant-result-row"([^>]*)>', page)]


# ── 1. contrato: identidad compuesta, destinos válidos por modo, composición ────────────────────────────────────────────────────────────────────────
def test_the_identity_is_the_composite_kind_and_ref_so_families_never_collide():
    a = caps.ResultItem(kind=caps.CATALOG_ENTRY, ref="x1", title="A", url="/catalog/flow?flow=x1")
    b = caps.ResultItem(kind=caps.CURATED_RESOURCE, ref="x1", title="B", url=SHP, mode=caps.MODE_EXTERNAL)
    assert a.key == (caps.CATALOG_ENTRY, "x1") and b.key == (caps.CURATED_RESOURCE, "x1") and a.key != b.key
    page = _render([a, b])
    assert [(r["data-kind"], r["data-ref"]) for r in _rows(page)] == [a.key, b.key]                    # el render conserva ambas identidades por separado


def test_catalog_attributes_are_composed_and_optional_for_other_families():
    r = caps.ResultItem(kind=caps.CURATED_RESOURCE, ref="m:1", title="Doc", url="/pack-resources/p/templates/t.abap", mode=caps.MODE_EXTERNAL)
    assert r.catalog is None and r.subtitle == ""                                                      # una familia no Catálogo no necesita cliente ni línea de negocio
    c = caps.ResultItem(kind=caps.CATALOG_ENTRY, ref="e1", title="Modelo", subtitle="Pampa · Ventas", url="/catalog/flow?flow=e1",
                        catalog=caps.CatalogAttrs(client="Pampa", lob_label="Ventas", lob_source="AUTOMATIC"))
    assert c.catalog.client == "Pampa" and not hasattr(c, "client") and not hasattr(caps.ResultSet("list", total=0), "aggregates")


@pytest.mark.parametrize("mode,url", [
    ("embed", "https://evil.example/x"), ("embed", "//evil.example/x"), ("embed", "javascript:alert(1)"), ("embed", "/ok path"), ("embed", "/ok\\evil"),
    ("link", "https://evil.example/x"), ("link", "//evil.example/x"), ("link", "data:text/html,x"), ("link", ""),
    ("external", "javascript:alert(1)"), ("external", "ftp://x/y"), ("external", "//evil.example/x"), ("external", "/catalog/flow?flow=x"), ("external", "https:///sin-host"),
    ("external", "https://a b/c"), ("teleport", "/ok")])
def test_an_item_cannot_be_built_with_a_target_that_does_not_fit_its_mode(mode, url):
    with pytest.raises(ValueError):
        caps.ResultItem(kind="k", ref="r", title="t", url=url, mode=mode)


@pytest.mark.parametrize("mode,url", [("embed", "/catalog/flow?flow=x&pack=p"), ("link", "/modules/04_Modelado_y_Desarrollo?pack=p#seccion"),
                                      ("external", SHP), ("external", "HTTP://Example.com/a"), ("external", "/pack-resources/p/templates/t.abap")])
def test_valid_targets_per_mode_are_accepted(mode, url):
    assert caps.ResultItem(kind="k", ref="r", title="t", url=url, mode=mode).url == url


def test_internal_paths_are_decided_by_the_single_redirect_authority(monkeypatch):
    """La regla de «interno» vive en `webapp.safe_redirect` (un test del repo exige UNA sola copia): el contrato la reutiliza, no la reimplementa."""
    assert caps.is_internal_path("/catalog/flow?flow=x") and not caps.is_internal_path("/\\evil.example") and not caps.is_internal_path("/\t/evil.example")
    monkeypatch.setattr(caps, "safe_internal_next", lambda value: "")
    assert caps.is_internal_path("/catalog/flow?flow=x") is False                                       # si la autoridad rechaza, el contrato rechaza


def test_an_item_needs_a_kind_and_a_ref():
    for kind, ref in (("", "r"), ("k", "")):
        with pytest.raises(ValueError):
            caps.ResultItem(kind=kind, ref=ref, title="t", url="/ok")


# ── 2. render por modo (cada modo validado individualmente) ─────────────────────────────────────────────────────────────────────────────────────────
def test_embed_opens_in_the_central_panel_with_htmx_and_no_new_tab():
    (row,) = _rows(_render([caps.ResultItem(kind="catalog_entry", ref="e1", title="Modelo", subtitle="Pampa · Ventas", url="/catalog/flow?flow=e1")]))
    assert row["hx-get"] == row["href"] == "/catalog/flow?flow=e1" and row["hx-target"] == "#catalog-detail-slot" and row["hx-swap"] == "innerHTML"
    assert "target" not in row and "rel" not in row


def test_link_is_a_plain_same_tab_navigation_to_an_internal_route():
    page = _render([caps.ResultItem(kind="module_section", ref="04#capas", title="Capas", url="/modules/04_Modelado_y_Desarrollo?pack=p#capas", mode=caps.MODE_LINK)])
    (row,) = _rows(page)
    assert row["href"].startswith("/modules/") and not any(k in row for k in ("hx-get", "hx-target", "target", "rel"))
    assert "se abre en una pestaña nueva" not in page and "↗" not in page


def test_external_opens_in_a_new_tab_with_noopener_noreferrer_and_says_so_to_assistive_tech():
    page = _render([caps.ResultItem(kind="curated_resource", ref="m:1", title="Template", url=SHP, mode=caps.MODE_EXTERNAL)])
    (row,) = _rows(page)
    assert row["href"] == SHP and row["target"] == "_blank" and row["rel"] == "noopener noreferrer" and "hx-get" not in row
    assert "se abre en una pestaña nueva" in page and 'aria-hidden="true">↗' in page


def test_rows_escape_titles_subtitles_and_footer_and_only_render_validated_urls():
    page = _render([caps.ResultItem(kind="curated_resource", ref='m:"1', title='<img src=x onerror="alert(1)">', subtitle="<b>x</b>", url=SHP, mode=caps.MODE_EXTERNAL)], footer="<script>alert(1)</script>")
    assert "<img src=x" not in page and "&lt;img src=x" in page and "<b>x</b>" not in page and "<script>" not in page
    assert 'data-ref="m:&#34;1"' in page or 'data-ref="m:&quot;1"' in page                           # el atributo no se rompe con comillas


# ── 3. primer caso transversal: recursos curados (intent_assets) como filas ─────────────────────────────────────────────────────────────────────────
def _assets():
    return [
        {"description": "Template Danwor de Seguridad, Roles y DAC", "asset": SHP, "_module": "02_Seguridad_y_Gobernanza", "_module_label": "02 · Seguridad y Gobernanza", "keywords": ["x"]},
        {"description": "Template CDS para extracción", "asset": "templates/cds_extractor_template.abap", "_module": "04_Modelado_y_Desarrollo", "_module_label": "04 · Modelado y Desarrollo"},
        {"description": "SAP Help CDS Views", "url": "https://help.sap.com/docs/cds", "_module": "03_Integracion_y_Estrategia_de_Datos", "_module_label": "03 · Integración y Estrategia de Datos"},
    ]


def test_curated_resources_become_rows_in_the_same_order_with_readable_title_origin_subtitle_and_stable_identity():
    rs = assistant_routing._resource_result(_assets(), PACK)
    assert rs.capability == "list" and rs.total == 3 and rs.catalog is None
    assert [it.title for it in rs.items] == ["Template Danwor de Seguridad, Roles y DAC", "Template CDS para extracción", "SAP Help CDS Views"]
    assert [it.subtitle for it in rs.items] == ["02 · Seguridad y Gobernanza", "04 · Modelado y Desarrollo", "03 · Integración y Estrategia de Datos"]
    assert all(it.kind == caps.CURATED_RESOURCE and it.mode == caps.MODE_EXTERNAL for it in rs.items)     # igual que antes: «Abrir recurso» siempre abría pestaña nueva
    assert [it.url for it in rs.items] == [SHP, f"/pack-resources/{PACK}/templates/cds_extractor_template.abap", "https://help.sap.com/docs/cds"]
    again = assistant_routing._resource_result(_assets(), PACK)
    assert [it.key for it in again.items] == [it.key for it in rs.items] and len({it.key for it in rs.items}) == 3        # identidad estable y sin colisiones
    assert not any(SHP in it.ref for it in rs.items)                                                       # la identidad no expone el enlace


def test_the_same_destination_in_two_modules_is_two_resources_and_a_repeated_one_is_listed_once():
    a, b = _assets()[0], dict(_assets()[0], _module="03_Integracion_y_Estrategia_de_Datos")
    assert len(assistant_routing._resource_result([a, b], PACK).items) == 2
    assert len(assistant_routing._resource_result([a, dict(a)], PACK).items) == 1


def test_a_resource_without_description_still_gets_a_neutral_title_and_one_without_destination_is_skipped():
    rs = assistant_routing._resource_result([{"asset": SHP, "_module": "m"}, {"description": "sin destino", "_module": "m"}], PACK)
    assert [it.title for it in rs.items] == ["Recurso curado"] and rs.total == 1
    assert assistant_routing._resource_result([{"description": "x"}], PACK) is None


@pytest.mark.parametrize("raw", ["javascript:alert(1)", "//evil.example/x", "data:text/html,<script>", "..\\..\\secret", "http:/solo-un-slash", "file:///etc/passwd", "ftp://x/y"])
def test_hostile_destinations_never_become_unsafe_links(raw):
    rs = assistant_routing._resource_result([{"description": "x", "asset": raw, "_module": "m"}], PACK)
    for it in (rs.items if rs else ()):
        assert it.url.startswith("/pack-resources/") or it.url.lower().startswith(("http://", "https://"))
        assert not it.url.startswith("//") and "javascript:" not in it.url.lower() and "\\" not in it.url


def test_the_permission_note_appears_only_with_external_links_and_never_claims_access():
    ext = assistant_routing._resource_result(_assets(), PACK)
    assert "dependen de los permisos de tu cuenta" in ext.footer and "pedí acceso" in ext.footer
    assert not re.search(r"garantiz|siempre|tenés acceso|(?<!no )podés abrir", ext.footer)           # no afirma acceso; solo avisa que depende de los permisos
    only_pack = assistant_routing._resource_result([_assets()[1]], PACK)
    assert only_pack.footer == ""


def _route(question, stem=HOST):
    return assistant_routing.AssistantRoutingService().route(load_module(stem, PACK), question, PACK)


def _client(with_session=True):
    c = TestClient(main.app)
    if with_session:
        c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "t@d.com", "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def _chat_raw(question, stem=HOST) -> str:
    from webapp.routers.assistant import assistant_store
    conv = assistant_store.start(stem, PACK).id
    return _client().post(f"/modules/{stem}/assistant/chat", data={"question": question, "conversation_id": conv, "pack": PACK}).text


def test_the_route_returns_rows_instead_of_repeated_open_resource_buttons(monkeypatch):
    monkeypatch.setattr(assistant_routing, "_find_matching_assets", lambda *a, **k: _assets())
    r = _route("¿Qué documentación tenemos de zorrinwax?")
    assert r.message == "Encontré varios recursos curados que pueden ayudarte. Elegí cuál querés abrir." and r.actions == ()          # mismo encabezado; sin botones
    assert [it.title for it in r.result.items] == [a["description"] for a in _assets()]


def test_real_pack_end_to_end_rows_open_only_validated_destinations_and_buttons_are_gone():
    q = "¿Qué documentación tenemos de CDS?"
    r = _route(q)
    assert r is not None and r.result is not None and len(r.result.items) >= 2 and r.actions == ()
    page = _chat_raw(q)
    rows = _rows(page)
    assert len(rows) == len(r.result.items) and page.count("Abrir recurso") == 0 and page.count('class="assistant-route-action"') == 0
    for row in rows:
        assert row["data-kind"] == "curated_resource" and caps.valid_target("external", row["href"])
        assert row["target"] == "_blank" and row["rel"] == "noopener noreferrer"
    assert len({(row["data-kind"], row["data-ref"]) for row in rows}) == len(rows)
    assert "assistant-results-note" in page and "<table" not in page


def test_retrieval_and_relevance_order_are_unchanged_and_urls_never_come_from_the_question():
    q = "¿Qué documentación tenemos de CDS? https://evil.example/x"
    r = _route(q)
    direct = assistant_routing._find_matching_assets(q, HOST, load_module(HOST, PACK).source, pack_name=PACK)
    assert [it.title for it in r.result.items] == [str(a.get("description") or "").strip() for a in direct if assistant_routing._asset_action(a, PACK)]
    assert not any("evil.example" in it.url for it in r.result.items)


def test_single_resource_section_and_methodological_routes_are_unchanged():
    single = _route("template CDS")
    assert single.result is None and single.message.startswith("Encontré un recurso curado") and [a.label for a in single.actions] == ["Abrir recurso"] and single.actions[0].external
    section = _route("¿Cómo se definen los dominios de negocio?")
    assert section.result is None and [a.label for a in section.actions][0] == "Ir a la sección"


def test_catalog_rows_and_resource_rows_coexist_in_the_same_contract_without_mixing_identities(monkeypatch):
    monkeypatch.setattr(assistant_routing, "_find_matching_assets", lambda *a, **k: _assets())
    resources = {it.key for it in _route("¿Qué documentación tenemos de zorrinwax?").result.items}
    assert all(kind == caps.CURATED_RESOURCE for kind, _ref in resources)
    assert not any(kind == caps.CATALOG_ENTRY for kind, _ref in resources)
