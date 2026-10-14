"""4B.1 / 4B.2 — Lo que el asistente sabe de UN modelo: atributos, vigencia, no entregados y fundamentos del Juicio.

Mundo sintético escrito a mano con LOS SIETE estados (vigente con Juicio determinado, vigente con Juicio no determinable, desactualizado, sin assessment, no aplica, no disponible, esquema incompatible).
El ORÁCULO son los valores literales de `CAST`, escritos acá desde las reglas del contrato (no calculados con el código bajo prueba).
"""
from __future__ import annotations

import dataclasses
import re
import types

import pytest

import webapp.main as main
from tools.catalog import catalog_service as cs
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import assistant_capabilities as caps
from webapp.services import assistant_entry_facts as facts
from webapp.services import assistant_indicators as ind
from webapp.services import catalog_reads
from webapp.services import domain_classification_service as dcs

PACK = "datasphere_core"
GENERATED = "2026-10-09T10:00:00+00:00"
GENERATED_TEXT = "09/10/2026"

S = lambda dim, key: {"dimension": dim, "statement_key": key}                                                    # noqa: E731
W = lambda dim, key, cls: {"dimension": dim, "statement_key": key, "classification": cls}                         # noqa: E731
STRENGTHS = [S("SOURCE_INGESTION", "src.governed_acquisition_explicit"), S("HARMONIZATION", "har.reusable_semantic_enrichment"),
             S("PUBLISHED_CONTRACT", "pub.semantically_intentional_consumer_contract"), S("PROPAGATION", "pro.product_composes_resolved_contracts")]
REASONS = [W("PROPAGATION", "pro.branch_alignment_completed_at_product", "LOCAL_WEAKNESS"), W("HARMONIZATION", "har.fragmented_layers_with_undeclared_roles", "RELEVANT_WEAKNESS"),
           W("SOURCE_INGESTION", "src.physical_reach_without_bypass_and_compensation", "LOCAL_WEAKNESS"), W("PUBLISHED_CONTRACT", "lek.unrestored_responsibility_leakage", "MATERIAL_DEVIATION")]

# clave → especificación. `level` None = sin Juicio; "ND" = Juicio no determinable. `title` None = sin modelo terminal identificable.
CAST = {
    "dd": dict(client="Kensing", dominio="Delivery Document", title="Delivery Document", state=svc.CURRENT, level="BUENA", strict=0.95, conf="HIGH", review=7, score=90, ver="portability-scoring/v2", lob="SALES"),
    "ddi": dict(client="Kensing", dominio="Delivery Document Item", title="Delivery Document Item", state=svc.CURRENT, level="ADECUADA", strict=0.80, conf="MEDIUM", review=12, score=70, ver="portability-scoring/v2"),
    "so": dict(client="Biogenesis", dominio="Sell Out", title="Sell Out", state=svc.CURRENT, level="PLENA", strict=0.99, conf="HIGH", review=0, score=100, ver="portability-scoring/v2"),
    "soh": dict(client="Biogenesis", dominio="Sell Out Hojobar", title="Sell Out Hojobar", state=svc.CURRENT, level="PARCIAL", strict=0.60, conf="LOW", review=20, score=None, ver=None),
    "mp": dict(client="Biogenesis", dominio="AM_RESERVA_MP", title="Reserva MP", state=svc.CURRENT, level="ND", strict=0.85, conf="HIGH", review=4, score=80, ver="portability-scoring/v2"),
    "lp": dict(client="Biogenesis", dominio="AM_Lista_Precios", title="Lista Precios", state=svc.STALE, level="BUENA", strict=0.80, conf="LOW", review=3, score=60, ver="portability-scoring/v2",
               stale_reason=svc.STALE_INPUT_CHANGED),
    "ped": dict(client="Illolay", dominio="Pedidos Entregas", title="Pedidos Entregas", state=svc.NOT_YET_ASSESSED, score=50, ver="portability-scoring/v2"),
    "dan": dict(client="Danone", dominio="Almacén Danone", title=None, state=svc.NOT_APPLICABLE, reason="Este flujo no contiene un modelo analítico terminal que evaluar.", score=55, ver="portability-scoring/v2"),
    "desp": dict(client="Loma", dominio="Despachos", title="Despachos", state=svc.FAILED, reason="El motor no pudo evaluar este modelo.", score=65, ver="portability-scoring/v2"),
    "fac": dict(client="Pampa", dominio="Facturas", title="Facturas", state=svc.INCOMPATIBLE_SCHEMA, reason="El informe guardado usa un esquema que esta versión no sabe leer.", score=40, ver=None),
    "stl": dict(client="Loma", dominio="Stock Loma", title="Stock", state=svc.CURRENT, level="BUENA", strict=0.91, conf="HIGH", review=5, score=85, ver="portability-scoring/v2"),
    "stp": dict(client="Pampa", dominio="Stock Pampa", title="Stock", state=svc.CURRENT, level="ADECUADA", strict=0.72, conf="MEDIUM", review=9, score=75, ver="portability-scoring/v2"),
}
LEVEL_LABEL = {"PLENA": "Plena", "BUENA": "Buena", "ADECUADA": "Adecuada", "PARCIAL": "Parcial"}
CONF_LABEL = {"HIGH": "Alta", "MEDIUM": "Media", "LOW": "Baja"}


