"""
test_client_adoption_synthesis.py — callout «Síntesis de adopción metodológica» del Informe de Cliente (entre las secciones 02 y 03).

Contrato: agregación DESCRIPTIVA y determinística de los veredictos del Juicio PERSISTIDOS y VIGENTES (moda, no promedio; NO DETERMINABLE jamás es un valor; los no vigentes y los sin veredicto se informan
aparte); empates sin desempate; sin recalcular ni regenerar nada; mismo snapshot para HTML / JSON / Markdown; tono del sistema visual existente; tabla individual y contratos previos intactos.
Regla verificada con filas sintéticas (función pura) y con el informe real sobre un portfolio sintético.
"""
from __future__ import annotations

import random
import re
import types

import pytest
from fastapi.testclient import TestClient

import webapp.main as main
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import auth_service, catalog_reads
from webapp.services import client_assessment_portfolio as pf

PACK = "datasphere_core"
HX = {"HX-Request": "true"}


# ── regla pura ──────────────────────────────────────────────────────────────
def row(verdict, state=svc.CURRENT):
    return {"state": state, "adoption": {"verdict": verdict}}


def rows(**spec):
    out = []
    for key, n in spec.items():
        v = {"BUENA": "BUENA", "ADECUADA": "ADECUADA", "PARCIAL": "PARCIAL", "ND": "NOT_DETERMINABLE"}.get(key)
        if key == "STALE":
            out += [row("PARCIAL", svc.STALE)] * n
        elif key == "NONE":
            out += [row(None, svc.NOT_YET_ASSESSED)] * n
        else:
            out += [row(v)] * n
    return out


def test_kensing_before_a1_five_buena_and_one_not_determinable_is_buena_predominance_with_the_pending_case():
    s = pf.adoption_synthesis(rows(BUENA=5, ND=1))
    assert (s["kind"], s["predominant"], s["counts"]["BUENA"], s["not_determinable"], s["current"]) == (pf.SYN_PREDOMINANT, "BUENA", 5, 1, 6)
    story = pf.adoption_story(s)
    assert story["headline"] == "Predomina la adopción Buena: 5 de 6 artefactos vigentes; 1 no determinable, pendiente de determinar."
    assert story["tone"] == presenter.status_tone("BUENA") and [c["label"] for c in story["chips"]] == ["Buena", "No determinable"]


def test_kensing_after_a1_six_of_six_buena_follows_from_the_data_without_code_changes():
    s = pf.adoption_synthesis(rows(BUENA=6))
    assert (s["kind"], s["predominant"]) == (pf.SYN_UNANIMOUS, "BUENA")
    assert pf.adoption_story(s)["headline"] == "Adopción Buena en 6 de 6 artefactos vigentes."


def test_ties_have_no_predominant_and_are_not_broken_by_quality_order():
    s = pf.adoption_synthesis(rows(BUENA=2, ADECUADA=2))
    assert (s["kind"], s["predominant"], s["tied"]) == (pf.SYN_TIE, None, ["BUENA", "ADECUADA"])
    story = pf.adoption_story(s)
    assert "empate entre Buena y Adecuada" in story["headline"] and story["tone"] == presenter.NEUTRAL
    assert pf.adoption_synthesis(rows(PARCIAL=1, ADECUADA=1, BUENA=1))["tied"] == ["BUENA", "ADECUADA", "PARCIAL"]


def test_the_predominant_is_the_mode_not_an_average():
    """2 BUENA + 1 ADECUADA + 1 PARCIAL: la moda es BUENA; un promedio ordinal sería «ADECUADA»-ish. Se informa la moda con su distribución."""
    s = pf.adoption_synthesis(rows(BUENA=2, ADECUADA=1, PARCIAL=1))
    story = pf.adoption_story(s)
    assert s["predominant"] == "BUENA" and story["headline"].startswith("Predomina la adopción Buena: 2 de 4")
    assert "no es un promedio" in story["criterion"]
    assert not re.search(r"\d+[.,]\d+", story["headline"] + story["markdown"])                      # ningún valor numérico sintético


