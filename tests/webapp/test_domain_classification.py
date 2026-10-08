"""
test_domain_classification.py — Clasificación de dominio v1: SUGERENCIA determinista, hermana del Juicio (jamás insumo de calidad).

Fija: casos representativos (A–F) del motor puro SIN excepciones por cliente/raíz; persistencia como clave hermana del sobre (no dentro del
Juicio); invariante del Juicio; backfill idempotente sin reassess; presentación con primitivas del Hub y copy no punitivo.
Fixtures SINTÉTICAS (nombres de ejemplo; el motor no conoce ningún cliente).
"""
from __future__ import annotations

import dataclasses
import json
import re
import socket
from pathlib import Path

import pytest

import webapp.main as main
from tools.catalog.domain_classification import (ALIGNED, DOMAIN_CLASSIFICATION_VERSION, EXISTING_DOMAIN, HIGH, LOW, MEDIUM, NEW_DOMAIN_CANDIDATE, SUGGEST,
                                                 UNDETERMINED, classify_domain, is_generic_domain)
from tests.webapp.test_governed_assessment_durability import files, restart, world  # noqa: F401  (fixture reutilizada)
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import domain_classification_service as dcs

REPO = Path(__file__).resolve().parents[2]


def _closure(*names):
    return [{"name": n} for n in names]


# ── A. dominio genérico + vocabulario fuerte y coherente → sugerencia no genérica ──────────────────────────────────
def test_generic_domain_with_strong_coherent_vocabulary_suggests_a_new_domain_with_high_confidence():
    r = classify_domain(current_domain="General", root={"name": "4MA_PEDIDOS_ENTREGAS"}, client_domains=["General"], client="Cliente",
                        closure=_closure("1TR_VBAK", "3VF_PEDIDOS_ENTREGAS", "V_Pedidos", "V_Entregas", "V_Entregas_Final", "4MA_PEDIDOS_ENTREGAS", "SAP.TIME.M_TIME_DIMENSION"))
    assert r["version"] == DOMAIN_CLASSIFICATION_VERSION and r["classification_status"] == SUGGEST
    assert (r["suggested_domain"], r["suggestion_type"], r["confidence"]) == ("Pedidos / Entregas", NEW_DOMAIN_CANDIDATE, HIGH)
    assert is_generic_domain(r["current_domain"]) and not is_generic_domain(r["suggested_domain"])
    terms = {e["term"]: e["artifacts"] for e in r["evidence"]}
    assert set(terms) == {"Pedidos", "Entregas"} and "4MA_PEDIDOS_ENTREGAS" in terms["Pedidos"] and "V_Entregas_Final" in terms["Entregas"]   # sustento = artefactos reales


def test_the_engine_is_generic_not_tied_to_any_client_or_vocabulary():
    r = classify_domain(current_domain="Otros", root={"name": "AM_FACTURAS_COBRANZAS"}, client_domains=[], client="OtroCliente",
                        closure=_closure("V_Facturas", "VF_Cobranzas", "T_Facturas_Hist", "V_Cobranzas_Final", "AM_FACTURAS_COBRANZAS"))
    assert r["classification_status"] == SUGGEST and r["suggested_domain"] == "Facturas / Cobranzas" and r["confidence"] == HIGH


def test_an_existing_client_domain_is_preferred_over_a_new_candidate():
    r = classify_domain(current_domain="General", root={"name": "4MA_PEDIDOS_ENTREGAS"}, client_domains=["General", "Pedidos y Entregas", "Stock"],
                        closure=_closure("3VF_PEDIDOS_ENTREGAS", "V_Pedidos", "V_Entregas", "4MA_PEDIDOS_ENTREGAS"))
    assert (r["suggested_domain"], r["suggestion_type"], r["confidence"]) == ("Pedidos y Entregas", EXISTING_DOMAIN, HIGH)


