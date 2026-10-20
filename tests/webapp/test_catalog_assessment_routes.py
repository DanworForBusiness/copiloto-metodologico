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
    # Gate UX.4 §1: la cabecera es la barra de acciones transversal (← Volver + acciones) + el hero debajo.
    m = re.search(r'<div class="catalog-actionbar".*?</section>', r.text, re.S)
    assert m, "no hay cabecera (barra de acciones + hero) en el detalle del flujo"
    return m.group(0)


def _post(c, flow="acme-4am"):
    return c.post(f"/catalog/flow/assessment?flow={flow}", headers={**ORIGIN, **HX})


def _no_engine(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("el GET no debe ejecutar el motor")
    monkeypatch.setattr(svc, "_engine_assess", boom)


# ── Gate UX.1: pulido de presentación (resultado primero, carga integrada, chips, identidad) ──
def test_ux_generation_has_integrated_loading_and_no_duplicate_floating_label(world):
    html = client_for().get("/catalog/flow/assessment?flow=acme-4am", headers=HX).text         # NOT_YET: muestra el botón "Generar informe"
    assert 'class="support-link asr-generate"' in html and 'hx-disabled-elt="this"' in html     # botón con carga integrada, anti doble-envío
    assert 'asr-generate__spinner' in html and 'asr-generate__label' in html
    assert 'id="asr-busy"' not in html and html.count("Generando informe…") == 1 and "hx-indicator" not in html  # un único label integrado; sin hx-indicator inválido ni label flotante


def test_ux_judgment_is_result_first_with_compact_contract_chips(world):
    html = _post(client_for()).text                                                             # CURRENT -> Juicio persistido visible
    seg = html[html.index('id="asr-juicio"'):html.index("</section>", html.index('id="asr-juicio"'))]
    # Gate UX.4 (reuse): sección del informe con heading del Hub; nivel y contratos = badge canónico `.catalog-card__verdict`;
    # evidencia = tabla nativa. Sin primitivos propios espejados (asr-judgment__/asr-contract/asr-evidence) ni tiles KPI en el detalle.
    assert '<h2>Adopción metodológica</h2>' in seg
    # Gate UX.5: SIN badge de nivel flotante arriba; recorrido = contratos evaluados → tiles → (tablas) → cierre con el tono de `status_tone`.
    assert seg.index("Contratos evaluados") < seg.index('class="catalog-card__verdict')              # lo primero bajo el encabezado son los contratos, no un badge de nivel
    for c in ("Ingesta", "Armonización", "Propagación", "Contrato publicado"):
        assert c in seg
    assert seg.index("Contratos evaluados") < seg.index("activity-kpi-grid") < seg.index("Lectura de la adopción")
    assert "Evidencias favorables" in seg and "Puntos de atención" in seg                          # tiles KPI del Hub (no disclosures)
    assert re.search(r'<aside class="callout callout--(plena|buena|parcial|neutra)"', seg)          # cierre = callout canónico con tono de status_tone
    assert not any(x in seg for x in ("asr-judgment__", "asr-contract", "asr-evidence"))


def test_ux_hero_shows_business_name_primary_and_technical_id_secondary(world, monkeypatch):
    monkeypatch.setattr(catalog_reads, "artifact_title", lambda entry, name: ("Libro mayor · Acme", name))
    html = _post(client_for()).text
    assert re.search(r'<h1 class="asr-title">[^<]+</h1>\s*<p class="asr-subid"><code>4AM_ACME</code></p>', html)  # negocio primario + id técnico secundario


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
    for needle in ("Assessment vigente", "Resumen", "Observaciones", "Casos a revisar", "No evaluado / Limitaciones de evidencia",
                   "Cierre: limitaciones y trazabilidad", "Arquitectura &amp; Modelado", "Gobierno &amp; estándares", "Cobertura estricta", "Cobertura inclusiva", "Exportar Markdown", "Exportar JSON", "4AM_ACME"):
        assert needle in r.text, needle
    ma_profile.validate_assessment(svc.status_for_entry(world.entries[0]).profile)
    again = client_for().get("/catalog/flow/assessment?flow=acme-4am", headers=HX)
    assert "Assessment vigente" in again.text


def test_report_shows_findings_with_why_context_and_lineage_links(world):
    html = _post(client_for()).text
    assert "Acceso directo a tabla" in html or "ACCESO DIRECTO A TABLA" in html.upper()
    assert "Pregunta para el proyecto" in html and "Ver en linaje" in html and "/catalog/flow?" in html and "focus=" in html


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
        # La sección "Juicio metodológico" (Gate 8D.2) muestra la taxonomía AUTORIZADA del Juicio (incluye "Parcial");
        # se excluye de esta regresión de anti-scoring (sin puntaje/banda/madurez, validada en test_judgment_persistence).
        html = re.sub(r'<section[^>]*id="asr-juicio".*?</section>', " ", html, flags=re.S)
        visible = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
        visible = re.sub(r"<[^>]+>", " ", visible)
        assert not BANNED.search(visible), BANNED.search(visible).group(0)
    assert "Adopción metodológica" in tile and "Cobertura" in tile and "Confianza" in tile
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
    assert 'Assessment desactualizado</strong>' in html and "<p>El modelo cambió desde la última evaluación.</p>" in html      # título corto + nota como primer párrafo (mismo formato que el resto de los punch)
    assert "Regenerar assessment" in html and "Resumen" in html            # banner + informe anterior + acción
    assert c.get("/catalog/flow/assessment.json?flow=acme-4am").status_code == 200


# ── 4–8 estados en la cabecera del detalle (y los estados en el informe) ────
def test_detail_header_states_follow_the_contract(world, monkeypatch):
    c = client_for()

    def hdr():
        return _detail(c, monkeypatch)
    h = hdr()                                                                  # NOT_YET_ASSESSED
    assert "Sin assessment" in h and "Generar informe" in h and "hx-post" in h and "Cobertura" not in h
    # Gate UX.2: la acción de generación es un BOTÓN con carga integrada (se deshabilita, sin label flotante).
    assert "support-links heading-supports" in h and 'class="support-link asr-generate"' in h and 'hx-disabled-elt="this"' in h
    assert "id=\"asm-busy\"" not in h and h.count("Generando informe…") == 1    # un único label, dentro del botón (no flotante)
    assert _post(c).status_code == 200
    h = hdr()                                                                  # CURRENT
    assert "Adopción metodológica · " in h and "Ver Assessment" in h and "Ver informe" not in h and "hx-get" in h and "hx-post" not in h and re.search(r"Cobertura \d+\.\d%", h)
    assert re.search(r"Confianza (Alta|Media|Baja)", h) and re.search(r"(\d+ casos? a revisar|Sin casos a revisar)", h)
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
    # Gate UX.4 §1: las acciones viven en la barra transversal (← Volver + acciones) ARRIBA del hero; el estado queda bajo el título.
    assert h.index("catalog-actionbar") < h.index("support-links heading-supports") < h.index("<h1>") < h.index("Adopción metodológica · ")
    assert "catalog-back" in h and "asm-hero-has-support" not in h


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
    assert hv["reviews"] == ("Sin casos a revisar" if n == 0 else f"{n} caso a revisar" if n == 1 else f"{n} casos a revisar")
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


# ── 18. el grado PRIMARIO de la tile del listado = Adopción metodológica persistida; el veredicto del catálogo queda como guía descriptiva ──
def test_catalog_list_tile_grade_comes_from_adoption_and_catalog_guidance_stays_descriptive(world):
    c = client_for()
    before = c.get("/catalog/domain?dominio=Dom").text
    assert re.search(r'catalog-card__verdict catalog-card__verdict--neutra" title="Adopción metodológica">Sin assessment<', before)    # sin assessment: neutro, NUNCA el grado del catálogo
    assert "catalog-card__verdict--plena" not in before and "catalog-card--plena" not in before
    _post(c)
    after = c.get("/catalog/domain?dominio=Dom").text
    j = presenter.judgment_view(svc.status_for_entry(world.entries[0]).judgment)
    assert f'catalog-card__verdict--{j["tone"]}" title="Adopción metodológica">{j["level_label"]}<' in after   # grado = Juicio persistido
    for html in (before, after):
        for absent in ("asm-", "Ver informe", "Generar informe", "Cobertura", "Confianza"):
            assert absent not in html, absent
        assert "Referencia reutilizable" not in html and "catalog-card__apto" not in html                 # la guía/veredicto LEGACY del catálogo ya no se muestra
    assert not (REPO / "webapp/templates/partials/catalog_assessment_tile.html").exists()


def _judgment(level):
    if level:
        return {"status": "DETERMINED", "level": level, "strengths": [], "reasons": []}
    return {"status": "NOT_DETERMINABLE", "level": None, "not_determinable_reason": "INSUFFICIENT_AFFIRMATIVE_EVIDENCE"}


def _tile(world, verdict, level, state=None, with_judgment=True):
    """Renderiza la tile real (`catalog_flows.html`) para un veredicto de catálogo dado y un Juicio persistido dado."""
    import dataclasses
    e = world.entries[0]
    e = dataclasses.replace(e, veredicto=verdict) if dataclasses.is_dataclass(e) else e
    st = svc.AssessmentStatus(state=state or svc.CURRENT, entry_id=e.entry_id, client=e.cliente, terminal_am="X",
                              judgment=_judgment(level) if with_judgment else None)
    grade = presenter.tile_grade(st)
    html = main.templates.get_template("partials/catalog_flows.html").render(flows=[e], grades={e.entry_id: grade}, dominio="Dom", foco=None, pack="datasphere_core")
    return html, grade


def test_tile_catalog_buena_with_adoption_not_determinable_is_neutral(world):
    html, g = _tile(world, "buena", None)
    assert g["tone"] == "neutra" and g["label"] == "No determinable"
    assert 'catalog-card__verdict--neutra" title="Adopción metodológica">No determinable<' in html and "catalog-card--neutra" in html
    assert "catalog-card__verdict--plena" not in html and "catalog-card__verdict--buena" not in html     # una sola insignia de grado


def test_tile_catalog_buena_with_adoption_buena_is_green(world):
    html, g = _tile(world, "buena", "BUENA")
    assert (g["tone"], g["label"]) == ("plena", "Buena")                                               # status_tone: Buena = verde
    assert 'catalog-card__verdict--plena" title="Adopción metodológica">Buena<' in html


def test_tile_catalog_parcial_with_adoption_adecuada_uses_the_canonical_tone(world):
    from webapp.services.status_tone import status_tone
    html, g = _tile(world, "parcial", "ADECUADA")
    assert g["tone"] == status_tone("ADECUADA") == "buena" and g["label"] == "Adecuada"
    assert 'catalog-card__verdict--buena" title="Adopción metodológica">Adecuada<' in html
    assert "catalog-card__verdict--parcial" not in html                                                # el PARCIAL del catálogo no se filtra a la tile


def test_tile_without_assessment_or_judgment_never_falls_back_to_the_catalog_grade(world):
    for state, label in ((svc.NOT_YET_ASSESSED, "Sin assessment"), (svc.NOT_APPLICABLE, "No aplica"), (svc.FAILED, "Assessment no disponible"), (svc.CURRENT, "Juicio no disponible")):
        html, g = _tile(world, "plena", None, state=state, with_judgment=False)
        assert (g["tone"], g["label"], g["has_judgment"]) == ("neutra", label, False), state
        assert f'catalog-card__verdict--neutra" title="Adopción metodológica">{label}<' in html and "catalog-card__verdict--plena" not in html
    bare = main.templates.get_template("partials/catalog_flows.html").render(flows=[world.entries[0]], grades={}, dominio="Dom", foco=None, pack="x")
    assert "catalog-card__verdict--neutra" in bare and "catalog-card__verdict--plena" not in bare     # sin `grades` tampoco hay fallback al catálogo


def test_tile_stale_assessment_keeps_the_adoption_grade_and_shows_freshness_separately(world):
    html, g = _tile(world, "parcial", "BUENA", state=svc.STALE)
    assert (g["tone"], g["label"], g["freshness"]) == ("plena", "Buena", "Assessment desactualizado")  # el grado NO se recolorea por vigencia
    assert 'catalog-card__verdict--plena" title="Adopción metodológica">Buena<' in html
    assert re.search(r'<p class="catalog-card__meta">\d+ artefactos? · Assessment desactualizado</p>', html)   # vigencia en la línea de metadata, aparte
    cur, gc = _tile(world, "parcial", "BUENA", state=svc.CURRENT)
    assert gc["tone"] == g["tone"] and gc["freshness"] is None and "desactualizado" not in cur


def test_tile_grades_are_fail_soft_and_never_use_the_catalog_verdict(world, monkeypatch):
    monkeypatch.setattr(svc, "statuses_for_entries", lambda es: (_ for _ in ()).throw(RuntimeError("boom")))
    grades = presenter.tile_grades(world.entries)
    assert {g["tone"] for g in grades.values()} == {"neutra"} and {g["label"] for g in grades.values()} == {"Assessment no disponible"}
    r = client_for().get("/catalog/domain?dominio=Dom")
    assert r.status_code == 200 and "catalog-card__verdict--plena" not in r.text and "boom" not in r.text


def test_assessment_failure_never_breaks_the_flow_detail(world, monkeypatch):
    monkeypatch.setattr(svc, "status_for_entry", lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    c = client_for()
    h = _detail(c, monkeypatch)
    assert "<h1>" in h and "Veredicto del catálogo" not in h and "catalog-card__verdict--plena" not in h and "boom" not in h   # hero: sin veredicto legacy; grado neutro
    lst = c.get("/catalog/domain?dominio=Dom")
    assert lst.status_code == 200 and "boom" not in lst.text


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
    # Gate UX.1: jerarquía nombre de negocio PRIMARIO (h1) + identificador técnico SECUNDARIO (línea mono), no "desc · técnico".
    assert re.search(r'<h1 class="asr-title">Acme</h1>', html)                                   # nombre de negocio primario (sin prefijo "Modelo analítico - ")
    assert re.search(r'<p class="asr-subid"><code>4AM_ACME</code></p>', html)                    # identificador técnico secundario
    monkeypatch.setattr(catalog_reads, "artifact_title", lambda entry, name: (name, name))
    fb = _post(c).text
    # Sin nombre de negocio: el título es el nombre técnico LEGIBLE (mismo resolver que el encabezado del Catálogo) y el identificador técnico sigue visible debajo.
    assert re.search(r'<h1 class="asr-title">Acme</h1>', fb) and re.search(r'<p class="asr-subid"><code>4AM_ACME</code></p>', fb)


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
    # Gate UX.4 §8: "Adopción metodológica" entra como sección del informe JUSTO después de "Resumen" (Resumen → Adopción → resto).
    # Gate UX.5: recorrido (Resumen → Adopción → Arquitectura → Gobierno → [Portabilidad: solo si el Catálogo trae portabilidad] → Casos/preguntas → Cierre).
    assert [t for _n, t in heads] == ["Resumen", "Adopción metodológica", "Arquitectura &amp; Modelado", "Gobierno &amp; estándares",
                                      "Casos a revisar y preguntas abiertas", "Cierre: limitaciones y trazabilidad"]
    assert [n for n, _t in heads] == [f"{i:02d}" for i in range(1, 7)]                       # numeración continua generada por la plantilla
    assert 'id="asr-areas"' not in html and "<h2>Áreas</h2>" not in html
    arch = html[html.index('id="asr-m-architecture_modeling"'):html.index('id="asr-gobierno"')]
    for title in ("Modelo analítico", "Propagación", "Armonización", "Ingesta"):
        assert f'<span class="dashboard-section__title">{title}</span>' in arch
    assert len(re.findall(r'<details class="dashboard-section" id="asr-a-', arch)) == 5                     # 4 subáreas + Ingesta / Fuente & Extracción
    for sec, area in (("asr-m-architecture_modeling", "SOURCE_EXTRACTION"), ("asr-gobierno", "NAMING")):   # Fuente & Extracción → Arquitectura; Convención de nombres → Gobierno
        part = html[html.index(f'id="{sec}"'):]
        part = part[:part.index("</section>")]
        assert f'<details class="dashboard-section" id="asr-a-{area}"' in part
    assert 'id="asr-portabilidad"' not in html and 'id="asr-a-SOURCE_EXTRACTION"' in arch                  # Portabilidad = solo portabilidad futura; sin datos no hay sección
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
    assert [l for _v, l in kpis] == ["Cobertura estricta", "Confianza", "Casos a revisar", "Preguntas abiertas"]
    prof = svc.status_for_entry(world.entries[0]).profile
    vals = dict((l, v) for v, l in kpis)
    assert vals["Cobertura estricta"] == presenter.P.percent_text(prof["summary"]["coverage"]["strict"])
    assert vals["Casos a revisar"] == str(sum(g["occurrence_count"] for g in prof["review_required"]))
    assert vals["Preguntas abiertas"] == str(prof["summary"]["context_required"]["open_questions"])
    assert "Perfil de evidencia, cobertura y confianza" in first.split("</blockquote>")[0]                # la nota metodológica vive en el subtítulo de Resumen
    assert html.count("Perfil de evidencia, cobertura y confianza") == 1


def test_findings_are_hub_disclosure_rows_with_business_metadata_not_raw_inline_text(world):
    html = _post(client_for()).text
    assert not re.search(r'<aside class="callout[^"]*" id="asr-g-', html)
    rows = re.findall(r'<details class="dashboard-section" id="asr-g-[^"]+">.*?</details>', html, re.S)
    assert rows
    for row in rows:
        head = row[:row.index("</summary>")]
        assert '<span class="dashboard-section__title">' in head and '<span class="dashboard-section__count">' in head           # título a la izquierda, metadata a la derecha
        meta = re.search(r'<span class="dashboard-section__count">([^<]+)</span>', head).group(1)
        assert re.fullmatch(r"[^·]+ · \d+ ocurrencias? · \d+ artefactos?", meta), meta                                           # categoría · N ocurrencias · N artefactos
        body = row[row.index("</summary>"):]
        assert "Pregunta para el proyecto" not in head and "Razones legítimas" not in head                                         # la historia completa va bajo demanda
        assert "catalog-card__verdict" not in row                                                                                  # sin etiquetas sueltas (CASO A REVISAR, N OCURRENCIAS…)
    sec = html[html.index('id="asr-revision"'):html.index('id="asr-limitaciones"')]
    visible = re.sub(r"<(script|style)\b.*?</\1>", " ", sec, flags=re.S)
    for rule_id in re.findall(r'asr-g-([A-Z_]+)"', sec):
        before_rows = re.sub(r"<details.*?</details>", " ", visible, flags=re.S)                                                   # fuera de las filas: nada de ids internos
        assert rule_id not in before_rows, rule_id
        assert f'<tr><td>Identificador de la regla (trazabilidad)</td><td><span class="catalog-note__ev">{rule_id}</span>' in sec     # el id queda en el detalle técnico


def test_facts_have_a_reading_hierarchy_headline_kpis_then_supporting_tables_and_nothing_is_lost(world):
    html = _post(client_for()).text
    prof = svc.status_for_entry(world.entries[0]).profile
    for a in prof["areas"]:
        v = presenter._facts_view(a["id"], a["highlights"])
        assert len(v["headline"]) <= 4                                                                # titulares: pocos y de síntesis
        shown = {m["label"] for m in v["headline"]}
        for g in v["groups"]:
            shown |= {p["label"] for p in g["properties"]} | {t["title"] for t in g["tables"]} | {g["title"]}
        moved = {"views_in_closure", "view_type_distribution"} if a["id"] == "ANALYTIC_MODEL" else set()           # la composición del cierre vive en el resumen de la sección
        assert all(h["label"] in shown for h in a["highlights"] if h["id"] not in moved), a["id"]                # ningún otro hecho se pierde
        assert (v["closure_views"] is not None) == (a["id"] == "ANALYTIC_MODEL" and any(h["id"] == "views_in_closure" for h in a["highlights"]))
        # los valores numéricos escalares siguen presentes (titular o tabla de propiedades)
        for h in a["highlights"]:
            if isinstance(h["value"], int) and not h.get("breakdown"):
                assert any(m["label"] == h["label"] and m["value"] == h["value"] for m in v["headline"]) or \
                       any(p["label"] == h["label"] and p["value"] == h["value"] for g in v["groups"] for p in g["properties"])
    am = presenter._facts_view("ANALYTIC_MODEL", next(a for a in prof["areas"] if a["id"] == "ANALYTIC_MODEL")["highlights"])
    assert [m["label"] for m in am["headline"]] == ["Campos expuestos", "Medidas", "Asociaciones declaradas"]
    assert [g["title"] for g in am["groups"]] == ["Composición del modelo", "Cierre semántico del modelo analítico (artefactos)", "Linaje y asociaciones",
                                                          "Vistas del cierre (nombre técnico y tipo)"]
    sec = html[html.index('id="asr-m-architecture_modeling"'):html.index('id="asr-gobierno"')]
    assert 'class="activity-kpi-grid asm-facts"' in sec and "<th>Hecho</th><th>Valor</th>" in sec
    assert not re.search(r"<li>[^<]*: \d+</li>", sec)                                                  # sin pared de bullets "Hecho: n"
    nam = html[html.index('id="asr-a-NAMING"'):html.index('id="asr-revision"')]
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
    assert "Hallazgos en esta área" not in sec                                                          # la lista redundante se integró a la tabla (ver test_assessment_contextual_navigation)
    assert not re.search(r"<li>[^<]*(ausencia|patrón|evaluad)[^<]*</li>", sec)


def test_question_links_use_the_hub_content_link(world):
    html = _post(client_for()).text
    sec = html[html.index("Preguntas que solo el proyecto puede responder"):html.index('id="asr-observaciones"')]
    links = re.findall(r'<td><a ([^>]*)><code>', sec)
    assert links and all("class=" not in l and 'href="#asr-g-' in l for l in links)                       # mismo enlace de contenido del Hub, sin clase propia
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8")
    assert "sap-note-link" not in css[css.index("Assessment de modelado en el Catálogo"):]                  # sin color de enlace propio


def test_open_questions_are_one_table_and_reconcile_with_the_kpi(world):
    c = client_for()
    html = _post(c).text
    prof = svc.status_for_entry(world.entries[0]).profile
    ctx = prof["summary"]["context_required"]
    # corta en «Observaciones» (Gate UX.5: las preguntas abiertas viven en la sección «Casos a revisar y preguntas abiertas»).
    sec = html[html.index("Preguntas que solo el proyecto puede responder"):html.index('id="asr-observaciones"')]
    tables = re.findall(r"<table[^>]*>.*?</table>", sec, re.S)
    assert len(tables) == 1 and 'data-block="open-questions"' in tables[0]                         # UNA sola tabla (antes: una por tipo de hallazgo)
    assert "<th>Pregunta</th><th>Vinculada a</th><th>Hallazgo</th><th>Ocurrencias</th>" in tables[0]
    rows = re.findall(r"<tr><td>.*?</td></tr>", tables[0], re.S)
    assert len(rows) == ctx["open_questions"]                                                       # cada pregunta canónica aparece exactamente una vez
    backed_rows = len(rows) - ctx["questions_without_findings"]
    n_independent = ctx["questions_without_findings"]
    kpi = int(re.search(r'<span class="activity-kpi__value">(\d+)</span><span class="activity-kpi__label">Preguntas abiertas', html).group(1))
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
    sec = html[html.index('id="asr-observaciones"'):html.index('id="asr-limitaciones"')]
    assert '<div class="dashboard-empty"><span class="dashboard-empty__label">No detectado</span>' in sec
    assert "No disponible" not in sec and "No aplica" not in sec
    assert "Ver «Cierre» para la evidencia que no pudo evaluarse." in sec


def test_finding_detail_uses_structured_tables_with_a_stable_occurrence_schema(world):
    html = _post(client_for()).text
    rows = re.findall(r'<details class="dashboard-section" id="asr-g-[^"]+">.*?</details>', html, re.S)
    assert rows
    for row in rows:
        det = row[row.index("</summary>"):]
        assert re.search(r"<h[345]>Ocurrencias \(\d+\)</h[345]>", det)                                       # subtítulo semántico dentro del detalle
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
    badge = re.search(r'<a class="catalog-card__verdict[^"]*"[^>]*>Adopción metodológica · [^<]+</a>', h, re.S).group(0)
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
    kinds = {"content": 0, "support": 0, "back": 0, "action": 0}
    for attrs, inner in anchors:
        if 'class="table-action-btn chart-tooltip-anchor"' in attrs:                                   # acción ICÓNICA de fila (patrón CCHH; ver test_assessment_contextual_navigation)
            assert 'aria-label="Ver evidencia del hallazgo «' in attrs and inner.lstrip().startswith("<svg")
            kinds["action"] += 1
        elif 'class="support-link"' in attrs:
            kinds["support"] += 1
        elif 'class="catalog-back"' in attrs:
            kinds["back"] += 1
        else:
            assert "class=" not in attrs, attrs                                                       # sin clases propias (ni sap-note-link, ni asr-link)
            assert inner.strip().startswith("<code>") and inner.strip().endswith("</code>"), inner      # mismo tratamiento en TODOS
            assert "<strong>" not in inner and "<b>" not in inner and "style=" not in attrs
            kinds["content"] += 1
    assert kinds["content"] >= 10 and kinds["support"] >= 2 and kinds["back"] == 1 and kinds["action"] >= 1
    kinds_of_text = {m for m in re.findall(r"<a href=\"#asr-g-[^\"]+\"><code>([^<]+)</code></a>", html)}
    assert len(kinds_of_text) >= 5                                                                    # preguntas + hallazgos usan el mismo marcado
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8")
    block = css[css.index("Assessment de modelado en el Catálogo"):]
    assert not re.search(r"\.asr-link|\.assessment-link|sap-note-link|reference-text-link", block)       # sin CSS de enlace propio



# ── Gate UX.5: «Mirada Danwor» del Catálogo + gramática compartida con el informe ──────────────────────────
S28 = {"applicable": 4, "non_compliant": 1, "non_compliant_artifacts": ["4AM_ACME"],
       "violation_reason_histogram": [{"key": "artefacto tipado 'AM' no debe llevar prefijo de capa '4' (§29: los artefactos tipados se nombran sin número de capa)", "count": 1}]}


def _evaluation_stub():
    from webapp.services import asset_evaluation as ae
    gov = ae.governance_view(ae.governed_findings([{"id": "FIELD_VOCABULARY_MIX", "title": "Etiqueta compartida entre vocabularios técnicos distintos",
                                                    "explanation": "Una misma etiqueta nombra campos distintos.", "kind_text": "Observación"}], S28))
    por = ae.portability_view({"clasificacion": "condicionada", "score": 72, "confianza": "media", "n_riesgos": 2, "n_adaptaciones": 1,
                               "campos_revisados": 40, "campos_no_evaluables": 0,
                               "findings": [{"regla": "R1", "semaforo": "orange", "tipo_hallazgo": "riesgo", "titulo": "DecimalFloat",
                                             "artefacto": "V1", "campo": "X", "recomendacion": "Usar Decimal"}]})
    return {"governance": gov, "portability": por}


def test_catalog_evaluation_is_one_numbered_section_with_three_independent_lenses(world, monkeypatch):
    c = client_for()
    _post(c)                                                                                                 # Adopción persistida (CURRENT)
    monkeypatch.setattr(main, "_catalog_flow_view", lambda entry, show_assoc=None, pack="": ([{"slug": "m", "html": "x"}], "", bool(show_assoc), False))
    monkeypatch.setattr(main.asset_evaluation, "asset_evaluation", lambda entry, findings=None, assessed=False: _evaluation_stub())
    html = c.get("/catalog/flow?flow=acme-4am&pack=datasphere_core", headers=HX).text
    sec = html[html.index('id="evaluacion-del-activo"'):]
    sec = sec[:sec.index("</section>")]
    assert '<span class="section-eyebrow">[02]</span><h2>Mirada Danwor</h2>' in sec                      # numeración continúa tras las secciones del flujo (1)
    titles = re.findall(r'<span class="dashboard-section__title">([^<]+)</span>', sec)
    assert titles == ["Gobierno &amp; estándares", "Portabilidad &amp; preparación BDC", "Adopción metodológica"]
    ado = sec[sec.index('id="eval-adopcion"'):]
    head = ado[:ado.index("</summary>")]
    assert head.count("dashboard-section__state") == 1 and "&nbsp;·&nbsp;" in head and head.index("evidencias favorables") < head.index("catalog-card__verdict")   # resumen · badge, juntos a la derecha
    # Gobierno: factual, SIN nota global ni badge de grado en la cabecera de la fila; Portabilidad: capacidad técnica, sin badge de grado.
    gov = sec[sec.index('id="eval-gobierno"'):sec.index('id="eval-portabilidad"')]
    assert "2 observaciones de gobierno" in gov and "dashboard-section__state" not in gov and "convención de nombres" in gov.lower()
    por = sec[sec.index('id="eval-portabilidad"'):]
    assert "Consistencia 72/100" in por and "Consistencia de tipos" in por and "dashboard-section__state" not in por.split("</summary>")[0]
    # el detalle del Assessment NO se duplica en el Catálogo y la acción lleva al informe
    assert "Evidencias favorables</h3>" not in sec and "Ver Assessment" in sec


def test_governed_findings_compose_naming_section_28_from_the_persisted_canonical_evidence():
    from webapp.services import asset_evaluation as ae
    ref = {"module_key": "04", "section_key": "naming", "text": "Módulo 04 · §29 · Convención de Nombres"}
    rows = ae.governed_findings([], S28, ref)
    assert len(rows) == 1 and rows[0]["title"] == "Oportunidades en la convención de nombres" and rows[0]["finding_type"] == "NAMING_CONVENTION"
    assert "4AM_ACME" in rows[0]["evidence"] and "no debe llevar prefijo de capa" in rows[0]["evidence"]          # evidencia explicativa y artefacto, del propio perfil
    assert rows[0]["reference"] == {"module_key": "04", "section_key": "naming"} and rows[0]["reference_text"] == ref["text"]   # identidad = claves semánticas; el número solo se presenta
    assert ae.governed_findings([], {"applicable": 4, "non_compliant": 0}) == [] and ae.governed_findings([], None) == []
    g = ae.governance_view(rows)
    assert g["total"] == 1 and g["summary"] == "1 observación de gobierno" and "grade" not in g and ae.governance_view(None) is None
    assert ae.asset_evaluation(object(), findings=rows, assessed=False) == {"governance": None, "portability": None}   # sin Assessment: nada del evaluador legacy


def test_report_closing_callout_takes_its_tone_from_status_tone_for_every_level():
    from webapp.services.status_tone import status_tone
    for level in ("BUENA", "ADECUADA", "PARCIAL"):
        j = presenter.judgment_view({"status": "DETERMINED", "level": level, "strengths": [], "reasons": []})
        assert j["tone"] == status_tone(level)
    nd = presenter.judgment_view({"status": "NOT_DETERMINABLE", "level": None, "not_determinable_reason": "INSUFFICIENT_AFFIRMATIVE_EVIDENCE"})
    assert nd["tone"] == "neutra"
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8")
    for t in ("plena", "buena", "parcial", "neutra"):
        assert f".callout--{t} " in css and f"--tone-{t}:" in css                                              # un solo juego de tokens de tono para badge, punto y callout


def test_legacy_compliance_classification_is_unchanged_for_the_other_catalog_pages():
    """«Mirada Danwor» clasifica por el marcador del generador; las páginas existentes (conexiones, seguridad, data products, tabla de dominio
    heredada) siguen con el clasificador legacy SIN cambios: texto/palabras clave, y el emoji NO decide por sí solo."""
    st = main._catalog_compliance_status
    assert [st(x) for x in ("cumple Cobertura", "observar X", "atender Y", "nota Z")] == [("green", "Cumple"), ("amber", "Observar"), ("orange", "Atender"), ("blue", "Nota")]
    assert st("\U0001f534 Hoja sin contrato") == ("blue", "Nota") and st("\U0001f7e1 Nombre") == ("blue", "Nota")     # el emoji solo, no clasifica (comportamiento legacy)
    assert st("\U0001f534 Algo critico") == ("red", "Critico")
    html = main._catalog_compliance_table_html("- cumple **Cobertura** _Evidencia:_ ok\n- atender **Cap** _Evidencia:_ a | b")
    assert 'catalog-card__verdict--plena">Cumple<' in html and 'catalog-card__verdict--parcial">Atender<' in html and "<th>Estado</th><th>Hallazgo</th><th>Evidencia</th>" in html


def test_portability_section_exists_only_with_catalog_portability_data_and_has_no_source_extraction(world):
    c = client_for()
    _post(c)
    e0 = world.entries[0]
    view = presenter.report_view(svc.status_for_entry(e0), e0, "datasphere_core")
    view["evaluation"] = _evaluation_stub()
    html = main.templates.get_template("partials/catalog_assessment_report.html").render(v=view, pack="datasphere_core")
    por = html[html.index('id="asr-portabilidad"'):]
    por = por[:por.index("</section>")]
    assert "Consistencia de tipos" in por and "asr-a-" not in por     # solo portabilidad futura (score/riesgos/adaptaciones); la fuente vive en Arquitectura


def test_asset_evaluation_is_composition_only_and_imports_no_engine():
    src = (REPO / "webapp/services/asset_evaluation.py").read_text(encoding="utf-8")
    imports = re.findall(r"^(?:from|import)\s+([\w.]+)", src, re.M)
    assert not any(m.startswith(("tools", "core")) for m in imports), imports               # sin motores de Assessment / Juicio / Portabilidad
    assert not re.search(r"evaluate_judgment|portabilidad_bdc\s*\(|def\s+(compute|calc|score)", src)


def test_mirada_danwor_action_mirrors_the_top_bar_action(world, monkeypatch):
    """Los botones son un ESPEJO: sin assessment, arriba «Generar informe» y abajo (Mirada Danwor) lo mismo; «Ver Assessment» solo si existe."""
    monkeypatch.setattr(main, "_catalog_flow_view", lambda entry, show_assoc=None, pack="": ([{"slug": "m", "html": "x"}], "", bool(show_assoc), False))
    monkeypatch.setattr(main.asset_evaluation, "asset_evaluation", lambda entry, findings=None, assessed=False: _evaluation_stub())
    c = client_for()

    def parts():
        html = c.get("/catalog/flow?flow=acme-4am&pack=datasphere_core", headers=HX).text
        top = html[html.index('<div class="catalog-actionbar">'):html.index('<section class="module-hero">')]
        mir = html[html.index('id="evaluacion-del-activo"'):]
        return top, mir[:mir.index("</section>")]

    top, mir = parts()                                                              # NOT_YET_ASSESSED
    assert "Generar informe" in top and "hx-post" in top and "Ver Assessment" not in top
    assert "Generar informe" in mir and "hx-post" in mir and "asr-generate" in mir and "Ver Assessment" not in mir
    _post(c)                                                                        # CURRENT
    top, mir = parts()
    assert "Ver Assessment" in top and "Ver Assessment" in mir and "Generar informe" not in top and "Generar informe" not in mir
    assert re.search(r'hx-get="([^"]+)"[^>]*>Ver Assessment', top).group(1) == re.search(r'hx-get="([^"]+)"[^>]*>Ver Assessment', mir).group(1)   # mismo destino


def test_hero_status_dot_and_badge_are_neutral_when_there_is_no_assessment(world, monkeypatch):
    h = _detail(client_for(), monkeypatch)                                          # NOT_YET_ASSESSED
    first = h[h.index('<span class="catalog-verdict-line">'):]
    first = first[:first.index("</span>\n    </span>") if "</span>\n    </span>" in first else 600]
    assert 'catalog-meta-dot catalog-meta-dot--neutra' in first and 'catalog-card__verdict--neutra' in first      # mismo gris que la tile (no el dorado por defecto)


# ── UN activo, UN grado primario: Adopción metodológica persistida; el grado legacy del catálogo está RETIRADO de la UI ──────────────────────
def _surfaces(world, monkeypatch, catalog_verdict, state=None, level=None, with_judgment=True):
    """(hero, tile) reales de UN mismo flujo para un veredicto LEGACY dado y un estado/Juicio persistido dado (estado inyectado en el servicio)."""
    import dataclasses
    e = dataclasses.replace(world.entries[0], veredicto=catalog_verdict)
    world.entries[0] = e                                                                    # la lista es la que sirve el catálogo falso
    judgment = _judgment(level) if with_judgment else None
    st = svc.AssessmentStatus(state=state or svc.CURRENT, entry_id=e.entry_id, client=e.cliente, terminal_am="4AM_ACME", judgment=judgment)
    monkeypatch.setattr(svc, "status_for_entry", lambda entry: st)
    monkeypatch.setattr(svc, "statuses_for_entries", lambda es: {x.entry_id: st for x in es})
    monkeypatch.setattr(main, "_catalog_flow_view", lambda entry, show_assoc=None, pack="": ([{"slug": "m", "html": "x"}], "", bool(show_assoc), False))
    c = client_for()
    hero = c.get("/catalog/flow?flow=acme-4am&pack=datasphere_core", headers=HX).text
    hero = hero[hero.index('<section class="module-hero">'):hero.index("</section>", hero.index('<section class="module-hero">'))]
    tile = c.get("/catalog/domain?dominio=Dom").text
    return hero, tile


def _grade_badges(html):
    return re.findall(r'<(?:a|span) class="catalog-card__verdict catalog-card__verdict--(\w+)[^"]*"[^>]*>([^<]+)</(?:a|span)>', html)


def test_no_assessment_with_legacy_catalog_buena_shows_sin_assessment_only(world, monkeypatch):
    hero, tile = _surfaces(world, monkeypatch, "buena", state=svc.NOT_YET_ASSESSED, with_judgment=False)
    for html in (hero, tile):
        assert {(t, x.strip()) for t, x in _grade_badges(html)} == {("neutra", "Sin assessment")}    # UN solo badge distinto: neutro; sin grado
        assert "BUENA" not in html.upper().replace("SIN ASSESSMENT", "") and "Veredicto del catálogo" not in html and "Reutilizable" not in html


def test_assessment_not_determinable_with_legacy_catalog_buena_shows_no_determinable_only(world, monkeypatch):
    hero, tile = _surfaces(world, monkeypatch, "buena", level=None)
    assert [(t, x.strip()) for t, x in _grade_badges(hero)] == [("neutra", "Adopción metodológica · No determinable")]
    assert {(t, x.strip()) for t, x in _grade_badges(tile)} == {("neutra", "No determinable")}                          # (la lista del dominio trae 2 flujos con el mismo estado inyectado)
    for html in (hero, tile):
        assert "Buena" not in html and "BUENA" not in html and "Veredicto del catálogo" not in html


def test_assessment_buena_with_legacy_catalog_parcial_shows_buena_only(world, monkeypatch):
    hero, tile = _surfaces(world, monkeypatch, "parcial", level="BUENA")
    assert [(t, x.strip()) for t, x in _grade_badges(hero)] == [("plena", "Adopción metodológica · Buena")]
    assert {(t, x.strip()) for t, x in _grade_badges(tile)} == {("plena", "Buena")}                          # (la lista del dominio trae 2 flujos con el mismo estado inyectado)
    for html in (hero, tile):
        assert "parcial" not in html.lower() and "Veredicto del catálogo" not in html


def test_stale_assessment_keeps_the_persisted_adoption_grade_and_shows_freshness_without_a_legacy_grade(world, monkeypatch):
    hero, tile = _surfaces(world, monkeypatch, "parcial", state=svc.STALE, level="ADECUADA")
    assert [(t, x.strip()) for t, x in _grade_badges(hero)] == [("buena", "Adopción metodológica · Adecuada")]    # el grado NO se recolorea por vigencia
    assert {(t, x.strip()) for t, x in _grade_badges(tile)} == {("buena", "Adecuada")}                          # (la lista del dominio trae 2 flujos con el mismo estado inyectado)
    assert "Assessment desactualizado" in hero and "Assessment desactualizado" in tile                      # vigencia, aparte
    for html in (hero, tile):
        assert "Veredicto del catálogo" not in html and "catalog-card__verdict--parcial" not in html


def test_right_tile_and_central_hero_use_the_same_grade_source(world, monkeypatch):
    for verdict, state, level, with_j in (("buena", svc.CURRENT, None, True), ("parcial", svc.CURRENT, "BUENA", True), ("plena", svc.STALE, "PARCIAL", True),
                                          ("buena", svc.NOT_YET_ASSESSED, None, False)):
        hero, tile = _surfaces(world, monkeypatch, verdict, state=state, level=level, with_judgment=with_j)
        (ht, hl), (tt, tl) = _grade_badges(hero)[0], _grade_badges(tile)[0]
        assert ht == tt and hl.strip().split(" · ")[-1] == tl.strip()                                        # mismo tono y mismo nivel
    e = world.entries[0]
    st = svc.AssessmentStatus(state=svc.CURRENT, entry_id=e.entry_id, client=e.cliente, terminal_am="4AM_ACME", judgment=_judgment("BUENA"))
    assert presenter.header_view(st, e, "datasphere_core")["grade"] == presenter.tile_grade(st)               # UNA sola función


# ── Hallazgos de gobierno: SOLO del Assessment gobernado; el evaluador legacy del catálogo no produce notas ni CTAs ──────────────────────────
LEGACY_NOTE = {"severidad": "nota", "titulo": "Oportunidades en la convención de nombres §28 (módulo 04)",
               "evidencia": "4AM_LEGACY: artefacto tipado 'AM' no debe llevar prefijo de capa '4' (§29)"}


def _governed(world, monkeypatch, state=svc.CURRENT, s28=None, legacy=True, groups=None, with_profile=True):
    """(hero, tile, status, entry) con un Assessment gobernado sintético (perfil real + evidencia de §28 inyectada) y una nota LEGACY en el catálogo."""
    import copy
    import dataclasses
    svc.generate(world.entries[0])
    prof = copy.deepcopy(svc.status_for_entry(world.entries[0]).profile)
    hy = prof["canonical_evidence"]["naming_evidence"]["hygiene"]["artifact_name_populations"]
    hy["section_28"] = {**hy["section_28"], **(s28 or {"applicable": 4, "non_compliant": 0, "non_compliant_artifacts": [], "violation_reason_histogram": []})}
    for key in ("observations", "review_required", "descriptive_signals"):
        prof[key] = [g for g in prof[key] if g.get("area") != "NAMING"]
    for area in prof["areas"]:
        if area["id"] == "NAMING":
            area["finding_groups"] = []
    e = dataclasses.replace(world.entries[0], hallazgos=[LEGACY_NOTE] if legacy else [])
    world.entries[0] = e
    st = svc.AssessmentStatus(state=state if with_profile else svc.NOT_YET_ASSESSED, entry_id=e.entry_id, client=e.cliente, terminal_am="4AM_ACME",
                              profile=prof if with_profile else None, judgment=_judgment("BUENA") if with_profile else None)
    monkeypatch.setattr(svc, "status_for_entry", lambda entry: st)
    monkeypatch.setattr(svc, "statuses_for_entries", lambda es: {x.entry_id: st for x in es})
    monkeypatch.setattr(main, "_catalog_flow_view", lambda entry, show_assoc=None, pack="": ([{"slug": "m", "html": "x"}], "", bool(show_assoc), False))
    c = client_for()
    hero = c.get("/catalog/flow?flow=acme-4am&pack=datasphere_core", headers=HX).text
    tile = c.get("/catalog/domain?dominio=Dom").text
    return hero, tile, st, e


def test_no_assessment_with_a_legacy_naming_note_renders_no_note_anywhere(world, monkeypatch):
    hero, tile, _st, _e = _governed(world, monkeypatch, with_profile=False)
    for html in (hero, tile):
        assert "convención de nombres" not in html.lower() and "Ver notas" not in html and "4AM_LEGACY" not in html and "catalog-card__notes" not in html
    assert "Sin assessment" in tile and 'id="eval-gobierno"' not in hero and 'id="eval-portabilidad"' not in hero    # Mirada: solo estado vacío + acción
    assert "Generar informe" in hero and "dashboard-empty" in hero.split('id="evaluacion-del-activo"')[1]


def test_current_assessment_with_a_governed_naming_finding_shows_the_same_finding_in_tile_mirada_and_assessment(world, monkeypatch):
    hero, tile, st, e = _governed(world, monkeypatch, s28=S28)
    expected = presenter.governance_findings(st.profile)
    assert len(expected) == 1 and expected[0]["title"] == "Oportunidades en la convención de nombres" and "§" not in expected[0]["evidence"]
    tile_notes = tile[tile.index('catalog-card__notes'):]
    tile_notes = tile_notes[:tile_notes.index("</details>")]
    assert "1 observación de gobierno" in tile_notes and expected[0]["title"] in tile_notes and "4AM_ACME" in tile_notes      # resumen compacto con el MISMO hallazgo
    assert "4AM_LEGACY" not in tile + hero and "§28" not in tile + hero + client_for().get("/catalog/flow/assessment?flow=acme-4am", headers=HX).text   # nada del payload legacy ni ordinales viejos
    assert "Módulo 04 · §" in tile_notes and "Convención de Nombres" in tile_notes                                       # referencia semántica resuelta dinámicamente
    gov = hero[hero.index('id="eval-gobierno"'):]
    assert expected[0]["title"] in gov[:gov.index("</details>")]                                                          # Mirada: el MISMO hallazgo
    report = client_for().get("/catalog/flow/assessment?flow=acme-4am", headers=HX).text
    sec = report[report.index('id="asr-gobierno"'):report.index('id="asr-revision"')]
    assert expected[0]["title"] in sec and "4AM_ACME" in sec and "no debe llevar prefijo de capa" in sec and "4AM_LEGACY" not in report   # Assessment [04]: idéntico


def test_current_assessment_without_governance_findings_leaks_no_legacy_note(world, monkeypatch):
    hero, tile, st, e = _governed(world, monkeypatch, s28=None, legacy=True)
    assert presenter.governance_findings(st.profile) == []
    assert "catalog-card__notes" not in tile and "Oportunidades en la convención" not in (tile + hero) and "4AM_LEGACY" not in tile + hero
    assert "Sin observaciones de gobierno" in hero and "El Assessment no detectó observaciones de gobierno." in hero
    report = client_for().get("/catalog/flow/assessment?flow=acme-4am", headers=HX).text
    assert "4AM_LEGACY" not in report and "Oportunidades en la convención" not in report and "El Assessment no detectó observaciones de gobierno." in report


def test_stale_assessment_keeps_the_persisted_governed_finding_with_freshness_apart(world, monkeypatch):
    hero, tile, st, e = _governed(world, monkeypatch, state=svc.STALE, s28=S28)
    assert "1 observación de gobierno" in tile and "Assessment desactualizado" in tile                                     # hallazgo del último snapshot + vigencia aparte
    assert "Assessment desactualizado" in hero and "4AM_LEGACY" not in tile + hero


def test_legacy_catalog_payload_alone_cannot_produce_a_note_or_call_to_action():
    for name in ("catalog_flows.html", "catalog_flow_content.html"):
        src = (REPO / "webapp/templates/partials" / name).read_text(encoding="utf-8")
        scrub = src.replace("hallazgos de portabilidad", "").replace("Sin hallazgos de portabilidad", "").replace("Hallazgos de portabilidad", "")
        scrub = re.sub(r"\{#.*?#\}", "", scrub, flags=re.S)                                  # (los comentarios de plantilla explican la política)
        assert "hallazgos" not in scrub and "Ver notas" not in src, name
    ev = main.asset_evaluation.asset_evaluation(type("E", (), {"hallazgos": [LEGACY_NOTE], "body": "## Hallazgos de cumplimiento\n- x **t** _Evidencia:_ y", "portabilidad_bdc": None})(), assessed=False)
    assert ev == {"governance": None, "portability": None}


# ── [06]/[07] normalizados a la gramática del Hub ───────────────────────────────────────────────────────────────────────────────────────
def test_section_06_reads_as_the_same_report_with_concise_copy_and_counted_subsections(world):
    html = _post(client_for()).text
    sec = html[html.index('id="asr-revision"'):html.index('id="asr-limitaciones"')]
    assert "Casos que requieren criterio técnico o contexto del proyecto para poder interpretarse o cerrarse." in sec
    assert re.search(r"<h3>Casos a revisar · \d+</h3>", sec) and re.search(r"<h3>Observaciones · \d+</h3>", sec)
    assert re.search(r'id="asr-preguntas"', sec) and "Preguntas que solo el proyecto puede responder" in sec                    # tabla/disclosure canónico existente
    lead = sec[:sec.index("<details")]                                                                                         # entre el encabezado y la primera fila: nada suelto
    assert "catalog-card__verdict" not in lead and "FIELD_VOCABULARY_MIX" not in lead and "<code>" not in lead


def test_confidence_components_are_a_canonical_table_with_two_decimals_and_a_tone_badge(world):
    html = _post(client_for()).text
    sec = html[html.index('id="asr-limitaciones"'):]
    comp = re.search(r'<table data-block="confidence-components">.*?</table>', sec, re.S).group(0)
    assert "<th>Componente</th><th>Valor</th><th>Nivel</th>" in comp
    rows = re.findall(r'<tr><td>([^<]+)</td><td><strong title="Valor exacto: ([^"]+)">(\d\.\d{2})</strong></td><td><span class="catalog-card__verdict catalog-card__verdict--(\w+)">([A-ZÁÉÍÓÚ]+)</span></td></tr>', comp)
    prof = svc.status_for_entry(world.entries[0]).profile
    comps = prof["summary"]["confidence"]["components"]
    assert len(rows) == len(comps) and rows
    for (label, exact, shown, tone, level), c in zip(rows, comps):
        assert float(exact) == c["value"] and shown == f"{c['value']:.2f}"                                  # 2 decimales de DISPLAY; el valor persistido no cambia
        assert tone == {"ALTA": "plena", "MEDIA": "buena", "BAJA": "parcial"}[level]                        # tono canónico (status_tone)
    assert "Los componentes se combinan tomando el nivel más bajo." in sec                                  # la explicación se conserva
    assert not re.search(r"\d\.\d+ — [A-Z]+", sec)                                                          # sin fragmentos crudos «1.0 — ALTA»


def test_limitations_table_uses_the_neutral_state_badge_and_keeps_technical_ids_secondary(world):
    html = _post(client_for()).text
    sec = html[html.index('id="asr-limitaciones"'):]
    tbl = re.search(r"<table>\s*<thead><tr><th>Evidencia</th><th>Área</th><th>Estado</th>.*?</table>", sec, re.S)
    if tbl:                                                                                                  # el fixture puede no traer limitaciones
        t = tbl.group(0)
        assert 'catalog-card__verdict catalog-card__verdict--neutra">' in t and "<code>" not in t           # estado = badge neutro; ids técnicos sin competir
        assert re.search(r'<span class="catalog-note__ev">[^<]+</span>', t)
        assert re.search(r"<td>(cuenta|no cuenta)</td>", t) and "verdict\">cuenta" not in t                 # «¿Cuenta?» sigue siendo texto simple


def test_confidence_levels_are_tone_mapped_by_the_single_status_tone_source():
    from webapp.services.status_tone import status_tone
    assert (status_tone("ALTA"), status_tone("MEDIA"), status_tone("BAJA")) == ("plena", "buena", "parcial")
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8")
    assert not re.search(r"confidence-components|\.asr-confidence|\.asr-component", css)                     # sin CSS propio de confianza


def test_badge_columns_in_hub_tables_fit_their_content_on_one_line():
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8")
    assert re.search(r"\.methodology table:not\(\.table-fixed-columns\) \.catalog-card__verdict \{ white-space: nowrap; \}", css)
    assert re.search(r"td:has\(> \.catalog-card__verdict:only-child\) \{ width: 1%; white-space: nowrap; \}", css)       # la columna del badge se ajusta; las demás se reparten el resto


# ── avisos de estado del informe (desactualizado / sin assessment): mismo lenguaje visual que el resto de los `callout("punch")` ──
def _status_block(html):
    """El bloque de avisos de estado: el `<div class="methodology">` con su(s) callout(s), antes del índice/artículo del informe."""
    m = re.search(r'<div class="methodology">(?:(?!<details|<article).)*?</aside>\s*</div>', html, re.S)
    return m.group(0) if m else ""


def test_stale_notice_follows_the_hub_punch_style_not_a_bare_callout(world):
    c = client_for()
    _post(c)
    f = world.inbound / "Acme/views/1VR_RAW.json"
    f.write_text(f.read_text(encoding="utf-8").replace('"A"', '"A9"', 1), encoding="utf-8")
    html = c.get("/catalog/flow/assessment?flow=acme-4am", headers=HX).text
    block = _status_block(html)
    # vive dentro del ámbito `.methodology` (sin él el texto perdía la tipografía/color del Hub: 16 px gris claro en lugar de 14.4 px)
    assert block.startswith('<div class="methodology">') and block.rstrip().endswith("</div>")
    assert 'class="callout callout--punch" role="status"' in block
    assert '<span class="callout__icon">💡</span><strong class="callout__title">Assessment desactualizado</strong>' in block   # MISMO ícono y título corto que los demás punch
    assert block.count("<p>") == 2 and "Regenerar assessment" in block                                                       # nota + explicación, luego la acción
    questions = html[html.index("Para qué sirven estas preguntas") - 200: html.index("Para qué sirven estas preguntas")]
    assert 'class="callout callout--punch"' in questions and "💡" in questions                                                 # el punch general que sirve de referencia


def test_the_generate_button_label_takes_the_button_color_like_any_support_link():
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8")
    assert ".asr-generate .asr-generate__label { margin-left: 0; color: inherit; }" in css                                # `.support-link span` (flecha dorada) no tiñe el label del botón


def test_status_notices_are_wrapped_only_when_there_is_a_notice(world):
    c = client_for()
    pre = c.get("/catalog/flow/assessment?flow=acme-4am", headers=HX).text                                                 # sin assessment todavía
    assert '<div class="methodology">' in pre and "Este modelo todavía no tiene assessment" in _status_block(pre)
    _post(c)
    current = c.get("/catalog/flow/assessment?flow=acme-4am", headers=HX).text
    assert _status_block(current) == ""                                                                                     # CURRENT: sin envoltorio vacío ni aviso
