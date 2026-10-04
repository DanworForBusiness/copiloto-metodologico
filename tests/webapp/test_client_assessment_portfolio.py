"""test_client_assessment_portfolio.py — "Assessment del cliente" (consolidado V1): modelo de lectura DERIVADO, solo lectura.

Contrato: la unidad son las ENTRADAS del Catálogo del cliente (no se las llama "modelos"/"raíces"); el estado de cada una es el canónico del
assessment individual (``assessment_service.statuses_for_entries``, solo lectura); HTML, JSON y Markdown salen del MISMO snapshot; abrirlo NO
conecta a Datasphere ni genera assessments; solo CURRENT aporta métricas ("—", nunca 0, en el resto); confianza = distribución; sin pestaña nueva.
"""
from __future__ import annotations

import json
import re
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import webapp.main as main
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import auth_service, catalog_reads
from webapp.services import client_assessment_portfolio as pf

REPO = Path(__file__).resolve().parents[2]
PACK = "datasphere_core"
HX = {"HX-Request": "true"}


def _entry(eid, cliente, am, dominio="Compras", veredicto="buena"):
    return types.SimpleNamespace(entry_id=eid, cliente=cliente, dominio=dominio, veredicto=veredicto, artefactos=[am], path=f"/x/{eid}.md")


def _group(gid, title, n=1):
    return {"id": gid, "title": title, "occurrence_count": n}


def _profile(strict=0.9, confidence="HIGH", observations=(), reviews=()):
    return {
        "assessment_state": "ASSESSED", "schema_version": "modeling_assessment.assessment/v1", "scope": {"root_type": "analytic-model"},
        "summary": {"coverage": {"strict": strict}, "confidence": {"level": confidence}},
        "observations": [_group(*g) for g in observations], "review_required": [_group(*g) for g in reviews],
    }


def _status(entry, state, profile=None, **kw):
    am = None if state == svc.NOT_APPLICABLE else entry.artefactos[0]
    return svc.AssessmentStatus(state, entry.entry_id, entry.cliente, am, profile=profile, generated_at="2026-10-04T10:00:00+00:00", **kw)


@pytest.fixture()
def world(monkeypatch):
    e = {n: _entry(n, "Acme", f"AM_{n.upper()}", dominio=f"D{n}") for n in "abcdef"}
    other = _entry("z", "Otro", "AM_Z")
    entries = [*e.values(), other]
    statuses = {
        "a": _status(e["a"], svc.CURRENT, _profile(0.90, "HIGH", [("LAYER_BOUNDARY", "Frontera de capas", 7)], [("FIELD_VOCABULARY_MIX", "Mezcla de vocabulario", 3)])),
        "b": _status(e["b"], svc.CURRENT, _profile(0.70, "MEDIUM", [("LAYER_BOUNDARY", "Frontera de capas", 1)], [("LABEL_NOT_ENRICHING", "Etiqueta sin aporte", 2)])),
        "c": _status(e["c"], svc.CURRENT, _profile(0.80, "LOW", reviews=[("FIELD_VOCABULARY_MIX", "Mezcla de vocabulario", 1)])),
        "d": _status(e["d"], svc.STALE, _profile(0.99, "HIGH", reviews=[("OLD", "Viejo", 1)])),
        "e": _status(e["e"], svc.NOT_YET_ASSESSED),
        "f": _status(e["f"], svc.FAILED, reason="No se pudo generar", error_code="GENERATION_ERROR"),
        "z": _status(other, svc.CURRENT, _profile(0.10, "LOW", reviews=[("OTRO", "Otro cliente", 9)])),
    }
    calls = []

    def fake_statuses(batch):
        batch = list(batch)
        calls.append([x.entry_id for x in batch])
        return {x.entry_id: statuses[x.entry_id] for x in batch}
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (types.SimpleNamespace(find_flow_by_id=lambda es, i: next((x for x in es if x.entry_id == i), None)), entries))
    monkeypatch.setattr(svc, "statuses_for_entries", fake_statuses)
    return types.SimpleNamespace(entries=e, statuses=statuses, calls=calls)


def _client(email="user@danwor.com"):
    c = TestClient(main.app)
    if email:
        c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def _snap(world):
    return pf.build_snapshot(world.entries["a"], PACK)


# ───────────────────────── universo / grupo por cliente ─────────────────────────
def test_catalog_entries_are_grouped_by_client_and_nothing_else(world):
    s = _snap(world)
    assert s["client"] == "Acme" and s["catalog_assets"] == 6
    assert [r["entry_id"] for r in s["assets"]] == ["a", "b", "c", "d", "e", "f"]                  # sin entradas de `Otro`
    assert world.calls == [["a", "b", "c", "d", "e", "f"]]
    assert "models" not in s and "published_models" not in s                                        # no se afirma "modelos publicados"


def test_not_yet_assessed_entries_stay_visible_with_dashes(world):
    row = {r["entry_id"]: r for r in _snap(world)["assets"]}["e"]
    assert row["state"] == svc.NOT_YET_ASSESSED and row["state_label"] == presenter.STATE_LABELS[svc.NOT_YET_ASSESSED]
    assert (row["coverage_text"], row["confidence_text"], row["observation_occurrences"], row["review_occurrences"]) == ("—", "—", None, None)