def _profile(c):
    return {"assessment_state": "ASSESSED", "summary": {"coverage": {"strict": c["strict"]}, "confidence": {"level": c["conf"]}},
            "review_required": [{"occurrence_count": c["review"]}], "observations": [{"occurrence_count": 1}]}


def _judgment(c):
    if c["level"] == "ND":
        return {"status": "NOT_DETERMINABLE", "level": None, "ruleset_version": "judgment-rules/v2", "strengths": [], "reasons": [],
                "not_determinable_reason": "INSUFFICIENT_AFFIRMATIVE_EVIDENCE"}
    many = c["level"] in ("BUENA", "PARCIAL")
    return {"status": "DETERMINED", "level": c["level"], "ruleset_version": "judgment-rules/v2", "strengths": STRENGTHS if many else STRENGTHS[:2], "reasons": REASONS if many else REASONS[:1]}


def _status(entry, c):
    if c["state"] in (svc.NOT_YET_ASSESSED, svc.NOT_APPLICABLE, svc.FAILED, svc.INCOMPATIBLE_SCHEMA):
        return svc.AssessmentStatus(state=c["state"], entry_id=entry.entry_id, client=entry.cliente, terminal_am=None, reason=c.get("reason"))
    return svc.AssessmentStatus(state=c["state"], entry_id=entry.entry_id, client=entry.cliente, terminal_am="4AM_" + c["dominio"], generated_at=GENERATED, profile=_profile(c),
                                judgment=_judgment(c), stale_reason=c.get("stale_reason"))


def _write(root, i, c):
    d = root / c["client"] / f"D{i:03d}"
    d.mkdir(parents=True)
    lob = f"  linea_de_negocio: {c['lob']}\n" if c.get("lob") else ""
    (d / f"flujo_D{i:03d}.md").write_text(f"---\nauto:\n  tipo: flujo\n  dominio: {c['dominio']}\n  artefactos:\n  - 4AM_X_{i:03d}\nmanual:\n  cliente: {c['client']}\n  veredicto: buena\n{lob}---\nCuerpo.\n", encoding="utf-8")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """Entradas reales del Catálogo (sintéticas) con estados de Assessment REALES y títulos controlados (el resolver de títulos del producto se reemplaza por el mapa de `CAST`)."""
    keys = list(CAST)
    for i, k in enumerate(keys):
        _write(tmp_path, i, CAST[k])
    entries = cs.load_catalog(str(tmp_path))
    by_dom = {e.dominio: e for e in entries}
    e = {k: by_dom[CAST[k]["dominio"]] for k in keys}
    key_of = {x.entry_id: k for k, x in e.items()}
    for k, x in e.items():
        c = CAST[k]
        x.portabilidad_bdc = ({"score": c["score"], "version": c["ver"], "clasificacion": "plena"} if c.get("score") is not None else {"clasificacion": "no_evaluable"})
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(catalog_reads, "_catalog_data_products_entries", lambda: (cs, [types.SimpleNamespace(product_id="DP", title="X")]))
    statuses = {x.entry_id: _status(x, CAST[k]) for k, x in e.items()}

    def read(flows):
        st = {f.entry_id: statuses[f.entry_id] for f in flows}
        return caps.Governed(st, dcs.effective_lob_by_entry(flows, st))
    monkeypatch.setattr(caps, "governed_read", read)

    def entry_title(status, entry):
        c = CAST[key_of[entry.entry_id]]
        return (c["title"], f"AM_{key_of[entry.entry_id].upper()}") if c["title"] else (presenter.NO_TERMINAL_TITLE, None)
    monkeypatch.setattr(presenter, "entry_title", entry_title)
    return types.SimpleNamespace(e=e, entries=entries, statuses=statuses, key_of=key_of, gov=lambda *keys: read([e[k] for k in (keys or CAST)]))


