"""M1 — universo de AM terminales, clausura semantica, scope del assessment e indice de contencion cross-AM.

Sin evaluacion: responde "QUE ESTA EN SCOPE?". El indice cross-AM es solo el hecho de contencion
(no es senal de reuso).
"""
from __future__ import annotations

import pytest

from tools.catalog.modeling_assessment import legacy_bridge as bridge
from tools.catalog.modeling_assessment import model as m
from tools.catalog.modeling_assessment.graph import EdgeKind, UnresolvedReason
from tools.catalog.modeling_assessment.scope import (
    AssessmentUniverse, TerminalAmSelectionRequired, UnknownTerminalAmError,
)

from tests.test_modeling_assessment_graph import ent, pool


def _client():
    """Dos AM terminales que comparten una dimension y un ancestro; un artefacto huerfano.

    AM_X --lineage--> VF_X --lineage--> VR_SHARED --lineage--> SRC_SHARED
    AM_X --assoc--> DIM_SHARED --lineage--> DSRC           (dimension compartida)
    AM_Y --lineage--> VF_Y --lineage--> VR_SHARED
    AM_Y --assoc--> DIM_SHARED ;  AM_Y --assoc--> DIM_ONLY_Y
    AM_Z --assoc--> DIM_SHARED
    ORPHAN: no pertenece a ninguna clausura.
    """
    return pool(
        ent("AM_X", up=["VF_X"], assoc=["DIM_SHARED"], declared=["DIM_SHARED"], cube=True),
        ent("VF_X", up=["VR_SHARED"]), ent("VR_SHARED", up=["SRC_SHARED"]), ent("SRC_SHARED"),
        ent("DIM_SHARED", up=["DSRC"]), ent("DSRC"),
        ent("AM_Y", up=["VF_Y"], assoc=["DIM_SHARED", "DIM_ONLY_Y"], declared=["DIM_SHARED", "DIM_ONLY_Y"], cube=True),
        ent("VF_Y", up=["VR_SHARED"]), ent("DIM_ONLY_Y"),
        ent("AM_Z", assoc=["DIM_SHARED"], declared=["DIM_SHARED"], cube=True),
        ent("ORPHAN"))


# ── enumeracion de AM terminales ───────────────────────────────────────────
def test_terminal_am_enumeration_uses_the_legacy_cube_criterion_and_is_sorted():
    ents = _client()
    u = AssessmentUniverse.from_entities(ents)
    assert u.terminal_ams == ("AM_X", "AM_Y", "AM_Z")
    assert set(u.terminal_ams) == {n for n, e in ents.items() if bridge.is_cube(e)}


def test_terminal_criterion_can_be_injected():
    ents = pool(ent("A"), ent("B", up=["A"]))
    u = AssessmentUniverse.from_entities(ents, is_terminal=lambda e: e.name == "B")
    assert u.terminal_ams == ("B",) and u.closure_of("B") == ("A", "B")


# ── clausura semantica recursiva ───────────────────────────────────────────
def test_closure_is_recursive_semantic_and_includes_the_terminal_am():
    u = AssessmentUniverse.from_entities(_client())
    assert u.closure_of("AM_X") == ("AM_X", "DIM_SHARED", "DSRC", "SRC_SHARED", "VF_X", "VR_SHARED")
    assert u.closure_of("AM_Z") == ("AM_Z", "DIM_SHARED", "DSRC")


def test_closure_matches_the_legacy_cube_subgraph():
    ents = _client()
    u = AssessmentUniverse.from_entities(ents)
    for am in u.terminal_ams:
        assert set(u.closure_of(am)) == set(bridge.cube_subgraph(ents, ents[am]))


def test_lineage_closure_and_feeds_do_not_treat_associations_as_feeding():
    u = AssessmentUniverse.from_entities(_client())
    assert u.lineage_closure_of("AM_X") == ("AM_X", "SRC_SHARED", "VF_X", "VR_SHARED")
    assert u.feeds("SRC_SHARED", "AM_X") is True
    assert u.feeds("DSRC", "AM_X") is False and "DSRC" in u.closure_of("AM_X")
    with pytest.raises(UnknownTerminalAmError):
        u.feeds("SRC_SHARED", "VF_X")                     # VF_X no es terminal


# ── scope del assessment ───────────────────────────────────────────────────
def test_scope_for_returns_the_m0_profile_scope_for_one_selected_am():
    u = AssessmentUniverse.from_entities(_client())
    s = u.scope_for("AM_Y", pool_signature="sha256:test")
    assert isinstance(s, m.ProfileScope)
    assert s.applicability.state is m.Applicability.APPLICABLE
    assert s.terminal_am == "AM_Y" and s.closure_names == u.closure_of("AM_Y")
    assert s.closure_size == len(s.closure_names) == 7 and s.pool_signature == "sha256:test"
    assert s.closure_names == ("AM_Y", "DIM_ONLY_Y", "DIM_SHARED", "DSRC", "SRC_SHARED", "VF_Y", "VR_SHARED")
    assert "ORPHAN" not in s.closure_names                       # el cliente NO es el scope
    m.to_jsonable(s)                                              # serializable por el contrato de M0