# ── B. dominio específico respaldado → ALIGNED, sin alternativa innecesaria ────────────────────────────────────────
def test_specific_current_domain_supported_by_evidence_is_aligned_without_an_alternative():
    r = classify_domain(current_domain="Pedidos / Entregas", root={"name": "4MA_PEDIDOS_ENTREGAS"}, client_domains=["Pedidos / Entregas", "Stock"],
                        closure=_closure("3VF_PEDIDOS_ENTREGAS", "V_Pedidos", "V_Entregas", "4MA_PEDIDOS_ENTREGAS"))
    assert r["classification_status"] == ALIGNED and r["suggested_domain"] is None and r["suggestion_type"] is None and r["alternatives"] == []
    assert r["confidence"] == HIGH


# ── C. genérico + evidencia insuficiente → UNDETERMINED, sin candidato fabricado ───────────────────────────────────
def test_generic_domain_without_semantic_evidence_is_undetermined_and_fabricates_nothing():
    r = classify_domain(current_domain="General", root={"name": "4AM_FNC_GLACCOUNTLINEITRAWDATA"}, client_domains=["General"],
                        closure=_closure("1TR_SAP_FNC_GLACCOUNTLINEITEMRAWDATA", "2VR_FNC_GLACCOUNTLINEITEMRAWDATA", "4AM_FNC_GLACCOUNTLINEITRAWDATA"))
    assert r["classification_status"] == UNDETERMINED and r["suggested_domain"] is None and r["suggestion_type"] is None and r["confidence"] == LOW
    r2 = classify_domain(current_domain="", root={"name": "V_Pedidos"}, client_domains=[], closure=_closure("V_Pedidos"))        # una sola palabra, sin corroboración
    assert r2["classification_status"] == UNDETERMINED and r2["suggested_domain"] is None


# ── D. evidencia en conflicto ───────────────────────────────────────────────────────────────────────────────────────
def test_conflicting_existing_domains_are_undetermined_with_both_as_alternatives():
    r = classify_domain(current_domain="General", root={"name": "AM_VENTAS_STOCK"}, client_domains=["General", "Ventas", "Stock"],
                        closure=_closure("V_Ventas", "V_Stock", "T_Ventas", "T_Stock", "AM_VENTAS_STOCK"))
    assert r["classification_status"] == UNDETERMINED and r["suggested_domain"] is None and set(r["alternatives"]) == {"Ventas", "Stock"}


def test_specific_but_unsupported_domain_with_a_supported_existing_one_is_a_medium_suggestion_never_high():
    r = classify_domain(current_domain="Tesorería", root={"name": "AM_PEDIDOS"}, client_domains=["Tesorería", "Pedidos"],
                        closure=_closure("V_Pedidos", "T_Pedidos", "AM_PEDIDOS"))
    assert r["classification_status"] == SUGGEST and r["suggested_domain"] == "Pedidos" and r["suggestion_type"] == EXISTING_DOMAIN and r["confidence"] == MEDIUM
    r2 = classify_domain(current_domain="Tesorería", root={"name": "AM_ZZZ"}, client_domains=["Tesorería"], closure=_closure("AM_ZZZ"))
    assert r2["classification_status"] == UNDETERMINED and r2["suggested_domain"] is None


def test_the_result_is_deterministic_and_the_signature_tracks_the_inputs():
    kw = dict(current_domain="General", root={"name": "4MA_PEDIDOS_ENTREGAS"}, client_domains=["General"], client="C",
              closure=_closure("3VF_PEDIDOS_ENTREGAS", "V_Pedidos", "V_Entregas", "4MA_PEDIDOS_ENTREGAS"))
    a, b = classify_domain(**kw), classify_domain(**kw)
    assert a == b
    assert classify_domain(**{**kw, "current_domain": "Otros"})["input_signature"] != a["input_signature"]
    assert json.loads(json.dumps(a)) == a                                                       # serializable tal cual (sin porcentajes, sin objetos)
    assert not any(isinstance(v, float) for v in a.values())


# ── E. invariante: el Juicio NO cambia con la clasificación; sin red ni adquisición ─────────────────────────────────
def test_the_domain_classification_module_is_independent_of_the_judgment_and_the_assessment_engines():
    src = (REPO / "tools/catalog/domain_classification.py").read_text(encoding="utf-8")
    imports = re.findall(r"^(?:from|import)\s+([\w.]+)", src, re.M)
    assert not any(m.startswith(("tools.catalog.methodological_judgment", "tools.catalog.modeling_assessment", "webapp", "core", "requests", "urllib", "http", "socket")) for m in imports), imports
    code = src.split('"""', 2)[2]                                                                                       # sin el docstring del módulo
    assert "judgment" not in code.lower() and "juicio" not in code.lower()                                              # el código no menciona el Juicio (solo el docstring explica la relación)


