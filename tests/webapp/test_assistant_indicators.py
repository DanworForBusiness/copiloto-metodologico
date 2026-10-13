"""Paso 2 — consultas analíticas del asistente sobre INDICADORES GOBERNADOS (registro cerrado) con ordenamiento de entradas por UN indicador explícito.

Principios que fijan estos tests: no existe un «scoring» universal; sin indicador explícito se aclara con las métricas reales (sin asumir ninguna); con indicador se resuelve directo con datos reales;
no se ordenan dominios, líneas de negocio ni clientes (no hay agregación gobernada); los empates, la cobertura incompleta y los no determinables se preservan y se explican; un nivel ordinal nunca se
convierte en número; el orden lo fija el indicador y no el veredicto legado; una consulta analítica reconocida jamás cae al LLM libre. Los valores salen EXCLUSIVAMENTE de los contratos existentes: acá se
comparan contra ellos (Juicio persistido, resumen del Assessment, portabilidad). Fixtures SINTÉTICAS: estados de Assessment reales (`svc.AssessmentStatus`) con perfiles mínimos válidos."""
from __future__ import annotations

import dataclasses
import html as _html
import re
import types

import pytest
from fastapi.testclient import TestClient

import webapp.main as main
from tools.catalog import catalog_service as cs
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import assistant_capabilities as caps
from webapp.services import assistant_indicators as ind
from webapp.services import assistant_navigation as nav
from webapp.services import assistant_routing, auth_service, catalog_reads
from webapp.services import client_assessment_portfolio as portfolio
from webapp.services import domain_classification_service as dcs
from webapp.services.content_service import load_module

PACK = "datasphere_core"
HOST = "04_Modelado_y_Desarrollo"
N = 40
CLIENTS = ["Pampa", "Loma", "Kensing", "Danone"]
GENERATED = "2026-10-09T10:00:00+00:00"
LEVEL_POS = {"PLENA": 0, "BUENA": 1, "ADECUADA": 2, "PARCIAL": 3}          # orden ordinal de la fuente (best → worst) escrito acá, independiente del código bajo prueba
CONF_POS = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _spec(i: int) -> dict:
    """Valores sintéticos de la entrada i (todo derivado del índice: reproducible)."""
    kind = i % 10
    level = {0: "BUENA", 1: "BUENA", 2: "BUENA", 3: "ADECUADA", 4: "ADECUADA", 5: "PARCIAL", 6: "PLENA"}.get(kind)
    return {
        "client": CLIENTS[i % 4], "lob": {0: "SALES", 1: "FINANCE", 2: None}[i % 3],
        "state": {8: svc.STALE, 9: svc.NOT_YET_ASSESSED}.get(kind, svc.CURRENT),
        "judgment": ("ND" if kind == 7 else level) if kind not in (9,) else None,
        "strict": round(0.90 + 0.01 * (i % 5), 2), "confidence": ("HIGH", "MEDIUM", "LOW")[i % 3], "review": (i * 7) % 13,
        # veredicto LEGADO invertido respecto de la adopción para probar que NO interviene en el orden
        "veredicto": ("parcial", "buena", "plena")[LEVEL_POS.get(level, 3) % 3] if level else "buena",
        "score": None if i % 13 == 0 else 100 - (i % 6) * 10, "version": None if i % 11 == 0 else "portability-scoring/v2",
    }


def _status(entry, i: int) -> svc.AssessmentStatus:
    s = _spec(i)
    if s["state"] == svc.NOT_YET_ASSESSED:
        return svc.AssessmentStatus(state=svc.NOT_YET_ASSESSED, entry_id=entry.entry_id, client=entry.cliente, terminal_am=None)
    profile = {"assessment_state": "ASSESSED", "summary": {"coverage": {"strict": s["strict"]}, "confidence": {"level": s["confidence"]}},
               "review_required": [{"occurrence_count": s["review"]}], "observations": [{"occurrence_count": 1}]}
    judgment = ({"status": "NOT_DETERMINABLE", "level": None, "ruleset_version": "judgment-rules/v2", "strengths": [], "reasons": []} if s["judgment"] == "ND"
                else {"status": "DETERMINED", "level": s["judgment"], "ruleset_version": "judgment-rules/v2", "strengths": [], "reasons": []})
    return svc.AssessmentStatus(state=s["state"], entry_id=entry.entry_id, client=entry.cliente, terminal_am=f"4AM_X_{i:03d}", generated_at=GENERATED, profile=profile, judgment=judgment)


