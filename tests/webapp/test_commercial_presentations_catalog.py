"""
test_commercial_presentations_catalog.py — Presentaciones V1: homologación del CATÁLOGO con el Hub (cabecera CRM, filtro de CCHH, agrupador con contador, estado vacío).

Garantías:
  * cabecera: MISMO contrato de markup que el Dashboard CRM (`.hero-lead-row` > `.hero-lead.full-page-subtitle`), sin CSS equivalente propio;
  * filtro de página ANTES del agrupador (`form.hc-profile-filters.filter-toolbar-card`), solo «Buscar», placeholder acordado, sin selects ni endpoints nuevos;
  * agrupador = `details.dashboard-section` del Hub con título + contador `.dashboard-section__count` y la tabla en `__body`;
  * contador inicial = total real (semilla + copias), con pluralización correcta; cada fila trae su texto de búsqueda (título + responsable) y NO la narrativa del documento;
  * estado vacío con el primitivo `.dashboard-empty` (oculto de entrada); acciones y permisos por fila intactos;
  * cero CSS propio de filtro/agrupador/contador en commercial.css (todo sale de tools.css).
La interacción en vivo (tipeo, contador, vacío, claro/oscuro, responsive) la cubre el gate E2E de navegador.
"""
from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

from tests.webapp.test_commercial_presentations_access import (  # noqa: F401  (fixtures y helpers compartidos)
    A, B, OWNER_C, SEED_ID, _copy, world,
)

