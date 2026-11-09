"""Pruebas FOCALIZADAS de DUPLICATED_REMOTE_IMPORT como revisión resoluble (Iteración III, alcance A · prioridad 2).

Identidad = cliente (clave del store) + modelo raíz + regla + identidad remota. Las representaciones locales NO están en la
identidad: viven en la HUELLA, proyectada a la entrada de esa identidad remota dentro del hecho de CLAUSURA
`ingestion.duplicate_remote_imports@closure` (compartido por todas las ocurrencias). Garantías:
  · identidad estable y diferenciada por entidad remota;
  · cambiar OTRA entidad no invalida la huella; cambiar el propio conjunto de representaciones ⇒ STALE (misma identidad);
  · hecho ausente / entidad ausente / sidecar incompatible ⇒ UNKNOWN (nunca MATCH; la justificación humana no es evidencia);
  · resolver/revocar una entidad no altera otra;
  · las reglas ya cubiertas conservan su huella genérica (digests sin cambios).
Store LOCAL REAL en tmp_path (sin Spaces, sin residuos).
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
CLIENT, ROOT = "Biogenesis", "AM_ANALISIS_VENCIMIENTO"
FACT = rev.DUPLICATE_REMOTE_IMPORTS_FACT
RULE = "DUPLICATED_REMOTE_IMPORT"


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setenv("DANWOR_GOVERNED_DECISIONS_DIR", str(tmp_path / "gd"))
    monkeypatch.setenv("DANWOR_GOVERNED_DECISIONS_LOCAL", "1")
    yield tmp_path / "gd"


class _Fact:
    def __init__(self, key, value):
        self.key, self.value = key, value


GRP1 = {"remote_entity": "SAPI.0MATL_GRP_1_TEXT", "local_artifacts": ["0MATL_GRP_1_TEXT", "1TD_0MATL_GRP_1_TEXT"], "in_closure": ["0MATL_GRP_1_TEXT"]}
GRP2 = {"remote_entity": "SAPI.0MATL_GRP_2_TEXT", "local_artifacts": ["0MATL_GRP_2_TEXT", "1TD_0MATL_GRP_2_TEXT"], "in_closure": ["0MATL_GRP_2_TEXT"]}


def _build(entries=(GRP1, GRP2), *, fact_present=True):
    """Perfil + hechos con la MISMA forma que produce el motor (`facts_ingestion` + `_rule_duplicated_remote_import`)."""
    occs = [{"occurrence_id": f"{RULE}-{i:04d}", "affected_artifacts": list(g["in_closure"]), "evidence_facts": [FACT],
             "values": {"remote_entity": g["remote_entity"], "local_artifacts": list(g["local_artifacts"]), "in_closure": list(g["in_closure"]),
                        "spans_outside_closure": len(g["in_closure"]) < len(g["local_artifacts"]), "duplicated_inside_closure": len(g["in_closure"]) > 1}}
            for i, g in enumerate(entries)]
    doc = {"scope": {"pool_signature": "POOL"}, "versions": {"signals": "signal-rules/v5"},
           "review_required": [{"id": RULE, "occurrences": occs}], "observations": []}
    facts = [_Fact(FACT, {"in_closure": [], "in_universe_touching_closure": [copy.deepcopy(g) for g in entries]})] if fact_present else []
    return doc, facts


def _ident(entity_entry):
    return rev.review_identity(RULE, ROOT, {"remote_entity": entity_entry["remote_entity"], "local_artifacts": entity_entry["local_artifacts"]},
                               list(entity_entry["in_closure"]))


def _tid(entry):
    return rev.review_technical_id(_ident(entry))


def _digest(doc, facts, entry):
    sc = rev.build_sidecar(doc, facts, ROOT)
    return rev.current_footprint_digest(_tid(entry), sc, doc)


# ─────────────────────────── identidad ───────────────────────────

def test_identity_is_root_rule_and_remote_entity_without_local_representations():
    ident = _ident(GRP1)
    assert ident == {"klass": rev.KLASS_MODEL_REVIEW, "root_am": ROOT, "rule": RULE, "motive": "SAPI.0MATL_GRP_1_TEXT"}
    assert "1TD_0MATL_GRP_1_TEXT" not in _tid(GRP1)


def test_distinct_remote_entities_have_distinct_identities_and_storage_keys():
    assert _tid(GRP1) != _tid(GRP2)
    k1 = store.subject_prefix({"client": CLIENT, "technical_id": _tid(GRP1)})
    k2 = store.subject_prefix({"client": CLIENT, "technical_id": _tid(GRP2)})
    assert k1 != k2


def test_identity_stable_when_local_representations_change():
    grown = dict(GRP1, local_artifacts=GRP1["local_artifacts"] + ["2TD_0MATL_GRP_1_TEXT"])
    assert _tid(grown) == _tid(GRP1)


# ─────────────────────────── huella ───────────────────────────

def test_footprint_is_projected_to_its_own_entity():
    doc, facts = _build()
    sc = rev.build_sidecar(doc, facts, ROOT)
    fp = sc["footprints"][_tid(GRP1)]
    assert fp["rule"] == RULE and fp["klass"] == rev.KLASS_MODEL_REVIEW
    material = rev.occurrence_footprint(RULE, doc["review_required"][0]["occurrences"][0], {FACT: facts[0].value})
    assert material == {FACT: {"remote_entity": "SAPI.0MATL_GRP_1_TEXT", "local_artifacts": ["0MATL_GRP_1_TEXT", "1TD_0MATL_GRP_1_TEXT"],
                               "in_closure": ["0MATL_GRP_1_TEXT"]}}
    assert fp["digest"] == rev.footprint_digest(material)


def test_changing_another_entity_does_not_invalidate_this_footprint():
    base = _digest(*_build(), GRP1)
    other_changed = dict(GRP2, local_artifacts=GRP2["local_artifacts"] + ["2TD_0MATL_GRP_2_TEXT"])
    assert _digest(*_build((GRP1, other_changed)), GRP1) == base
    assert _digest(*_build((GRP1,)), GRP1) == base                           # otra entidad que desaparece tampoco la invalida


def test_changing_own_local_representations_changes_digest():
    base = _digest(*_build(), GRP1)
    grown = dict(GRP1, local_artifacts=GRP1["local_artifacts"] + ["2TD_0MATL_GRP_1_TEXT"])
    assert _digest(*_build((grown, GRP2)), grown) != base
    moved = dict(GRP1, in_closure=["1TD_0MATL_GRP_1_TEXT"])
    assert _digest(*_build((moved, GRP2)), moved) != base


def test_missing_fact_or_entity_or_incompatible_sidecar_is_unknown():
    doc, _facts = _build(fact_present=False)
    assert _digest(doc, [], GRP1) is None                                     # hecho ausente ⇒ sin huella publicada
    doc, facts = _build()
    facts[0].value["in_universe_touching_closure"] = [GRP2]                   # la entidad no está en el hecho
    assert _digest(doc, facts, GRP1) is None
    doc, facts = _build()
    sc = rev.build_sidecar(doc, facts, ROOT)
    other_profile = dict(doc, scope={"pool_signature": "OTRO"})               # sidecar que no liga al perfil
    assert rev.current_footprint_digest(_tid(GRP1), sc, other_profile) is None
    st = rr.derive_review_status([], current_footprint_digest=None, now=NOW, presence="PRESENT")
    assert st["evidence"] in (rr.EVIDENCE_NA, rr.EVIDENCE_UNKNOWN)


def test_existing_rules_keep_generic_footprint():
    occ = {"evidence_facts": ["harmonization.contract_character@2TR"], "values": {"materialized_table": "2TR"}}
    facts = {"harmonization.contract_character@2TR": {"sources": ["2VR"]}}
    for rule in ("TRANSFORMATION_FLOW_CONSOLIDATION_OPPORTUNITY", "ROLE_LAYER_DIVERGENCE", "FIELD_VOCABULARY_MIX", "LAYER_BOUNDARY_REVIEW"):
        assert rev.occurrence_footprint(rule, occ, facts) == rev.footprint_material(occ["evidence_facts"], facts)


# ─────────────────────────── ciclo gobernado ───────────────────────────

def _resolve(doc, sc, entry, disposition, event_id, at):
    return ops.resolve_review(client=CLIENT, identity=_ident(entry), disposition=disposition,
                              rationale="0MATL_GRP_1_TEXT = consumo remoto; 1TD_0MATL_GRP_1_TEXT = materialización replicada",
                              decided_at=at, event_id=event_id, sidecar=sc, profile=doc, auth=OWNER, store=store)


def _status(doc, sc, entry):
    return ops.review_status(client=CLIENT, technical_id=_tid(entry), sidecar=sc, profile=doc, now=NOW, presence="PRESENT", store=store)


def _label(st):
    return presenter.STATE_LABELS[presenter._state_key(st)][0]


def test_resolve_reload_revoke_and_independence(local_store):
    doc, facts = _build()
    sc = rev.build_sidecar(doc, facts, ROOT)
    _resolve(doc, sc, GRP1, gd.DISP_VALIDATED_DESIGN, "d-1", "2026-11-09T15:00:00Z")
    st = _status(doc, sc, GRP1)
    assert st["evidence"] == rr.EVIDENCE_MATCH and _label(st) == "Cerrada"
    assert _label(_status(doc, sc, GRP2)) == "Sin resolver"                   # la otra entidad no se toca
    ev = store.load_events(CLIENT, _tid(GRP1))                                # relectura (≡ recargar)
    assert len(ev) == 1 and "materialización replicada" in ev[0]["rationale"]
    ops.revoke_review(client=CLIENT, technical_id=_tid(GRP1), revokes_event_id="d-1", rationale="r", decided_at="2026-11-09T15:05:00Z",
                      event_id="d-2", auth=OWNER, store=store)
    assert _label(_status(doc, sc, GRP1)) == "Reabierta"
    assert store.load_events(CLIENT, _tid(GRP2)) == []


def test_synthetic_change_of_local_set_marks_stale_with_same_identity(local_store):
    doc, facts = _build()
    sc = rev.build_sidecar(doc, facts, ROOT)
    _resolve(doc, sc, GRP1, gd.DISP_VALIDATED_DESIGN, "d-1", "2026-11-09T15:00:00Z")
    grown = dict(GRP1, local_artifacts=GRP1["local_artifacts"] + ["2TD_0MATL_GRP_1_TEXT"])
    doc2, facts2 = _build((grown, GRP2))
    sc2 = rev.build_sidecar(doc2, facts2, ROOT)
    assert _tid(grown) == _tid(GRP1)
    st = _status(doc2, sc2, grown)
    assert st["evidence"] == rr.EVIDENCE_STALE and _label(st) == "Evidencia desactualizada"
    assert _label(_status(doc2, sc2, GRP2)) == "Sin resolver"


def test_resolution_refused_without_verifiable_footprint(local_store):
    doc, _ = _build(fact_present=False)
    sc = rev.build_sidecar(doc, [], ROOT)
    with pytest.raises(ops.FootprintUnavailable):
        _resolve(doc, sc, GRP1, gd.DISP_VALIDATED_DESIGN, "d-1", "2026-11-09T15:00:00Z")
    assert store.load_events(CLIENT, _tid(GRP1)) == []