def _write(root, i: int) -> None:
    s = _spec(i)
    d = root / s["client"] / f"D{i:03d}"
    d.mkdir(parents=True)
    lob = f"  linea_de_negocio: {s['lob']}\n" if s["lob"] else ""
    (d / f"flujo_D{i:03d}.md").write_text(
        f"---\nauto:\n  tipo: flujo\n  dominio: Dominio {i:03d}\n  artefactos:\n  - 4AM_X_{i:03d}\nmanual:\n  cliente: {s['client']}\n  veredicto: {s['veredicto']}\n{lob}---\nCuerpo.\n", encoding="utf-8")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    for i in range(N):
        _write(tmp_path, i)
    entries = cs.load_catalog(str(tmp_path))
    index = {}
    for e in entries:
        i = int(re.search(r"-d(\d{3})-", e.entry_id).group(1))
        index[e.entry_id] = i
        s = _spec(i)
        e.portabilidad_bdc = ({"score": s["score"], "version": s["version"], "clasificacion": "plena"} if s["score"] is not None else {"clasificacion": "no_evaluable"})
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(catalog_reads, "_catalog_data_products_entries", lambda: (cs, [types.SimpleNamespace(product_id="DP", title="X")]))

    def read(flows):
        statuses = {e.entry_id: _status(e, index[e.entry_id]) for e in flows}
        return caps.Governed(statuses, dcs.effective_lob_by_entry(flows, statuses))
    monkeypatch.setattr(caps, "governed_read", read)
    return types.SimpleNamespace(entries=entries, index=index, by_id={e.entry_id: e for e in entries})


def _route(question, stem=HOST):
    return assistant_routing.AssistantRoutingService().route(load_module(stem, PACK), question, PACK)


def _client():
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "t@d.com", "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def _chat_raw(question, stem=HOST) -> str:
    from webapp.routers.assistant import assistant_store
    conv = assistant_store.start(stem, PACK).id
    return _client().post(f"/modules/{stem}/assistant/chat", data={"question": question, "conversation_id": conv, "pack": PACK}).text


def _refs(route) -> list[str]:
    return [it.ref for it in route.result.items]


def _expected(world, key, order, *, predicate=lambda s: True, value=lambda s: s):
    """Orden esperado, escrito acá desde las reglas documentadas: valor del indicador, desempate estable (cliente, nombre, entry_id). `key(spec)` → clave comparable o None (excluida)."""
    rows = []
    for e in world.entries:
        s = _spec(world.index[e.entry_id])
        k = key(s)
        if k is not None and predicate(s):
            rows.append((k, e.cliente.lower(), e.dominio.lower(), e.entry_id))
    rows.sort(key=lambda r: ((r[0] if order == "best_first" else tuple(-x for x in r[0])), r[1], r[2], r[3]))
    return [r[3] for r in rows]


# ── 1. registro cerrado ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_registry_is_closed_and_has_exactly_the_five_governed_indicators():
    assert [i.id for i in ind.INDICATORS] == ["adoption", "coverage", "confidence", "attention", "types"]
    assert [i.label for i in ind.INDICATORS] == ["Adopción metodológica", "Cobertura estricta", "Confianza de la evidencia", "Casos a revisar", "Consistencia de tipos"]
    for i in ind.INDICATORS:
        assert i.source and i.caveat and i.aliases and i.kind in ("ordinal", "ratio", "count", "score") and callable(i.measure)
    assert not any("veredicto" in a for i in ind.INDICATORS for a in i.aliases) and "veredicto" not in " ".join(i.source.lower() for i in ind.INDICATORS)     # el veredicto legado no es un indicador


def test_ordinal_levels_come_from_the_authoritative_sources():
    assert ind.BY_ID["adoption"].levels is portfolio.ADOPTION_LEVELS or ind.BY_ID["adoption"].levels == portfolio.ADOPTION_LEVELS
    assert ind.BY_ID["confidence"].levels == ("HIGH", "MEDIUM", "LOW")
    assert all(presenter.confidence_label(level) for level in ind.BY_ID["confidence"].levels)          # los tres niveles existen en la presentación del Assessment


def test_the_attention_indicator_declares_volume_and_the_methodological_caveat():
    a = ind.BY_ID["attention"]
    assert a.volume and "más casos no equivale necesariamente a peor calidad" in a.caveat
    assert a.label == "Casos a revisar" and "puntos de atencion" in a.aliases and "casos a revisar" in a.aliases       # nombre visible vs. alias de reconocimiento
    assert "no las razones ni las fortalezas del Juicio metodológico" in a.caveat and "review_occurrences" in a.source
    assert not any(i.volume for i in ind.INDICATORS if i.id != "attention")
    assert "independiente de la compatibilidad con un destino BDC" in ind.BY_ID["types"].caveat