def _facts(world, key, *ids, fundamentals=False):
    g = world.gov(key)
    return facts.answer(world.e[key], g, PACK, ids or facts.ATTRIBUTE_ORDER, with_fundamentals=fundamentals)


def _val(a, iid):
    return next((v for v in a.attributes if v.indicator == iid), None)


def _held(a, iid):
    return next((w for w in a.withheld if w.indicator == iid), None)


# ══ 1. vigente con Juicio determinado: los cinco indicadores, contra el ORÁCULO literal ═══════════════════════════════════════════════════
@pytest.mark.parametrize("key", ["dd", "ddi", "so", "stl", "stp"])
def test_current_entry_attributes_equal_the_oracle(world, key):
    c, a = CAST[key], _facts(world, key)
    assert _val(a, "adoption").value == c["level"] and _val(a, "adoption").display == LEVEL_LABEL[c["level"]]
    assert _val(a, "confidence").value == c["conf"] and _val(a, "confidence").display == CONF_LABEL[c["conf"]]
    assert _val(a, "coverage").value == c["strict"]
    assert _val(a, "attention").value == c["review"]
    assert _val(a, "types").value == c["score"] and _val(a, "types").display == f"{c['score']}/100"
    assert not a.withheld and not any(v.historical for v in a.attributes)
    assert a.state.state == svc.CURRENT and a.state.current and a.state.label == "Assessment vigente" and a.state.generated_at == GENERATED_TEXT


def test_every_value_names_the_entry_it_comes_from_and_never_mixes_entries(world):
    seen = {}
    for key in ("dd", "ddi", "stl", "stp", "so"):
        a = _facts(world, key)
        assert all(v.entry_id == world.e[key].entry_id for v in a.attributes) and a.entry.ref == world.e[key].entry_id
        seen[key] = tuple(v.value for v in a.attributes)
    assert len(set(seen.values())) == len(seen)                                                    # ningún valor «prestado» de otra entrada: cada ficha es distinta


def test_evidence_carries_source_date_ruleset_and_a_link_built_from_the_entry_id(world):
    a = _facts(world, "dd")
    adoption, confidence, types_ = _val(a, "adoption"), _val(a, "confidence"), _val(a, "types")
    assert adoption.evidence.source == "Juicio metodológico" and adoption.evidence.ruleset == "judgment-rules/v2" and adoption.evidence.generated_at == GENERATED_TEXT
    assert confidence.evidence.source == "Assessment · confianza de la evidencia" and confidence.evidence.url == presenter.assessment_url(world.e["dd"], PACK)
    assert types_.evidence.source.startswith("Portabilidad") and types_.evidence.ruleset == "portability-scoring/v2" and types_.evidence.url == presenter.flow_url(world.e["dd"], PACK)
    assert all(v.caveat for v in a.attributes)                                                      # cada valor viaja con su advertencia metodológica


# ══ 2. Juicio no determinable ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════
def test_judgment_not_determinable_withholds_only_adoption_and_says_why(world):
    a = _facts(world, "mp")
    w = _held(a, "adoption")
    assert w.code == facts.NOT_DETERMINABLE and "No determinable" in w.text and "Falta evidencia afirmativa de fortaleza" in w.text
    assert [v.indicator for v in a.attributes] == ["confidence", "coverage", "types", "attention"]            # los demás indicadores siguen su curso
    assert _val(a, "coverage").value == 0.85 and not any(v.historical for v in a.attributes)


# ══ 3. desactualizado: último valor SOLO como histórico y no vigente ═══════════════════════════════════════════════════════════════════════
def test_stale_assessment_values_are_historical_and_flagged(world):
    a = _facts(world, "lp")
    c = CAST["lp"]
    assert a.state.state == svc.STALE and not a.state.current and a.state.label == "Assessment desactualizado"
    assert a.state.note == "El modelo cambió desde la última evaluación." and a.state.body.startswith("Se muestra el informe anterior")
    hist = {v.indicator: v for v in a.attributes if v.historical}
    assert set(hist) == {"adoption", "confidence", "coverage", "attention"}
    assert hist["adoption"].value == "BUENA" and hist["confidence"].value == c["conf"] and hist["coverage"].value == c["strict"] and hist["attention"].value == c["review"]
    types_ = _val(a, "types")
    assert types_ is not None and not types_.historical and types_.value == c["score"]               # la consistencia de tipos es de la portabilidad: no depende del Assessment


