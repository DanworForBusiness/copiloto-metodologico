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


def _entry(eid, cliente, am, dominio="Compras"):
    return types.SimpleNamespace(entry_id=eid, cliente=cliente, dominio=dominio, artefactos=[am], path=f"/x/{eid}.md")


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


def test_state_labels_come_from_the_presenter_not_a_second_dictionary():
    import inspect
    src = inspect.getsource(pf)
    assert "presenter.STATE_LABELS" in src and 'CURRENT: "' not in src and "Vigentes" not in src


# ───────────────────────── KPIs ─────────────────────────
def test_only_current_contributes_to_coverage_and_the_denominator_is_explicit(world):
    s = _snap(world)
    cov = s["summary"]["coverage"]
    assert cov["over_current_assessments"] == 3 and cov["average_strict"] == pytest.approx((0.9 + 0.7 + 0.8) / 3)
    kpis = {k["label"]: k["value"] for k in pf.report_view(s)["kpis"]}
    assert kpis["Activos del catálogo"] == "6" and kpis["Assessments vigentes · de 6"] == "3 / 6"
    assert any(label.startswith("Cobertura promedio") and "3 assessments vigentes" in label for label in kpis)
    assert any(label.startswith("Requieren revisión") and value == "3" for label, value in kpis.items())


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
    assert set(snap) == {"schema_version", "generated_at", "client", "catalog_assets", "states", "summary", "assets", "recurring_findings"}
    fresh = pf.build_snapshot(world.entries["a"], PACK)
    fresh["generated_at"] = snap["generated_at"]
    assert snap == fresh and md.text == pf.to_markdown(snap)                                                    # JSON = snapshot; MD = f(snapshot)
    for a in snap["assets"]:
        assert (a["asset_id"] or a["domain"]) in html.text
    assert 'data-state="STALE"' in html.text and 'data-state="NOT_YET_ASSESSED"' in html.text
    assert "3 / 6" in html.text and "Hallazgos recurrentes" in html.text and "Frontera de capas" in html.text
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
    own = {c for c in used - vocabulary if not c.startswith("catalog-card__verdict--")}           # los tonos del badge son los del informe individual
    assert not own, own
    assert re.findall(r'<span class="section-eyebrow">\[(\d\d)\]</span>', html) == ["01", "02", "03"]      # encabezado de sección compartido
    for own in ("tone-gold", "admin-help", "asm-client", "asr-client", "portfolio"):
        assert own not in html, own                                                                  # sin título dorado propio ni textos/estilos ad hoc


def test_no_portfolio_specific_visual_css_exists():
    css = (REPO / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    assert not re.search(r"\.(asm|asr)-client|\.portfolio|\.client-assessment|\.cli-", css)