# ── 2. equivalencia exacta con el contrato autoritativo ─────────────────────────────────────────────────────────────────────────────────────────
def test_every_indicator_value_equals_its_authoritative_contract(world):
    g = caps.governed_read(world.entries)
    for e in world.entries:
        i, st, s = world.index[e.entry_id], g.statuses[e.entry_id], _spec(world.index[e.entry_id])
        if st.state == svc.CURRENT:
            jv = presenter.judgment_view(st.judgment)
            m = ind.BY_ID["adoption"].measure(e, g, PACK)
            if jv["determined"]:
                assert (m.state, m.value, m.display) == (ind.OK, jv["level"], jv["level_label"])
            else:
                assert m.state == ind.NOT_DETERMINABLE and m.value is None
            summ = svc.headline_summary(st.profile)
            cov, conf, att = (ind.BY_ID[k].measure(e, g, PACK) for k in ("coverage", "confidence", "attention"))
            assert (cov.value, cov.display) == (summ["strict"], presenter.percent(summ["strict"]))
            assert (conf.value, conf.display) == (summ["confidence"], presenter.confidence_label(summ["confidence"]))
            assert att.value == summ["review_occurrences"] == s["review"]
        t = ind.BY_ID["types"].measure(e, g, PACK)
        port = e.portabilidad_bdc
        if isinstance(port.get("score"), int) and port.get("version"):
            assert (t.state, t.value, t.display) == (ind.OK, port["score"], f"{port['score']}/100")


def test_evidence_names_the_source_the_rules_the_date_and_a_report_rebuilt_from_presenters(world):
    g = caps.governed_read(world.entries)
    e = next(x for x in world.entries if world.index[x.entry_id] % 10 == 0)
    m = ind.BY_ID["adoption"].measure(e, g, PACK)
    assert m.evidence == caps.Evidence("Juicio metodológico", "09/10/2026", "judgment-rules/v2", presenter.assessment_url(e, PACK))
    c = ind.BY_ID["coverage"].measure(e, g, PACK)
    assert (c.evidence.source, c.evidence.generated_at, c.evidence.url) == ("Assessment · cobertura estricta", "09/10/2026", presenter.assessment_url(e, PACK))
    t = ind.BY_ID["types"].measure(next(x for x in world.entries if world.index[x.entry_id] % 11 and world.index[x.entry_id] % 13), g, PACK)
    assert t.evidence.ruleset == "portability-scoring/v2" and t.evidence.generated_at is None


@pytest.mark.parametrize("kind,expect", [(7, ind.NOT_DETERMINABLE), (8, ind.STALE), (9, ind.NOT_ASSESSED)])
def test_assessment_indicators_exclude_what_is_not_a_current_comparable_value(world, kind, expect):
    g = caps.governed_read(world.entries)
    e = next(x for x in world.entries if world.index[x.entry_id] % 10 == kind)
    assert ind.BY_ID["adoption"].measure(e, g, PACK).state == expect
    if kind != 7:                                                                                       # un Juicio no determinable sí tiene cobertura/confianza vigentes
        for k in ("coverage", "confidence", "attention"):
            assert ind.BY_ID[k].measure(e, g, PACK).state == expect


def test_types_excludes_legacy_rules_and_missing_scores_instead_of_comparing_them(world):
    g = caps.governed_read(world.entries)
    legacy = next(x for x in world.entries if world.index[x.entry_id] % 11 == 0 and world.index[x.entry_id] % 13)
    missing = next(x for x in world.entries if world.index[x.entry_id] % 13 == 0)
    assert ind.BY_ID["types"].measure(legacy, g, PACK).state == ind.LEGACY_RULES
    assert ind.BY_ID["types"].measure(missing, g, PACK).state == ind.NO_VALUE


# ── 3. ordenamiento, empates, cobertura ────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_adoption_orders_by_the_ordinal_position_best_to_worst_and_back_without_numbers(world):
    best = _route("¿Qué entradas tienen mejor adopción metodológica?")
    rs = best.result
    key = lambda s: (LEVEL_POS[s["judgment"]],) if s["state"] == svc.CURRENT and s["judgment"] not in (None, "ND") else None
    exp = _expected(world, key, "best_first")
    assert _refs(best) == exp[:10] and rs.ranking.evaluated == len(exp) and rs.ranking.order == "best_first"
    worst = _route("¿Qué entradas tienen peor adopción metodológica?")
    assert _refs(worst) == _expected(world, key, "worst_first")[:10] and worst.result.ranking.order_text == "de peor a mejor"
    for it in rs.items + worst.result.items:                                                            # niveles ordinales: etiqueta y valor crudo, jamás un número
        (metric,) = it.catalog.metrics
        assert metric.value in portfolio.ADOPTION_LEVELS and not re.search(r"\d", it.badge)


def test_the_legacy_verdict_never_decides_the_order(world):
    by_legacy = sorted(world.entries, key=lambda e: {"plena": 0, "buena": 1, "parcial": 2}.get(e.veredicto, 9))
    ranked = _route("¿Qué entradas tienen mejor adopción metodológica?")
    assert _refs(ranked) != [e.entry_id for e in by_legacy][:10]
    assert all(it.catalog.metrics[0].indicator == "adoption" for it in ranked.result.items)