def test_a_whole_client_assessment_is_never_inferred():
    u = AssessmentUniverse.from_entities(_client())
    with pytest.raises(TerminalAmSelectionRequired):
        u.scope_for(None)
    single = AssessmentUniverse.from_entities(pool(ent("AM", up=["V"], cube=True), ent("V"), ent("ORPHAN")))
    with pytest.raises(TerminalAmSelectionRequired):              # ni siquiera con un unico AM se infiere
        single.scope_for(None)
    assert "ORPHAN" not in single.scope_for("AM").closure_names


def test_client_without_terminal_am_is_not_applicable():
    u = AssessmentUniverse.from_entities(pool(ent("T1"), ent("V1", up=["T1"])))
    assert u.terminal_ams == ()
    s = u.scope_for(None)
    assert s.applicability == m.ApplicabilityDecision(m.Applicability.NOT_APPLICABLE, m.ApplicabilityReason.NO_TERMINAL_ANALYTIC_MODEL)
    assert s.terminal_am is None and s.closure_size == 0
    with pytest.raises(UnknownTerminalAmError):
        u.scope_for("V1")


def test_unknown_or_non_terminal_selection_is_rejected():
    u = AssessmentUniverse.from_entities(_client())
    for bad in ("NOPE", "VF_X", "ORPHAN"):
        with pytest.raises(UnknownTerminalAmError):
            u.scope_for(bad)
        with pytest.raises(UnknownTerminalAmError):
            u.closure_of(bad)


def test_one_cube_client_still_resolves_its_closure_not_the_whole_pool():
    """Cliente de 1 cubo (caso LEGACY-1): el scope es la clausura del AM, no todo el pool."""
    ents = pool(ent("AM_ONLY", up=["VF"], cube=True), ent("VF"), ent("ORPHAN_RAW", remote_entity="ABAPTABLES.X"))
    u = AssessmentUniverse.from_entities(ents)
    assert u.closure_of("AM_ONLY") == ("AM_ONLY", "VF")


def test_am_without_upstream_has_a_closure_of_itself():
    u = AssessmentUniverse.from_entities(pool(ent("AM_ALONE", cube=True)))
    assert u.closure_of("AM_ALONE") == ("AM_ALONE",) and u.scope_for("AM_ALONE").closure_size == 1


# ── indice de contencion cross-AM ──────────────────────────────────────────
def test_containment_index_lists_the_terminal_ams_whose_closure_contains_each_artifact():
    u = AssessmentUniverse.from_entities(_client())
    idx = u.containment_index
    assert idx["DIM_SHARED"] == ("AM_X", "AM_Y", "AM_Z")
    assert idx["VR_SHARED"] == ("AM_X", "AM_Y") and idx["DSRC"] == ("AM_X", "AM_Y", "AM_Z")
    assert idx["DIM_ONLY_Y"] == ("AM_Y",)
    assert "ORPHAN" not in idx                                     # ningun AM lo contiene
    assert idx["AM_X"] == ("AM_X",)                                # cada AM se contiene a si mismo


def test_other_terminal_ams_excludes_the_assessed_am():
    """Ejemplo del owner: AM_X evaluado; D aparece en las clausuras de AM_X, AM_Y y AM_Z -> 2 otros AM."""
    u = AssessmentUniverse.from_entities(_client())
    others = u.other_terminal_ams_containing("DIM_SHARED", excluding="AM_X")
    assert others == ("AM_Y", "AM_Z") and len(others) == 2
    assert u.other_terminal_ams_containing("DIM_SHARED", excluding="AM_Z") == ("AM_X", "AM_Y")
    assert u.other_terminal_ams_containing("DIM_ONLY_Y", excluding="AM_Y") == ()
    assert u.other_terminal_ams_containing("VF_X", excluding="AM_X") == ()
    assert u.other_terminal_ams_containing("ORPHAN", excluding="AM_X") == ()
    with pytest.raises(UnknownTerminalAmError):
        u.other_terminal_ams_containing("DIM_SHARED", excluding="VF_X")


def test_shared_ancestor_is_found_through_lineage_in_one_am_and_association_in_another():
    u = AssessmentUniverse.from_entities(_client())
    assert u.graph.feeds("SRC_SHARED", "AM_X") and not u.graph.feeds("DSRC", "AM_Z")
    assert u.terminal_ams_containing("DSRC") == ("AM_X", "AM_Y", "AM_Z")     # contencion semantica, no linaje


