"""Consistencia del tile «Convención de nombres y nivel» entre las DOS vistas que presentan el Juicio: Assessment [02] (``catalog_assessment_report.html``) y
Catálogo → Mirada Danwor → Adopción metodológica (``catalog_flow_content.html``). Ambas consumen el MISMO resultado (``assessment_presenter.naming_gate_view``, vía
``judgment_view``): no hay segundo cálculo. Se verifica que, para un mismo Juicio v5, el bloque aparece en las dos con idéntico estado, conclusión y conteos, en PASS,
BLOCK y NOT_ACCREDITED. Solo presentación: no se recalcula el nivel ni se tocan reglas/scoring."""
from __future__ import annotations

import copy
import json

from bs4 import BeautifulSoup

from tests._judgment_cases import fresh
from tests.webapp import test_catalog_assessment_routes as cat
from tests.webapp.test_catalog_assessment_routes import world  # noqa: F401 — fixture
from tools.catalog.methodological_judgment import RULESET_V5, evaluate_judgment
from webapp.services import assessment_presenter as P
from webapp.services import assessment_service as asvc
from webapp import main

V5 = dict(ruleset=RULESET_V5, ratified_contracts=[])


def _base_v5_judgment() -> dict:
    """Juicio v5 real con ``naming_gate`` poblado (kensing: PASS con todas las poblaciones)."""
    return evaluate_judgment(fresh("kensing_gl_line_item_raw"), **V5)


def _variant(state: str) -> dict:
    """Juicio v5 con el estado de naming pedido. PASS = tal cual; BLOCK/NOT_ACCREDITED = cierre LVL-09 (limita BUENA), espejo de la transformación ya usada en v5_presentation."""
    j = _base_v5_judgment()
    if state == "PASS":
        return j
    return {**copy.deepcopy(j), "status": "DETERMINED", "level": "ADECUADA", "level_rule": "LVL-09",
            "naming_gate": {**j["naming_gate"], "state": state}}


def _render(judgment_fn, monkeypatch, *, surface: str) -> str:
    """Genera el assessment de ``acme-4am``, le inyecta el Juicio y devuelve el HTML del informe o del flujo (Mirada Danwor)."""
    c = cat.client_for()
    cat._post(c)
    env = json.loads(json.dumps(asvc._load_envelope("Acme", "4AM_ACME")))
    env["judgment"] = judgment_fn(env["judgment"])
    asvc._store_envelope(env)
    asvc.reset_memory_cache()
    if surface == "report":
        return c.get("/catalog/flow/assessment?flow=acme-4am", headers=cat.HX).text
    # Mirada Danwor: el cuerpo del flujo (secciones .md) se stubea; solo importa la sección «Mirada Danwor», que se arma desde el Juicio gobernado.
    monkeypatch.setattr(main, "_catalog_flow_view", lambda entry, show_assoc=None, pack="": ([], "", bool(show_assoc), False))
    return c.get("/catalog/flow?flow=acme-4am&pack=datasphere_core", headers=cat.HX).text


def _gate(soup: BeautifulSoup):
    sec = soup.select_one("[data-asr-naming-gate]")
    if sec is None:
        return None
    counts = {}
    for row in sec.select(".asr-ng-row"):
        key = row.get("data-asr-ng-cat")
        counts[key] = row.select_one(".asr-ng-count").get_text(strip=True)
    return {"state": sec.get("data-asr-naming-gate"),
            "headline": sec.select_one(".asr-ng-conclusion").get_text(" ", strip=True),
            "detail": sec.select_one("[data-asr-naming-detail]").get_text(" ", strip=True),
            "counts": counts}


