"""Transversalidad EFECTIVA del componente de Feedback del Hub (`partials/dw_feedback.html`).

Criterio de cierre del componente: una única implementación; todos los consumidores equivalentes la usan (solo declaran su contexto);
no quedan implementaciones legacy activas por desconocimiento. El inventario de abajo ES el registro de consumidores: si aparece una
botonera de reacciones fuera del componente, o se migra una pendiente sin actualizarlo, este test falla.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace as N

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.presentation.academy_feedback import feedback_assets, feedback_html
from webapp.templating import templates

ROOT = Path(__file__).resolve().parents[2]
client = TestClient(app)
EMOJIS = ("👍", "👎", "&#128077;", "&#128078;")

# Adaptadores server-side: declaran su contexto y delegan TODO en el componente.
ADAPTERS = {
    "partials/section_feedback.html": "secciones del Copiloto y de Estrategia Comercial",
    "partials/catalog_section_feedback.html": "secciones de fichas de Catálogo",
    "partials/module_feedback.html": "cierre de módulo",
    "partials/catalog_feedback.html": "cierre de ficha de Catálogo / Assessment / instructivo",
    "partials/academy_feedback.html": "cierre de recorrido de Danwor Academy (iframe)",
    "partials/assistant_feedback.html": "respuesta del Asistente",
    "partials/assistant_feedback_thanks.html": "agradecimiento del Asistente",
    "partials/assistant_feedback_form.html": "comentario `down` histórico del Asistente",
    "partials/tree_feedback.html": "resultado de Árbol de decisión (semántica propia útil / no útil)",
}
# Consumidores DINÁMICOS (render en el cliente): declaran un marcador y `hub_reactions.js` clona el componente desde `base.html`.
DYNAMIC = {
    "webapp/static/reporting.js": ("dwFeedback.html(", "/operaciones/reporting/integral/feedback", "report_tab"),
    "webapp/static/commercial.js": ("dwFeedback.html(", "/comercial/feedback", "capability"),
    "webapp/templates/operations/staffing_dashboard.html": ("dw_feedback_mount(", "/operaciones/staffing/feedback", "staffing:asignaciones"),
}
# Marcas de una botonera de reacciones implementada a mano (emojis o botones de voto propios).
LEGACY = re.compile(r'section-feedback__react\b|module-feedback__react"|feedback__emoji|type="submit" name="rating"|send\(\'up\'\)|,\s*\'up\'\)|data-commercial-feedback|ReportingFeedback')
COMPONENT_FILES = {"webapp/templates/partials/dw_feedback.html", "webapp/templates/partials/dw_reaction_picker.html"}


def _render(name, **ctx):
    return templates.get_template(name).render(pack="datasphere_core", **ctx)


def test_every_adapter_delegates_in_the_single_component():
    for name in ADAPTERS:
        src = (ROOT / "webapp/templates" / name).read_text(encoding="utf-8")
        assert 'import dw_feedback' in src and "dw_feedback(" in src, name
        assert "reaction_picker(" not in src and 'name="rating"' not in src, f"{name} re-compone el widget"


def test_no_legacy_reaction_bar_is_left_outside_the_component():
    found = set()
    for path in list((ROOT / "webapp/templates").rglob("*.html")) + list((ROOT / "webapp/static").glob("*.js")) + list((ROOT / "webapp/presentation").glob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel not in COMPONENT_FILES and LEGACY.search(path.read_text(encoding="utf-8", errors="ignore")):
            found.add(rel)
    assert not found, f"botoneras de feedback fuera del componente: {sorted(found)}"


def test_academy_closing_is_the_shared_component_inside_its_iframe():
    html = feedback_html("ACAD01_Paradigmas", "datasphere_core")
    assert 'data-dw-rxp' in html and 'dw-rxp--center' in html and 'data-dw-idea-open' in html and 'value="down"' not in html
    assert 'hx-post="/academy/ACAD01_Paradigmas/feedback"' in html and 'name="context" value="academy:ACAD01_Paradigmas"' in html
    assert not any(e in html for e in EMOJIS) and "Me sirvió" not in html
    assets = feedback_assets("ACAD01_Paradigmas", "datasphere_core")
    assert "htmx.org@2.0.4" in assets and "/static/hub_reactions.js" in assets and "/static/feedback.css" in assets
    assert "fetch(" not in assets and "data-academy-feedback" not in assets                      # sin transporte propio
    assert assets.rstrip().endswith("</div>") and 'id="academy-feedback"' in assets                # cierre inyectado como markup (htmx lo procesa)
    thanks = feedback_html("ACAD01_Paradigmas", "datasphere_core", submitted=True, rating="up")
    assert 'dw-rxp-main--chosen' in thanks and "is-visible" in thanks


def test_catalog_closing_uses_the_component_with_its_own_context():
    html = _render("partials/catalog_feedback.html", catalog_kind="cds", catalog_ref="ZCDS_X", fb_eyebrow="", fb_prompt="")
    assert 'id="catalog-feedback" class="module-feedback catalog-feedback"' in html and 'data-dw-rxp' in html
    assert 'name="context" value="catalog:cds/ZCDS_X"' in html and 'name="catalog_ref" value="ZCDS_X"' in html
    assert not any(e in html for e in EMOJIS)
    legacy_down = _render("partials/catalog_feedback.html", catalog_kind="cds", catalog_ref="Z", submitted=True, rating="down")
    assert 'data-rx="down"' in legacy_down                                                       # histórico legible


def test_tree_keeps_its_own_semantics_with_the_shared_picker():
    html = _render("partials/tree_feedback.html", run=N(id="w1", feedback_rating=None, case_id=None), diagnostic_mode=False)
    assert 'data-dw-rxp' in html and 'value="up"' in html and 'value="down"' in html             # no útil = aprendizaje del caso
    assert 'aria-label="No fue útil"' in html and 'data-dw-idea-open' not in html
    assert not any(e in html for e in EMOJIS)
    form = _render("partials/tree_feedback_form.html", run=N(id="w1"), rating="down")
    assert 'dw-rxp-main--chosen' in form and not any(e in form for e in EMOJIS)


def test_dynamic_consumers_only_declare_their_context():
    for rel, (api, endpoint, identity) in DYNAMIC.items():
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert api in src and endpoint in src and identity in src, rel
        assert "dw-rxp" not in src and "section-feedback__" not in src, f"{rel} reconstruye markup del componente"
    js = (ROOT / "webapp/static/hub_reactions.js").read_text(encoding="utf-8")
    assert "dw-rxp-main" not in js.split("Instancias DINÁMICAS")[1] and "<svg" not in js     # clona; no arma el componente


def test_base_renders_the_component_templates_once_from_the_macro():
    base = (ROOT / "webapp/templates/base.html").read_text(encoding="utf-8")
    assert base.count("dw_feedback_templates(") == 1 and base.count("hub_reactions.js") == 1
    tpl = templates.get_template("partials/dw_feedback.html").module.dw_feedback_templates("datasphere_core")
    assert 'data-dw-feedback-tpl="bar"' in tpl and 'data-dw-action=""' in tpl and "hx-post=\"\"" not in tpl   # transporte JSON
    assert 'data-dw-idea-open' in tpl and 'data-dw-feedback-tpl="idea"' in tpl and 'data-rx="idea"' in tpl
    assert 'value="down"' not in tpl and not any(e in tpl for e in EMOJIS)


def test_mount_marker_carries_the_consumer_contract_without_markup():
    html = templates.get_template("partials/dw_feedback.html").module.dw_feedback_mount(
        {"key": "reporting:cartera", "prompt": "¿Qué te pareció este reporte?", "action": "/operaciones/reporting/integral/feedback",
         "fields": {"report_tab": "cartera"}, "idea": {"module": "operaciones/reporting", "context": "reporting:cartera", "prompt": "x"}})
    assert 'data-dw-feedback-mount="reporting:cartera"' in str(html) and 'data-fields="report_tab=cartera"' in str(html)
    assert "dw-rxp" not in str(html)


def test_idea_from_another_vertical_is_a_suggestion_not_a_negative_vote(monkeypatch, tmp_path):
    from webapp.routers import wizard as wizard_router
    monkeypatch.setattr(wizard_router, "FEEDBACK_LOG_DIR", tmp_path)
    r = client.post("/trees/suggest", data={"title": "Agregar corte por cliente", "module": "operaciones/reporting", "kind": "idea",
                                            "sg_id": "dw-feedback-3", "sg_class": "section-feedback", "context": "reporting:cartera"})
    assert r.status_code == 200 and 'data-rx="idea"' in r.text and 'id="dw-feedback-3"' in r.text
    assert [p.name.startswith("tree_suggestion_") for p in tmp_path.iterdir()] == [True]            # ni reporting_feedback ni down
