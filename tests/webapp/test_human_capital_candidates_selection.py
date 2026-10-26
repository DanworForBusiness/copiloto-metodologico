"""test_human_capital_candidates_selection.py — CCHH → Candidatos: selección múltiple, «Compartir perfiles» y ficha de consulta (Fases 1A y 1B).

Cubre: la FICHA de solo lectura (permisos, contenido, CV original, contexto de retorno, 404, sin escrituras), el marcado del panel (columna fija, casillas, barra, acciones), la paginación y
los filtros con selección, el endpoint del diálogo (permisos, mismo origen, límite de 25, privacidad: solo nombre + enlace al CV en SharePoint tomado de la metadata guardada, sin datos de contacto; varios CVs ⇒ se elige, nunca se asume), el módulo
JS (vía node) y las superficies que NO existen en esta fase (ZIP, descarga, auditoría de exportación, selección de todo el filtro)."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from webapp import access
from webapp.main import app
from webapp.services import auth_service, human_capital_service, human_capital_share, profile_document_repository, profile_repository
from webapp.services import profile_document_service as profile_document_service_module

_ROOT = Path(__file__).resolve().parents[2]
_HARNESS = Path(__file__).resolve().parent / "human_capital_candidates_selection.node.js"
_HC = "hc-selection-test@danwor.com"
_ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture(autouse=True)
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _HC)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(access, "APP_OWNER", "owner-selection@danwor.com")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")


def client(email: str | None = _HC) -> TestClient:
    c = TestClient(app)
    if email:
        c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def make_profile(first="Ana", last="Sintetica", **extra) -> dict:
    data = {"first_name": first, "last_name": last, "email": f"{first}.{last}@sintetico.test".lower(), "phone": "+54 11 5555-0000", "linkedin_url": "https://linkedin.example/in/x",
            "location": "CABA", "profile_skills": "SAP, Datasphere", "seniority": "Senior", "source": "LinkedIn", "tags": "tag-uno, tag-dos", "notes": "nota privada del candidato",
            "potential_rating": 4, "potential_mark": "Alto", **extra}
    return profile_repository.create(data)


def add_doc(profile_id: str, document_id="doc-1", filename="cv.pdf", web_url="https://danwor.sharepoint.com/sites/cchh/cv.pdf", **extra) -> dict:
    doc = {"document_id": document_id, "profile_id": profile_id, "provider": "sharepoint", "site_id": "s", "drive_id": "d", "item_id": f"i-{document_id}", "filename": filename,
           "content_type": "application/pdf", "size_bytes": 2048, "uploaded_at": "2026-10-01T00:00:00+00:00", "uploaded_by": "rrhh@test", "web_url": web_url, **extra}
    return profile_document_repository.create_with_explicit_id(document_id, doc)[0]


def main_of(html: str) -> str:
    """Solo el contenido de la ficha (`<main>`): el shell del Hub (topbar, diálogo de confirmación compartido) tiene sus propios formularios."""
    return re.search(r"<main id=\"content\".*?</main>", html, re.S).group(0)


def data_digest() -> dict:
    out = {}
    for sub in ("profiles", "profile-documents"):
        d = Path(config.BASE_DIR) / "human-capital" / sub
        out[sub] = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(d.glob("*.json"))} if d.exists() else {}
    return out


# ═══════════════ 1A · ficha de consulta ═══════════════
def test_detail_requires_cchh_authorization_like_every_other_screen():
    pid = make_profile()["profile_id"]
    anon = TestClient(app).get(f"/cchh/candidatos/{pid}", follow_redirects=False)
    assert anon.status_code == 303 and anon.headers["location"].startswith("/platform?next=")
    assert client("fuera-de-cchh@danwor.com").get(f"/cchh/candidatos/{pid}").status_code == 403
    assert client().get(f"/cchh/candidatos/{pid}").status_code == 200
    assert client("owner-selection@danwor.com").get(f"/cchh/candidatos/{pid}").status_code == 200              # APP_OWNER: el esquema de siempre


def test_detail_shows_the_candidate_read_only_with_the_same_blocks_as_the_edit_screen():
    p = make_profile(first="Ana", last="Pérez")
    html = client().get(f"/cchh/candidatos/{p['profile_id']}").text
    for fragment in ("Ana", "Pérez", f'href="mailto:{p["email"]}"', "+54 11 5555-0000", "CABA", "SAP, Datasphere", "Senior", "LinkedIn", "tag-uno, tag-dos", "nota privada del candidato", "Alto"):
        assert fragment in html, fragment
    assert 'href="https://linkedin.example/in/x"' in html and 'rel="noopener noreferrer"' in html
    for title in ("Datos del candidato", "Clasificación", "Notas", "Potencial a futuro", "Documentos"):
        assert f'<span class="dashboard-section__title">{title}</span>' in html, title
    assert "Ficha del candidato" in html and "← Volver al listado" in html


def test_detail_is_strictly_read_only_no_form_no_inputs_no_write_controls():
    p = make_profile()
    add_doc(p["profile_id"])
    html = main_of(client().get(f"/cchh/candidatos/{p['profile_id']}").text)
    for forbidden in ("<form", "<input", "<textarea", "<select", "hx-post", "hx-delete", "data-dw-graph-token", "dwHumanCapitalDocumentWriteToken", "Dar de baja", "Eliminar", "Subir"):
        assert forbidden not in html, forbidden
    assert f'href="/cchh/candidatos/{p["profile_id"]}/editar"' in html                                                # «Editar» es solo un enlace


def test_detail_is_the_stable_shareable_url_and_never_the_edit_screen():
    p = make_profile()
    r = client().get(f"/cchh/candidatos/{p['profile_id']}")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.url.path == f"/cchh/candidatos/{p['profile_id']}" and "/editar" not in r.url.path                      # la URL de la ficha es estable y jamás la de edición


def test_detail_documents_open_the_original_cv_in_sharepoint_with_a_plain_link():
    p = make_profile()
    add_doc(p["profile_id"], "d1", "cv-ana.pdf", "https://danwor.sharepoint.com/sites/cchh/ana.pdf")
    add_doc(p["profile_id"], "d2", "sin-enlace.docx", "")
    html = client().get(f"/cchh/candidatos/{p['profile_id']}").text
    assert "cv-ana.pdf" in html and "sin-enlace.docx" in html and "2.0 kB" in html
    assert 'href="https://danwor.sharepoint.com/sites/cchh/ana.pdf"' in html and 'target="_blank"' in html and "Abrir cv-ana.pdf en SharePoint" in html
    assert "el Hub no descarga ni copia el archivo" in html                                                           # no hay ruta de descarga del Hub


def test_detail_without_documents_and_with_a_failing_document_store_degrades_cleanly(monkeypatch):
    p = make_profile()
    assert 'data-empty-kind="no_documents"' in client().get(f"/cchh/candidatos/{p['profile_id']}").text
    from webapp.services import profile_document_service

    def boom(_pid):
        raise profile_document_repository.ProfileDocumentStorageError("sin acceso")
    monkeypatch.setattr(profile_document_service, "list_documents_for_profile", boom)
    r = client().get(f"/cchh/candidatos/{p['profile_id']}")
    assert r.status_code == 200 and 'data-error-kind="source_error"' in r.text and p["first_name"] in r.text         # la ficha igual se muestra


def test_detail_escapes_values_and_only_links_http_linkedin():
    p = make_profile(first="<b>Ana</b>", linkedin_url="javascript:alert(1)", notes="<script>x</script>")
    html = client().get(f"/cchh/candidatos/{p['profile_id']}").text
    assert "<b>Ana</b>" not in html and "&lt;b&gt;Ana&lt;/b&gt;" in html and "<script>x</script>" not in html
    assert 'href="javascript:' not in html and "javascript:alert(1)" in html                                         # se muestra como texto, nunca como enlace


def test_detail_returns_404_for_unknown_deleted_or_odd_ids():
    p = make_profile()
    assert client().get("/cchh/candidatos/no-existe").status_code == 404
    assert client().get("/cchh/candidatos/..%2Fsecreto").status_code in (404, 422)
    human_capital_service.soft_delete_profile(p["profile_id"]) if hasattr(human_capital_service, "soft_delete_profile") else profile_repository.soft_delete(p["profile_id"])
    assert client().get(f"/cchh/candidatos/{p['profile_id']}").status_code == 404                                     # dado de baja = inexistente


def test_detail_back_link_keeps_the_list_context_and_rejects_foreign_return_targets():
    p = make_profile()
    ctx = "/cchh/candidatos?q=ana&page=2&page_size=50"
    ok = client().get(f"/cchh/candidatos/{p['profile_id']}", params={"return_to": ctx}).text
    assert 'class="catalog-back" href="/cchh/candidatos?q=ana&amp;page=2&amp;page_size=50"' in ok
    assert "/editar?return_to=" in ok
    for evil in ("https://evil.example/x", "//evil.example", "javascript:alert(1)"):
        bad = client().get(f"/cchh/candidatos/{p['profile_id']}", params={"return_to": evil}).text
        assert 'class="catalog-back" href="/cchh/candidatos"' in bad and "evil.example" not in bad, evil


def test_fixed_routes_of_the_same_level_are_not_shadowed_by_the_detail_route():
    for path in ("/cchh/candidatos/nuevo", "/cchh/candidatos/importar"):
        assert client().get(path).status_code == 200, path
    assert client().get("/cchh/candidatos/compartir").status_code == 404                                              # GET no existe (el diálogo es POST)


def test_detail_never_writes_profiles_or_documents():
    p = make_profile()
    add_doc(p["profile_id"])
    before = data_digest()
    for _ in range(3):
        client().get(f"/cchh/candidatos/{p['profile_id']}")
    assert data_digest() == before


# ═══════════════ 1B · panel: columna fija, casillas, barra, acciones ═══════════════
def seed(n: int, prefix="Cand") -> list[dict]:
    return [make_profile(first=f"{prefix}{i:03d}", last="Prueba") for i in range(n)]


def page_ids(html: str) -> list[str]:
    return re.findall(r'data-hc-select="([^"]+)"', html)


def test_panel_has_a_fixed_first_selection_column_checkboxes_bar_and_new_row_action():
    rows = seed(3)
    html = client().get("/cchh/candidatos").text
    assert len(re.findall(r'<(?:th|td)[^>]*data-column-fixed="start"', html)) == 1 + len(rows)                         # 1 encabezado + 1 por fila (las celdas, no los comentarios)
    assert "data-hc-select-page" in html and 'aria-label="Seleccionar todos los candidatos de esta página"' in html
    assert sorted(page_ids(html)) == sorted(r["profile_id"] for r in rows)
    assert 'aria-label="Seleccionar a Cand000 Prueba"' in html
    bar = re.search(r'<div class="hc-selection-bar[^>]*>.*?</div>\s*</div>', html, re.S).group(0)
    assert "data-hc-selection-bar" in bar and re.search(r"\shidden>", bar.split(">", 1)[0] + ">")                       # sin selección la barra está OCULTA: no reserva altura ni márgenes
    assert html.index("</summary>") < html.index('<div class="dashboard-section__body">') < html.index("data-hc-selection-bar") < html.index('id="hc-candidatos-table"')   # dentro del agrupador «Candidatos»: bajo su encabezado, antes de la tabla
    assert "is-empty" not in bar and "disabled" not in bar                                                          # no hay estado «vacío» permanente ni acciones deshabilitadas
    clear = re.search(r"<button[^>]*data-hc-clear-selection[^>]*>(.*?)</button>", bar, re.S)
    assert clear and 'class="whatsnew-filter__clear"' in clear.group(0) and clear.group(1).strip() == "✕ Limpiar selección"   # MISMO componente que «✕ Limpiar» del panel de filtros
    assert html.count('class="whatsnew-filter__clear"') == 2                                                         # el de filtros + este, sin clase nueva
    assert "Compartir perfiles" in html
    first_col, first_th = html.index("<colgroup>"), html.index("<thead>")
    colgroup = html[first_col:first_th]
    assert re.search(r"<colgroup>\s*<col style=\"width:44px\">", colgroup) and colgroup.count("<col ") == 7        # columna de selección sin data-column-key + 5 de negocio + acciones
    for r in rows:
        assert f'href="/cchh/candidatos/{r["profile_id"]}?return_to=' in html and f'/cchh/candidatos/{r["profile_id"]}/editar?return_to=' in html


def test_each_row_keeps_its_existing_actions_edit_and_delete_alongside_the_new_view_action():
    r = seed(1)[0]
    row = re.search(r"<tr>\s*<td class=\"center hc-select-col\".*?</tr>", client().get("/cchh/candidatos").text, re.S).group(0)
    assert "data-hc-view-profile" in row and "/editar?return_to=" in row and "Dar de baja" in row
    assert row.index("data-hc-view-profile") < row.index("/editar?return_to=")                                      # «Ver ficha» antes que «Editar»; nada existente desapareció
    assert row.count("<td") == 7 and r["profile_id"] in row


def test_selection_markup_carries_only_opaque_ids_no_personal_data_in_data_attributes():
    seed(2)
    html = main_of(client().get("/cchh/candidatos").text)
    for value in re.findall(r'data-hc-select(?:-page)?="([^"]*)"', html):
        assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value), value
    assert "sessionStorage" not in html and "localStorage" not in html                                              # el estado vive en el módulo JS, no en el contenido de la página


def test_full_page_loads_the_selection_script_and_dialog_but_the_swapped_fragment_does_not():
    seed(1)
    full = client().get("/cchh/candidatos").text
    assert 'id="hc-share-dialog"' in full and 'id="hc-share-content"' in full and "human_capital_candidates_selection.js" in full
    assert "busqueda-contact-dialog" in full                                                                          # el mismo diálogo/estilo que «Contactar al candidato»
    frag = client().get("/cchh/candidatos", headers={"HX-Request": "true"}).text
    assert "data-hc-selection-bar" in frag and "data-hc-select-page" in frag and "<script src=" not in frag and "hc-share-dialog" not in frag


def test_pagination_pages_have_disjoint_selectable_ids_and_filters_narrow_them():
    rows = seed(120)
    c = client()
    p1 = page_ids(c.get("/cchh/candidatos", params={"page_size": 50, "sort": "nombre", "dir": "asc"}).text)
    p2 = page_ids(c.get("/cchh/candidatos", params={"page_size": 50, "page": 2, "sort": "nombre", "dir": "asc"}).text)
    p3 = page_ids(c.get("/cchh/candidatos", params={"page_size": 50, "page": 3, "sort": "nombre", "dir": "asc"}).text)
    assert (len(p1), len(p2), len(p3)) == (50, 50, 20) and not (set(p1) & set(p2)) and not (set(p2) & set(p3))
    assert set(p1 + p2 + p3) == {r["profile_id"] for r in rows}                                                     # cada candidato es seleccionable en exactamente una página
    filtered = page_ids(c.get("/cchh/candidatos", params={"q": "Cand007"}).text)
    assert filtered == [next(r["profile_id"] for r in rows if r["first_name"] == "Cand007")]


def test_panel_without_results_still_renders_the_bar_so_a_cross_filter_selection_stays_visible():
    seed(2)
    html = client().get("/cchh/candidatos", params={"q": "zzz-sin-coincidencias"}).text
    assert 'data-empty-kind="no_matches"' in html and "data-hc-selection-bar" in html and page_ids(html) == []


def test_selection_bar_css_hides_for_real_and_does_not_redefine_the_shared_clear_component():
    css = (_ROOT / "webapp" / "static" / "human_capital.css").read_text(encoding="utf-8")
    assert ".hc-selection-bar[hidden] { display: none; }" in css                       # `display:flex` de la barra pisaría el atributo `hidden`
    assert not re.search(r"(^|\n)\s*\.whatsnew-filter__clear\s*(\{|,|:)", css)          # el estilo base vive en tools.css; acá solo el ajuste de alineación dentro de la barra
    assert ".hc-selection-bar__actions .whatsnew-filter__clear" in css
    assert ".hc-selection-bar button:disabled" not in css and ".hc-selection-bar .whatsnew-filter__clear {" not in css
    base = (_ROOT / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    assert ".whatsnew-filter__clear {" in base and ".whatsnew-filter__clear:hover { text-decoration: underline; }" in base


def test_adapter_and_css_support_the_fixed_column_light_dark_and_mobile():
    panel = (_ROOT / "webapp/templates/partials/human_capital_profiles_panel.html").read_text(encoding="utf-8")
    assert 'dataset.columnFixed === "start"' in panel and "fixed.concat(keyed, rest)" in panel
    css = (_ROOT / "webapp/static/human_capital.css").read_text(encoding="utf-8")
    for rule in (".hc-selection-bar", ".hc-select-col", ".hc-readonly-value", ".content-theme-dark .hc-readonly-value", "@media (max-width: 640px)", "tr.is-selected"):
        assert rule in css, rule
    assert "pager" not in css.lower() and "pagination" not in css.lower()                                           # contrato de CSS del módulo


# ═══════════════ 1B · endpoint del diálogo «Compartir perfiles» (enlaces al CV en SharePoint) ═══════════════
def share(c: TestClient, ids, origin=True):
    return c.post("/cchh/candidatos/compartir", data={"profile_ids": ids}, headers=_ORIGIN if origin else {})


def items_of(html: str) -> list[dict]:
    return json.loads(re.search(r'<script type="application/json" data-share-items>(.*?)</script>', html, re.S).group(1))


SP = "https://danwor.sharepoint.com/sites/cchh/Shared%20Documents/"


def test_share_dialog_requires_session_cchh_authorization_and_same_origin():
    p = make_profile()
    anon = TestClient(app).post("/cchh/candidatos/compartir", data={"profile_ids": [p["profile_id"]]}, headers=_ORIGIN, follow_redirects=False)
    assert anon.status_code == 303 and anon.headers["location"].startswith("/platform")
    assert share(client("fuera-de-cchh@danwor.com"), [p["profile_id"]]).status_code == 403
    assert share(client(), [p["profile_id"]], origin=False).status_code == 403
    assert client().post("/cchh/candidatos/compartir", data={"profile_ids": [p["profile_id"]]}, headers={"Origin": "https://evil.example"}).status_code == 403
    r = share(client(), [p["profile_id"]])
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"


def test_single_cv_links_to_the_stored_sharepoint_url_exactly_and_never_to_the_hub():
    a = make_profile(first="Ana", last="Pérez")
    stored = SP + "carpeta-rara/otro-nombre-que-no-sale-del-archivo.pdf?web=1"             # NO derivable del nombre del archivo: si se construyera, no coincidiría
    add_doc(a["profile_id"], "d-ana", "cv.pdf", stored, uploaded_at="2026-10-01T10:00:00+00:00")
    html = share(client(), [a["profile_id"]]).text
    assert items_of(html) == [{"id": a["profile_id"], "name": "Ana Pérez", "docs": [{"id": "d-ana", "label": "cv.pdf · 2026-10-01", "url": stored}]}]
    assert "Compartir 1 perfil" in html and "data-share-copy" in html and "Abrir Outlook" in html
    for leak in (a["email"], "+54 11 5555-0000", "nota privada", "tag-uno", "/editar", "linkedin.example", f"/cchh/candidatos/{a['profile_id']}"):
        assert leak not in html, leak


def test_several_cvs_are_all_listed_newest_first_and_none_is_preselected():
    a, b = make_profile(first="Ana", last="Pérez"), make_profile(first="Luis", last="Gómez")
    add_doc(a["profile_id"], "d-viejo", "cv-2025.pdf", SP + "2025.pdf", uploaded_at="2025-03-01T00:00:00+00:00")
    add_doc(a["profile_id"], "d-nuevo", "cv-2026.pdf", SP + "2026.pdf", uploaded_at="2026-09-01T00:00:00+00:00")
    html = share(client(), [a["profile_id"], b["profile_id"]]).text
    items = items_of(html)
    assert [d["id"] for d in items[0]["docs"]] == ["d-nuevo", "d-viejo"] and items[1]["docs"] == []                  # orden de presentación (no una elección); sin CV = lista vacía
    blob = json.dumps(items)
    for marker in ("selected", "default", "primary", "preferred", "current"):
        assert marker not in blob                                                                                  # el servidor nunca marca un CV como elegido: lo resuelve el usuario


def test_missing_or_invalid_urls_are_never_offered_as_links():
    p = make_profile()
    bad = {"d-none": None, "d-js": "javascript:alert(1)", "d-http": "http://danwor.sharepoint.com/x", "d-evil": "https://evil.example.com/x", "d-cred": "https://u:pw@danwor.sharepoint.com/x",
           "d-space": "https://danwor.sharepoint.com/a b", "d-dot": "https://.sharepoint.com/x", "d-tld": "https://sharepoint.com.evil.io/x", "d-quote": 'https://danwor.sharepoint.com/x"onmouseover="1',
           "d-big": "https://danwor.sharepoint.com/" + "a" * 3000, "d-rel": "/sites/cchh/cv.pdf", "d-empty": ""}
    for number, (doc_id, url) in enumerate(bad.items()):
        add_doc(p["profile_id"], doc_id, f"f{number}.pdf", url, uploaded_at=f"2026-01-{number + 1:02d}T00:00:00+00:00")
    html = share(client(), [p["profile_id"]]).text
    docs = items_of(html)[0]["docs"]
    assert len(docs) == len(bad) and all(d["url"] is None for d in docs)
    for raw in ("javascript:alert", "evil.example.com", "u:pw@", "onmouseover", "sharepoint.com.evil.io"):
        assert raw not in html, raw


def test_a_cv_without_stored_url_is_not_rebuilt_from_its_filename():
    p = make_profile()
    add_doc(p["profile_id"], "d1", "Ana Perez CV.pdf", None)
    html = share(client(), [p["profile_id"]]).text
    assert items_of(html)[0]["docs"] == [{"id": "d1", "label": "Ana Perez CV.pdf · 2026-10-01", "url": None}]
    assert "sharepoint.com" not in html.replace("Los enlaces", "")


def test_share_limit_is_25_and_enforced_by_the_server_before_reading_any_profile(monkeypatch):
    rows = seed(26)
    ok = share(client(), [r["profile_id"] for r in rows[:25]])
    assert ok.status_code == 200 and "data-share-root" in ok.text and "Compartir 25 perfiles" in ok.text
    calls = []
    monkeypatch.setattr(human_capital_service, "get_profile", lambda pid: calls.append(pid))
    over = share(client(), [r["profile_id"] for r in rows])
    assert 'data-share-error="over_limit"' in over.text and "hasta 25 perfiles" in over.text and "data-share-root" not in over.text
    assert calls == []                                                                                               # el límite se valida ANTES de consultar ningún perfil
    assert human_capital_share.MAX_SHARE_PROFILES == 25


def test_share_handles_empty_unavailable_deleted_duplicate_and_malformed_ids_without_echoing_them(monkeypatch):
    live, gone = make_profile(first="Viva"), make_profile(first="Baja")
    add_doc(gone["profile_id"], "d-gone", "baja.pdf", SP + "baja.pdf")
    profile_repository.soft_delete(gone["profile_id"])
    assert 'data-share-error="empty"' in share(client(), []).text
    only_bad = share(client(), ["no-existe-1", gone["profile_id"]]).text
    assert 'data-share-error="none_available"' in only_bad and "Baja" not in only_bad and "baja.pdf" not in only_bad
    read = []
    original = human_capital_share.resolve

    def spy(ids, get_profile, list_documents):
        return original(ids, get_profile, lambda pid: (read.append(pid), list_documents(pid))[1])
    monkeypatch.setattr(human_capital_share, "resolve", spy)
    mixed = share(client(), [live["profile_id"], live["profile_id"], "no-existe-1", gone["profile_id"], "../etc/passwd", "<script>alert(1)</script>", " "]).text
    unavailable = json.loads(re.search(r"data-share-unavailable>(.*?)</script>", mixed, re.S).group(1))
    assert [i["name"] for i in items_of(mixed)] == ["Viva Sintetica"] and set(unavailable) == {"no-existe-1", gone["profile_id"]}
    assert read == [live["profile_id"]]                                                                              # los documentos de un perfil dado de baja ni se consultan
    assert "etc/passwd" not in mixed and "<script>alert(1)" not in mixed and "Baja" not in mixed and "baja.pdf" not in mixed


def test_share_escapes_hostile_names_and_filenames_in_html_and_json():
    p = make_profile(first="O'Hara <b>x</b>", last='"Q"')
    add_doc(p["profile_id"], "d1", '"><img src=x onerror=alert(1)>.pdf', SP + "x.pdf")
    html = share(client(), [p["profile_id"]]).text
    assert "<b>x</b>" not in html and "<img src=x" not in html and "\\u003cb\\u003e" in html and "\\u003cimg" in html


def test_share_reports_an_unreadable_bank_or_document_store_without_leaking_details(monkeypatch):
    p = make_profile()

    def boom(_pid):
        raise human_capital_service.ProfileStorageError("detalle interno de Spaces")
    with monkeypatch.context() as m:
        m.setattr(human_capital_service, "get_profile", boom)
        r = share(client(), [p["profile_id"]])
    assert r.status_code == 200 and 'data-share-error="source_error"' in r.text and "detalle interno" not in r.text

    def boom_docs(_pid):
        raise profile_document_repository.ProfileDocumentStorageError("detalle interno de documentos")
    with monkeypatch.context() as m:
        m.setattr(profile_document_service_module, "list_documents_for_profile", boom_docs)
        r2 = share(client(), [p["profile_id"]])
    assert r2.status_code == 200 and 'data-share-error="source_error"' in r2.text and "detalle interno" not in r2.text


def test_share_never_writes_anything_and_does_not_log_or_audit():
    p = make_profile()
    add_doc(p["profile_id"])
    before = data_digest()
    share(client(), [p["profile_id"]])
    assert data_digest() == before
    src = (_ROOT / "webapp/routers/human_capital.py").read_text(encoding="utf-8")
    body = src[src.index("def human_capital_profiles_share_dialog"):src.index("_export_logger = logging.getLogger")]
    assert "log_activity" not in body and "log_verbose" not in body and "human_capital_export" not in body          # «Compartir» no audita: solo ayuda a redactar (la auditoría durable es de «Descargar CVs»)
    assert "graph" not in body.lower() and "microsoft365" not in body                                                # no toca Graph: solo la metadata ya guardada


def test_dialog_template_has_editable_subject_intro_outro_a_safe_preview_and_honest_outlook_note():
    p = make_profile()
    html = share(client(), [p["profile_id"]]).text
    for hook in ("data-share-subject", "data-share-intro", "data-share-outro", "data-share-list", "data-share-pending", 'data-share-copy="mensaje"', "data-share-open"):
        assert hook in html, hook
    assert "data-share-message" not in html and "<textarea id=\"hc-share-mensaje\"" not in html                    # ya no hay un textarea con el cuerpo en texto plano
    assert "solo admite texto plano con las URLs completas" in html and "mailto:" in html                          # límite real del mailto, dicho sin rodeos
    assert "conserva los nombres como enlaces" in html


# ═══════════════ servicio puro ═══════════════
def test_share_service_rules_are_pure_and_deterministic():
    ids, invalid = human_capital_share.normalize_ids(["a1", "a1", " b2 ", "bad id", "", None, "x" * 65, "c3"])
    assert ids == ["a1", "b2", "c3"] and invalid == 4
    docs = {"a1": [{"document_id": "z", "filename": "a.pdf", "uploaded_at": "2026-02-02T00:00:00+00:00", "web_url": SP + "a.pdf"}]}
    sel = human_capital_share.resolve(["a1", "b2"], lambda pid: {"first_name": "Ana", "last_name": ""} if pid == "a1" else {"deleted_at": "x"}, lambda pid: docs.get(pid, []))
    assert sel.ok and [i["name"] for i in sel.items] == ["Ana"] and sel.unavailable == ["b2"] and sel.items[0]["docs"][0]["url"] == SP + "a.pdf"
    assert human_capital_share.full_name({}) == "Candidato sin nombre"
    assert not hasattr(human_capital_share, "profile_path")                                                          # el enlace ya no apunta a la ficha del Hub


def test_safe_cv_url_never_accepts_what_the_historic_original_cv_rule_rejects():
    from webapp.services import cv_history_review
    samples = [SP + "a.pdf", "https://danwor-my.sharepoint.com/personal/x/y.pdf", "https://DANWOR.SHAREPOINT.COM/x", " https://danwor.sharepoint.com/x ", "http://danwor.sharepoint.com/x",
               "https://evil.com/x", "https://u@danwor.sharepoint.com/x", "https://sharepoint.com/x", "https://.sharepoint.com/x", "https://danwor.sharepoint.com.evil.io/x", "javascript:alert(1)", None, 5, ""]
    for value in samples:
        mine, theirs = human_capital_share.safe_cv_url(value), cv_history_review._safe_web_url(value)
        assert mine is None or mine == theirs, value                                                               # nunca más permisiva que la regla ya vigente


# ═══════════════ fuera de alcance: la Fase 2 NO existe todavía ═══════════════
def test_download_surface_is_exactly_the_two_phase_two_routes_and_there_is_still_no_select_all_or_batching():
    surface = json.loads((_ROOT / "tests" / "route_surface.json").read_text(encoding="utf-8"))               # las rutas de CCHH viven en un router anidado: se usa el contrato congelado (y su test lo mantiene al día)
    paths = sorted(entry for entry in surface if " /cchh/candidatos" in entry and re.search(r"zip|descarg|download|export|todos|select-all|lote|tanda|batch", entry, re.I))
    assert paths == ["POST /cchh/candidatos/descargar", "POST /cchh/candidatos/descargar/resumen"]            # sin rutas de ZIP por tandas, ni de «todos los resultados del filtro»
    sel_js = (_ROOT / "webapp/static/human_capital_candidates_selection.js").read_text(encoding="utf-8")
    assert not re.search(r"seleccionar todos los resultados|select-all|exportar", client().get("/cchh/candidatos").text + sel_js, re.I)


def test_phase_two_requirements_are_documented():
    doc = (_ROOT / "docs" / "CCHH_EXPORTACION_CVS_FASE2_REQUISITOS.md").read_text(encoding="utf-8").lower()
    for requirement in ("auditoría durable", "identificadores internos", "log_activity", "errores parciales", "límites", "fail-closed"):
        assert requirement in doc, requirement


# ═══════════════ módulo JS ═══════════════
@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_candidates_selection_module_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"Human Capital candidates selection failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL HUMAN CAPITAL CANDIDATES SELECTION INVARIANTS PASSED" in result.stdout
