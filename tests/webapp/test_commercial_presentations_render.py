"""
test_commercial_presentations_render.py — Presentaciones V1: SEGURIDAD DE SALIDA, paridad del seed, independencia de las copias, compatibilidad del template y assets compartidos.

Garantías (contrato V1):
  * ningún campo editable se convierte en HTML/JS: todos son texto plano y se emiten SOLO como texto de elemento (autoescape), nunca en atributo/URL/<script>/<style>;
  * las instancias no duplican CSS, JS ni assets (el template los sirve como archivos compartidos);
  * la evolución del template no rompe en silencio las instancias existentes (fixture dorado, candado de claves/límites, versión mayor, template desconocido).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
from bs4 import BeautifulSoup

from tests.webapp.test_commercial_presentations_access import (  # noqa: F401  (fixtures y helpers compartidos)
    A, APP_OWNER, B, HUB_ONLY, ORIGIN, OWNER_C, SEED_ID, SEED_YAML, _copy, _save, world,
)
from webapp.services import commercial_presentations as cp
from webapp.templating import templates

REPO = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO / "webapp" / "templates" / "presentations" / "vanture-bdc.html"
ASSETS = REPO / "content_packs" / "datasphere_core" / "assets" / "presentations" / "vanture-bdc"
FIXTURE = REPO / "tests" / "fixtures" / "presentations" / "vanture_bdc_v1_instance.json"
SPEC = cp.TEMPLATES["vanture-bdc"]
SEED = cp._seed_raw("vanture-bdc")["content"]


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _page(world, pid=SEED_ID, email=A, embed=False) -> str:
    r = world.client(email).get(f"/presentaciones/{pid}" + ("?embed=1" if embed else ""))
    assert r.status_code == 200, r.text[:200]
    return r.text


# ── Paridad: el seed renderizado conserva el texto del original ─────────────────────────────────────────────────────────

def test_the_seed_renders_every_original_text_inside_the_immersive_document(world):
    text = _norm(_soup(_page(world)).get_text(" "))
    for key, value in SEED.items():
        assert _norm(value) in text, key
    for fixed in ("SAP Business Data Cloud", "Qué es SAP Business Data Cloud", "Knowledge Core", "Socios estratégicos", "CFO", "CHRO"):
        assert fixed in text, fixed                                                              # el resto del texto lo gobierna el template, intacto


def test_the_document_is_standalone_with_external_shared_assets_and_no_base64(world):
    html = _page(world)
    assert "base64" not in html
    soup = _soup(html)
    sheets = [urlparse(l["href"]).path for l in soup.find_all("link", rel="stylesheet")]
    assert sheets[-1] == "/pack-assets/datasphere_core/presentations/vanture-bdc/vanture.css"
    assert sheets[-5:-1] == ["/static/app.css", "/static/content.css", "/static/layout.css", "/static/tools.css"], "las hojas del Hub, en el MISMO orden que base.html, y la de la presentación al final (gana la cascada)"
    assert soup.find("script", src=re.compile("vanture.js"))["src"].startswith("/pack-assets/datasphere_core/presentations/vanture-bdc/vanture.js?v=")
    assert [urlparse(s["src"]).path for s in soup.find_all("script", src=True)] == ["/static/hub_theme.js", "/pack-assets/datasphere_core/presentations/vanture-bdc/vanture.js"]
    assert not [s for s in soup.find_all("script") if not s.get("src")], "el template no trae JS en línea"
    assert not soup.find_all("style"), "el template no trae <style> en línea (el CSS es un archivo compartido)"
    assert len(html.encode()) < 120_000
    assert soup.find("meta", attrs={"name": "robots"})["content"] == "noindex,nofollow"


def test_embed_hides_the_cobranding_bar_and_the_standalone_keeps_it(world):
    assert "Danwor — Data &amp; AI for Business" in _page(world)
    assert "Danwor — Data &amp; AI for Business" not in _page(world, embed=True)


def test_the_vanture_marquee_logo_keeps_its_original_alt(world):
    assert 'alt="Vanturess"' in _page(world)                                                    # el logo dice «Vanture eSS»: otra marca, no un typo


# ── Seguridad de salida ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_every_editable_field_is_emitted_only_as_element_text_never_in_attributes_scripts_or_styles(world):
    pid = _copy(world, A)
    sentinels = {k: f"QZ{i:02d}QZ" for i, k in enumerate(SPEC.field_keys)}
    marked = {k: f"{v}<i>\"'&" for k, v in sentinels.items()}                                  # sufijo corto: respeta el límite de los campos más cortos (20)
    assert _save(world, A, pid, marked, 1).status_code == 200
    seed_soup, soup = _soup(_page(world)), _soup(_page(world, pid))
    source = TEMPLATE_PATH.read_text(encoding="utf-8")

    for key, token in sentinels.items():
        holders = [t for t in soup.find_all(string=re.compile(token))]
        assert len(holders) == 1 + source.count(f"m('{key}')"), key                              # el campo + sus espejos declarados (etiqueta de sección en el riel, título del pilar en su detalle, etiqueta superior en el encabezado)
        assert holders[0].parent.name not in {"script", "style", "title"}, key
        for tag in soup.find_all(True):
            assert not any(token in str(v) for v in tag.attrs.values()), (key, tag.name)
    # el HTML inyectado quedó como TEXTO: no aparecen tags nuevos ni handlers
    assert [t.name for t in soup.find_all(True)] == [t.name for t in seed_soup.find_all(True)]
    assert not soup.find_all(attrs={"onmouseover": True})
    assert len(soup.find_all("script")) == len(seed_soup.find_all("script"))


@pytest.mark.parametrize("payload", [
    "<script>alert(1)</script>", "</style><script>alert(1)</script>", "\" onmouseover=\"alert(1)", "javascript:alert(1)", "{{ 7*7 }}", "{% raw %}", "&lt;b&gt;", "<img src=x onerror=alert(1)>",
])
def test_hostile_payloads_render_as_inert_text(world, payload):
    pid = _copy(world, A)
    assert _save(world, A, pid, {"hero_lead": payload}, 1).status_code == 200
    html, seed_html = _page(world, pid), _page(world)
    soup = _soup(html)
    assert len(soup.find_all("script")) == len(_soup(seed_html).find_all("script"))
    assert not soup.find_all(attrs={"onerror": True}) and not soup.find_all(attrs={"onmouseover": True})
    assert "49" not in _norm(soup.get_text(" ")) or payload != "{{ 7*7 }}"                    # el contenido nunca se interpreta como template
    assert payload in soup.get_text(" ") or payload.replace("&lt;", "<").replace("&gt;", ">") in soup.get_text(" ")


def test_jinja_autoescape_is_active_for_the_hub_templates():
    assert templates.env.from_string("{{ x }}").render(x="<b>&") == "&lt;b&gt;&amp;"


def test_the_template_source_has_no_unsafe_constructs_and_uses_exactly_the_spec_keys():
    source = TEMPLATE_PATH.read_text(encoding="utf-8")
    for forbidden in ("|safe", "| safe", "Markup", "autoescape", "{% raw", "|tojson", "{% include", "{% extends"):
        assert forbidden not in source, forbidden
    assert re.findall(r"\{%-?\s*(?:from|import)[^%]*-?%\}", source) == ['{%- from "partials/theme_icons.html" import theme_icon -%}'], "el único import permitido es la macro compartida de íconos de tema"
    used = re.findall(r"\{\{\s*f\('([a-z0-9_]+)'\)\s*\}\}", source) + re.findall(r"\{\{\s*nextlink\('s\d+','s\d+','([a-z0-9_]+)'\)\s*\}\}", source)
    assert sorted(used) == sorted(set(SPEC.field_keys)) and len(used) == len(set(used))         # ni claves de más ni campos sin usar, cada uno exactamente una vez (vía la macro `f`, o `nextlink`, que la usa)
    assert not re.search(r"\bc\.[a-z0-9_]+", source), "el contenido solo se lee a través de la macro `f` (que escapa y, en edición, envuelve el leaf)"
    # Los macros de COMPOSICIÓN gobernada (componentes, regiones, herramientas de edición) leen solo parámetros institucionales (`addons`, `regions`, `library`): nunca contenido editable (`c`). Se verifica aparte.
    governed = ("layer_stack", "flow", "marquee", "component", "region_tools", "addon_dialog", "region", "qr_tools", "slide_tools")
    for name in governed:
        body = re.search(r"\{%-?\s*macro " + name + r"\(.*?\{%-?\s*endmacro\s*-?%\}", source, re.S).group(0)
        assert not re.search(r"\bc\[|\bc\.|\bf\(|\bm\(", body), f"el macro {name} no puede leer contenido editable"
        source = source.replace(body, "")
    expressions = {e.strip() for e in re.findall(r"\{\{\s*([^}]+?)\s*\}\}", source)}
    mirrors = set(re.findall(r"\{\{\s*m\('([a-z0-9_]+)'\)\s*\}\}", source))
    assert mirrors <= set(SPEC.field_keys), "un espejo solo puede reflejar un campo editable de la spec (nunca contenido propio)"
    non_content = expressions - {f"f('{k}')" for k in SPEC.field_keys} - {f"m('{k}')" for k in mirrors}
    slide = r"s(?:[2-9]|1[0-2])"                                                                 # composición por slide: solo ids de slide quitable, nunca contenido
    structural = [re.compile(p) for p in (rf"' data-cp-removed' if '{slide}' in removed_ids else ''", rf"' hidden' if '{slide}' in removed_ids else ''", rf"kno\('{slide}'\)",
                                          rf"slide_tools\('{slide}'\) if edit_mode else ''", rf"nextlink\('{slide}','{slide}','[a-z0-9_]+'\)",
                                          r"region\('s\d+\.(?:body|bleed)'(?:, gap=True|, style='[^']*')?\)")]
    non_content = {e for e in non_content if not any(p.fullmatch(e) for p in structural)}
    assert non_content == {"key", "c[key]", "sid", "orig", "t", "nx or orig", "f(key)", "nav_of[nx]", "first_slide or 's2'", "qr_url", "o.ref", "addon_dialog()", "component(o.component, o.variant, o.params)", "component(preview_component.component, preview_component.variant, preview_component.params)", "qr_tools() if edit_mode else ''", "' data-cp-qr' if edit_mode else ''", '" data-cp-edit" if edit_mode else ""', "css_url", "js_url", "asset_base", "institutional_logo_url", "partner_logo_url", "partner_logo_alt", "theme", "hub_fonts_url", "static_version", '" data-cp-edit" if edit_mode else ""', "css_url", "js_url", "asset_base", "institutional_logo_url", "partner_logo_url", "partner_logo_alt", "theme", "hub_fonts_url", "static_version",
                           "url_for('static', path='app.css')", "url_for('static', path='content.css')", "url_for('static', path='layout.css')", "url_for('static', path='tools.css')",
                           "url_for('static', path='hub_theme.js')", 'theme_icon("light", \'style="display:none"\' if theme != "light" else "")', 'theme_icon("dark", \'style="display:none"\' if theme != "dark" else "")'}, non_content


def test_there_are_no_editable_attribute_or_url_contexts_in_v1():
    source = TEMPLATE_PATH.read_text(encoding="utf-8")
    for key in SPEC.field_keys:
        for m in re.finditer(r"\{\{\s*f\('" + key + r"'\)\s*\}\}", source):
            before = source[:m.start()]
            assert before.rfind(">") > before.rfind("<"), key                                   # el campo cae FUERA de cualquier tag (texto de elemento)


# ── Independencia: las instancias no duplican CSS/JS/assets ───────────────────────────────────────────────────────────────

def test_a_copy_shares_css_js_and_assets_with_the_seed_and_stays_small(world):
    pid = _copy(world, A)
    assert _save(world, A, pid, {"hero_lead": "Texto propio de la copia"}, 1).status_code == 200
    seed_soup, copy_soup = _soup(_page(world)), _soup(_page(world, pid))

    def urls(soup):
        return sorted({v for t in soup.find_all(True) for k, v in t.attrs.items() if k in {"href", "src"} and str(v).startswith("/pack-assets/")})

    assert urls(seed_soup) == urls(copy_soup) and len(urls(seed_soup)) > 40
    assert [s["src"] for s in seed_soup.find_all("script", src=True)] == [s["src"] for s in copy_soup.find_all("script", src=True)]
    raw = (world.directory / f"{pid}.json").read_bytes()
    assert len(raw) < 20_000                                                                     # la copia guarda TODO el contenido editable (hoy ~200 textos), no markup ni assets
    stored = json.loads(raw)
    assert set(stored["content"]) == set(SPEC.field_keys)
    assert not any("/pack-assets" in v or "<" in v for v in stored["content"].values())        # la instancia no referencia assets ni guarda markup
    assert set(stored) == {"schema_version", "presentation_id", "template_id", "template_version", "title", "revision", "created_by_id", "created_by_label", "owner_id",
                           "copied_from", "created_at", "updated_at", "updated_by_id", "updated_by_label", "content"}


def test_editing_a_copy_leaves_the_seed_render_byte_identical(world):
    before = _page(world)
    pid = _copy(world, A)
    for i in range(3):
        assert _save(world, A, pid, {"hero_lead": f"v{i}", "cta_lead": f"c{i}"}, i + 1).status_code == 200
    assert _page(world) == before
    assert "v2" in _page(world, pid)


# ── Assets compartidos ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_asset_directory_contains_only_brand_files_and_everything_referenced_exists():
    files = [p for p in ASSETS.rglob("*") if p.is_file()]
    assert {p.suffix for p in files} <= {".css", ".js", ".png", ".jpg"}, "solo CSS, JS e imágenes de marca bajo /pack-assets: las fuentes ya no son propias (salen del Hub)"
    assert len([p for p in files if p.parent.name == "img"]) == 45
    assert not (ASSETS / "fonts").exists(), "sin fuentes propias: Inter y JetBrains Mono son las institucionales del Hub"
    assert (ASSETS / "vanture.css").is_file() and (ASSETS / "vanture.js").is_file()
    css = (ASSETS / "vanture.css").read_text(encoding="utf-8")
    assert "base64" not in css and "@font-face" not in css and "url(fonts/" not in css
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    for ref in re.findall(r"\{\{ asset_base \}\}/(img/[0-9a-f]+\.(?:png|jpg))", template):
        assert (ASSETS / ref).is_file(), ref


def test_the_shared_assets_are_served_under_the_hub_read_contract_without_commercial(world):
    base = "/pack-assets/datasphere_core/presentations/vanture-bdc"
    for name in ("vanture.css", "vanture.js"):
        r = world.client(HUB_ONLY).get(f"{base}/{name}")
        assert r.status_code == 200 and len(r.content) > 1000, name
    img = sorted((ASSETS / "img").iterdir())[0].name
    assert world.client(HUB_ONLY).get(f"{base}/img/{img}").status_code == 200
    anonymous = world.client(None).get(f"{base}/vanture.css", follow_redirects=False)
    assert anonymous.status_code in (303, 307) and "var(--" not in anonymous.text               # sin sesión no hay lectura: redirige a login


def test_the_document_urls_all_resolve_for_a_hub_user(world):
    soup = _soup(_page(world, email=HUB_ONLY))
    for tag in soup.find_all(["link", "script", "img"]):
        url = tag.get("href") or tag.get("src")
        if url and url.startswith("/pack-assets/"):
            assert world.client(HUB_ONLY).get(url).status_code == 200, url


# ── Compatibilidad del template con instancias existentes ──────────────────────────────────────────────────────────────────

def _write_instance(world, payload: dict) -> str:
    world.directory.mkdir(parents=True, exist_ok=True)
    (world.directory / f"{payload['presentation_id']}.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload["presentation_id"]


def test_the_golden_v1_instance_validates_renders_and_is_listed_forever(world):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1 and payload["template_version"] == 1 and set(payload["content"]) == set(PINNED_V1), "la fixture dorada es una instancia creada con los 20 campos originales"
    pid = _write_instance(world, payload)
    view = cp.get(pid)
    assert view["content"] == {**SEED, **payload["content"]} and set(view["content"]) == set(SPEC.field_keys), "los campos agregados después caen al valor del seed; los propios se conservan"
    assert view["owner_id"] == payload["owner_id"]
    html = _page(world, pid)
    assert _norm(payload["content"]["hero_lead"]) in _norm(_soup(html).get_text(" "))
    assert pid in world.client(A).get("/comercial/presentaciones").text


def test_a_field_added_after_the_instance_was_created_falls_back_to_the_seed_default(world):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    removed = ["cta_lead", "alliance_4_body", "recognition_tail"]
    for k in removed:
        payload["content"].pop(k)
    pid = _write_instance(world, payload)
    text = _norm(_soup(_page(world, pid)).get_text(" "))
    for k in removed:
        assert _norm(SEED[k]) in text, k
    assert _save(world, A, pid, {"hero_lead": "x"}, 1).status_code == 403                      # (la fixture es de otro responsable)


def test_unknown_content_keys_in_an_instance_are_never_rendered(world):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["content"]["evil"] = "<script>alert(1)</script>"
    html = _page(world, _write_instance(world, payload))
    assert "alert(1)" not in html and "evil" not in html


def test_an_instance_of_a_newer_template_version_is_a_clear_409_and_marked_unavailable_in_the_catalog(world):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["template_version"] = SPEC.version + 1
    pid = _write_instance(world, payload)
    assert world.client(A).get(f"/presentaciones/{pid}").status_code == 409
    assert world.client(A).get(f"/comercial/presentaciones/{pid}").status_code == 409
    catalog = world.client(A).get("/comercial/presentaciones")
    assert catalog.status_code == 200 and "Template no disponible" in catalog.text


def test_an_instance_of_an_unknown_template_is_404_and_does_not_break_the_catalog(world):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["template_id"] = "template-retirado"
    pid = _write_instance(world, payload)
    assert world.client(A).get(f"/presentaciones/{pid}").status_code == 404
    assert world.client(A).get("/comercial/presentaciones").status_code == 200


def test_an_older_template_version_is_upgraded_in_memory_and_persisted_only_on_the_next_save(world, monkeypatch):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["owner_id"] = A
    pid = _write_instance(world, payload)
    upgraded = dict(SPEC.__dict__, version=2, upgrade=lambda content, frm: {**content, "hero_lead": content["hero_lead"] + " [v2]"})
    monkeypatch.setitem(cp.TEMPLATES, "vanture-bdc", cp.TemplateSpec(**upgraded))
    assert cp.get(pid)["content"]["hero_lead"].endswith("[v2]") and cp.get(pid)["template_version"] == 2
    assert json.loads((world.directory / f"{pid}.json").read_text(encoding="utf-8"))["template_version"] == 1      # en memoria: el disco no cambió
    assert _save(world, A, pid, {"cta_lead": "nuevo"}, payload["revision"]).status_code == 200
    on_disk = json.loads((world.directory / f"{pid}.json").read_text(encoding="utf-8"))
    assert on_disk["template_version"] == 2 and on_disk["content"]["hero_lead"].endswith("[v2]")


# ── Candados de evolución del template (fallan si alguien rompe la compatibilidad sin subir la versión) ───────────────────

PINNED_V1 = {
    "hero_title_prefix": 120, "hero_title_accent": 40, "hero_lead": 400, "s2_stat_value": 20, "s2_stat_label": 200,
    "cta_title_prefix": 120, "cta_title_accent": 60, "cta_lead": 300, "cta_qr_caption": 120,
    "alliance_1_title": 80, "alliance_1_body": 300, "alliance_2_title": 80, "alliance_2_body": 300,
    "alliance_3_title": 80, "alliance_3_body": 300, "alliance_4_title": 80, "alliance_4_body": 300,
    "recognition_intro": 300, "recognition_highlight": 80, "recognition_tail": 400,
}


def test_the_v1_field_spec_can_only_grow_within_the_same_template_version():
    if SPEC.version == 1:
        for key, limit in PINNED_V1.items():
            field = SPEC.field(key)
            assert field is not None, f"{key} desapareció sin subir template_version"
            assert field.max_len >= limit, f"{key} redujo su límite sin subir template_version"
    else:
        assert SPEC.upgrade.__name__ != "<lambda>", "una versión nueva del template exige una función upgrade() explícita"


def test_every_spec_field_has_a_valid_value_in_the_seed_and_the_seed_respects_its_own_limits():
    assert set(SEED) == set(SPEC.field_keys)
    for field in SPEC.fields:
        assert cp.validate_fields("vanture-bdc", {field.key: SEED[field.key]}) == {field.key: SEED[field.key]}, field.key


def test_the_golden_fixture_covers_every_spec_field_with_the_v1_limits():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for key, limit in PINNED_V1.items():
        assert 0 < len(payload["content"][key]) <= limit


# ── Homologación institucional con el Hub (diferencias APROBADAS respecto del HTML original) ─────────────────────────────────

ACADEMY_LOGO = REPO / "content_packs" / "datasphere_core" / "assets" / "academy" / "assets" / "img" / "danwor-logo.png"
ACADEMY_LOGO_URL = "/pack-assets/datasphere_core/academy/assets/img/danwor-logo.png"


def test_the_danwor_logo_is_the_same_asset_danwor_academy_shows_fullscreen_in_both_of_its_uses(world):
    soup = _soup(_page(world))
    danwor = [i["src"] for i in soup.find_all("img") if i["src"] == ACADEMY_LOGO_URL]
    assert danwor == [ACADEMY_LOGO_URL, ACADEMY_LOGO_URL]                                         # barra superior + pie de página
    assert ACADEMY_LOGO.is_file()
    assert "615c473fbf" not in TEMPLATE_PATH.read_text(encoding="utf-8") and not (ASSETS / "img" / "615c473fbf.png").exists(), \
        "la variante de wordmark oscuro ya no se usa ni se duplica"
    assert world.client(HUB_ONLY).get(ACADEMY_LOGO_URL).status_code == 200                       # accesible con el contrato de lectura del Hub


def test_the_vanture_cobranding_next_to_the_danwor_logo_is_preserved(world):
    html = _page(world)
    assert "Vanture — a Knowmad Mood company" in html
    header = _soup(html).find("div", style=re.compile("z-index:50"))
    assert [i["alt"] for i in header.find_all("img")] == ["Danwor — Data & AI for Business", "Vanture — a Knowmad Mood company"]


def test_the_narrative_section_numbers_are_the_academy_kicker_above_their_titles_and_keep_the_sequence(world):
    soup = _soup(_page(world))
    rail = {a["href"][1:]: a.get_text(strip=True) for a in soup.select("nav.rail a")}
    expected = {"s2": "01", "s3": "02", "s4": "03", "s5": "04", "s6": "05", "s7": "06", "s8": "07", "s9": "08", "s11": "10", "s12": "11"}
    for section, number in expected.items():
        h2 = soup.find(id=section).find("h2")
        row, kick = h2.parent, h2.find_previous_sibling("span")
        assert kick.get_text(strip=True) == f"{number} / 11 · {rail[section]}", section              # «09 / 10 · Donde todo esto converge» de Academy: número / total · sección
        assert kick["class"] == ["topic-kicker"] and not kick.has_attr("style"), section             # tratamiento institucional compartido: ni tipografía ni color propios
        style = re.sub(r"\s+", "", row["style"])
        assert "flex-direction:column" in style and "align-items:flex-start" in style, section     # el kicker queda ENCIMA del título
        assert "align-items:baseline" not in style and "animation:wipeIn" in style, section        # la animación de entrada se conserva
    assert "align-items:baseline;gap:20px" not in _page(world)
    assert not soup.select(".topic-num"), "el documento ya no usa la numeración mínima (.topic-num): usa el kicker de Academy"


def test_diagram_and_card_indicators_are_kept_and_the_s10_kicker_adopts_the_shared_treatment(world):
    soup = _soup(_page(world))
    s10 = soup.find(id="s10").find("h2").parent.find(class_="topic-kicker")
    assert s10.get_text(strip=True) == "09 / 11 · Conclusión y demo" and not s10.has_attr("style")
    assert [d.get_text(strip=True) for d in soup.find(id="s2").find_all("div", string=re.compile(r"^0[1-3]$"))] == ["01", "02", "03"]
    assert [d.get_text(strip=True) for d in soup.find(id="s8").find_all("div", string=re.compile(r"^0[1-4]$"))] == ["01", "02", "03", "04"]
    assert len(soup.select(".topic-kicker")) == 11, "10 encabezados narrativos + s10; los números de tarjetas, pasos y pilares NO se tocan"


def test_the_blocks_are_separate_bordered_dw_cards_with_the_academy_hover_and_no_forced_glow(world):
    soup = _soup(_page(world))
    cards = soup.select(".dw-card")
    assert len(cards) == 24 and not soup.select("[data-tilt]")
    for c in cards:
        assert "box-shadow" not in c.get("style", "") and "perspective" not in c.get("style", "")
        assert "background:var(--content-surface)" not in c.get("style", "").replace(" ", "")             # el fondo lo da la tarjeta compartida
    grids = [d for d in soup.find_all("div", style=True) if "grid-template-columns:repeat(auto-fit" in d["style"] and d.select_one(".dw-card")]
    assert len(grids) >= 5 and all("gap:1px" not in g["style"] and not re.search(r"(?<!-)background:", g["style"]) for g in grids), "las tarjetas ya no son un bloque unido por una línea de 1px"
    css, js = VANTURE_CSS.read_text(encoding="utf-8"), (ASSETS / "vanture.js").read_text(encoding="utf-8")
    assert "data-tilt" not in css and "perspective(" not in css and "tilt" not in js.lower()
    shared = (STATIC / "content.css").read_text(encoding="utf-8")
    assert ".dw-card, .finale-card { background: var(--bg-card, var(--content-tile)); border: 1px solid var(--line, var(--content-line)); border-radius: 14px; }" in shared
    hover = re.search(r"\.dw-card:hover, \.finale-card-link:hover \{([^}]*)\}", shared).group(1)
    assert "border-color" in hover and "translateY(-2px)" in hover and "box-shadow" not in hover, "el hover de Academy: borde de acento y 2px hacia arriba"
    academy = (REPO / "content_packs" / "datasphere_core" / "assets" / "academy" / "dw-academy.css").read_text(encoding="utf-8")
    assert "finale-card-link:hover" not in academy and ".topic-kicker{" not in academy, "una sola definición (content.css)"


def test_the_title_accent_is_the_plain_hub_gold_and_the_entry_animations_do_not_keep_a_composited_layer(world):
    html = _page(world)
    css = VANTURE_CSS.read_text(encoding="utf-8")
    assert "shimmerText" not in css and "background-clip" not in css and "text-fill-color" not in css, "«datos» en oro pleno, sin degradé ni brillo"
    assert re.search(r'<span style="color:var\(--gold\)">datos</span>', html), "el acento del título es var(--gold) (#E7B008, el del helper {dw-gold})"
    # un `animation-fill-mode: both` deja la capa compuesta con transform identidad y el texto se rasteriza borroso; `backwards` devuelve el estilo base al terminar
    assert not re.search(r"animation:(?:riseIn|fadeIn|wipeIn) [^;\"]*\bboth\b", html), "las animaciones de entrada no retienen el relleno final"


def test_the_shared_url_document_carries_no_commercial_navigation_and_no_back_button(world):
    html = _page(world, email=HUB_ONLY)
    for forbidden in ("catalog-back", "module-nav", "/comercial", "commercial_nav", "Volver"):
        assert forbidden not in html, forbidden


# ── Pantallas del Hub: Volver, aviso informativo y confirmación transitoria ─────────────────────────────────────────────

def _hub_page(world, path):
    r = world.client(A).get(path)
    assert r.status_code == 200, path
    return _soup(r.text)


def test_the_detail_and_the_editor_use_the_shared_back_primitive_as_first_child_of_main(world):
    pid = _copy(world, A)
    detail = _hub_page(world, f"/comercial/presentaciones/{pid}")
    back = detail.find("main", id="content").find(True, recursive=False)
    assert back.name == "a" and back["class"] == ["catalog-back"] and back["href"] == "/comercial/presentaciones"
    assert back.get_text(strip=True) == "← Volver a Presentaciones"
    edit = _hub_page(world, f"/comercial/presentaciones/{pid}/editar")
    back = edit.find("main", id="content").find(True, recursive=False)
    assert back["class"] == ["catalog-back"] and back["href"] == f"/comercial/presentaciones/{pid}"
    assert not [a for a in edit.find_all("a") if a.get_text(strip=True) == "Volver"], "sin un segundo «Volver» redundante"
    assert not detail.select("p.eyebrow a") and not edit.select("p.eyebrow a"), "ningún eyebrow dorado hecho enlace"


def test_the_link_visibility_notices_use_the_hub_note_callout_with_compact_scale_and_no_inline_styles(world):
    pid = _copy(world, A)
    detail = _hub_page(world, f"/comercial/presentaciones/{pid}")
    note = detail.select_one(".callout.callout--note.cp-notice")
    assert note and not note.has_attr("style") and not note.find(style=True)
    assert "URL" in note.get_text() and "Comercial" in note.get_text()
    edit = _hub_page(world, f"/comercial/presentaciones/{pid}/editar")
    warn = edit.select_one(".callout.callout--punch.cp-notice")
    assert warn and not warn.find(style=True) and "cualquier usuario autorizado del Hub" in warn.get_text()
    css = (REPO / "webapp" / "static" / "commercial.css").read_text(encoding="utf-8")
    assert ".cp-notice .callout__body" in css and ".cp-toast .callout__body" in css


@pytest.mark.parametrize("path", ["/comercial/presentaciones", "/comercial/presentaciones/{pid}", "/comercial/presentaciones/{pid}/editar"])
def test_every_hub_screen_hosts_the_transient_status_in_the_shared_auto_dismiss_container(world, path):
    pid = _copy(world, A)
    page = _hub_page(world, path.format(pid=pid))
    host = page.select_one("[data-cp-status]")
    assert host and "cp-toast" in host["class"] and host["data-dw-auto-dismiss"] == "6000" and host["aria-live"] == "polite"
    assert not host.get_text(strip=True), "nace vacío: no hay un mensaje persistente renderizado"
    assert len(page.select("[data-cp-status]")) == 1
    assert not page.select("aside.callout--success[data-cp-status], [data-cp-status][hidden]")


def test_the_status_script_reuses_the_hub_primitive_builds_the_callout_with_text_nodes_and_never_stacks():
    source = (REPO / "webapp" / "static" / "commercial_presentations.js").read_text(encoding="utf-8")
    js = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)                                    # solo código, sin comentarios
    assert "replaceChildren(box)" in js and "textContent" in js
    assert "innerHTML" not in js
    assert "setTimeout" not in js, "el temporizador es el del primitivo Hub-wide (`data-dw-auto-dismiss`), no uno propio"
    app = (REPO / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    assert "function initAutoDismissFeedback" in app and "[data-dw-auto-dismiss]" in app


# ── Homologación REAL con el design system del Hub: nada institucional hardcodeado, todo consume la fuente compartida ──────────────────────────

VANTURE_CSS = ASSETS / "vanture.css"
STATIC = REPO / "webapp" / "static"
ACADEMY_CSS = REPO / "content_packs" / "datasphere_core" / "assets" / "academy" / "dw-academy.css"


def _doc_source() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8") + VANTURE_CSS.read_text(encoding="utf-8")


def test_no_institutional_value_is_hardcoded_in_the_presentation_only_semantic_and_decorative_ones_remain():
    src = _doc_source()
    for institutional in ("Barlow", "IBM Plex", "#EBBF08", "#0A0A0A", "rgba(235,191,8", "rgba(255,255,255", "#A3A3A6", "color:#fff"):
        assert institutional.lower() not in src.lower().replace(" ", "") if institutional.startswith(("rgba", "color")) else institutional.lower() not in src.lower(), institutional
    colors = {c.upper() for c in re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", src)}
    assert colors == {"#000", "#FFF", "#F4F4F4", "#5BB5F0", "#0D2447"}, colors
    # clasificación: #000 = tinta sobre acento dorado y máscaras (semántico/técnico) · #FFF/#F4F4F4 = tiles blancos de logos y QR (legibilidad de la marca) · (el degradé del título se retiró) brillo del
    # degradé del título (decorativo) · #5BB5F0 = acento de plataformas · #0D2447 = fondo del tile de un logo (semánticos)
    assert len(re.findall(r"font-family:", src)) == len(re.findall(r"font-family:var\(--(?:sans|mono)\)", src)), "toda familia tipográfica sale de los tokens del Hub"


def test_the_presentation_consumes_the_hub_tokens_for_family_ink_surface_and_accent():
    src = _doc_source()
    for token in ("var(--sans)", "var(--mono)", "var(--gold)", "var(--content-ink)", "var(--content-ink-soft)", "var(--content-surface)", "var(--bg)", "var(--ink)"):
        assert token in src, token
    css = VANTURE_CSS.read_text(encoding="utf-8")
    assert re.search(r"body\s*\{[^}]*background:var\(--content-canvas\)[^}]*color:var\(--content-ink\)[^}]*font-family:var\(--sans\)", css)
    assert re.search(r'html\.content-theme-light body, html\[data-dw-theme="light"\] body \{ background:var\(--content-surface\); \}', css), "en claro, la superficie blanca"
    assert not re.search(r"(?m)^\s*a(?::hover)?\s*\{", css), "los enlaces no redefinen color (lo gobierna el Hub)"


def test_entry_and_section_jumps_use_the_shared_scroll_cue_and_callouts_and_numbers_the_shared_components(world):
    soup = _soup(_page(world))
    hero = soup.select("#s1 a[href='#s2']")
    assert len(hero) == 1 and hero[0]["class"] == ["scroll-cue"] and hero[0].select_one("span:first-child").text == "Ver la presentación" and hero[0].select_one(".scroll-cue__line")
    nxt = soup.select("a.next-link")
    assert len(nxt) == 10 and all("scroll-cue" in a["class"] and "btn-secondary" not in a["class"] and a.select_one(".scroll-cue__line") for a in nxt)
    assert [a["href"] for a in nxt] == [f"#s{i}" for i in range(3, 13)] and all(a.select_one("span:first-child").text.startswith("Siguiente · ") for a in nxt)
    assert not soup.select("a.btn-primary, a.btn-secondary, .side-nav"), "el documento no tiene botones propios: el cue (landing) y el rail (Academy) son los primitivos"
    for a in nxt + hero:
        assert not re.search(r"border-radius|font-family|font-size|font-weight|letter-spacing|text-transform|padding:|border:|color:|transition", a["style"]), a["style"]   # la apariencia es del primitivo
    assert len(soup.select("aside.callout.callout--punch")) == 5          # s2 (dato), s3 y s4 (cierres), s9 (cierre), s11 (reconocimientos)
    assert not re.search(r"border-left:\s*\d+px solid", TEMPLATE_PATH.read_text(encoding="utf-8")), "ningún punch es un bloque paralelo con borde propio"
    js = re.sub(r"/\*.*?\*/|//[^\n]*", "", (ASSETS / "vanture.js").read_text(encoding="utf-8"), flags=re.S)
    assert "lastMagnet" not in js and "translate(" not in js, "sin efecto magnético en los controles"
    assert "spotlight" in js, "el foco de luz del héroe se conserva (la inclinación 3D de las tarjetas se retiró: hover de Academy)"


def test_the_shared_tokens_live_in_one_place_and_every_consumer_reads_them():
    content = (STATIC / "content.css").read_text(encoding="utf-8")
    block = re.search(r":root \{\s*--content-surface:.*?\}", content, re.S).group(0)
    light = re.search(r'html\.content-theme-light,\s*html\[data-dw-theme="light"\] \{.*?\}', content, re.S).group(0)
    for token in ("surface", "canvas", "tile", "ink", "ink-soft", "ink-dim", "line"):
        assert f"--content-{token}:" in block and f"--content-{token}:" in light, token
    assert "--content-accent:" in block and "--content-accent" not in light, "el acento es invariante al tema"
    tools = (STATIC / "tools.css").read_text(encoding="utf-8")
    assert not re.search(r"--content-surface:\s*#", tools), "tools.css ya no declara su propia copia de la superficie (consume la compartida)"
    assert "var(--content-canvas)" in tools and "var(--content-ink)" in (STATIC / "layout.css").read_text(encoding="utf-8")
    assert "--gold:var(--content-accent)" in (STATIC / "app.css").read_text(encoding="utf-8")
    academy = ACADEMY_CSS.read_text(encoding="utf-8")
    assert '@import url("/static/content.css");' in academy
    for legacy, token in (("--bg-card", "--content-tile"), ("--ink", "--content-ink"), ("--ink-soft", "--content-ink-soft"), ("--ink-dim", "--content-ink-dim"), ("--line", "--content-line"), ("--gold", "--content-accent")):
        assert re.search(rf"(?m)^\s*{re.escape(legacy)}:\s*var\({re.escape(token)}\);", academy), legacy
    assert ".topic-num{" not in academy.replace(" ", ""), ".topic-num se define UNA vez (content.css), no en Academy"
    assert content.count(".topic-num {") == 1


def test_the_theme_contract_has_a_single_implementation_shared_by_the_hub_and_the_presentation():
    theme = (STATIC / "hub_theme.js").read_text(encoding="utf-8")
    for must in ('"dw-content-theme"', "content-theme-", '"danwor-theme"', "iframe.content-embed", "localStorage", "document.cookie"):
        assert must in theme, must
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "window.dwTheme.set(this.contentTheme)" in app
    toggle = re.sub(r"//[^\n]*", "", re.search(r"toggleTheme\(\) \{.*?\n    \},", app, re.S).group(0))      # sin comentarios: solo el código
    assert "localStorage" not in toggle and "document.cookie" not in toggle and "postMessage" not in toggle, "toggleTheme() delega: no reimplementa el contrato"
    base = (REPO / "webapp" / "templates" / "base.html").read_text(encoding="utf-8")
    assert base.index("hub_theme.js") < base.index("path='app.js'")
    icons = [str(t.relative_to(REPO)) for t in (REPO / "webapp" / "templates").rglob("*.html") if "M20.5 15.5A9 9 0 0 1 8.5 3.5" in t.read_text(encoding="utf-8")]
    assert icons == [str(Path("webapp/templates/partials/theme_icons.html"))], icons          # los SVG del toggle existen UNA sola vez
    assert 'theme_icon("light"' in (REPO / "webapp" / "templates" / "partials" / "platform_topbar.html").read_text(encoding="utf-8") or "theme_icon(\"light\"" in (REPO / "webapp" / "templates" / "partials" / "platform_topbar.html").read_text(encoding="utf-8")


def test_the_fonts_url_has_one_definition_used_by_the_hub_and_the_presentation(world):
    from webapp.templating import templates as t
    url = t.env.globals["hub_fonts_url"]
    assert "family=Inter" in url and "JetBrains+Mono" in url
    assert (REPO / "webapp" / "templates" / "base.html").read_text(encoding="utf-8").count("{{ hub_fonts_url }}") == 1
    assert "fonts.googleapis.com/css2" not in (REPO / "webapp" / "templates" / "base.html").read_text(encoding="utf-8").replace("{{ hub_fonts_url }}", "").split("preconnect")[-1]
    assert _soup(_page(world)).find("link", href=url) is not None
    hub = world.client(A).get("/comercial/presentaciones").text
    assert f'href="{url}"'.replace("&", "&amp;") in hub or f'href="{url}"' in hub                 # el Hub sigue cargando exactamente la misma URL de siempre


@pytest.mark.parametrize("cookie, expected", [("dark", "content-theme-dark"), ("light", "content-theme-light"), (None, "content-theme-light"), ("basura", "content-theme-light")])
def test_the_theme_comes_from_the_hub_cookie_on_the_server_so_there_is_no_flash(world, cookie, expected):
    c = world.client(HUB_ONLY)
    if cookie:
        c.cookies.set("dw-content-theme", cookie)
    html = c.get(f"/presentaciones/{SEED_ID}").text
    assert re.search(r'<html lang="es" class="%s">' % expected, html)
    visible = re.findall(r'<svg (style="display:none" )?data-dw-theme-icon="(light|dark)"', html)
    assert [(hidden == "", icon) for hidden, icon in visible] == [(icon == expected.split("-")[-1], icon) for _, icon in visible], "el ícono del tema actual llega visible desde el servidor"


def test_the_standalone_header_shows_the_toggle_and_the_embedded_viewer_does_not_duplicate_controls(world):
    standalone = _soup(_page(world))
    toggle = standalone.select_one("[data-dw-theme-toggle].theme-toggle")
    assert toggle and toggle.parent.find("span") is not None and len(toggle.find_all("svg")) == 2
    assert not _soup(_page(world, embed=True)).select("[data-dw-theme-toggle]")


def test_the_shared_primitive_buttons_keep_an_accessible_focus_ring():
    for css in ("app.css", "layout.css", "tools.css"):
        text = (STATIC / css).read_text(encoding="utf-8")
        for rule in re.findall(r"([^{}]*\.btn-(?:primary|secondary)[^{}]*)\{([^}]*)\}", text):
            assert not re.search(r"outline\s*:\s*(?:none|0)\b", rule[1]), (css, rule[0].strip())     # ninguna regla elimina el foco visible de la primitiva


def test_the_section_rail_is_the_shared_academy_primitive_without_glow_and_with_hover_tooltips(world):
    soup = _soup(_page(world))
    rail = soup.select_one("nav.rail[aria-label]")
    assert rail is not None and not soup.select(".side-nav, .nav-dot")
    dots = rail.select("a[href^='#s'] > span")
    assert [d.parent["href"] for d in dots] == [f"#s{i}" for i in range(1, 13)] and dots[3].text == "Knowledge Core"     # el texto de cada sección es el tooltip
    assert all(not a.get("style") and not a.get("title") for a in rail.select("a"))                                       # sin estilos inline ni title nativo: lo da el primitivo
    css = VANTURE_CSS.read_text(encoding="utf-8")
    js = (ASSETS / "vanture.js").read_text(encoding="utf-8")
    assert ".nav-dot" not in css and not re.search(r"\.rail[^{]*\{[^}]*box-shadow", css)                                      # el glow ya no existe
    assert not re.search(r"\.rail[^{]*\{[^}]*box-shadow", (STATIC / "content.css").read_text(encoding="utf-8"))
    assert "classList.toggle('active'" in js and "nav-dot" not in js and "is-active" not in js
    shared = (STATIC / "content.css").read_text(encoding="utf-8")
    assert ".rail a.active { background: var(--gold, var(--content-accent)); transform: scale(1.3); }" in shared and ".rail a:hover span, .rail a.active span { opacity: 1; }" in shared
    academy = (REPO / "content_packs" / "datasphere_core" / "assets" / "academy" / "dw-academy.css").read_text(encoding="utf-8")
    assert ".rail a{" not in academy and ".rail{position" not in academy and "@media (max-width:1600px){ .rail a span{ display:none; } }" in academy   # Academy conserva solo su política de labels
    assert re.search(r"--line-2:\s*var\(--content-line-strong\)", academy)


def test_the_scroll_cue_is_one_shared_primitive_with_the_landing_values():
    shared = (STATIC / "content.css").read_text(encoding="utf-8")
    landing = (STATIC / "brand-hero.css").read_text(encoding="utf-8")
    for token in ("font-size: .62rem; text-transform: uppercase; letter-spacing: .3em", "width: 1px; height: 48px", "gap: .55rem", "opacity: .4"):
        assert token in shared, token
    for token in (".hero-scroll span{font-size:.62rem;text-transform:uppercase;letter-spacing:.3em}", "width:1px;height:48px", "gap:.55rem", "opacity:.4"):
        assert token in landing, token                                                                                       # mismos valores que la landing de la que se tomó
    assert sum(1 for t in (REPO / "webapp" / "templates").rglob("*.html") if "scroll-cue" in t.read_text(encoding="utf-8")) == 1


def test_the_cover_pointer_glow_is_the_one_of_the_hub_landing():
    css = VANTURE_CSS.read_text(encoding="utf-8")
    landing = (STATIC / "brand-hero.css").read_text(encoding="utf-8")
    js = (ASSETS / "vanture.js").read_text(encoding="utf-8")
    spot = re.search(r"\.spotlight\{([^}]*)\}", css).group(1)
    assert "radial-gradient(circle 1300px at var(--mx,30%) var(--my,40%)" in spot and "var(--gold) 5%, transparent) 0%" in spot and "var(--gold) 2%, transparent) 45%" in spot and "transparent 70%)" in spot
    assert "radial-gradient(circle 1300px at var(--mx,30%) var(--my,40%),rgba(231,176,8,.05) 0%,rgba(231,176,8,.02) 45%,transparent 70%)" in landing       # los mismos valores que la landing (oro 5% → 2% → nada)
    assert "opacity:0" not in spot.replace(" ", "") and "transition" not in spot, "siempre visible, como en la landing (no se enciende ni se apaga)"
    assert "matchMedia('(hover: hover) and (pointer: fine)')" in js and "mousemove" in js and "spot.style.opacity" not in js


def test_the_cover_is_exactly_one_screen_and_the_entry_cue_is_anchored_to_its_bottom_like_the_landing(world):
    soup = _soup(_page(world))
    hero = soup.find(id="s1")
    style = re.sub(r"\s+", "", hero["style"])
    assert "box-sizing:border-box" in style and "min-height:100svh" in style and "min-height:100vh" in style, "la portada mide la pantalla (el padding no se suma), como `.screen` de la landing"
    cue = hero.select_one("a.scroll-cue")
    assert cue.parent is hero, "el cue cuelga de la portada (no del bloque de texto): se ancla al pie de la pantalla, no al final del contenido"
    cue_style = re.sub(r"\s+", "", cue["style"])
    assert "position:absolute" in cue_style and "bottom:2.5rem" in cue_style, "mismo anclaje que `.hero-scroll` de la landing (2,5rem sobre el borde inferior)"
    landing = (STATIC / "brand-hero.css").read_text(encoding="utf-8")
    assert ".hero-scroll{position:absolute;bottom:2.5rem;" in landing and "min-height:100svh" in landing
    pad_bottom = int(re.search(r"padding:\d+px\s*\d+px\s*(\d+)px", hero["style"]).group(1))
    assert pad_bottom >= 110, "el padding inferior reserva el alto del cue: el contenido nunca lo pisa"


def test_the_section_jump_cue_flows_with_the_content_and_never_floats_over_it(world):
    soup = _soup(_page(world))
    nxt = soup.select("a.next-link")
    assert len(nxt) == 10 and all("scroll-cue" in a["class"] and "scroll-cue--sticky" not in a["class"] for a in nxt)
    assert not any(re.search(r"position:\s*(sticky|fixed|absolute)", a["style"]) for a in nxt), "el cue de cada sección va en el flujo: si cae fuera de la pantalla se corta, nunca se superpone al contenido ni se arrastra"
    shared = (STATIC / "content.css").read_text(encoding="utf-8")
    assert "sticky" not in shared.split("scroll-cue")[-1] and ".scroll-cue--sticky" not in shared


# ── Edición INLINE (como «02. Estrategia») ───────────────────────────────────────────────────────────────────────────

def _edit_doc(world, pid, email, query="?embed=1&edit=1"):
    return _soup(world.client(email).get(f"/presentaciones/{pid}{query}").text)


def test_the_owner_gets_every_editable_text_as_a_plain_text_contenteditable_leaf_in_its_place(world):
    pid = _copy(world, A)
    soup = _edit_doc(world, pid, A)
    assert soup.html.has_attr("data-cp-edit")
    leaves = soup.select("[data-cp-field]")
    assert sorted(l["data-cp-field"] for l in leaves) == sorted(SPEC.field_keys) and len(leaves) == len(SPEC.fields)
    for l in leaves:
        assert l.name == "span" and l["contenteditable"] == "true" and "dw-inline-edit" in l["class"]
        assert l.get_text() == l["data-original"] == cp.full_content("vanture-bdc", cp.get(pid)["content"])[l["data-cp-field"]]      # el valor de siempre, en su lugar
    assert not any(l.has_attr("data-cp-multiline") for l in leaves)                              # el servidor no admite saltos de línea en ningún campo: todos son de una línea


def test_the_edit_mode_document_is_otherwise_the_same_document(world):
    pid = _copy(world, A)
    plain = _soup(world.client(A).get(f"/presentaciones/{pid}?embed=1").text)
    edit = _edit_doc(world, pid, A)
    for soup in (plain, edit):
        for tool in soup.select(".cp-slide-tools, [data-cp-derived], .cp-region-tools, .cp-asset-tools, template, dialog"):                          # herramientas de edición (X por slide) y texto derivado oculto: no son parte del documento
            tool.decompose()
        for kno in soup.select("[data-cp-kicker-no]"):
            kno.unwrap()
        for leaf in soup.select("[data-cp-field]"):
            leaf.unwrap()
        for el in soup.select("[data-cp-edit]"):
            del el["data-cp-edit"]
    assert plain.get_text(" ", strip=True) == edit.get_text(" ", strip=True)                    # mismo texto, mismas secciones: solo cambia que los leaves son editables


@pytest.mark.parametrize("who, query", [(HUB_ONLY, "?embed=1&edit=1"), (B, "?embed=1&edit=1"), (A, "?edit=1"), (A, "?embed=1")], ids=["hub-user", "commercial-not-owner", "owner-without-embed", "owner-without-edit"])
def test_edit_mode_is_never_granted_by_the_query_alone(world, who, query):
    pid = _copy(world, A)
    soup = _edit_doc(world, pid, who, query)
    assert not soup.html.has_attr("data-cp-edit") and not soup.select("[data-cp-field], [contenteditable]")


def test_the_seed_is_never_editable_and_the_administrators_are(world):
    assert not _edit_doc(world, SEED_ID, A).select("[contenteditable]") and not _edit_doc(world, SEED_ID, OWNER_C).select("[contenteditable]")
    pid = _copy(world, A)
    assert len(_edit_doc(world, pid, OWNER_C).select("[data-cp-field]")) == len(SPEC.fields)      # CONTENT_OWNER edita copias ajenas; APP_OWNER no, por ser administrador (ver `can_edit_presentation_user`)


def test_edited_values_are_emitted_escaped_inside_the_leaf_and_in_the_original_attribute(world):
    pid = _copy(world, A)
    hostile = '<img src=x onerror=alert(1)> & "comillas"'
    assert _save(world, A, pid, {"hero_lead": hostile}, 1).status_code == 200
    html = world.client(A).get(f"/presentaciones/{pid}?embed=1&edit=1").text
    assert "<img src=x onerror" not in html
    leaf = _soup(html).select_one('[data-cp-field="hero_lead"]')
    assert leaf.get_text() == hostile and leaf["data-original"] == hostile                      # el texto sigue siendo texto; el navegador lo ve igual al guardado


def test_the_editor_page_embeds_the_live_document_with_the_estrategia_style_toolbar_and_no_form_fields(world):
    pid = _copy(world, A)
    soup = _soup(world.client(A).get(f"/comercial/presentaciones/{pid}/editar").text)
    ed = soup.select_one("[data-cp-editor]")
    assert ed["data-save-url"] == f"/comercial/presentaciones/{pid}" and ed["data-revision"] == "1" and ed["data-back-url"] == f"/comercial/presentaciones/{pid}"
    assert ed.select_one("iframe[data-cp-frame]")["src"] == f"/presentaciones/{pid}?embed=1&edit=1"
    bar = ed.select_one(".cp-toolbar")
    assert [(b.name, b.get("class"), b.text.strip()) for b in bar.find_all(["button", "span"], recursive=False)] == [
        ("button", ["btn-secondary"], "Cancelar"), ("span", ["status-badge", "status-badge--tentativo"], "Sin guardar"), ("button", ["btn-primary"], "Guardar")]
    assert bar.select_one("[data-cp-dirty]").has_attr("hidden") and bar.select_one("[data-cp-save]").has_attr("disabled")      # sin cambios no hay nada que guardar
    assert not soup.select_one("main").select("form, textarea, input, [data-cp-edit-form]"), "ya no es un formulario: se edita sobre el documento"
    assert soup.select_one("a.catalog-back") is not None and soup.select_one("aside.callout--punch.cp-notice") is not None


def test_the_inline_editor_saves_only_changed_fields_with_plain_text_and_the_loaded_revision():
    js = (REPO / "webapp" / "static" / "commercial_presentations.js").read_text(encoding="utf-8")
    block = js.split("// ── Editor INLINE", 1)[1]
    assert "el.textContent.replace(" in block and "u00a0" in block and "innerText" not in block      # texto plano de una línea; el NBSP del contenteditable se normaliza
    assert "revision: parseInt(editor.getAttribute(\"data-revision\"), 10), fields: delta }" in block and "removed_slides: removedNow()" in block      # los campos cambiados + (solo si cambió) la lista de slides eliminadas
    assert "innerHTML" not in block and "eval(" not in block
    assert "dwConfirm" in block and "beforeunload" in block                                    # confirma antes de descartar cambios
    doc = (ASSETS / "vanture.js").read_text(encoding="utf-8")
    assert "hasAttribute('data-cp-edit')" in doc and "text/plain" in doc and "insertText" in doc and "e.key === 'Enter'" in doc


def test_the_shared_inline_edit_look_mirrors_the_estrategia_leaf():
    shared = (STATIC / "content.css").read_text(encoding="utf-8")
    assert ".dw-inline-edit { outline: 1px dashed var(--line, var(--content-line)); outline-offset: 3px; border-radius: 4px; cursor: text;" in shared and "text-transform: none !important;" in shared
    strategy = (STATIC / "commercial_strategy.css").read_text(encoding="utf-8")
    assert "outline: 1px dashed var(--line); outline-offset: 3px; border-radius: 4px;" in strategy        # el valor de referencia que se copia