def test_judgment_and_assessment_identity_are_identical_whatever_the_domain_classification(world, monkeypatch):
    st1 = svc.generate(world.acme)
    env1 = json.loads(files(world)[0].read_text(encoding="utf-8"))
    j1, sig1, prof1 = env1["judgment"], env1["assessment_artifact_signature"], env1["profile_json"]
    assert env1["domain_classification"]["version"] == DOMAIN_CLASSIFICATION_VERSION and "domain_classification" not in j1       # hermano, NO dentro del Juicio
    # el Catálogo cambia el dominio (cambia la clasificación) → el Juicio, el perfil y su firma son IDÉNTICOS
    renamed = dataclasses.replace(world.acme, dominio="Pedidos / Entregas")
    monkeypatch.setattr(dcs, "derive_for_entry", lambda entry, am, all_entries=None: {"version": DOMAIN_CLASSIFICATION_VERSION, "classification_status": ALIGNED,
                                                                                  "current_domain": "Pedidos / Entregas", "input_signature": "other", "evidence": [], "alternatives": []})
    restart()
    svc.generate(renamed, force=True)
    env2 = json.loads(files(world)[0].read_text(encoding="utf-8"))
    assert env2["domain_classification"]["classification_status"] == ALIGNED != env1["domain_classification"]["classification_status"]
    assert (env2["judgment"], env2["assessment_artifact_signature"], env2["profile_json"]) == (j1, sig1, prof1)
    assert st1.judgment == j1