def test_states_are_the_canonical_individual_states_and_stale_stays_stale(world):
    s = _snap(world)
    assert list(s["states"]) == list(svc.STATES)
    assert s["states"] == {svc.CURRENT: 3, svc.STALE: 1, svc.NOT_YET_ASSESSED: 1, svc.NOT_APPLICABLE: 0, svc.FAILED: 1, svc.INCOMPATIBLE_SCHEMA: 0}
    stale = {r["entry_id"]: r for r in s["assets"]}["d"]
    assert stale["state"] == svc.STALE and stale["coverage_text"] == "—" and stale["confidence_text"] == "—"     # STALE no aporta métricas


def test_json_and_markdown_keep_the_canonical_presenter_state_labels(world):
    """El mapeo corto es solo del HTML del consolidado: el snapshot/JSON y el Markdown conservan los rótulos canónicos del presenter."""
    c = _client()
    snap = c.get("/catalog/client/assessment.json?flow=a").json()
    assert {r["state"]: r["state_label"] for r in snap["assets"]} == {r["state"]: presenter.STATE_LABELS[r["state"]] for r in snap["assets"]}
    md = c.get("/catalog/client/assessment.md?flow=a").text
    assert f"- {presenter.STATE_LABELS[svc.CURRENT]}: 3" in md


# ───────────────────────── KPIs ─────────────────────────
def test_only_current_contributes_to_coverage_and_the_denominator_is_explicit(world):
    s = _snap(world)
    cov = s["summary"]["coverage"]
    assert cov["over_current_assessments"] == 3 and cov["average_strict"] == pytest.approx((0.9 + 0.7 + 0.8) / 3)
    kpis = {k["label"]: k["value"] for k in pf.report_view(s)["kpis"]}
    assert list(kpis) == ["Total de Artefactos", "Assessments vigentes", "Requieren revisión", "Cobertura promedio"]   # sin denominadores en el texto
    assert kpis["Total de Artefactos"] == "6" and kpis["Assessments vigentes"] == "3" and kpis["Requieren revisión"] == "3"
    assert kpis["Cobertura promedio"] == presenter.percent((0.9 + 0.7 + 0.8) / 3)                              # misma semántica, sin cambios
    assert cov["over_current_assessments"] == 3                                                                # el denominador sigue en el snapshot


def test_no_current_assessment_means_a_dash_not_zero(world, monkeypatch):
    monkeypatch.setattr(svc, "statuses_for_entries", lambda batch: {x.entry_id: _status(x, svc.NOT_YET_ASSESSED) for x in batch})
    s = _snap(world)
    assert s["summary"]["coverage"] == {"average_strict": None, "over_current_assessments": 0}
    assert pf.report_view(s)["kpis"][-1]["value"] == "—"


def test_confidence_is_a_distribution_never_an_average(world):
    s = _snap(world)
    assert s["summary"]["confidence_distribution"] == {"HIGH": 1, "MEDIUM": 1, "LOW": 1}
    assert [(r["label"], r["count"]) for r in pf.report_view(s)["confidence_rows"]] == [("Alta", 1), ("Media", 1), ("Baja", 1)]


def test_recurring_findings_count_each_entry_once_and_sort_deterministically(world):
    f = {x["id"]: x for x in _snap(world)["recurring_findings"]}
    assert f["LAYER_BOUNDARY"]["entries"] == 2 and f["LAYER_BOUNDARY"]["asset_ids"] == ["AM_A", "AM_B"]          # 7 + 1 ocurrencias → 2 entradas
    assert f["FIELD_VOCABULARY_MIX"]["entries"] == 2 and f["LABEL_NOT_ENRICHING"]["entries"] == 1
    assert "OLD" not in f and "OTRO" not in f                                                                    # ni STALE ni otro cliente
    assert [x["id"] for x in _snap(world)["recurring_findings"]] == ["FIELD_VOCABULARY_MIX", "LAYER_BOUNDARY", "LABEL_NOT_ENRICHING"]


def test_no_numeric_methodological_score_band_or_weight(world):
    blob = (json.dumps(_snap(world), ensure_ascii=False) + pf.to_markdown(_snap(world))).lower()
    for forbidden in ('"score"', "weight", "penalty", "pleno", "banda", "puntaje de madurez"):
        assert forbidden not in blob


# ───────────────────────── resiliencia ─────────────────────────
def test_a_broken_batch_read_degrades_per_entry(world, monkeypatch):
    monkeypatch.setattr(svc, "statuses_for_entries", lambda batch: (_ for _ in ()).throw(RuntimeError("boom")))

    def one(entry):
        if entry.entry_id == "b":
            raise RuntimeError("roto")
        return world.statuses[entry.entry_id]
    monkeypatch.setattr(svc, "status_for_entry", one)
    s = _snap(world)
    assert {r["entry_id"]: r["state"] for r in s["assets"]}["b"] == svc.FAILED and s["states"][svc.CURRENT] == 2