def test_stale_values_never_enter_current_rankings(world):
    g = world.gov()
    for iid in ("adoption", "confidence", "coverage", "attention"):
        rs = ind.rank_scope(ind.BY_ID[iid], ind.BEST_FIRST, list(world.e.values()), g, PACK)
        assert world.e["lp"].entry_id not in {i.ref for i in rs.items}
        assert dict(rs.ranking.excluded).get(ind.STALE) == 1


def test_stale_without_a_readable_report_withholds_with_a_stale_code(world):
    st = dataclasses.replace(world.statuses[world.e["lp"].entry_id], profile=None, judgment=None)
    g = caps.Governed({world.e["lp"].entry_id: st}, dcs.effective_lob_by_entry([world.e["lp"]], {world.e["lp"].entry_id: st}))
    a = facts.answer(world.e["lp"], g, PACK, ("adoption", "coverage"))
    assert not a.attributes and {w.code for w in a.withheld} == {facts.STALE}


# ══ 4. ausente, no aplica, no disponible, no compatible: conceptos DISTINTOS ═════════════════════════════════════════════════════════════
@pytest.mark.parametrize("key,code,label,concept", [
    ("ped", facts.ABSENT, "Sin assessment", "NOT_EVALUATED"),
    ("dan", facts.NOT_APPLICABLE, "No aplica", "NOT_APPLICABLE"),
    ("desp", facts.UNAVAILABLE, "Assessment no disponible", "FAILED"),
    ("fac", facts.INCOMPATIBLE, "Informe no compatible", "INCOMPATIBLE_SCHEMA"),
])
def test_missing_states_withhold_every_assessment_value_with_a_distinct_code(world, key, code, label, concept):
    a = _facts(world, key)
    assert a.state.label == label and a.state.concept == concept and not a.state.current
    held = {w.indicator: w for w in a.withheld}
    assert set(held) == {"adoption", "confidence", "coverage", "attention"} | ({"types"} if key == "fac" else set())
    assert {w.code for w in held.values() if w.indicator != "types"} == {code}
    assert not any(v.indicator in ("adoption", "confidence", "coverage", "attention") for v in a.attributes)


def test_no_assessment_vs_no_usable_result_texts_are_not_interchangeable(world):
    absent, na = _held(_facts(world, "ped"), "adoption"), _held(_facts(world, "dan"), "adoption")
    assert "todavía no se evaluó" in absent.text and "todavía no se evaluó" not in na.text
    assert "No aplica" in na.text and CAST["dan"]["reason"] in na.text
    assert CAST["desp"]["reason"] in _held(_facts(world, "desp"), "coverage").text and CAST["fac"]["reason"] in _held(_facts(world, "fac"), "coverage").text


def test_types_is_independent_of_the_assessment_state(world):
    for key in ("ped", "dan", "desp"):
        v = _val(_facts(world, key, "types"), "types")
        assert v.value == CAST[key]["score"] and not v.historical, key
    legacy = _held(_facts(world, "fac", "types"), "types")
    assert legacy.code == facts.LEGACY_RULES                                                       # puntaje sin versión de reglas: no es comparable
    assert _held(_facts(world, "soh", "types"), "types").code == facts.NO_VALUE


# ══ 5. fundamentos: solo lo que el producto ya presenta ═════════════════════════════════════════════════════════════════════════════════════
def test_fundamentals_use_the_exact_product_texts_and_caps(world):
    f = _facts(world, "dd", "adoption", fundamentals=True).fundamentals
    assert f.level == "BUENA" and f.level_label == "Buena" and f.determined and not f.historical
    assert f.headline == "La evidencia observada muestra una adopción consistente de los cuatro contratos metodológicos."
    assert f.strengths == (("Ingesta", "Adquisición de la fuente gobernada en todo contacto con el origen"), ("Armonización", "Enriquecimiento semántico reutilizable consumido aguas abajo"),
                           ("Contrato publicado", "Contrato publicado intencional y autónomo para el consumidor")) and f.strengths_total == 4
    assert f.reasons[0] == ("Propagación", "Debilidad local", "Alineación de ramas completada recién en el producto") and len(f.reasons) == 3 and f.reasons_total == 4
    assert [c[0] for c in f.contracts] == ["Ingesta", "Armonización", "Propagación", "Contrato publicado"]
    assert {state for _dim, state in f.contracts} == {"Requiere atención"}                          # las cuatro dimensiones tienen una razón: la atención prevalece sobre la fortaleza
    so = _facts(world, "so", "adoption", fundamentals=True).fundamentals                           # PLENA: dos fortalezas y una razón
    assert dict(so.contracts) == {"Ingesta": "Evidencia positiva", "Armonización": "Evidencia positiva", "Propagación": "Requiere atención", "Contrato publicado": "Sin señal específica"}


