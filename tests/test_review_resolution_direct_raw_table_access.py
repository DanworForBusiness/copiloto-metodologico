"""Pruebas FOCALIZADAS de DIRECT_RAW_TABLE_ACCESS como OBSERVACIÓN resoluble (Iteración III).

La señal conserva SignalKind.OBSERVATION (vive en `observations`, no en `review_required`). Identidad = modelo raíz + regla + artefacto
local + tabla física remota. Huella = SOLO la relación de acceso (contrato técnico sin la etiqueta, filtro de origen, `key_fields`);
fuera: alcance aguas abajo y continuidad de identidad del nombre. Criterios: identidad estable/distinta · independencia · propia ⇒
STALE · ajena/cosmética/consumidor nuevo ⇒ sigue MATCH · ausente ⇒ UNKNOWN sin registrar · resolver/consultar/revocar sin contaminar ·
presenter proyecta observaciones con disposiciones contextualizadas · huellas previas byte-idénticas. Store LOCAL en tmp_path.
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
CLIENT, ROOT, RULE = "ClienteFixture", "AM_ANALISIS_VENCIMIENTO", "DIRECT_RAW_TABLE_ACCESS"
ACCESSES = {"a": ("1TR_MARC", "ABAPTABLES.MARC", "MARC"), "b": ("T001L", "ABAPTABLES.T001L", "T001L"), "c": ("T001W", "ABAPTABLES.T001W", "T001W")}


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setenv("DANWOR_GOVERNED_DECISIONS_DIR", str(tmp_path / "gd"))
    monkeypatch.setenv("DANWOR_GOVERNED_DECISIONS_LOCAL", "1")
    yield tmp_path / "gd"


class _Fact:
    def __init__(self, key, value):
        self.key, self.value = key, value


def _occ(s):
    art, entity, stem = ACCESSES[s]
    return {"occurrence_id": f"{RULE}-{'abc'.index(s):04d}", "affected_artifacts": [art],
            "evidence_facts": [f"source.contract@{art}", f"source.downstream_reach@{art}", f"source.query_filter@{art}",
                               f"source.identity_continuity@{art}", f"source.keys@{art}"],
            "values": {"remote_entity": entity, "remote_connection": "S4PREM", "artifact_label": f"Etiqueta {stem}", "remote_stem": stem,
                       "reaches_terminal_by_strict_lineage": True, "lineage_distance": 2, "source_filter": "ABSENT"}}


def _state():
    st = {}
    for art, entity, stem in ACCESSES.values():
        st[f"source.contract@{art}"] = {"remote_connection": "S4PREM", "remote_entity": entity, "namespace": "ABAPTABLES",
                                         "source_state": "RAW_PHYSICAL_TABLE", "remote_stem": stem, "artifact_label": f"Etiqueta {stem}"}
        st[f"source.downstream_reach@{art}"] = {"reaches_terminal_by_strict_lineage": True, "direct_lineage_consumers": ["3VF_X"]}
        st[f"source.query_filter@{art}"] = {"has_remote_query": False, "where": None, "query_column_count": 0}
        st[f"source.identity_continuity@{art}"] = {"identity": "SOURCE_DERIVED_IMPORTED", "rule": "ID_SOURCE_EXACT", "certainty": "PROVEN", "remote_stem": stem}
        st[f"source.keys@{art}"] = {"key_count": 2, "key_fields": ["MATNR", "WERKS"], "field_count": 40, "element_count": 40}
    return st


def _doc():
    return {"scope": {"pool_signature": "POOL"}, "versions": {"signals": "signal-rules/v5"}, "review_required": [],
            "observations": [{"id": RULE, "kind": "OBSERVATION", "occurrences": [_occ(s) for s in "abc"]}]}


def _sc(state):
    doc = _doc()
    return doc, rev.build_sidecar(doc, [_Fact(k, v) for k, v in state.items()], ROOT)


def _ident(s):
    o = _occ(s)
    return rev.review_identity(RULE, ROOT, o["values"], o["affected_artifacts"])


def _tid(s):
    return rev.review_technical_id(_ident(s))


def _digest(state, s):
    doc, sc = _sc(state)
    return rev.current_footprint_digest(_tid(s), sc, doc)


def _status(doc, sc, s):
    return ops.review_status(client=CLIENT, technical_id=_tid(s), sidecar=sc, profile=doc, now=NOW, presence="PRESENT", store=store)


def _label(st):
    return presenter.STATE_LABELS[presenter._state_key(st)][0]


# ─────────────────────────── identidad ───────────────────────────

def test_identity_root_rule_local_artifact_and_physical_table():
    assert _ident("a") == {"klass": rev.KLASS_MODEL_REVIEW, "root_am": ROOT, "rule": RULE, "artifact": "1TR_MARC", "motive": "ABAPTABLES.MARC"}
    assert len({_tid(s) for s in "abc"}) == 3
    assert len({store.subject_prefix({"client": CLIENT, "technical_id": _tid(s)}) for s in "abc"}) == 3
    assert _tid("a") == _tid("a") and f"{RULE}-0000" not in _tid("a")
    assert rev.review_identity(RULE, ROOT, {}, ["1TR_MARC"]) is None and rev.review_identity(RULE, ROOT, {"remote_entity": "X"}, []) is None


# ─────────────────────────── huella ───────────────────────────

def test_footprint_contains_only_the_access_relation():
    material = rev.occurrence_footprint(RULE, _occ("a"), _state())
    assert set(material) == {"source.contract@1TR_MARC", "source.query_filter@1TR_MARC", "source.keys@1TR_MARC"}
    assert "artifact_label" not in material["source.contract@1TR_MARC"]
    assert material["source.keys@1TR_MARC"] == {"key_fields": ["MATNR", "WERKS"]}


@pytest.mark.parametrize("change", [
    lambda st: st["source.contract@1TR_MARC"].update(remote_connection="S4OTRA"),
    lambda st: st["source.contract@1TR_MARC"].update(source_state="GOVERNED_CDS"),
    lambda st: st["source.query_filter@1TR_MARC"].update(has_remote_query=True, where="WERKS = '1000'"),
    lambda st: st["source.keys@1TR_MARC"].update(key_fields=["MATNR"]),
])
def test_own_material_change_changes_digest(change):
    base = _digest(_state(), "a")
    st = _state(); change(st)
    assert _digest(st, "a") not in (None, base)


@pytest.mark.parametrize("change", [
    lambda st: st["source.contract@1TR_MARC"].update(artifact_label="Otra etiqueta"),            # cosmético
    lambda st: st["source.downstream_reach@1TR_MARC"].update(direct_lineage_consumers=["3VF_X", "3VF_NUEVO"]),  # consumidor nuevo
    lambda st: st["source.identity_continuity@1TR_MARC"].update(rule="ID_SOURCE_NAME_EXTENDS_STEM"),  # heurística de nombre
    lambda st: st["source.keys@1TR_MARC"].update(field_count=41, element_count=41),             # conteos derivados
    lambda st: st["source.contract@T001L"].update(remote_connection="S4OTRA"),                    # otra observación
    lambda st: st["source.keys@T001W"].update(key_fields=["WERKS"]),                             # otra observación
])
def test_cosmetic_contextual_or_foreign_change_keeps_digest(change):
    base = _digest(_state(), "a")
    st = _state(); change(st)
    assert _digest(st, "a") == base


def test_missing_or_mismatched_contract_is_unknown():
    st = _state(); st.pop("source.contract@1TR_MARC")
    assert _digest(st, "a") is None
    st = _state(); st["source.contract@1TR_MARC"]["remote_entity"] = "ABAPTABLES.OTRA"
    assert _digest(st, "a") is None
    doc, sc = _sc(_state())
    assert rev.current_footprint_digest(_tid("a"), sc, dict(doc, scope={"pool_signature": "OTRO"})) is None


def test_previous_footprints_remain_byte_identical():
    from tests.test_review_resolution_generic_families import test_previous_families_footprints_are_byte_identical as golden
    golden()


# ─────────────────────────── ciclo gobernado ───────────────────────────

def _resolve(doc, sc, s, event_id, disposition=gd.DISP_VALIDATED_DESIGN):
    return ops.resolve_review(client=CLIENT, identity=_ident(s), disposition=disposition,
                              rationale="Excepción: MARC se lee directo por ausencia de CDS liberada en S4PREM.",
                              decided_at="2026-11-09T17:00:00Z", event_id=event_id, sidecar=sc, profile=doc, auth=OWNER, store=store)


def test_resolve_consult_revoke_stale_unknown_without_contamination(local_store):
    st0 = _state()
    doc, sc = _sc(st0)
    _resolve(doc, sc, "a", "dr-1")
    s = _status(doc, sc, "a")
    assert s["evidence"] == rr.EVIDENCE_MATCH and _label(s) == "Cerrada"
    assert [_label(_status(doc, sc, x)) for x in "bc"] == ["Sin resolver", "Sin resolver"]
    ev = store.load_events(CLIENT, _tid("a"))                                  # consulta / relectura
    assert len(ev) == 1 and ev[0]["disposition"] == gd.DISP_VALIDATED_DESIGN and "Excepción" in ev[0]["rationale"]
    foreign = copy.deepcopy(st0); foreign["source.keys@T001L"]["key_fields"] = ["WERKS"]
    assert _status(*_sc(foreign), "a")["evidence"] == rr.EVIDENCE_MATCH       # evidencia ajena ⇒ MATCH
    own = copy.deepcopy(st0); own["source.query_filter@1TR_MARC"]["where"] = "WERKS = '1000'"
    s_own = _status(*_sc(own), "a")
    assert s_own["evidence"] == rr.EVIDENCE_STALE and _label(s_own) == "Evidencia desactualizada"
    gone = copy.deepcopy(st0); gone.pop("source.contract@1TR_MARC")
    assert _status(*_sc(gone), "a")["evidence"] == rr.EVIDENCE_UNKNOWN
    ops.revoke_review(client=CLIENT, technical_id=_tid("a"), revokes_event_id="dr-1", rationale="r", decided_at="2026-11-09T17:05:00Z",
                      event_id="dr-2", auth=OWNER, store=store)
    assert _label(_status(doc, sc, "a")) == "Reabierta"
    assert all(store.load_events(CLIENT, _tid(x)) == [] for x in "bc")


def test_cannot_resolve_without_verifiable_footprint(local_store):
    st = _state(); st.pop("source.contract@1TR_MARC")
    doc, sc = _sc(st)
    with pytest.raises(ops.FootprintUnavailable):
        _resolve(doc, sc, "a", "dr-x")
    assert store.load_events(CLIENT, _tid("a")) == []


def test_correction_required_is_pending_remediation(local_store):
    doc, sc = _sc(_state())
    _resolve(doc, sc, "b", "dr-b", gd.DISP_CORRECTION_REQUIRED)
    assert _label(_status(doc, sc, "b")) == "Pendiente de remediación"


# ─────────────────────────── presenter: observación (no revisión) con disposiciones contextualizadas ───────────────────────────

def test_presenter_projects_observations_with_contextual_dispositions(local_store):
    doc, sc = _sc(_state())
    _resolve(doc, sc, "a", "dr-1")

    class _St:
        profile, review_evidence, client, terminal_am = doc, sc, CLIENT, ROOT

    view = {"review_required": [], "observations": [{"id": RULE, "occurrences": [{"id": o["occurrence_id"]} for o in doc["observations"][0]["occurrences"]]}]}
    presenter.enrich(view, _St(), is_owner=True, now=NOW, store=store)
    g = view["observations"][0]
    assert g["resoluble"] is True and g["klass_label"] is None                 # sin rótulo de «revisión» en la observación
    ra, rb = g["occurrences"][0]["resolution"], g["occurrences"][1]["resolution"]
    assert ra["noun"] == "observación" and ra["state_label"] == "Cerrada" and rb["state_label"] == "Sin resolver"
    labels = dict(ra["allowed_dispositions"])
    assert labels[gd.DISP_VALIDATED_DESIGN] == "Diseño validado · excepción metodológica justificada"
    assert labels[gd.DISP_CORRECTION_REQUIRED] == "Requiere corrección · desviación pendiente de remediación"
    assert labels[gd.DISP_NOT_APPLICABLE] == "No aplica · observación no aplicable al caso"
    assert labels[gd.DISP_DEFERRED] == "Pospuesta · decisión diferida"
    assert ra["decision"]["disposition_label"] == "Diseño validado · excepción metodológica justificada"
    assert set(ra["disposition_hints"]) == set(labels)