@pytest.mark.parametrize("phrase,indicator,key,order", [
    ("mejor cobertura estricta", "coverage", lambda s: (-s["strict"],) if s["state"] == svc.CURRENT else None, "best_first"),
    ("peor cobertura estricta", "coverage", lambda s: (-s["strict"],) if s["state"] == svc.CURRENT else None, "worst_first"),
    ("mayor confianza de la evidencia", "confidence", lambda s: (CONF_POS[s["confidence"]],) if s["state"] == svc.CURRENT else None, "best_first"),
    ("peor confianza de la evidencia", "confidence", lambda s: (CONF_POS[s["confidence"]],) if s["state"] == svc.CURRENT else None, "worst_first"),
    ("más casos a revisar", "attention", lambda s: (-s["review"],) if s["state"] == svc.CURRENT else None, "best_first"),
    ("menos casos a revisar", "attention", lambda s: (-s["review"],) if s["state"] == svc.CURRENT else None, "worst_first"),
    ("más puntos de atención", "attention", lambda s: (-s["review"],) if s["state"] == svc.CURRENT else None, "best_first"),            # alias de reconocimiento
    ("menos puntos de atención", "attention", lambda s: (-s["review"],) if s["state"] == svc.CURRENT else None, "worst_first"),
    ("mejor consistencia de tipos", "types", lambda s: (-s["score"],) if s["score"] is not None and s["version"] else None, "best_first"),
    ("peor consistencia de tipos", "types", lambda s: (-s["score"],) if s["score"] is not None and s["version"] else None, "worst_first"),
])
def test_each_indicator_orders_by_its_own_value_with_the_documented_tie_break(world, phrase, indicator, key, order):
    r = _route(f"¿Qué entradas tienen {phrase}?")
    assert r.result.capability == "rank" and r.result.ranking.indicator == indicator and r.result.ranking.order == order
    assert _refs(r) == _expected(world, key, order)[:10]


def test_ties_are_grouped_announced_and_ordered_stably(world):
    r = _route("¿Qué entradas tienen mejor cobertura estricta?")
    info = r.result.ranking
    assert info.ties and all(n > 1 for _d, n in info.ties)
    top = presenter.percent(1.0 if False else max(_spec(i)["strict"] for i in range(N) if _spec(i)["state"] == svc.CURRENT))
    n_top = sum(1 for i in range(N) if _spec(i)["state"] == svc.CURRENT and presenter.percent(_spec(i)["strict"]) == top)
    assert (top, n_top) in info.ties and f"{n_top} entradas con «{top}»" in r.result.footer and "no implica un ranking" in r.result.footer
    inside = [it for it in r.result.items if it.badge == top]
    assert [it.ref for it in inside] == sorted([it.ref for it in inside], key=lambda ref: (world.by_id[ref].cliente.lower(), world.by_id[ref].dominio.lower(), ref))


def test_a_cut_inside_a_tie_is_declared(world):
    info = _route("¿Qué entradas tienen mejor cobertura estricta?").result.ranking
    assert info.cut_in_tie is not None and info.cut_in_tie[1] > 0
    assert "El corte cae dentro de un empate" in _route("¿Qué entradas tienen mejor cobertura estricta?").result.footer


def test_incomplete_coverage_is_counted_and_explained_never_ranked(world):
    r = _route("¿Qué entradas tienen mejor adopción metodológica?")
    info = r.result.ranking
    cur = [i for i in range(N) if _spec(i)["state"] == svc.CURRENT]
    nd = sum(1 for i in cur if _spec(i)["judgment"] == "ND")
    stale = sum(1 for i in range(N) if _spec(i)["state"] == svc.STALE)
    none = sum(1 for i in range(N) if _spec(i)["state"] == svc.NOT_YET_ASSESSED)
    assert info.universe == N and info.evaluated == len(cur) - nd and dict(info.excluded) == {ind.NOT_DETERMINABLE: nd, ind.STALE: stale, ind.NOT_ASSESSED: none}
    assert f"{info.evaluated} de {N} tienen un valor comparable" in r.message
    assert f"{nd} no determinables" in r.result.footer and f"{stale} desactualizadas" in r.result.footer
    ranked = {it.ref for it in r.result.items}
    assert not any(_spec(world.index[ref])["judgment"] in (None, "ND") or _spec(world.index[ref])["state"] != svc.CURRENT for ref in ranked)


def test_when_nothing_is_comparable_it_says_so_with_the_reasons_and_shows_no_rows(world, monkeypatch):
    monkeypatch.setitem(ind.BY_ID, "types", dataclasses.replace(ind.BY_ID["types"], measure=lambda e, g, p: ind.Measurement(ind.NO_VALUE)))
    r = _route("¿Qué entradas tienen mejor consistencia de tipos?")
    assert r.result.items == () and "Ninguna de las 40 entradas navegables" in r.message and "sin valor en el contrato" in r.message