def test_fundamentals_equal_judgment_view_for_every_current_level(world):
    for key in ("dd", "ddi", "so", "soh", "stl", "stp"):
        f = _facts(world, key, "adoption", fundamentals=True).fundamentals
        jv = presenter.judgment_view(world.statuses[world.e[key].entry_id].judgment)
        assert (f.level_label, f.headline) == (jv["level_label"], jv["headline"])
        assert f.strengths == tuple((s["dimension"], s["text"]) for s in jv["strengths"][:3]) and f.reasons == tuple((r["dimension"], r["kind"], r["text"]) for r in jv["reasons"][:3])


def test_fundamentals_of_one_entry_never_carry_another_levels_text(world):
    good, partial = (_facts(world, k, "adoption", fundamentals=True).fundamentals for k in ("dd", "soh"))
    assert good.level != partial.level and good.headline != partial.headline


def test_fundamentals_not_determinable_carry_the_reason_and_no_level(world):
    f = _facts(world, "mp", "adoption", fundamentals=True).fundamentals
    assert not f.determined and f.level is None and f.nd_reason == "Falta evidencia afirmativa de fortaleza" and f.level_label == "No determinable"


def test_fundamentals_of_a_stale_assessment_are_marked_historical(world):
    f = _facts(world, "lp", "adoption", fundamentals=True).fundamentals
    assert f is not None and f.historical and f.level == "BUENA"


@pytest.mark.parametrize("key", ["ped", "dan", "desp", "fac"])
def test_no_fundamentals_without_a_judgment(world, key):
    assert _facts(world, key, "adoption", fundamentals=True).fundamentals is None


def test_limitations_come_from_the_report_view_and_are_capped(world, monkeypatch):
    rows = [{"title": f"Superficie {n}", "area": "Área", "state": "No evaluable", "reason": f"Motivo {n}"} for n in range(4)]
    monkeypatch.setattr(presenter, "report_view", lambda *a, **k: {"limitations": rows})
    f = _facts(world, "dd", "adoption", fundamentals=True).fundamentals
    assert f.limitations == (("Superficie 0", "Área", "No evaluable", "Motivo 0"), ("Superficie 1", "Área", "No evaluable", "Motivo 1")) and f.limitations_total == 4


def test_a_failing_report_view_does_not_break_the_fundamentals(world, monkeypatch):
    monkeypatch.setattr(presenter, "report_view", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    f = _facts(world, "dd", "adoption", fundamentals=True).fundamentals
    assert f is not None and f.limitations == () and f.strengths


def test_the_contract_exposes_no_field_the_product_does_not_present():
    names = {f.name for cls in (caps.Fundamentals, caps.EntryAnswer, caps.AttributeValue, caps.StateInfo) for f in dataclasses.fields(cls)}
    assert not names & {"what_would_change_the_judgment", "what_would_improve_the_solution", "cta_candidates", "review_candidates"}
    src = open(facts.__file__, encoding="utf-8").read()
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"what_would|cta_candidates|review_candidates", code.split('"""', 2)[2])   # ni se leen (solo se nombran en la documentación del módulo)


def test_links_are_built_from_the_entry_id(world):
    a = _facts(world, "dd")
    assert dict(a.links) == {"Ver Assessment": presenter.assessment_url(world.e["dd"], PACK), "Abrir detalle": presenter.flow_url(world.e["dd"], PACK)}


def test_a_stale_assessment_whose_last_judgment_was_not_determinable_says_so_as_historical(world):
    key = world.e["lp"].entry_id
    nd = {"status": "NOT_DETERMINABLE", "level": None, "ruleset_version": "judgment-rules/v2", "strengths": [], "reasons": [], "not_determinable_reason": "INSUFFICIENT_AFFIRMATIVE_EVIDENCE"}
    world.statuses[key] = dataclasses.replace(world.statuses[key], judgment=nd)
    a = _facts(world, "lp", "adoption", "confidence")
    held = _held(a, "adoption")
    assert held.code == facts.NOT_DETERMINABLE and held.text.startswith("Último informe conocido (Assessment desactualizado, no vigente): no determinable")
    assert "Falta evidencia afirmativa de fortaleza" in held.text and "no hay un último valor legible" not in held.text
    assert _val(a, "confidence").historical and _val(a, "confidence").value == "LOW"                  # y los demás valores históricos siguen su curso