# ───────────────────────── drilldown ─────────────────────────
def test_drilldown_uses_the_existing_flow_detail_path(world):
    a = {r["entry_id"]: r for r in _snap(world)["assets"]}["a"]
    assert a["detail_url"] == presenter.flow_url(world.entries["a"], PACK) and a["detail_url"].startswith("/catalog/flow?")


# ───────────────────────── HTML / JSON / Markdown: un solo snapshot ─────────────────────────
def test_html_json_and_markdown_derive_from_one_snapshot(world):
    c = _client()
    html = c.get(f"/catalog/client/assessment?flow=a&pack={PACK}", headers=HX)
    j = c.get("/catalog/client/assessment.json?flow=a")
    md = c.get("/catalog/client/assessment.md?flow=a")
    assert html.status_code == j.status_code == md.status_code == 200
    snap = j.json()
    assert set(snap) == {"schema_version", "generated_at", "client", "catalog_assets", "states", "summary", "assets", "recurring_findings", "modeling_composition"}   # + composición del modelado (aditiva)
    fresh = pf.build_snapshot(world.entries["a"], PACK)
    fresh["generated_at"] = snap["generated_at"]
    assert snap == fresh and md.text == pf.to_markdown(snap)                                                    # JSON = snapshot; MD = f(snapshot)
    for a in snap["assets"]:
        assert (a["asset_id"] or a["domain"]) in html.text
    assert 'data-state="STALE"' in html.text and 'data-state="NOT_YET_ASSESSED"' in html.text
    assert "Hallazgos recurrentes" in html.text and "Frontera de capas" in html.text
    assert "attachment" in j.headers["content-disposition"] and ".json" in j.headers["content-disposition"]
    for forbidden in ("review_required", "fact_index", "rule_results", "profile_json"):                          # sin payload de perfiles individuales
        assert forbidden not in j.text


def test_back_returns_to_the_originating_flow_detail_in_the_same_slot(world):
    html = _client().get(f"/catalog/client/assessment?flow=c&pack={PACK}", headers=HX).text
    back = presenter.flow_url(world.entries["c"], PACK).replace("&", "&amp;")
    assert f'hx-get="{back}"' in html and 'hx-target="#catalog-detail-slot"' in html and "← Volver" in html


def test_direct_navigation_without_htmx_redirects_to_the_flow_detail(world):
    r = _client().get(f"/catalog/client/assessment?flow=a&pack={PACK}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == presenter.flow_url(world.entries["a"], PACK)


# ───────────────────────── solo lectura: cero Datasphere, cero generación ─────────────────────────
def test_opening_the_portfolio_runs_no_extraction_and_no_assessment_generation(world, monkeypatch):
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    from webapp.services import datasphere_assessment_service as asd
    monkeypatch.setattr(dsp, "run_export", lambda *a, **k: pytest.fail("el consolidado no extrae de Datasphere"))
    monkeypatch.setattr(asd, "run", lambda *a, **k: pytest.fail("no ejecuta el assessment ad-hoc"))
    monkeypatch.setattr(svc, "generate", lambda *a, **k: pytest.fail("no genera assessments"))
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: pytest.fail("no ejecuta el motor de assessment"))
    monkeypatch.setattr(svc.ma_shadow, "assess", lambda *a, **k: pytest.fail("no ejecuta el Modeling Assessment"))
    monkeypatch.setattr(asd, "list_results", lambda *a, **k: pytest.fail("no consulta resultados ad-hoc"))
    c = _client()
    assert c.get(f"/catalog/client/assessment?flow=a&pack={PACK}", headers=HX).status_code == 200
    assert c.get("/catalog/client/assessment.json?flow=a").status_code == 200 and c.get("/catalog/client/assessment.md?flow=a").status_code == 200


def test_service_uses_only_read_apis():
    import inspect
    src = inspect.getsource(pf)
    assert "statuses_for_entries" in src
    for forbidden in ("svc.generate", "_engine_assess", "run_export", "ExportRequest", "client_secret", "save_result", "_store_envelope", "modeling_assessment"):
        assert forbidden not in src                                                                             # tampoco importa el paquete directo


def test_routes_are_get_only_and_ask_for_no_credentials(world):
    c = _client()
    for path in ("/catalog/client/assessment", "/catalog/client/assessment.json", "/catalog/client/assessment.md"):
        for method in ("post", "put", "delete", "patch"):
            assert getattr(c, method)(f"{path}?flow=a").status_code == 405, (method, path)
    html = c.get(f"/catalog/client/assessment?flow=a&pack={PACK}", headers=HX).text
    assert 'type="password"' not in html and "datasphere_client_secret" not in html


def test_requires_a_session_and_unknown_flow_is_404(world):
    anon = _client(None)
    assert anon.get("/catalog/client/assessment.json?flow=a").status_code == 401
    assert anon.get(f"/catalog/client/assessment?flow=a&pack={PACK}", headers=HX).status_code == 401
    assert _client().get(f"/catalog/client/assessment?flow=nope&pack={PACK}", headers=HX).status_code == 404