def test_terminal_am_inside_another_terminal_closure_is_just_a_fact():
    ents = pool(ent("AM_A", up=["AM_B"], cube=True), ent("AM_B", up=["V"], cube=True), ent("V"))
    u = AssessmentUniverse.from_entities(ents)
    assert u.terminal_ams_containing("AM_B") == ("AM_A", "AM_B") and u.terminal_ams_containing("V") == ("AM_A", "AM_B")


def test_containment_index_is_immutable_and_exposes_no_quality_judgment():
    u = AssessmentUniverse.from_entities(_client())
    with pytest.raises(TypeError):
        u.containment_index["X"] = ()                            # type: ignore[index]
    assert all(isinstance(v, tuple) and all(isinstance(a, str) for a in v) for v in u.containment_index.values())


# ── evidencia de grafo por clausura ────────────────────────────────────────
def test_closure_evidence_reports_unresolved_references_and_cycles_inside_the_closure_only():
    ents = pool(ent("AM_X", up=["V"], cube=True), ent("V", up=["GHOST"], raw=["GHOST"]),
                ent("AM_Y", up=["P"], cube=True), ent("P", up=["Q"]), ent("Q", up=["P"]),
                ent("OTHER", up=["GHOST2"]))
    u = AssessmentUniverse.from_entities(ents)
    ex = u.evidence_for("AM_X")
    assert {(x.consumer, x.target, x.reason) for x in ex.unresolved} == {
        ("V", "GHOST", UnresolvedReason.MISSING_TARGET), ("V", "GHOST", UnresolvedReason.UNVERIFIED_QUERY_REFERENCE)}
    assert ex.cycles == ()
    ey = u.evidence_for("AM_Y")
    assert ey.cycles == (("P", "Q"),) and ey.unresolved == ()
    assert not any(x.consumer == "OTHER" for x in ex.unresolved)


def test_universe_survives_a_malformed_graph_without_crashing():
    ents = pool(ent("AM", up=["A"], assoc=["MISSING"], declared=["MISSING"], cube=True),
                ent("A", up=["B"]), ent("B", up=["A", "B"]))
    u = AssessmentUniverse.from_entities(ents)
    assert u.closure_of("AM") == ("A", "AM", "B")
    s = u.scope_for("AM")
    assert s.closure_size == 3
    assert u.evidence_for("AM").cycles == (("A", "B"),)


# ── plano Processing ───────────────────────────────────────────────────────
def test_loader_file_flag_policy_excludes_processing_dataflow_targets_from_every_modeling_structure():
    """(politica LOADER_FILE_FLAG, el contrato previo a closure-rules/v1) Frontera de planos: un target de Data Flow NO participa de la clausura semantica, la de linaje, el
    scope del AM, el indice de contencion cross-AM, la validacion de referencias ni los terminales."""
    ents = pool(ent("AM_X", up=["V", "T"], cube=True), ent("V"),
                ent("AM_Y", up=["T"], cube=True),
                ent("T", dataflow=True, up=["ONLY_VIA_T"]), ent("ONLY_VIA_T"),
                ent("AM_AS_DF", dataflow=True, cube=True))              # incluso un "cubo" marcado como target de DF
    u = AssessmentUniverse.from_entities(ents, plane_policy="LOADER_FILE_FLAG")
    assert u.terminal_ams == ("AM_X", "AM_Y")                           # AM_AS_DF no es terminal
    assert u.closure_of("AM_X") == ("AM_X", "V") and u.lineage_closure_of("AM_X") == ("AM_X", "V")
    assert u.closure_of("AM_Y") == ("AM_Y",)
    assert "T" not in u.scope_for("AM_X").closure_names and "ONLY_VIA_T" not in u.scope_for("AM_X").closure_names
    assert "T" not in u.containment_index and "ONLY_VIA_T" not in u.containment_index
    assert u.evidence_for("AM_X").unresolved == ()                       # no es referencia sin resolver
    assert u.processing_context.dataflow_targets == ("AM_AS_DF", "T")    # solo contexto complementario
    assert {b.consumer for b in u.processing_context.boundary_references} == {"AM_X", "AM_Y"}
    with pytest.raises(UnknownTerminalAmError):
        u.closure_of("AM_AS_DF")


def test_association_only_closure_is_the_semantic_minus_lineage_difference():
    u = AssessmentUniverse.from_entities(_client())
    assert u.association_only_closure_of("AM_X") == ("DIM_SHARED", "DSRC")
    assert set(u.association_only_closure_of("AM_X")) == set(u.closure_of("AM_X")) - set(u.lineage_closure_of("AM_X"))


def test_edge_kinds_are_exposed_for_later_milestones_without_judgment():
    u = AssessmentUniverse.from_entities(_client())
    assert {e.kind for e in u.graph.edges} == {EdgeKind.LINEAGE, EdgeKind.ASSOCIATION}
