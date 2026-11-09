"""Pruebas FOCALIZADAS de las cuatro familias REVIEW incorporadas en Iteración III (alcance A · prioridad 3).

  · PASS_THROUGH_REVIEW            identidad = arista `proveedor->consumidor`; huella = `propagation.contract_delta@<arista>` (genérica)
  · LABEL_NOT_ENRICHING            identidad = artefacto; huella = `naming.field_name_contract@<artefacto>` (genérica)
  · AM_SEMANTIC_UNDERUSE_REVIEW    identidad = modelo analítico; huella = `am.contract_inventory@<AM>` (genérica)
  · REPEATED_DERIVATION_IN_LINEAGE identidad = arista; huella PROYECTADA a las rederivaciones de esa arista desde
                                   `propagation.rederivation@closure` (adaptador), con su detalle técnico completo

Criterios por familia: identidad estable y distinta · independencia entre sujetos (evidencia ajena ⇒ sigue MATCH) · evidencia propia
⇒ STALE · evidencia ausente ⇒ UNKNOWN y no se puede registrar · resolver/revocar un sujeto no altera otro. Además: digests GOLDEN de
las familias previas (Iteración II + DUPLICATED_REMOTE_IMPORT) byte-idénticos. Fixtures con la forma que emite `signals.py`; store
LOCAL REAL en tmp_path (descartable, sin Spaces, nunca el store del owner).
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

import config
from tools.catalog.modeling_assessment import review_evidence as rev
from webapp.services import governed_decisions as gd
from webapp.services import governed_decisions_store as store
from webapp.services import review_resolution_ops as ops
from webapp.services import review_resolution_presenter as presenter
from webapp.services import review_resolutions as rr

NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)
OWNER = ops.AuthContext(actor="owner@danwor.com", is_owner=True, same_origin=True)
CLIENT, ROOT = "ClienteFixture", "AM_VENTAS"


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setenv("DANWOR_GOVERNED_DECISIONS_DIR", str(tmp_path / "gd"))
    monkeypatch.setenv("DANWOR_GOVERNED_DECISIONS_LOCAL", "1")
    yield tmp_path / "gd"


class _Fact:
    def __init__(self, key, value):
        self.key, self.value = key, value


# ─────────────────────────── fixtures representativos (forma de signals.py) ───────────────────────────
# Cada familia: dos sujetos (a, b). `occ(s)` = ocurrencia del perfil; `facts(state)` = hechos; `mutate(state, s)` = cambia la
# evidencia PROPIA de s; `drop(state, s)` = la evidencia de s desaparece.

def _pt_occ(s):
    edge = {"a": "2VR_A->3VF_A", "b": "2VR_B->3VF_B"}[s]
    p, c = edge.split("->")
    return {"affected_artifacts": [p, c], "evidence_facts": [f"propagation.contract_delta@{edge}"],
            "values": {"edge": edge, "field_count": 12, "consumer_declared_pattern": "FACT", "provider_inferred_layer": "2", "consumer_inferred_layer": "3"}}


def _pt_state():
    return {f"propagation.contract_delta@{e}": {"descriptors": {"schema_identical": True}, "consumer_summary": {"n_fields": 12}}
            for e in ("2VR_A->3VF_A", "2VR_B->3VF_B")}


def _lne_occ(s):
    art = {"a": "1TD_CLIENTE", "b": "1TD_MATERIAL"}[s]
    return {"affected_artifacts": [art], "evidence_facts": [f"naming.field_name_contract@{art}"],
            "values": {"artifact": art, "n_fields": 4, "label_equals_field": 4}}


def _lne_state():
    return {f"naming.field_name_contract@{a}": {"n_fields": 4, "label_equals_field": 4, "fields": [{"field": "KUNNR", "label": "KUNNR"}]}
            for a in ("1TD_CLIENTE", "1TD_MATERIAL")}


def _am_occ(s):
    am = {"a": "AM_VENTAS", "b": "AM_STOCK"}[s]
    return {"affected_artifacts": [am], "evidence_facts": [f"am.contract_inventory@{am}"],
            "values": {"n_associations": 0, "n_measures": 3, "n_declared_dimensions": 0, "label_coverage": 1.0}}


def _am_state():
    return {f"am.contract_inventory@{am}": {"associations": [], "measures": ["M1", "M2", "M3"], "declared_dimensions": [], "label_coverage": 1.0}
            for am in ("AM_VENTAS", "AM_STOCK")}


RED = rev.REDERIVATION_FACT


def _rd_occ(s):
    p, c = {"a": ("2VR_A", "3VF_A"), "b": ("2VR_B", "3VF_B")}[s]
    return {"affected_artifacts": [p, c], "evidence_facts": [RED],
            "values": {"provider": p, "consumer": c, "fields": ["MONTO"], "lineage_distance": 1, "adjacent": True,
                       "field_relations": [{"field": "MONTO", "relation": "DUPLICATED_TRANSFORMATION", "relation_reason": "SAME_EXPRESSION"}]}}


def _rd_state():
    occ = [{"provider": p, "consumer": c, "field": "MONTO", "lineage_distance": 1, "adjacent": True, "relation": "DUPLICATED_TRANSFORMATION",
            "relation_reason": "SAME_EXPRESSION", "exposed_by_root": True, "exposure_basis": "SAME_NAME"}
           for p, c in (("2VR_A", "3VF_A"), ("2VR_B", "3VF_B"))]
    return {RED: {"occurrences": occ, "adjacent_count": 2, "relation_counts": {"DUPLICATED_TRANSFORMATION": 2}, "match_rule": "..."}}


def _mut_key(state, key):
    state[key] = dict(state[key], _changed=True)


def _rd_mutate(state, s):
    p = {"a": "2VR_A", "b": "2VR_B"}[s]
    for o in state[RED]["occurrences"]:
        if o["provider"] == p:
            o["relation"] = "CHAINED_REFINEMENT"


def _rd_drop(state, s):
    p = {"a": "2VR_A", "b": "2VR_B"}[s]
    state[RED]["occurrences"] = [o for o in state[RED]["occurrences"] if o["provider"] != p]


FAMILIES = {
    "PASS_THROUGH_REVIEW": dict(occ=_pt_occ, state=_pt_state, key=lambda s: _pt_occ(s)["evidence_facts"][0]),
    "LABEL_NOT_ENRICHING": dict(occ=_lne_occ, state=_lne_state, key=lambda s: _lne_occ(s)["evidence_facts"][0]),
    "AM_SEMANTIC_UNDERUSE_REVIEW": dict(occ=_am_occ, state=_am_state, key=lambda s: _am_occ(s)["evidence_facts"][0]),
    "REPEATED_DERIVATION_IN_LINEAGE": dict(occ=_rd_occ, state=_rd_state, mutate=_rd_mutate, drop=_rd_drop),
}


def _mutate(rule, state, s):
    fam = FAMILIES[rule]
    if "mutate" in fam:
        fam["mutate"](state, s)
    else:
        _mut_key(state, fam["key"](s))


def _drop(rule, state, s):
    fam = FAMILIES[rule]
    if "drop" in fam:
        fam["drop"](state, s)
    else:
        state.pop(fam["key"](s))


def _doc(rule):
    occ = FAMILIES[rule]["occ"]
    occs = [dict(occ(s), occurrence_id=f"{rule}-{i:04d}") for i, s in enumerate(("a", "b"))]
    return {"scope": {"pool_signature": "POOL"}, "versions": {"signals": "signal-rules/v5"},
            "review_required": [{"id": rule, "occurrences": occs}], "observations": []}


def _sidecar(rule, state):
    doc = _doc(rule)
    return doc, rev.build_sidecar(doc, [_Fact(k, v) for k, v in state.items()], ROOT)


def _ident(rule, s):
    o = FAMILIES[rule]["occ"](s)
    return rev.review_identity(rule, ROOT, o["values"], o["affected_artifacts"])


def _tid(rule, s):
    return rev.review_technical_id(_ident(rule, s))


def _status(rule, doc, sc, s):
    return ops.review_status(client=CLIENT, technical_id=_tid(rule, s), sidecar=sc, profile=doc, now=NOW, presence="PRESENT", store=store)


def _label(st):
    return presenter.STATE_LABELS[presenter._state_key(st)][0]


def _resolve(rule, doc, sc, s, event_id):
    return ops.resolve_review(client=CLIENT, identity=_ident(rule, s), disposition=gd.DISP_VALIDATED_DESIGN, rationale="criterio del consultor",
                              decided_at="2026-11-09T16:00:00Z", event_id=event_id, sidecar=sc, profile=doc, auth=OWNER, store=store)


RULES = list(FAMILIES)


# ─────────────────────────── 1 · identidad estable y distinta ───────────────────────────

@pytest.mark.parametrize("rule", RULES)
def test_identity_stable_distinct_and_anchored(rule):
    a1, a2, b = _ident(rule, "a"), _ident(rule, "a"), _ident(rule, "b")
    assert a1 == a2 and a1["klass"] == rev.KLASS_MODEL_REVIEW and a1["rule"] == rule and a1["root_am"] == ROOT
    assert _tid(rule, "a") != _tid(rule, "b")
    assert store.subject_prefix({"client": CLIENT, "technical_id": _tid(rule, "a")}) != store.subject_prefix({"client": CLIENT, "technical_id": _tid(rule, "b")})
    assert f"{rule}-0000" not in _tid(rule, "a")                              # nunca el índice ordinal de la ocurrencia


def test_expected_identity_anchors():
    assert _ident("PASS_THROUGH_REVIEW", "a")["motive"] == "2VR_A->3VF_A"
    assert _ident("REPEATED_DERIVATION_IN_LINEAGE", "a")["motive"] == "2VR_A->3VF_A"
    assert _ident("LABEL_NOT_ENRICHING", "a")["artifact"] == "1TD_CLIENTE"
    assert _ident("AM_SEMANTIC_UNDERUSE_REVIEW", "b")["artifact"] == "AM_STOCK"


@pytest.mark.parametrize("rule", RULES)
def test_identity_without_anchor_is_not_resoluble(rule):
    assert rev.review_identity(rule, ROOT, {}, []) is None


# ─────────────────────────── 2/3/4 · huella: ajena ⇒ MATCH, propia ⇒ STALE, ausente ⇒ UNKNOWN ───────────────────────────

@pytest.mark.parametrize("rule", RULES)
def test_footprint_independence_own_change_and_absence(rule):
    state = FAMILIES[rule]["state"]()
    doc, sc = _sidecar(rule, state)
    da, db = (rev.current_footprint_digest(_tid(rule, s), sc, doc) for s in ("a", "b"))
    assert da and db and da != db
    other = copy.deepcopy(state); _mutate(rule, other, "b")                     # evidencia AJENA cambia
    doc2, sc2 = _sidecar(rule, other)
    assert rev.current_footprint_digest(_tid(rule, "a"), sc2, doc2) == da
    own = copy.deepcopy(state); _mutate(rule, own, "a")                         # evidencia PROPIA cambia
    doc3, sc3 = _sidecar(rule, own)
    assert rev.current_footprint_digest(_tid(rule, "a"), sc3, doc3) not in (None, da)
    gone = copy.deepcopy(state); _drop(rule, gone, "a")                         # evidencia AUSENTE
    doc4, sc4 = _sidecar(rule, gone)
    assert rev.current_footprint_digest(_tid(rule, "a"), sc4, doc4) is None
    assert rev.current_footprint_digest(_tid(rule, "b"), sc4, doc4) == db


def test_rederivation_footprint_keeps_full_technical_detail_of_its_edge_only():
    state = _rd_state()
    material = rev.occurrence_footprint("REPEATED_DERIVATION_IN_LINEAGE", _rd_occ("a"), state)
    proj = material[RED]
    assert proj["provider"] == "2VR_A" and proj["consumer"] == "3VF_A"
    assert proj["occurrences"] == [state[RED]["occurrences"][0]]               # detalle completo (relación, motivo, distancia...)
    assert all(o["provider"] == "2VR_A" for o in proj["occurrences"])
    assert "relation_counts" not in proj and "adjacent_count" not in proj       # agregados de clausura fuera de la huella


# ─────────────────────────── ciclo gobernado en store descartable ───────────────────────────

@pytest.mark.parametrize("rule", RULES)
def test_governed_cycle_match_stale_and_independence(rule, local_store):
    state = FAMILIES[rule]["state"]()
    doc, sc = _sidecar(rule, state)
    _resolve(rule, doc, sc, "a", f"{rule}-r1")
    st = _status(rule, doc, sc, "a")
    assert st["evidence"] == rr.EVIDENCE_MATCH and _label(st) == "Cerrada"
    assert _label(_status(rule, doc, sc, "b")) == "Sin resolver"
    other = copy.deepcopy(state); _mutate(rule, other, "b")
    doc2, sc2 = _sidecar(rule, other)
    assert _status(rule, doc2, sc2, "a")["evidence"] == rr.EVIDENCE_MATCH     # evidencia ajena: sigue MATCH
    own = copy.deepcopy(state); _mutate(rule, own, "a")
    doc3, sc3 = _sidecar(rule, own)
    st3 = _status(rule, doc3, sc3, "a")
    assert st3["evidence"] == rr.EVIDENCE_STALE and _label(st3) == "Evidencia desactualizada"
    gone = copy.deepcopy(state); _drop(rule, gone, "a")
    doc4, sc4 = _sidecar(rule, gone)
    assert _status(rule, doc4, sc4, "a")["evidence"] == rr.EVIDENCE_UNKNOWN
    ops.revoke_review(client=CLIENT, technical_id=_tid(rule, "a"), revokes_event_id=f"{rule}-r1", rationale="r",
                      decided_at="2026-11-09T16:05:00Z", event_id=f"{rule}-r2", auth=OWNER, store=store)
    assert _label(_status(rule, doc, sc, "a")) == "Reabierta"
    assert store.load_events(CLIENT, _tid(rule, "b")) == []


@pytest.mark.parametrize("rule", RULES)
def test_cannot_resolve_without_verifiable_footprint(rule, local_store):
    state = FAMILIES[rule]["state"](); _drop(rule, state, "a")
    doc, sc = _sidecar(rule, state)
    with pytest.raises(ops.FootprintUnavailable):
        _resolve(rule, doc, sc, "a", f"{rule}-x")
    assert store.load_events(CLIENT, _tid(rule, "a")) == []


# ─────────────────────────── huellas previas byte-idénticas (golden, calculadas ANTES de este cambio) ───────────────────────────

GOLDEN = {
    "ROLE_LAYER_DIVERGENCE": "e82ee19df619742702ce112178bc0093f84a3f81366ed6ae38481fcc7f1192e4",
    "TRANSFORMATION_FLOW_CONSOLIDATION_OPPORTUNITY": "619534007a4f71cd6cb2da8277b6ae1167d03b295d396dfd86983493075f939d",
    "LAYER_BOUNDARY_REVIEW": "2d808bce576d3b3dffe6db35a9c9298c2b5cd4c0f1d6993bb4c75b2585b50b0c",
    "DUPLICATED_REMOTE_IMPORT": "f54f71fa59a1858b3f04a06e9cbfa1b3fc7a14a7e919ad282c5f57e22e5d7089",
    "FIELD_VOCABULARY_MIX": "3214864e10001e3c1512eb564165193ea27c5ccbeafe00083af3f8ea4b8c7a3b",
}


def test_previous_families_footprints_are_byte_identical():
    D = rev.DUPLICATE_REMOTE_IMPORTS_FACT
    groups = [
        {"id": "TRANSFORMATION_FLOW_CONSOLIDATION_OPPORTUNITY", "occurrences": [{"affected_artifacts": ["2TR"], "evidence_facts": ["harmonization.contract_character@2TR"], "values": {"materialized_table": "2TR"}}]},
        {"id": "ROLE_LAYER_DIVERGENCE", "occurrences": [{"affected_artifacts": ["1TD_X"], "evidence_facts": ["layer.x@1TD_X"], "values": {"artifact": "1TD_X", "divergence_kinds": ["K"]}}]},
        {"id": "FIELD_VOCABULARY_MIX", "occurrences": [{"affected_artifacts": ["A", "B"], "evidence_facts": ["naming.same@closure"], "values": {"label": "monto"}}]},
        {"id": "LAYER_BOUNDARY_REVIEW", "occurrences": [{"affected_artifacts": ["1TR", "3VF"], "evidence_facts": ["ingestion.first_consumers@1TR"], "values": {"source": "1TR"}}]},
        {"id": "DUPLICATED_REMOTE_IMPORT", "occurrences": [{"affected_artifacts": ["0M"], "evidence_facts": [D], "values": {"remote_entity": "SAPI.0M"}}]},
    ]
    doc = {"scope": {"pool_signature": "P"}, "versions": {"signals": "v5"}, "review_required": groups, "observations": []}
    facts = [_Fact("harmonization.contract_character@2TR", {"sources": ["2VR"]}), _Fact("layer.x@1TD_X", {"l": 1}),
             _Fact("naming.same@closure", {"labels": ["monto"]}), _Fact("ingestion.first_consumers@1TR", {"c": ["3VF"]}),
             _Fact(D, {"in_universe_touching_closure": [{"remote_entity": "SAPI.0M", "local_artifacts": ["0M", "1TD_0M"], "in_closure": ["0M"]}]})]
    fps = rev.build_sidecar(doc, facts, "AM_X")["footprints"]
    assert {v["rule"]: v["digest"] for v in fps.values()} == GOLDEN