# ───────────────────────── entrada / navegación ─────────────────────────
def test_entrypoint_is_a_contextual_action_in_the_flow_detail_and_no_tab_exists(world):
    tpl = (REPO / "webapp" / "templates" / "partials" / "catalog_flow_content.html").read_text(encoding="utf-8")
    assert "Ver Informe de Cliente" in tpl and "/catalog/client/assessment?flow=" in tpl
    link = tpl.split("Ver Informe de Cliente")[0].rsplit("<a ", 1)[1]
    assert 'class="support-link"' in link and 'hx-target="#catalog-detail-slot"' in link            # botón hermano de "Ver Assessment"
    assert "Ver assessment del cliente" not in tpl and "asm-client-link" not in tpl                 # sin el link de texto anterior
    module = (REPO / "webapp" / "templates" / "module.html").read_text(encoding="utf-8")
    assert "Assessment del cliente" not in module and "assessment-cliente" not in module
    catalog_tab = (REPO / "webapp" / "templates" / "partials" / "catalog_tab.html").read_text(encoding="utf-8")
    assert "client/assessment" not in catalog_tab and "Ver assessment del cliente" not in catalog_tab           # sin acción repetida en cada tarjeta/selector
    cards = (REPO / "webapp" / "templates" / "partials" / "catalog_flows.html").read_text(encoding="utf-8")
    assert "client/assessment" not in cards


def test_flow_detail_renders_the_entrypoint(world, monkeypatch):
    # el detalle real del flujo incluye la acción (render del parcial con el contexto mínimo del hero)
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(REPO / "webapp" / "templates")), autoescape=True)
    env.filters["catalog_verdict_class"] = lambda v: "x"
    env.filters["catalog_verdict_label"] = lambda v: "x"
    src = (REPO / "webapp" / "templates" / "partials" / "catalog_flow_content.html").read_text(encoding="utf-8")
    hero = src[src.index('<section class="module-hero'): src.index("</section>")]
    out = env.from_string(hero + "</section>").render(assessment_header=None, cliente="Acme", dominio="D", entry_id="flow-1", pack=PACK,
                                                      veredicto="x", apto="y", n_art=1)
    assert "Ver Informe de Cliente" in out and "/catalog/client/assessment?flow=flow-1&pack=datasphere_core" in out
    group = out[out.index("support-links"): out.index("</div>", out.index("support-links"))]
    assert 'class="support-link"' in group and "Ver Informe de Cliente" in group


def test_no_new_top_level_decision_tab():
    module = (REPO / "webapp" / "templates" / "module.html").read_text(encoding="utf-8")
    nav = module[module.index('<nav class="tool-tabs'): module.index("</nav>", module.index('<nav class="tool-tabs'))]
    assert re.findall(r">([^<]+)</button>", nav) == ["Sección", "Árbol", "Catálogo", "Asistente"] and "Assessment del cliente" not in nav
    routes = (REPO / "webapp" / "routers" / "modules.py").read_text(encoding="utf-8")
    assert '_MODULE_TABS = {"section", "tree", "catalogo", "assistant"}' in routes


# ───────────────────────── reuso del contrato visual de la familia de informes ─────────────────────────
def _tpl(name):
    return (REPO / "webapp" / "templates" / "partials" / name).read_text(encoding="utf-8")


def _classes(html):
    return {c for m in re.findall(r'class="([^"]+)"', html) for c in m.split()}


def test_portfolio_reuses_the_individual_report_structure_not_a_copy(world):
    """El consolidado es OTRO informe de la familia del informe individual: mismo wrapper/hero/export/KPIs/secciones, mismo macro de
    encabezado de sección (importado, no copiado) y ningún estilo propio de aspecto."""
    client, single = _tpl("client_assessment_report.html"), _tpl("catalog_assessment_report.html")
    macros = _tpl("assessment_report_macros.html")
    assert 'import heading' in client and 'import heading' in single and "macro heading" in macros
    assert "macro heading" not in client and "macro heading" not in single                       # una sola definición del encabezado de sección
    html = _client().get(f"/catalog/client/assessment?flow=a&pack={PACK}", headers=HX).text
    shared = {"catalog-back", "module-hero", "eyebrow", "asr-title", "hero-meta", "catalog-verdict-line", "support-links", "asr-actions",
              "support-link", "dashboard-section", "activity-kpi-grid", "activity-kpi", "methodology", "catalog-detail", "content-section",
              "content-heading", "content-heading-copy", "section-eyebrow", "section-subtitle"}
    used = _classes(html)
    assert shared <= used
    # todas las clases del consolidado existen en el informe individual o en el macro compartido (ninguna clase propia del Portfolio)
    vocabulary = set(re.findall(r"[A-Za-z][\w-]+", single + macros))                          # incluye clases armadas con Jinja (`catalog-meta-dot{% if … %}`)
    own = {c for c in used - vocabulary if not c.startswith(("catalog-card__verdict--", "callout--"))}           # los tonos del badge son los del informe individual
    assert not own, own
    assert re.findall(r'<span class="section-eyebrow">\[(\d\d)\]</span>', html) == ["01", "02", "03", "04"]      # encabezado de sección compartido (la 04 es la Composición del modelado, aditiva)
    for own in ("tone-gold", "admin-help", "asm-client", "asr-client", "portfolio"):
        assert own not in html, own                                                                  # sin título dorado propio ni textos/estilos ad hoc


