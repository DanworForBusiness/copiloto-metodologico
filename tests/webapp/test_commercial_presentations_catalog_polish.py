"""Presentaciones V1 — pulido final del catálogo y de la visualización de componentes.

Fechas sin valor (fecha de liberación), contrato de columnas común a las dos colecciones, «Componentes reutilizables» colapsado, responsables institucionales, sin badge «Institucional», vista previa a escala
proporcional (ancho de referencia) y scrollbar del Hub en el documento embebible.
"""
from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

from tests.webapp.test_commercial_presentations_access import A, OWNER_C, SEED_ID, _copy, world  # noqa: F401
from tests.webapp.test_commercial_presentations_components import _catalog, _mk
from webapp.services import commercial_presentations as cp

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "webapp" / "static"
VANTURE = REPO / "content_packs" / "datasphere_core" / "assets" / "presentations" / "vanture-bdc"
CSS = (STATIC / "commercial.css").read_text(encoding="utf-8")
TOOLS = (STATIC / "tools.css").read_text(encoding="utf-8")
JS = (STATIC / "commercial_presentations.js").read_text(encoding="utf-8")
VANTURE_CSS = (VANTURE / "vanture.css").read_text(encoding="utf-8")


def _cells(tr):
    return [td.get_text(" ", strip=True) for td in tr.select("td")]


def test_every_empty_last_edit_date_shows_the_release_date_without_mutating_anything(world):
    assert cp.RELEASE_DATE_LABEL == "29-10-2026"
    before = world.files()
    soup = _catalog(world)
    seed = soup.select_one(f'#cp-catalog-table tr[data-presentation-id="{SEED_ID}"]')
    assert _cells(seed)[3] == "29-10-2026"                                                                 # la semilla no tiene fecha propia
    for row in soup.select("#cc-catalog-table tbody tr"):
        assert _cells(row)[3] == "29-10-2026"                                                              # ni los componentes institucionales
    assert world.files() == before and not (world.directory / "components").exists()                       # solo se muestra: no se escribió nada


def test_dates_that_exist_are_never_replaced_by_the_release_date(world):
    pid = _copy(world, A)
    cid = _mk(world, A)
    soup = _catalog(world)
    assert re.fullmatch(r"\d\d-\d\d-\d{4}", _cells(soup.select_one(f'tr[data-presentation-id="{pid}"]'))[3])
    assert re.fullmatch(r"\d\d-\d\d-\d{4}", _cells(soup.select_one(f'tr[data-cc-ref="own:{cid}"]'))[3])


def test_the_components_table_has_no_provenance_column_and_the_two_tables_share_the_column_contract(world):
    _mk(world, A)
    soup = _catalog(world)
    pres, comp = soup.select_one("#cp-catalog-table"), soup.select_one("#cc-catalog-table")
    assert [th.get_text(strip=True) for th in comp.select("thead th")] == ["Componente", "Región", "Responsable", "Última edición", "Acciones"]
    assert [th.get_text(strip=True) for th in pres.select("thead th")] == ["Presentación", "Origen", "Responsable", "Última edición", "Acciones"]
    for table in (pres, comp):
        assert "cp-catalog" in table["class"] and len(table.select("colgroup > col")) == 5 and not [c for c in table.select("colgroup > col") if c.has_attr("style")]     # sin anchos propios por tabla
        assert [th["class"] for th in table.select("thead th")] == [["left"]] * 4 + [["center"]]               # misma alineación
        for tr in table.select("tbody tr"):
            assert [td["class"] for td in tr.select("td")] == [["left"]] * 4 + [["center"]]
    assert len(re.findall(r"cp-catalog col:nth-child\(\d\)", CSS)) == 5                                     # el contrato de columnas existe UNA vez y lo consumen las dos tablas
    assert "Procedencia" not in comp.get_text()


def test_the_components_grouping_starts_collapsed_and_presentations_stays_open(world):
    sections = _catalog(world).select("details.dashboard-section")
    assert [(s["data-section-id"], s.has_attr("open")) for s in sections] == [("presentaciones", True), ("componentes", False)]
    assert ".open = true" not in JS                                                                         # nada del JS lo abre por su cuenta: usa el comportamiento nativo de <details>


