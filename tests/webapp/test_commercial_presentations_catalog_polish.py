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
    rule = re.search(r"\.module-content, \.copilot-panel, html\.content-doc \{[^}]*\}", TOOLS)
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


def test_the_scroll_reveal_reuses_the_presentation_timeline_mechanism_without_a_second_engine(world):
    doc = _seed_doc(world)
    assert len(re.findall(r'animation-timeline:view\(\)', doc)) >= 50                                            # el reveal por scroll ya existe en las slides, con view timelines inline
    hero = re.search(r'<section id="s1".*?</section>', doc, re.S).group(0)
    assert "animation-timeline:view()" not in hero                                                               # el hero conserva su entrada inmediata
    js = (VANTURE / "vanture.js").read_text(encoding="utf-8")
    assert "IntersectionObserver" not in js and "content-reveal" not in doc and not re.search(r'class="reveal[ "]', doc)      # sin segundo motor ni clases de reveal paralelas
    assert 'href="#s9"' in doc                                                                                   # el riel por anclas sigue siendo navegación nativa (el timeline lo deja revelado por posición)
    off = '[style*="animation-timeline:view()"]{animation:none!important}'
    assert VANTURE_CSS.count(off) == 2                                                                           # una para prefers-reduced-motion y otra para el modo edición (no oculta ni mueve el blanco de acciones)
    assert re.search(r"@media \(prefers-reduced-motion: reduce\)\{[^@]*?\[style\*=\"animation-timeline:view\(\)\"\]\{animation:none!important\}", VANTURE_CSS.replace("\n", ""))
    assert re.search(r"html\[data-cp-edit\] \[style\*=\"animation-timeline:view\(\)\"\]\{animation:none!important\}", VANTURE_CSS)
    assert 'animation-timeline:scroll(root)' in doc and "scroll(root)" not in VANTURE_CSS                         # la barra de progreso (timeline de scroll del documento) no la apaga ninguna de las dos reglas