def test_at_most_ten_rows_and_the_caveat_always_travels_with_the_result(world):
    for q, caveat in (("¿Qué entradas tienen mejor consistencia de tipos?", "No es un puntaje global"), ("¿Qué entradas tienen más puntos de atención?", "no equivale necesariamente a peor calidad"),
                      ("¿Qué entradas tienen mejor adopción metodológica?", "no se promedia ni se convierte en número")):
        r = _route(q)
        assert len(r.result.items) <= 10 and caveat in r.result.footer


def test_each_row_shows_value_evidence_and_opens_the_real_entry(world):
    r = _route("¿Qué entradas tienen mejor adopción metodológica?")
    for it in r.result.items:
        e = world.by_id[it.ref]
        assert (it.kind, it.mode, it.url) == (caps.CATALOG_ENTRY, caps.MODE_EMBED, presenter.flow_url(e, PACK)) and it.key == (caps.CATALOG_ENTRY, e.entry_id)
        assert it.badge and "Juicio metodológico" in it.note and "judgment-rules/v2" in it.note and "09/10/2026" in it.note
        assert it.catalog.metrics[0].evidence.url == presenter.assessment_url(e, PACK)
        assert it.subtitle == f"{it.catalog.client} · {it.catalog.lob_label}"


# ── 4. filtros por cliente y línea de negocio efectiva ──────────────────────────────────────────────────────────────────────────────────────────
def test_filters_by_client_and_effective_line_of_business_apply_before_ordering(world):
    r = _route("¿Qué entradas de Pampa tienen mejor consistencia de tipos?")
    key = lambda s: (-s["score"],) if s["score"] is not None and s["version"] else None
    assert _refs(r) == _expected(world, key, "best_first", predicate=lambda s: s["client"] == "Pampa")[:10] and r.result.ranking.universe == sum(1 for i in range(N) if _spec(i)["client"] == "Pampa")
    assert [a[0] for a in (r.actions and [(x.label,) for x in r.actions])] == ["Abrir Catálogo", "Informe de Cliente · Pampa"]
    r = _route("¿Qué entradas en Ventas tienen peor cobertura estricta?")
    key = lambda s: (-s["strict"],) if s["state"] == svc.CURRENT else None
    assert _refs(r) == _expected(world, key, "worst_first", predicate=lambda s: s["lob"] == "SALES")[:10]
    assert all(it.catalog.lob_label == "Ventas" for it in r.result.items) and "línea de negocio **Ventas**" in r.message
    both = _route("¿Qué entradas de Loma en Finanzas tienen mejor consistencia de tipos?")
    assert both.result.ranking.universe == sum(1 for i in range(N) if _spec(i)["client"] == "Loma" and _spec(i)["lob"] == "FINANCE")
    assert all(it.catalog.client == "Loma" and it.catalog.lob_label == "Finanzas" for it in both.result.items)


def test_unclassified_entries_are_never_ranked_under_a_line_and_can_be_asked_as_such(world):
    r = _route("¿Qué entradas sin clasificar tienen mejor consistencia de tipos?")
    assert r.result.ranking.universe == sum(1 for i in range(N) if _spec(i)["lob"] is None) and all(it.catalog.lob_label == "Sin clasificar" for it in r.result.items)
    sales = _route("¿Qué entradas en Ventas tienen mejor consistencia de tipos?")
    assert not any(_spec(world.index[it.ref])["lob"] is None for it in sales.result.items)


def test_two_filters_of_the_same_kind_are_not_chosen_for_the_user(world):
    r = _route("¿Qué entradas de Pampa y Loma tienen mejor cobertura estricta?")
    assert r.result is None and "una consulta por vez" in r.message


# ── 5. aclaración sin indicador y rechazo de agrupaciones ──────────────────────────────────────────────────────────────────────────────────────
def _ask_questions(route) -> dict[str, str]:
    return {a.label: a.ask for a in route.actions if a.ask}


def test_scoring_without_a_metric_asks_which_one_and_assumes_none(world):
    r = _route("¿Cuál es el dominio con mejor scoring?")
    assert r.result is None and "agregación gobernada" in r.message and "entradas individuales" in r.message and not r.result
    asks = _ask_questions(r)
    assert {"Adopción metodológica", "Cobertura estricta", "Confianza de la evidencia", "Consistencia de tipos"} <= set(asks) and "Ver la distribución por línea de negocio" in asks
    assert "Casos a revisar" not in " ".join(asks)                                                       # «mejor» no aplica a un indicador de volumen
    assert all(not a.url and not a.embed and not a.external for a in r.actions)


