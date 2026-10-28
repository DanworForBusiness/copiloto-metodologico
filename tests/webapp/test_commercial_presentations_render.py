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

import pytest
from bs4 import BeautifulSoup

from tests.webapp.test_commercial_presentations_access import (  # noqa: F401  (fixtures y helpers compartidos)
    A, HUB_ONLY, ORIGIN, SEED_ID, SEED_YAML, _copy, _save, world,
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
    assert soup.find("link", rel="stylesheet")["href"].startswith("/pack-assets/datasphere_core/presentations/vanture-bdc/vanture.css?v=")
    assert soup.find("script", src=True)["src"].startswith("/pack-assets/datasphere_core/presentations/vanture-bdc/vanture.js?v=")
    assert not [s for s in soup.find_all("script") if not s.get("src")], "el template no trae JS en línea"
    assert not soup.find_all("style"), "el template no trae <style> en línea (el CSS es un archivo compartido)"
    assert len(html.encode()) < 120_000                                                         # antes: 3 MB con fuentes e imágenes en base64
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

    for key, token in sentinels.items():
        holders = [t for t in soup.find_all(string=re.compile(token))]
        assert len(holders) == 1, key
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
    for forbidden in ("|safe", "| safe", "Markup", "autoescape", "{% raw", "|tojson", "{% include", "{% extends", "{% import", "{% from"):
        assert forbidden not in source, forbidden
    used = set(re.findall(r"\bc\.([a-z0-9_]+)\b", source))
    assert used == set(SPEC.field_keys)                                                         # ni claves de más ni campos sin usar
    assert sorted(set(re.findall(r"\{\{\s*([^}]+?)\s*\}\}", source))) == sorted({f"c.{k}" for k in SPEC.field_keys} | {"css_url", "js_url", "asset_base"})


def test_there_are_no_editable_attribute_or_url_contexts_in_v1():
    source = TEMPLATE_PATH.read_text(encoding="utf-8")
    for key in SPEC.field_keys:
        for m in re.finditer(r"\{\{\s*c\." + key + r"\s*\}\}", source):
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
    assert len(raw) < 8_000
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
    assert {p.suffix for p in files} <= {".css", ".js", ".woff2", ".png", ".jpg"}, "solo fuentes, CSS, JS e imágenes de marca bajo /pack-assets"
    assert len([p for p in files if p.suffix == ".woff2"]) == 33 and len([p for p in files if p.parent.name == "img"]) == 46
    assert (ASSETS / "vanture.css").is_file() and (ASSETS / "vanture.js").is_file()
    css = (ASSETS / "vanture.css").read_text(encoding="utf-8")
    assert "base64" not in css
    for ref in re.findall(r"url\((fonts/[0-9a-f]+\.woff2)\)", css):
        assert (ASSETS / ref).is_file(), ref
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    for ref in re.findall(r"\{\{ asset_base \}\}/(img/[0-9a-f]+\.(?:png|jpg))", template):
        assert (ASSETS / ref).is_file(), ref


def test_the_shared_assets_are_served_under_the_hub_read_contract_without_commercial(world):
    base = "/pack-assets/datasphere_core/presentations/vanture-bdc"
    for name in ("vanture.css", "vanture.js"):
        r = world.client(HUB_ONLY).get(f"{base}/{name}")
        assert r.status_code == 200 and len(r.content) > 1000, name
    font = sorted((ASSETS / "fonts").iterdir())[0].name
    assert world.client(HUB_ONLY).get(f"{base}/fonts/{font}").status_code == 200
    anonymous = world.client(None).get(f"{base}/vanture.css", follow_redirects=False)
    assert anonymous.status_code in (303, 307) and "font-face" not in anonymous.text             # sin sesión no hay lectura: redirige a login


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
    assert payload["schema_version"] == 1 and payload["template_version"] == 1 and set(payload["content"]) == set(SPEC.field_keys)
    pid = _write_instance(world, payload)
    view = cp.get(pid)
    assert view["content"] == payload["content"] and view["owner_id"] == payload["owner_id"]
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