REPO = Path(__file__).resolve().parents[2]
COMMERCIAL_CSS = (REPO / "webapp" / "static" / "commercial.css").read_text(encoding="utf-8")
TOOLS_CSS = (REPO / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
HC_CSS = (REPO / "webapp" / "static" / "human_capital.css").read_text(encoding="utf-8")
CRM_HTML = (REPO / "webapp" / "templates" / "commercial" / "crm.html").read_text(encoding="utf-8")
PRESENTATIONS_JS = (REPO / "webapp" / "static" / "commercial_presentations.js").read_text(encoding="utf-8")


def _catalog(world, email=A) -> BeautifulSoup:
    r = world.client(email).get("/comercial/presentaciones")
    assert r.status_code == 200
    return BeautifulSoup(r.text, "html.parser")


def test_the_header_uses_the_same_markup_contract_as_the_crm_dashboard(world):
    soup = _catalog(world)
    sub = soup.select_one(".module-hero > .hero-lead-row > p.hero-lead.full-page-subtitle")
    assert sub is not None and "Presentaciones comerciales" in sub.text
    assert soup.select_one(".module-hero > p.eyebrow") is not None and soup.select_one(".module-hero > h1") is not None
    assert soup.select_one(".module-hero > p.hero-lead") is None                            # nunca como hijo directo: lo tomaría la regla `.module-hero > p:not(.eyebrow)` (más chica y apagada)
    assert 'class="hero-lead-row"' in CRM_HTML and 'class="hero-lead full-page-subtitle"' in CRM_HTML   # el contrato que se replica sigue siendo el del CRM


def test_the_filter_is_a_page_level_hub_toolbar_before_the_grouping_with_only_a_search_field(world):
    soup = _catalog(world)
    form = soup.select_one("form.hc-profile-filters.filter-toolbar-card")
    assert form is not None and form.get("role") == "search" and form.find_parent(class_="dashboard-section") is None
    sections = soup.select_one(".dashboard-sections")
    assert sections is not None and form.find_next(class_="dashboard-sections") is sections   # va antes del agrupador, en el mismo panel
    fields = form.select(".feedback-field")
    assert len(fields) == 1 and len(form.find_all(["select", "button", "textarea"])) == 0
    label, field = form.select_one("label[for=cp-search-q]"), form.select_one("input#cp-search-q")
    assert label is not None and label.text.strip() == "Buscar"
    assert field is not None and field.get("type") == "text" and field.get("placeholder") == "Título, descripción, autor..." and field.get("autocomplete") == "off"
    assert not form.get("action") and not form.get("hx-get") and not form.get("method")        # sin endpoint, sin parámetros, sin preferencias persistidas


def test_the_table_lives_in_a_hub_grouping_with_a_right_aligned_count_in_its_header(world):
    _copy(world, A, title="Propuesta Acme")
    soup = _catalog(world)
    section = soup.select_one("details.dashboard-section")
    assert section is not None and section.has_attr("open")
    head = section.select_one("summary.dashboard-section__head")
    assert [c.name for c in head.find_all(True, recursive=False)] == ["span", "span", "span"]
    assert [c["class"][0] for c in head.find_all(True, recursive=False)] == ["dashboard-section__caret", "dashboard-section__title", "dashboard-section__count"]
    assert head.select_one(".dashboard-section__title").text.strip() == "Presentaciones"
    body = section.select_one(".dashboard-section__body")
    assert body is not None and body.select_one(".matrix-scroll > table.dashboard-table#cp-catalog-table") is not None
    assert len(soup.select(".dashboard-section")) == 1 and len(soup.select("table")) == 1       # ni segundo agrupador ni variante nueva de tabla


def test_the_initial_count_is_the_real_total_with_correct_plural_and_search_text_is_title_plus_owner_only(world):
    _copy(world, A, title="Propuesta Acme")
    _copy(world, B, title="Oferta Beta")
    soup = _catalog(world)
    rows = soup.select("#cp-catalog-table tbody tr")
    assert len(rows) == 3                                                                        # semilla institucional + 2 copias independientes
    assert soup.select_one("[data-cp-count]").text.strip() == "3 presentaciones"
    by_title = {r.select_one("strong").text: r["data-cp-search"] for r in rows}
    assert by_title["Propuesta Acme"].startswith("Propuesta Acme ") and by_title["Propuesta Acme"].lower().endswith("com-a")
    assert by_title["Oferta Beta"].lower().endswith("com-b")
    seed_title = next(t for t in by_title if t not in ("Propuesta Acme", "Oferta Beta"))
    assert "Institucional" in by_title[seed_title] or "danwor" in by_title[seed_title].lower() or by_title[seed_title].startswith(seed_title)
    for text in by_title.values():                                                               # nunca la narrativa del documento ni el HTML
        assert len(text) < 200 and "<" not in text


def test_the_count_is_singular_when_only_the_institutional_seed_exists(world):
    soup = _catalog(world)
    assert len(soup.select("#cp-catalog-table tbody tr")) == 1
    assert soup.select_one("[data-cp-count]").text.strip() == "1 presentación"


def test_the_empty_state_is_the_hub_primitive_hidden_until_the_search_leaves_no_rows(world):
    soup = _catalog(world)
    empty = soup.select_one("[data-cp-empty]")
    assert empty is not None and empty.has_attr("hidden") and "dashboard-empty" in empty["class"] and empty["data-empty-kind"] == "no_matches"
    assert empty.select_one(".dashboard-empty__label").text.strip() == "Sin resultados" and "No se encontraron presentaciones" in empty.select_one("p").text
    assert soup.select_one("[data-cp-table-wrap]") is not None and "matrix-scroll" in soup.select_one("[data-cp-table-wrap]")["class"]
    assert empty.find_parent(class_="dashboard-section__body") is not None                       # el vacío vive dentro del agrupador, en lugar de la tabla


def test_row_actions_and_permissions_are_unchanged(world):
    mine, theirs = _copy(world, A, title="Mía"), _copy(world, B, title="Ajena")
    soup = _catalog(world)
    acts = {tr["data-presentation-id"]: [a.get("aria-label") for a in tr.select(".dw-row-actions > *")] for tr in soup.select("#cp-catalog-table tbody tr")}
    base = ["Ver presentación", "Presentar en una pestaña nueva", "Copiar enlace", "Copiar como nueva presentación"]
    assert acts[SEED_ID] == base                                                                 # la semilla institucional nunca se edita
    assert acts[mine] == base + ["Editar presentación"] and acts[theirs] == base                 # solo el responsable edita
    assert len(soup.select("[data-cp-copy-link]")) == 3 and len(soup.select("[data-cp-copy-open]")) == 3
    owner_acts = {tr["data-presentation-id"]: len(tr.select(".dw-row-actions > *")) for tr in _catalog(world, OWNER_C).select("#cp-catalog-table tbody tr")}
    assert owner_acts[mine] == 5 and owner_acts[theirs] == 5 and owner_acts[SEED_ID] == 4         # CONTENT_OWNER edita cualquier copia, nunca la semilla


def test_the_page_content_is_wrapped_in_the_panel_that_carries_the_hero_spacing(world):
    soup = _catalog(world)
    panel = soup.select_one("main.module-content > .module-hero + #cp-catalog-panel")
    assert panel is not None and panel.select_one("form.hc-profile-filters") is not None and panel.select_one(".dashboard-sections") is not None
    assert re.search(r"\.module-hero \+ #cp-catalog-panel\s*\{\s*margin-top:\s*24px", COMMERCIAL_CSS)      # MISMA convención por-panel que CCHH/Operaciones


def test_no_presentations_specific_styles_for_the_filter_the_grouping_or_the_count(world):
    for forbidden in (".cp-filter", ".cp-search", ".cp-count", ".cp-section", ".cp-grouping", ".cp-empty", ".cp-header", ".cp-subtitle"):
        assert forbidden not in COMMERCIAL_CSS and forbidden not in PRESENTATIONS_JS
    for shared in (".dashboard-section__count", ".dashboard-section__head", ".hc-profile-filters", ".hero-lead"):
        assert shared in TOOLS_CSS or shared in COMMERCIAL_CSS                                  # existen en el tier compartido…
    assert not re.search(r"^\s*\.hc-profile-filters[^{]*\{", COMMERCIAL_CSS, re.M)               # …y commercial.css no los redefine
    assert not re.search(r"^\s*\.dashboard-section(__\w+)?\s*\{", COMMERCIAL_CSS.split("Presentaciones V1 (CM03)", 1)[1], re.M)
    assert "dashboard-section__count" not in COMMERCIAL_CSS.split("Presentaciones V1 (CM03)", 1)[1].replace("`.dashboard-section__count`", "")


def test_the_filter_look_lives_next_to_its_layout_in_tools_css_and_no_longer_in_the_cchh_stylesheet(world):
    assert 'padding: 8px 11px; border: 1px solid rgba(128,128,128,.35);' in TOOLS_CSS
    assert '.hc-profile-filters input[type="text"]' in TOOLS_CSS and ".hc-profile-filters input:focus" in TOOLS_CSS
    assert '.hc-profile-filters input[type="text"]' not in HC_CSS and ".hc-profile-filters input:focus" not in HC_CSS    # movidas, no copiadas


def test_the_client_filter_has_no_network_or_persistence_and_never_touches_the_narrative(world):
    block = PRESENTATIONS_JS.split("// ── Buscar (catálogo)", 1)[1].split("// ── Copiar enlace", 1)[0]
    for forbidden in ("fetch(", "XMLHttpRequest", "localStorage", "sessionStorage", "innerHTML", "location."):
        assert forbidden not in block
    assert 'data-cp-search' in block and "toLowerCase" in block and r"\s+" in block and ".trim()" in block   # normaliza: minúsculas + espacios colapsados/recortados
    assert "preventDefault" in block                                                             # Enter no recarga ni navega


def test_the_presentations_css_block_has_balanced_comments_so_the_table_floor_rule_is_not_swallowed():
    block = COMMERCIAL_CSS.split("Presentaciones V1 (CM03)", 1)[1]
    assert block.count("/*") + 1 == block.count("*/")                                            # +1: el comentario de cabecera abre antes del marcador; un `*/` de más descarta la regla que sigue
    head, rules = block.split("*/", 1)
    assert ".dashboard-table.cp-catalog { min-width: 820px; }" in rules.split("/*")[0]           # la regla vive FUERA de cualquier comentario