@pytest.mark.parametrize("question", ["¿Cuál es el dominio con mejor scoring?", "¿Qué línea de negocio tiene el mejor puntaje?", "¿Qué cliente tiene mejor cobertura estricta?", "¿Qué dominios tienen mejor adopción metodológica?",
                                       "ranking de líneas de negocio por confianza"])
def test_groups_are_never_ranked_without_a_governed_aggregation_rule(world, question):
    r = _route(question)
    assert r is not None and r.result is None and "No hay una regla de agregación gobernada" in r.message and "entradas individuales" in r.message
    assert not re.search(r"\b(?:Biogenesis|Pampa|Loma|Kensing|Danone)\b.*\b(?:tiene|es) el (?:mejor|peor)\b", r.message)            # nadie es coronado


def test_a_metric_without_a_subject_filter_still_requires_choosing_it(world):
    r = _route("¿Qué modelos de Ventas con mayores riesgos?")
    assert r.result is None and "No existe un «scoring» único" in r.message and "Ventas" in r.message
    assert len(_ask_questions(r)) == 5 and "Casos a revisar (más casos primero)" in _ask_questions(r)       # «mayores» sí admite el indicador de volumen


def test_more_than_one_metric_is_not_combined(world):
    r = _route("¿Qué entradas tienen mejor cobertura estricta y confianza de la evidencia?")
    assert r.result is None and "no las combino en un puntaje" in r.message and set(_ask_questions(r)) == {"Cobertura estricta", "Confianza de la evidencia"}


def test_better_or_worse_does_not_apply_to_the_volume_indicator(world):
    r = _route("¿Qué entradas tienen mejor puntos de atención?")
    assert r.result is None and "mide cantidad, no calidad" in r.message and set(_ask_questions(r)) == {"Más casos a revisar", "Menos casos a revisar"}


@pytest.mark.parametrize("indicator", [i.id for i in ind.INDICATORS])
@pytest.mark.parametrize("order", ["best_first", "worst_first"])
@pytest.mark.parametrize("filters", [dict(), dict(client="Pampa"), dict(lob_label="Ventas"), dict(client="Loma", lob_label="Finanzas"), dict(unclassified=True)])
def test_canonical_questions_round_trip_through_the_router(world, indicator, order, filters):
    q = ind.canonical_question(indicator, order, **filters)
    r = _route(q)
    assert r is not None and r.result is not None and r.result.ranking.indicator == indicator and r.result.ranking.order == order, q
    if filters.get("client"):
        assert all(it.catalog.client == filters["client"] for it in r.result.items)


def test_every_clarification_option_resolves_directly_with_real_data(world):
    for r in (_route("¿Cuál es el dominio con mejor scoring?"), _route("¿Qué modelos de Ventas con mayores riesgos?")):
        for label, q in _ask_questions(r).items():
            if label.startswith("Ver la distribución"):
                assert _route(q).result.capability == "summary"
                continue
            out = _route(q)
            assert out.result.capability == "rank" and out.result.ranking.label in label


# ── 6. aislamiento del LLM y chat real ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_recognized_analytic_questions_never_reach_the_llm(world, monkeypatch):
    from webapp.routers.assistant import assistant_chat_service

    def boom(**_kw):
        raise AssertionError("una consulta analítica reconocida no debe invocar al LLM")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(assistant_chat_service, "client_factory", boom)
    monkeypatch.setattr(assistant_chat_service, "execution_mode", "produccion")
    for q in ("¿Cuál es el dominio con mejor scoring?", "¿Qué modelo tiene mayor confianza?", "¿Qué modelos de Ventas con mayores riesgos?", "¿Qué entradas tienen peor adopción metodológica?",
              "¿Qué entradas de Pampa tienen más puntos de atención?", "¿Qué cliente tiene mejor cobertura estricta?", "¿Qué entradas tienen mejor puntos de atención?"):
        page = _chat_raw(q)
        assert "Respuesta generada con IA" not in page, q
        assert ("assistant-route-ask" in page) or ("assistant-result-row" in page), q                   # respuesta gobernada o aclaración explícita


def test_the_clarification_buttons_post_a_server_fixed_question_to_the_same_chat(world):
    raw = _chat_raw("¿Cuál es el dominio con mejor scoring?")
    forms = re.findall(r'<form class="assistant-route-ask"[^>]*hx-post="([^"]+)"[^>]*>(.*?)</form>', raw, re.S)
    assert len(forms) == 5
    for hx_post, body in forms:
        assert hx_post == f"/modules/{HOST}/assistant/chat" and 'name="conversation_id"' in body and 'name="pack"' in body
        q = _html.unescape(re.search(r'name="question" value="([^"]*)"', body).group(1))
        assert q in {a.ask for a in _route("¿Cuál es el dominio con mejor scoring?").actions}        # la pregunta sale del registro, no del usuario
    answered = _chat_raw(_html.unescape(re.search(r'name="question" value="([^"]*)"', forms[0][1]).group(1)))
    assert "assistant-result-row" in answered and "Adopción metodológica" in answered