# ── 1. El dato es UNO solo: lo que presenta el informe (``judgment_view``) es exactamente ``naming_gate_view`` ──
def test_both_views_derive_the_naming_gate_from_the_single_shared_result():
    for state in ("PASS", "BLOCK", "NOT_ACCREDITED"):
        j = _variant(state)
        assert P.judgment_view(j)["naming_gate"] == P.naming_gate_view(j)       # Assessment [02] y Mirada Danwor (adopción) salen del MISMO naming_gate_view: sin recálculo


# ── 2. Render: el bloque aparece en AMBAS vistas con idéntico estado, conclusión, detalle y conteos ──
def test_naming_gate_is_rendered_identically_in_report_and_mirada_danwor(world, monkeypatch):  # noqa: F811
    for state in ("PASS", "BLOCK", "NOT_ACCREDITED"):
        rep = _gate(BeautifulSoup(_render(lambda _b, s=state: _variant(s), monkeypatch, surface="report"), "html.parser"))
        mir = _gate(BeautifulSoup(_render(lambda _b, s=state: _variant(s), monkeypatch, surface="flow"), "html.parser"))
        assert rep is not None, f"el informe no dibuja el tile de naming ({state})"
        assert mir is not None, f"Mirada Danwor no dibuja el tile de naming ({state})"
        assert rep["state"] == mir["state"] == state
        assert rep["headline"] == mir["headline"] and rep["detail"] == mir["detail"]
        assert rep["counts"] == mir["counts"] and rep["counts"]                  # mismos conteos por población (gobernables/conformes/recuperables/no recuperables/excluidos/identidad)


# ── 2b. Contrato de diseño: el Assessment conserva el drill-down a artefactos; Mirada Danwor NO (misma conclusión y conteos; el detalle vive en el Assessment) ──
def test_mirada_danwor_naming_block_omits_artifact_drilldown_but_report_keeps_it(world, monkeypatch):  # noqa: F811
    def jf(_b):
        j = _base_v5_judgment()
        g = {**j["naming_gate"], "state": "BLOCK", "non_recoverable": 2, "non_recoverable_artifacts": ["VT_UNO", "VT_DOS"]}
        return {**copy.deepcopy(j), "status": "DETERMINED", "level": "ADECUADA", "level_rule": "LVL-09", "naming_gate": g}
    rep = BeautifulSoup(_render(jf, monkeypatch, surface="report"), "html.parser").select_one("[data-asr-naming-gate]")
    mir = BeautifulSoup(_render(jf, monkeypatch, surface="flow"), "html.parser").select_one("[data-asr-naming-gate]")
    assert rep.select("a.asr-ng-count") and rep.select(".asr-ng-detail")               # Assessment: conserva el drill-down a artefactos
    assert not mir.select("a.asr-ng-count") and not mir.select(".asr-ng-detail")        # Mirada Danwor: conteos estáticos, sin drill-down
    assert rep.select_one(".asr-ng-conclusion").get_text(strip=True) == mir.select_one(".asr-ng-conclusion").get_text(strip=True)   # misma conclusión


# ── 3. Semántica honesta por estado, en la vista de Mirada Danwor (no solo en el informe) ──
def test_mirada_danwor_block_does_not_assert_a_breach_when_responsibility_is_not_accredited(world, monkeypatch):  # noqa: F811
    mir = _gate(BeautifulSoup(_render(lambda _b: _variant("NOT_ACCREDITED"), monkeypatch, surface="flow"), "html.parser"))
    assert mir["state"] == "NOT_ACCREDITED"
    assert "no se" in mir["headline"].lower() and "no se afirma un incumplimiento" in (mir["headline"] + " " + mir["detail"]).lower()


def test_mirada_danwor_block_states_the_consequence_on_buena_when_blocked(world, monkeypatch):  # noqa: F811
    mir = _gate(BeautifulSoup(_render(lambda _b: _variant("BLOCK"), monkeypatch, surface="flow"), "html.parser"))
    assert mir["state"] == "BLOCK" and "BUENA" in mir["headline"]


