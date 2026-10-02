"""
test_catalog_assessment_routes.py — rutas, tile, informe estructurado y foco de linaje (Catalog Assessment Report V1).

Fixtures SINTÉTICAS (pool `write_inbound`, catálogo de prueba en tmp). Nunca datos reales.

Contratos que fija:
  * GET jamás genera ni escribe (un miss es NOT_YET_ASSESSED); POST genera y devuelve el informe estructurado;
  * acceso: sesión obligatoria; POST exige mismo origen; `readonly` (academy = sin IA) PUEDE ver y generar
    (el Catálogo es determinista/sin tokens y `/catalog/regenerate` ya acepta a cualquier usuario logueado);
  * la UI se renderiza desde el JSON (sin |safe, sin Markdown→HTML); el Markdown exportado es `render_markdown(perfil)`;
  * el tile es compacto, rotulado "Assessment de modelado" y NO altera el veredicto/apto legacy;
  * `focus` resalta nodos del linaje (uno, varios) y un nombre ausente degrada sin error;
  * sin vocabulario de puntaje/banda de madurez y con texto humano en español.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
import webapp.main as main
from tests._modeling_assessment_m7_fixtures import write_inbound
from tools.catalog import catalog_service as cs
from tools.catalog.modeling_assessment import profile as ma_profile
from tools.catalog.modeling_assessment.render_md import render_markdown
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import auth_service, catalog_reads

REPO = Path(__file__).resolve().parents[2]
ORIGIN = {"Origin": "http://testserver"}
HX = {"HX-Request": "true"}
BANNED = re.compile(r"\b(score|scores|scoring|band|bands|weight|weights|penalty|penalties|rating|ranking|rank|PLENO|BUENO|PARCIAL|grade|grades|maturity|"
                    r"puntaje|puntajes|puntuaci[oó]n|banda|bandas|penalizaci[oó]n|penalidad|ponderaci[oó]n|madurez)\b|\b[1-5]\s*/\s*5\b", re.I)
ACME = ["1VR_RAW", "3VF_P", "4AM_ACME", "RAW_T"]


def _flow(cliente, artefactos, eid, dominio="Dom"):
    return cs.CatalogEntry(path="x", cliente=cliente, dominio=dominio, veredicto="plena", apto="Referencia reutilizable",
                           entry_id=eid, artefactos=list(artefactos))


@pytest.fixture()
def world(tmp_path, monkeypatch):
    inbound = write_inbound(tmp_path / "pool")
    cache = tmp_path / "runtime-cache"
    monkeypatch.setenv("DANWOR_CATALOG_CSN_CACHE", str(inbound))
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "catalog"))
    monkeypatch.setattr(config, "ASSESSMENT_CACHE_DIR", cache)
    entries = [_flow("Acme", ACME, "acme-4am"), _flow("Solo", ["1VR_ONLY", "LOCAL_X"], "solo-na")]
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))
    svc.reset_memory_cache()
    svc.invalidate_pool_index()

    class W:
        pass
    w = W()
    w.inbound, w.cache, w.entries, w.tmp = inbound, cache, entries, tmp_path
    yield w
    svc.reset_memory_cache()
    svc.invalidate_pool_index()


def client_for(email="t@d.com"):
    session = auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "U"})
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


def anon():
    return TestClient(main.app)


def _cache_files(w):
    return sorted(p.name for p in w.cache.rglob("*") if p.is_file()) if w.cache.exists() else []


def _block(html: str, eid: str) -> str:
    m = re.search(rf'<div class="asm-tile[^"]*" id="asm-tile-{re.escape(eid)}".*?</div>', html, re.S)
    assert m, f"no hay bloque de assessment para {eid}"
    return m.group(0)


def _post(c, flow="acme-4am"):
    return c.post(f"/catalog/flow/assessment?flow={flow}", headers={**ORIGIN, **HX})


def _no_engine(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("el GET no debe ejecutar el motor")
    monkeypatch.setattr(svc, "_engine_assess", boom)


# ── 2/20. GET: sin motor, sin efectos ───────────────────────────────────────
def test_get_cache_miss_is_not_yet_assessed_and_has_no_side_effects(world, monkeypatch):
    _no_engine(monkeypatch)
    c = client_for()
    for _ in range(2):
        r = c.get("/catalog/flow/assessment?flow=acme-4am", headers=HX)
        assert r.status_code == 200
        assert "Sin assessment" in r.text and "Generar informe" in r.text and "hx-post" in r.text
        assert "Abrir esta pantalla no ejecuta nada" in r.text
    assert not _cache_files(world) and not world.cache.exists()
    t = c.get("/catalog/flow/assessment?flow=acme-4am&frag=tile", headers=HX)
    assert t.status_code == 200 and "Generar informe" in t.text and not _cache_files(world)
    d = c.get("/catalog/domain?dominio=Dom")
    assert d.status_code == 200 and not _cache_files(world)


def test_get_without_htmx_redirects_to_the_flow_detail_and_never_generates(world, monkeypatch):
    _no_engine(monkeypatch)
    r = client_for().get("/catalog/flow/assessment?flow=acme-4am", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/catalog/flow?") and "flow=acme-4am" in r.headers["location"]
    assert not _cache_files(world)


# ── 3. POST: genera, valida, cachea, devuelve el informe ────────────────────
def test_post_generates_validated_json_and_returns_the_structured_report(world):
    r = _post(client_for())
    assert r.status_code == 200
    assert json.loads(r.headers["HX-Trigger"]) == {"assessment-changed": {"flow": "acme-4am"}}
    for needle in ("Assessment vigente", "Resumen", "Áreas", "Observaciones", "Revisión requerida", "No evaluado / Limitaciones de evidencia",
                   "Evidencia / Detalle técnico", "Estricta", "Inclusiva", "Exportar Markdown", "Exportar JSON", "4AM_ACME", "Ver flujo en el catálogo"):
        assert needle in r.text, needle
    ma_profile.validate_assessment(svc.status_for_entry(world.entries[0]).profile)
    again = client_for().get("/catalog/flow/assessment?flow=acme-4am", headers=HX)
    assert "Assessment vigente" in again.text


def test_report_shows_findings_with_why_context_and_lineage_links(world):
    html = _post(client_for()).text
    assert "Acceso directo a tabla" in html or "ACCESO DIRECTO A TABLA" in html.upper()
    assert "Por qué se muestra" in html and "Ver en linaje" in html and "/catalog/flow?" in html and "focus=" in html


# ── 13. el HTML sale del JSON; nada de Markdown→HTML ────────────────────────
def test_report_html_derives_from_json_with_no_markdown_or_safe_path(world):
    c = client_for()
    _post(c)
    st = svc.status_for_entry(world.entries[0])
    # fuente única: doctoramos el JSON cacheado y el HTML lo refleja (no hay otro origen de texto)
    env = json.loads(json.dumps(svc._load_envelope("Acme", "4AM_ACME")))
    env["profile_json"]["observations"][0]["title"] = "TITULO_UNICO_DESDE_JSON"
    env["assessment_artifact_signature"] = svc._artifact_signature(env["profile_json"])
    svc._store_envelope(env)
    assert "TITULO_UNICO_DESDE_JSON" in c.get("/catalog/flow/assessment?flow=acme-4am", headers=HX).text
    assert "TITULO_UNICO_DESDE_JSON" in svc.export_markdown(svc.status_for_entry(world.entries[0]))      # y la exportación, del mismo JSON
    # guardas estáticas: sin |safe en los templates del assessment, sin librería de Markdown en la capa UI
    for name in ("catalog_assessment_report.html", "catalog_assessment_tile.html"):
        code = re.sub(r"\{#.*?#\}", "", (REPO / "webapp/templates/partials" / name).read_text(encoding="utf-8"), flags=re.S)     # sin comentarios Jinja
        assert not re.search(r"\|\s*safe\b|\{%\s*autoescape\s+false|Markup\(", code), name
    for rel in ("webapp/services/assessment_presenter.py", "webapp/routers/catalog_assessment.py", "webapp/services/assessment_service.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import|from)\s+(markdown|mistune|commonmark|markdown_it)\b", src, re.M), rel
        assert "content_service" not in src and "render_sections" not in src, rel
    assert "render_markdown" not in (REPO / "webapp/services/assessment_presenter.py").read_text(encoding="utf-8").replace("render_md", "")
    del st


def test_assessment_text_is_escaped_never_trusted_as_html(world):
    c = client_for()
    _post(c)
    env = json.loads(json.dumps(svc._load_envelope("Acme", "4AM_ACME")))
    g = env["profile_json"]["observations"][0]
    g["title"] = "<script>alert('x')</script>"
    g["occurrences"][0]["values"]["remote_entity"] = "<img src=x onerror=alert(1)>"
    env["assessment_artifact_signature"] = svc._artifact_signature(env["profile_json"])
    svc._store_envelope(env)
    html = c.get("/catalog/flow/assessment?flow=acme-4am", headers=HX).text
    assert "<script>alert" not in html and "&lt;script&gt;alert" in html
    assert "<img src=x" not in html and "&lt;img src=x" in html


# ── 19. vocabulario ─────────────────────────────────────────────────────────
def test_no_maturity_score_or_band_vocabulary_and_human_text_is_spanish(world):
    c = client_for()
    report = _post(c).text
    tile = c.get("/catalog/flow/assessment?flow=acme-4am&frag=tile", headers=HX).text
    for html in (report, tile):
        visible = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
        visible = re.sub(r"<[^>]+>", " ", visible)
        assert not BANNED.search(visible), BANNED.search(visible).group(0)
    assert "Assessment vigente" in tile and "Cobertura" in tile and "Confianza" in tile
    for english in ("Not assessed", "Coverage", "Confidence", "Review required", "Generate report", "Retry"):
        assert english not in report and english not in tile, english


# ── exportaciones (14/15) ───────────────────────────────────────────────────
def test_exports_are_the_canonical_json_and_render_markdown_of_the_same_profile(world):
    c = client_for()
    assert c.get("/catalog/flow/assessment.json?flow=acme-4am").status_code == 404        # sin assessment no hay export
    assert c.get("/catalog/flow/assessment.md?flow=acme-4am").status_code == 404
    _post(c)
    prof = svc.status_for_entry(world.entries[0]).profile
    j = c.get("/catalog/flow/assessment.json?flow=acme-4am")
    m = c.get("/catalog/flow/assessment.md?flow=acme-4am")
    assert j.status_code == m.status_code == 200
    assert j.text == ma_profile.canonical_assessment_json(prof) and json.loads(j.text) == prof
    assert m.text == render_markdown(prof)
    assert 'filename="4AM_ACME.profile.json"' in j.headers["content-disposition"] and 'filename="4AM_ACME.profile.md"' in m.headers["content-disposition"]
    assert j.headers["content-type"].startswith("application/json") and m.headers["content-type"].startswith("text/markdown")


def test_stale_report_is_still_exportable_and_viewable(world):
    c = client_for()
    _post(c)
    f = world.inbound / "Acme/views/1VR_RAW.json"
    f.write_text(f.read_text(encoding="utf-8").replace('"A"', '"A9"', 1), encoding="utf-8")
    html = c.get("/catalog/flow/assessment?flow=acme-4am", headers=HX).text
    assert "Assessment desactualizado — El modelo cambió desde la última evaluación." in html
    assert "Regenerar assessment" in html and "Resumen" in html            # banner + informe anterior + acción
    assert c.get("/catalog/flow/assessment.json?flow=acme-4am").status_code == 200


# ── 4–8 estados en el tile (y los estados en el informe) ────────────────────
def test_tile_states_follow_the_contract(world, monkeypatch):
    c = client_for()

    def tile():
        return _block(c.get("/catalog/domain?dominio=Dom").text, "acme-4am")
    t = tile()                                                                 # NOT_YET_ASSESSED
    assert "Assessment de modelado" in t and "Sin assessment" in t and "Generar informe" in t and "hx-post" in t and "Cobertura" not in t
    # el bloque se refresca a sí mismo: sin hx-target="this" heredaría el del <li> (#catalog-detail-slot) y reemplazaría el panel de detalle
    assert 'hx-target="this"' in t and 'hx-swap="outerHTML"' in t and "assessment-changed" in t and "stopPropagation" in t
    assert _post(c).status_code == 200
    t = tile()                                                                 # CURRENT
    assert "Assessment vigente" in t and "Ver informe" in t and "hx-get" in t and re.search(r"Cobertura \d+\.\d%", t)
    assert re.search(r"Confianza (Alta|Media|Baja)", t) and re.search(r"(\d+ revisi(ón|ones)|Sin revisiones)", t)
    for absent in ("Inclusiva", "inclusive", "signal-rules", "assessment-rules", "confidence-cuts", "UTC", "2026", "preguntas de contexto", "Generado"):
        assert absent not in t, absent
    f = world.inbound / "Acme/views/1VR_RAW.json"
    f.write_text(f.read_text(encoding="utf-8").replace('"A"', '"A8"', 1), encoding="utf-8")
    t = tile()                                                                 # STALE
    assert "Assessment desactualizado" in t and "Ver informe" in t and "Cobertura" in t
    monkeypatch.setattr(svc, "_engine_assess", lambda *a: (_ for _ in ()).throw(RuntimeError("interno")))
    _post(c)
    t = tile()                                                                 # FAILED (conserva el último informe, pero el tile pide reintentar)
    assert "No se pudo generar" in t and "Reintentar" in t and "Cobertura" not in t and "interno" not in t and "RuntimeError" not in t
    na = _block(c.get("/catalog/domain?dominio=Dom").text, "solo-na")          # NOT_APPLICABLE
    assert "No aplica" in na and "no contiene un modelo analítico terminal" in na and "<button" not in na


def test_failed_report_view_offers_retry_and_shows_no_traceback(world, monkeypatch):
    c = client_for()
    monkeypatch.setattr(svc, "_engine_assess", lambda *a: (_ for _ in ()).throw(RuntimeError("traza-interna")))
    html = _post(c).text
    assert "No se pudo generar el assessment" in html and "Reintentar" in html and "traza-interna" not in html and "Traceback" not in html


def test_not_applicable_report_shows_reason_and_no_generation_action(world):
    html = client_for().get("/catalog/flow/assessment?flow=solo-na", headers=HX).text
    assert "No aplica" in html and "no contiene un modelo analítico terminal" in html and "hx-post" not in html


def test_incompatible_schema_is_never_rendered_and_offers_regeneration(world):
    c = client_for()
    _post(c)
    env = dict(svc._load_envelope("Acme", "4AM_ACME"))
    env["assessment_schema_version"] = "modeling_assessment.assessment/v0"
    svc._store_envelope(env)
    html = c.get("/catalog/flow/assessment?flow=acme-4am", headers=HX).text
    assert "Informe no compatible" in html and "Regenerar assessment" in html and "Resumen" not in html and "hx-post" in html
    assert c.get("/catalog/flow/assessment.json?flow=acme-4am").status_code == 404
    assert "Resumen" in _post(c).text


def test_low_confidence_or_partial_evidence_are_not_errors(world):
    html = _post(client_for()).text
    assert "asr-state--error" not in html and "role=\"alert\"" not in html
    assert "Parcialmente evaluado" in html or "No evaluado" in html or "evaluada" in html      # la evidencia parcial se muestra como información


# ── 17. autorización ────────────────────────────────────────────────────────
def test_unauthenticated_requests_are_rejected(world):
    a = anon()
    assert a.get("/catalog/flow/assessment?flow=acme-4am", headers=HX).status_code == 401
    assert a.get("/catalog/flow/assessment?flow=acme-4am", follow_redirects=False).status_code == 307      # navegación directa → login
    assert a.post("/catalog/flow/assessment?flow=acme-4am", headers={**ORIGIN, **HX}).status_code == 401
    assert a.get("/catalog/flow/assessment.json?flow=acme-4am").status_code == 401
    assert a.get("/catalog/flow/assessment.md?flow=acme-4am").status_code == 401
    assert not _cache_files(world)


def test_post_requires_same_origin(world):
    c = client_for()
    assert c.post("/catalog/flow/assessment?flow=acme-4am", headers=HX).status_code == 403
    assert c.post("/catalog/flow/assessment?flow=acme-4am", headers={"Origin": "https://evil.example"}).status_code == 403
    assert not _cache_files(world)


def test_unknown_flow_is_404(world):
    c = client_for()
    assert c.get("/catalog/flow/assessment?flow=nope", headers=HX).status_code == 404
    assert _post(c, "nope").status_code == 404


def test_readonly_academy_user_can_view_and_generate_by_policy(world, monkeypatch):
    """`readonly` = tier academy = IA apagada (sin asistente ni análisis de sección). El assessment es determinista y
    sin LLM, y `/catalog/regenerate` ya acepta a cualquier usuario logueado: el perfil readonly PUEDE ver y generar."""
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "otro@d.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "ro@d.com")
    assert auth_service.user_tier("ro@d.com") == "academy"                       # el usuario ES readonly de verdad
    c = client_for("ro@d.com")
    assert c.get("/catalog/flow/assessment?flow=acme-4am", headers=HX).status_code == 200
    r = _post(c)
    assert r.status_code == 200 and "Assessment vigente" in r.text
    assert c.get("/catalog/flow/assessment.json?flow=acme-4am").status_code == 200


# ── 18. el veredicto/apto legacy no cambia ──────────────────────────────────
def test_legacy_tile_verdict_and_apto_are_unchanged_by_the_assessment_block(world):
    c = client_for()
    with_none = c.get("/catalog/domain?dominio=Dom").text
    _post(c)
    with_block = c.get("/catalog/domain?dominio=Dom").text
    strip = lambda h: re.sub(r'\s*<div class="asm-tile.*?</div>', "", h, flags=re.S)      # noqa: E731
    assert strip(with_none) == strip(with_block)
    assert "catalog-card__verdict--plena" in with_block
    assert '<p class="catalog-card__apto">Referencia reutilizable</p>' in with_block and '<p class="catalog-card__apto">Referencia reutilizable</p>' in with_none
    assert re.search(r'<span class="catalog-card__verdict catalog-card__verdict--plena">\s*\S+\s*</span>', with_block)
    # el bloque está en CADA tile (también en el NOT_APPLICABLE) y rotulado aparte del veredicto
    assert with_block.count("Assessment de modelado") == 2


def test_assessment_failure_never_breaks_the_legacy_tile_list(world, monkeypatch):
    monkeypatch.setattr(svc, "statuses_for_entries", lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    r = client_for().get("/catalog/domain?dominio=Dom")
    assert r.status_code == 200 and "catalog-card__verdict--plena" in r.text and "Assessment de modelado" not in r.text


# ── 16. foco de linaje ──────────────────────────────────────────────────────
def _node(name, layer, upstreams=(), assoc=(), is_factcube=False):
    return {"name": name, "num": 0, "slug": name.lower(), "title": name, "label": name, "layer": layer, "is_factcube": is_factcube,
            "upstreams": list(upstreams), "assoc_upstreams": list(assoc)}


def _svg(focus=None):
    nodes = [_node("SRC", 0), _node("DIM", 1, upstreams=["SRC"]), _node("OUT", 3, assoc=["DIM"], is_factcube=True)]
    keep = main._catalog_flow_keep(nodes, True)
    return main._catalog_lineage_svg(nodes, True, keep, focus=focus)


def test_lineage_focus_marks_only_the_requested_nodes_and_keeps_the_graph_identical():
    base = _svg()
    one = _svg(["DIM"])
    two = _svg(["SRC", "OUT"])
    assert base == _svg([]) == _svg(None) and "ln-node-focus" not in base
    assert one.count("ln-node-focus") == 1 and re.search(r'class="ln-node-a ln-node-focus"><title>DIM', one)
    assert two.count("ln-node-focus") == 2
    # el grafo no cambia: mismo SVG una vez retirada la clase de foco
    assert one.replace(" ln-node-focus", "") == base == two.replace(" ln-node-focus", "")


def test_lineage_focus_absent_node_degrades_safely():
    svg = _svg(["NO_EXISTE", "DIM"])
    assert svg.count("ln-node-focus") == 1 and "NO_EXISTE" not in svg


def _build_flow(base, flow_dir, cliente, cube):
    d = base / cliente / flow_dir
    arts = ["1TR_SAP_MD_X", "2VD_MD_X", cube]
    vista = lambda capa, up=(), assoc=(), pattern="DATA_STRUCTURE", cat="null": (   # noqa: E731
        "---\nauto:\n  tipo: vista\n" f"  capa: {capa}\n  modeling_pattern: {pattern}\n  data_category: {cat}\n  origen_remoto: null\n"
        + (("  upstreams:\n" + "".join(f"  - {u}\n" for u in up)) if up else "  upstreams: []\n")
        + (("  upstreams_asociacion:\n" + "".join(f"  - {u}\n" for u in assoc)) if assoc else "  upstreams_asociacion: []\n")
        + "---\n\n# Vista\n\nCuerpo.\n")
    (d / "vistas").mkdir(parents=True)
    (d / f"flujo_{flow_dir}.md").write_text("---\nauto:\n  tipo: flujo\n  dominio: Ventas\n  artefactos:\n" + "".join(f"  - {a}\n" for a in arts)
                                            + f"manual:\n  cliente: {cliente}\n---\n\n# Flujo\n\n## Hallazgos\n\n- nota\n", encoding="utf-8")
    (d / "vistas" / "1TR_SAP_MD_X.md").write_text(vista("Ingesta"), encoding="utf-8")
    (d / "vistas" / "2VD_MD_X.md").write_text(vista("Armonización", up=["1TR_SAP_MD_X"], pattern="ANALYTICAL_DIMENSION"), encoding="utf-8")
    (d / "vistas" / f"{cube}.md").write_text(vista("Consumo", assoc=["2VD_MD_X"], pattern="ANALYTICAL_CUBE", cat="CUBE"), encoding="utf-8")


@pytest.fixture()
def flow_catalog(tmp_path, monkeypatch):
    _build_flow(tmp_path, "Ventas", "Cli", "4AM_SND_ALPHA")
    entries = cs.load_catalog(str(tmp_path))
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))
    return entries[0]


def test_flow_view_focus_reports_present_and_missing_and_enables_associations_when_needed(flow_catalog):
    e = flow_catalog
    out: dict = {}
    _s, svg, show_assoc, _ctx = main._catalog_flow_view(e, False, "datasphere_core", focus=["2VD_MD_X", "NO_EXISTE"], focus_out=out)
    assert [p["name"] for p in out["present"]] == ["2VD_MD_X"] and out["present"][0]["slug"] and out["missing"] == ["NO_EXISTE"]
    assert svg.count("ln-node-focus") == 1
    # sin foco la vista es idéntica a la previa
    plain = main._catalog_flow_view(e, None, "datasphere_core")
    assert plain[1] == main._catalog_flow_view(e, None, "datasphere_core", focus=[])[1]


def test_flow_route_focus_param_highlights_nodes_and_lists_missing_ones(flow_catalog):
    c = client_for()
    eid = flow_catalog.entry_id
    base = f"/catalog/flow?flow={eid}&pack=datasphere_core"
    plain = c.get(base, headers=HX).text
    assert "lineage-focus" not in plain and "ln-node-focus" not in plain
    r = c.get(base + "&focus=2VD_MD_X,4AM_SND_ALPHA,NO_EXISTE", headers=HX)
    assert r.status_code == 200
    assert r.text.count("ln-node-focus") == 2 and "lineage-focus" in r.text
    assert "No visible en el diagrama actual" in r.text and "NO_EXISTE" in r.text
    assert "Volver al informe" in r.text and "Quitar enfoque" in r.text
    assert f"focus=" in r.text and "assoc=" in r.text                     # el toggle de asociaciones conserva el foco
    # las anclas de los chips reutilizan el scroll existente (#slug)
    assert re.search(r'class="lineage-focus__chip" href="#[^"]+"', r.text)
    frag = c.get(base + "&focus=2VD_MD_X&frag=lineage", headers=HX)
    assert frag.status_code == 200 and "ln-node-focus" in frag.text


def test_flow_route_without_focus_keeps_calling_the_view_with_the_legacy_signature(world, monkeypatch):
    seen = {}

    def stub(entry, show_assoc=None, pack=""):                           # firma legacy: no acepta focus/focus_out
        seen["called"] = True
        return [{"slug": "m", "html": "x"}], "", bool(show_assoc), False
    monkeypatch.setattr(main, "_catalog_flow_view", stub)
    r = client_for().get("/catalog/flow?flow=acme-4am&pack=datasphere_core", headers=HX)
    assert r.status_code == 200 and seen["called"]


# ── varios ──────────────────────────────────────────────────────────────────
def test_catalog_cache_invalidation_also_resets_the_pool_index(world, monkeypatch):
    entries_fn = lambda: (cs, world.entries)                    # noqa: E731
    entries_fn.cache_clear = lambda: None                        # el real es un @lru_cache; acá solo se ejercita el hook
    monkeypatch.setattr(main, "_catalog_entries", entries_fn)
    svc.status_for_entry(world.entries[0])
    assert svc._index_memo
    main._invalidate_catalog_cache()
    assert not svc._index_memo


def test_new_routes_are_declared_in_the_route_contract_snapshot():
    snap = set(json.loads((REPO / "tests/route_surface.json").read_text(encoding="utf-8")))
    assert {"GET /catalog/flow/assessment", "POST /catalog/flow/assessment",
            "GET /catalog/flow/assessment.json", "GET /catalog/flow/assessment.md"} <= snap
    assert {s for s in snap if "/catalog/flow/assessment" in s and not s.startswith(("GET ", "POST "))} == set()


def test_presenter_parse_focus_bounds_and_dedupes():
    assert presenter.parse_focus("A, B,A,,C") == ["A", "B", "C"]
    assert presenter.parse_focus("") == [] and presenter.parse_focus("x" * 500) == []
    assert len(presenter.parse_focus(",".join(f"N{i}" for i in range(500)))) == presenter.MAX_FOCUS_NAMES


def test_new_assessment_sources_and_tests_contain_no_stray_control_characters():
    # guarda permanente: un `\b` mal escapado dentro de un regex se vuelve un backspace literal y deja el test vacuo
    files = ["webapp/services/assessment_service.py", "webapp/services/assessment_presenter.py", "webapp/routers/catalog_assessment.py",
             "webapp/templates/partials/catalog_assessment_report.html", "webapp/templates/partials/catalog_assessment_tile.html",
             "tests/webapp/test_assessment_service.py", "tests/webapp/test_catalog_assessment_routes.py"]
    for rel in files:
        text = (REPO / rel).read_text(encoding="utf-8")
        bad = sorted({hex(ord(ch)) for ch in text if ord(ch) < 32 and ch not in "\t\n\r"})
        assert not bad, (rel, bad)