def test_not_determinable_is_never_a_value_and_a_majority_of_them_declares_no_predominant():
    s = pf.adoption_synthesis(rows(BUENA=1, ND=2))
    assert (s["kind"], s["predominant"]) == (pf.SYN_MAJORITY_ND, None)
    assert pf.adoption_story(s)["tone"] == presenter.NEUTRAL and "no se declara un veredicto predominante" in pf.adoption_story(s)["headline"]
    one_one = pf.adoption_synthesis(rows(BUENA=1, ND=1))
    assert (one_one["kind"], one_one["predominant"]) == (pf.SYN_PREDOMINANT, "BUENA")                                  # 1-1: el ND no compite; se informa aparte como pendiente
    only = pf.adoption_synthesis(rows(ND=3))
    assert only["kind"] == pf.SYN_ONLY_ND and only["predominant"] is None and sum(only["counts"].values()) == 0


def test_stale_and_unassessed_artifacts_are_reported_apart_and_never_counted():
    s = pf.adoption_synthesis(rows(BUENA=3, STALE=2, NONE=1))
    assert (s["kind"], s["current"], s["stale"], s["no_verdict"], s["counts"]["PARCIAL"]) == (pf.SYN_UNANIMOUS, 3, 2, 1, 0)       # el PARCIAL desactualizado NO cuenta
    story = pf.adoption_story(s)
    assert "2 desactualizados" in story["outside"] and "1 sin veredicto vigente" in story["outside"]


def test_absence_of_data_cases():
    assert pf.adoption_synthesis([])["kind"] == pf.SYN_NO_ARTIFACTS and pf.adoption_story(pf.adoption_synthesis([])) is None
    none_current = pf.adoption_synthesis(rows(STALE=2, NONE=1))
    assert none_current["kind"] == pf.SYN_NONE_CURRENT and "No hay assessments vigentes" in pf.adoption_story(none_current)["headline"]
    unknown = pf.adoption_synthesis([row("SOMETHING_NEW")])
    assert unknown["kind"] == pf.SYN_NONE_CURRENT and unknown["no_verdict"] == 1                                                # categoría desconocida: jamás se inventa un valor


def test_the_synthesis_is_deterministic_and_order_independent():
    base = rows(BUENA=3, ADECUADA=2, PARCIAL=1, ND=1, STALE=1, NONE=1)
    ref = pf.adoption_synthesis(base)
    for seed in range(5):
        shuffled = list(base)
        random.Random(seed).shuffle(shuffled)
        assert pf.adoption_synthesis(shuffled) == ref


# ── informe real (portfolio sintético): HTML / JSON / Markdown del MISMO snapshot ──
def _entry(eid, am):
    return types.SimpleNamespace(entry_id=eid, cliente="Acme", dominio="Compras", veredicto="buena", artefactos=[am], path=f"/x/{eid}.md")


def _judgment(verdict):
    if verdict == "NOT_DETERMINABLE":
        return {"status": "NOT_DETERMINABLE", "level": None, "not_determinable_reason": "INSUFFICIENT_AFFIRMATIVE_EVIDENCE"}
    return {"status": "DETERMINED", "level": verdict}


def _profile():
    return {"assessment_state": "ASSESSED", "schema_version": "modeling_assessment.assessment/v1", "scope": {"root_type": "analytic-model"},
            "summary": {"coverage": {"strict": 0.9}, "confidence": {"level": "HIGH"}}, "observations": [], "review_required": []}


@pytest.fixture()
def portfolio(monkeypatch):
    def build(spec):
        entries, statuses = [], {}
        for i, (verdict, state) in enumerate(spec):
            e = _entry(f"e{i}", f"AM_{i}")
            entries.append(e)
            has = verdict is not None
            statuses[e.entry_id] = svc.AssessmentStatus(state, e.entry_id, "Acme", None if state == svc.NOT_APPLICABLE else e.artefactos[0], profile=_profile() if has else None,
                                                        generated_at="2026-10-04T10:00:00+00:00", judgment=_judgment(verdict) if has else None)
        monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (types.SimpleNamespace(find_flow_by_id=lambda es, x: next((y for y in es if y.entry_id == x), None)), entries))
        monkeypatch.setattr(svc, "statuses_for_entries", lambda batch: {x.entry_id: statuses[x.entry_id] for x in batch})
        return entries
    return build


def _client():
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "t@d.com", "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def _html(c):
    return c.get(f"/catalog/client/assessment?flow=e0&pack={PACK}", headers=HX).text


