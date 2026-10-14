"""test_catalog_view_source.py — "Abrir SQL" para vistas SQL de Datasphere en el Catálogo (Gate 3).

Contrato:
  * el botón aparece SOLO en el artefacto-vista cuyo JSON del pool es SQL (clasificador único) y trae `@DataWarehouse.sqlEditor.query`;
    GRAPHICAL, UNKNOWN y SQL sin consulta no muestran nada (ni botón roto);
  * la fuente sale del JSON del pool (no del Markdown generado), EXACTA y escapada (un `<script>` o un ``` no rompen el panel);
  * mismo panel/plantilla/navegación que "Abrir CDS View"; sesión obligatoria; 404 si no es una vista SQL con consulta;
  * pools con carpetas por tipo y pools planos (pertenencia estructural, sin tablas remotas ni cubos).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
import webapp.main as main
from tests._modeling_assessment_m7_fixtures import _view, _write
from tools.catalog import catalog_service as cs
from webapp.services import assessment_service as svc
from webapp.services import auth_service, catalog_view_source

HX = {"HX-Request": "true"}
SQL_KEY = "@DataWarehouse.sqlEditor.query"
NASTY_SQL = "SELECT 'a<script>alert(1)</script>' AS X\n-- ``` no cierres el bloque\nFROM \"2VG_GR\" WHERE A = '&amp;'"


def _sql_view(name, source, query):
    v = _view(name, source, ("A",))
    if query is not None:
        v["definitions"][name][SQL_KEY] = query
    return v


def _graphical(name, source):
    v = _view(name, source, ("A",))
    v["editorSettings"] = {name: {"editor": {"default": "GRAPHICALVIEWBUILDER"}}}
    return v


def _remote(name):
    return {"definitions": {name: {"kind": "entity", "@DataWarehouse.remote.connection": "S4", "@DataWarehouse.remote.entity": "T1",
                                   "elements": {"A": {"type": "cds.String"}}}}}


def _families(root: Path) -> Path:
    _write(root, "Acme/remote-tables/RAW_T.json", _remote("RAW_T"))
    _write(root, "Acme/views/1VR_UNK.json", _view("1VR_UNK", "RAW_T", ("A",)))                  # UNKNOWN
    _write(root, "Acme/views/2VG_GR.json", _graphical("2VG_GR", "1VR_UNK"))                    # GRAPHICAL
    _write(root, "Acme/views/3VF_SQL.json", _sql_view("3VF_SQL", "2VG_GR", NASTY_SQL))         # SQL con consulta
    _write(root, "Acme/views/3VF_EMPTY.json", _sql_view("3VF_EMPTY", "2VG_GR", "   "))         # SQL con consulta vacía
    _write(root, "Acme/views/3VF_BOTH.json", {**_sql_view("3VF_BOTH", "2VG_GR", "SELECT 2"),   # GRAPHICAL gana aunque tenga query
                                              "editorSettings": {"3VF_BOTH": {"editor": {"default": "GRAPHICALVIEWBUILDER"}}}})
    _write(root, "Acme/analytic-models/4AM_T.json", _sql_view("4AM_T", "3VF_SQL", "SELECT 3"))  # AM con query: NO es una vista
    return root


def _flat(root: Path) -> Path:
    _write(root, "Flat/RAW_T.json", _remote("RAW_T"))
    _write(root, "Flat/3VF_SQL.json", _sql_view("3VF_SQL", "RAW_T", "SELECT 1"))
    remote_sql = _remote("RAW_Q")
    remote_sql["definitions"]["RAW_Q"][SQL_KEY] = "SELECT 9"                                   # tabla remota con la clave: excluida
    _write(root, "Flat/RAW_Q.json", remote_sql)
    return root


@pytest.fixture()
def world(tmp_path, monkeypatch):
    inbound = _families(tmp_path / "pool")
    _flat(inbound)
    monkeypatch.setenv("DANWOR_CATALOG_CSN_CACHE", str(inbound))
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "catalog"))
    monkeypatch.setattr(config, "ASSESSMENT_CACHE_DIR", tmp_path / "cache")
    svc.reset_memory_cache()
    svc.invalidate_pool_index()
    catalog_view_source._memo.clear()
    yield inbound
    catalog_view_source._memo.clear()
    svc.reset_memory_cache()
    svc.invalidate_pool_index()


def client_for():
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "t@d.com", "name": "T", "first_name": "T", "family_name": "U"}))
    return c


# ── servicio ────────────────────────────────────────────────────────────────
def test_only_sql_views_with_a_query_are_openable(world):
    assert catalog_view_source.sql_view_names("Acme", "") == frozenset({"3VF_SQL"})   # no UNKNOWN, GRAPHICAL(+query), query vacía ni AM
    assert catalog_view_source.read_view_sql("Acme", "", "2VG_GR") is None and catalog_view_source.read_view_sql("Acme", "", "1VR_UNK") is None
    assert catalog_view_source.read_view_sql("Acme", "", "3VF_EMPTY") is None and catalog_view_source.read_view_sql("Acme", "", "3VF_BOTH") is None
    assert catalog_view_source.read_view_sql("Acme", "", "4AM_T") is None


def test_the_source_is_the_exact_text_of_the_pool_json_and_the_type_comes_from_the_single_classifier(world):
    hit = catalog_view_source.read_view_sql("Acme", "", "3VF_SQL")
    assert hit.sql == NASTY_SQL and hit.view_type == "SQL" and hit.view_type_basis == "DERIVED" and hit.source_file == "Acme/views/3VF_SQL.json"
    from tools.catalog import dsp_view_type as vt
    data = json.loads((world / "Acme/views/3VF_SQL.json").read_text(encoding="utf-8"))
    assert vt.sql_query_of(data, "3VF_SQL") == NASTY_SQL and vt.view_type_of_export(data, "3VF_SQL") == ("SQL", "DERIVED")


def test_flat_pools_use_the_structural_marker_and_exclude_remote_tables(world):
    assert catalog_view_source.sql_view_names("Flat", "") == frozenset({"3VF_SQL"})
    assert catalog_view_source.read_view_sql("Flat", "", "RAW_Q") is None


def test_unknown_clients_and_hostile_names_degrade_to_nothing(world):
    assert catalog_view_source.sql_view_names("Nadie", "") == frozenset()
    for bad in ("", "../x", "a/b", "a\\b", "NO_EXISTE"):
        assert catalog_view_source.read_view_sql("Acme", "", bad) is None


def test_the_index_follows_the_pool_and_never_stores_sql_text(world):
    catalog_view_source.sql_view_names("Acme", "")
    ((_fp, index),) = [v for k, v in catalog_view_source._memo.items() if k[1] == "acme"]
    assert all(isinstance(v, str) and "SELECT" not in v for v in index.values())
    _write(world, "Acme/views/3VF_NEW.json", _sql_view("3VF_NEW", "2VG_GR", "SELECT 7"))
    assert catalog_view_source.sql_view_names("Acme", "") == frozenset({"3VF_SQL", "3VF_NEW"})


# ── botón en el detalle del flujo ───────────────────────────────────────────
def _flow_entry(tmp_path, names):
    catalog = tmp_path / "catalog"
    (catalog / "vistas").mkdir(parents=True, exist_ok=True)
    for n in names:
        (catalog / "vistas" / f"{n}.md").write_text(f"---\nauto:\n  label: {n}\n---\n# {n}\n\nCuerpo de {n}.\n", encoding="utf-8")
    return cs.CatalogEntry(path=str(catalog / "flujo_x.md"), cliente="Acme", dominio="Dom", veredicto="plena", apto="Referencia reutilizable",
                           entry_id="acme-x", artefactos=list(names))


def test_the_button_appears_only_on_the_sql_artifact_and_points_back_to_the_flow(world, tmp_path):
    entry = _flow_entry(tmp_path, ["1VR_UNK", "2VG_GR", "3VF_SQL", "3VF_EMPTY"])
    sections = main._catalog_flow_view(entry, pack="datasphere_core")[0]
    html = "\n".join(s.html for s in sections)
    assert html.count("Abrir SQL") == 1 and html.count("/catalog/sql?") == 1
    sql_section = next(s.html for s in sections if "Abrir SQL" in s.html)
    assert "3VF_SQL" in sql_section and "name=3VF_SQL" in sql_section and "flow=acme-x" in sql_section and "cliente=Acme" in sql_section
    assert 'hx-target="#catalog-detail-slot"' in sql_section and "catalog-sql-link" in sql_section
    assert "NASTY" not in html and "SELECT" not in html                      # el texto SQL no viaja en el detalle del flujo


def test_the_existing_cds_button_markup_is_unchanged(world):
    assert main._catalog_cds_button_html("ZCDS_X", "datasphere_core", "Acme", "Dom", "f1") == (
        '\n\n<div class="support-links heading-supports catalog-cds-launch">'
        '<a class="support-link catalog-cds-link" href="#" role="button" '
        'hx-get="/catalog/cds?name=ZCDS_X&cliente=Acme&dominio=Dom&flow=f1&pack=datasphere_core" hx-target="#catalog-detail-slot" hx-swap="innerHTML" '
        '@click.prevent>Abrir CDS View <span>&#8249;/&#8250;</span></a></div>\n\n')


# ── endpoint ────────────────────────────────────────────────────────────────
def test_the_endpoint_requires_a_session(world):
    r = TestClient(main.app).get("/catalog/sql?name=3VF_SQL&cliente=Acme&pack=datasphere_core", follow_redirects=False)
    assert r.status_code in (302, 303, 307) and "3VF_SQL" not in r.text and "SELECT" not in r.text


def test_the_endpoint_renders_the_sql_escaped_in_the_shared_panel_with_back_navigation(world):
    r = client_for().get("/catalog/sql?name=3VF_SQL&cliente=Acme&dominio=Dom&flow=acme-x&pack=datasphere_core", headers=HX)
    assert r.status_code == 200
    html = r.text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html          # escapado, no ejecutable
    assert "no cierres el bloque" in html and "<pre" in html                              # el ``` interno no cortó el bloque: el comentario sigue dentro
    assert "Vista SQL" in html and "3VF_SQL" in html and "Código SQL" in html
    assert "/catalog/flow?cliente=Acme&dominio=Dom&flow=acme-x&pack=datasphere_core" in html and "Volver al flujo" in html


def test_the_endpoint_404s_for_anything_that_is_not_a_sql_view_with_a_query(world):
    c = client_for()
    for name in ("2VG_GR", "1VR_UNK", "3VF_EMPTY", "3VF_BOTH", "4AM_T", "NO_EXISTE", "..", ""):
        assert c.get(f"/catalog/sql?name={name}&cliente=Acme&pack=datasphere_core", headers=HX).status_code == 404, name
    assert c.get("/catalog/sql?name=3VF_SQL&cliente=Nadie&pack=datasphere_core", headers=HX).status_code == 404


def test_the_full_page_variant_renders_with_the_shell(world):
    r = client_for().get("/catalog/sql?name=3VF_SQL&cliente=Acme&pack=datasphere_core")
    assert r.status_code == 200 and "Vista SQL" in r.text and "&lt;script&gt;" in r.text


def test_the_cds_panel_label_is_unchanged_for_cds_views():
    tpl = (Path(__file__).resolve().parents[2] / "webapp/templates/partials/catalog_cds_content.html").read_text(encoding="utf-8")
    assert '{{ source_label or "API corporativa" }}' in tpl


# ── identidad del encabezado de la página SQL (nombre de negocio · capa · uso semántico · nombre técnico) ──
VISTA_MD = "---\nauto:\n  tipo: vista\n  nombre: 3VF_SQL\n  label: {label}\n  capa: {capa}\n  uso_semantico: {uso}\n---\n# 3VF_SQL\n\nCuerpo.\n"


def _with_identity(monkeypatch, tmp_path, *, label="Vista relacional - Despachos", capa="Armonización", uso="Relacional", write=True):
    from webapp.services import catalog_reads
    catalog = tmp_path / "catalog"
    (catalog / "vistas").mkdir(parents=True, exist_ok=True)
    if write:
        (catalog / "vistas" / "3VF_SQL.md").write_text(VISTA_MD.format(label=label, capa=capa, uso=uso), encoding="utf-8")
    else:
        (catalog / "vistas" / "3VF_SQL.md").unlink(missing_ok=True)
    entry = cs.CatalogEntry(path=str(catalog / "flujo_x.md"), cliente="Acme", dominio="Dom", veredicto="plena", apto="Referencia reutilizable",
                            entry_id="acme-x", artefactos=["3VF_SQL"])
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, [entry]))
    return entry


def _page(name="3VF_SQL"):
    r = client_for().get(f"/catalog/sql?name={name}&cliente=Acme&dominio=Dom&flow=acme-x&pack=datasphere_core", headers=HX)
    assert r.status_code == 200
    return r.text


def _hero(html):
    return html[html.index('<section class="module-hero">'):html.index("</section>", html.index('<section class="module-hero">'))]


def test_the_hero_leads_with_the_business_name_and_keeps_the_technical_name_in_the_meta_line(world, tmp_path, monkeypatch):
    _with_identity(monkeypatch, tmp_path)
    hero = _hero(_page())
    assert "<h1>Vista relacional - Despachos</h1>" in hero
    assert '<span>Armonización · Relacional · <code>3VF_SQL</code></span>' in hero                              # capa · uso semántico · nombre técnico (siempre último)
    assert '<p class="hero-lead">Código SQL de la vista en Datasphere</p>' in hero                              # descripción exacta
    for gone in ("Consulta SQL de la vista tal como se exportó", "solo lectura", "no se ejecuta"):
        assert gone not in hero
    assert "<h1>3VF_SQL</h1>" not in hero and "Código SQL" in _page()                                          # la sección [01] "Código SQL" no cambia


def test_missing_or_unresolved_business_name_falls_back_to_the_technical_name(world, tmp_path, monkeypatch):
    for label in ('""', "null", "'{i18n>Remote.X@ENDUSERTEXT.LABEL}'"):
        _with_identity(monkeypatch, tmp_path, label=label)
        hero = _hero(_page())
        assert "<h1>3VF_SQL</h1>" in hero and "<code>3VF_SQL</code>" in hero, label                             # sin nombre canónico: jamás se inventa uno


def test_missing_optional_metadata_omits_the_segment_without_malformed_separators(world, tmp_path, monkeypatch):
    cases = {("Armonización", "null"): "Armonización · <code>3VF_SQL</code>", ("Sin definir", "Relacional"): "Relacional · <code>3VF_SQL</code>",
             ("Sin definir", "N/A"): "<code>3VF_SQL</code>", ("null", "null"): "<code>3VF_SQL</code>"}
    for (capa, uso), expected in cases.items():
        _with_identity(monkeypatch, tmp_path, capa=capa, uso=uso)
        hero = _hero(_page())
        assert f"<span>{expected}</span>" in hero, (capa, uso)
        assert not re.search(r"·\s*·|<span>\s*·|·\s*</span>", hero)
    _with_identity(monkeypatch, tmp_path, write=False)                                                          # sin metadata del Catálogo: solo el nombre técnico
    hero = _hero(_page())
    assert "<h1>3VF_SQL</h1>" in hero and "<span><code>3VF_SQL</code></span>" in hero


def test_identity_is_read_from_the_catalog_metadata_and_nothing_is_inferred(world, tmp_path, monkeypatch):
    _with_identity(monkeypatch, tmp_path, label="Vista X", capa="Consumo", uso="Fact")
    ident = catalog_view_source.artifact_identity("3VF_SQL", "Acme", "Dom", "acme-x")
    assert (ident.business_name, ident.layer, ident.semantic_use) == ("Vista X", "Consumo", "Fact") and ident.meta_segments("3VF_SQL") == ["Consumo", "Fact", "3VF_SQL"]
    by_domain = catalog_view_source.artifact_identity("3VF_SQL", "Acme", "Dom", "")                              # sin `flow`: la entrada que lista al artefacto
    assert by_domain.business_name == "Vista X"
    other = catalog_view_source.artifact_identity("3VF_SQL", "Otro", "Dom", "")                                  # ningún cliente: sin inferir capa/uso del prefijo "3VF_"
    assert (other.business_name, other.layer, other.semantic_use) == ("3VF_SQL", None, None)


def test_the_source_route_auth_and_copy_behaviour_are_unchanged_by_the_hero_polish(world, tmp_path, monkeypatch):
    _with_identity(monkeypatch, tmp_path)
    html = _page()
    assert "&lt;script&gt;" in html and "<script>alert(1)</script>" not in html and "no cierres el bloque" in html     # mismo SQL, mismo escapado
    assert "Volver al flujo" in html and "/catalog/flow?cliente=Acme&dominio=Dom&flow=acme-x" in html
    r = TestClient(main.app).get("/catalog/sql?name=3VF_SQL&cliente=Acme&pack=datasphere_core", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert client_for().get("/catalog/sql?name=2VG_GR&cliente=Acme&pack=datasphere_core", headers=HX).status_code == 404


def test_no_css_or_js_was_added_for_the_sql_hero():
    root = Path(__file__).resolve().parents[2]
    css = (root / "webapp/static/tools.css").read_text(encoding="utf-8")
    assert not re.search(r"meta_prefix|catalog-sql-(hero|meta)|sql-hero", css)
    tpl = (root / "webapp/templates/partials/catalog_cds_content.html").read_text(encoding="utf-8")
    assert "<script" not in tpl and "style=" not in tpl.replace('style="border-top:1px solid rgba(128,128,128,.32)"', "")


def test_view_type_map_gives_the_real_type_of_views_only_and_nothing_without_a_pool(world):
    catalog_view_source._type_memo.clear()
    got = catalog_view_source.view_type_map("Acme", "")
    assert got == {"2VG_GR": "GRAPHICAL", "3VF_SQL": "SQL", "3VF_EMPTY": "SQL", "3VF_BOTH": "GRAPHICAL"}      # sin UNKNOWN, tablas remotas ni modelos analíticos: nunca se adivina
    assert catalog_view_source.view_type_map("Acme", "") == got                                                   # memoizado por huella del pool
    assert catalog_view_source.view_type_map("NoExiste", "") == {}
