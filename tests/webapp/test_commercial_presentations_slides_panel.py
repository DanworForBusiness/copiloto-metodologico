"""Presentaciones V2.1 — panel izquierdo de diapositivas con miniaturas reales.

La lista sale del DOM del editor; las miniaturas son UN iframe adicional (`?embed=1&thumbs=1`: el MISMO renderer en modo lectura, sin JS ni animaciones, 12 slides de 1440×810 con las eliminadas como bloque vacío), cargado
después del editor y solo en pantallas anchas; sin él, cada tarjeta es texto. El modo miniatura es de solo lectura y no cambia permisos ni rutas.
"""
from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

from tests.webapp.test_commercial_presentations_access import A, HUB_ONLY, INTRUDER, ORIGIN, SEED_ID, _copy, world  # noqa: F401
from webapp.services import commercial_presentations as cp

REPO = Path(__file__).resolve().parents[2]
CSS = (REPO / "webapp" / "static" / "commercial.css").read_text(encoding="utf-8")
TOOLS = (REPO / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
JS = (REPO / "webapp" / "static" / "commercial_presentations.js").read_text(encoding="utf-8")
VANTURE_CSS = (REPO / "content_packs" / "datasphere_core" / "assets" / "presentations" / "vanture-bdc" / "vanture.css").read_text(encoding="utf-8")


def _soup(world, path, email=A):
    r = world.client(email).get(path)
    assert r.status_code == 200, (path, r.status_code)
    return BeautifulSoup(r.text, "html.parser")


def test_the_editor_hosts_the_slides_panel_with_one_lazy_thumbnail_iframe_and_a_disabled_new_slide_placeholder(world):
    pid = _copy(world, A)
    page = _soup(world, f"/comercial/presentaciones/{pid}/editar")
    body = page.select_one("[data-cp-editor] > .cp-workspace__body")
    kids = [c for c in body.find_all(True, recursive=False)]
    assert [c.name for c in kids] == ["aside", "div"] and "cp-slides" in kids[0]["class"] and "cp-workspace__frame" in kids[1]["class"]      # panel a la izquierda, documento a la derecha
    panel = kids[0]
    assert panel.has_attr("data-cp-slides") and panel["aria-label"] == "Diapositivas" and panel.select_one(".cp-slides__title").get_text(strip=True) == "Presentación"
    assert panel.select_one("ol.cp-slides__list[data-cp-slides-list]") and not panel.select("ol.cp-slides__list > li"), "la lista la arma el JS desde el DOM del editor"
    thumbs = panel.select("iframe[data-cp-thumbs-frame]")
    assert len(thumbs) == 1, "UN solo iframe adicional"
    t = thumbs[0]
    assert t["data-src"] == f"/presentaciones/{pid}?embed=1&thumbs=1" and not t.has_attr("src"), "no carga con la página: lo carga el JS después del editor y solo en pantallas anchas"
    assert "content-embed" in t["class"] and t["tabindex"] == "-1" and t.find_parent(attrs={"aria-hidden": "true"}) is not None      # recibe el tema del Hub, sin foco ni lectura de pantalla
    assert len(page.select("iframe.content-embed")) == 2                                                         # el editor + las miniaturas (la marca animada no es `.content-embed`)
    new = panel.select_one(".cp-slides__add button.cp-slide-new")
    assert new.has_attr("disabled") and new["aria-disabled"] == "true" and "próxima etapa" in new["aria-label"] and "Nueva diapositiva" in new.get_text()
    assert "Disponible en una próxima etapa" in panel.select_one(".cp-slides__add .chart-tooltip").get_text()


def test_the_thumbnail_mode_is_the_same_renderer_read_only_without_js_with_removed_slides_as_empty_blocks(world):
    pid = _copy(world, A)
    r = world.client(A).post(f"/comercial/presentaciones/{pid}", json={"revision": 1, "removed_slides": ["s4"]}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    thumbs = _soup(world, f"/presentaciones/{pid}?embed=1&thumbs=1")
    assert thumbs.html.has_attr("data-cp-thumbs") and not thumbs.html.has_attr("data-cp-edit")
    assert [s["id"] for s in thumbs.select("section[id^=s]")] == [f"s{i}" for i in range(1, 13)], "las 12 slides: todas las filas del panel tienen el mismo paso"
    assert [s["id"] for s in thumbs.select("section[data-cp-removed]")] == ["s4"]
    assert not thumbs.find("script", src=re.compile(r"vanture\.js")) and thumbs.find("script", src=re.compile(r"hub_theme\.js"))      # sin JS propio; el tema del Hub sí
    assert not thumbs.select("[contenteditable], .cp-slide-tools, .cp-region-tools, template, dialog")
    normal = _soup(world, f"/presentaciones/{pid}?embed=1")                                                       # el modo lectura de V1 no cambia: la slide eliminada no se renderiza
    assert "s4" not in [s["id"] for s in normal.select("section[id^=s]")] and not normal.html.has_attr("data-cp-thumbs") and normal.find("script", src=re.compile(r"vanture\.js"))
    seed = _soup(world, f"/presentaciones/{SEED_ID}?embed=1&thumbs=1")
    assert len(seed.select("section[id^=s]")) == 12 and not seed.select("section[data-cp-removed]")


def test_thumbs_never_enable_editing_and_are_ignored_outside_an_embed(world):
    pid = _copy(world, A)
    both = world.client(A).get(f"/presentaciones/{pid}?embed=1&thumbs=1&edit=1").text
    assert "data-cp-thumbs" in both and "data-cp-edit" not in both and "contenteditable" not in both       # la miniatura gana: nunca editable
    plain = world.client(A).get(f"/presentaciones/{pid}?thumbs=1").text
    assert "data-cp-thumbs" not in plain                                                                       # sin embed=1 es el documento normal
    ctx = cp.render_context(cp.get(pid), embed=True, edit=True, thumbs=True)
    assert ctx["thumbs"] is True and ctx["edit_mode"] is False


def test_the_thumbnail_mode_keeps_the_read_permissions_of_the_document(world):
    pid = _copy(world, A)
    for email in (A, HUB_ONLY):
        assert world.client(email).get(f"/presentaciones/{pid}?embed=1").status_code == world.client(email).get(f"/presentaciones/{pid}?embed=1&thumbs=1").status_code == 200
    assert world.client(INTRUDER).get(f"/presentaciones/{pid}?embed=1").status_code == world.client(INTRUDER).get(f"/presentaciones/{pid}?embed=1&thumbs=1").status_code == 403


def test_the_thumbnail_stylesheet_and_the_panel_agree_on_the_geometry():
    css = VANTURE_CSS
    assert 'html[data-cp-thumbs] section[id^="s"]{box-sizing:border-box;height:810px!important;min-height:0!important;max-height:810px;overflow:hidden;margin-bottom:120px;padding-top:160px!important;' in css      # 810 + 120 = 930 px de paso
    assert "html[data-cp-thumbs] *,html[data-cp-thumbs] *::before,html[data-cp-thumbs] *::after{animation:none!important;transition:none!important}" in css
    assert 'section[data-cp-removed]::before{content:"Slide eliminada"' in css
    assert "calc(var(--cp-thumb-w) * 930 / 1440)" in CSS and "calc(var(--cp-n) * 930px)" in CSS and "width: 1440px" in CSS        # el panel usa el MISMO paso y ancho de referencia
    assert re.search(r"\.cp-slides \{[^}]*--cp-thumb-w: calc\(1440px \* var\(--cp-k\)\)", CSS)


def test_the_panel_css_is_one_scroller_with_fixed_head_and_add_card_and_theme_aware_dividers():
    block = CSS[CSS.index("V2.1 — panel izquierdo de diapositivas"):CSS.index("@media (max-width: 640px)")]
    assert re.search(r"\.cp-slides \{[^}]*overflow-y: auto;[^}]*scrollbar-gutter: stable;[^}]*border-right: 1px solid var\(--content-line\);", block)       # el PANEL es el scroller: su barra corre de borde a borde
    assert re.search(r"\.cp-slides__scroll \{ position: relative; flex: 1 0 auto; \}", block)                       # la lista interior no scrollea por su cuenta
    assert re.search(r"\.cp-slides__head \{ position: sticky; top: 0;[^}]*background: var\(--content-surface\);", block)
    assert re.search(r"\.cp-slides__add \{ position: sticky; bottom: 0;[^}]*background: var\(--content-surface\);", block)
    assert "var(--line)" not in block and "border-bottom: 1px solid var(--content-line)" in CSS                      # divisorias con el token que cambia con el tema (en claro `--line` casi no se veía)
    for sel in (".cp-slides { scrollbar-width: thin;", ".cp-slides::-webkit-scrollbar { width: 9px;", ".cp-slides::-webkit-scrollbar-track", ".cp-slides::-webkit-scrollbar-thumb {", ".cp-slides::-webkit-scrollbar-thumb:hover"):
        assert sel in TOOLS, sel                                                                                       # la misma regla de scrollbar del Hub, sin colores propios
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", block), "ningún color propio: solo tokens del Hub"
    assert block.index(".cp-slide:hover .cp-slide__thumb { outline-color: var(--content-ink); }") < block.index('.cp-slide[aria-current="true"] .cp-slide__thumb { outline: 2px solid var(--gold);')      # hover blanco; la activa conserva el dorado (gana por orden)
    assert re.search(r"\.cp-slide__no \{ position: absolute; top: 2px; left: 2px; z-index: 2; height: 13px;", block)  # el número va superpuesto en la esquina, sin columna propia


def test_the_panel_script_loads_thumbnails_lazily_locks_the_target_while_navigating_and_never_scrolls_horizontally():
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", JS, flags=re.S)                                    # solo código, sin comentarios (misma expresión que el test del estado transitorio)
    assert 'matchMedia("(min-width: 901px)")' in JS and "requestIdleCallback" in JS and 'getAttribute("data-src")' in JS      # después del editor y solo en pantallas anchas
    assert "refreshThumbs();" in JS[JS.index("originalBlocks = JSON.stringify(blocksNow());"):JS.index('toast("Guardado"')]       # tras guardar las miniaturas reflejan lo guardado
    assert "MutationObserver" in JS and "content-theme-" in JS                                                          # el documento de miniaturas no corre JS: si cambia el tema se recarga ese único iframe
    assert "slidesScroll.clientWidth / 1440" in JS and "ResizeObserver" in JS                                           # la escala sale del área útil real (descontada la barra de scroll)
    assert "scrollIntoView({ block: \"nearest\" })" not in JS and "keepVisible" in JS                                    # ajuste solo vertical: scrollIntoView también movería el eje horizontal
    assert "navTarget" in JS and "settleNav" in JS and 'performance.now() - lastScrollAt > 160' in JS                  # el destino de un clic queda fijo hasta que el scroll se asienta: sin ir y venir
    assert all(ev in JS for ev in ('"wheel"', '"touchstart"', '"keydown"', '"pointerdown"')) and "releaseNav" in JS      # si la persona toma el control del scroll, manda su posición
    assert "setTimeout" not in code and "innerHTML" not in code                                                        # sin temporizadores propios ni HTML interpolado
    assert 'if (d.readyState === "complete" && slideSections().length) loadThumbs();' in JS
    assert "Diapositiva eliminada: " in JS and 'no.textContent = it.removed ? "–" : pad2(n);' in JS                      # las eliminadas se atenúan y no consumen numeración