def test_the_callout_sits_between_artefactos_and_hallazgos_and_shows_counts_with_the_system_tone(portfolio):
    portfolio([("BUENA", svc.CURRENT)] * 5 + [("NOT_DETERMINABLE", svc.CURRENT)])
    html = _html(_client())
    a, b, c = html.index('id="cli-artefactos"'), html.index('id="cli-adopcion"'), html.index('id="cli-hallazgos"')
    assert a < b < c
    block = html[b:c]
    assert 'class="callout callout--punch"' in block and "Síntesis de adopción metodológica" in block                      # primitivo `callout("punch", …)` existente
    assert "Predomina la adopción Buena: 5 de 6 artefactos vigentes" in block and "Buena · 5" in block and "No determinable · 1" in block
    assert "catalog-card__verdict--plena" in block and "catalog-card__verdict--neutra" in block                         # verde (Buena) y gris (ND) del mapeo canónico
    assert 'data-adoption-kind="PREDOMINANT"' in html


def test_after_a1_the_same_report_code_shows_six_of_six_buena(portfolio):
    portfolio([("BUENA", svc.CURRENT)] * 6)
    block = _html(_client())
    block = block[block.index('id="cli-adopcion"'): block.index('id="cli-hallazgos"')]
    assert "Adopción Buena en 6 de 6 artefactos vigentes." in block and "No determinable" not in block


def test_the_individual_table_and_the_numbered_sections_are_untouched(portfolio):
    portfolio([("BUENA", svc.CURRENT)] * 5 + [("NOT_DETERMINABLE", svc.CURRENT)])
    html = _html(_client())
    arts = html[html.index('id="cli-artefactos"'): html.index('id="cli-adopcion"')]
    assert "<aside" not in arts and arts.count("<tr data-entry=") == 6
    assert "<th>Adopción metodológica</th>" in arts and arts.count('catalog-card__verdict--neutra">No determinable<') == 1
    assert re.findall(r'<span class="section-eyebrow">\[(\d\d)\]</span>', html) == ["01", "02", "03", "04"]               # el callout no se numera ni desplaza secciones


def test_json_and_markdown_come_from_the_same_snapshot(portfolio):
    portfolio([("BUENA", svc.CURRENT)] * 5 + [("NOT_DETERMINABLE", svc.CURRENT)])
    c = _client()
    snap = c.get("/catalog/client/assessment.json?flow=e0").json()
    assert snap["adoption_synthesis"]["kind"] == "PREDOMINANT" and snap["adoption_synthesis"]["counts"]["BUENA"] == 5 and snap["adoption_synthesis"]["not_determinable"] == 1
    assert [r["adoption"]["verdict"] for r in snap["assets"]].count("BUENA") == 5 and "summary" in snap and "adoption_synthesis" not in snap["summary"]
    md = c.get("/catalog/client/assessment.md?flow=e0").text
    assert "Síntesis de adopción metodológica" in md and "Predomina la adopción Buena: 5 de 6" in md
    assert md.index("Síntesis de adopción") < md.index("## Artefactos")


def test_building_the_synthesis_never_recomputes_or_regenerates_the_judgment(portfolio, monkeypatch):
    portfolio([("BUENA", svc.CURRENT)] * 2)
    import webapp.services.assessment_service as s2
    monkeypatch.setattr(s2, "evaluate_judgment", lambda *a, **k: pytest.fail("la síntesis NO recalcula el Juicio"))
    monkeypatch.setattr(s2, "generate", lambda *a, **k: pytest.fail("la síntesis NO regenera assessments"))
    snap = pf.build_snapshot(types.SimpleNamespace(cliente="Acme", entry_id="e0", dominio="Compras", artefactos=["AM_0"], path="/x"), PACK)
    assert snap["adoption_synthesis"]["counts"]["BUENA"] == 2


def test_a_stale_artifact_with_a_persisted_verdict_is_not_counted_in_the_report(portfolio):
    portfolio([("BUENA", svc.CURRENT)] * 2 + [("PARCIAL", svc.STALE)])
    block = _html(_client())
    block = block[block.index('id="cli-adopcion"'): block.index('id="cli-hallazgos"')]
    assert "Adopción Buena en 2 de 2 artefactos vigentes." in block and "1 desactualizado" in block and "Parcial ·" not in block