def test_deriving_the_classification_never_runs_the_engine_or_touches_the_network(world, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no debe adquirir ni evaluar nada")
    monkeypatch.setattr(svc, "_engine_assess", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    dc = dcs.derive_for_entry(world.acme, "4AM_ACME")
    assert dc and dc["version"] == DOMAIN_CLASSIFICATION_VERSION and dc["classification_status"] in (ALIGNED, SUGGEST, UNDETERMINED)
    assert dcs.derive_for_entry(world.acme, None) is None and dcs.derive_for_entry(None, "X") is None                       # fail-soft sin raíz


# ── persistencia + backfill (sin reassess) ──────────────────────────────────────────────────────────────────────────
def test_backfill_materializes_the_classification_idempotently_without_touching_profile_or_judgment(world, monkeypatch):
    svc.generate(world.acme)
    path = files(world)[0]
    env = json.loads(path.read_text(encoding="utf-8"))
    original = {k: env[k] for k in ("profile_json", "judgment", "assessment_artifact_signature", "assessment_versions", "input_pool_signature", "generated_at")}
    env.pop("domain_classification")                                                              # sobre histórico (anterior al contrato)
    path.write_text(json.dumps(env), encoding="utf-8")
    restart()
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: (_ for _ in ()).throw(AssertionError("el backfill NO reevalúa")))
    dry = bf.backfill_domain_classification([world.acme], apply=False)
    assert dry["counts"]["would_update"] == 1 and "domain_classification" not in json.loads(path.read_text(encoding="utf-8"))   # dry-run: cero escrituras
    done = bf.backfill_domain_classification([world.acme], apply=True)
    assert done["counts"]["updated"] == 1 and done["counts"]["failed"] == 0
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["domain_classification"]["version"] == DOMAIN_CLASSIFICATION_VERSION
    assert {k: after[k] for k in original} == original                                           # identidad, perfil, Juicio y firmas intactos
    again = bf.backfill_domain_classification([world.acme], apply=True)
    assert again["counts"]["updated"] == 0 and again["counts"]["already_current"] == 1 and again["counts"]["complete"] is True    # idempotente
    stale = bf.backfill_domain_classification([dataclasses.replace(world.acme, dominio="Otro dominio")], apply=False)             # el Catálogo cambió → firma distinta
    assert stale["counts"]["would_update"] == 1


def test_backfill_has_a_dedicated_explicit_mode_that_requires_dry_run_or_apply(capsys):
    with pytest.raises(SystemExit):
        bf.main(["--backfill-domain-classification"])


# ── presentación: primitivas del Hub, copy no punitivo ─────────────────────────────────────────────────────────────
def _render(world, dc):
    st = svc.generate(world.acme)
    view = presenter.report_view(st, world.acme, "datasphere_core")
    view["domain_classification"] = presenter.domain_classification_view(dc)
    return main.templates.get_template("partials/catalog_assessment_report.html").render(v=view, pack="datasphere_core")


def _dc(status, **kw):
    base = {"version": DOMAIN_CLASSIFICATION_VERSION, "current_domain": "General", "current_domain_is_generic": True, "classification_status": status,
            "suggested_domain": None, "suggestion_type": None, "confidence": LOW, "evidence": [], "alternatives": [], "reason": "NO_COHERENT_EVIDENCE"}
    base.update(kw)
    return base


def test_suggestion_is_visible_non_destructive_and_uses_hub_primitives(world):
    dc = _dc(SUGGEST, suggested_domain="Pedidos / Entregas", suggestion_type=NEW_DOMAIN_CANDIDATE, confidence=HIGH, reason="GENERIC_DOMAIN_STRONG_COHERENT_VOCABULARY",
             evidence=[{"family": "ROOT+CLOSURE", "term": "Pedidos", "artifacts": ["4MA_PEDIDOS_ENTREGAS", "V_Pedidos"]}])
    html = _render(world, dc)
    sec = html[html.index('id="asr-dominio"'):]
    sec = sec[:sec.index("</details>")]
    assert 'id="asr-dominio" open' in html                                                                            # con sugerencia, abierta
    for text in ("Clasificación de dominio", "Dominio actual", "General", "Sugerencia", "Pedidos / Entregas", "Confianza", "Alta", "Evidencia", "4MA_PEDIDOS_ENTREGAS",
                 "La evidencia disponible sugiere una clasificación más específica.", "no mueve el modelo ni modifica el catálogo"):
        assert text in sec, text
    assert not re.search(r"incorrect|errónea|erronea|equivocad", sec, re.I)
    assert not re.search(r"catalog-card__verdict", sec)                                                               # sin insignia de grado: es metadata de gobierno
    assert re.search(r'<h2>Gobierno &amp; estándares</h2>', html)                                                     # vive en [04]


def test_aligned_is_compact_and_undetermined_says_there_is_not_enough_evidence(world):
    al = _render(world, _dc(ALIGNED, current_domain="Pedidos", confidence=HIGH, reason="CURRENT_DOMAIN_SUPPORTED"))
    assert 'id="asr-dominio">' in al and "El dominio actual es consistente con la evidencia disponible." in al and "Alineado con «Pedidos»" in al and "<h4>Evidencia</h4>" not in al
    un = _render(world, _dc(UNDETERMINED))
    assert 'id="asr-dominio">' in un and "No hay evidencia suficiente para recomendar otro dominio." in un and "Sin recomendación" in un
    cf = presenter.domain_classification_view(_dc(UNDETERMINED, reason="CONFLICTING_CANDIDATES", alternatives=["Ventas", "Stock"]))
    assert "más de un dominio existente" in cf["headline"] and cf["alternatives"] == ["Ventas", "Stock"] and not cf["suggested"]


def test_the_domain_classification_view_adds_no_css_and_the_report_resolves_persisted_or_fresh(world):
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8") + (REPO / "webapp/static/layout.css").read_text(encoding="utf-8")
    assert not re.search(r"asr-dominio|domain-classification|\.dc-", css)                                              # sin CSS propio
    st = svc.generate(world.acme)
    persisted = st.domain_classification
    assert persisted and dcs.resolve(persisted, world.acme, st.terminal_am) == (persisted, True)
    changed = dataclasses.replace(world.acme, dominio="Pedidos")
    fresh, is_persisted = dcs.resolve(persisted, changed, st.terminal_am)                                              # el Catálogo cambió → derivación en SOLO LECTURA
    assert is_persisted is False and fresh["input_signature"] != persisted["input_signature"]
    assert json.loads(files(world)[0].read_text(encoding="utf-8"))["domain_classification"] == persisted               # leer NO persiste
