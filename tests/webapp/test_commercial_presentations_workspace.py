"""Presentaciones V2.0 — workspace dedicado de edición.

El editor pierde la navegación lateral del Hub (la plantilla no incluye `commercial_nav` ni su handle) y gana un encabezado compacto (← Volver a Presentaciones · identidad con el (i) de visibilidad del enlace · acciones
actuales). Se conserva la barra superior, el tema, los diálogos, los permisos y el guardado. El catálogo, el detalle y el modo lectura mantienen su shell normal. Sin rutas nuevas.
"""
from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

from tests.webapp.test_commercial_presentations_access import A, APP_OWNER, B, HUB_ONLY, OWNER_C, SEED_ID, _copy, world  # noqa: F401

REPO = Path(__file__).resolve().parents[2]
CSS = (REPO / "webapp" / "static" / "commercial.css").read_text(encoding="utf-8")
JS = (REPO / "webapp" / "static" / "commercial_presentations.js").read_text(encoding="utf-8")
TEMPLATE = (REPO / "webapp" / "templates" / "commercial" / "presentation_edit.html").read_text(encoding="utf-8")


def _soup(world, path, email=A):
    r = world.client(email).get(path)
    assert r.status_code == 200, (path, r.status_code)
    return BeautifulSoup(r.text, "html.parser")


def test_the_editor_page_is_a_workspace_without_the_hub_side_navigation_and_keeps_the_topbar(world):
    pid = _copy(world, A)
    page = _soup(world, f"/comercial/presentaciones/{pid}/editar")
    shell = page.select_one(".app-shell")
    assert "cp-workspace" in shell["class"] and "no-copilot" in shell["class"]
    assert not page.select("aside.module-nav, .resize-handle, [data-resize]"), "sin menú lateral ni su handle: el ancho queda libre para el workspace"
    assert "commercial_nav" not in TEMPLATE.replace("`commercial_nav`", "") and "sidebar_resize_handle" not in TEMPLATE.replace("`commercial_nav` y su handle", "")      # la plantilla ya no los incluye
    top = page.select_one("header.topbar")                                                                      # la barra superior transversal se conserva: marca, workspace, tema y sesión
    assert top and top.select_one('button.theme-toggle[aria-label^="Alternar tema"]') and top.select_one("button.logout-btn") and top.select_one("a.animated-brand") and top.select_one("button.mobile-menu")
    assert page.select_one("main#content.module-content [data-cp-editor] iframe[data-cp-frame]")


def test_the_compact_header_has_back_identity_visibility_info_and_the_current_actions(world):
    pid = _copy(world, A, title="Presentación para Acme")
    page = _soup(world, f"/comercial/presentaciones/{pid}/editar")
    header = page.select_one("[data-cp-editor] > header.catalog-actionbar.cp-wsheader")
    kids = [c for c in header.find_all(True, recursive=False)]
    assert [c.name for c in kids] == ["a", "div", "div"]
    assert kids[0]["href"] == "/comercial/presentaciones" and kids[0].has_attr("data-cp-back") and kids[0].get_text(strip=True) == "← Volver a Presentaciones"
    ident = kids[1]
    assert ident.select_one("h1.cp-wsheader__title").get_text(strip=True) == "Presentación para Acme" and ident.select_one(".cp-wsheader__eyebrow").get_text(strip=True) == "Editando"
    info = ident.select_one(".chart-tooltip-anchor")
    assert info.select_one("button.activity-kpi__info[aria-label]") and "cualquier usuario autorizado del Hub" in info.select_one(".chart-tooltip").get_text()      # el contrato real de acceso se conserva
    bar = kids[2]
    assert "cp-toolbar" in bar["class"] and [(b.name, b.text.strip()) for b in bar.find_all(["button", "span"], recursive=False)] == [("button", "Cancelar"), ("span", "Sin guardar"), ("button", "Guardar")]
    assert bar.select_one("[data-cp-dirty]").has_attr("hidden") and bar.select_one("[data-cp-save]").has_attr("disabled")
    ed = page.select_one("[data-cp-editor]")
    assert ed["data-back-url"] == "/comercial/presentaciones" and ed["data-save-url"] == f"/comercial/presentaciones/{pid}" and ed["data-revision"] == "1"       # el guardado no cambia
    assert not page.select(".module-hero, .callout, .cp-notice"), "sin el hero ni el callout extenso: ganan altura para el documento"