def test_no_portfolio_specific_visual_css_exists():
    css = (REPO / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    assert not re.search(r"\.(asm|asr)-client|\.portfolio|\.client-assessment|\.cli-", css)


# ───────────────────────── pulido V1: terminología, identidad, badges, veredicto del catálogo, divisor ─────────────────────────
def test_report_says_artefacto_not_activo_in_html_json_and_markdown(world):
    c = _client()
    html = c.get(f"/catalog/client/assessment?flow=a&pack={PACK}", headers=HX).text
    md = c.get("/catalog/client/assessment.md?flow=a").text
    txt = re.sub(r"<[^>]+>", " ", html)
    for surface in (txt, md):
        assert "Total de Artefactos" in surface and "Artefacto" in surface
        assert not re.search(r"\bactivos?\b", surface, re.I), re.findall(r".{20}\bactivos?\b.{20}", surface, re.I)
    assert [k["label"] for k in pf.report_view(_snap(world))["kpis"]][0] == "Total de Artefactos"


def test_artifact_identity_is_one_label_without_duplicated_description():
    assert pf._identity("Despachos", "AM_DESPACHOS", "Despachos") == ("Despachos · AM_DESPACHOS", None)
    assert pf._identity(None, "AM_RESERVA_MP", "AM_RESERVA_MP") == ("AM_RESERVA_MP", None)
    assert pf._identity("Ventas", "AM_V", "Comercial") == ("Ventas · AM_V", "Comercial")        # un concepto distinto NO se descarta en silencio
    assert pf._identity(None, None, "Compras") == ("Compras", None)


def test_every_surface_describes_the_artifact_row_the_same_way(world):
    c = _client()
    snap = c.get("/catalog/client/assessment.json?flow=a").json()
    html = c.get(f"/catalog/client/assessment?flow=a&pack={PACK}", headers=HX).text
    md = c.get("/catalog/client/assessment.md?flow=a").text
    table = html[html.index('id="cli-artefactos"'): html.index('id="cli-hallazgos"')]
    for r in snap["assets"]:
        assert r["artifact_label"] in html and r["artifact_label"] in md
        assert table.count(f"<code>{r['artifact_label']}</code>") == 1                                # una sola vez por fila (sin segunda línea repetida)


def test_all_states_use_the_same_badge_and_neutral_states_get_the_gray_variant(world):
    html = _client().get(f"/catalog/client/assessment?flow=a&pack={PACK}", headers=HX).text
    summary = html[html.index('id="cli-resumen"'): html.index('id="cli-artefactos"')]
    rows = re.findall(r'<tr data-state="(\w+)"><td><span class="([^"]+)">', summary)
    assert [s for s, _ in rows] == list(svc.STATES)
    assert all(cls.startswith("catalog-card__verdict ") for _, cls in rows)                             # el MISMO primitivo para todos
    tone = {s: cls.rsplit("--", 1)[1] for s, cls in rows}
    assert tone[svc.NOT_YET_ASSESSED] == tone[svc.NOT_APPLICABLE] == "neutra"
    assert tone[svc.CURRENT] == "plena" and tone[svc.STALE] == "buena" and tone[svc.FAILED] == "parcial"   # semántica de estados sin cambios
    css = (REPO / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    assert ".catalog-card__verdict--neutra {" in css and "padding" not in css.split(".catalog-card__verdict--neutra {")[1].split("}")[0]


def test_catalog_verdict_is_a_labeled_secondary_column_and_never_a_score(world):
    c = _client()
    html = c.get(f"/catalog/client/assessment?flow=a&pack={PACK}", headers=HX).text
    assert "<th>Veredicto del catálogo</th>" in html and "scoring" not in html.lower()
    snap = c.get("/catalog/client/assessment.json?flow=a").json()
    assert {r["catalog_verdict"] for r in snap["assets"]} == {"buena"}                              # CatalogEntry.veredicto tal cual
    kpis = " ".join(k["label"] for k in pf.report_view(snap)["kpis"])
    assert not re.search(r"plena|buena|parcial", kpis, re.I)                                           # nunca un KPI de veredicto
    # el veredicto del catálogo NO se deriva de cobertura/confianza: cambia la cobertura, no cambia el veredicto
    world.statuses["a"].profile["summary"]["coverage"]["strict"] = 0.05
    again = pf.build_snapshot(world.entries["a"], PACK)
    assert {r["entry_id"]: r["catalog_verdict"] for r in again["assets"]} == {r["entry_id"]: r["catalog_verdict"] for r in snap["assets"]}


def test_catalog_detail_labels_the_legacy_verdict_explicitly():
    tpl = (REPO / "webapp" / "templates" / "partials" / "catalog_flow_content.html").read_text(encoding="utf-8")
    line = tpl[tpl.index("Veredicto LEGACY del catálogo"):]
    assert line.index("Veredicto del catálogo") < line.index("catalog_verdict_label") < line.index("{{ apto }}")   # etiqueta · BUENA · texto canónico


def test_report_hero_divider_rhythm_is_symmetric_and_shared_by_both_reports():
    css = (REPO / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    pad = re.search(r"\.module-hero\.asr-hero \{ padding-bottom: (\d+)px; \}", css).group(1)
    gap = re.search(r"\.module-hero\.asr-hero \+ \* \{ margin-top: (\d+)px; \}", css).group(1)
    assert pad == gap                                                                                    # mismo aire arriba y abajo del divisor
    assert ".module-hero.asr-hero > :last-child { margin-bottom: 0; }" in css
    for name in ("client_assessment_report.html", "catalog_assessment_report.html"):
        assert '<section class="module-hero asr-hero">' in _tpl(name), name


# ───────────────────────── backfill histórico (offline) ─────────────────────────
def test_backfill_generates_only_not_yet_assessed_and_skips_the_rest(world):
    from webapp.services import assessment_backfill as bf
    generated = []

    def fake_generate(entry):
        generated.append(entry.entry_id)
        return _status(entry, svc.CURRENT, _profile(0.8, "HIGH"))
    out = bf.backfill(list(world.entries.values()), status_for=lambda e: world.statuses[e.entry_id], generate=fake_generate)
    assert generated == ["e"] and [x["entry_id"] for x in out["generated"]] == ["e"]
    assert {x["entry_id"]: x["state"] for x in out["skipped"]} == {"a": svc.CURRENT, "b": svc.CURRENT, "c": svc.CURRENT, "d": svc.STALE, "f": svc.FAILED}
    assert out["counts"] == {"generated": 1, "skipped": 5, "failed": 0}


def test_backfill_continues_after_an_individual_failure_and_reports_it(world):
    from webapp.services import assessment_backfill as bf
    entries = [_entry("x1", "Acme", "AM_X1"), _entry("x2", "Acme", "AM_X2"), _entry("x3", "Acme", "AM_X3")]

    def generate(entry):
        if entry.entry_id == "x2":
            raise RuntimeError("pool roto")
        return _status(entry, svc.CURRENT, _profile())
    out = bf.backfill(entries, status_for=lambda e: _status(e, svc.NOT_YET_ASSESSED), generate=generate)
    assert out["counts"] == {"generated": 2, "skipped": 0, "failed": 1} and "pool roto" in out["failed"][0]["reason"]


def test_backfill_dry_run_generates_nothing(world):
    from webapp.services import assessment_backfill as bf
    out = bf.backfill(world.entries.values(), dry_run=True, status_for=lambda e: world.statuses[e.entry_id],
                      generate=lambda e: pytest.fail("dry-run no genera"))
    assert out["counts"]["generated"] == 0 and out["dry_run"] is True


def test_backfill_uses_only_the_local_pool_no_datasphere_no_credentials(world, monkeypatch):
    import inspect
    from webapp.services import assessment_backfill as bf
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    from webapp.services import datasphere_assessment_service as asd
    src = inspect.getsource(bf)
    for forbidden in ("run_export", "ExportRequest", "client_secret", "datasphere_publication", "datasphere_assessment_service", "requests", "httpx"):
        assert forbidden not in src, forbidden
    monkeypatch.setattr(dsp, "run_export", lambda *a, **k: pytest.fail("el backfill no extrae de Datasphere"))
    monkeypatch.setattr(asd, "run", lambda *a, **k: pytest.fail("no ejecuta el assessment ad-hoc"))
    monkeypatch.setattr(svc, "generate", lambda e: _status(e, svc.CURRENT, _profile()))
    out = bf.backfill([world.entries["e"]], status_for=lambda e: world.statuses[e.entry_id], generate=svc.generate)
    assert out["counts"]["generated"] == 1


def test_opening_the_portfolio_never_runs_the_backfill_or_generates(world, monkeypatch):
    from webapp.services import assessment_backfill as bf
    monkeypatch.setattr(bf, "backfill", lambda *a, **k: pytest.fail("un GET jamás ejecuta el backfill"))
    monkeypatch.setattr(svc, "generate", lambda *a, **k: pytest.fail("un GET jamás genera assessments"))
    assert _client().get(f"/catalog/client/assessment?flow=a&pack={PACK}", headers=HX).status_code == 200
    routers = (REPO / "webapp" / "routers" / "catalog_assessment.py").read_text(encoding="utf-8")
    assert "assessment_backfill" not in routers and "assessment_backfill" not in (REPO / "webapp" / "main.py").read_text(encoding="utf-8")


# ───────────────────────── pulido cosmético: KPIs simples, estados cortos, cobertura entera, subtexto ─────────────────────────
SHORT = {svc.CURRENT: "Vigente", svc.STALE: "Desactualizado", svc.NOT_YET_ASSESSED: "Sin Iniciar",
         svc.NOT_APPLICABLE: "No Aplica", svc.FAILED: "No Disponible", svc.INCOMPATIBLE_SCHEMA: "No Compatible"}


def test_kpi_tiles_are_simple_without_denominators(world):
    html = _client().get(f"/catalog/client/assessment?flow=a&pack={PACK}", headers=HX).text
    tiles = re.findall(r'<span class="activity-kpi__value">([^<]*)</span><span class="activity-kpi__label">([^<]*)</span>', html)
    assert [lbl for _, lbl in tiles][:4] == ["Total de Artefactos", "Assessments vigentes", "Requieren revisión", "Cobertura promedio"]   # fila ejecutiva intacta
    assert [v for v, _ in tiles][:3] == ["6", "3", "3"]
    executive = tiles[:4]
    assert " / " not in "".join(v for v, _ in executive) and not re.search(r"\bde \d|sobre \d", "".join(l for _, l in executive))


def test_state_badges_use_one_short_label_mapping_in_summary_and_artifacts(world):
    assert pf.STATE_DISPLAY_LABELS == SHORT                                                         # una sola fuente, exactamente estos rótulos
    html = _client().get(f"/catalog/client/assessment?flow=a&pack={PACK}", headers=HX).text
    summary = html[html.index('id="cli-resumen"'): html.index('id="cli-artefactos"')]
    assert re.findall(r'<tr data-state="(\w+)"><td><span class="[^"]+">([^<]+)</span>', summary) == [(s, SHORT[s]) for s in svc.STATES]
    arts = html[html.index('id="cli-artefactos"'): html.index('id="cli-hallazgos"')]
    for state, label in re.findall(r'<tr data-entry="[^"]+" data-state="(\w+)">.*?<td><span class="catalog-card__verdict[^"]*">([^<]+)</span>', arts, re.S):
        assert label == SHORT[state]
    assert "Assessment vigente" not in summary and "Assessment vigente" not in arts


def test_artifact_table_shows_whole_percent_but_snapshot_and_json_keep_their_precision(world):
    c = _client()
    html = c.get(f"/catalog/client/assessment?flow=a&pack={PACK}", headers=HX).text
    arts = html[html.index('id="cli-artefactos"'): html.index('id="cli-hallazgos"')]
    cells = re.findall(r"<td>(\d+(?:\.\d+)?%)</td>", arts)
    assert cells and all("." not in x for x in cells) and {"90%", "70%", "80%"} <= set(cells)
    snap = c.get("/catalog/client/assessment.json?flow=a").json()
    cov = {r["entry_id"]: r["coverage_strict"] for r in snap["assets"]}
    assert cov["a"] == 0.9 and cov["b"] == 0.7 and cov["c"] == 0.8 and cov["e"] is None             # valor canónico intacto
    assert {r["entry_id"]: r["coverage_text"] for r in snap["assets"]}["a"] == presenter.percent(0.9) == "90.0%"
    assert "—" in arts                                                                               # no vigentes siguen en guion


def test_percent_precision_is_presentation_only_and_default_is_unchanged():
    assert presenter.percent(0.935) == "93.5%" and presenter.percent(0.935, 1) == "93.5%"           # formato canónico por defecto
    assert presenter.percent(0.935, 0) == "94%" and presenter.percent(0.925, 0) == "93%" and presenter.percent(1.0, 0) == "100%"
    assert presenter.percent(0.9, 0) == "90%" and presenter.percent(None, 0) == "n/d"


def test_artifact_section_subtitle_is_the_approved_text_in_the_shared_primitive(world):
    html = _client().get(f"/catalog/client/assessment?flow=a&pack={PACK}", headers=HX).text
    arts = html[html.index('id="cli-artefactos"'): html.index('id="cli-hallazgos"')]
    sub = re.search(r'<blockquote class="section-subtitle"><p>([^<]+)</p></blockquote>', arts).group(1)
    assert sub == ("Solo los assessments vigentes aportan métricas; los valores representados con guiones significan no disponible. "
                   "El veredicto del catálogo es el histórico (higiene de extracción), distinto del Assessment. "
                   "Cada artefacto abre su detalle en el Catálogo, donde vive el informe individual.")
    head = arts[: arts.index("<table")]
    assert "admin-help" not in arts and "callout" not in head                                        # el subtítulo no es un callout (el cierre va después de la tabla)


# ───────────────────────── cierres narrativos (callout `punch` estándar) ─────────────────────────
def test_confidence_story_follows_the_actual_distribution_with_few_qualitative_branches():
    s = pf.confidence_story
    assert s({"HIGH": 0, "MEDIUM": 0, "LOW": 0}) is None                                            # sin vigentes: no se inventa una conclusión
    solid = s({"HIGH": 9, "MEDIUM": 2, "LOW": 0})
    consistent = s({"HIGH": 11, "MEDIUM": 14, "LOW": 0})
    reasonable = s({"HIGH": 1, "MEDIUM": 20, "LOW": 0})
    cautious = s({"HIGH": 3, "MEDIUM": 3, "LOW": 3})
    assert len({solid, consistent, reasonable, cautious}) == 4                                       # cambiar la distribución cambia la rama
    assert "base sólida" in solid and "predominio de assessments de confianza alta" in solid
    assert "base consistente" in consistent and "confianza media" in consistent and "sin señales significativas de confianza baja" in consistent
    assert "mayor cautela" in cautious and "confianza baja" in cautious
    assert s({"HIGH": 5, "MEDIUM": 5, "LOW": 1}) != cautious and "mayor cautela" in s({"HIGH": 3, "MEDIUM": 4, "LOW": 3})   # baja material (≥ 25%)


def test_catalog_verdict_story_follows_the_historical_verdict_distribution_only():
    s = pf.catalog_verdict_story
    assert s([]) is None and s([None, "?", ""]) is None                                              # sin veredictos usables: se omite
    plena, buena = s(["plena"] * 6 + ["buena"] * 2 + ["parcial"]), s(["buena"] * 6 + ["plena"] * 2 + ["parcial"])
    attention, mixed = s(["plena"] * 3 + ["parcial"] * 3), s(["plena"] * 4 + ["buena"] * 4 + ["parcial"])
    assert len({plena, buena, attention, mixed}) == 4
    assert "sólido" in plena and "Plena" in plena and "mayormente reutilizable" in buena and "veredicto histórico del catálogo" in buena
    assert "requiere atención" in attention and "panorama mixto" in mixed


def test_stories_never_present_the_catalog_verdict_as_an_assessment_score_nor_create_a_number(world):
    texts = [pf.confidence_story({"HIGH": h, "MEDIUM": m, "LOW": b}) for h, m, b in ((9, 1, 0), (1, 9, 0), (1, 1, 5), (3, 3, 0))]
    texts += [pf.catalog_verdict_story(v) for v in (["plena"] * 5, ["buena"] * 5, ["parcial"] * 5, ["plena", "buena"])]
    for t in texts:
        assert not re.search(r"score|scoring|puntaje|\d", t, re.I), t                                # sin número sintético ni lenguaje de puntaje
    snap = _snap(world)
    assert "story" not in " ".join(snap) and not any("story" in k for r in snap["assets"] for k in r)  # el snapshot/JSON no gana campos de puntaje ni de relato


def test_zero_current_assessments_render_no_confidence_callout(world, monkeypatch):
    for st in world.statuses.values():
        if st.state == svc.CURRENT:
            world.statuses[st.entry_id] = _status(world.entries.get(st.entry_id, _entry(st.entry_id, "Acme", "AM_X")), svc.NOT_YET_ASSESSED)
    html = _client().get(f"/catalog/client/assessment?flow=a&pack={PACK}", headers=HX).text
    resumen = html[html.index('id="cli-resumen"'): html.index('id="cli-artefactos"')]
    assert "callout" not in resumen                                                                  # sin vigentes no hay conclusión inventada
    assert "callout" in html[html.index('id="cli-artefactos"'): html.index('id="cli-hallazgos"')]    # el veredicto del catálogo es independiente del Assessment


def test_both_closures_use_the_standard_punch_callout_primitive(world):
    c = _client()
    html = c.get(f"/catalog/client/assessment?flow=a&pack={PACK}", headers=HX).text
    resumen = html[html.index('id="cli-resumen"'): html.index('id="cli-artefactos"')]
    arts = html[html.index('id="cli-artefactos"'): html.index('id="cli-hallazgos"')]
    for block, story in ((resumen, pf.report_view(_snap(world))["confidence_story"]), (arts, pf.report_view(_snap(world))["catalog_verdict_story"])):
        assert block.count('<aside class="callout callout--punch">') == 1 and story in block
    assert resumen.index("</table>") < resumen.index("<aside") and arts.rindex("</table>") < arts.index("<aside")   # después de las tablas
    client, macros, single = _tpl("client_assessment_report.html"), _tpl("assessment_report_macros.html"), _tpl("catalog_assessment_report.html")
    assert macros.count("macro callout") == 1 and "macro callout" not in client and "macro callout" not in single   # un solo primitivo, importado
    assert "<aside" not in client and "import heading, callout" in client and "import heading, callout" in single
    css = (REPO / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    selectors = re.findall(r"(?m)^\s*([^{}/*\n]*\.callout[^{}\n]*)\{", css)
    assert selectors and not [s for s in selectors if re.search(r"asm|asr|cli-|portfolio|client", s)]      # sin CSS de callout propio (solo el canónico)


def test_markdown_uses_the_same_narrative_source_as_the_html(world):
    c = _client()
    md = c.get("/catalog/client/assessment.md?flow=a").text
    v = pf.report_view(_snap(world))
    assert f"> {v['confidence_story']}" in md and f"> {v['catalog_verdict_story']}" in md
    html = c.get(f"/catalog/client/assessment?flow=a&pack={PACK}", headers=HX).text
    assert v["confidence_story"] in html and v["catalog_verdict_story"] in html