def test_the_institutional_owners_are_the_agreed_names_without_touching_ownership_or_authorization(world):
    _mk(world, A, name="Propio de Ana")
    soup = _catalog(world)
    seed = soup.select_one(f'#cp-catalog-table tr[data-presentation-id="{SEED_ID}"]')
    assert _cells(seed)[2] == "Juan Manuel Melgarejo" and seed["data-cp-search"].endswith("Juan Manuel Melgarejo")
    inst = [r for r in soup.select("#cc-catalog-table tbody tr") if not r["data-cc-ref"].startswith("own:")]
    assert len(inst) == 4 and {_cells(r)[2] for r in inst} == {"Danwor"}
    own = soup.select_one('#cc-catalog-table tr[data-cc-ref^="own:"]')
    assert _cells(own)[2] not in ("Danwor", "Juan Manuel Melgarejo")                                         # los propios conservan su metadata
    view = cp.get(SEED_ID)
    assert view["is_seed"] is True and view["owner_id"] is None and view["created_by_id"] is None            # el ownership no cambió: es solo la etiqueta mostrada
    assert not cp.can_edit_presentation_user({"sub": OWNER_C}, view) and not cp.can_delete_presentation_user({"sub": OWNER_C}, view)


def test_no_institutional_badge_is_rendered_in_either_collection_and_actions_keep_the_differential_treatment(world):
    _mk(world, A)
    soup = _catalog(world)
    assert not [b for b in soup.select(".status-badge") if b.get_text(strip=True) == "Institucional"]
    assert [b.get_text(strip=True) for b in soup.select("#cc-catalog-table .status-badge")] == ["Propio"]    # el badge de los propios se conserva
    seed = soup.select_one(f'#cp-catalog-table tr[data-presentation-id="{SEED_ID}"]')
    acts = seed.select(".dw-row-actions > *")
    assert acts[4].has_attr("disabled") and acts[5].has_attr("disabled") and "solo lectura" in acts[4]["aria-label"]      # la inmutabilidad sigue en las acciones
    for tr in soup.select('#cc-catalog-table tr[data-cc-ref]:not([data-cc-ref^="own:"])'):
        buttons = tr.select(".dw-row-actions > *")
        assert buttons[2].has_attr("disabled") and buttons[3].has_attr("disabled")


def test_the_component_preview_is_laid_out_at_the_presentation_reference_width_and_scaled_to_the_available_width(world):
    assert re.search(r"\.cc-preview-frame \{[^}]*width: 1260px", CSS) and "transform-origin: 0 0" in CSS and ".cc-preview-box { position: relative; overflow: hidden" in CSS
    assert "fitPreview" in JS and "vBox.clientWidth / vFrame.offsetWidth" in JS and 'scale(" + k + ")' in JS        # el iframe (renderer real) conserva su ancho de referencia y se escala
    doc = world.client(A).get("/comercial/componentes/vista?ref=layer-stack:bdc-architecture").text
    assert "max-width:1260px" in doc and "content-doc" in doc                                                  # el documento de la vista previa maqueta al ancho de la slide (1180 + márgenes)
    assert "@media (max-width" in VANTURE_CSS and ".layer-row{grid-template-columns:1fr!important" in VANTURE_CSS    # el responsive de la presentación real sigue ahí, intacto


def test_the_embeddable_documents_reuse_the_hub_scrollbar_rule_without_colors_of_their_own(world):
    pid = _copy(world, A)
    for url in (f"/presentaciones/{SEED_ID}", f"/presentaciones/{pid}?embed=1&edit=1", "/comercial/componentes/vista?ref=layer-stack:bdc-architecture"):
        soup = BeautifulSoup(world.client(A).get(url).text, "html.parser")
        assert "content-doc" in soup.html["class"], url                                                        # lo que scrollea es el viewport del documento embebido: se engancha a la regla del Hub
    rule = re.search(r"\.module-content, \.copilot-panel, html\.content-doc, \.cp-slides \{[^}]*\}", TOOLS)      # + el panel de miniaturas del editor (V2.1): misma regla, sin colores propios
    assert rule and "scrollbar-width: thin" in rule.group(0) and "rgba(20,22,28,.18)" in rule.group(0)
    assert TOOLS.count("rgba(20,22,28,.18)") == 1                                                              # una sola definición del color
    assert "scrollbar" not in VANTURE_CSS and "scrollbar" not in (VANTURE / "vanture.js").read_text(encoding="utf-8")


def _seed_doc(world) -> str:
    return world.client(A).get(f"/presentaciones/{SEED_ID}").text


def test_the_hero_orbit_is_softened_in_the_light_theme_only_with_a_hub_token_and_nothing_else_changes(world):
    doc = _seed_doc(world)
    orbit = re.search(r'<div class="hero-orbit" style="([^"]*)"', doc).group(1)
    assert "animation:orbit 46s linear infinite" in orbit and "aspect-ratio:1" in orbit and "border:1px solid" in orbit and "border-radius:50%" in orbit     # geometría, grosor y movimiento intactos
    assert "background:var(--gold)" in doc.split('class="hero-orbit"', 1)[1].split("</div>", 1)[0]                                                  # el punto sigue dorado
    rules = re.findall(r"(\.[\w-]+ )?\.hero-orbit\{([^}]*)\}", VANTURE_CSS)
    assert rules == [(".content-theme-light ", "border-color:var(--line-2,var(--content-line-strong))!important")]       # UNA regla, solo tema claro y solo el color de la trayectoria (token del riel de los indicadores laterales)
    assert not re.search(r"#[0-9a-fA-F]{3,8}|rgba?\(", rules[0][1])                                                  # sin colores propios: solo tokens; el tema oscuro no se toca (sin regla propia)