def test_pass_in_mirada_danwor_does_not_assert_a_violation(world, monkeypatch):  # noqa: F811
    mir = _gate(BeautifulSoup(_render(lambda _b: _variant("PASS"), monkeypatch, surface="flow"), "html.parser"))
    assert mir["state"] == "PASS" and "no impide" in mir["headline"]


# ── 4. El 3.er tile «Conformidad de nombres» (%) aparece en AMBAS vistas con el MISMO valor (mismo dato §28 persistido, sin recálculo) ──
def _conformity_tile(soup: BeautifulSoup):
    for t in soup.select(".activity-kpi"):
        lab = t.select_one(".activity-kpi__label")
        if lab and lab.get_text(strip=True) == "Conformidad de nombres":
            return t.select_one(".activity-kpi__value").get_text(strip=True)
    return None


def test_naming_conformity_percent_tile_present_and_equal_in_both_views(world, monkeypatch):  # noqa: F811
    rep = _conformity_tile(BeautifulSoup(_render(lambda _b: _variant("BLOCK"), monkeypatch, surface="report"), "html.parser"))
    mir = _conformity_tile(BeautifulSoup(_render(lambda _b: _variant("BLOCK"), monkeypatch, surface="flow"), "html.parser"))
    assert rep is not None, "el informe [02] no muestra el tile 'Conformidad de nombres' (%)"
    assert mir is not None, "Mirada Danwor → Adopción no muestra el tile 'Conformidad de nombres' (%)"
    assert rep.endswith("%") and rep == mir                                    # mismo % en ambas (mismo dato §28, sin recalcular)


# ── 5. Callout de cierre «Lectura de la adopción» presente e idéntico en Adopción de ambas vistas (nivel + lectura) ──
def _closing_callout(soup: BeautifulSoup, scope_sel: str):
    sc = soup.select_one(scope_sel)
    if not sc:
        return None
    for c in sc.select(".callout"):
        t = c.select_one(".callout__title")
        if t and "Lectura de la adopción" in t.get_text():
            return (t.get_text(" ", strip=True), c.select_one(".callout__body").get_text(" ", strip=True))
    return None


def test_closing_adoption_callout_present_and_equal_in_both_views(world, monkeypatch):  # noqa: F811
    rep = _closing_callout(BeautifulSoup(_render(lambda _b: _variant("BLOCK"), monkeypatch, surface="report"), "html.parser"), "#asr-juicio")
    mir = _closing_callout(BeautifulSoup(_render(lambda _b: _variant("BLOCK"), monkeypatch, surface="flow"), "html.parser"), "#eval-adopcion")
    assert rep is not None, "el informe [02] no tiene el callout de cierre 'Lectura de la adopción'"
    assert mir is not None, "Mirada Danwor → Adopción no tiene el callout de cierre 'Lectura de la adopción'"
    assert rep == mir                                                          # mismo título (nivel) y misma lectura (headline), sin duplicar arriba


# ── 6. Veredicto general de compatibilidad BDC consistente entre Portabilidad de ambas vistas (o ausente en ambas) ──
def _compat_verdict(soup: BeautifulSoup, scope_sel: str):
    sc = soup.select_one(scope_sel)
    if not sc:
        return None
    vl = sc.select_one(".catalog-verdict-line .catalog-card__verdict")
    return vl.get_text(strip=True) if vl else None


def test_bdc_compatibility_verdict_is_consistent_between_views(world, monkeypatch):  # noqa: F811
    rep = _compat_verdict(BeautifulSoup(_render(lambda _b: _variant("BLOCK"), monkeypatch, surface="report"), "html.parser"), "#asr-compatibilidad")
    mir = _compat_verdict(BeautifulSoup(_render(lambda _b: _variant("BLOCK"), monkeypatch, surface="flow"), "html.parser"), "#eval-portabilidad")
    assert rep == mir                                                          # mismo veredicto general BDC en ambas superficies (o ambas sin compatibilidad)