def test_the_chat_renders_rows_with_value_and_evidence_and_the_methodological_footer(world):
    page = _chat_raw("¿Qué entradas tienen mejor adopción metodológica?")
    assert page.count('class="assistant-result-row"') == 10 and 'class="assistant-result-metric"' in page and "judgment-rules/v2" in page and "assistant-results-note" in page
    assert "no se promedia ni se convierte en número" in page and "<table" not in page


def test_response_size_stays_bounded_with_many_entries(world):
    for q in ("¿Qué entradas tienen mejor cobertura estricta?", "¿Qué entradas tienen más puntos de atención?", "¿Qué entradas tienen mejor adopción metodológica?"):
        assert len(_chat_raw(q)) < 16_000


# ── 7. regresiones ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("question", ["¿Cómo se calcula la adopción metodológica?", "¿Qué es la confianza de la evidencia?", "¿Cómo diseñar mejores prácticas para modelos de Ventas?",
                                       "buenas prácticas para la consistencia de tipos", "¿Qué es el juicio metodológico?"])
def test_methodological_questions_about_the_indicators_are_not_analytic_queries(world, question):
    assert nav._catalog_ranking(question, PACK, HOST) is None


@pytest.mark.parametrize("question,expect", [("¿Qué dominios hay disponibles?", "El Catálogo contiene"), ("Mostrame 10 modelos", "Hay **40 entradas navegables**"), ("Modelos de Ventas", "en la línea de negocio **Ventas**"),
                                              ("¿Qué catálogos de Ventas tenemos?", "en la línea de negocio **Ventas**"), ("¿Qué tiene Pampa?", "de **Pampa**"), ("¿Qué modelos sin clasificar hay?", "**sin clasificar**")])
def test_inventory_queries_still_resolve_as_inventory(world, question, expect):
    r = _route(question)
    assert expect in r.message and (r.result.ranking is None) and r.result.capability in ("summary", "list")


def test_curated_resources_client_report_catalog_navigation_and_sections_are_unchanged(world):
    assert _route("Ver informe de cliente para Pampa").actions[0].label == "Ver informe"
    assert _route("Abrí el Catálogo").message.startswith("Acá tenés el **Catálogo de datos**")
    res = _route("¿Qué documentación tenemos de CDS?")
    assert res.result is not None and all(it.kind == caps.CURATED_RESOURCE for it in res.result.items)
    assert _route("¿Cómo se definen los dominios de negocio?").actions[0].label == "Ir a la sección"


def test_an_ordering_cue_with_no_catalog_subject_is_not_this_capability(world):
    assert nav._catalog_ranking("¿Cuál es el mejor?", PACK, HOST) is None and nav._catalog_ranking("mayor información sobre CDS", PACK, HOST) is None


def test_registry_order_places_the_analytic_target_before_the_inventory_and_after_the_open_catalog():
    assert [t.key for t in nav.TARGETS] == ["catalog_followup", "client_report", "data_catalog", "catalog_query", "catalog_ranking", "catalog_inventory"]


def test_the_indicator_names_never_trigger_the_methodological_guard_by_themselves():
    assert ind.without_indicator_names("que entradas tienen mejor adopcion metodologica") == "que entradas tienen mejor"
    assert not nav.is_methodological(ind.without_indicator_names("que modelos tienen mejor juicio metodologico"))
    assert nav.is_methodological(ind.without_indicator_names("como se calcula la adopcion metodologica"))


# ── 8. «Casos a revisar» no se mezcla con las razones del Juicio ni con las observaciones ──────────────────────────────────────────────────────
def test_cases_to_review_come_only_from_review_occurrences_never_from_the_judgment_reasons(world, monkeypatch):
    """El indicador cuenta `review_occurrences` del Assessment. Aunque el Juicio traiga OTRAS razones/fortalezas (otro conteo), el valor no cambia."""
    g = caps.governed_read(world.entries)
    e = next(x for x in world.entries if world.index[x.entry_id] % 10 == 0)
    base = ind.BY_ID["attention"].measure(e, g, PACK)
    st = g.statuses[e.entry_id]
    st.judgment["reasons"] = [{"dimension": "d", "statement_key": f"k{n}", "classification": "x"} for n in range(7)]
    st.judgment["strengths"] = [{"dimension": "d", "statement_key": f"s{n}"} for n in range(5)]
    assert presenter.judgment_view(st.judgment)["reasons_count"] != base.value
    after = ind.BY_ID["attention"].measure(e, g, PACK)
    assert (after.state, after.value) == (ind.OK, svc.headline_summary(st.profile)["review_occurrences"]) == (base.state, base.value)
    st.profile["observations"] = [{"occurrence_count": 99}]                                            # las observaciones tampoco entran
    assert ind.BY_ID["attention"].measure(e, g, PACK).value == base.value