def test_the_seven_of_ten_punch_is_83_percent_of_its_previous_size_with_a_specific_selector(world):
    doc = _seed_doc(world)
    assert doc.count("clamp(46px,6.64vw,86px)") == 1 and "clamp(56px,8vw,104px)" not in doc                    # 56x0,83 / 8x0,83 / 104x0,83, el resto de la tipografía del punch no cambia
    span = re.search(r'<span style="font-size:clamp\(46px,6\.64vw,86px\);([^"]*)"', doc).group(1)
    assert "font-weight:800" in span and "line-height:1" in span and "color:var(--gold)" in span and "letter-spacing:-.03em" in span


def test_the_footer_drops_the_tagline_and_gives_the_disclaimer_a_wide_short_box_symmetric_with_the_logos(world):
    footer = BeautifulSoup(_seed_doc(world), "html.parser").footer
    assert "Data & AI for Business" not in footer.get_text(" ", strip=True)                                      # la frase bajo los logos se eliminó (el alt del encabezado no se toca)
    row = footer.select_one(".footer-row")
    assert "display:flex" in row["style"] and "flex-wrap:wrap" in row["style"] and "align-items:center" in row["style"]     # logos centrados respecto de la caja; en móvil la caja baja
    logos, box = row.find_all("div", recursive=False)
    assert len(logos.find_all("img")) == 2 and "margin-bottom" not in logos.find("div")["style"]
    assert "flex:1 1 560px" in box["style"] and "min-height" not in box["style"]                                 # más ancha (toma el resto de la fila) y sin alto mínimo: baja a lo que ocupa el texto
    assert box.get_text(" ", strip=True).startswith("AVISO Contenido informativo preparado por Danwor")           # el aviso no cambió


def test_the_scroll_reveal_reuses_the_hub_contract_activated_once_per_visit_without_a_second_engine(world):
    doc = _seed_doc(world)
    assert len(re.findall(r'animation-timeline:view\(\)', doc)) >= 50                                           # el template no cambia: de ahí salen las unidades y queda de degradación (Chromium sin JS/IO)
    assert "content-reveal" not in doc and "is-visible" not in doc                                              # la clase la agrega SOLO el JS: sin JS el contenido queda visible por defecto
    hero = re.search(r'<section id="s1".*?</section>', doc, re.S).group(0)
    assert "animation-timeline:view()" not in hero                                                               # el hero conserva su entrada inmediata
    js = (VANTURE / "vanture.js").read_text(encoding="utf-8")
    assert "'IntersectionObserver' in window" in js and "!reduceMotion" in js and "hasAttribute('data-cp-edit')" in js         # sin soporte, en reduced-motion y en edición no se activa
    assert "s.id !== 's1'" in js                                                                                 # la portada queda fuera
    assert "threshold: 0.05" in js and "rootMargin: '0px 0px -4% 0px'" in js                                    # mismas opciones que `initContentReveals` del Copiloto
    assert "classList.add('content-reveal')" in js and "classList.add('is-visible')" in js and "revealIo.unobserve(" in js      # el contrato del Hub, UNA sola vez por elemento
    assert "classList.remove('is-visible')" not in js and "toggle('is-visible'" not in js                       # lo revelado nunca se vuelve a ocultar
    assert "function revealSection" in js or "var revealSection" in js
    assert "'hashchange'" in js and "a[href^=\"#\"]" in js                                                       # anclas: riel, «Siguiente» y hash de la URL revelan el destino de inmediato
    assert "getBoundingClientRect().top < revealVh" in js                                                        # lo que ya está en pantalla o por encima entra visible, sin animar
    assert 'href="#s9"' in doc
    content_css = (STATIC / "content.css").read_text(encoding="utf-8")
    assert ".content-reveal.is-visible { opacity: 1; transform: none; }" in content_css and "transition: opacity .48s cubic-bezier(.22,.61,.36,1), transform .48s cubic-bezier(.22,.61,.36,1);" in content_css
    assert not re.search(r"(?m)^\s*\.content-reveal\s*\{", VANTURE_CSS)                                          # NO se redefine el contrato del Hub: se reutiliza el de content.css
    both = '.content-reveal[style*="animation-timeline:view()"],.content-reveal [style*="animation-timeline:view()"]{animation:none!important}'
    assert VANTURE_CSS.count(both) == 1                                                                          # el `view()` se apaga SOLO donde la unidad recibe el reveal nuevo: no compiten
    off = '[style*="animation-timeline:view()"]{animation:none!important}'
    assert VANTURE_CSS.count(off) == 3                                                                           # esa regla + prefers-reduced-motion + modo edición (contenido siempre visible)
    assert re.search(r"@media \(prefers-reduced-motion: reduce\)\{[^@]*?\[style\*=\"animation-timeline:view\(\)\"\]\{animation:none!important\}", VANTURE_CSS.replace("\n", ""))
    assert re.search(r"html\[data-cp-edit\] \[style\*=\"animation-timeline:view\(\)\"\]\{animation:none!important\}", VANTURE_CSS)
    assert 'animation-timeline:scroll(root)' in doc and "scroll(root)" not in VANTURE_CSS                         # la barra de progreso no se toca