def test_the_catalog_the_detail_and_the_reading_mode_keep_their_normal_shell(world):
    pid = _copy(world, A)
    for path in ("/comercial/presentaciones", f"/comercial/presentaciones/{pid}"):
        page = _soup(world, path)
        assert page.select_one("aside.module-nav") and page.select_one(".resize-handle"), path                  # el shell normal del Hub, con su navegación lateral
        assert "cp-workspace" not in page.select_one(".app-shell")["class"], path
        assert not page.select_one("header.cp-wsheader"), path
    doc = world.client(A).get(f"/presentaciones/{pid}").text                                                    # el documento de lectura/presentación no cambia
    assert "cp-workspace" not in doc and "cp-wsheader" not in doc
    seed = world.client(A).get(f"/presentaciones/{SEED_ID}").text
    assert "cp-workspace" not in seed


def test_the_workspace_css_frees_the_navigation_width_and_gives_the_document_the_remaining_space():
    assert re.search(r"\.app-shell\.cp-workspace \{[^}]*grid-template-columns: minmax\(0, 1fr\);", CSS)       # una sola columna: el ancho de la navegación se libera
    assert re.search(r"\.app-shell\.cp-workspace \.module-content \{[^}]*padding: 0;[^}]*overflow: hidden;[^}]*height: calc\(100dvh - var\(--cp-topbar-h\) - 34px\);", CSS)
    assert "@media (max-width: 1180px) { .app-shell.cp-workspace .module-content { position: static; height: calc(100dvh - var(--cp-topbar-h) - 24px); } }" in CSS      # donde el Hub apila el contenido
    assert "@media (max-width: 900px) { .app-shell.cp-workspace { --cp-topbar-h: 66px; } }" in CSS             # topbar más bajo en móvil
    assert ".cp-workspace .mobile-menu { display: none; }" in CSS                                               # sin menú lateral que abrir
    assert re.search(r"\.cp-workspace__editor \.cp-workspace__frame \{[^}]*flex: 1 1 auto;[^}]*height: auto;", CSS)      # el iframe llena el resto (ya no 78vh)
    assert "--embed-height:78vh" not in TEMPLATE
    assert ".cp-wsheader .chart-tooltip { bottom: auto; top: calc(100% + 8px); left: 0;" in CSS                 # el (i) abre hacia abajo y hacia adentro
    assert "scrollbar-gutter: auto;" in CSS.split(".app-shell.cp-workspace .module-content {", 1)[1].split("}", 1)[0]      # sin el hueco de barra que reserva `.module-content`: el documento usa el ancho completo
    assert ".cp-wsheader .chart-tooltip-anchor { position: static; }" in CSS and "left: 12px; right: 12px; width: auto; max-width: none;" in CSS      # en móvil la explicación se ancla al encabezado
    # lo propio queda en commercial.css (página del editor): ningún color ni primitivo nuevo
    block = CSS[CSS.index("Presentaciones V2.0 — workspace dedicado"):CSS.index("left: 12px; right: 12px; width: auto; max-width: none; }")]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", block)


def test_back_and_cancel_share_the_unsaved_changes_confirmation_and_back_is_a_real_link():
    assert 'editor.querySelectorAll("[data-cp-cancel], [data-cp-back]")' in JS
    block = JS[JS.index("leaveBtns.forEach"):JS.index("saveBtn.addEventListener")]
    assert "ev.preventDefault()" in block and 'btn.getAttribute("href") || editor.getAttribute("data-back-url")' in block
    assert "if (refresh())" in block and "Descartar cambios" in block and "dwConfirm" in block and "saving = true" in block and "window.location.assign(back)" in block
    assert 'addEventListener("beforeunload"' in JS                                                              # el aviso nativo ante cierre de pestaña sigue ahí
    assert "href=\"/comercial/presentaciones\" data-cp-back" in TEMPLATE                                       # sin JS, «Volver» es un enlace directo al landing


def test_authorization_and_routes_are_unchanged_by_the_workspace(world):
    pid = _copy(world, A)
    assert world.client(A).get(f"/comercial/presentaciones/{pid}/editar").status_code == 200
    assert world.client(OWNER_C).get(f"/comercial/presentaciones/{pid}/editar").status_code == 200
    for email in (B, HUB_ONLY, APP_OWNER):
        assert world.client(email).get(f"/comercial/presentaciones/{pid}/editar").status_code == 403         # APP_OWNER no edita lo ajeno por ser administrador
    assert world.client(A).get(f"/comercial/presentaciones/{SEED_ID}/editar").status_code == 403              # la semilla sigue sin edición