def test_the_visible_name_is_cases_to_review_everywhere_and_points_of_attention_only_recognizes(world):
    r = _route("¿Qué entradas tienen más puntos de atención?")                                         # alias de reconocimiento
    assert r.result.ranking.label == "Casos a revisar" and "**Casos a revisar**" in r.message and "Puntos de atención" not in r.message + r.result.footer
    assert all("caso" in it.badge and "a revisar" in it.badge for it in r.result.items) and all(it.catalog.metrics[0].evidence.source == "Assessment · casos a revisar" for it in r.result.items)
    assert "no las razones ni las fortalezas del Juicio metodológico" in r.result.footer
    page = _chat_raw("¿Qué entradas tienen mejor puntos de atención?")                                 # la aclaración de dirección también usa el nombre visible
    assert "Casos a revisar" in page and "Puntos de atención" not in page
    other = nav._catalog_ranking("¿Qué entradas tienen más observaciones?", PACK, HOST)                  # «observaciones» es otro contador: no se mapea a este indicador
    assert other is None or other.result is None or other.result.ranking is None or other.result.ranking.indicator != "attention"


def test_the_clarification_options_name_the_indicator_cases_to_review(world):
    labels = set(_ask_questions(_route("¿Qué modelos de Ventas con mayores riesgos?")))
    assert "Casos a revisar (más casos primero)" in labels and not any("Puntos de atención" in label for label in labels)
    assert ind.canonical_question("attention", "best_first") == "¿Qué entradas tienen más casos a revisar?"


# ── 9. empates (incluidos los que exceden diez entradas) y límites metodológicos ───────────────────────────────────────────────────────────────
def _all_equal(monkeypatch, n=25):
    """Un indicador sintético donde `n` entradas empatan en el mismo valor (más que las diez visibles)."""
    monkeypatch.setitem(ind.BY_ID, "coverage", dataclasses.replace(ind.BY_ID["coverage"], measure=lambda e, g, p: ind.Measurement(ind.OK, 1.0, "100.0%", caps.Evidence("Assessment · cobertura estricta"))))


def test_a_tie_wider_than_ten_is_declared_as_a_tie_and_never_as_distinct_positions(world, monkeypatch):
    _all_equal(monkeypatch)
    r = _route("¿Qué entradas tienen mejor cobertura estricta?")
    info = r.result.ranking
    assert len(r.result.items) == 10 and info.evaluated == N and info.ties == (("100.0%", N),) and info.cut_in_tie == ("100.0%", N - 10)
    assert f"Empatan: {N} entradas con «100.0%»" in r.result.footer and "no implica un ranking" in r.result.footer
    assert f"El corte cae dentro de un empate: hay {N - 10} entradas más con «100.0%»" in r.result.footer
    assert {it.badge for it in r.result.items} == {"100.0%"}                                           # todas con el MISMO valor: nada sugiere una posición distinta
    page = _chat_raw("¿Qué entradas tienen mejor cobertura estricta?")
    assert "<ol" not in page and not re.search(r"assistant-result-title\">\s*(?:#?\d+[.)°]|\d+º)", page) and "posición" not in page.lower()    # sin numeración ni «primero/segundo…»


def test_the_order_inside_a_tie_is_stable_and_independent_of_load_order(world, monkeypatch):
    _all_equal(monkeypatch)
    a = _refs(_route("¿Qué entradas tienen mejor cobertura estricta?"))
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, list(reversed(world.entries))))
    assert _refs(_route("¿Qué entradas tienen mejor cobertura estricta?")) == a


def test_stale_assessments_are_excluded_and_counted_for_every_assessment_indicator(world):
    stale = sum(1 for i in range(N) if _spec(i)["state"] == svc.STALE)
    assert stale > 0
    for q in ("adopción metodológica", "cobertura estricta", "confianza de la evidencia", "casos a revisar"):
        r = _route(f"¿Qué entradas tienen mejor {q}?" if q != "casos a revisar" else "¿Qué entradas tienen más casos a revisar?")
        assert dict(r.result.ranking.excluded)[ind.STALE] == stale and f"{stale} desactualizadas" in r.result.footer
        assert not any(_spec(world.index[it.ref])["state"] == svc.STALE for it in r.result.items)


def test_methodological_limits_travel_with_every_result(world):
    conf = _route("¿Qué entradas tienen mayor confianza de la evidencia?").result.footer
    assert "no un juicio sobre la calidad del modelo" in conf and "una confianza alta no significa que el modelo sea favorable" in conf
    types = _route("¿Qué entradas tienen mejor consistencia de tipos?").result.footer
    assert "independiente de la compatibilidad con un destino BDC" in types and "no implica compatibilidad con cualquier destino" in types and "No es un puntaje global" in types
