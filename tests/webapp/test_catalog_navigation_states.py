"""
test_catalog_navigation_states.py — carga, panel central y estados vacíos/error del Catálogo (UX).

Contratos que fija:
  * Líneas de negocio y Clientes: carga NATIVA de htmx (hx-indicator + hx-sync replace) con un loader que reusa el existente; las tarjetas anteriores se ocultan durante
    la carga; el selector queda visible; Modelos y Data Products NO cambian de comportamiento;
  * el panel central muestra el loader apenas se pide un tile y los errores son ERROR con reintento (nunca "sin datos");
  * los estados sin resultados usan UN componente del Hub (`catalog_state.html`) y se distinguen: sin modelos en una línea / sin modelos de un cliente / sin resultados /
    pedido de selección / error de recuperación;
  * la lógica de cambio rápido, respuestas fuera de orden, error HTTP + recuperación y cancelación se prueba contra el `DwCatalogNav` REAL (harness node);
  * un solo juego de listeners y sin timers.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import webapp.main as main
from tools.catalog import catalog_service as cs
from webapp.services import assessment_service as svc
from webapp.services import auth_service
from webapp.services import domain_classification_service as dcs

REPO = Path(__file__).resolve().parents[2]
APP_JS = (REPO / "webapp/static/app.js").read_text(encoding="utf-8")
HARNESS = Path(__file__).resolve().parent / "catalog_nav_states.node.js"


def _flow(root, client, d, dominio):
    p = root / client / d
    p.mkdir(parents=True)
    (p / f"flujo_{d}.md").write_text(f"---\nauto:\n  tipo: flujo\n  dominio: {dominio}\n  artefactos:\n  - 4AM_{d}\nmanual:\n  cliente: {client}\n---\nC\n", encoding="utf-8")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    _flow(tmp_path, "Alfa", "V1", "Ventas")
    entries = cs.load_catalog(str(tmp_path))
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(svc, "statuses_for_entries", lambda es: {e.entry_id: svc.AssessmentStatus("NOT_YET_ASSESSED", e.entry_id, e.cliente, None) for e in es})
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "t@d.com", "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def _tab(**over):
    ctx = dict(document=SimpleNamespace(summary=SimpleNamespace(number="04", stem="04_Modelado")), pack="datasphere_core", request=None,
               catalog_models=["Despachos"], catalog_domains=["Despachos", "Ventas"], catalog_lobs=dcs.lob_options(), catalog_clients=["Alfa"], catalog_data_products=[])
    ctx.update(over)
    return main.templates.get_template("partials/catalog_tab.html").render(**ctx)


def _select(html, sid):
    return re.search(rf'<select id="{sid}".*?>', html, re.S).group(0)


# ── carga de las listas: declarativa y nativa de htmx ───────────────────────
@pytest.mark.parametrize("sid,region", [("catalog-lob", "catalog-lob-region"), ("catalog-client", "catalog-client-region")])
def test_lob_and_client_selectors_use_native_htmx_loading_and_sync(sid, region):
    tag = _select(_tab(), sid)
    assert 'hx-sync="this:replace"' in tag and f'hx-indicator="#{region}"' in tag and f'data-dw-nav-region="#{region}"' in tag and 'hx-trigger="change"' in tag
    html = _tab()
    block = html.split(f'id="{region}"')[1]
    loader = block.index("catalog-loading")
    assert loader < block.index('class="catalog-flows"')                           # el loader precede al contenedor que se oculta durante la carga
    assert "section-analysis-spinner" in block[:block.index('class="catalog-flows"')] and 'role="status"' in block[:block.index('class="catalog-flows"')]


def test_the_loader_reuses_the_existing_component_and_creates_no_new_one():
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8")
    assert "analysis-spin" in css and ".section-analysis-spinner" in css
    new_block = css.split("Catálogo: carga de navegación")[1]
    assert "@keyframes" not in new_block and "animation" not in new_block              # sin animaciones propias: usa la del loader existente
    assert ".catalog-region.htmx-request > .catalog-flows { display: none; }" in new_block and ".catalog-region.htmx-request > .catalog-loading { display: flex; }" in new_block
    assert "#content:has(.catalog-detail-loading) { overflow-y: hidden; scrollbar-gutter: auto; }" in new_block     # el panel central cargando no muestra scrollbar


def test_models_and_data_products_keep_their_behaviour():
    html = _tab()
    models = _select(html, "catalog-dominio")
    assert "hx-sync" not in models and "hx-indicator" not in models and "data-dw-nav-region" not in models              # Modelos: sin cambios
    assert 'hx-get="/catalog/domain?' in models and 'hx-target="#catalog-flows"' in models
    dp = _tab(catalog_data_products=[SimpleNamespace(veredicto="buena", business_name="DP", title="DP", apto="x", product_id="p", artifact_names=[], data_categories=[], industries=[], delivery_mode="")])
    assert "/catalog/data-product?product_id=p" in dp and dp.count("catalog-region") == dp.count('class="catalog-region"') == 2    # solo las dos regiones nuevas


def test_module_page_ships_the_state_templates_next_to_the_detail_slot(client):
    html = client.get("/modules/04_Modelado_y_Desarrollo?pack=datasphere_core").text
    for tid in ("catalog-detail-loading-tpl", "catalog-detail-error-tpl", "catalog-list-error-tpl"):
        assert f'<template id="{tid}">' in html
    assert html.index('id="catalog-detail-slot"') < html.index('id="catalog-detail-loading-tpl"')
    err = html.split('<template id="catalog-list-error-tpl">')[1].split("</template>")[0]
    assert 'role="alert"' in err and "data-dw-nav-retry" in err and "No significa que no existan" in err and "Sin modelos disponibles" not in err
    detail_err = html.split('<template id="catalog-detail-error-tpl">')[1].split("</template>")[0]
    assert "data-dw-nav-retry" in detail_err and "dwCatalogBack" in detail_err
    loading = html.split('<template id="catalog-detail-loading-tpl">')[1].split("</template>")[0]
    assert "data-dw-nav-loading" in loading and 'role="status"' in loading and "section-analysis-spinner" in loading and "dwCatalogBack" in loading


# ── estados vacíos: un componente, cinco significados ────────────────────────
def _render_state(**kw):
    return main.templates.get_template("partials/catalog_state.html").render(**kw)


def test_state_component_differentiates_empty_prompt_and_error():
    empty = _render_state(state_kind="empty", state_title="Sin modelos disponibles", state_text="Todavía no hay modelos asociados a esta línea de negocio.")
    assert 'class="tool-empty catalog-state catalog-state--empty"' in empty and 'role="status"' in empty and "<svg" in empty and "data-dw-nav-retry" not in empty
    prompt = _render_state(state_kind="prompt", state_title="Elegí un cliente.")
    assert "catalog-state--prompt" in prompt and 'role="status"' in prompt
    error = _render_state(state_kind="error", state_title="No se pudo cargar", state_text="x", state_retry=True)
    assert "catalog-state--error" in error and 'role="alert"' in error and "data-dw-nav-retry" in error and "Sin modelos" not in error


def test_lob_empty_states_are_the_component_with_the_suggested_copy(client):
    r = client.get("/catalog/lob?lob=MANUFACTURING")
    assert "catalog-state--empty" in r.text and "empty-panel" not in r.text
    assert "Sin modelos disponibles" in r.text and "Todavía no hay modelos asociados a esta línea de negocio." in r.text
    assert "No hay modelos pendientes de clasificar." not in r.text
    assert "catalog-state--prompt" in client.get("/catalog/lob").text and "Elegí una línea de negocio." in client.get("/catalog/lob").text


def test_client_empty_states_are_distinct_from_lob_and_from_prompt(client):
    r = client.get("/catalog/client?cliente=Nadie")
    assert "catalog-state--empty" in r.text and "Sin modelos disponibles" in r.text and "Todavía no hay modelos asociados al cliente «Nadie»." in r.text
    assert "línea de negocio" not in r.text
    p = client.get("/catalog/client")
    assert "catalog-state--prompt" in p.text and "Elegí un cliente." in p.text and "Sin modelos" not in p.text


def test_other_selector_without_results_uses_the_component_and_keeps_its_legacy_copy(client):
    r = client.get("/catalog/domain?dominio=NoExiste")
    import html as _html
    assert "catalog-state--empty" in r.text and "Sin resultados" in r.text and 'Sin flujos implementados para "NoExiste".' in _html.unescape(r.text) and "empty-panel" not in r.text
    assert "catalog-state--prompt" in client.get("/catalog/domain").text and "Elegí un dominio." in client.get("/catalog/domain").text


def test_a_recovery_failure_is_an_error_state_never_presented_as_no_data(client, monkeypatch):
    monkeypatch.setattr(svc, "statuses_for_entries", lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    r = client.get("/catalog/lob?lob=SALES")
    assert r.status_code == 200 and "catalog-state--error" in r.text and 'role="alert"' in r.text and "data-dw-nav-retry" in r.text
    assert "Clasificación no disponible" in r.text and "Sin modelos disponibles" not in r.text and "catalog-state--empty" not in r.text and "boom" not in r.text


def test_non_empty_results_are_untouched_by_the_state_component(client):
    r = client.get("/catalog/domain?dominio=Ventas")
    assert "catalog-card" in r.text and "catalog-state" not in r.text and "empty-panel" not in r.text
    r = client.get("/catalog/client?cliente=Alfa")
    assert "catalog-card" in r.text and "catalog-state" not in r.text


# ── guardas estáticas de app.js ─────────────────────────────────────────────
def _nav_block():
    a = APP_JS.index("const DwCatalogNav = (() => {")
    return APP_JS[a:APP_JS.index("window.DwCatalogNav = DwCatalogNav;")]


def test_nav_logic_has_one_grace_timer_no_simulated_progress_one_listener_per_event_and_is_defined_once():
    block = _nav_block()
    assert not re.search(r"\b(setInterval|requestAnimationFrame)\b", block)                            # sin progreso simulado ni animaciones por JS
    assert block.count("setTimeout(") == 1 and "clearTimeout(" in block                                # UN solo timer: el umbral de continuidad del loader (cancelado en cada salida)
    assert re.search(r"const GRACE_MS = \d{2,3};", block) and "}, GRACE_MS);" in block
    assert "MIN_" not in block and "minDuration" not in block                                          # sin duración mínima: el loader dura lo que dure la espera real
    for ev in ("htmx:beforeRequest", "htmx:afterRequest", "htmx:afterSwap", "click"):
        assert block.count(f'addEventListener("{ev}"') == 1, ev
    assert 'forEach((n) => document.body.addEventListener(n, onFailure))' in block
    assert APP_JS.count("const DwCatalogNav") == 1 and APP_JS.count("window.dwCatalogBack = function") == 1
    back = APP_JS.split("window.dwCatalogBack = function")[1].split("};")[0]
    assert "DwCatalogNav.cancelDetail()" in back                                                       # volver durante la carga cancela la solicitud en vuelo


def test_catalog_flows_template_keeps_its_cards_markup_and_only_the_empty_branch_changed():
    html = (REPO / "webapp/templates/partials/catalog_flows.html").read_text(encoding="utf-8")
    assert "empty-panel" not in html and 'include "partials/catalog_state.html"' in html
    assert 'hx-target="#catalog-detail-slot" hx-swap="innerHTML" hx-trigger="click, keyup[key==\'Enter\']"' in html


# ── lógica de carga/error/cancelación contra el DwCatalogNav REAL ───────────
@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_dw_catalog_nav_contract_holds_against_the_real_code():
    r = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("OK"), r.stdout + r.stderr
