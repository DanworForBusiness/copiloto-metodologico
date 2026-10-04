"""
test_catalog_assessment_routes.py — rutas, cabecera del flujo, informe estructurado y foco de linaje (Catalog Assessment Report V1).

Fixtures SINTÉTICAS (pool `write_inbound`, catálogo de prueba en tmp). Nunca datos reales.

Contratos que fija:
  * GET jamás genera ni escribe (un miss es NOT_YET_ASSESSED); POST genera y devuelve el informe estructurado;
  * acceso: sesión obligatoria; POST exige mismo origen; `readonly` (academy = sin IA) PUEDE ver y generar
    (el Catálogo es determinista/sin tokens y `/catalog/regenerate` ya acepta a cualquier usuario logueado);
  * la UI se renderiza desde el JSON (sin |safe, sin Markdown→HTML); el Markdown exportado es `render_markdown(perfil)`;
  * el tile del listado NO lleva assessment; el estado vive en la cabecera del detalle del flujo (badge + métricas del MISMO
    perfil canónico + acción tipo `.support-link`) y NO altera el veredicto/apto legacy;
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


def _detail(c, monkeypatch, flow="acme-4am") -> str:
    """Cabecera del detalle del flujo (HTML del hero). El cuerpo del flujo se stubea: acá solo importa la cabecera."""
    monkeypatch.setattr(main, "_catalog_flow_view", lambda entry, show_assoc=None, pack="": ([{"slug": "m", "html": "x"}], "", bool(show_assoc), False))
    r = c.get(f"/catalog/flow?flow={flow}&pack=datasphere_core", headers=HX)
    assert r.status_code == 200
    m = re.search(r'<section class="module-hero.*?</section>', r.text, re.S)
    assert m, "no hay hero en el detalle del flujo"
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
    assert "Generar informe" in _detail(c, monkeypatch) and not _cache_files(world)       # la cabecera tampoco genera
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
    assert "HX-Trigger" not in r.headers                                  # nada escuchaba el evento del tile: se retiró
    for needle in ("Assessment vigente", "Resumen", "Observaciones", "Revisión requerida", "No evaluado / Limitaciones de evidencia",
                   "Evidencia / Detalle técnico", "Arquitectura &amp; Modelado", "Convención de nombres", "Fuente &amp; Extracción", "Cobertura estricta", "Cobertura inclusiva", "Exportar Markdown", "Exportar JSON", "4AM_ACME"):
        assert needle in r.text, needle
    ma_profile.validate_assessment(svc.status_for_entry(world.entries[0]).profile)
    again = client_for().get("/catalog/flow/assessment?flow=acme-4am", headers=HX)
    assert "Assessment vigente" in again.text


def test_report_shows_findings_with_why_context_and_lineage_links(world):
    html = _post(client_for()).text
    assert "Acceso directo a tabla" in html or "ACCESO DIRECTO A TABLA" in html.upper()
    assert "Detalle del hallazgo" in html and "Pregunta para el proyecto" in html and "Ver en linaje" in html and "/catalog/flow?" in html and "focus=" in html


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
    for name in ("catalog_assessment_report.html",):
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
def test_no_maturity_score_or_band_vocabulary_and_human_text_is_spanish(world, monkeypatch):
    c = client_for()
    report = _post(c).text
    tile = _detail(c, monkeypatch)
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


# ── 4–8 estados en la cabecera del detalle (y los estados en el informe) ────
def test_detail_header_states_follow_the_contract(world, monkeypatch):
    c = client_for()

    def hdr():
        return _detail(c, monkeypatch)
    h = hdr()                                                                  # NOT_YET_ASSESSED
    assert "Sin assessment" in h and "Generar informe" in h and "hx-post" in h and "Cobertura" not in h
    assert 'class="support-link"' in h and "support-links heading-supports" in h and "<button" not in h
    assert _post(c).status_code == 200
    h = hdr()                                                                  # CURRENT
    assert "Assessment vigente" in h and "Ver Assessment" in h and "Ver informe" not in h and "hx-get" in h and "hx-post" not in h and re.search(r"Cobertura \d+\.\d%", h)
    assert re.search(r"Confianza (Alta|Media|Baja)", h) and re.search(r"(\d+ revisi(ón|ones)|Sin revisiones)", h)
    for absent in ("Inclusiva", "inclusive", "signal-rules", "assessment-rules", "confidence-cuts", "UTC", "2026", "preguntas de contexto", "Generado"):
        assert absent not in h, absent
    f = world.inbound / "Acme/views/1VR_RAW.json"
    f.write_text(f.read_text(encoding="utf-8").replace('"A"', '"A8"', 1), encoding="utf-8")
    h = hdr()                                                                  # STALE (la regeneración vive dentro del informe)
    assert "Assessment desactualizado" in h and "Ver Assessment" in h and "Cobertura" in h and "hx-post" not in h
    monkeypatch.setattr(svc, "_engine_assess", lambda *a: (_ for _ in ()).throw(RuntimeError("interno")))
    _post(c)
    h = hdr()                                                                  # FAILED (conserva el último informe, pero la cabecera pide reintentar)
    assert "Assessment no disponible" in h and "Reintentar" in h and "Cobertura" not in h and "interno" not in h and "RuntimeError" not in h
    na = _detail(c, monkeypatch, "solo-na")                                    # NOT_APPLICABLE
    assert "No aplica" in na and "no contiene un modelo analítico terminal" in na and "Ver Assessment" not in na and "<button" not in na
    assert na.count('class="support-link"') == 1 and "Ver Informe de Cliente" in na      # único botón: el consolidado del cliente (no hay acción de assessment)


def test_detail_header_sits_right_below_the_title_with_the_action_in_the_support_link_slot(world, monkeypatch):
    c = client_for()
    _post(c)
    h = _detail(c, monkeypatch)
    assert h.index("support-links heading-supports") < h.index("<h1>") < h.index("Assessment vigente")   # acción arriba a la derecha; estado justo bajo el título
    assert "asm-hero-has-support" in h


def test_header_values_come_from_the_same_cached_profile_as_report_and_exports(world, monkeypatch):
    """Una sola fuente: el JSON canónico cacheado. Se adultera ese JSON y cabecera, informe y exportes lo reflejan igual."""
    c = client_for()
    _post(c)
    env = json.loads(json.dumps(svc._load_envelope("Acme", "4AM_ACME")))
    prof = env["profile_json"]
    prof["summary"]["coverage"]["strict"] = 0.4321
    env["assessment_artifact_signature"] = svc._artifact_signature(prof)
    svc._store_envelope(env)
    expected = presenter.P.percent_text(0.4321)
    header = _detail(c, monkeypatch)
    report = c.get("/catalog/flow/assessment?flow=acme-4am", headers=HX).text
    flat = lambda h: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))      # noqa: E731
    assert f"Cobertura {expected}" in flat(header) and f"Cobertura estricta {expected}" in flat(report)
    assert re.search(rf'<span class="activity-kpi__value">{re.escape(expected)}</span><span class="activity-kpi__label">Cobertura estricta</span>', report)   # KPI del informe, mismo perfil
    e0 = world.entries[0]
    status = svc.status_for_entry(e0)
    hv = presenter.header_view(status, e0, "datasphere_core")
    rv = presenter.report_view(status, e0, "datasphere_core")
    assert hv["coverage"] == expected == rv["summary"]["coverage_strict"]
    n = sum(g["occurrence_count"] for g in status.profile["review_required"])
    assert hv["confidence"].upper() == rv["summary"]["confidence"].upper() and rv["summary"]["review_occurrences"] == n
    assert hv["reviews"] == ("Sin revisiones" if n == 0 else f"{n} revisión" if n == 1 else f"{n} revisiones")
    assert json.loads(c.get("/catalog/flow/assessment.json?flow=acme-4am").text)["summary"]["coverage"]["strict"] == 0.4321
    assert svc.export_markdown(status) == render_markdown(status.profile)


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


# ── 18. el tile del listado vuelve a su rol mínimo; el veredicto/apto legacy no cambia ──
def test_catalog_list_tile_has_no_assessment_block_and_legacy_content_is_unchanged(world):
    c = client_for()
    before = c.get("/catalog/domain?dominio=Dom").text
    _post(c)
    after = c.get("/catalog/domain?dominio=Dom").text
    assert before == after                                          # el assessment no altera el listado en ningún estado
    for html in (before, after):
        for absent in ("Assessment", "asm-", "assessment", "Ver informe", "Generar informe", "Cobertura", "Confianza"):
            assert absent not in html, absent
    assert "catalog-card__verdict--plena" in after
    assert '<p class="catalog-card__apto">Referencia reutilizable</p>' in after
    assert re.search(r'<span class="catalog-card__verdict catalog-card__verdict--plena">\s*\S+\s*</span>', after)
    assert not (REPO / "webapp/templates/partials/catalog_assessment_tile.html").exists()


def test_assessment_failure_never_breaks_the_flow_detail(world, monkeypatch):
    monkeypatch.setattr(svc, "status_for_entry", lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    c = client_for()
    h = _detail(c, monkeypatch)
    assert "<h1>" in h and "catalog-card__verdict--plena" in h and "Assessment" not in h and "boom" not in h
    lst = c.get("/catalog/domain?dominio=Dom")
    assert lst.status_code == 200 and "catalog-card__verdict--plena" in lst.text


def test_assessment_css_reuses_hub_primitives_instead_of_duplicating_them():
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8")
    block = css[css.index("Assessment de modelado en el Catálogo"):css.index("Foco de linaje (server-side")]
    # tipografía/tablas/listas/secciones/detalles/callouts estándar: los primitivos del Hub, no réplicas con nombre propio
    for dup in (".asr-table", ".asr-section", ".asr-list", ".asr-note", ".asr-label", ".asr-area", ".asr-tech", ".asr-state", ".asr-measure",
                ".asr-callout", ".asr-index", ".asr-disclaimer", ".asr-dl", ".asr-occ", ".asm-tile", ".asm-btn", ".asr-group", ".asr-meta", ".asr-count",
                ".asr-lineage", ".asm-busy"):
        assert dup not in block, dup
    tpl = (REPO / "webapp/templates/partials/catalog_assessment_report.html").read_text(encoding="utf-8")
    for primitive in ("methodology", "content-section", "content-heading", "section-subtitle", "callout", "dashboard-section", "activity-kpi", "support-link", "catalog-card__verdict"):
        assert primitive in tpl, primitive
    assert "section-index" not in tpl and "content-collapsible" not in tpl and "En este informe" not in tpl


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
             "webapp/templates/partials/catalog_assessment_report.html",
             "tests/webapp/test_assessment_service.py", "tests/webapp/test_catalog_assessment_routes.py"]
    for rel in files:
        text = (REPO / rel).read_text(encoding="utf-8")
        bad = sorted({hex(ord(ch)) for ch in text if ord(ch) < 32 and ch not in "\t\n\r"})
        assert not bad, (rel, bad)


# ── título coherente con el detalle del flujo + "Volver" a quien invocó ─────
def test_report_title_matches_the_flow_detail_heading_format_description_dot_technical_id(world, tmp_path, monkeypatch):
    d = tmp_path / "cat" / "Acme" / "Dom"
    (d / "vistas").mkdir(parents=True)
    (d / "vistas" / "4AM_ACME.md").write_text("---\nauto:\n  tipo: vista\n  label: Modelo analítico - Acme\n---\n\n# V\n", encoding="utf-8")
    (d / "vistas" / "SIN_LABEL.md").write_text("---\nauto:\n  tipo: vista\n  label: '{i18n>X@ENDUSERTEXT.LABEL}'\n---\n\n# V\n", encoding="utf-8")
    e = cs.CatalogEntry(path=str(d / "flujo_Dom.md"), cliente="Acme", dominio="Dom", veredicto="plena", apto="x", entry_id="e", artefactos=[])
    assert catalog_reads.artifact_title(e, "4AM_ACME") == ("Modelo analítico - Acme", "4AM_ACME")
    assert catalog_reads.artifact_title(e, "SIN_LABEL") == ("SIN_LABEL", "SIN_LABEL")          # placeholder i18n → nombre técnico, como el detalle
    assert catalog_reads.artifact_title(e, "NO_EXISTE") == ("NO_EXISTE", "NO_EXISTE")           # sin vista: fail-soft
    assert main._catalog_clean_label is catalog_reads.catalog_clean_label                         # una sola regla compartida con el heading del detalle
    c = client_for()
    monkeypatch.setattr(catalog_reads, "artifact_title", lambda entry, name: ("Modelo analítico - Acme", name))
    html = _post(c).text
    assert re.search(r'<h1 class="asr-title"><span class="tone-black">Acme · </span><span class="tone-gold">4AM_ACME</span></h1>', html)          # sin el prefijo "Modelo analítico - "
    monkeypatch.setattr(catalog_reads, "artifact_title", lambda entry, name: (name, name))
    assert re.search(r'<span class="tone-black">4AM_ACME · </span><span class="tone-gold">4AM_ACME</span>', _post(c).text)                       # fallback: nombre técnico


def test_report_back_returns_to_the_flow_detail_and_support_links_use_the_hub_pattern(world):
    c = client_for()
    report = _post(c).text
    back = re.search(r'<a class="catalog-back"[^>]*>', report).group(0)
    assert "hx-get" in back and "/catalog/flow?" in back and "flow=acme-4am" in back and "dwCatalogBack" not in back    # informe → detalle del flujo
    for m in re.finditer(r'<a class="support-link"[^>]*>.*?</a>', report, re.S):
        assert "<span>↗</span>" in m.group(0)                                                                             # mismo patrón visual del {{support}}


def test_focused_lineage_back_returns_to_the_report_and_plain_detail_keeps_the_module_back(flow_catalog):
    c = client_for()
    eid = flow_catalog.entry_id
    base = f"/catalog/flow?flow={eid}&pack=datasphere_core"
    plain = c.get(base, headers=HX).text
    assert "dwCatalogBack" in re.search(r'<a class="catalog-back"[^>]*>', plain).group(0)                                # detalle normal → módulo/listado
    focused = re.search(r'<a class="catalog-back"[^>]*>', c.get(base + "&focus=2VD_MD_X", headers=HX).text).group(0)
    assert "hx-get" in focused and "/catalog/flow/assessment?" in focused and "dwCatalogBack" not in focused             # linaje con foco → informe


def test_report_sections_follow_the_governed_markdown_hierarchy_without_a_generic_areas_section(world):
    """Sin sección "Áreas": tres secciones de primer nivel (como los `##` del Markdown gobernado). Solo Arquitectura & Modelado
    tiene subáreas reales (4 disclosures del CRM); Convención de nombres y Fuente & Extracción renderizan su contenido directo.
    La superficie la resuelve `.dashboard-section`; el CSS del assessment no lleva ningún literal de color."""
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8")
    block = css[css.index("Assessment de modelado en el Catálogo"):]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", block)
    assert "content-collapsible--flat" not in (REPO / "webapp/static/content.css").read_text(encoding="utf-8")
    c = client_for()
    html = _post(c).text
    heads = re.findall(r'<span class="section-eyebrow">\[(\d+)\]</span><h2>([^<]+)</h2>', html)
    assert [t for _n, t in heads] == ["Resumen", "Arquitectura &amp; Modelado", "Convención de nombres", "Fuente &amp; Extracción", "Observaciones",
                                      "Revisión requerida", "No evaluado / Limitaciones de evidencia", "Evidencia / Detalle técnico"]
    assert [n for n, _t in heads] == [f"{i:02d}" for i in range(1, 9)]                       # numeración continua generada por la plantilla
    assert 'id="asr-areas"' not in html and "<h2>Áreas</h2>" not in html
    arch = html[html.index('id="asr-m-architecture_modeling"'):html.index('id="asr-m-naming"')]
    for title in ("Modelo analítico", "Propagación", "Armonización", "Ingesta"):
        assert f'<span class="dashboard-section__title">{title}</span>' in arch
    assert len(re.findall(r'<details class="dashboard-section" id="asr-a-', arch)) == 4
    for sec in ("asr-m-naming", "asr-m-source_extraction"):
        part = html[html.index(f'id="{sec}"'):]
        part = part[:part.index("</section>")]
        assert '<details class="dashboard-section"' not in part and "dashboard-section__title" not in part      # sin disclosure de un único hijo
        assert 'class="catalog-card__verdict' in part and "<h3>Evaluación de reglas</h3>" in part
    assert "content-collapsible" not in html
    # alineación con el Markdown gobernado: mismos `##` de primer nivel (con su rotulado) y las 4 subáreas como `###`
    md = render_markdown(svc.status_for_entry(world.entries[0]).profile)
    h2 = [l[3:] for l in md.splitlines() if l.startswith("## ")]
    assert h2[:4] == ["Resumen", "Arquitectura / Modelado", "Convención de nombres", "Fuente / Extracción"] and "Áreas" not in h2
    assert [l[4:] for l in md.splitlines() if l.startswith("### ")][:4] == ["Modelo analítico", "Propagación", "Armonización", "Ingesta"]


def test_report_leads_with_kpis_in_the_crm_disclosure_and_hero_text_is_simplified(world):
    html = _post(client_for()).text
    assert "Assessment de Modelado &amp; Desarrollo · Acme" in html and "Assessment de modelado ·" not in html
    assert "Ver flujo en el catálogo" not in html and "Nota metodológica" not in html and "En este informe" not in html
    first = html[html.index('<details class="dashboard-section" open>'):]
    assert first.index("Indicadores del assessment") < first.index("activity-kpi-grid") < first.index('id="asr-resumen"')
    assert html.index('<details class="dashboard-section" open>') < html.index('id="asr-resumen"')       # KPIs = primer contenido bajo el hero
    kpis = re.findall(r'<span class="activity-kpi__value">([^<]*)</span><span class="activity-kpi__label">([^<]*)</span>', first.split('id="asr-resumen"')[0])
    assert [l for _v, l in kpis] == ["Cobertura estricta", "Confianza", "Revisiones requeridas", "Contexto abierto"]
    prof = svc.status_for_entry(world.entries[0]).profile
    vals = dict((l, v) for v, l in kpis)
    assert vals["Cobertura estricta"] == presenter.P.percent_text(prof["summary"]["coverage"]["strict"])
    assert vals["Revisiones requeridas"] == str(sum(g["occurrence_count"] for g in prof["review_required"]))
    assert vals["Contexto abierto"] == str(prof["summary"]["context_required"]["open_questions"])
    assert "Perfil de evidencia, cobertura y confianza" in first.split("</blockquote>")[0]                # la nota metodológica vive en el subtítulo de Resumen
    assert html.count("Perfil de evidencia, cobertura y confianza") == 1


def test_findings_are_semantic_headings_with_progressive_disclosure_not_callouts(world):
    html = _post(client_for()).text
    assert not re.search(r'<aside class="callout[^"]*" id="asr-g-', html)
    arts = re.findall(r'<article id="asr-g-[^"]+">\s*<h3>[^<]+</h3>.*?</article>', html, re.S)
    assert arts
    for art in arts:
        assert '<details class="dashboard-section">' in art and "Detalle del hallazgo" in art
        visible = art.split('<details class="dashboard-section">')[0]
        assert "Pregunta para el proyecto" not in visible and "Razones legítimas" not in visible               # la historia completa va bajo demanda


def test_facts_have_a_reading_hierarchy_headline_kpis_then_supporting_tables_and_nothing_is_lost(world):
    html = _post(client_for()).text
    prof = svc.status_for_entry(world.entries[0]).profile
    for a in prof["areas"]:
        v = presenter._facts_view(a["id"], a["highlights"])
        assert len(v["headline"]) <= 4                                                                # titulares: pocos y de síntesis
        shown = {m["label"] for m in v["headline"]}
        for g in v["groups"]:
            shown |= {p["label"] for p in g["properties"]} | {t["title"] for t in g["tables"]} | {g["title"]}
        assert all(h["label"] in shown for h in a["highlights"]), a["id"]                              # ningún hecho se pierde
        # los valores numéricos escalares siguen presentes (titular o tabla de propiedades)
        for h in a["highlights"]:
            if isinstance(h["value"], int) and not h.get("breakdown"):
                assert any(m["label"] == h["label"] and m["value"] == h["value"] for m in v["headline"]) or \
                       any(p["label"] == h["label"] and p["value"] == h["value"] for g in v["groups"] for p in g["properties"])
    am = presenter._facts_view("ANALYTIC_MODEL", next(a for a in prof["areas"] if a["id"] == "ANALYTIC_MODEL")["highlights"])
    assert [m["label"] for m in am["headline"]] == ["Campos expuestos", "Medidas", "Asociaciones declaradas"]
    assert [g["title"] for g in am["groups"]] == ["Composición del modelo", "Cierre semántico del modelo analítico (artefactos)", "Linaje y asociaciones"]
    sec = html[html.index('id="asr-m-architecture_modeling"'):html.index('id="asr-observaciones"')]
    assert 'class="activity-kpi-grid asm-facts"' in sec and "<th>Hecho</th><th>Valor</th>" in sec
    assert not re.search(r"<li>[^<]*: \d+</li>", sec)                                                  # sin pared de bullets "Hecho: n"
    nam = html[html.index('id="asr-m-naming"'):html.index('id="asr-m-source_extraction"')]
    assert len(re.findall(r'class="activity-kpi"', nam)) == 3                                           # Convención de nombres: 3 titulares, no un muro de tiles
    # tipos de valor
    view = presenter._facts_view("SIN_LAYOUT", [
        {"id": "n", "label": "n", "value": 3}, {"id": "c", "label": "c", "value": 28, "breakdown": [{"name": "a", "count": 4}, {"name": "b", "count": 24}]},
        {"id": "l", "label": "l", "value": ["X", "Y"]}, {"id": "d", "label": "d", "value": [{"name": "A", "applicable": 2, "compliant": 1}]},
        {"id": "m", "label": "m", "value": {"observed": 0, "unavailable": 14}}])
    assert view["headline"] == [{"label": "n", "value": 3}]
    props = [p for g in view["groups"] for p in g["properties"]]
    assert {"label": "l", "value": None, "names": ["X", "Y"]} in props
    cols = {t["title"]: t["columns"] for g in view["groups"] for t in g["tables"]}
    assert cols["c"] == ["Concepto", "Cantidad"] and cols["d"] == ["Nombre", "Aplican", "Cumplen"] and cols["m"] == ["Concepto", "Valor"]


def test_rule_evaluation_is_a_table_not_bullets(world):
    html = _post(client_for()).text
    assert html.count("<th>Regla</th><th>Resultado</th>") >= 6                                          # una por área
    sec = html[html.index('id="asr-m-architecture_modeling"'):html.index('id="asr-observaciones"')]
    assert not re.search(r"<li>[^<]*(ausencia|patrón|evaluad)[^<]*</li>", sec.split("Hallazgos en esta área")[0])


def test_contexto_adicional_uses_the_shared_punch_markup_and_question_links_use_the_hub_content_link(world):
    html = _post(client_for()).text
    det = re.search(r'<details class="dashboard-section">\s*<summary[^>]*>.*?Contexto adicional del proyecto.*?</details>', html, re.S)
    if det:                                                                                          # solo si hay preguntas independientes
        assert re.search(r'<aside class="callout callout--punch"><div class="callout__head"><span class="callout__icon">[^<]+</span><strong class="callout__title">[^<]+</strong></div><div class="callout__body"><p>Preguntas abiertas que no están respaldadas por un hallazgo', det.group(0))
        assert det.group(0).index("callout--punch") < det.group(0).index("<ul>")
    sec = html[html.index("Preguntas que solo el proyecto puede responder"):html.index('id="asr-m-architecture_modeling"')]
    links = re.findall(r'<td><a ([^>]*)><code>', sec)
    assert links and all("class=" not in l and 'href="#asr-g-' in l for l in links)                       # mismo enlace de contenido del Hub, sin clase propia
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8")
    assert "sap-note-link" not in css[css.index("Assessment de modelado en el Catálogo"):]                  # sin color de enlace propio


def test_context_questions_are_split_between_finding_backed_table_and_independent_disclosure_and_reconcile(world):
    c = client_for()
    html = _post(c).text
    prof = svc.status_for_entry(world.entries[0]).profile
    ctx = prof["summary"]["context_required"]
    sec = html[html.index("Preguntas que solo el proyecto puede responder"):html.index('id="asr-m-architecture_modeling"')]
    tables = re.findall(r"<table>.*?</table>", sec, re.S)
    assert tables
    backed_rows = 0
    for t in tables:
        assert re.search(r"<th>Pregunta</th><th>(Revisión requerida|Observación)</th><th>Ocurrencias</th>", t)
        assert 'class="catalog-card__verdict' not in t and "—" not in t                           # sin badge repetido por fila ni guiones de relleno
        rows = re.findall(r"<tr><td>.*?</td><td><a href=\"#asr-g-[^\"]+\"><code>[^<]+</code></a></td><td>\d+</td></tr>", t, re.S)
        assert len(rows) == t.count("<tr><td>")
        backed_rows += len(rows)
    detail = re.search(r'<details class="dashboard-section">\s*<summary[^>]*>.*?Contexto adicional del proyecto.*?</details>', sec, re.S)
    n_independent = sum(1 for q in ctx["questions"] if q["occurrence_count"] is None)
    if n_independent:
        assert detail and detail.group(0).count("<li>") == n_independent and "<table" not in detail.group(0)
    else:
        assert not detail
    # reconciliación: KPI "Contexto abierto" = preguntas con hallazgo + preguntas independientes del proyecto
    kpi = int(re.search(r'<span class="activity-kpi__value">(\d+)</span><span class="activity-kpi__label">Contexto abierto', html).group(1))
    assert kpi == ctx["open_questions"] == backed_rows + n_independent == ctx["questions_backed_by_findings"] + ctx["questions_without_findings"]
    view = presenter.report_view(svc.status_for_entry(world.entries[0]), world.entries[0], "datasphere_core")["summary"]
    assert view["context_backed_n"] + view["context_independent_n"] == view["context_open_n"] == kpi


def test_empty_observaciones_uses_the_hub_empty_state_with_no_detectado(world):
    c = client_for()
    _post(c)
    e0 = world.entries[0]
    view = presenter.report_view(svc.status_for_entry(e0), e0, "datasphere_core")
    view["observations"] = []                                                                      # presentación: sin grupos de observación
    html = main.templates.get_template("partials/catalog_assessment_report.html").render(v=view, pack="datasphere_core")
    sec = html[html.index('id="asr-observaciones"'):html.index('id="asr-revision"')]
    assert '<div class="dashboard-empty"><span class="dashboard-empty__label">No detectado</span>' in sec
    assert "No disponible" not in sec and "No aplica" not in sec
    assert "Ver “No evaluado / Limitaciones” para la evidencia que no pudo evaluarse." in sec


def test_finding_detail_uses_structured_tables_with_a_stable_occurrence_schema(world):
    html = _post(client_for()).text
    arts = re.findall(r'<article id="asr-g-[^"]+">.*?</article>', html, re.S)
    assert arts
    for art in arts:
        assert re.search(r"<h[34]>[^<]+</h[34]>", art)                                                    # título = encabezado semántico
        det = art.split('<details class="dashboard-section">')[1]
        assert "<thead><tr><th>Aspecto</th><th>Detalle</th></tr></thead>" in det and "<th>Artefactos</th><th>Detalle</th><th>Acción</th>" in det
        assert "<strong>Pregunta para el proyecto" not in det                                      # sin narrativa suelta
        assert "<td>Descripción</td>" in det and "<td>Unidad de cada ocurrencia</td>" in det
        assert 'class="catalog-card__verdict' not in det                                           # sin badges por fila en el detalle
        assert det.count("<tr><td>") >= 2


def test_clickable_status_badge_matches_the_support_action_and_not_applicable_is_not_a_link(world, monkeypatch):
    c = client_for()
    h0 = _detail(c, monkeypatch)                                                                            # NOT_YET_ASSESSED → badge = generar
    badge = re.search(r'<a class="catalog-card__verdict[^"]*" [^>]*>Sin assessment</a>', h0, re.S)
    assert badge and "hx-post" in badge.group(0) and 'role="button"' in badge.group(0) and "Generar informe" in badge.group(0)
    _post(c)
    h = _detail(c, monkeypatch)                                                                             # CURRENT → badge = Ver Assessment (mismo destino)
    badge = re.search(r'<a class="catalog-card__verdict[^"]*"[^>]*>Assessment vigente</a>', h, re.S).group(0)
    support = re.search(r'<a class="support-link"[^>]*>', h).group(0)
    url = re.search(r'hx-get="([^"]+)"', badge).group(1)
    assert url == re.search(r'hx-get="([^"]+)"', support).group(1) and "Ver Assessment" in badge and "click" not in badge.replace("@click.prevent", "")
    na = _detail(c, monkeypatch, "solo-na")
    assert re.search(r'<span class="catalog-card__verdict[^"]*">No aplica</span>', na) and "<a class=\"catalog-card__verdict" not in na


def test_every_content_link_in_the_report_uses_the_one_hub_content_link_treatment(world):
    """Inventario: enlaces de CONTENIDO = `<a><code>` (oro oscuro de los valores de contenido del Hub dentro del enlace de `.methodology`);
    SUPPORT = `.support-link`; el badge-enlace vive en la cabecera del flujo. Nada en negrita/negro ni clases propias."""
    html = _post(client_for()).text
    anchors = re.findall(r"<a\b([^>]*)>(.*?)</a>", html, re.S)
    assert anchors
    kinds = {"content": 0, "support": 0, "back": 0}
    for attrs, inner in anchors:
        if 'class="support-link"' in attrs:
            kinds["support"] += 1
        elif 'class="catalog-back"' in attrs:
            kinds["back"] += 1
        else:
            assert "class=" not in attrs, attrs                                                       # sin clases propias (ni sap-note-link, ni asr-link)
            assert inner.strip().startswith("<code>") and inner.strip().endswith("</code>"), inner      # mismo tratamiento en TODOS
            assert "<strong>" not in inner and "<b>" not in inner and "style=" not in attrs
            kinds["content"] += 1
    assert kinds["content"] >= 10 and kinds["support"] >= 2 and kinds["back"] == 1
    kinds_of_text = {m for m in re.findall(r"<a href=\"#asr-g-[^\"]+\"><code>([^<]+)</code></a>", html)}
    assert len(kinds_of_text) >= 5                                                                    # preguntas + hallazgos usan el mismo marcado
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8")
    block = css[css.index("Assessment de modelado en el Catálogo"):]
    assert not re.search(r"\.asr-link|\.assessment-link|sap-note-link|reference-text-link", block)       # sin CSS de enlace propio