def test_the_reveal_composes_with_the_classes_that_define_their_own_transition_opacity_or_transform():
    css = VANTURE_CSS
    assert ".content-reveal.dw-card{transition:opacity .48s cubic-bezier(.22,.61,.36,1),transform .2s,border-color .2s}" in css                       # `.dw-card` pisaría el fade (content.css): fade del Hub y hover original (.2 s) intactos
    assert ".content-reveal.flow-hub{transition:opacity .48s" in css and "box-shadow .4s ease,border-color .4s ease}" in css
    assert ".scroll-cue.content-reveal.is-visible{opacity:.4}" in css and ".scroll-cue.content-reveal.is-visible:hover" in css            # el cue «Siguiente» conserva su atenuado de reposo y su hover
    assert ".arch__side.content-reveal{transform:translateY(14px) rotate(180deg)}" in css and ".arch__side.content-reveal.is-visible{transform:rotate(180deg)}" in css      # el rótulo vertical no se da vuelta al revelarse
    assert "@media (max-width: 860px){.arch__side.content-reveal{transform:translateY(14px)}.arch__side.content-reveal.is-visible{transform:none}}" in css      # y en móvil sigue sin rotación


def test_only_the_cover_keeps_a_gold_background_glow_and_every_other_slide_has_a_clean_background(world):
    soup = BeautifulSoup(_seed_doc(world), "html.parser")
    sections = soup.select("section[id^=s]")
    assert [s["id"] for s in sections] == [f"s{i}" for i in range(1, 13)]
    for sec in sections[1:]:
        assert "background" not in sec.get("style", "") and not [e for e in [sec, *sec.find_all(True)] if "radial-gradient" in e.get("style", "")], sec["id"]     # fondo limpio, sin glow dorado
    cover = sections[0]
    assert [e for e in cover.find_all(True) if "radial-gradient" in e.get("style", "")]                                                                      # la portada conserva su glow
    assert ".spotlight" in VANTURE_CSS and "spotlight" in (VANTURE / "vanture.js").read_text(encoding="utf-8")                                                # y el glow del puntero de la portada


def test_the_fixed_header_starts_transparent_and_fades_its_background_and_border_in_together_on_scroll(world):
    page = world.client(A).get(f"/presentaciones/{SEED_ID}").text
    bar = BeautifulSoup(page, "html.parser").select_one(".cp-topbar")
    assert bar and "background" not in bar["style"] and "border" not in bar["style"]                                  # sin fondo ni línea propios: los pone la capa que hace el fade
    layer = re.search(r"\.cp-topbar::before\{([^}]*)\}", VANTURE_CSS).group(1)
    assert "background:var(--bg)" in layer and "border-bottom:1px solid var(--line)" in layer and "opacity:var(--cp-topbar-rest)" in layer       # UNA capa: fondo pleno + línea, ambos con el MISMO fade
    assert "--cp-topbar-rest:0}" in VANTURE_CSS and ".content-theme-light .cp-topbar{--cp-topbar-rest:.8}" in VANTURE_CSS                  # reposo: transparente en oscuro; 80 % en claro (los logos son para fondo oscuro)
    assert ".content-theme-light .cp-topbar .theme-toggle{background:transparent}" in VANTURE_CSS                                   # el botón de tema toma el tono de la capa (solo en claro; el oscuro no cambia)
    assert "transition" not in layer and re.search(r"\.cp-topbar\.is-ready::before\{transition:opacity 1s ease\}", VANTURE_CSS)  # 1 s, y solo tras el primer frame (carga scrolleada = pleno de una)
    assert ".cp-topbar.is-solid::before{opacity:1}" in VANTURE_CSS and "isolation:isolate" in VANTURE_CSS
    js = (VANTURE / "vanture.js").read_text(encoding="utf-8")
    assert "classList.toggle('is-solid'" in js and "> 4" in js and "classList.add('is-ready')" in js and "{ passive: true }" in js
    assert not BeautifulSoup(world.client(A).get(f"/presentaciones/{SEED_ID}?embed=1").text, "html.parser").select_one(".cp-topbar")   # el documento embebido en el Hub no trae encabezado
