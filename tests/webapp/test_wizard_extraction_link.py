"""test_wizard_extraction_link.py — Árbol "Opciones de Extracción": el camino
YES/YES/YES recomienda "API Estándar" con un link INLINE clickeable a SAP Help
(I_DataExtractionEnabledView), y el renderer de outcomes gana soporte de links
estructurados de forma GENÉRICA y SEGURA (reusa el mini-markup del Hub). Los
demás caminos y leaves quedan sin cambios."""
from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.copilot_runtime import store
from webapp.main import app          # importa routers → registra el filtro leaf_markup
from webapp.templating import templates

client = TestClient(app)

_SAP_URL = ("https://help.sap.com/docs/SAP_S4HANA_CLOUD/"
            "c0c54048d35849128be8e872df5bea6d/"
            "870d66c6bfc44d6c9f128c888f0c7957.html?locale=en-US")


def _start():
    return store.start(store.tree_by_id("extraction_options"))


# ── navegación ───────────────────────────────────────────────────────────────

def test_yes_yes_yes_reaches_standard_api_leaf():
    run = _start()
    for a in ["ERP", "S4HANA", "Private", "Sí", "Sí", "Sí"]:
        store.answer(run.id, a)
    assert run.error == ""
    assert run.current == "leaf_api_corp_contrato"
    assert run.leaf["decision"] == "API Estándar"


def test_control_paths_outcomes_unchanged():
    # YES → YES → NO  (apta=No)
    run = _start()
    for a in ["ERP", "S4HANA", "Private", "Sí", "Sí", "No"]:
        store.answer(run.id, a)
    assert run.current == "leaf_api_corp_wrap_func"
    # YES → NO  (cumple=No)
    run = _start()
    for a in ["ERP", "S4HANA", "Private", "Sí", "No"]:
        store.answer(run.id, a)
    assert run.current == "leaf_api_corp_wrap_func"
    # NO  (API autorizada=No)
    run = _start()
    for a in ["ERP", "S4HANA", "Private", "No"]:
        store.answer(run.id, a)
    assert run.current == "leaf_api_corp_wrap_tabla"


# ── render del outcome ─────────────────────────────────────────────────────────

def test_yes_yes_yes_render_has_inline_clickable_sap_link():
    run = _start()
    for a in ["ERP", "S4HANA", "Private", "Sí", "Sí"]:
        store.answer(run.id, a)
    r = client.post(f"/wizards/{run.id}/answer", data={"answer": "Sí"})
    assert r.status_code == 200
    html = r.text
    # Título = API Estándar; NO API Corporativa como decisión principal.
    assert "API Estándar" in html and "<h3>API Corporativa" not in html
    # Cuerpo con el copy nuevo.
    assert "El contrato se define en SAP mediante una API estándar" in html
    # I_DataExtractionEnabledView es el anchor clickeable, href EXACTO, sin utm.
    assert (f'<a href="{_SAP_URL}" target="_blank" rel="noopener">'
            f'I_DataExtractionEnabledView</a>') in html
    assert "utm_source" not in html


def test_control_leaf_without_detail_renders_no_detail_paragraph():
    run = _start()
    for a in ["ERP", "S4HANA", "Private"]:
        store.answer(run.id, a)
    r = client.post(f"/wizards/{run.id}/answer", data={"answer": "No"})  # leaf_api_corp_wrap_tabla
    assert r.status_code == 200
    assert "decision-detail" not in r.text          # sin cuerpo enriquecido
    assert "API Corporativa" in r.text              # su decisión sigue igual


# ── seguridad del renderer genérico ───────────────────────────────────────────

def test_leaf_markup_filter_is_safe_and_link_only():
    f = templates.env.filters["leaf_markup"]
    # Link permitido (https) → <a>.
    out = str(f("ver [Doc](https://help.sap.com/x)"))
    assert '<a href="https://help.sap.com/x" target="_blank" rel="noopener">Doc</a>' in out
    # HTML arbitrario en el texto NUNCA se confía (se escapa).
    out2 = str(f("<script>alert(1)</script> y **negrita**"))
    assert "<script>" not in out2 and "&lt;script&gt;" in out2
    assert "<strong>negrita</strong>" in out2
    # javascript: NO produce link (el patrón exige https?://) → queda como texto.
    out3 = str(f("[x](javascript:alert(1))"))
    assert "<a" not in out3 and "javascript:alert(1)" in out3
